import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

/* The Rig's colour and class rules, checked (SPEC: colour rule; CONTRACT: css conventions).
   1. Every --rig-* primitive in index.css :root exists in both themes (-d / -l).
   2. Text accents are >= 4.5:1 on bg, panel, panel2 and panel3 of their own theme, and the four
      signal hues (accent, pass, fail, caution) sit >= 25° apart so none borrows another's meaning.
   3. Green (hue 75°–165° with any real saturation) is reserved for "passed": only the sky
      tokens may be green, so nothing else in the palette can be read as a success.
   4. Rig stylesheets use only var(--rig-*) for colour, and only rg-* classes (bare modifier
      words only compounded onto an rg- class), so the classic app's .btn/.pane/.chip… never leak. */

const css = readFileSync(new URL('../index.css', import.meta.url), 'utf8')
const root = css.slice(css.indexOf(':root{'), css.indexOf('\n}', css.indexOf(':root{')))
const tok = (name) => (root.match(new RegExp('--' + name + ':\\s*(#[0-9a-fA-F]{6})')) || [])[1]
const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4) }
const lum = (h) => { const n = parseInt(h.slice(1), 16); return 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255) }
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05) }
function hsl(h) {
  const n = parseInt(h.slice(1), 16); const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1))
  let hue = 0
  if (d) hue = mx === r ? 60 * (((g - b) / d) % 6) : mx === g ? 60 * ((b - r) / d + 2) : 60 * ((r - g) / d + 4)
  return { h: (hue + 360) % 360, s, l }
}
const NAMES = ['bg', 'panel', 'panel2', 'panel3', 'line', 'line2', 'line3', 'text', 'dim', 'mute', 'unk', 'sel', 'sel-t', 'sel-ink', 'sky', 'sky-t', 'red', 'red-t', 'amb', 'amb-t', 'qty', 'ser1', 'ser2', 'ser3', 'ser4', 'ser5', 'ser6', 'on-accent']

