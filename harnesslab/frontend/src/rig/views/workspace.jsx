/* ====================================================================================
   Rig · views/workspace.jsx — the workbench's machine-side documents. Owner: Phase 2 (workspace).

     sources    results on disk (+ descriptions) · three ways to add runs (import a path,
                download the SWE-agent sample, session capture) · harness versions
     capture    session capture (PRIVATE: every request carries the x-harnesslab-private header)
     sentinel   early warning on a partial trajectory: AUC, operating points, weights, histogram,
                trained-model leaderboard (+ EW-AUC@k replay), detector catalogue, rule check
     canvas     studies → models → cells (pass@1, CI, n) with paired-Δ wires and the cell detail
     settings   appearance (theme / density / motion), Student Lab, Buddy, narrator key, lab key, paths
     guide      Student Lab reading guide     package   school replication package (reference)
   + dock `capture` (Capture watcher).

   Every write goes through <ConfirmAction> and the SAME endpoint the classic component uses:
     POST /api/import (method/Sources.jsx ImportTrace) · POST /api/real/import (SweAgentImport) ·
     POST /api/capture/control via captureControl() (method/Capture.jsx) · POST /api/settings/key
     (LabKey.jsx). Browser-held keys use matekey.js exactly as BuddySettings/MateSettings do.
   Static exports (IS_STATIC) hide or disable every write.

   Round 3 (dead ends + breathing): a disabled control shows why next to it (<Btn why>,
   <ConfirmAction why>); an empty or unavailable state offers a way forward — when the terminal
   is the only way, a copyable command (<CopyCmd>); flows end in a next step ("Open <dataset>").
   Documents stack their sections (<Sec>: a heading and whitespace, not a box) and long
   explanations sit behind <About> disclosures.

   Local helpers that belong in the shell (see report): density + reduced-motion preferences are
   applied here as data-rig-density / data-rig-motion on the `.rig` root, because RigApp only
   owns the theme today. Sec / About / CopyCmd are local; they would serve every view from ui.jsx.
   ==================================================================================== */
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, IS_STATIC, captureControl } from '../../api'
import schoolPackage from '../../../../school-package.json'
import { arrival, suggestResultsDir, cleanResultsDir, detectState, progressPct, progressKnown, progressPhrase, verdictTile } from '../../method/importing'
import { browserStores, readMate, writeKey, forgetKey, writeModel, isKeyRemembered, setCaptured, DEFAULT_MODEL } from '../../matekey'
import { KEY_HAS_INNER_WHITESPACE, validateKey } from '../../mate/openrouter'
import { buddySettings } from '../../workspace/buddyClient'
import BuddyModelPicker from '../../workspace/BuddyModelPicker'
import { makeSpec } from '../route'
import {
  useApi, useOverview, useDatasets, useSettings, useSentinel, useMetrics, useComparisons, useHarnessVersions, useJobs, useRuns,
  invalidate, short, fmt, verdict, conditionRuns, tally, paths,
} from '../data'
import { useRig, useTabState } from '../context'
import { downloadText } from '../caseFile'
import { Icon, Kbd, MOD, Stat, Tag, Note, Seg, Switch, Go, Loading, ErrorState, Empty, NextSteps, Btn, ConfirmAction, CiBar, PinButton, FailureChip } from '../ui'
import './workspace.css'

const { pct, int, fx, usd, pp, plural } = fmt
const NOTES = schoolPackage.dataset_notes || {}
const PRIV = { private: true }
const cx = (...a) => a.filter(Boolean).join(' ')
const errText = (e) => String((e && e.message) || e || 'unknown error')
const day = (iso) => { const d = new Date(iso); return Number.isFinite(d.getTime()) ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—' }
/** An epoch second as its own timestamp, never "2 hours ago" (method/Capture.jsx stamp()). */
const stamp = (at) => { const d = new Date((at || 0) * 1000); return at && Number.isFinite(d.getTime()) ? d.toISOString().slice(0, 19).replace('T', ' ') : '—' }

/* ================================================================ local primitives */
function Head({ eyebrow, title, children, actions, el }) {
  return (
    <div className="rg-dh" data-el={el}>
      <div className="t"><div className="rg-eyebrow">{eyebrow}</div><h1>{title}</h1>{children && <p>{children}</p>}</div>
      {actions}
    </div>
  )
}
const StaticNote = ({ what }) => <Note el="read-only-export-state"><div><b>Read-only export.</b> {what} needs the live app (<span className="rg-mono">python -m harnesslab</span>).</div></Note>

/** <Sec title meta? lead? el?>…</Sec> — a section of a document: a heading and whitespace, no box.
 *  Boxes stay for units of data (a table, a list); sections are separated by the page rhythm. */
function Sec({ title, meta, lead, el, className, children }) {
  return (
    <section className={cx('rg-ws-sec', className)} data-el={el} aria-label={typeof title === 'string' ? title : undefined}>
      <div className="rg-ws-sech"><h2>{title}</h2>{meta ? <span className="rg-ws-meta">{meta}</span> : null}</div>
      {lead ? <p className="rg-ws-lead">{lead}</p> : null}
      {children}
    </section>
  )
}
/** <SlotSec> — a Sec with fixed slots (heading · lead · status · field · actions · rest). Two of
 *  them side by side in .rg-ws-pairrow share their rows (CSS subgrid), so the key fields and the
 *  buttons of both panels sit on the same lines however long either description is. */
function SlotSec({ title, meta, lead, el, status, field, actions, rest }) {
  return (
    <section className="rg-ws-sec rg-ws-slots" data-el={el} aria-label={typeof title === 'string' ? title : undefined}>
      <div className="rg-ws-sech"><h2>{title}</h2>{meta ? <span className="rg-ws-meta">{meta}</span> : null}</div>
      <p className="rg-ws-lead">{lead}</p>
      <div className="rg-ws-slot">{status}</div>
      <div className="rg-ws-slot">{field}</div>
      <div className="rg-ws-slot">{actions}</div>
      <div className="rg-ws-slot rg-ws-rest">{rest}</div>
    </section>
  )
}
/** <About summary>…</About> — explanatory copy longer than about two lines goes behind a disclosure,
 *  so the view leads with the answer or the action. */
function About({ summary = 'About this', el, children }) {
  return <details className="rg-ws-about" data-el={el}><summary>{summary}</summary><div className="rg-ws-aboutb">{children}</div></details>
}
/** <CopyCmd cmd label?/> — a terminal command with a Copy button: the way forward when only the
 *  terminal can do the thing (start a watcher, backfill, evaluate a rule on a server without a route). */
function CopyCmd({ cmd, label, el = 'copy-command' }) {
  const { toast } = useRig()
  const copy = () => {
    const fail = () => toast('Copy did not work here — select the command and copy it')
    try { navigator.clipboard.writeText(cmd).then(() => toast('Command copied'), fail) } catch { fail() }
  }
  return (
    <div className="rg-ws-cmd" data-el={el}>
      {label && <span className="rg-ws-cmdl">{label}</span>}
      <div className="rg-ws-cmdr"><code className="rg-mono">{cmd}</code><button type="button" className="rg-btn" onClick={copy} aria-label={`Copy the command: ${cmd}`}>Copy</button></div>
    </div>
  )
}
/** <Failed what error onRetry/> — a request that failed: what, in plain words, then Retry and a way back. */
function Failed({ what, error, onRetry, back = true }) {
  return (
    <div className="rg-ws-err" role="alert" data-el="error-state">
      <p><b>{what}</b> {errText(error)}</p>
      <NextSteps actions={[onRetry && { label: 'Retry', onClick: onRetry, primary: true }, back && { label: 'Back to datasets', spec: 'home' }]} />
    </div>
  )
}
/** Quote a word for a POSIX shell only when it needs it (a leading ~/ stays outside the quotes). */
const SHELL_SAFE = /^[\w@%+=:,./~-]+$/
function shq(s) {
  const t = String(s ?? '')
  if (t && SHELL_SAFE.test(t)) return t
  const q = (x) => `'${x.replace(/'/g, "'\\''")}'`
  return t.startsWith('~/') ? '~/' + q(t.slice(2)) : q(t)
}

/* ================================================================ appearance prefs (shell-level) */
const DENSITY_KEY = 'rig.density', MOTION_KEY = 'rig.motion'
const readPref = (k, ok, d) => { try { const v = localStorage.getItem(k); return ok.includes(v) ? v : d } catch { return d } }
const writePref = (k, v) => { try { localStorage.setItem(k, v); return localStorage.getItem(k) === v } catch { return false } }
/** Stamp the stored density / motion preference onto every `.rig` root on the page. */
export function applyRigPrefs() {
  if (typeof document === 'undefined') return 0
  const els = document.querySelectorAll('.rig')
  const d = readPref(DENSITY_KEY, ['compact', 'comfortable'], 'compact'), m = readPref(MOTION_KEY, ['on', 'off'], 'on')
  for (const el of els) { el.setAttribute('data-rig-density', d); el.setAttribute('data-rig-motion', m) }
  return els.length
}
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  let tries = 0
  const raf = window.requestAnimationFrame || ((f) => setTimeout(f, 16))
  const tick = () => { if (applyRigPrefs() || tries++ > 90) return; raf(tick) }
  window.addEventListener('hashchange', () => { tries = 0; tick() })
  tick()
}

/* ================================================================ SOURCES */
/** Adapter names as people say them. Unknown names show raw. */
const TOOL = { claude_code: 'Claude Code', codex: 'Codex', cursor: 'Cursor', gemini_cli: 'Gemini CLI', qwen_code: 'Qwen Code', inspect: 'Inspect', swe_agent: 'SWE-agent', trajectory: 'Trajectory v1', openhands: 'OpenHands' }
const tool = (n) => TOOL[n] || n
/** The private results directory (backend/results_scope.py PRIVATE_DIRS): the only place a live
 *  session store may be imported into; it is held back from /api/results and every export. */
const PRIVATE_DIR = 'captured'
/** Where a tool keeps its sessions by default, as capture/adapters.py documents it (default_roots).
 *  Used only when /api/import/sources names no folder, and always to recognise a live session store. */
const DOCUMENTED_FOLDERS = [['claude_code', '~/.claude/projects/'], ['codex', '~/.codex/sessions/'], ['cursor', '~/.cursor/projects/'], ['gemini_cli', '~/.gemini/tmp/'], ['qwen_code', '~/.qwen/projects/']].map(([source, path]) => ({ source, path }))
/** The session folder each adapter's description names ("~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl"
 *  → "~/.codex/sessions/"): the path up to its first templated segment. */
export function sessionFolders(adapters) {
  const out = []
  for (const a of adapters || []) {
    const m = String((a && a.description) || '').match(/~\/[^\s)]+/)
    if (!m) continue
    const keep = []
    for (const seg of m[0].split('/')) { if (/[<*{]|^(YYYY|MM|DD)$|^[^.].*\.\w+$/.test(seg)) break; keep.push(seg) }   // a dot-folder stays; a file name ends it
    if (keep.length >= 2 && !out.some((f) => f.path === keep.join('/') + '/')) out.push({ source: a.name, path: keep.join('/') + '/' })
  }
  return out
}
/** The live session store a path lies in (by its typed ~ form, or the server's expanded path), or null.
 *  The server is the authority (capture/adapters.py is_private_source); this only steers the default. */
function storeOf(typed, det, folders) {
  const real = det && det.path ? String(det.path) : ''
  for (const f of folders) {
    const base = f.path.replace(/\/$/, ''), tail = base.slice(1)
    if (typed === base || typed.startsWith(base + '/')) return f
    if (real && (real.replace(/\/$/, '').endsWith(tail) || real.includes(tail + '/'))) return f
  }
  return null
}
const openSpecOf = (dir) => (dir === PRIVATE_DIR ? 'capture' : makeSpec('ds', dir))
const openLabelOf = (dir) => (dir === PRIVATE_DIR ? 'Open Session capture' : `Open ${dir}`)

function Sources({ spec }) {
  const ds = useDatasets()
  const st = useSettings()
  const rig = useRig()
  const { datasets } = rig
  if (ds.error) return <ErrorState error={ds.error} onRetry={ds.reload} what="Results on disk" />
  if (!ds.data) return <Loading label="Reading /api/overview…" />
  const all = [...ds.data].sort((a, b) => a.name.localeCompare(b.name))
  const total = all.reduce((a, r) => a + (r.runs || 0), 0)
  const vdir = (datasets.find((d) => d.name === 'llma4se_live') || all.find((d) => d.kind === 'recorded') || all[0] || {}).name
  return (
    <div className="rg-dpad rg-ws rg-ws-doc">
      <Head eyebrow="Sources & machine" title="Results on disk">
        <span className="rg-mono rg-small">{st.data ? st.data.results_root : 'data/runs'} · {int(total)} runs in {plural(all.length, 'directory', 'directories')}</span>
      </Head>
      <div className="rg-pnl" data-el="results-on-disk">
        <div className="rg-tblwrap"><table className="rg-tbl rg-ws-disk">
          <thead><tr><th>directory</th><th className="rg-num">runs</th><th className="rg-hide-sm">models</th><th className="rg-hide-sm">updated</th><th><span className="sr-only">Open</span></th></tr></thead>
          <tbody>{all.map((r) => (
            <tr key={r.name} className="hov" tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) rig.openTab(makeSpec('ds', r.name), { side: e.metaKey || e.ctrlKey }) }}
              onClick={(e) => { if (!e.target.closest('button,a')) rig.openTab(makeSpec('ds', r.name), { side: e.metaKey || e.ctrlKey }) }}>
              <td>
                <div className="rg-row rg-wrap"><Go spec={makeSpec('ds', r.name)} className="rg-tlink"><b>{r.name}</b></Go><Tag tone={r.kind === 'mock' ? 'dash' : undefined}>{r.kind === 'mock' ? 'mock' : 'recorded'}</Tag></div>
                <div className="rg-small rg-dim rg-ws-desc">{NOTES[r.name] || <span className="rg-mute">No description ships with this directory.</span>}</div>
                <div className="rg-tiny rg-mute rg-mono rg-show-sm">{r.models.length === 1 ? short(r.models[0]) : `${r.models.length} models`} · {day(r.updated)}</div>
              </td>
              <td className="rg-num rg-mono">{int(r.runs)}</td>
              <td className="rg-mono rg-small rg-hide-sm" title={r.models.join('\n')}>{r.models.length === 1 ? short(r.models[0]) : `${r.models.length} models`}</td>
              <td className="rg-mono rg-small rg-hide-sm rg-ws-nowrap">{day(r.updated)}</td>
              <td className="rg-num"><Go spec={makeSpec('ds', r.name)} className="rg-rowgo" label={`Open ${r.name}`}><Icon name="right" size={15} /></Go></td>
            </tr>))}</tbody>
        </table></div>
      </div>
      <Sec title="Add runs" el="add-runs" lead="Three ways in. Pick the one that matches what you have.">
        <ImportTrace spec={spec} datasets={all} />
        <SweAgentImport datasets={all} />
        <Road n={3} title="Record your own sessions" el="road-capture">
          <p className="rg-ws-lead">Session capture follows Claude Code, Codex, Cursor, Gemini CLI and Qwen Code on this machine by itself, so nobody types a path for those.</p>
          <NextSteps actions={[{ label: 'Open Session capture', spec: 'capture' }]} />
        </Road>
      </Sec>
      {vdir && <HarnessVersions dir={vdir} />}
    </div>
  )
}

