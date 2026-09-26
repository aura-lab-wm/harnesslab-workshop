/* ====================================================================================
   Rig · route.js — the hash grammar after `#/rig`. PURE: no DOM, no React, no storage.

   CONTRACT (Phase 2 views depend on this; change only additively)
   ----------------------------------------------------------------
   Hash      #/rig/<panes>[?<query>]
   panes     <pane>[|<pane>]                at most two panes (split view)
   pane      <tab>[+<tab>...]               a leading `!` marks the pane's active tab
                                            (omitted when the pane has one tab or tab 0 is active)
   tab       a spec string, e.g. `ds:llma4se_live:deepseek-v4-flash:baseline`
   query     dock=buddy|log|capture|case    open dock tab
             pal=1                           command palette open
             sheet=tabs                      mobile tab-switcher sheet open
             f=<0|1>                         focused pane (only written when 1)
             o=visible|strengthened          oracle (test suite); hidden is the default and not written
             c=<dir>:<model>:<harness>       condition memory for kinds whose spec does not carry one
             theme=dark|light|system         session override of the stored theme preference
             max=1                           maximized: rail and status bar folded, focused pane only
             lab=1|0                         one-shot Student Lab override (applied, then persisted by RigApp)

   Spec      <kind>[:<arg>...][~<chain>]
             args are colon-separated and escaped with encArg() (only % : | + ? & # ~ = ! and
             whitespace are escaped, so `/` in a model id stays readable).
             `~q:<dir>:<qid>` is an EVIDENCE CHAIN suffix: the tab was reached from a question.
             parseSpec() strips it into `chain`, so views never see it in `args`.

   API       parseHash(hash) -> state        toHash(state) -> '#/rig/...'
             parseSpec(spec) -> {kind, args, chain}
             makeSpec(kind, ...args) -> spec  (args escaped; trailing null/'' args dropped)
             withChain(spec, chain|null) -> spec    specKind(spec) -> kind
             isRigHash(hash) -> bool          DEFAULT_STATE
   ==================================================================================== */

export const SUITES = ['visible', 'hidden', 'strengthened']
export const DOCKS = ['buddy', 'log', 'capture', 'case']
export const THEMES = ['dark', 'light', 'system']

export const DEFAULT_STATE = Object.freeze({
  panes: [{ tabs: ['home'], active: 0 }],
  focus: 0,
  dock: null,
  pal: false,
  sheet: null,
  oracle: 'hidden',
  cond: null,
  theme: null,
  lab: null,
  max: false,
})