describe('rig tokens', () => {
  it('every primitive exists in both themes', () => {
    for (const n of NAMES) for (const t of ['d', 'l']) expect(tok(`rig-${n}-${t}`), `--rig-${n}-${t}`).toMatch(/^#[0-9a-fA-F]{6}$/)
  })
  it('text accents reach 4.5:1 on every ground of their theme', () => {
    for (const t of ['d', 'l']) for (const fg of ['text', 'dim', 'mute', 'unk', 'sel-t', 'sky-t', 'red-t', 'amb-t']) for (const bg of ['bg', 'panel', 'panel2', 'panel3']) {
      expect(ratio(tok(`rig-${fg}-${t}`), tok(`rig-${bg}-${t}`)), `${fg} on ${bg} (${t})`).toBeGreaterThanOrEqual(4.5)
    }
  })
  it('glyph ink on verdict fills and ink on sodium reach 4.5:1', () => {
    for (const t of ['d', 'l']) {
      expect(ratio(tok(`rig-on-accent-${t}`), tok(`rig-sky-t-${t}`))).toBeGreaterThanOrEqual(4.5)
      expect(ratio(tok(`rig-on-accent-${t}`), tok(`rig-red-t-${t}`))).toBeGreaterThanOrEqual(4.5)
      expect(ratio(tok(`rig-sel-ink-${t}`), tok(`rig-sel-${t}`))).toBeGreaterThanOrEqual(4.5)
    }
  })
  it('keeps accent, pass, fail and caution on distinct hues', () => {
    for (const t of ['d', 'l']) {
      // a neutral grey has no meaningful hue, so a grey "passed" can never be mistaken for the accent
      const hs = ['sel', 'sky', 'red', 'amb'].filter((n) => hsl(tok(`rig-${n}-${t}`)).s >= 0.15).map((n) => [n, hsl(tok(`rig-${n}-${t}`)).h])
      for (let i = 0; i < hs.length; i++) for (let j = i + 1; j < hs.length; j++) {
        const d = Math.abs(hs[i][1] - hs[j][1]); const gap = Math.min(d, 360 - d)
        expect(gap, `${hs[i][0]} vs ${hs[j][0]} (${t})`).toBeGreaterThanOrEqual(25)
      }
    }
  })
  it('orders the data series by prominence, and keeps quantity bars visible on their track', () => {
    for (const t of ['d', 'l']) {
      // within each group (factors ser1-3, nuisance ser4-6) the first stands out most from the panel
      const c = [1, 2, 3, 4, 5, 6].map((i) => ratio(tok(`rig-ser${i}-${t}`), tok(`rig-panel-${t}`)))
      for (const i of [1, 2, 4, 5]) expect(c[i] < c[i - 1], `ser${i + 1} quieter than ser${i} (${t})`).toBe(true)
      // factors are blue, nuisance terms are grey
      for (const i of [1, 2, 3]) expect(hsl(tok(`rig-ser${i}-${t}`)).s, `ser${i} saturated (${t})`).toBeGreaterThan(0.3)
      for (const i of [4, 5, 6]) expect(hsl(tok(`rig-ser${i}-${t}`)).s, `ser${i} neutral (${t})`).toBeLessThan(0.15)
      expect(ratio(tok(`rig-qty-${t}`), tok(`rig-panel3-${t}`)), `qty on panel3 (${t})`).toBeGreaterThanOrEqual(3)
    }
  })
  it('reserves green for passed', () => {
    const all = [...root.matchAll(/--(rig-[\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]])
    expect(all.length).toBeGreaterThan(30)
    const green = (h) => { const c = hsl(h); return c.s > 0.15 && c.h >= 75 && c.h <= 165 }
    for (const [name, h] of all) expect(green(h), `${name} ${h}`).toBe(/^rig-sky(-t)?-[dl]$/.test(name))
  })
})

const RIG = fileURLToPath(new URL('./', import.meta.url))
const files = [join(RIG, 'rig.css'), ...readdirSync(join(RIG, 'views')).filter((f) => f.endsWith('.css')).map((f) => join(RIG, 'views', f))]
const strip = (t) => t.replace(/\/\*[\s\S]*?\*\//g, ' ')
/* Classes the classic app styles through a selector that names that class alone (".btn{",
   ".pane:hover{", ".sm{"): a Rig element carrying one of these would pick up classic styling. */
const SRC = fileURLToPath(new URL('../', import.meta.url))
function classicCss(dir = SRC) {
  const out = []
  for (const f of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, f.name)
    if (f.isDirectory()) { if (!p.startsWith(RIG.replace(/\/$/, ''))) out.push(...classicCss(p)) } else if (f.name.endsWith('.css') && !p.startsWith(RIG)) out.push(p)
  }
  return out
}
const RISKY = new Set()
for (const f of classicCss()) {
  for (const m of strip(readFileSync(f, 'utf8')).matchAll(/([^{}]+)\{/g)) {
    for (const part of m[1].split(',')) { const x = part.trim().match(/^\.([a-zA-Z][\w-]*)(:[\w-]+(\([^)]*\))?)*$/); if (x) RISKY.add(x[1]) }
  }
}
describe('rig stylesheets', () => {
  for (const f of files) {
    const text = strip(readFileSync(f, 'utf8'))
    const name = f.slice(RIG.length)
    it(`${name}: colour only via var(--rig-*)`, () => {
      expect(text.match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g)).toBeNull()
      const vars = [...text.matchAll(/var\(--([\w-]+)/g)].map((m) => m[1]).filter((v) => !v.startsWith('rig-'))
      expect(vars).toEqual([])
    })
    it(`${name}: classes are rg-*; other words only qualify an rg- class and never collide with the classic app`, () => {
      const selectors = [...text.matchAll(/([^{}@]+)\{/g)].map((m) => m[1]).filter((s) => !/^\s*(from|to|\d+%)\s*$/.test(s) && !/@/.test(s))
      const bad = []
      for (const sel of selectors) {
        for (const part of sel.split(',')) {
          for (const m of part.matchAll(/(^|[^\w-])\.([a-zA-Z][\w-]*)/g)) {
            const cls = m[2]
            if (cls.startsWith('rg-') || cls === 'rig') continue
            if (cls.startsWith('pw-buddy-') && name.endsWith('dock.css') && part.trim().startsWith('.rig ')) continue
            // a non-rg class may only qualify an rg- class (compound or descendant) and must not be one
            // the classic stylesheets style on its own
            if (!/\.rg-[\w-]+/.test(part)) bad.push(`${part.trim()} (.${cls} not under an rg- class)`)
            else if (RISKY.has(cls)) bad.push(`${part.trim()} (.${cls} is styled by the classic app)`)
          }
        }
      }
      expect(bad).toEqual([])
    })
  }
})