/** One road into data/runs: a numbered sub-section of "Add runs". */
function Road({ n, title, el, children }) {
  return (
    <section className="rg-ws-road" data-el={el} aria-label={title}>
      <h3><span className="rg-ws-rn" aria-hidden="true">{n}</span>{title}</h3>
      {children}
    </section>
  )
}

/** Port of method/Sources.jsx ImportTrace on Rig primitives, same endpoints:
 *  GET /import/sources · GET /import/status (polled while running) · GET /import/detect ·
 *  POST /import {path, results_dir, source} behind a confirm. */
function ImportTrace({ spec, datasets }) {
  const srcs = useApi(IS_STATIC ? null : '/import/sources')
  const [path, setPath] = useTabState(spec, 'imp.path', '')
  const [source, setSource] = useTabState(spec, 'imp.src', '')
  const [typed, setTyped] = useTabState(spec, 'imp.dir', null)
  const [sniffed, setSniffed] = useState(null)
  const [msg, setMsg] = useState('')
  const [pollOn, setPollOn] = useState(false)
  const st = useApi(IS_STATIC ? null : '/import/status', { poll: pollOn ? 800 : 0 })
  const s = st.data || {}
  const running = s.status === 'running'
  useEffect(() => { setPollOn(running) }, [running])
  const was = useRef(s.status)
  useEffect(() => {
    if (was.current === 'running' && (s.status === 'done' || s.status === 'error')) invalidate('/overview')
    was.current = s.status
  }, [s.status])

  const sniff = (q, now) => {
    let dead = false
    const t = setTimeout(() => {
      api('/import/detect?path=' + encodeURIComponent(q))
        .then((det) => { if (!dead) setSniffed({ path: q, det, error: '' }) })
        .catch((e) => { if (!dead) setSniffed({ path: q, det: null, error: errText(e) }) })
    }, now ? 0 : 350)
    return () => { dead = true; clearTimeout(t) }
  }
  useEffect(() => { const q = path.trim(); if (!q || IS_STATIC) return undefined; return sniff(q, false) }, [path]) // eslint-disable-line react-hooks/exhaustive-deps

  if (IS_STATIC) return <Road n={1} title="Import files you already have" el="import-trace"><StaticNote what="Importing" /></Road>
  const adapters = (srcs.data && srcs.data.sources) || []
  const derived = sessionFolders(adapters)
  const chips = [...(derived.length ? derived : DOCUMENTED_FOLDERS)].sort((x, y) => tool(x.source).localeCompare(tool(y.source)))
  const stores = [...derived, ...DOCUMENTED_FOLDERS.filter((f) => !derived.some((x) => x.path === f.path))]
  const p = path.trim()
  const fresh = sniffed && sniffed.path === p ? sniffed : null
  const det = fresh ? fresh.det : null
  const sniffing = !!p && !fresh
  const d = detectState(det, source)
  const store = p ? storeOf(p, det, stores) : null
  const suggested = store ? PRIVATE_DIR : d.source ? suggestResultsDir(d.source, p) : ''
  const dir = (typed == null ? suggested : typed).trim()
  const exists = !!dir && datasets.some((x) => x.name === dir)
  const forcedOther = !!source && !!det && !!det.source && source !== det.source
  const nothing = !!det && det.exists && !!det.source && !forcedOther && det.sessions === 0
  // the adapter itself failed to read the path (e.g. a missing package): the import would fail the same way
  const unreadable = !!det && det.exists && !!det.source && !forcedOther && !!det.error && det.sessions == null
  const fixCmd = unreadable ? ((String(det.error).match(/pip install [^\n;]+/) || [])[0] || '').trim() : ''
  const failed = s.status === 'error' || !!msg
  const block = running ? 'An import is already running — wait for it to finish'
    : !p ? 'Enter a path, then Inspect it'
      : sniffing ? 'Wait for Inspect to finish'
        : fresh.error ? 'Inspect did not answer — inspect again'
          : !det.exists ? 'Nothing at this path — check it, or pick a folder above'
            : !d.ready ? 'No adapter recognises this path — choose one under Adapter'
              : nothing ? 'Nothing to import: no sessions found here'
                : unreadable ? `The ${d.source} adapter cannot read this here — see above`
                : !dir ? 'Name a results directory'
                  : ''
  const picked = adapters.find((a) => a.name === source)
  const inspect = () => { setSniffed(null); sniff(p, true) }
  const pick = (f) => { setPath(f.path); setTyped(null); setMsg('') }
  const again = () => { setPath(''); setTyped(null); setSource(''); setMsg('') }
  const start = async () => {
    setMsg('')
    try {
      await api('/import', { method: 'POST', body: { path: p, results_dir: dir, source: source || null } })
      setPollOn(true); st.reload()
    } catch (e) { setMsg(errText(e)) }
  }
  const top = det && (det.candidates || []).find((c) => c.source === det.source)
  const ses = (k) => (k == null ? 'sessions' : `${int(k)} session${k === 1 ? '' : 's'}`)
  const others = d.others.length > 0 && <p className="rg-tiny rg-mute rg-mono">also scored {d.others.map((c) => `${c.source} ${Number(c.confidence).toFixed(2)}`).join(' · ')}{det && det.is_dir ? ' (mean over the files each one won)' : ''}</p>
  let found = null
  if (!p) found = null
  else if (sniffing) found = <p className="rg-ws-wait">Inspecting <span className="rg-mono">{p}</span>… a large folder takes a few seconds.</p>
  else if (fresh.error) found = <><p><b>Inspect did not answer.</b> {fresh.error}</p><NextSteps actions={[{ label: 'Inspect again', onClick: inspect }]} /></>
  else if (!det.exists) found = <p><b>Nothing at <span className="rg-mono">{det.path || p}</span>.</b> Check the spelling, or pick one of the folders above.</p>
  else if (!d.source) found = <><p><b>No adapter recognises this path.</b> Choose one under Adapter to import it anyway, or point at a session file or a folder of them.</p>{others}</>
  else {
    found = <>
      <p>{det.source
        ? <><b>{det.sessions == null ? `Recognised as ${tool(det.source)} format` : `Found ${ses(det.sessions)} in ${tool(det.source)} format`}</b>{top ? ` (confidence ${Number(top.confidence).toFixed(2)})` : ''}.</>
        : <><b>The sniffer does not recognise this path.</b></>}
        {forcedOther && ` You chose ${tool(source)}, so Import uses it instead.`}
        {!det.source && source && ` You chose ${tool(source)}, so Import reads it that way.`}</p>
      {unreadable
        ? <><p>But the <span className="rg-mono">{d.source}</span> adapter could not read it on this server: <span className="rg-mono">{det.error}</span>. Import would fail the same way, so it waits until that is fixed.</p>
          {fixCmd && <CopyCmd label="Install what it needs on the server, then Inspect again" cmd={fixCmd} el="import-fix" />}</>
        : nothing
        ? <p>The {d.source} adapter reads no session here, so Import would write nothing. Point at another folder.</p>
        : <p>Import reads {forcedOther || !det.source || det.sessions == null ? 'the sessions' : det.sessions === 1 ? 'it' : 'them'} through the <span className="rg-mono">{d.source}</span> adapter and writes one run per session to <span className="rg-mono">data/runs/{dir || '…'}</span>{!dir ? ' — name the directory below.' : dir === PRIVATE_DIR ? ', the private directory; sessions already in it are skipped.' : exists ? ', which already exists: sessions already in it are skipped.' : ', a new directory.'}</p>}
      {!unreadable && det.error && <p className="rg-small">The sniffer also reported: <span className="rg-mono">{det.error}</span></p>}
      {store && <p>{tool(store.source)} keeps its live sessions here, so they stay private: {dir === PRIVATE_DIR
        ? <>every listing and export holds <span className="rg-mono">{PRIVATE_DIR}</span> back.</>
        : <>the server imports them only into <span className="rg-mono">{PRIVATE_DIR}</span>. <button type="button" className="rg-tlink" onClick={() => setTyped(null)}>Use {PRIVATE_DIR}</button></>}
        {' '}<Go spec="capture" className="rg-tlink">Session capture</Go> can also follow this folder by itself.</p>}
      {others}
    </>
  }
  const history = Array.isArray(s.history) ? s.history : []
  const earlier = history.slice(s.status === 'done' && s.result ? 1 : 0).filter((h) => h && h.results_dir)
  const fixPrivate = /session store/i.test(msg || s.error || '') && dir !== PRIVATE_DIR
  return (
    <Road n={1} title="Import files you already have" el="import-trace">
      <p className="rg-ws-lead">Point at a session file or a folder of them from another agent harness. Each session becomes one run, with the same ledger the lab&apos;s own runner writes.</p>
      <About summary="About importing" el="import-about">
        <p><b>Capture</b> follows the interactive CLIs on this machine by itself ({(adapters.some((a) => arrival(a.name) === 'capture') ? adapters.filter((a) => arrival(a.name) === 'capture').map((a) => a.name) : DOCUMENTED_FOLDERS.map((f) => f.source)).map(tool).join(', ')}); nobody types a path for those. <b>Import</b> is for everything else ({adapters.filter((a) => arrival(a.name) === 'import').map((a) => tool(a.name)).join(', ') || 'none listed'}), and for a session store you want read once.</p>
        <p>Inspect sniffs the path first: which adapter recognises it, how confidently, and how many sessions it holds. Nothing is written until you confirm the import. Importing into a directory that exists adds to it and skips runs already there.</p>
        {picked && <p><b>{tool(picked.name)}</b>: {picked.description}. Recognises <span className="rg-mono">{(picked.patterns || []).join('  ·  ')}</span>.</p>}
      </About>
      <div className="rg-ws-form">
        <label className="rg-fld">Path to a session file or folder
          <input className="rg-inp" value={path} spellCheck={false} autoComplete="off" placeholder="e.g. ~/.codex/sessions/2026/07/11/ or logs/run.eval" onChange={(e) => { setPath(e.target.value); setMsg('') }} />
        </label>
        <div className="rg-ws-chips" data-el="path-suggestions">
          <span className="rg-ws-chipsl">{derived.length ? 'Where these tools keep sessions, from the adapters' : 'Where these tools keep sessions by default'} — suggestions, checked when you pick one:</span>
          {chips.map((f) => <button type="button" key={f.path} className={cx('rg-ws-chip', p === f.path && 'on')} aria-pressed={p === f.path} onClick={() => pick(f)}><span>{tool(f.source)}</span><span className="rg-mono rg-mute">{f.path}</span></button>)}
        </div>
        {srcs.error && <Failed what="Could not load the adapters." error={srcs.error} onRetry={srcs.reload} back={false} />}
        {found && <div className="rg-ws-found" role="status" aria-live="polite" data-el="import-detect">{found}</div>}
        <div className="rg-ws-pair">
          <label className="rg-fld">Adapter
            <select className="rg-selc" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Adapter">
              <option value="">auto — detect from the path</option>
              {adapters.map((a) => <option key={a.name} value={a.name}>{tool(a.name)} · {a.name}</option>)}
            </select>
          </label>
          <label className="rg-fld">Results directory, under data/runs
            <input className="rg-inp" value={typed == null ? suggested : typed} spellCheck={false} placeholder="e.g. imported_codex_0711" onChange={(e) => setTyped(cleanResultsDir(e.target.value))} />
          </label>
        </div>
        {typed != null && suggested && typed !== suggested && <p className="rg-small rg-mute">Inspect suggests <button type="button" className="rg-tlink" onClick={() => setTyped(null)}>{suggested}</button>.</p>}
        <div className="rg-row rg-wrap rg-ws-acts">
          <Btn disabled={!p || sniffing} why={!p ? 'Enter a path first' : 'Inspecting…'} onClick={inspect}>Inspect</Btn>
          <ConfirmAction label={running ? 'Importing…' : failed ? 'Retry the import' : 'Import this path'} confirmLabel="Import now" disabled={!!block} why={block} el="import-path"
            detail={`${dir === PRIVATE_DIR ? 'Writes' : exists ? 'Adds to' : 'Creates'} data/runs/${dir || '…'} from ${p || '…'}${source ? ` as ${source}` : ''}.`} onConfirm={start} />
        </div>
        {p && dir && !running && <About summary="Or run the same import from a terminal"><CopyCmd cmd={`harnesslab import ${shq(p)} --results-dir ${shq(dir)}${source ? ` --source ${source}` : ''}`} /></About>}
        {running && (
          <div className="rg-col rg-ws-tight" role="status">
            {progressKnown(s.progress) && <div className="rg-ws-bar"><i style={{ width: `${progressPct(s.progress)}%` }} /></div>}
            <span className="rg-mono rg-small rg-dim">Importing: {progressPhrase(s.progress)} → data/runs/{s.results_dir}</span>
          </div>
        )}
        {failed && !running && (
          <div className="rg-ws-err" role="alert" data-el="import-error">
            <p><b>{msg ? 'The server refused the import.' : `The last import (${s.path || 'a path'} → ${s.results_dir || '…'}) did not finish.`}</b> {msg || s.error}</p>
            <p>Fix the path or the directory above, then use Retry the import.</p>
            <NextSteps actions={[fixPrivate && { label: `Use ${PRIVATE_DIR} instead`, onClick: () => setTyped(PRIVATE_DIR), primary: true }, { label: 'Back to datasets', spec: 'home' }]} />
          </div>
        )}
        {st.error && <Failed what="Could not read the import status." error={st.error} onRetry={st.reload} />}
        {s.status === 'done' && s.result && !running && <ImportResult r={s.result} onAgain={again} />}
        {earlier.length > 0 && (
          <div className="rg-col" data-el="import-history">
            <h4 className="rg-ws-h4">Earlier imports since the server started</h4>
            <ul className="rg-ws-list rg-ws-histl">{earlier.map((h, i) => (
              <li key={i}><span className="rg-mono">{h.results_dir}</span><span className="rg-small rg-dim">{tool(h.source)} · {int(h.imported)} imported · {int(h.skipped)} already there</span><Go spec={openSpecOf(h.results_dir)} className="rg-btn">{openLabelOf(h.results_dir)}</Go></li>))}</ul>
          </div>
        )}
      </div>
    </Road>
  )
}

function ImportResult({ r, onAgain }) {
  const v = verdictTile(r)
  const unknown = Number.isFinite(r.outcomes_unknown) ? r.outcomes_unknown : 0
  const any = r.imported > 0 || r.skipped > 0
  return (
    <div className="rg-ws-result" data-el="import-result">
      <h4 className="rg-ws-h4">Last import · {tool(r.source)} → data/runs/{r.results_dir}</h4>
      <div className="rg-stats rg-ws-s4">
        <Stat label="imported"><span className="rg-fig sm">{int(r.imported)}</span><span className="rg-tiny rg-mute">new runs</span></Stat>
        <Stat label="already there"><span className="rg-fig sm">{int(r.skipped)}</span><span className="rg-tiny rg-mute">re-importing adds nothing</span></Stat>
        <Stat label="tasks"><span className="rg-fig sm">{int(r.n_tasks)}</span><span className="rg-tiny rg-mute">{(r.tasks || []).slice(0, 1).join('') || '—'}</span></Stat>
        <Stat label="real verdicts"><span className={cx('rg-fig sm', v.flagged && 'amb')}>{v.value}</span><span className="rg-tiny rg-mute">{v.note}</span></Stat>
      </div>
      {unknown > 0 && <Note><div>{plural(unknown, 'run')} carried no verdict and {unknown === 1 ? 'imports' : 'import'} with an unknown outcome. Unknown is never a failure here, but pass@1 over this directory counts only the known ones.</div></Note>}
      {(r.harnesses || []).map((h) => <div key={h.harness_id} className="rg-mono rg-tiny rg-dim">{h.harness_id} · {plural(h.runs, 'run')} · hash {h.hash}</div>)}
      {(r.errors || []).slice(0, 4).map((e, i) => <div key={i} className="rg-mono rg-tiny rg-red">{e}</div>)}
      {!any && <p className="rg-small rg-dim">Nothing was imported from this path.</p>}
      <NextSteps actions={[any && r.results_dir && { label: openLabelOf(r.results_dir), spec: openSpecOf(r.results_dir), primary: true }, { label: 'Import another path', onClick: onAgain }]} />
    </div>
  )
}

