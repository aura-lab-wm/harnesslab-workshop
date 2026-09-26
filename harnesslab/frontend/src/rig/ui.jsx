/* ====================================================================================
   Rig · ui.jsx — shared primitives. Views compose these; they never restyle them.
   Every class is `rg-*` (see rig.css). Props are documented on each export.
   ==================================================================================== */
import { useEffect, useState, useSyncExternalStore } from 'react'
import { useRig } from './context'
import { makeSpec, withChain, parseSpec } from './route'
import { useCaseFile, togglePin } from './caseFile'
import { useDatasets, useDataset, useOutcomes, verdict, short, fmt, isFailureMode, resolveCondition } from './data'
import { QUESTION_META } from './answers'

export const MOD = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '') ? '⌘' : 'Ctrl'
const cx = (...a) => a.filter(Boolean).join(' ')

/* ---------------------------------------------------------------- icons */
const IC = {
  data: <><path d="M4 5h16M4 12h16M4 19h16" /><path d="M8 3v4M14 10v4M10 17v4" /></>,
  db: <><ellipse cx="12" cy="5.5" rx="7.5" ry="2.5" /><path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13" /><path d="M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5" /></>,
  sidebar: <><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="M9.5 4.5v15" /></>,
  updown: <path d="M8 9l4-4 4 4M8 15l4 4 4-4" />,
  an: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  traj: <path d="M3 12h4l3-7 4 14 3-7h4" />,
  src: <><path d="M12 3.5v11M7.5 10l4.5 4.5 4.5-4.5" /><path d="M4 14.5v5h16v-5" /></>,
  sent: <><path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" /><path d="M12 8v5M12 16v.01" /></>,
  canvas: <><rect x="3" y="3" width="7" height="6" /><rect x="14" y="15" width="7" height="6" /><rect x="14" y="3" width="7" height="6" /><path d="M10 6h4M17.5 9v6" /></>,
  set: <><circle cx="12" cy="12" r="2.8" /><path d="M10.3 3h3.4l.6 2.6 1.9 1.1 2.5-.8 1.7 2.9-2 1.8v2.2l2 1.8-1.7 2.9-2.5-.8-1.9 1.1-.6 2.6h-3.4l-.6-2.6-1.9-1.1-2.5.8-1.7-2.9 2-1.8v-2.2l-2-1.8 1.7-2.9 2.5.8 1.9-1.1z" /></>,
  guide: <><path d="M4 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z" /><path d="M20 4h-4a2 2 0 0 0-2 2" /><path d="M20 4v14h-6" /></>,
  search: <><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></>,
  split: <><rect x="3" y="4" width="18" height="16" /><path d="M12 4v16" /></>,
  max: <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />,
  unmax: <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />,
  buddy: <><path d="M4 5h16v11H9l-5 4z" /><path d="M8 10h.01M12 10h.01M16 10h.01" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5" /></>,
  moon: <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />,
  sys: <><rect x="3" y="4" width="18" height="12" /><path d="M8 20h8M12 16v4" /></>,
  tabs: <><rect x="3" y="6" width="18" height="14" /><path d="M3 10h18M8 6V3h13v13h-3" /></>,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  chev: <path d="M6 9l6 6 6-6" />,
  up: <path d="M6 15l6-6 6 6" />,
  right: <path d="M9 6l6 6-6 6" />,
  ext: <path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" />,
  cap: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /></>,
  log: <path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5" />,
  pin: <><path d="M9 3h6l-1 6 3 3H7l3-3z" /><path d="M12 12v9" /></>,
  case: <><rect x="3" y="7" width="18" height="13" /><path d="M9 7V4h6v3M3 12h18" /></>,
  q: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17v.01" /></>,
  down: <path d="M12 4v12M6 10l6 6 6-6M4 20h16" />,
  fork: <><circle cx="6" cy="5" r="2" /><circle cx="18" cy="5" r="2" /><circle cx="12" cy="19" r="2" /><path d="M6 7v3a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7M12 13v4" /></>,
  cmp: <><rect x="3" y="4" width="7" height="16" /><rect x="14" y="4" width="7" height="16" /></>,
  pkg: <><path d="M3 7l9-4 9 4v10l-9 4-9-4z" /><path d="M3 7l9 4 9-4M12 11v10" /></>,
}
/** <Icon name="search" size={16}/> — stroke icon, aria-hidden. */
export function Icon({ name, size = 18 }) {
  return <svg className="rg-ico" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">{IC[name] || null}</svg>
}
/** <Kbd>{MOD} K</Kbd> */
export function Kbd({ children }) { return <span className="rg-kbd">{children}</span> }