const ESC = /[%:|+?&#~=!\s]/g
/** Escape one spec argument. Only the characters the grammar uses are escaped. */
export const encArg = (s) => String(s ?? '').replace(ESC, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0'))
/** Undo encArg (and any other %XX a pasted link carries). Never throws. */
export const decArg = (s) => { try { return decodeURIComponent(s) } catch { return s } }

export function isRigHash(hash) {
  return /^#\/?rig(?:[/?]|$)/.test(String(hash || ''))
}

export function specKind(spec) {
  const s = String(spec || '')
  const i = s.search(/[:~]/)
  return i < 0 ? s : s.slice(0, i)
}

/** Split a spec into kind, decoded args and the optional evidence chain. */
export function parseSpec(spec) {
  const raw = String(spec || '')
  const t = raw.indexOf('~')
  const body = t < 0 ? raw : raw.slice(0, t)
  const chainRaw = t < 0 ? '' : raw.slice(t + 1)
  const [kind, ...args] = body.split(':')
  let chain = null
  if (chainRaw) {
    const [ck, dir, qid] = chainRaw.split(':')
    if (ck === 'q' && dir && qid) chain = { dir: decArg(dir), qid: decArg(qid) }
  }
  return { kind: kind || '', args: args.map(decArg), chain }
}

/** Build a spec from a kind and raw (unescaped) args. Trailing empty args are dropped so
 *  `makeSpec('ds', 'x', null, null)` is `ds:x`; an empty arg in the middle stays as `::`. */
export function makeSpec(kind, ...args) {
  const a = args.map((x) => (x == null ? '' : String(x)))
  while (a.length && a[a.length - 1] === '') a.pop()
  return [kind, ...a.map(encArg)].join(':')
}

/** Attach (or with null, remove) an evidence chain. */
export function withChain(spec, chain) {
  const base = String(spec || '').split('~')[0]
  return chain && chain.dir && chain.qid ? `${base}~q:${encArg(chain.dir)}:${encArg(chain.qid)}` : base
}

function parseQuery(q) {
  const out = {}
  for (const kv of String(q || '').split('&')) {
    if (!kv) continue
    const i = kv.indexOf('=')
    out[i < 0 ? kv : kv.slice(0, i)] = i < 0 ? '1' : kv.slice(i + 1)
  }
  return out
}

/** Parse `#/rig/...` into a full state object. Anything malformed falls back to a default,
 *  never throws: a bad link opens the workbench, not a blank page. */
export function parseHash(hash) {
  const raw = String(hash || '').replace(/^#\/?rig\/?/, '')
  const qi = raw.indexOf('?')
  const path = qi < 0 ? raw : raw.slice(0, qi)
  const q = parseQuery(qi < 0 ? '' : raw.slice(qi + 1))
  let panes = path
    ? path.split('|').slice(0, 2).map((ps) => {
      let active = 0
      const tabs = ps.split('+').filter(Boolean).map((t, i) => {
        if (t[0] === '!') { active = i; return t.slice(1) }
        return t
      }).filter(Boolean)
      return { tabs, active: Math.min(active, Math.max(0, tabs.length - 1)) }
    }).filter((p) => p.tabs.length)
    : []
  if (!panes.length) panes = DEFAULT_STATE.panes.map((p) => ({ ...p, tabs: [...p.tabs] }))
  let cond = null
  if (q.c) {
    const [dir, model, harness] = q.c.split(':').map(decArg)
    if (dir) cond = { dir, model: model || null, harness: harness || null }
  }
  const focus = Math.max(0, Math.min(Number.parseInt(q.f || '0', 10) || 0, panes.length - 1))
  return {
    panes,
    focus,
    dock: DOCKS.includes(q.dock) ? q.dock : null,
    pal: q.pal === '1',
    sheet: q.sheet === 'tabs' ? 'tabs' : null,
    oracle: SUITES.includes(q.o) ? q.o : 'hidden',
    cond,
    theme: THEMES.includes(q.theme) ? q.theme : null,
    lab: q.lab === '1' ? true : q.lab === '0' ? false : null,
    max: q.max === '1',
  }
}

/** Serialise a state back to a hash. parseHash(toHash(s)) deep-equals s for any valid s. */
export function toHash(state) {
  const s = { ...DEFAULT_STATE, ...state }
  const panes = (s.panes || []).filter((p) => p.tabs && p.tabs.length)
  const path = panes.map((p) => p.tabs.map((t, i) => (i === p.active && i !== 0 ? '!' : '') + t).join('+')).join('|')
  const q = []
  if (s.dock) q.push('dock=' + s.dock)
  if (s.pal) q.push('pal=1')
  if (s.sheet) q.push('sheet=' + s.sheet)
  if (s.focus && panes.length > 1) q.push('f=' + s.focus)
  if (s.oracle && s.oracle !== 'hidden') q.push('o=' + s.oracle)
  if (s.cond && s.cond.dir) q.push('c=' + [s.cond.dir, s.cond.model || '', s.cond.harness || ''].map(encArg).join(':').replace(/:+$/, ''))
  if (s.theme) q.push('theme=' + s.theme)
  if (s.lab === true) q.push('lab=1')
  if (s.lab === false) q.push('lab=0')
  if (s.max) q.push('max=1')
  return '#/rig/' + path + (q.length ? '?' + q.join('&') : '')
}