/** Port of method/Sources.jsx SweAgentImport: POST /real/import {n, model, out} behind a confirm.
 *  The server names the directory real_swe_agent_<n>[_<model>] when `out` is blank, and REWRITES the
 *  index of a directory that already exists (backend/real_import.py opens index.jsonl with "w"). */
function SweAgentImport({ datasets }) {
  const [pollOn, setPollOn] = useState(false)
  const st = useApi(IS_STATIC ? null : '/real/status', { poll: pollOn ? 1500 : 0 })
  const [n, setN] = useState(500)
  const [model, setModel] = useState('')
  const [out, setOut] = useState('')
  const [msg, setMsg] = useState('')
  const s = st.data || {}
  const running = s.status === 'running'
  useEffect(() => { setPollOn(running) }, [running])
  const was = useRef(s.status)
  useEffect(() => {
    if (was.current === 'running' && (s.status === 'done' || s.status === 'error')) invalidate('/overview')
    was.current = s.status
  }, [s.status])
  if (IS_STATIC) return null
  const auto = `real_swe_agent_${n}` + (model ? `_${model.split('/').pop()}` : '')
  const name = out || auto
  const taken = datasets.some((x) => x.name === name)
  let k = 2
  while (datasets.some((x) => x.name === `${name}_${k}`)) k++
  const free = `${name}_${k}`
  const known = [...new Set(datasets.filter((x) => /^real_swe_agent/.test(x.name)).flatMap((x) => x.models || []))].sort()
  const failed = s.status === 'error' || !!msg
  const go = async () => {
    setMsg('')
    try { await api('/real/import', { method: 'POST', body: { n, model: model || null, out: out || null } }); setPollOn(true); st.reload() } catch (e) { setMsg(errText(e)) }
  }
  return (
    <Road n={2} title="Download a public sample" el="swe-agent-import">
      <p className="rg-ws-lead">Streams trajectories from the public nebius/SWE-agent-trajectories set on Hugging Face and writes them as a results directory. The server needs network access.</p>
      <About summary="About the SWE-agent sample">
        <p>Imported runs have no cost ledger and no strengthened oracle; the views that need those say so instead of showing $0. The server also needs the <span className="rg-mono">datasets</span> package (<span className="rg-mono">pip install datasets</span>). The model filter matches a model name exactly.</p>
      </About>
      <div className="rg-ws-form">
        <div className="rg-ws-trip">
          <label className="rg-fld">Runs<input className="rg-inp" type="number" min={10} max={5000} value={n} onChange={(e) => setN(Math.max(10, Math.min(5000, +e.target.value || 10)))} /></label>
          <label className="rg-fld">Model (optional)<input className="rg-inp" value={model} spellCheck={false} placeholder="e.g. swe-agent-llama-70b · blank: all" onChange={(e) => setModel(e.target.value.trim())} /></label>
          <label className="rg-fld">Results directory (optional)<input className="rg-inp" value={out} spellCheck={false} placeholder="e.g. swe_sample" onChange={(e) => setOut(e.target.value.replace(/[^a-zA-Z0-9_-]/g, ''))} /></label>
        </div>
        {known.length > 0 && <div className="rg-ws-chips" data-el="model-suggestions">
          <span className="rg-ws-chipsl">Models in the sample already on disk:</span>
          {known.map((m) => <button type="button" key={m} className={cx('rg-ws-chip', model === m && 'on')} aria-pressed={model === m} onClick={() => setModel(model === m ? '' : m)}><span className="rg-mono">{m}</span></button>)}
        </div>}
        <p className="rg-small rg-dim">Writes <span className="rg-mono">data/runs/{name}</span>{out ? '' : ', named from the run count and the model'}.</p>
        {taken && <Note el="swe-dir-taken"><div><b>data/runs/{name} already exists.</b> This download would rewrite its index. Keep it by naming a new directory. <button type="button" className="rg-tlink" onClick={() => setOut(free)}>Use {free}</button></div></Note>}
        <div className="rg-row rg-wrap rg-ws-acts rg-ws-quiet">
          <ConfirmAction label={running ? 'Downloading…' : failed ? 'Retry the download' : 'Download SWE-agent sample'} confirmLabel="Download & import" disabled={running} why="A download is already running" el="swe-agent-download"
            detail={`Streams ${int(n)} trajectories and ${taken ? 'rewrites the index of' : 'writes'} data/runs/${name}.`} onConfirm={go} />
        </div>
        {running && <p className="rg-mono rg-small rg-dim" role="status">Downloading: {Array.isArray(s.progress) ? `${int(s.progress[0])} of ${int(s.progress[1])} trajectories` : 'starting'}</p>}
        {failed && !running && (
          <div className="rg-ws-err" role="alert">
            <p><b>{msg ? 'The server refused the download.' : 'The last download did not finish.'}</b> {msg || s.error}</p>
            <NextSteps actions={[{ label: 'Back to datasets', spec: 'home' }]} />
          </div>
        )}
        {st.error && <Failed what="Could not read the download status." error={st.error} onRetry={st.reload} />}
        {s.status === 'done' && s.result && !running && (
          <div className="rg-ws-result" data-el="swe-agent-result">
            <p className="rg-small">Downloaded {plural(s.result.n, 'trajectory', 'trajectories')}{Number.isFinite(s.result.resolved) ? ` (${int(s.result.resolved)} resolved)` : ''} into <span className="rg-mono">data/runs/{s.result.out}</span>.</p>
            <NextSteps actions={[{ label: openLabelOf(s.result.out), spec: openSpecOf(s.result.out), primary: true }]} />
          </div>
        )}
      </div>
    </Road>
  )
}

function HarnessVersions({ dir }) {
  const v = useHarnessVersions(dir)
  const list = (v.data && v.data.versions) || []
  const drift = list.some((x) => !x.matches_current)
  const ids = new Set(list.map((x) => x.harness_id))
  return (
    <Sec title={`Harness versions · ${dir}`} el="harness-versions" meta={v.data ? `${plural(list.length, 'version')} · ${list.length > ids.size ? 'several per harness' : 'one per harness'}` : ''}
      lead="Each run records the hash of the harness file it ran under. A drifted version means the file on disk changed after those runs.">
      {v.error ? <Failed what="Could not read the harness versions." error={v.error} onRetry={v.reload} />
        : !v.data ? <div className="rg-skel rg-ws-sk" />
          : <div className="rg-pnl"><div className="rg-tblwrap"><table className="rg-tbl">
            <thead><tr><th>harness</th><th className="rg-hide-sm">hash</th><th className="rg-num">runs</th><th className="rg-num">pass@1</th><th>file</th></tr></thead>
            <tbody>{list.map((x) => (
              <tr key={x.harness_id + x.hash}><td className="rg-mono rg-small">{x.harness_id}</td><td className="rg-mono rg-small rg-hide-sm">{x.hash}</td><td className="rg-num rg-mono">{int(x.runs)}</td><td className="rg-num rg-mono">{pct(x.pass1)}</td>
                <td>{x.matches_current ? <span className="rg-small rg-sky">✓ current</span> : x.has_file ? <span className="rg-small rg-red">× drifted</span> : <span className="rg-small rg-mute">? no file</span>}</td></tr>))}</tbody>
          </table></div></div>}
      {v.data && !drift && list.length > 0 && <p className="rg-small rg-mute">Every recorded version matches its harness file on disk.</p>}
    </Sec>
  )
}

/* ================================================================ CAPTURE (private) */
const WATCH_CMD = 'python -m harnesslab.capture --watch'
const BACKFILL_CMD = 'python -m harnesslab.capture --backfill'
/** Watcher states in which a watcher process is up (it can be paused or nudged). */
const LIVE = ['idle', 'scanning', 'capturing', 'paused', 'locked']
const isWatching = (sn) => LIVE.includes((sn || {}).state)
const STATE_WORD = { never_started: 'Not running', idle: 'Watching', scanning: 'Capturing', capturing: 'Capturing', paused: 'Paused', locked: 'Waiting for the lock', error: 'Stopped on an error', stopped: 'Stopped' }
const stateWord = (sn) => { const k = (sn || {}).state; return STATE_WORD[k] || (k ? String(k).replace(/_/g, ' ') : 'Not running') }

function useCapture(poll = 0) {
  return useApi(IS_STATIC ? null : '/capture/status', { ...PRIV, poll })
}
/** The watcher's state sentence (after method/Capture.jsx), in plain words. */
function watcherSentence(sn) {
  if (!sn || !sn.state || sn.state === 'never_started') return 'Nothing is watching yet. Capture runs only when you start it: a backfill reads every source once; a watcher stays up and reads only what moved.'
  if (sn.state === 'paused') return 'Paused. The watcher is up and deliberately idle; it will not capture until it is resumed.'
  if (sn.state === 'locked') return 'Waiting. Something else holds the capture lock — a manual import, or another pass — and the watcher will try again.'
  if (sn.state === 'error') return 'Not capturing. The watcher hit a problem — see the message below — and what is shown is what it captured before that.'
  if (sn.state === 'stopped') return 'Stopped cleanly. What is shown is what it captured before it went away.'
  if (sn.state === 'scanning' || sn.state === 'capturing') return 'Capturing now. The numbers will move when this pass finishes.'
  return `Watching ${sn.roots || 0} root${(sn.roots || 0) === 1 ? '' : 's'}, every ${Math.round(sn.interval_s || 0)}s. It reads only what changed since its last pass.`
}

/** Pause / resume / capture now — captureControl() (POST /api/capture/control, private header),
 *  each behind a confirm, then re-read the status: control.json is applied between passes.
 *  With nothing watching, the way forward is the terminal: the start command, copyable. */
function WatcherControls({ st, compact }) {
  const [asked, setAsked] = useState('')
  const [err, setErr] = useState(null)
  const s = st.data || {}
  const sn = s.sniffer || {}
  const paused = s.control ? !!s.control.paused : null
  const watching = isWatching(sn)
  const ask = (action) => async () => {
    setErr(null)
    try { await captureControl(action); setAsked(action); st.reload() } catch (e) { setErr(errText(e)) }
  }
  if (IS_STATIC) return <StaticNote what="The capture controls" />
  const detail = compact ? null : 'Writes captured/control.json; the watcher reads it between passes.'
  return (
    <div className="rg-col rg-ws-ctl" data-el="capture-controls">
      {!watching && <CopyCmd label="Start a watcher from a terminal on this machine" cmd={WATCH_CMD} el="capture-start" />}
      <div className="rg-row rg-wrap rg-ws-acts">
        {watching && paused !== true && <ConfirmAction label="Pause capture" confirmLabel="Pause the watcher" detail={detail} onConfirm={ask('pause')} el="capture-pause" />}
        {paused !== false && (watching || paused === true) && <ConfirmAction label="Resume capture" confirmLabel="Resume the watcher" detail={detail} onConfirm={ask('resume')} el="capture-resume" />}
        <ConfirmAction label="Capture now" confirmLabel="Ask for a pass" disabled={!watching || paused === true}
          why={!watching ? 'Nothing is watching — start a watcher first (command above)' : 'Paused — resume capture first'}
          detail={compact ? null : 'Asks a running watcher for one pass now.'} onConfirm={ask('nudge')} el="capture-now" />
      </div>
      {asked && <p className="rg-small rg-dim">Asked. The watcher reads this between passes.</p>}
      {paused === true && !watching && <p className="rg-small rg-dim">A pause still stands: the next watcher starts paused until you resume it.</p>}
      {watching && paused != null && paused !== (sn.state === 'paused') && <p className="rg-small rg-dim">{paused ? 'Pausing' : 'Resuming'} — asked, and applied when the watcher next looks.</p>}
      {err && <div className="rg-ws-err" role="alert"><p><b>The watcher did not take the request.</b> {err}</p><p>Try the same button again, or use the terminal: <span className="rg-mono">python -m harnesslab.capture --pause · --resume · --nudge</span>.</p></div>}
    </div>
  )
}