/* ---------------------------------------------------------------- verdicts & bars */
const VWORD = { true: 'pass', false: 'fail', null: 'unknown' }
/** <Verdict v={true|false|null}/> — ✓ / × / dashed ?. Unknown is never drawn as a failure. */
export function Verdict({ v }) {
  const k = v === true ? 'p' : v === false ? 'f' : 'u'
  return <span className={`rg-v ${k}`} role="img" aria-label={VWORD[String(v === true ? true : v === false ? false : null)]}>{v === true ? '✓' : v === false ? '×' : '?'}</span>
}
/** <VerdictText v words={['PASS','FAIL','UNKNOWN']}/> */
export function VerdictText({ v, words = ['PASS', 'FAIL', 'UNKNOWN'] }) {
  const k = v === true ? 'p' : v === false ? 'f' : 'u'
  return <span className={`rg-vt ${k}`}>{v === true ? '✓ ' + words[0] : v === false ? '× ' + words[1] : '? ' + words[2]}</span>
}
/** <OutcomeBar p f u tall?/> — stacked pass / fail / unknown(hatched). */
export function OutcomeBar({ p = 0, f = 0, u = 0, tall = false }) {
  const n = p + f + u || 1
  return (
    <div className={cx('rg-obar', tall && 'tall')} role="img" aria-label={`${p} pass, ${f} fail, ${u} unknown`}>
      <i className="p" style={{ width: `${(p / n) * 100}%` }} /><i className="f" style={{ width: `${(f / n) * 100}%` }} /><i className="u" style={{ width: `${(u / n) * 100}%` }} />
    </div>
  )
}
/** <CiBar lo hi pt min=0 max=1 zero?/> — interval with point estimate; `zero` draws a reference tick. */
export function CiBar({ lo, hi, pt, min = 0, max = 1, zero }) {
  const X = (v) => `${Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100)).toFixed(2)}%`
  if (lo == null || hi == null) return <div className="rg-cibar" role="img" aria-label="no interval" />
  return (
    <div className="rg-cibar" role="img" aria-label={`95% CI ${fmt.pct(lo)} to ${fmt.pct(hi)}`}>
      {zero != null && <span className="z" style={{ left: X(zero) }} />}
      <span className="ci" style={{ left: X(lo), width: `calc(${X(hi)} - ${X(lo)})` }} />
      {pt != null && <span className="pt" style={{ left: `calc(${X(pt)} - 1px)` }} />}
    </div>
  )
}
/** <HBar label value max fmt/> — one labelled horizontal bar (value/max), value text right. */
export function HBar({ label, value, max = 1, text, tone }) {
  const w = value == null || !max ? 0 : Math.max(0, Math.min(1, value / max)) * 100
  return (
    <div className="rg-hb">
      <span className="nm" title={String(label)}>{label}</span>
      <span className="bt"><i className={tone || ''} style={{ width: `${w}%` }} /></span>
      <span className="vl">{text ?? (value == null ? '—' : String(value))}</span>
    </div>
  )
}

