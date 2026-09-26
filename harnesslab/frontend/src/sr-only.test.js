import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, relative, sep } from 'node:path'

/* Home.jsx has used className="sr-only" on three labels (the study-status filter, the study-sort
   control, and the table's "Open" column header) since before this rule existed anywhere in
   index.css -- with no CSS behind the class name, `sr-only` did nothing, and all three painted as
   plain visible text right next to (or inside) the control they were meant to silently label.

   This file checks the CSS contract directly: the rule exists, it uses the standard clip/1px-box
   technique (never `display:none` or `visibility:hidden`, either of which would also pull the
   node OUT of the accessibility tree -- the opposite of "screen-reader-only"), and every
   `className="sr-only"` in the app names a class this stylesheet actually defines. jsdom under
   this project's vitest config does not apply index.css at test time (no layout engine, and CSS
   imports are not injected -- see no-hardcoded-color.test.js's own note on this), so "renders
   hidden" is checked as a static property of the rule itself, the same way tokens.test.js checks
   index.css's other design rules; the live, rendered proof is a headless-browser screenshot, not
   this file. */

const SRC = fileURLToPath(new URL('./', import.meta.url))
const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')

function srOnlyRule(text) {
  const m = text.match(/\.sr-only\{([^}]*)\}/)
  return m ? m[1] : null
}

function allSources(dir = SRC) {
  const out = []
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) out.push(...allSources(p))
    else if (/\.jsx?$/.test(f)) out.push(p)
  }
  return out.sort()
}

describe('.sr-only is a real, visually-hidden-but-announced rule', () => {
  it('index.css defines it', () => {
    expect(srOnlyRule(css)).toBeTruthy()
  })

  it('never uses display:none or visibility:hidden -- both pull the node out of the accessibility tree too', () => {
    const rule = srOnlyRule(css)
    expect(rule).not.toMatch(/display\s*:\s*none/)
    expect(rule).not.toMatch(/visibility\s*:\s*hidden/)
  })

  it('uses the standard clip/1px-box technique: off-layout size, clipped, and not wrapped onto a second line', () => {
    const rule = srOnlyRule(css)
    // 1x1px (or smaller) box
    expect(rule).toMatch(/width\s*:\s*1px/)
    expect(rule).toMatch(/height\s*:\s*1px/)
    // taken out of normal document flow so it cannot push visible layout around
    expect(rule).toMatch(/position\s*:\s*absolute/)
    // clipped to nothing -- `clip` (the legacy property, still needed by some screen readers) or
    // the modern `clip-path` equivalent; this rule carries both.
    expect(rule).toMatch(/clip(-path)?\s*:/)
    expect(rule).toMatch(/overflow\s*:\s*hidden/)
  })

  it('every className="sr-only" in the app names a class this stylesheet actually defines', () => {
    // Not a per-file check by construction: `.sr-only` either exists in index.css or it doesn't,
    // so this is really "at least one component uses the class, and the stylesheet defines it" --
    // written as a loop over every source file so a NEW component reaching for the class is
    // exactly as covered as Home.jsx's three original (once-orphaned) uses.
    const users = allSources()
      .filter((p) => /\bclassName\s*=\s*["'`][^"'`]*\bsr-only\b/.test(readFileSync(p, 'utf8')))
      .map((p) => relative(SRC, p).split(sep).join('/'))
    expect(users.length, 'no component uses className="sr-only" -- this guard would be checking nothing').toBeGreaterThan(0)
    if (users.length) expect(srOnlyRule(css), `used by: ${users.join(', ')}`).toBeTruthy()
  })

  it('finds uses in components the app actually loads, so this guard cannot quietly become a no-op', () => {
    // method/Home.jsx and Workspace.jsx, the earlier users, went with the retired console.
    for (const f of ['rig/ui.jsx']) {
      const hits = readFileSync(join(SRC, f), 'utf8').match(/className=["'`][^"'`]*\bsr-only\b/g) || []
      expect(hits.length, f).toBeGreaterThan(0)
    }
  })
})