function Capture() {
  const st = useCapture(IS_STATIC ? 0 : 5000)
  const runs = useApi(IS_STATIC ? null : '/capture/runs?limit=25', PRIV)
  const rel = useApi(IS_STATIC ? null : '/capture/relations', PRIV)
  if (IS_STATIC) return <div className="rg-dpad rg-ws rg-ws-doc" data-el="capture"><Head eyebrow="On this machine" title="Session capture">Captured sessions are private and are excluded from every export.</Head><StaticNote what="Session capture" /></div>
  if (st.error && !st.data) return <ErrorState error={st.error} onRetry={st.reload} what="Session capture" />
  if (!st.data) return <Loading label="Reading /api/capture/status (private)…" />
  const s = st.data
  const t = s.totals || {}
  const sn = s.sniffer || {}
  const open = s.open || []
  const rows = (runs.data && runs.data.rows) || []
  const relations = (rel.data && rel.data.relations) || {}
  const related = Object.entries(relations).filter(([, v]) => (v.superseded_by || []).length || (v.forked_from || []).length)
  const superseded = related.filter(([, v]) => (v.superseded_by || []).length)
  const forked = related.filter(([, v]) => (v.forked_from || []).length && !(v.superseded_by || []).length)
  const cursors = s.cursors || {}
  const um = t.unmeasured_runs || 0
  const none = !(s.runs_indexed > 0) && !open.length
  const watching = isWatching(sn)
  return (
    <div className="rg-dpad rg-ws rg-ws-doc" data-el="capture">
      <Head eyebrow="On this machine" title="Session capture">Your own coding-agent sessions, recorded on this machine and read back as runs.</Head>
      <div className="rg-col rg-ws-intro">
        <Note el="capture-private"><div><b>Private.</b> These runs stay on this machine — hidden from every listing, excluded from every export, and each request this page makes has to ask for them by name.</div></Note>
        <About summary="About session capture">
          <p>Each captured session is read back with the same ledger the lab&apos;s runner writes, so every page measures it like any other corpus. A session becomes a run once it has been quiet long enough to count as finished; until then it is open and counted separately.</p>
          <p>A resumed session supersedes the run it continued; one that diverges is a fork. Sources it can read: {(s.adapters || []).map(tool).join(', ') || 'none reported'}.</p>
        </About>
      </div>
      {none ? (
        <Empty title="Nothing captured yet." el="empty-state capture-empty" actions={[{ label: 'Import a session file instead', spec: 'sources' }]}>
          <p className="rg-ws-p">No session has been recorded on this machine. Record what is already here with one backfill, then start a watcher (below) to keep up as you work.</p>
          <CopyCmd label="Record what is already here" cmd={BACKFILL_CMD} el="capture-backfill" />
        </Empty>
      ) : (
        <div className="rg-stats rg-ws-s5" data-el="capture-kpis">
          <Stat label="runs captured"><span className="rg-fig sm">{int(s.runs_indexed)}</span></Stat>
          <Stat label="still open"><span className="rg-fig sm">{int(s.open_runs)}</span><span className="rg-tiny rg-mute">quiet for less than the segment gap</span></Stat>
          <Stat label="input tokens"><span className="rg-fig sm">{int(t.input_tokens)}</span>{um > 0 && <span className="rg-tiny rg-mute">over the runs that reported usage</span>}</Stat>
          <Stat label="output tokens"><span className="rg-fig sm">{int(t.output_tokens)}</span>{um > 0 && <span className="rg-tiny rg-mute">{plural(um, 'run')} recorded no usage</span>}</Stat>
          <Stat label="cost"><span className="rg-fig sm">{usd(t.cost_usd, 2)}</span><span className="rg-tiny rg-mute">{um ? `priced from the ledger; ${plural(um, 'run')} unpriced` : 'priced from the ledger, not estimated'}</span></Stat>
        </div>
      )}
      <Sec title="The watcher" meta={stateWord(sn)} el="capture-watcher">
        <p className="rg-ws-lead">{watcherSentence(sn)}</p>
        {sn.error ? <p className="rg-mono rg-small rg-dim">{sn.error}</p> : null}
        {watching && <p className="rg-mono rg-small rg-mute">{plural(sn.roots || 0, 'root')} · every {fx(sn.interval_s, 0)}s · last pass {stamp(sn.last_tick_at)} · {plural(sn.errors || 0, 'error')}</p>}
        <WatcherControls st={st} />
        <About summary="Housekeeping and terminal controls" el="capture-housekeeping">
          <dl className="rg-kv">
            <dt>crash debris</dt><dd>{sn.debris ? `${int(sn.debris)} left behind` : sn.debris_checked_at ? 'none' : 'not checked yet'} — {sn.debris_checked_at ? `checked ${stamp(sn.debris_checked_at)}` : 'a watcher counts it once a day, and never removes it'}. <span className="rg-mono">python -m harnesslab.capture --sweep</span></dd>
            <dt>cursors</dt><dd>{plural(cursors.sources || 0, 'source')} remembered · {cursors.seeded ? `${int(cursors.seeded)} adopted from an existing corpus` : 'none adopted — a first watch reads every source once'}. <span className="rg-mono">python -m harnesslab.capture --seed-cursors</span></dd>
            <dt>controls</dt><dd><span className="rg-mono">python -m harnesslab.capture --pause · --resume · --nudge</span></dd>
          </dl>
        </About>
      </Sec>
      {!none && (
        <Sec title="Captured runs" el="capture-runs" meta={runs.data ? `${int(runs.data.total)} total` : ''}>
          <h3 className="rg-ws-h3">Most recently captured</h3>
          {runs.error ? <Failed what="Could not read the captured runs." error={runs.error} onRetry={runs.reload} />
            : rows.length === 0 ? <Empty title="No finished run to list yet." actions={[{ label: 'Import a session file instead', spec: 'sources' }]}><p className="rg-ws-p">Sessions still open are listed below; a backfill records what is already on this machine.</p><CopyCmd cmd={BACKFILL_CMD} /></Empty>
              : <div className="rg-pnl"><ul className="rg-ws-list">{rows.map((r) => (
                <li key={r.run_id} className="rg-ws-capl"><span className="rg-mono rg-ell">{r.task_id || r.run_id}</span><span className="rg-mono rg-small rg-dim rg-ell">{(r.model || '').split('/').pop() || '—'}</span><span className="rg-mono rg-small rg-dim rg-num">{int(r.steps)} steps</span><span className="rg-mono rg-small rg-dim rg-ell">{r.exit_reason || '—'}</span><span className="rg-mono rg-small rg-mute rg-num">{usd(r.cost_usd, 2)}</span></li>))}</ul></div>}
          <h3 className="rg-ws-h3">Open right now <span className="rg-ws-meta">{open.length}</span></h3>
          {open.length === 0 ? <p className="rg-small rg-dim">None open: every captured session has been quiet long enough to count as finished.</p>
            : <div className="rg-pnl"><ul className="rg-ws-list">{open.map((r) => <li key={r.run_id} className="rg-ws-capo"><span className="rg-mono rg-ell">{r.task_id || r.run_id}</span><span className="rg-mono rg-small rg-dim rg-num">{int(r.steps)} steps</span><span className="rg-mono rg-small rg-mute rg-num">{String(r.last_ts || '').slice(0, 19).replace('T', ' ')}</span></li>)}</ul></div>}
          <h3 className="rg-ws-h3">What replaced what <span className="rg-ws-meta">{superseded.length} superseded · {forked.length} forked</span></h3>
          {rel.error ? <Failed what="Could not read which runs replaced which." error={rel.error} onRetry={rel.reload} />
            : related.length === 0 ? <p className="rg-small rg-dim">No captured session has been resumed or forked.</p>
              : <div className="rg-pnl"><ul className="rg-ws-list">{related.slice(0, 25).map(([id, v]) => <li key={id}><span className="rg-mono rg-small rg-ell">{id}</span><span className="rg-small rg-dim">{(v.superseded_by || []).length ? 'superseded' : 'forked'}</span></li>)}</ul></div>}
        </Sec>
      )}
    </div>
  )
}

function CaptureDock() {
  const st = useCapture(IS_STATIC ? 0 : 5000)
  if (IS_STATIC) return <div className="rg-ws-dock"><StaticNote what="The capture watcher" /></div>
  if (st.error && !st.data) return <div className="rg-ws-dock"><Failed what="Could not read the capture watcher." error={st.error} onRetry={st.reload} back={false} /></div>
  if (!st.data) return <div className="rg-ws-dock"><span className="rg-small rg-mute">Reading the watcher (private)…</span></div>
  const s = st.data, sn = s.sniffer || {}
  const watching = isWatching(sn)
  return (
    <div className="rg-ws-dock" data-el="capture-dock">
      <div className="rg-row rg-wrap rg-ws-dockh">
        <span className={cx('rg-ws-dot', ['idle', 'scanning', 'capturing'].includes(sn.state) && 'on')} /><b>{stateWord(sn)}</b>
        {watching && <span className="rg-small rg-mute rg-mono">{plural(sn.roots || 0, 'root')} · every {fx(sn.interval_s, 0)}s{sn.errors ? ` · ${plural(sn.errors, 'error')}` : ''}</span>}
        <span className="rg-grow" /><Go spec="capture" className="rg-btn">Open Session capture <Icon name="right" size={13} /></Go>
      </div>
      <p className="rg-small rg-dim rg-ws-p">{watcherSentence(sn)}</p>
      <WatcherControls st={st} compact />
      <div className="rg-row rg-wrap rg-small rg-dim rg-ws-counts"><span>{s.runs_indexed > 0 || s.open_runs > 0 ? `${int(s.runs_indexed)} captured · ${int(s.open_runs)} open` : 'Nothing captured yet'} · reads {plural((s.adapters || []).length, 'source')}</span><Tag tone="dash" nc>private</Tag></div>
    </div>
  )
}

/* ================================================================ SENTINEL */
/* The model's own feature names in plain English (method/Sentinel.jsx PLAIN); unknown names show raw. */
const PLAIN = {
  n_boundary: 'boundary events so far', edited_since_last_test: 'edited since the last test run', no_test_yet: 'has not run the tests yet',
  steps_without_edit: 'steps without an edit', n_blocked: 'blocked tool calls', test_tamper_attempt: 'tried to change the test suite',
  n_errors: 'tool calls that errored', repeat_calls: 'repeated the same call', max_consecutive_repeat: 'longest repeat streak',
  consecutive_failed_tests: 'failing tests in a row', last_test_failed: 'last test run failed', files_touched: 'files touched',
  n_edits: 'edits made', n_tests: 'test runs', n_reads: 'file reads', n_bash: 'shell calls',
  step_frac: 'share of the step budget used', token_frac: 'share of the token budget used',
}
/** Train + save a sentinel from a terminal in the lab root (backend/sentinel.py train, save_model). */
const trainCmd = (dir) => `python3 -c ${shq(`from harnesslab.backend.sentinel import train, save_model; m, _ = train(["data/runs/${dir}"]); save_model(m, "${dir}")`)}`
/** Evaluate one rule expression with safe_eval over the given feature values (every other feature 0),
 *  exactly as the engine does during a run (backend/sentinel.py safe_eval, rule_env). */
const ruleCmd = (when, env) => `python3 -c ${shq(`from harnesslab.backend.sentinel import safe_eval, rule_env, FEATURE_NAMES as F; print(safe_eval(${JSON.stringify(String(when))}, rule_env({**dict.fromkeys(F, 0.0), **${JSON.stringify(env)}})))`)}`

function Sentinel({ spec }) {
  const sn = useSentinel()
  const { datasets } = useRig()
  const [idx, setIdx] = useTabState(spec, 'op', null)
  if (sn.error) return <ErrorState error={sn.error} onRetry={sn.reload} what="The sentinel model" />
  if (!sn.data) return <Loading label="Reading /api/sentinel…" />
  const model = sn.data.model || null
  const M = (model && model.meta) || null
  if (!M || !M.trained) {
    const dir = (datasets.find((d) => d.name === 'llma4se_live') || datasets.find((d) => d.kind === 'recorded') || datasets[0] || {}).name || 'llma4se_live'
    return (
      <div className="rg-dpad rg-ws rg-ws-doc" data-el="sentinel">
        <Head eyebrow="Sentinel · early warning on a partial trajectory" title="No trained model on disk" />
        <Empty title="Nothing to show yet." actions={[{ label: `Open ${dir}`, spec: makeSpec('ds', dir) }]}>
          <p className="rg-ws-p">An untrained sentinel has no operating points, so this page cannot say what a threshold buys. Train one on recorded runs from a terminal in the lab root:</p>
          <CopyCmd cmd={trainCmd(dir)} el="sentinel-train" />
        </Empty>
        <Leaderboard spec={spec} models={sn.data.models || []} active={sn.data.active} />
        <Detectors spec={spec} />
      </div>
    )
  }
  const ops = M.operating_points || []
  const deployed = (sn.data.default_config && sn.data.default_config.threshold) ?? null
  const di = Math.max(0, ops.findIndex((o) => deployed != null && Math.abs(o.threshold - deployed) < 1e-9))
  const i = idx == null || idx >= ops.length ? di : idx
  const op = ops[i] || null
  const thr = op ? op.threshold : deployed
  const cal = M.calibration || []
  const maxn = Math.max(1, ...cal.map((c) => c.n || 0))
  const buckets = M.auc_by_prefix || []
  const ci = (M.auc_ci && M.auc_ci.any_prefix) || null
  const oi = M.oracle_invisible || null
  const feats = (model.names || []).map((n, k) => ({ raw: n, name: PLAIN[n] || n, w: (model.w || [])[k] })).filter((r) => Number.isFinite(r.w) && Math.abs(r.w) > 1e-6).sort((a, b) => Math.abs(b.w) - Math.abs(a.w)).slice(0, 10)
  const maxw = Math.max(1e-6, ...feats.map((f) => Math.abs(f.w)))
  return (
    <div className="rg-dpad rg-ws rg-ws-doc" data-el="sentinel">
      <Head eyebrow="Sentinel · score the partial trajectory at every step" title="Block the submit when it crosses">
        Model <span className="rg-mono">{sn.data.active || M.name || '—'}</span>, trained {String(M.trained_at || '').slice(0, 10) || '—'} on {int(M.n_runs)} runs ({int(M.n_examples)} prefixes) · {M.folds}-fold · harness filter <span className="rg-mono">{M.harness_filter || 'none'}</span> · fail rate {pct(M.fail_rate)}.
      </Head>
      <div className="rg-stats rg-ws-s4">
        <Stat label="training AUC · all prefixes"><span className="rg-fig md">{fx(M.auc_all_prefixes)}</span><span className="rg-small rg-mono rg-mute">{ci ? `95% [${fx(ci[0])}, ${fx(ci[1])}] · ${M.auc_ci.bootstrap} boot` : 'no interval recorded'}</span></Stat>
        <Stat label="run-weighted AUC"><span className="rg-fig md">{fx(M.auc_run_weighted)}</span><span className="rg-small rg-mono rg-mute">{M.auc_baselines ? `rules only ${fx(M.auc_baselines.rules_only)} · prior ${fx(M.auc_baselines.prior)}` : ''}</span></Stat>
        <Stat label="calibration ECE"><span className="rg-fig md">{fx(M.ece)}</span><span className="rg-small rg-mute">lower is better</span></Stat>
        <Stat label="failures invisible to visible tests"><span className="rg-fig md">{pct(M.oracle_invisible_failure_share, 0)}</span><span className="rg-small rg-mono rg-mute">{oi ? `${oi.invisible} of ${oi.n_fail} fails` : '—'}</span></Stat>
      </div>
      <Sec title="Threshold · operating point" meta={deployed != null ? `deployed at ${fx(deployed)}` : ''} el="sentinel-threshold">
        {ops.length && op ? <div className="rg-ws-2up">
          <div className="rg-ws-col760">
          <h3 className="rg-ws-h3">Operating point</h3>
          <div className="rg-row rg-ws-thr">
            <input type="range" min="0" max={ops.length - 1} step="1" value={i} onChange={(e) => setIdx(Number(e.target.value))} aria-label="Sentinel threshold operating point" aria-valuetext={fx(thr)} />
            <span className="rg-fig sm acc">{fx(thr)}</span>
          </div>
          <div className="rg-row rg-mono rg-tiny rg-mute rg-ws-ticks">{ops.map((o) => <span key={o.threshold}>{fx(o.threshold, 1)}</span>)}</div>
          <div className="rg-stats rg-ws-s3">
            <Stat label="bad submits caught"><span className="rg-fig md">{pct(op.recall, 0)}</span></Stat>
            <Stat label="good submits blocked"><span className="rg-fig md rg-ws-redfig">{pct(op.false_alarm, 0)}</span></Stat>
            <Stat label="mean warning lead"><span className="rg-fig md">{fx(op.mean_lead_steps, 1)}<span className="u"> steps</span></span></Stat>
          </div>
          <p className="rg-ws-lead">At {fx(op.threshold)} the hook blocks about {pct(op.false_alarm, 0)} of good submits to catch {pct(op.recall, 0)} of bad ones, {fx(op.mean_lead_steps, 1)} steps before the end.</p>
          <About summary="About the operating points">
            <p>Only {plural(ops.length, 'operating point')} {ops.length === 1 ? 'was' : 'were'} evaluated when the model was trained; the slider snaps to them rather than inventing points in between. The deployed threshold is the engine&apos;s config and is not changed from here.</p>
          </About>
          </div>
          {cal.length > 0 && <div className="rg-ws-col760">
            <h3 className="rg-ws-h3">Prefix scores by calibration bin</h3>
            <div className="rg-ws-hist" role="img" aria-label={`Prefix count per score bin; ${cal.map((c) => `${c.bin}: ${c.n}`).join(', ')}`}>
              {cal.map((c) => { const lo = parseFloat(String(c.bin).split('-')[0]); return <span key={c.bin} className={cx('b', thr != null && lo >= thr - 1e-9 && 'hi')} style={{ height: `${Math.max(2, (c.n / maxn) * 100)}%` }} title={`${c.bin}: n=${c.n}, fail ${pct(c.fail_rate, 0)}`} /> })}
              {thr != null && <span className="thr" style={{ left: `calc(${thr * 100}% - 1px)` }} />}
            </div>
            <div className="rg-row rg-mono rg-tiny rg-mute rg-ws-ticks"><span>0.0</span><span>score →</span><span>1.0</span></div>
            <p className="rg-small rg-mute">Bars: prefixes per bin (max n={int(maxn)}). Bins right of the blue line would be blocked (vermilion).</p>
          </div>}
        </div> : <Empty title="No operating points." actions={[{ label: 'Replay the models below', onClick: () => { const e = document.querySelector('[data-el~="sentinel-leaderboard"]'); if (e && e.scrollIntoView) e.scrollIntoView({ block: 'start' }) } }]}>This model was saved without an evaluated threshold sweep. Replaying it on a directory scores it on real runs.</Empty>}
      </Sec>
      <Sec title="What the model weighs" meta="logistic · standardised" el="sentinel-features">
        <div className="rg-ws-2up">
        <div className="rg-col rg-ws-col760">
          <h3 className="rg-ws-h3">Feature weights</h3>
          <div className="rg-col rg-ws-feats">
            {feats.map((f) => (
              <div className="rg-ws-feat" key={f.raw}>
                <span className="nm" title={f.raw}>{f.name}</span>
                <span className="bt"><i className={f.w > 0 ? 'pos' : 'neg'} style={f.w > 0 ? { left: '50%', width: `${(f.w / maxw) * 50}%` } : { right: '50%', width: `${(-f.w / maxw) * 50}%` }} /><b /></span>
                <span className={cx('vl', f.w > 0 ? 'rg-red' : 'rg-sky')}>{f.w > 0 ? '+' : '−'}{fx(Math.abs(f.w))}</span>
              </div>))}
            {!feats.length && <p className="rg-small rg-mute">The model carries no non-zero weights.</p>}
          </div>
          <p className="rg-small rg-mute"><span className="rg-red">+</span> pushes toward a bad submit · <span className="rg-sky">−</span> pulls away from one. Bias {fx(model.b)}.</p>
        </div>
          {buckets.length > 0 && <div className="rg-col rg-ws-col760">
            <h3 className="rg-ws-h3">AUC by how much of the trajectory has been seen</h3>
            <div className="rg-ws-prefix" role="img" aria-label={buckets.map((b) => `${b.bucket}: AUC ${fx(b.auc)}`).join(', ')}>
              {buckets.map((b) => <div key={b.bucket}><span className="rg-mono rg-tiny">{fx(b.auc)}</span><i style={{ height: `${Math.max(2, (b.auc - 0.5) * 2 * 44)}px` }} /><span className="rg-mono rg-tiny rg-mute">{b.bucket}</span></div>)}
            </div>
            <p className="rg-small rg-mute">Bars start at 0.5 (chance). Early prefixes are the hard case.</p>
          </div>}
        </div>
      </Sec>
      <Leaderboard spec={spec} models={sn.data.models || []} active={sn.data.active} />
      <Detectors spec={spec} />
      <RuleCheck spec={spec} />
    </div>
  )
}