/* ---------------------------------------------------------------- containers */
/** <Panel label meta? el? flush? id?>…</Panel> — hairline panel with a header strip. */
export function Panel({ label, meta, el, flush, id, className, children, actions }) {
  return (
    <section className={cx('rg-pnl', className)} data-el={el} id={id} aria-label={typeof label === 'string' ? label : undefined}>
      <div className="rg-pnl-h"><span className="rg-lbl">{label}</span>{(meta || actions) && <span className="meta">{meta}{actions}</span>}</div>
      <div className={cx('rg-pnl-b', flush && 'flush')}>{children}</div>
    </section>
  )
}
/** <Stat label el?>…</Stat> — one cell of a `.rg-stats` row. */
export function Stat({ label, el, children, className }) {
  return <div className={cx('rg-stat', className)} data-el={el}><span className="rg-lbl">{label}</span>{children}</div>
}
/** <Tag tone="sky|red|acc|amb|dash" nc?>…</Tag> — nc = no caps. acc = accent/selection, amb = caution. */
export function Tag({ tone, nc, title, children, el }) {
  return <span className={cx('rg-tag', tone, nc && 'nc')} title={title} data-el={el}>{children}</span>
}
/** <Note tone="acc|amb|red|sky">…</Note> */
export function Note({ tone, children, el }) { return <div className={cx('rg-note', tone)} data-el={el}>{children}</div> }

/** <Seg options={[[value,label],…]|[value,…]} value onChange label el/> — segmented control. */
export function Seg({ options, value, onChange, label, el }) {
  return (
    <div className="rg-seg" role="group" aria-label={label} data-el={el}>
      {options.map((o) => {
        const [v, l] = Array.isArray(o) ? o : [o, o]
        return <button type="button" key={v} className={v === value ? 'on' : ''} aria-pressed={v === value} onClick={() => onChange(v)}>{l}</button>
      })}
    </div>
  )
}
/** <SearchBox value onChange placeholder el id?/> — `/` focuses the first one in the focused doc. */
export function SearchBox({ value, onChange, placeholder, el, id }) {
  return (
    <label className="rg-search" data-el={el}>
      <span className="sr-only">{placeholder}</span><Icon name="search" size={14} />
      <input className="rg-inp" id={id} type="search" value={value} placeholder={placeholder} autoComplete="off" onChange={(e) => onChange(e.target.value)} />
      <Kbd>/</Kbd>
    </label>
  )
}
/** <Switch checked onChange label el/> — role=switch. */
export function Switch({ checked, onChange, label, el }) {
  return <button type="button" role="switch" aria-checked={!!checked} className="rg-sw" data-el={el} onClick={() => onChange(!checked)}><span className="rg-sw-tr" /><span>{label}</span></button>
}

/* ---------------------------------------------------------------- navigation */
/** <Go spec side? className? title? el?>label</Go> — opens a tab. ⌘/Ctrl-click opens it to the side. */
export function Go({ spec, side, className, children, title, el, chain, replace, label }) {
  const { openTab } = useRig()
  return (
    <button type="button" className={className || 'rg-go'} title={title} aria-label={label} data-el={el} data-spec={spec}
      onClick={(e) => { e.preventDefault(); openTab(spec, { side: side || e.metaKey || e.ctrlKey, chain, replace }) }}>{children}</button>
  )
}

/* ---------------------------------------------------------------- states */
/** <Loading label/> — a quiet placeholder shaped like a document; figures never render before
 *  their denominators arrive. `label` says what is being read (e.g. "Reading /api/overview…"). */
