import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, relative, sep } from 'node:path'

/* "A user-provided design token applies to EVERY visible screen -- no exceptions" (the operator's
   standing rule) is only as good as the grep that proves it. This file IS that grep, run on every
   command instead of from memory: a hex or rgb(a) colour literal anywhere outside index.css's
   :root, or in any non-test source file under src/, is a screen that has drifted from the shared
   token set, caught here rather than three screens later.

   Two separate rules, because they ask two different questions:
   (a) index.css is the one place literals are allowed to exist at all -- inside :root, where they
       define the tokens. Anywhere else in the file, a literal is a screen's worth of colour that
       skipped the token layer.
   (b) every OTHER source file should reach for a token (var(--x), or a value imported from a
       module that itself resolves to one) and never spell out a colour of its own. */

const SRC = fileURLToPath(new URL('./', import.meta.url))
const CSS_PATH = fileURLToPath(new URL('./index.css', import.meta.url))

const HEX = /#[0-9a-fA-F]{3,8}\b/g
const COLOR = /#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)/g

function lineOf(text, index) {
  return text.slice(0, index).split('\n').length
}

function findAll(re, text) {
  const out = []
  re.lastIndex = 0
  let m
  while ((m = re.exec(text))) out.push({ line: lineOf(text, m.index), value: m[0] })
  return out
}

/* :root opens on the file's first line and is not nested, so its own close is the first
   line that is exactly "}" after it opens -- everything from there on is outside :root. */
function cssOutsideRoot(css) {
  const rootStart = css.indexOf(':root{')
  const rootEnd = css.indexOf('\n}', rootStart)
  if (rootStart < 0 || rootEnd < 0) throw new Error('index.css: no :root{...} block found')
  const before = css.slice(0, rootEnd + 2)
  const after = css.slice(rootEnd + 2)
  const offsetLines = before.split('\n').length - 1
  return findAll(COLOR, after).map(h => ({ line: h.line + offsetLines, value: h.value }))
}

const isTest = (f) => /\.test\.jsx?$/.test(f)
const isSource = (f) => /\.jsx?$/.test(f) && !isTest(f)
/* index.css keeps its own dedicated check above (cssOutsideRoot), because it alone is allowed
   literals -- inside :root, where they define the tokens every other file below has to reach
   for instead. Every OTHER .css file under src/ is exactly like a .jsx/.js source file here: it
   should never spell out a colour of its own. (UI polish round 6 -- this loop used to stop at
   .jsx/.js, so steps.css and repeats.css had drifted into rgba(...) literals of their own,
   including one for a value index.css already had a token for.) */
const isOtherCss = (f) => f.endsWith('.css')

function allSources(dir = SRC) {
  const out = []
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) out.push(...allSources(p))
    else if (isSource(f)) out.push(p)
  }
  return out.sort()
}

/* p !== CSS_PATH, not a name check: index.css is the only file this must exclude, and comparing
   the full resolved path (rather than re-deriving one from `f` alone) is correct at any depth. */
function allOtherCss(dir = SRC) {
  const out = []
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) out.push(...allOtherCss(p))
    else if (isOtherCss(f) && p !== CSS_PATH) out.push(p)
  }
  return out.sort()
}

/* Comments are prose, not something a browser paints -- stripped (newlines kept, so line numbers
   in a failure message still point at the right place) before the same COLOR regex runs, so a
   file's own commentary about a colour ("no hex/rgb(a) literals here") can never be mistaken for
   one. */
function stripCssComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
}

const relPath = (p) => relative(SRC, p).split(sep).join('/')

/* No literal is exempt. The one allowance this rule used to carry (method/bits.jsx's MEASURE
   constant) went with the retired console; the Rig reads every colour from a --rig-* token. */
const EXEMPT = new Set()

describe('no hard-coded colour outside the token set', () => {
  it('index.css keeps every hex/rgb(a) literal inside :root', () => {
    const css = readFileSync(CSS_PATH, 'utf8')
    const hits = cssOutsideRoot(css).map(h => `index.css:${h.line} ${h.value}`)
    expect(hits).toEqual([])
  })

  it('finds files to check, so this guard cannot quietly become a no-op', () => {
    const rels = allSources().map(relPath)
    expect(rels).toContain('App.jsx')
    expect(rels).toContain('rig/RigApp.jsx')
    expect(rels.length).toBeGreaterThan(20)
  })

  for (const path of allSources()) {
    const rel = relPath(path)
    it(`${rel} has no hex or rgb(a) colour literal (outside the one exemption above)`, () => {
      const text = readFileSync(path, 'utf8')
      // COLOR, not HEX: a selected-row wash written as rgba(91,163,232,.07) slipped past a hex-only
      // check while --measure-wash existed for exactly that purpose.
      const hits = findAll(COLOR, text)
        .filter(h => !EXEMPT.has(`${rel}:${h.line}`))
        .map(h => `${rel}:${h.line} ${h.value}`)
      expect(hits).toEqual([])
    })
  }

  it('finds .css files besides index.css to check, so this half cannot quietly become a no-op', () => {
    const rels = allOtherCss().map(relPath)
    expect(rels).toContain('rig/rig.css')
    expect(rels).toContain('rig/views/analysis.css')
  })

  for (const path of allOtherCss()) {
    const rel = relPath(path)
    it(`${rel} has no hex or rgb(a) colour literal (every colour here is a var(--token))`, () => {
      const hits = findAll(COLOR, stripCssComments(readFileSync(path, 'utf8')))
        .map(h => `${rel}:${h.line} ${h.value}`)
      expect(hits).toEqual([])
    })
  }
})