/** GET /api/sentinel/plugins — the detector catalogue. `?reload=true` re-reads harnesslab/plugins/. */
function Detectors({ spec }) {
  const [reload, setReload] = useTabState(spec, 'plug.reload', 0)
  const pl = useApi(reload ? `/sentinel/plugins?reload=true&n=${reload}` : '/sentinel/plugins')
  const list = (pl.data && pl.data.detectors) || []
  return (
    <Sec title="Detectors" el="sentinel-plugins"
      meta={<>{pl.data ? `${pl.data.n_builtin} built-in · ${pl.data.n_plugin} plugin` : ''}{!IS_STATIC && <button type="button" className="rg-btn ghost" onClick={() => setReload(reload + 1)} title="GET /api/sentinel/plugins?reload=true">Re-read the plugin files</button>}</>}
      lead={pl.data ? <>What the sentinel checks at every step. Plugins are switched on and off in their files under <span className="rg-mono">{pl.data.dir}</span>; this page reads the catalogue and does not edit it.</> : null}>
      {pl.error ? <Failed what="Could not read the detector catalogue." error={pl.error} onRetry={pl.reload} />
        : !pl.data ? <div className="rg-skel rg-ws-sk" />
          : <div className="rg-pnl"><ul className="rg-ws-det">{list.map((d) => (
            <li key={d.id}>
              <div className="rg-row rg-wrap"><b>{d.label}</b><Tag tone={d.severity === 'critical' ? 'red' : d.severity === 'high' ? 'amb' : undefined}>{d.severity}</Tag><Tag tone="dash" nc>{d.source}{d.enabled === false ? ' · off' : ''}</Tag><span className="rg-mono rg-tiny rg-mute">{d.id}</span></div>
              <div className="rg-small rg-dim">{d.why}</div>
              {d.when && <div className="rg-mono rg-small rg-ws-when">when {d.when}</div>}
              {d.nudge && <div className="rg-small rg-mute">nudge: “{d.nudge}”</div>}
            </li>))}
            {pl.data.errors && pl.data.errors.length > 0 && <li className="rg-small rg-red">{pl.data.errors.join(' · ')}</li>}
          </ul></div>}
    </Sec>
  )
}

/** Rule check: evaluates only on the server. A POST to /api/sentinel/plugins/test_rule is offered only
 *  when this backend registers that route (read from /openapi.json). Otherwise the capability is shown
 *  as unavailable, with the reason and the alternative: the same safe_eval, from a terminal. */
function RuleCheck({ spec }) {
  const pl = useApi('/sentinel/plugins')
  const oa = useApi(IS_STATIC ? null : '/../openapi.json')
  const rules = ((pl.data && pl.data.detectors) || []).filter((d) => d.source === 'rule' && d.when)
  const [rid, setRid] = useTabState(spec, 'rule.id', null)
  const [env, setEnv] = useTabState(spec, 'rule.env', {})
  const [res, setRes] = useState(null)
  const r = rules.find((x) => x.id === rid) || rules[0] || null
  const names = r ? [...new Set((String(r.when).match(/[A-Za-z_][A-Za-z0-9_]*/g) || []).filter((w) => !['and', 'or', 'not', 'True', 'False', 'None', 'in'].includes(w)))] : []
  const num = (v) => (v === '' || v == null || !Number.isFinite(Number(v)) ? 0 : Number(v))
  const values = Object.fromEntries(names.map((n) => [n, num(env[n])]))
  const listed = oa.data && typeof oa.data === 'object' && oa.data.paths && typeof oa.data.paths === 'object' ? oa.data.paths : null
  const route = listed ? Object.keys(listed).find((p) => /\/api\/sentinel\/plugins\/test_rule$/.test(p)) : null
  const unread = oa.error ? errText(oa.error) : oa.data && !listed ? 'no OpenAPI document at /openapi.json' : null
  const run = async () => {
    try { const out = await api('/sentinel/plugins/test_rule', { method: 'POST', body: { id: r.id, when: r.when, env: values } }); setRes({ ok: true, out }) } catch (e) { setRes({ ok: false, out: errText(e) }) }
  }
  return (
    <Sec title="Test a rule" meta={r ? <span className="rg-mono">{r.file}</span> : ''} el="sentinel-test-rule"
      lead={r ? 'Pick a rule plugin, set the feature values it reads, and see whether it would fire.' : null}>
      {pl.error ? <Failed what="Could not read the rule plugins." error={pl.error} onRetry={pl.reload} />
        : !pl.data ? <div className="rg-skel rg-ws-sk" />
          : !r ? <Empty title="No rule plugins." actions={[{ label: 'Re-read the plugins', onClick: pl.reload }]}>Only rule plugins (<span className="rg-mono">*.rule.json</span> under <span className="rg-mono">{pl.data.dir}</span>) carry an expression to test. Add one there, then re-read.</Empty>
            : <div className="rg-col rg-ws-form">
              <label className="rg-fld">Rule
                <select className="rg-selc" value={r.id} onChange={(e) => { setRid(e.target.value); setRes(null) }} aria-label="Rule">{rules.map((x) => <option key={x.id} value={x.id}>{x.id}</option>)}</select>
              </label>
              <pre className="rg-pre rg-ws-when">{r.when}</pre>
              <div className="rg-ws-pair">{names.map((n) => (
                <label key={n} className="rg-fld">{n}<input className="rg-inp" type="number" step="any" value={env[n] ?? 0} placeholder="blank counts as 0" onChange={(e) => { setEnv({ ...env, [n]: e.target.value }); setRes(null) }} /></label>))}</div>
              {route && !IS_STATIC ? <div className="rg-row rg-wrap"><button type="button" className="rg-btn pri" onClick={run}>Test rule</button></div>
                : oa.loading && !IS_STATIC ? <p className="rg-small rg-mute">Checking whether this server can test rules…</p>
                  : <div className="rg-ws-unavail" data-el="rule-test-unavailable">
                    <p><b>Testing here is unavailable.</b> {IS_STATIC ? 'A read-only export cannot evaluate anything.'
                      : unread ? <>This server&apos;s route list could not be read ({unread}), so no rule-test route is known.</>
                        : <>This server registers no rule-test route (<span className="rg-mono">POST /api/sentinel/plugins/test_rule</span>).</>} No verdict is shown here rather than a guessed one.</p>
                    <p>Evaluate it with the values above from a terminal in the lab root. It runs the engine&apos;s own <span className="rg-mono">safe_eval</span> and prints True or False:</p>
                    <CopyCmd cmd={ruleCmd(r.when, values)} el="rule-test-command" />
                  </div>}
              {res && <pre className={cx('rg-pre', !res.ok && 'rg-red')}>{typeof res.out === 'string' ? res.out : JSON.stringify(res.out, null, 2)}</pre>}
            </div>}
    </Sec>
  )
}

/** Trained sentinels from /api/sentinel, plus the EW-AUC@k replay (GET /api/sentinel/leaderboard?dir=)
 *  computed on demand, and GET /api/sentinel/export/{name} as a download. */
function Leaderboard({ spec, models, active }) {
  const { datasets } = useRig()
  const [dir, setDir] = useTabState(spec, 'lb.dir', null)
  const [go, setGo] = useTabState(spec, 'lb.go', false)
  const [msg, setMsg] = useState('')
  const d = dir || (datasets.find((x) => x.name === 'live') || datasets[0] || {}).name || null
  const lb = useApi(go && d ? `/sentinel/leaderboard?dir=${encodeURIComponent(d)}` : null)
  const sorted = [...models].sort((a, b) => ((b.meta && b.meta.auc_all_prefixes) ?? -1) - ((a.meta && a.meta.auc_all_prefixes) ?? -1))
  const exp = async (name) => {
    setMsg('')
    try { const b = await api(`/sentinel/export/${encodeURIComponent(name)}`); downloadText(`${name}.sentinel.json`, JSON.stringify(b, null, 2) + '\n', 'application/json') } catch (e) { setMsg(errText(e)) }
  }
  return (
    <Sec title="Trained sentinels" meta={plural(models.length, 'model')} el="sentinel-leaderboard">
      {models.length === 0 ? <p className="rg-small rg-dim">No trained sentinel is registered on this server yet.</p>
        : <div className="rg-pnl"><div className="rg-tblwrap"><table className="rg-tbl">
          <thead><tr><th>model</th><th className="rg-num" title="AUC over all prefixes, out of fold">AUC</th><th className="rg-num rg-hide-sm">run-weighted</th><th className="rg-num">runs</th><th><span className="sr-only">Export</span></th></tr></thead>
          <tbody>{sorted.map((m, k) => (
            <tr key={m.name} className={m.name === active || m.active ? 'sel' : ''}>
              <td className="rg-mono rg-small">{k + 1}. {m.name} {(m.name === active || m.active) && <Tag tone="acc">active</Tag>}</td>
              <td className="rg-num rg-mono">{fx(m.meta && m.meta.auc_all_prefixes)}</td>
              <td className="rg-num rg-mono rg-hide-sm">{fx(m.meta && m.meta.auc_run_weighted)}</td>
              <td className="rg-num rg-mono">{int(m.meta && m.meta.n_runs)}</td>
              <td className="rg-num">{!IS_STATIC && <button type="button" className="rg-btn ghost" onClick={() => exp(m.name)} aria-label={`Export ${m.name}`} title="GET /api/sentinel/export/{name}"><Icon name="down" size={13} /></button>}</td>
            </tr>))}</tbody>
        </table></div></div>}
      {msg && <Failed what="The export did not download." error={msg} back={false} />}
      {!IS_STATIC && models.length > 0 && <>
        <div className="rg-row rg-wrap rg-ws-acts">
          <label className="rg-row rg-small rg-dim">Replay every model on
            <select className="rg-selc" value={d || ''} onChange={(e) => { setDir(e.target.value); setGo(false) }} aria-label="Directory to replay on">{datasets.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}</select>
          </label>
          <Btn disabled={!d || (go && lb.loading)} why={!d ? 'No directory on disk to replay on' : 'Replaying every model on it…'} onClick={() => { if (go) lb.reload(); else setGo(true) }}>Replay on this directory</Btn>
        </div>
        <About summary="Why replay on one directory?">
          <p>The AUCs in the table are each model&apos;s own out-of-fold training report, on different runs. Replaying every model on one directory scores them on the same runs with EW-AUC@k (AUC among runs still alive at step k), which removes the length confound.</p>
        </About>
      </>}
      {go && lb.error && <Failed what={`The replay on ${d} did not finish.`} error={lb.error} onRetry={lb.reload} back={false} />}
      {go && lb.data && <LeaderboardTable lb={lb.data} />}
    </Sec>
  )
}
function LeaderboardTable({ lb }) {
  const ks = lb.k || []
  const ciT = (x, c, d = 2) => (x == null ? '—' : `${fx(x, d)}${c ? ` [${fx(c[0], d)}, ${fx(c[1], d)}]` : ''}`)
  return (
    <div className="rg-col" data-el="sentinel-replay">
      <div className="rg-small rg-mono rg-mute">{lb.dir} · {int(lb.n_runs)} runs ({int(lb.n_fail)} eventual failures) · {int(lb.n_prefixes)} prefixes · recall and false alarm at risk ≥ {lb.threshold} · {lb.bootstrap} bootstrap resamples</div>
      <div className="rg-pnl"><div className="rg-tblwrap"><table className="rg-tbl rg-ws-lbt">
        <thead><tr><th>model</th>{ks.map((k) => <th key={k} className="rg-num">EW-AUC@{k}</th>)}<th className="rg-num">recall</th><th className="rg-num">false alarm</th></tr></thead>
        <tbody>{(lb.rows || []).map((r) => (
          <tr key={r.model} className={r.active ? 'sel' : ''}>
            <td className="rg-mono rg-small">{r.model}{r.in_sample ? ' †' : ''}</td>
            {ks.map((k) => { const p = (r.per_k || []).find((x) => x.k === k); return <td key={k} className="rg-num rg-mono rg-small">{p ? ciT(p.ew_auc, p.ew_auc_ci) : '—'}</td> })}
            <td className="rg-num rg-mono rg-small">{r.overall ? pct(r.overall.recall, 0) : '—'}</td>
            <td className="rg-num rg-mono rg-small">{r.overall ? pct(r.overall.false_alarm, 0) : '—'}</td>
          </tr>))}</tbody>
      </table></div></div>
      {(lb.rows || []).some((r) => r.in_sample) && <p className="rg-small rg-mute">† trained on this directory: in-sample, and therefore optimistic.</p>}
      <p className="rg-small rg-mute">Alive-at-k cohorts: {((lb.rows && lb.rows[0] && lb.rows[0].per_k) || []).map((p) => `k=${p.k}: n=${p.n_alive} (${pct(p.fail_share, 0)} fail)`).join(' · ')}</p>
    </div>
  )
}

/* ================================================================ CANVAS */
const TOP = 40
/** Canvas geometry from the room it has: three columns spread across the available width (wider
 *  nodes, longer wires), rows spaced to the available height, and never below the compact layout
 *  that zoom-to-fit shrinks on a phone. Pure, so it is tested on its own. */