export function Loading({ label = 'Loading…' }) {
  return (
    <div className="rg-state rg-loading" data-el="loading-state" aria-busy="true" role="status">
      <div className="rg-row rg-loading-l"><span className="rg-pulse" aria-hidden="true" /><span>{label}</span></div>
      <div className="rg-skel" style={{ height: 22, width: '36%' }} />
      <div className="rg-skel" style={{ height: 12, width: '58%' }} /><div className="rg-skel" style={{ height: 12, width: '44%' }} />
      <div className="rg-skel" style={{ height: 96, marginTop: 8 }} />
    </div>
  )
}
/** Plain-words reading of a failed request: what failed and the likely reason. */
export function errorWords(error) {
  const msg = String(error && error.message ? error.message : error || 'unknown error')
  if (/failed to fetch|networkerror|load failed|econnrefused|network request failed/i.test(msg)) return { msg, why: 'The backend did not answer. Is it running (python -m harnesslab)? This page fills in by itself as soon as it answers again.' }
  if (/\b404\b|not found/i.test(msg)) return { msg, why: 'The backend does not know this path — the dataset or run may have been moved or renamed.' }
  if (/\b5\d\d\b|internal server error/i.test(msg)) return { msg, why: 'The backend hit an error computing this. Retrying may help; the server log has the details.' }
  return { msg, why: null }
}
/** <ErrorState error onRetry? what?/> — a failed request shows nothing in its place: no zeros.
 *  DEAD-END RULE: names what failed in plain words, offers Retry (when possible) and a way back. */
export function ErrorState({ error, onRetry, what = 'This document' }) {
  const { msg, why } = errorWords(error)
  return (
    <div className="rg-state rg-errstate" data-el="error-state" role="alert">
      <div className="rg-empty-t"><span className="rg-v f" aria-hidden="true">×</span><b>{what} did not load.</b></div>
      <p className="rg-empty-why">{why || 'The request failed.'} Nothing is shown in its place — no partial numbers, no zeros.</p>
      <p className="rg-errstate-msg"><span className="rg-mute">Error · </span><span className="rg-mono">{msg}</span></p>
      <NextSteps actions={[onRetry && { label: 'Retry', onClick: onRetry, primary: true }, { label: 'Back to datasets', spec: 'home' }]} />
    </div>
  )
}
/** <Empty title actions? el? about?>why it is empty</Empty>
 *  DEAD-END RULE: an empty state says why it is empty AND offers at least one way forward.
 *  `actions`: [{label, spec?, onClick?, href?, copy?, primary?}] — see NextSteps.
 *  `about` (optional): longer explanation, tucked behind an "About this" disclosure.
 *  `more` (optional): extra nodes rendered after the actions (e.g. ways forward that load). */
export function Empty({ title, children, actions, el = 'empty-state', about, more }) {
  return (
    <div className="rg-empty" data-el={el}>
      <div className="rg-empty-t"><Verdict v={null} /><b>{title}</b></div>
      {children && <div className="rg-empty-why">{children}</div>}
      {actions && actions.filter(Boolean).length > 0 && <NextSteps actions={actions} />}
      {more}
      {about && <About>{about}</About>}
    </div>
  )
}
/** <NextSteps actions/> — a row of ways forward. Used by Empty and anywhere a flow ends.
 *  Each action: {label, spec} opens a tab · {label, onClick} runs a function · {label, href}
 *  follows a link · {copy: 'python -m …', label?} shows a copyable terminal command.
 *  `primary` marks the one main action. Falsy entries are skipped. */
export function NextSteps({ actions }) {
  return (
    <div className="rg-next" data-el="next-steps">
      {actions.filter(Boolean).map((a, i) => a.copy ? <CopyCmd key={i} cmd={a.copy} label={a.label} />
        : a.spec ? <Go key={i} spec={a.spec} side={a.side} className={cx('rg-btn', a.primary && 'pri')} title={a.title}>{a.label}</Go>
          : a.href ? <a key={i} href={a.href} className={cx('rg-btn', a.primary && 'pri')}>{a.label}</a>
            : <button key={i} type="button" className={cx('rg-btn', a.primary && 'pri')} onClick={a.onClick} title={a.title}>{a.label}</button>)}
    </div>
  )
}
/** <CopyCmd cmd label?/> — a terminal command with a Copy button, for when the only way
 *  forward is the terminal. The command stays selectable text either way. */