export function canvasLayout(availW, availH, counts) {
  const DELTA = 150                                    // room right of the cells for the paired-Δ loops
  const MINW = 16 + 3 * 200 + 2 * 60 + DELTA
  const w = Math.max(MINW, availW || 0)
  const NW = Math.round(Math.max(200, Math.min(300, w * 0.17)))
  const gap = Math.round(Math.max(60, Math.min(360, (w - 32 - 3 * NW - DELTA) / 2)))
  const used = 3 * NW + 2 * gap + DELTA
  const x0 = Math.max(16, Math.round((w - used) / 2))
  const CX = [x0, x0 + NW + gap, x0 + 2 * (NW + gap)]
  const rows = Math.max(1, counts.s, counts.m, counts.c)
  const room = availH ? (availH - TOP - 32) : 0
  const grow = (base, n) => Math.round(Math.max(base, Math.min(base * 1.5, room && n ? room / n : base)))
  return { NW, CX, W: Math.max(w, x0 + used + 16), pitch: { s: grow(84, counts.s), m: grow(84, counts.m), c: grow(104, counts.c) }, rows }
}
const sepOf = (c) => !!c && Array.isArray(c.ci95) && (c.ci95[0] > 0 || c.ci95[1] < 0)

function Canvas({ spec }) {
  const rig = useRig()
  const { datasets, openTab, setPalette, isMobile } = rig
  const jobs = useJobs()
  const def = (datasets.find((d) => d.name === (rig.condition && rig.condition.dir)) || datasets.find((d) => d.name === 'llma4se_live') || datasets.find((d) => d.kind === 'recorded') || datasets[0] || {}).name || null
  const [dirS, setDir] = useTabState(spec, 'dir', null)
  const dir = datasets.some((d) => d.name === dirS) ? dirS : def
  const row = datasets.find((d) => d.name === dir) || null
  const mt = useMetrics(dir)
  const cmp = useComparisons(dir, 'baseline')
  const [mS, setM] = useTabState(spec, 'm', null)
  const [hS, setH] = useTabState(spec, 'h', null)
  const [zS, setZ] = useTabState(spec, 'z', null)
  const [notes, setNotes] = useTabState(spec, 'notes', [])
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const wrap = useRef(null)
  const [wrapW, setWrapW] = useState(0)
  const [wrapH, setWrapH] = useState(0)
  useEffect(() => {
    const el = wrap.current
    if (!el) return undefined
    const on = () => {
      setWrapW(el.clientWidth)
      // the height the page gives the canvas (not its content height, which would feed back)
      const d = el.closest('.rg-doc')
      if (d) setWrapH(Math.max(0, d.clientHeight - (el.getBoundingClientRect().top - d.getBoundingClientRect().top) - 12))
    }
    on()
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(on); ro.observe(el)
    return () => ro.disconnect()
  }, [mt.data])

  const cells = useMemo(() => ((mt.data && mt.data.cells) || []).filter((c) => c.model && c.harness), [mt.data])
  const models = useMemo(() => [...new Set(cells.map((c) => c.model))].sort(), [cells])
  if (!datasets.length) return <Loading label="Reading /api/overview…" />
  if (!row) return <div className="rg-dpad"><Empty title="No dataset on disk." actions={[{ label: 'Add runs in Sources', spec: 'sources', primary: true }]}>The canvas wires studies to models to cells, and there is no study in data/runs yet.</Empty></div>
  if (mt.error) return <ErrorState error={mt.error} onRetry={mt.reload} what={`The metrics of ${dir}`} />
  if (!mt.data) return <Loading label={`Reading /api/results/${dir}/metrics…`} />
  const model = models.includes(mS) ? mS : models[0] || null
  const mcells = cells.filter((c) => c.model === model).sort((a, b) => (row.harnesses.indexOf(a.harness) - row.harnesses.indexOf(b.harness)) || a.harness.localeCompare(b.harness))
  const base = (cmp.data && cmp.data.baseline) || (row.harnesses.includes('baseline') ? 'baseline' : row.harnesses[0])
  const harness = mcells.some((c) => c.harness === hS) ? hS : (mcells.find((c) => c.harness === base) || mcells[0] || {}).harness || null
  const cell = mcells.find((c) => c.harness === harness) || null
  const C = (cmp.data && cmp.data.comparisons && cmp.data.comparisons[model]) || {}

  /* layout */
  const L = canvasLayout(isMobile ? 0 : wrapW, isMobile ? 0 : wrapH, { s: datasets.length, m: models.length, c: mcells.length })
  const { NW, CX } = L
  // columns are centred on each other, so a short column sits level with the middle of a long one
  const colH = { s: (datasets.length - 1) * L.pitch.s + 68, m: (models.length - 1) * L.pitch.m + 64, c: (mcells.length - 1) * L.pitch.c + 88 }
  const tallest = Math.max(colH.s, colH.m, colH.c)
  const off = (k) => Math.max(0, Math.round((tallest - colH[k]) / 2))
  const N = { s: datasets.map((d, i) => ({ y: TOP + off('s') + i * L.pitch.s, h: 68, d })), m: models.map((m, i) => ({ y: TOP + off('m') + i * L.pitch.m, h: 64, m })), c: mcells.map((c, i) => ({ y: TOP + off('c') + i * L.pitch.c, h: 88, c })) }
  const sIdx = datasets.findIndex((d) => d.name === dir)
  const mIdx = models.indexOf(model)
  const bIdx = mcells.findIndex((c) => c.harness === base)
  const noteY = TOP + off('m') + models.length * L.pitch.m + 12
  const W = L.W
  const H = Math.max(...[...N.s, ...N.m, ...N.c].map((n) => n.y + n.h), noteY + notes.length * 70) + 32
  const fit = wrapW ? Math.max(0.3, Math.min(1, +(wrapW / W).toFixed(2))) : 1
  const z = zS == null ? (isMobile || (wrapW && wrapW < W) ? fit : 1) : zS
  const mid = (n) => n.y + n.h / 2
  const curve = (x1, y1, x2, y2) => { const k = Math.max(40, (x2 - x1) * 0.45); return `M${x1},${y1} C${x1 + k},${y1} ${x2 - k},${y2} ${x2},${y2}` }
  const wires = []
  if (sIdx >= 0) N.m.forEach((n, i) => wires.push([curve(CX[0] + NW, mid(N.s[sIdx]), CX[1], mid(n)), i === mIdx, `s${i}`]))
  if (mIdx >= 0) N.c.forEach((n) => wires.push([curve(CX[1] + NW, mid(N.m[mIdx]), CX[2], mid(n)), n.c.harness === harness, `c${n.c.harness}`]))
  const deltaWires = bIdx < 0 ? [] : N.c.map((n, i) => {
    if (i === bIdx) return null
    const x = C[n.c.harness]
    if (!x) return null
    const k = 26 + Math.abs(i - bIdx) * 14
    const x0 = CX[2] + NW, y1 = mid(N.c[bIdx]), y2 = mid(n)
    return { d: `M${x0},${y1} C${x0 + k},${y1} ${x0 + k},${y2} ${x0},${y2}`, sep: sepOf(x), on: n.c.harness === harness || harness === base, lx: x0 + k * 0.78 + 4, ly: y2 + (y2 > y1 ? -6 : 12), t: pp(x.mean_diff), h: n.c.harness }
  }).filter(Boolean)
  const nsep = Object.values(C).filter(sepOf).length
  const comps = harness === base ? Object.entries(C).sort((a, b) => b[1].mean_diff - a[1].mean_diff) : C[harness] ? [[harness, C[harness]]] : []
  const zoom = (dz) => setZ(Math.max(0.3, Math.min(1.6, +(z + dz).toFixed(2))))
  const pickDir = (name) => { setDir(name); setM(null); setH(null) }

  return (
    <div className="rg-ws-cvdoc" data-el="canvas">
      <div className="rg-ws-cvtools" role="toolbar" aria-label="Canvas tools">
        <button type="button" className="rg-btn" aria-expanded={adding} onClick={() => setAdding(!adding)}>+ Note</button>
        <span className="rg-ws-sep" />
        <button type="button" className="rg-btn" aria-label="Zoom out" onClick={() => zoom(-0.1)}>−</button>
        <span className="rg-mono rg-small rg-ws-zv" aria-live="polite">{Math.round(z * 100)}%</span>
        <button type="button" className="rg-btn" aria-label="Zoom in" onClick={() => zoom(0.1)}>+</button>
        <button type="button" className="rg-btn" onClick={() => setZ(fit)}>fit</button>
        <button type="button" className="rg-btn" onClick={() => setZ(1)} aria-label="Actual size">1:1</button>
        <span className="rg-ws-sep" />
        <button type="button" className="rg-btn" onClick={() => setPalette(true)} aria-label="Search"><Icon name="search" size={13} /> <Kbd>{MOD} K</Kbd></button>
        <span className="rg-ws-sel rg-show-sm">
          <select className="rg-selc" aria-label="Study" value={dir} onChange={(e) => pickDir(e.target.value)}>{datasets.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}</select>
          <select className="rg-selc" aria-label="Model" value={model || ''} onChange={(e) => { setM(e.target.value); setH(null) }}>{models.map((x) => <option key={x} value={x}>{short(x)}</option>)}</select>
          <select className="rg-selc" aria-label="Cell" value={harness || ''} onChange={(e) => setH(e.target.value)}>{mcells.map((x) => <option key={x.harness} value={x.harness}>{x.harness} · {pct(x['pass@1'])}</option>)}</select>
        </span>
        <span className="rg-mono rg-small rg-mute rg-ws-run"><span className={cx('rg-ws-dot', jobs.data.running > 0 && 'on')} /> {int(jobs.data.running)} running</span>
      </div>
      {adding && (
        <form className="rg-pnl rg-ws-add" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) setNotes([...notes, draft.trim()]); setDraft(''); setAdding(false) }}>
          <div className="rg-pnl-h"><span className="rg-lbl">Add a note node</span></div>
          <div className="rg-pnl-b rg-col">
            <label className="rg-fld">note text<input className="rg-inp" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="e.g. short_context bites this model" /></label>
            <div className="rg-row"><button type="submit" className="rg-btn pri">Add note</button><button type="button" className="rg-btn ghost" onClick={() => setAdding(false)}>Cancel</button></div>
            <p className="rg-tiny rg-mute">Study, model and cell nodes come from the data on disk; notes live in this tab only.</p>
          </div>
        </form>
      )}
      <div className="rg-grid rg-ws-cvgrid">
        <div className="rg-ws-cvwrap" ref={wrap}>
          <div style={{ width: W * z, height: H * z }}>
            <div className="rg-ws-cv" style={{ width: W, height: H, transform: `scale(${z})` }}>
              <svg className="rg-ws-wires" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
                {wires.map(([d, on, k]) => <path key={k} d={d} className={cx('w', on && 'on')} />)}
                {deltaWires.map((w) => <g key={'d' + w.h}><path d={w.d} className={cx('dw', w.sep && 'sep', w.on && 'on')} /><text x={w.lx} y={w.ly} className={cx('dt', w.sep && 'sep')}>{w.t}</text></g>)}
              </svg>
              <span className="rg-lbl rg-ws-col" style={{ left: CX[0], top: 14 }}>Studies</span>
              <span className="rg-lbl rg-ws-col" style={{ left: CX[1], top: 14 }}>Models · {dir}</span>
              <span className="rg-lbl rg-ws-col" style={{ left: CX[2], top: 14 }}>Cells · {model ? short(model) : '—'} × harness</span>
              {N.s.map(({ y, h, d }) => (
                <button key={d.name} type="button" className={cx('rg-ws-node', d.name === dir && 'on')} style={{ width: NW, left: CX[0], top: y, minHeight: h }} onClick={() => pickDir(d.name)} onDoubleClick={() => openTab(makeSpec('ds', d.name))} aria-pressed={d.name === dir} title="Click to wire this study · double-click to open it">
                  <span className="port r" /><span className="nh">study{d.kind === 'mock' && <Tag tone="dash">mock</Tag>}</span>
                  <span className="nb"><b>{d.name}</b><span className="rg-tiny rg-mute">{int(d.runs)} runs · {d.models.length}m · {d.harnesses.length}h · {d.tasks.length}t</span></span>
                </button>))}
              {N.m.map(({ y, h, m }) => {
                const mc = cells.filter((c) => c.model === m)
                return (
                  <button key={m} type="button" className={cx('rg-ws-node', m === model && 'on')} style={{ width: NW, left: CX[1], top: y, minHeight: h }} onClick={() => { setM(m); setH(null) }} aria-pressed={m === model}>
                    <span className="port l" /><span className="port r" /><span className="nh">model</span>
                    <span className="nb"><b>{short(m)}</b><span className="rg-tiny rg-mute">{m.includes('/') ? m.split('/')[0] + ' · ' : ''}{plural(mc.length, 'cell')} · {int(mc.reduce((a, c) => a + (c.runs || 0), 0))} runs</span></span>
                  </button>)
              })}
              {notes.map((t, i) => (
                <div key={'n' + i} className="rg-ws-node note" style={{ width: NW, left: CX[1], top: noteY + i * 70 }}>
                  <span className="nh">note · this tab<button type="button" className="rg-ws-x" aria-label="Remove note" onClick={() => setNotes(notes.filter((_, k) => k !== i))}>×</button></span><span className="nb rg-small">{t}</span>
                </div>))}
              {N.c.map(({ y, h, c }) => {
                const x = C[c.harness]
                return (
                  <button key={c.harness} type="button" className={cx('rg-ws-node', c.harness === harness && 'on')} style={{ width: NW, left: CX[2], top: y, minHeight: h }} onClick={() => setH(c.harness)} aria-pressed={c.harness === harness}
                    aria-label={`${short(c.model)} under ${c.harness}: pass@1 ${pct(c['pass@1'])}, n=${c.runs}`}>
                    <span className="port l" /><span className="port r" /><span className="nh">cell · {c.harness}</span>
                    <span className="nb"><span className="rg-row"><span className="rg-mono rg-ws-p1">{pct(c['pass@1'])}</span><span className="rg-tiny rg-mute">{Array.isArray(c.ci95) ? `[${Math.round(c.ci95[0] * 100)}, ${Math.round(c.ci95[1] * 100)}]` : ''} n={int(c.runs)}</span></span>
                      <CiBar lo={c.ci95 && c.ci95[0]} hi={c.ci95 && c.ci95[1]} pt={c['pass@1']} />
                      {c.harness === base ? <span className="rg-tiny rg-mute">baseline of the paired Δ</span> : x ? <span className={cx('rg-tiny', sepOf(x) ? 'rg-acc' : 'rg-mute')} title={`paired Δ vs ${base}`}>Δ {pp(x.mean_diff)} · {sepOf(x) ? '◆ separates' : '○ covers 0'}</span> : null}
                    </span>
                  </button>)
              })}
            </div>
          </div>
        </div>
        <CanvasDetail dir={dir} model={model} harness={harness} cell={cell} base={base} comps={comps} nsep={nsep} cmp={cmp} />
      </div>
    </div>
  )
}

function CanvasDetail({ dir, model, harness, cell, base, comps, nsep, cmp }) {
  if (!cell) return <div className="rg-ws-detail" data-el="canvas-detail"><Empty title="No cell to show." actions={[{ label: `Open ${dir}`, spec: makeSpec('ds', dir) }, { label: 'Pick another study', onClick: () => { const e = document.querySelector('.rg-ws-node'); if (e) e.focus() } }]}>{dir} has no model × harness cell in /metrics, so there is nothing to wire.</Empty></div>
  const w = Array.isArray(cell.ci95) ? cell.ci95[1] - cell.ci95[0] : null
  const label = `${short(model)} × ${harness}`
  return (
    <section className="rg-pnl rg-ws-detail" data-el="canvas-detail" aria-label="Selected cell">
      <div className="rg-pnl-h"><span className="rg-lbl rg-acc">selected · cell</span><span className="meta"><PinButton compact item={{ id: `cell:${dir}:${model}:${harness}`, kind: 'cell', label: `${dir} · ${label}`, value: `pass@1 ${pct(cell['pass@1'])} (95% CI ${pct(cell.ci95 && cell.ci95[0], 0)}–${pct(cell.ci95 && cell.ci95[1], 0)}, n=${cell.runs}, hidden suite)`, spec: makeSpec('ds', dir, short(model), harness), dir, model, harness }} /></span></div>
      <div className="rg-pnl-b rg-col">
        <div><b className="rg-mono">{label}</b><div className="rg-tiny rg-mute rg-mono">{dir} · {int(cell.runs)} runs · {cell.repeats ?? '—'} repeats · hidden suite</div></div>
        <p className="rg-small rg-dim">{w != null ? `The interval is ${fx(w * 100, 1)} points wide at n=${cell.runs}. ` : ''}{cmp.error ? 'The paired comparison did not load.' : !cmp.data ? 'Reading the paired comparison…' : harness === base ? (comps.length ? (nsep ? `${nsep} of ${comps.length} harness${comps.length === 1 ? '' : 'es'} separate${nsep === 1 ? 's' : ''} from ${base} for this model; the rest cover zero.` : `No harness separates from ${base} for this model: every paired interval covers zero.`) : 'There is no other harness to pair with.') : `Paired against ${base} on the same tasks.`}</p>
        <div className="rg-grid rg-ws-mini">
          {[['pass@1', pct(cell['pass@1'])], ['pass^3', pct(cell['pass^3'])], ['flip rate', pct(cell.flip_rate, 0)], ['cost / run', usd(cell.mean_cost, 4)]].map(([l, v]) => <div key={l}><span className="rg-lbl">{l}</span><span className="rg-mono">{v}</span></div>)}
        </div>
        {comps.length > 0 && <>
          <div className="rg-lbl">wired to · paired Δ vs {base}</div>
          <ul className="rg-ws-dl">{comps.map(([h, x]) => { const s = sepOf(x); return <li key={h}><span className="rg-mono">{h}</span><span className={cx('rg-mono', s && 'rg-acc')}>{pp(x.mean_diff)}</span><span className={cx('rg-tiny', s ? 'rg-sky' : 'rg-mute')}>{s ? '◆ separates' : '○ covers 0'}</span></li> })}</ul>
          <p className="rg-tiny rg-mute">95% intervals over {comps[0][1].n_tasks ?? '—'} tasks; descriptive, not causal.</p>
        </>}
        <div className="rg-lbl">actions</div>
        <div className="rg-row rg-wrap">
          <Go spec={makeSpec('tasks', dir, short(model), harness)} className="rg-btn">Open {int(cell.runs)} runs</Go>
          <Go spec={makeSpec('an', dir, 'setup')} className="rg-btn">Design a repeat</Go>
          <Go spec={makeSpec('an', dir, 'delta')} className="rg-btn">Wire a comparison</Go>
        </div>
      </div>
    </section>
  )
}

/* ================================================================ SETTINGS */
const storageWorks = () => { try { localStorage.setItem('rig.probe', '1'); const ok = localStorage.getItem('rig.probe') === '1'; localStorage.removeItem('rig.probe'); return ok } catch { return false } }
const KEY_PH = 'e.g. sk-or-v1-…'

function Settings() {
  const rig = useRig()
  const st = useSettings()
  const tasks = useApi(IS_STATIC ? null : paths.tasks())
  const [density, setDensity] = useState(() => readPref(DENSITY_KEY, ['compact', 'comfortable'], 'compact'))
  const [motion, setMotion] = useState(() => readPref(MOTION_KEY, ['on', 'off'], 'on'))
  const [prefFail, setPrefFail] = useState(false)
  const [labFail, setLabFail] = useState(false)
  const [probe] = useState(storageWorks)
  useEffect(() => { if (readPref(DENSITY_KEY, [density], null) && readPref(MOTION_KEY, [motion], null)) applyRigPrefs() }, [density, motion])
  const setP = (k, v, set) => {
    set(v)
    const ok = writePref(k, v)
    setPrefFail(!ok)
    // stamp the choice directly too, so blocked storage still changes this visit
    document.querySelectorAll('.rig').forEach((el) => el.setAttribute(k === DENSITY_KEY ? 'data-rig-density' : 'data-rig-motion', v))
  }
  const toggleLab = (on) => {
    rig.setLab(on)
    let ok = false
    try { ok = localStorage.getItem('hs.studentLab') === (on ? 'on' : 'off') } catch { ok = false }
    setLabFail(!ok)
  }
  const s = st.data || null
  return (
    <div className="rg-dpad rg-ws rg-ws-doc rg-ws-settings">
      <Head eyebrow="Settings" title="Preferences, keys, paths">A key is only needed to ask Buddy a question in words or to run live models. Every finding in the workbench is arithmetic over the ledger and works without one.</Head>
      <div className="rg-ws-setrow">
      <Sec title="Appearance" meta="this browser" el="appearance">
        <div className="rg-col rg-ws-prefs">
          <div className="rg-row rg-wrap"><span className="rg-ws-pl">Theme</span><Seg label="Theme" value={rig.themePref} onChange={rig.setTheme} options={[['dark', 'dark · primary'], ['light', 'paper'], ['system', 'system']]} /></div>
          <div className="rg-row rg-wrap"><span className="rg-ws-pl">Density</span><Seg label="Density" value={density} onChange={(v) => setP(DENSITY_KEY, v, setDensity)} options={[['compact', 'compact'], ['comfortable', 'comfortable']]} /></div>
          <div className="rg-row rg-wrap"><span className="rg-ws-pl">Motion</span><Seg label="Motion" value={motion} onChange={(v) => setP(MOTION_KEY, v, setMotion)} options={[['on', 'on'], ['off', 'reduced']]} /></div>
        </div>
        <p className="rg-small rg-mute">Remembered in this browser. Your system&apos;s reduced-motion setting is always honoured.</p>
        {(prefFail || !probe) && <p className="rg-small rg-red">This browser blocks storage — the preference holds for this visit only.</p>}
      </Sec>
      <Sec title="Learning preferences" meta={rig.lab ? <Tag tone="acc">on</Tag> : <Tag tone="dash">off</Tag>} el="student-lab-toggle">
        <Switch checked={rig.lab} onChange={toggleLab} label={<b>Student Lab · Guided Analysis</b>} />
        <div className="rg-ws-pair">
          <Note tone={rig.lab ? 'acc' : undefined}><div><b>When on</b><br />Reading guide, reference materials and an evidence-linked lesson beside each attempt. Prediction and intervention controls are marked as outside the guided path.</div></Note>
          <Note tone={rig.lab ? undefined : 'acc'}><div><b>When off</b><br />The professional instrument: experiment comparisons and the trajectory inspector. Teaching resources stay out of navigation.</div></Note>
        </div>
        <p className="rg-small rg-mute">Off by default. Remembered in this browser (<span className="rg-mono">hs.studentLab</span>); it does not change data access.</p>
        {labFail && <Note tone="red"><div><b>Could not save the preference.</b> This browser blocks storage — Student Lab is {rig.lab ? 'on' : 'off'} for this visit only.</div></Note>}
        {rig.lab && <div className="rg-row rg-wrap" data-el="guidance-links"><Go spec="guide" className="rg-btn"><Icon name="guide" size={14} /> Reading guide</Go><Go spec="package" className="rg-btn"><Icon name="pkg" size={14} /> Reference materials</Go></div>}
      </Sec>
      </div>
      {/* The two key panels share their rows: key field beside key field, buttons beside buttons. */}
      <div className="rg-ws-setrow rg-ws-pairrow">
        <BuddyPanel />
        <LabKeyPanel st={st} />
      </div>
      <div className="rg-ws-setrow">
      <NarratorPanel />
      <Sec title="Paths in use" meta={IS_STATIC ? 'export' : 'GET /api/settings'} el="paths-in-use">
        {IS_STATIC ? <StaticNote what="The server's paths" /> : st.error ? <Failed what="Could not read the server's settings." error={st.error} onRetry={st.reload} /> : !s ? <div className="rg-skel rg-ws-sk" /> : (
          <dl className="rg-kv rg-ws-kv">
            <dt>base URL</dt><dd>{s.base_url}</dd>
            <dt>lab root</dt><dd>{s.lab_root}</dd>
            <dt>runs root</dt><dd>{s.results_root} <span className="rg-mute">· {plural((s.results || []).length, 'directory', 'directories')}</span></dd>
            <dt>harnesses</dt><dd>{s.harness_dir} <span className="rg-mute">· {(s.harnesses || []).length}</span></dd>
            <dt>tasks</dt><dd>{s.task_dir} <span className="rg-mute">· {Array.isArray(tasks.data) ? tasks.data.length : '—'}</span></dd>
          </dl>)}
      </Sec>
      </div>
    </div>
  )
}
function SettingsActions() {
  const { state, openTab } = useRig()
  const p = state && state.panes ? state.panes[state.focus] || state.panes[0] : null
  const back = (p && p.tabs.find((t) => t !== 'settings')) || 'home'
  return <button type="button" className="rg-btn pri" data-el="return-to-work" onClick={() => openTab(back)}>Return to work</button>
}

/** Buddy's browser-held key + model: matekey.js / buddyClient.js exactly as workspace/BuddySettings.jsx. */
function BuddyPanel() {
  const { setDock } = useRig()
  const stores = useMemo(() => browserStores(), [])
  const [state, setState] = useState(() => buddySettings(stores))
  const [key, setKey] = useState('')
  const [remember, setRemember] = useState(() => isKeyRemembered(stores))
  const [note, setNote] = useState(null)
  const refresh = () => setState(buddySettings(stores))
  const save = async () => {
    const k = key.trim()
    if (k && KEY_HAS_INNER_WHITESPACE(k)) { setNote({ ok: false, t: 'Paste the key on one line, with no spaces inside it.' }); return }
    const want = k || buddySettings(stores).key
    if (!want) { setNote({ ok: false, t: 'Paste a key first.' }); return }
    writeKey(stores, want, remember)
    const next = buddySettings(stores)
    if (next.key !== want || isKeyRemembered(stores) !== remember) { setNote({ ok: false, t: 'Browser storage is blocked. Allow site storage to save Buddy settings.' }); return }
    setKey(''); setState(next); setNote({ ok: true, t: remember ? 'Saved on this browser. No request made.' : 'Saved for this tab. No request made.' })
  }
  return (
    <SlotSec title="Buddy · OpenRouter" meta={state.keyPresent ? <Tag tone="sky">key held</Tag> : <Tag tone="dash">not connected</Tag>} el="buddy-settings"
      lead="Asks about the evidence you pin to the case file. The key lives only in this browser, shared with the narrator below, and is never sent to the harnesslab server."
      status={
        <dl className="rg-kv rg-ws-kv rg-ws-buddy">
          <dt>connection</dt><dd>{state.keyPresent ? <>key {state.keyHint || 'set'} · {isKeyRemembered(stores) ? 'remembered on this browser' : 'this tab only'}</> : <span className="rg-red">no key in this browser</span>}</dd>
          <dt>model</dt><dd><BuddyModelPicker variant="field" value={state.model} onChange={(v) => { writeModel(stores, v); refresh(); setNote(null) }} /></dd>
        </dl>}
      field={
        <div className="rg-col rg-ws-field">
          <input className="rg-inp" type="password" autoComplete="off" aria-label="Buddy OpenRouter key" value={key} placeholder={state.keyPresent ? 'Paste a new key to replace it' : KEY_PH} onChange={(e) => setKey(e.target.value)} />
          <label className="rg-row rg-small rg-dim"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} aria-label="Remember key on this browser" /> remember on this browser</label>
        </div>}
      actions={
        <div className="rg-row rg-wrap rg-ws-acts">
          <ConfirmAction label="Save Buddy key" confirmLabel="Save in this browser" disabled={!key.trim() && !state.keyPresent} why="Paste a key first" detail={remember ? 'Stored in localStorage on this browser.' : 'Stored for this tab only.'} onConfirm={save} />
          <Btn className="ghost" disabled={!state.keyPresent} why="Nothing to forget: no key saved" onClick={() => { forgetKey(stores); refresh(); setRemember(isKeyRemembered(stores)); setNote({ ok: true, t: 'Buddy key forgotten.' }) }}>Forget key</Btn>
        </div>}
      rest={<>
        {note && <p className={cx('rg-small', note.ok ? 'rg-dim' : 'rg-red')} role="status">{note.t}</p>}
        <button type="button" className="rg-tlink rg-ws-openbuddy" onClick={() => setDock('buddy')}>Open Buddy →</button>
        <About summary="How the key is kept">
          <p>Not an encrypted vault: scripts and extensions with access to this page can read it. Use a spend-limited key. Each question asks your consent before recorded excerpts go to OpenRouter.</p>
        </About>
      </>} />
  )
}

/** The lab's server-held key: POST /api/settings/key {key} (LabKey.jsx). Empty string clears it. */
function LabKeyPanel({ st }) {
  const [draft, setDraft] = useState('')
  const [note, setNote] = useState(null)
  const s = st.data || {}
  const send = async (key, said) => {
    try { await api('/settings/key', { method: 'POST', body: { key } }); setDraft(''); setNote({ ok: true, t: said }); invalidate('/settings'); invalidate('/overview') } catch (e) { setNote({ ok: false, t: errText(e) }) }
  }
  return (
    <SlotSec title="The lab's key" meta={st.data ? (s.key_present ? <Tag tone="sky">set</Tag> : <Tag tone="dash">not set</Tag>) : ''} el="lab-key"
      lead="Held by the harnesslab server for running live models and the judge. Kept in memory while the process runs and never sent back to this page. Recorded results work without one."
      status={
        <dl className="rg-kv rg-ws-kv">
          <dt>connection</dt><dd>{!st.data ? '…' : s.key_present
            ? <>The server holds a key: <span className="rg-mono">{s.key_hint || 'set'}</span> (its own mask).</>
            : 'The server holds no key.'}</dd>
          <dt>used for</dt><dd>live model runs and the LLM judge</dd>
        </dl>}
      field={IS_STATIC ? <StaticNote what="Setting the server key" />
        : <div className="rg-col rg-ws-field"><input className="rg-inp" type="password" autoComplete="off" aria-label="harnesslab key" value={draft} placeholder={KEY_PH} onChange={(e) => setDraft(e.target.value)} /></div>}
      actions={IS_STATIC ? null :
        <div className="rg-row rg-wrap rg-ws-acts">
          <ConfirmAction label="Save server key" confirmLabel="Send to the server" disabled={!draft.trim()} why="Paste a key first" detail="POST /api/settings/key" onConfirm={() => send(draft.trim(), 'Saved on the server.')} el="lab-key-save" />
          <ConfirmAction label="Clear" confirmLabel="Clear the server key" danger disabled={!s.key_present} why="Nothing to clear: the server holds no key" onConfirm={() => send('', 'Cleared on the server.')} />
        </div>}
      rest={note && <p className={cx('rg-small', note.ok ? 'rg-dim' : 'rg-red')} role="status">{note.t}</p>} />
  )
}