export function CopyCmd({ cmd, label }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    let ok = false
    try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(cmd); ok = true } } catch { ok = false }
    setDone(ok)
    toast(ok ? 'Copied the command — paste it in a terminal' : 'Copy is blocked here — select the command and copy it by hand')
  }
  return (
    <span className="rg-copy" data-el="copy-command">
      {label && <span className="rg-copy-l">{label}</span>}
      <code className="rg-copy-c">{cmd}</code>
      <button type="button" className="rg-btn ghost rg-copy-b" onClick={copy} aria-label={`Copy command: ${cmd}`}>{done ? 'Copied' : 'Copy'}</button>
    </span>
  )
}
/** <About summary?>longer explanation</About> — explanatory copy longer than ~2 lines lives
 *  behind this disclosure so the view leads with the answer or the action. */
export function About({ summary = 'About this', children, el }) {
  return <details className="rg-about" data-el={el}><summary>{summary}</summary><div className="rg-about-b">{children}</div></details>
}
/** <Why id?>reason</Why> — the visible reason a control is disabled (DEAD-END RULE: a disabled
 *  control always says why, and what to do to enable it). Pair with aria-describedby. */
export function Why({ id, children }) {
  return <span className="rg-why" id={id} data-el="disabled-reason">{children}</span>
}
let _why = 0
/** <Btn disabled why onClick …>label</Btn> — a button that, when disabled, shows its reason. */
export function Btn({ disabled, why, className, children, ...rest }) {
  const [id] = useState(() => 'rg-why-' + (++_why))
  return (
    <span className="rg-btnwrap">
      <button type="button" className={cx('rg-btn', className)} disabled={disabled} aria-describedby={disabled && why ? id : undefined} {...rest}>{children}</button>
      {disabled && why && <Why id={id}>{why}</Why>}
    </span>
  )
}
/* Kinds whose first argument is a dataset directory (route.js grammar). */
const DIR_KINDS = ['ds', 'q', 'an', 'field', 'tasks', 'task']
function editDistance(a, b) {
  const m = a.length, n = b.length
  if (Math.abs(m - n) > 6) return 99
  let prev = Array.from({ length: n + 1 }, (_, j) => j)
  for (let i = 1; i <= m; i++) {
    const cur = [i]
    for (let j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
    prev = cur
  }
  return prev[n]
}
/** Dataset names close to `name` (substring either way, or a few typos away), best first. */
export function similarDatasets(name, names) {
  const q = String(name || '').toLowerCase()
  if (!q) return []
  return names.map((n) => {
    const l = n.toLowerCase()
    const d = l === q ? 99 : l.includes(q) || q.includes(l) ? 0 : editDistance(q, l)
    return [n, d]
  }).filter(([, d]) => d <= Math.max(2, Math.floor(q.length / 4))).sort((a, b) => a[1] - b[1]).slice(0, 2).map(([n]) => n)
}
/** <NotFound spec detail?/> — the not-found document. Never a crash; never a dead end: it
 *  offers search, the library, a near-miss dataset when the link names one, and closing the tab. */
export function NotFound({ spec, detail }) {
  const { setPalette, state, closeTab } = useRig()
  const what = String(spec || '').split('~')[0]
  const { kind, args } = parseSpec(what)
  const names = (useDatasets().data || []).map((r) => r.name)
  const near = DIR_KINDS.includes(kind) && args[0] && !names.includes(args[0]) ? similarDatasets(args[0], names) : []
  let at = null
  ;((state && state.panes) || []).forEach((p, pi) => { const i = p.tabs.indexOf(spec); if (i >= 0 && !at) at = [pi, i] })
  return (
    <div className="rg-dpad"><div className="rg-state-doc" data-el="not-found">
      <div className="rg-eyebrow">Not found · <span className="rg-mono">{what}</span></div>
      <h2>Nothing in the loaded data answers to <span className="rg-mono">{what.split(':').slice(1).join(':') || what}</span>.</h2>
      <p className="rg-empty-why">{detail || 'The link may name a dataset, run or view that is not in data/runs on this machine.'}</p>
      {near.length > 0 && <div className="rg-col rg-state-near"><span className="rg-lbl">Did you mean</span>
        <NextSteps actions={near.map((n) => ({ label: makeSpec(kind, n, ...args.slice(1)), spec: makeSpec(kind, n, ...args.slice(1)), primary: true }))} /></div>}
      <NextSteps actions={[
        { label: <>Search everything <Kbd>{MOD} K</Kbd></>, onClick: () => setPalette(true), primary: !near.length },
        { label: 'Datasets', spec: 'home' },
        at && { label: 'Close this tab', onClick: () => closeTab(at[0], at[1]) },
      ]} />
    </div></div>
  )
}

/* ---------------------------------------------------------------- confirm step for writes */
/** <ConfirmAction label confirmLabel? detail? onConfirm disabled? danger?/> — every write
 *  (launch, import, capture control, key save) goes through an explicit second click. */
export function ConfirmAction({ label, confirmLabel = 'Confirm', detail, onConfirm, disabled, danger, el, why }) {
  const [armed, setArmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [id] = useState(() => 'rg-why-' + (++_why))
  if (!armed) return (
    <span className="rg-btnwrap">
      <button type="button" className={cx('rg-btn', danger ? 'warn' : 'pri')} disabled={disabled} data-el={el} aria-describedby={disabled && why ? id : undefined} onClick={() => setArmed(true)}>{label}</button>
      {disabled && why && <Why id={id}>{why}</Why>}
    </span>
  )
  return (
    <span className="rg-confirm" role="group" aria-label={`Confirm: ${label}`} data-el={el}>
      {detail && <span className="rg-small rg-dim">{detail}</span>}
      <button type="button" className={cx('rg-btn', danger ? 'warn' : 'pri')} disabled={busy}
        onClick={async () => { setBusy(true); try { await onConfirm() } finally { setBusy(false); setArmed(false) } }}>{busy ? 'Working…' : confirmLabel}</button>
      <button type="button" className="rg-btn ghost" disabled={busy} onClick={() => setArmed(false)}>Cancel</button>
    </span>
  )
}

/* ---------------------------------------------------------------- failure modes */
const SHORT_MODE = { cutoff_no_patch: 'cut off', step_limit_no_patch: 'step limit', no_patch: 'no patch', wrong_patch: 'wrong patch', harness_error: 'harness error', strengthened_only: 'strengthened only' }
const STRONG_ONLY = { id: 'strengthened_only', label: 'strengthened only', meaning: 'Passed the hidden suite; failed the extra cases of the strengthened suite.' }
/** The mode a failed run shows under a suite, or null. Modes are hidden-suite modes from
 *  /api/outcomes; under `strengthened` a hidden pass that fails the strengthened suite is
 *  'strengthened_only'. A run that did not fail under `oracle` has no failure mode. */
export function failureModeOf(run, oracle = 'hidden') {
  if (!run || verdict(run, oracle) !== false) return null
  if (isFailureMode(run.mode)) return run.mode
  if (oracle === 'strengthened' && run.hid === true && run.str === false) return 'strengthened_only'
  return null
}
/** <FailureChip run oracle?/> or <FailureChip mode dir/> — "cut off · no patch" etc.
 *  Renders nothing for a pass or an unknown grade. Tooltip + aria carry the mode's meaning. */
export function FailureChip({ run, mode, dir, oracle }) {
  const rig = useRig()
  const o = oracle || rig.oracle
  const m = mode || failureModeOf(run, o)
  const out = useOutcomes(m && m !== 'strengthened_only' ? dir || (run && run.ds) : null)
  if (!m || !isFailureMode(m)) return null
  const meta = m === 'strengthened_only' ? STRONG_ONLY : (out.data && out.data.byId[m]) || { label: m.replace(/_/g, ' '), meaning: '' }
  return <span className={cx('rg-fchip', m === 'wrong_patch' || m === 'strengthened_only' ? 'wp' : m === 'harness_error' ? 'he' : 'np')} title={meta.meaning} aria-label={`${meta.label}${meta.meaning ? ': ' + meta.meaning : ''}`} data-mode={m}>{meta.label}</span>
}
/** Summary of failure modes over runs: "3 failed: 3 cut off" (null when none failed). */
export function failureSummary(runs, oracle = 'hidden') {
  const failed = (runs || []).filter((r) => verdict(r, oracle) === false)
  if (!failed.length) return null
  const counts = {}
  let unexplained = 0
  for (const r of failed) { const m = failureModeOf(r, oracle); if (m) counts[m] = (counts[m] || 0) + 1; else unexplained++ }
  const parts = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([m, n]) => `${n} ${SHORT_MODE[m] || m.replace(/_/g, ' ')}`)
  if (unexplained) parts.push(`${unexplained} mode not recorded`)
  return `${failed.length} failed: ${parts.join(', ')}`
}
/** <FailureSummary runs/> */
export function FailureSummary({ runs }) {
  const { oracle } = useRig()
  const s = failureSummary(runs, oracle)
  return s ? <span className="rg-small rg-red-t" data-el="failure-summary">{s}</span> : null
}

/* ---------------------------------------------------------------- pin to case file */
/** <PinButton item={{id, kind, label, spec, …}} compact?/> — toggles the item in the case file. */
export function PinButton({ item, compact }) {
  const items = useCaseFile()
  const on = items.some((x) => x.id === item.id)
  const { toast, setDock } = useRig()
  return (
    <button type="button" className={cx('rg-pin', on && 'on', compact && 'cmp')} aria-pressed={on} title={on ? 'Unpin from case file' : 'Pin to case file'}
      aria-label={`${on ? 'Unpin' : 'Pin'} ${item.label}`}
      onClick={(e) => { e.stopPropagation(); togglePin(item); toast(on ? `Unpinned ${item.label}` : `Pinned ${item.label} to the case file`, on ? null : { label: 'Open case file', onClick: () => setDock('case') }) }}>
      <Icon name="pin" size={13} />{!compact && <span>{on ? 'Pinned' : 'Pin'}</span>}
    </button>
  )
}

/* ---------------------------------------------------------------- Asking… sentence */
function Bracket({ label, value, options, onChange, el }) {
  return (
    <label className="rg-brk" data-el={el}>
      <span className="sr-only">{label}</span>
      <select value={value || ''} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  )
}
/** <AskingSentence dir model? harness?/> — "Asking [dataset] about [model] under [harness],
 *  graded by the [suite] suite." Every bracket is a live control on the shared condition
 *  (useRig().setCondition / setOracle), the same state as the status bar. */
export function AskingSentence({ dir, model, harness }) {
  const { oracle, setOracle, setCondition, conditionFor } = useRig()
  const ds = useDatasets()
  const row = useDataset(dir).data
  const c = row ? resolveCondition(row, model || conditionFor(dir).model, harness || conditionFor(dir).harness) : null
  if (!ds.data || !row || !c) return null
  return (
    <p className="rg-asking" data-el="asking-sentence condition-picker">
      <span className="rg-q-mark" aria-hidden="true">?</span>
      Asking <Bracket label="Dataset" el="condition-dataset" value={dir} options={ds.data.map((r) => [r.name, r.name])} onChange={(v) => setCondition({ dir: v })} />
      {' '}about <Bracket label="Model" el="condition-model" value={c.model} options={row.models.map((m) => [m, short(m)])} onChange={(v) => setCondition({ dir, model: v, harness: c.harness })} />
      {' '}under <Bracket label="Harness" el="condition-harness" value={c.harness} options={row.harnesses.map((h) => [h, h])} onChange={(v) => setCondition({ dir, model: c.model, harness: v })} />,
      {' '}graded by the <Bracket label="Test suite" el="harness-oracle-picker" value={oracle} options={[['visible', 'visible'], ['hidden', 'hidden'], ['strengthened', 'strengthened']]} onChange={setOracle} /> suite.
    </p>
  )
}

/* ---------------------------------------------------------------- evidence chain */
/** <EvidenceChain dir qid runId? seq? step="answer|figure|runs|run|event" count?/> —
 *  Answer → Figure → Runs → Run → Event. Each reachable step is a link (deep-linkable spec);
 *  unreachable steps are shown disabled with the reason as a tooltip. */
export function EvidenceChain({ dir, qid, runId, seq, step, count, runLabel }) {
  const { openTab } = useRig()
  const chain = { dir, qid }
  const meta = QUESTION_META[qid]
  const steps = [
    ['answer', 'Answer', makeSpec('q', dir, qid), meta ? meta.short : qid],
    ['figure', 'Figure', makeSpec('q', dir, qid, 'figure'), null],
    ['runs', 'Runs', makeSpec('q', dir, qid, 'runs'), count != null ? fmt.int(count) : null],
    ['run', 'Run', runId ? withChain(makeSpec('run', runId), chain) : null, runLabel || (runId ? String(runId).slice(-6) : null)],
    ['event', 'Event', runId && seq != null ? withChain(makeSpec('span', runId, seq), chain) : null, seq != null ? '#' + seq : null],
  ]
  return (
    <ol className="rg-chain" aria-label="Evidence chain" data-el="evidence-chain">
      {steps.map(([k, l, spec, em], i) => (
        <li key={k}>
          {spec
            ? <button type="button" aria-current={step === k ? 'step' : undefined} onClick={(e) => openTab(spec, { side: e.metaKey || e.ctrlKey })}><b>{i + 1}</b>{l}{em && <em>{em}</em>}</button>
            : <span className="off" title={k === 'event' ? 'Open a run, then an event, to reach this step' : 'Open one of the runs to reach this step'}><b>{i + 1}</b>{l}</span>}
        </li>
      ))}
    </ol>
  )
}

/* ---------------------------------------------------------------- toast */
const toastStore = { msg: null, action: null, n: 0, l: new Set() }
/** toast(message, action?) — a transient status line. `action` = {label, onClick} adds the next
 *  step to a flow that just ended ("Pinned … · Open case file"); such a toast stays longer and
 *  holds while hovered or focused. */
export function toast(msg, action) {
  toastStore.msg = msg; toastStore.action = action && action.label && action.onClick ? action : null
  toastStore.n++; for (const f of [...toastStore.l]) f()
}
const subToast = (f) => { toastStore.l.add(f); return () => toastStore.l.delete(f) }
/** Rendered once by RigApp. */
export function Toaster() {
  const n = useSyncExternalStore(subToast, () => toastStore.n, () => 0)
  const [show, setShow] = useState(false)
  const [hold, setHold] = useState(false)
  useEffect(() => { if (n) setShow(true) }, [n])
  useEffect(() => {
    if (!n || !show || hold) return
    const t = setTimeout(() => setShow(false), toastStore.action ? 6000 : 2400)
    return () => clearTimeout(t)
  }, [n, show, hold])
  const act = show ? toastStore.action : null
  return (
    <div className={cx('rg-toast', show && 'show', act && 'act')} role="status" aria-live="polite"
      onMouseEnter={() => setHold(true)} onMouseLeave={() => setHold(false)} onFocus={() => setHold(true)} onBlur={() => setHold(false)}>
      {show ? toastStore.msg : ''}
      {act && <button type="button" className="rg-btn rg-toast-b" onClick={() => { setShow(false); setHold(false); act.onClick() }}>{act.label}</button>}
    </div>
  )
}

/* ---------------------------------------------------------------- run helpers for lists */
/** <RunRef run/> — "c95aff" link that opens run:<id> (inherits an evidence chain). */
export function RunRef({ run, chain }) {
  if (!run) return null
  return <Go spec={chain ? withChain(makeSpec('run', run.id), chain) : makeSpec('run', run.id)} className="rg-tlink" title={run.id}>{String(run.id).slice(-6)}</Go>
}