/** The narrator's browser-held key, slug and captured-text consent (MateSettings.jsx). */
function NarratorPanel() {
  const stores = useMemo(() => browserStores(), [])
  const [state, setState] = useState(() => readMate(stores))
  const [draft, setDraft] = useState('')
  const [slug, setSlug] = useState(() => readMate(stores).model)
  const [check, setCheck] = useState(null)
  const refresh = () => setState(readMate(stores))
  const saveAndCheck = async () => {
    const key = writeKey(stores, draft.trim() || state.key)
    setDraft(''); refresh()
    if (!key) { setCheck(null); return }
    setCheck({ busy: true })
    const r = await validateKey({ key, model: readMate(stores).model })
    setCheck({ busy: false, ok: r.ok, t: r.ok ? 'Key and model accepted.' : r.message })
  }
  return (
    <Sec title="The narrator" meta="browser-held key" el="narrator-key"
      lead="Calls OpenRouter from this page; its key never reaches the harnesslab server. Tab-only by default.">
      <div className="rg-col rg-ws-form">
        <input className="rg-inp" type="password" autoComplete="off" aria-label="Narrator OpenRouter key" value={draft} placeholder={state.keyPresent ? `Held: ${state.keyHint || 'a key'} — paste to replace` : KEY_PH} onChange={(e) => setDraft(e.target.value)} />
        <label className="rg-fld">Model slug<input className="rg-inp" value={slug} spellCheck={false} placeholder={`e.g. ${DEFAULT_MODEL}`} onChange={(e) => setSlug(e.target.value)} onBlur={() => { setSlug(writeModel(stores, slug)); refresh() }} /></label>
        <div className="rg-row rg-wrap rg-ws-acts">
          <ConfirmAction label="Save & check" confirmLabel="Check with OpenRouter" disabled={!draft.trim() && !state.keyPresent} why="Paste a key first" detail="Sends one 1-token request with this key." onConfirm={saveAndCheck} />
          <Btn className="ghost" disabled={!state.keyPresent} why="Nothing to forget: no key saved" onClick={() => { forgetKey(stores); refresh(); setCheck(null) }}>Forget</Btn>
        </div>
        {check && <p className={cx('rg-small', check.busy ? 'rg-mute' : check.ok ? 'rg-dim' : 'rg-red')} role="status">{check.busy ? 'Checking…' : check.t}</p>}
        <Switch checked={state.captured} onChange={(on) => { setCaptured(stores, on); refresh() }} label={<span className="rg-small">Let a beat quote text from a captured session (off by default, this tab only — sends real transcript text to OpenRouter while on)</span>} />
        <About summary="How the key is kept">
          <p>Anything running in this page can read it — use a key with a spend limit, and forget it when you are done.</p>
        </About>
      </div>
    </Sec>
  )
}

/* ================================================================ GUIDE (Student Lab) */
function pickDir(datasets) {
  return (datasets.find((d) => d.name === 'llma4se_live') || [...datasets].filter((d) => d.kind === 'recorded').sort((a, b) => b.runs - a.runs)[0] || datasets[0] || {}).name || null
}
function Guide() {
  const { datasets, conditionFor, lab, isMobile, oracle } = useRig()
  const dir = pickDir(datasets)
  const c = dir ? conditionFor(dir) : null
  const R = useRuns(dir)
  const ex = useMemo(() => {
    if (!R.data || !c || !c.model) return null
    const list = conditionRuns(R.data, c.model, c.harness)
    const tasks = [...new Set(list.map((r) => r.task))].sort()
    const mixed = tasks.filter((t) => { const o = tally(list.filter((r) => r.task === t), oracle); return o.p && o.f })
    const failing = list.filter((r) => mixed.includes(r.task) && verdict(r, oracle) === false).sort((a, b) => a.task.localeCompare(b.task) || a.rep - b.rep)[0] || list.find((r) => verdict(r, oracle) === false) || null
    return { n: list.length, tasks: tasks.length, mixed, failing }
  }, [R.data, c && c.model, c && c.harness, oracle]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!datasets.length) return <Loading label="Reading /api/overview…" />
  const m = c && c.model ? short(c.model) : null
  const steps = [
    ['Pick a dataset', 'home', 'Recorded datasets versus mock controls. Mocks have designed failure rates — an all-pass or all-fail mock means the grader broke, not the model.'],
    ['Read the answered questions', makeSpec('q', dir), `${dir} opens with its questions already answered from the data, each with one sentence and one number you can follow down.`],
    ['Read one condition', makeSpec('ds', dir, m, c && c.harness), `${m || 'A model'} under ${c && c.harness}: the success rate always shows its known denominator; unknown is never failure.`],
    ['Find the mixed task', makeSpec('tasks', dir, m, c && c.harness), ex ? (ex.mixed.length ? `${plural(ex.mixed.length, 'task')} of ${ex.tasks} ${ex.mixed.length === 1 ? 'has' : 'have'} both passing and failing repeats under the ${oracle} suite (${ex.mixed.slice(0, 3).join(', ')}${ex.mixed.length > 3 ? '…' : ''}). Same model, same harness, different outcomes: that spread is variance.` : `No task is mixed for this condition under the ${oracle} suite; try another model.`) : 'Same model, same harness, different outcomes: that spread is variance.'],
    ['Open a failing attempt', ex && ex.failing ? makeSpec('run', ex.failing.id) : makeSpec('tasks', dir, m, c && c.harness), ex && ex.failing ? <>Run <span className="rg-mono">{ex.failing.id.slice(-6)}</span> on {ex.failing.task} failed <FailureChip run={ex.failing} />. Follow each claim to the span that records it.</> : 'Follow each claim to the span that records it.'],
    ['Put an interval on it', makeSpec('an', dir, 'outcomes'), 'pass@1 with a 95% interval; pass^k is what a user who retries k times experiences.'],
    ['Attribute the difference', makeSpec('an', dir, 'delta'), 'Paired differences against baseline on the same tasks. Count how many intervals exclude zero before believing any of them.'],
    ['Question the oracle', makeSpec('an', dir, 'judge'), 'Grade the same runs with the visible, hidden and strengthened suites: part of any score can be the suite.'],
    ['Close with a report card', makeSpec('an', dir, 'report'), 'Claims, caveats, and what the card does not cover.'],
  ]
  return (
    <div className="rg-dpad rg-ws rg-ws-doc rg-ws-read">
      <Head eyebrow="Student Lab · guided analysis" title="Start here">
        {plural(steps.length, 'step')} from the big picture to the recorded action that changed an outcome, on <span className="rg-mono">{dir}</span>. Each opens as a document tab{isMobile ? '' : <> — keep the guide open beside it with <Kbd>{MOD} click</Kbd></>}.
      </Head>
      {!lab && <Note el="outside-guided-state"><div>Student Lab is <b>off</b>. The guide is readable, but lessons beside each attempt appear only when it is on (the button above turns it on).</div></Note>}
      <ol className="rg-pnl rg-ws-steps" data-el="start-here">
        {steps.map(([t, to, d], i) => (
          <li key={t}><Go spec={to} side={!isMobile} className="rg-ws-step" title={isMobile ? undefined : 'Opens in the other pane'}>
            <span className="rg-fig sm acc">{i + 1}</span><span className="rg-grow"><b>{t}</b><span className="rg-small rg-dim">{d}</span></span><span className="rg-mute" aria-hidden="true">⇥</span>
          </Go></li>))}
      </ol>
      <Sec title="What the included results are">
        <p className="rg-ws-lead">HarnessLab treats the agent harness — tool surface, permission policy, context window, stopping rule — as an experimental variable, and measures how much it moves a fixed model on fixed tasks. The recorded datasets replay from disk, so reanalysing them needs no API key.</p>
        <NextSteps actions={[{ label: 'Reference materials', spec: 'package' }]} />
      </Sec>
      <Sec title="Reproduce from the terminal" lead="HANDOUT.md walks through exercises 1–8.">
        <pre className="rg-pre">{'python3 exercises/ex1_variance.py\npython3 exercises/ex2_harness.py --results data/runs/live\npython3 exercises/ex7_real_trajectories.py --offline\npython3 exercises/ex8_experiment.py'}</pre>
      </Sec>
      <DatasetList />
    </div>
  )
}
function GuideActions() {
  const { lab, setLab } = useRig()
  return lab ? null : <button type="button" className="rg-btn pri" onClick={() => setLab(true)}>Turn Student Lab on</button>
}
function DatasetList() {
  const ds = useDatasets()
  const present = new Map((ds.data || []).map((d) => [d.name, d]))
  return (
    <Sec title="Included datasets" meta={String(schoolPackage.datasets.length)} el="included-datasets">
      <div className="rg-pnl"><ul className="rg-ws-dsl">{schoolPackage.datasets.map((name) => {
        const d = present.get(name)
        return (
          <li key={name}>
            <div className="rg-row rg-wrap">{d ? <Go spec={makeSpec('ds', name)} className="rg-tlink"><b>{name}</b></Go> : <b className="rg-mono rg-mute">{name}</b>}<span className="rg-grow" /><span className="rg-mono rg-small rg-mute">{d ? `${int(d.runs)} runs` : 'not present on disk'}</span></div>
            {NOTES[name] && <div className="rg-small rg-dim">{NOTES[name]}</div>}
          </li>)
      })}</ul></div>
    </Sec>
  )
}

/* ================================================================ PACKAGE (reference materials) */
function Package() {
  const ov = useOverview()
  const { datasets, conditionFor } = useRig()
  const dir = pickDir(datasets)
  const c = dir ? conditionFor(dir) : null
  const download = () => downloadText('school-package-scope.json', JSON.stringify(schoolPackage, null, 2) + '\n', 'application/json')
  const H = (ov.data && ov.data.harnesses) || []
  const T = (ov.data && ov.data.tasks) || []
  return (
    <div className="rg-dpad rg-ws rg-ws-doc rg-ws-read" data-el="package-contents">
      <Head eyebrow="School replication package" title="Reference materials" actions={<button type="button" className="rg-btn" onClick={download}><Icon name="down" size={14} /> Download scope</button>}>
        What this replication package contains, and how to reproduce it from this directory. Everything recorded replays from disk: no API key is needed to inspect or reanalyse it.
      </Head>
      <About summary="Where the runs come from">
        <p>The recorded datasets were collected for this lab through OpenRouter, plus third-party SWE-agent trajectories. Mock datasets are only for exercises that must behave identically on every machine. Captured sessions and credentials are left out.</p>
      </About>
      <Sec title="What this package contains">
        <div className="rg-pnl"><ul className="rg-ws-dsl">{schoolPackage.materials.map((it) => (
          <li key={it.title}><b>{it.title}</b><div className="rg-small rg-dim">{it.description}</div><div className="rg-row rg-wrap">{it.paths.map((p) => <code key={p} className="rg-mono rg-small">{p}</code>)}</div></li>))}</ul></div>
      </Sec>
      <DatasetList />
      <Sec title={`Task issues · ${T.length}`} lead={`Each opens the task investigator for ${c && c.model ? `${short(c.model)} × ${c.harness}` : dir}. Probe tasks carry a designed trap (amber).`}>
        {ov.error ? <Failed what="Could not read the task list." error={ov.error} onRetry={ov.reload} /> : <div className="rg-row rg-wrap">{T.map((t) => (
          <Go key={t.id} spec={c && c.model ? makeSpec('task', dir, short(c.model), c.harness, t.id) : makeSpec('ds', dir)} className="rg-ws-chip" title={t.title}>{t.id}{t.probe && t.probe !== 'none' && <span className="rg-amb"> · {t.probe}</span>}</Go>))}</div>}
        <NextSteps actions={[{ label: `Report card · ${dir}`, spec: makeSpec('an', dir, 'report') }]} />
      </Sec>
      <Sec title={`Harness definitions · ${H.length}`}>
        {ov.error ? <Failed what="Could not read the harness list." error={ov.error} onRetry={ov.reload} /> : <div className="rg-pnl"><ul className="rg-ws-dsl">{H.map((h) => (
          <li key={h.id}><span className="rg-mono rg-small">{h.file || `${h.id}.json`}</span><div className="rg-small rg-dim">{h.notes || '—'}</div></li>))}</ul></div>}
      </Sec>
      <Sec title="Reproduce from this directory">
        <h3 className="rg-ws-h3">1 · Install and run</h3>
        <pre className="rg-pre">{'python3 -m pip install -e .\npython3 -m harnesslab --no-browser'}</pre>
        <p className="rg-small rg-mute">Open <span className="rg-mono">http://127.0.0.1:8765</span>. No API key is needed to inspect the included results.</p>
        <h3 className="rg-ws-h3">2 · Reproduce the exercises</h3>
        <pre className="rg-pre">{'python3 exercises/ex1_variance.py\npython3 exercises/ex2_harness.py\npython3 exercises/ex7_real_trajectories.py --offline\npython3 exercises/ex8_experiment.py'}</pre>
        <p className="rg-small rg-mute">Read <span className="rg-mono">HANDOUT.md</span> for exercises 3–6, or open the <Go spec="guide" className="rg-tlink">reading guide</Go>.</p>
        <About summary="For maintainers: rebuild the archive"><pre className="rg-pre">python3 scripts/build_school_package.py</pre><p>Creates <span className="rg-mono">dist/harnesslab-school.zip</span> with an exact file manifest and SHA-256 checksums.</p></About>
      </Sec>
      <Sec title="Excluded from the handoff">
        <ul className="rg-ws-bul">{schoolPackage.excluded.map((x) => <li key={x} className="rg-dim">{x}</li>)}</ul>
      </Sec>
    </div>
  )
}

/* ================================================================ registration */
export const views = {
  sources: { tag: 'src', title: () => 'Sources', render: Sources, crumbs: () => [['sources & machine']],
    Actions: () => <Go spec="capture" className="rg-btn">Session capture <Icon name="right" size={13} /></Go>,
    palette: () => [{ title: 'Sources & results on disk', detail: 'data/runs · import a trace', spec: 'sources', keywords: 'load trajectories import' }] },
  capture: { tag: 'cap', title: () => 'Capture', render: Capture, crumbs: () => [['sources & machine', 'sources'], ['session capture']],
    palette: () => [{ title: 'Session capture', detail: 'watcher · captured sessions', spec: 'capture' }] },
  sentinel: { tag: 'sent', title: () => 'Sentinel', render: Sentinel, crumbs: () => [['sentinel · early warning on a partial trajectory']], outsideGuided: () => true,
    palette: () => [{ title: 'Sentinel', detail: 'early warning on a partial trajectory', spec: 'sentinel' }] },
  canvas: { tag: 'cv', title: () => 'Canvas', render: Canvas, crumbs: () => [['canvas · studies → models → cells']], outsideGuided: () => true,
    palette: () => [{ title: 'Canvas', detail: 'node canvas of studies, models, cells', spec: 'canvas' }] },
  settings: { tag: 'set', title: () => 'Settings', render: Settings, Actions: SettingsActions, crumbs: () => [['settings']],
    palette: () => [{ title: 'Settings', detail: 'appearance, Student Lab, keys, paths', spec: 'settings' }] },
  guide: { tag: 'lab', title: () => 'Reading guide', render: Guide, Actions: GuideActions, crumbs: () => [['student lab'], ['reading guide']],
    palette: () => [{ title: 'Reading guide', detail: 'Student Lab · start here', spec: 'guide' }] },
  package: { tag: 'pkg', title: () => 'Package contents', render: Package, crumbs: () => [['student lab', 'guide'], ['reference materials']],
    palette: () => [{ title: 'Reference materials', detail: 'school replication package', spec: 'package' }] },
}
export const docks = {
  capture: { label: 'Capture watcher', icon: 'cap', order: 30, side: true, render: CaptureDock },
}
