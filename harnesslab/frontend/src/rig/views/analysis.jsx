/* ====================================================================================
   Rig · views/analysis.jsx — `an:<dir>:<view>[:<model>:<harness>]` (eight study steps) and the
   `tasks:<dir>:<model>:<harness>` trajectory explorer. Owner: Phase 2 · analysis.

   Views (ported from the prototype's 20_views_analysis.js onto LIVE endpoints):
     family    model × harness family matrix over /results/<dir>/runs (grades under the
               current suite, unknown shown, never folded into fail)
     outcomes  /results/<dir>/metrics cell: pass@1 + 95% CI, pass^k, pass@k, flip rate,
               strengthened Δ; per-task repeat strips from the run index + failure modes
     delta     /results/<dir>/comparisons Δ-matrix vs baseline + ANOVA shares (/experiment)
     judge     /oracle (hidden vs strengthened, κ, 2×2), /integrity (flags, per-task oracle,
               leakage), /judge (LLM judge; run = POST /judge/run behind a confirm step)
     fit       /experiment two-factor fit, cell grid, pairwise contrasts
     report    /results/<dir>/report?harness=<h>. The card's cell names ONE model but its
               outcome pools every model under the harness; shown as "all N models · h (pooled)"
     setup     compose a cell; Launch = POST /jobs (same body as method/StepRun.jsx) behind
               ConfirmAction, never automatic; disabled in a static export
   Every body sits under the AskingSentence; figures and cells carry PinButtons.

   Round 3 (breathing + dead ends): each view leads with its ANSWER sentence (Head `lead`),
   method explanations sit in an "About this view" disclosure, unrelated panels stack, and
   every empty / unavailable state says what shape the dataset has and offers the view or
   dataset that can answer (shapeLine + useAlt). Datasets with no strengthened grades (e.g.
   imported trajectories) show "—" for every strengthened figure: the backend reports 0.0
   there, which would read as a measured collapse.
   Classes: rg-an-* (analysis.css). Local helpers only; nothing here is shared.
   ==================================================================================== */
import { useMemo, useState } from 'react'
import { makeSpec } from '../route'
import { api, IS_STATIC } from '../../api'
import {
  useDataset, useDatasets, useOverview, useRuns, useMetrics, useComparisons, useExperiment, useOracle, useIntegrity, useReport,
  useTasks, useModels, useJobs, useApi, short, fmt, tally, tokStats, conditionRuns, resolveCondition, metricsCell,
  verdict, invalidate, paths,
} from '../data'
import { useRig, useTabState } from '../context'
import {
  AskingSentence, Go, Panel, OutcomeBar, CiBar, Verdict, FailureChip, PinButton, Tag, Note, Seg, SearchBox,
  Loading, ErrorState, NotFound, Empty, NextSteps, Btn, ConfirmAction, About, CopyCmd, Kbd, MOD, failureModeOf, failureSummary,
} from '../ui'
import './analysis.css'

const { pct, pp, int, fx, usd, plural } = fmt
const cx = (...a) => a.filter(Boolean).join(' ')

/* [view, nav label, palette title, step number] */
export const AN_VIEWS = [
  ['family', 'Family matrix', 'Family matrix', '1'], ['outcomes', 'Outcomes', 'Outcomes · pass@1 with CI', '2'],
  ['delta', 'Comparison', 'Comparison · Δ-matrix vs baseline', '3'], ['traj', 'Trajectories', 'Trajectory explorer', '4'],
  ['judge', 'Judge & integrity', 'Judge & integrity', '5'], ['fit', 'Experiment', 'Experiment · two-factor fit', '6'],
  ['report', 'Report', 'Report card', '7'], ['setup', 'Run setup', 'Run setup · compose a cell', '8'],
]
const AN_LABEL = Object.fromEntries(AN_VIEWS.map(([v, l]) => [v, l]))
const PAL_KEYWORDS = { delta: 'compare comparison delta matrix baseline', judge: 'oracle integrity leakage kappa judge', fit: 'experiment anova two-factor contrasts', outcomes: 'pass@1 pass^k flip ci', family: 'family matrix models harnesses', report: 'report card export', setup: 'launch run setup compose' }
const LIVE_CMD = 'python -m harnesslab'

const probeLabel = (p) => String(p || '').replace(/_/g, ' ')
function ProbeTag({ probe }) {
  if (!probe || probe === 'none') return <span className="rg-small rg-mute">none</span>
  return <Tag tone="amb" nc title={`probe: ${probe}`}>{probeLabel(probe)}</Tag>
}
/** Diverging bar for a signed value in [-max, max]. */
function DBar({ v, max = 1 }) {
  const w = v == null ? 0 : Math.min(1, Math.abs(v) / max) * 50
  return (
    <span className="rg-an-dbar" role="img" aria-label={pp(v, 0)}>
      <i className={v < 0 ? 'neg' : 'pos'} style={{ left: `${v < 0 ? 50 - w : 50}%`, width: `${w}%` }} /><span className="z" />
    </span>
  )
}
/** A rate as a short bar with its value right-aligned (table cells). null = "—", no bar. */
function RateBar({ v, tone }) {
  return (
    <span className="rg-an-rb">
      <span className="rg-an-rbt">{v != null && <i className={tone === 'dim' ? 'dim' : ''} style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%` }} />}</span>
      <span className="rg-num">{pct(v, 0)}</span>
    </span>
  )
}
/** Browser download of a text blob (the real app can download). Returns false when unavailable. */
function download(name, text, type) {
  try {
    const url = URL.createObjectURL(new Blob([text], { type }))
    const a = document.createElement('a')
    a.href = url; a.download = name
    document.body.appendChild(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
    return true
  } catch { return false }
}
const ciText = (ci, d = 1) => (ci && ci.length === 2 ? `[${pct(ci[0], d)}, ${pct(ci[1], d)}]` : '—')
const sepOf = (ci) => !!ci && (ci[0] > 0 || ci[1] < 0)
const symMax = (vals, floor = 0.2) => Math.max(floor, Math.ceil(Math.max(0, ...vals.filter((x) => x != null).map(Math.abs)) * 10) / 10)

/* ------------------------------------------------------------------ dataset shape → ways forward */
/** What a dataset has, in words: "real_swe_agent_500 has 2 models under a single harness (swe-agent)". */
function shapeOf(row) {
  const nm = row.models.length, nh = row.harnesses.length
  return {
    nm, nh,
    ms: nm === 1 ? `one model (${short(row.models[0])})` : `${nm} models`,
    hs: nh === 1 ? `a single harness (${row.harnesses[0]})` : `${nh} harnesses`,
  }
}
const shapeLine = (row) => { const s = shapeOf(row); return `${row.name} has ${s.ms} under ${s.hs}` }
const REASON = { fewer_than_2_balanced_levels_a: 'fewer than two balanced models', fewer_than_2_balanced_levels_b: 'fewer than two balanced harnesses' }
/** Another dataset that CAN answer: `need` = 'harnesses' (≥2), 'models' (≥2) or 'fit' (≥2 of each).
 *  Recorded data first, then the most runs. null when none on this machine qualifies. */
function useAlt(dir, need) {
  const ds = useDatasets()
  return useMemo(() => {
    const ok = (r) => r.name !== dir && (need === 'models' ? r.models.length > 1 : need === 'harnesses' ? r.harnesses.length > 1 : r.models.length > 1 && r.harnesses.length > 1)
    return [...(ds.data || [])].filter(ok).sort((a, b) => (a.kind === 'mock') - (b.kind === 'mock') || b.runs - a.runs)[0] || null
  }, [ds.data, dir, need])
}
const altShape = (a) => `${plural(a.models.length, 'model')} × ${plural(a.harnesses.length, 'harness', 'harnesses')}`
/** Scroll to an element of THIS document (split panes can hold two of the same view). */
const scrollTo = (id) => (e) => {
  const root = e && e.currentTarget && e.currentTarget.closest('.rg-an-main, .rg-an-xp')
  const t = root && root.querySelector(`[data-el~="${id}"]`)
  if (t && t.scrollIntoView) t.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
/* ------------------------------------------------------------------ shell of every an: tab */
function AnNav({ dir, cur, c }) {
  const cs = [short(c.model), c.harness]
  const btn = ([k, l, , n]) => {
    const spec = k === 'traj' ? makeSpec('tasks', dir, ...cs) : makeSpec('an', dir, k, ...cs)
    return (
      <Go key={k} spec={spec} replace className={cx('rg-an-nb', k === cur && 'on')}>
        <span className="rg-ell">{l}</span><span className="n">{n}</span>
      </Go>
    )
  }
  return (
    <nav className="rg-an-nav" data-el="analysis-nav" aria-label="Analysis steps">
      <div className="sh rg-lbl">Study steps</div>
      {AN_VIEWS.slice(0, 4).map(btn)}
      <div className="sh rg-lbl">More analysis</div>
      {AN_VIEWS.slice(4).map(btn)}
    </nav>
  )
}

function AnalysisDoc({ spec, args }) {
  const [dir, v0, m, h] = args
  const ds = useDataset(dir)
  const { conditionFor } = useRig()
  if (ds.error) return <ErrorState error={ds.error} onRetry={ds.reload} what="The dataset index" />
  if (ds.missing) return <NotFound spec={spec} detail={`There is no dataset “${dir}” in data/runs on this machine.`} />
  if (!ds.data) return <Loading label="Reading /api/overview…" />
  const view = v0 || 'family'
  const Body = BODY[view]
  if (!Body) return <NotFound spec={spec} detail={`“${view}” is not an analysis view. The views are ${Object.keys(BODY).join(', ')}.`} />
  const row = ds.data
  const mem = conditionFor(dir)
  const c = resolveCondition(row, m || mem.model, h || mem.harness)
  return (
    <div className="rg-an-split">
      <AnNav dir={dir} cur={view} c={c} />
      <div className="rg-dpad rg-an-main" data-view={view}>
        <AskingSentence dir={dir} model={c.model} harness={c.harness} />
        <Body spec={spec} dir={dir} row={row} c={c} />
      </div>
    </div>
  )
}

function OracleActions() {
  const { oracle, setOracle } = useRig()
  return <span className="rg-hide-sm"><Seg label="Test suite" options={['visible', 'hidden', 'strengthened']} value={oracle} onChange={setOracle} /></span>
}

/** Document head: eyebrow, title, then the ANSWER sentence (`lead`), then the About disclosure. */
function Head({ eyebrow, title, lead, sub, about, actions, el }) {
  return (
    <header className="rg-an-head">
      <div className="rg-dh">
        <div className="t"><div className="rg-eyebrow">{eyebrow}</div><h1>{title}</h1></div>
        {actions && <div className="rg-an-acts">{actions}</div>}
      </div>
      {lead && <p className="rg-an-lead" data-el={el}>{lead}</p>}
      {sub && <p className="rg-an-sub rg-small rg-mute">{sub}</p>}
      {about && <About summary="About this view">{about}</About>}
    </header>
  )
}
/** A section: heading + whitespace instead of another box. */
function Sec({ title, meta, actions, el, children, className }) {
  return (
    <section className={cx('rg-an-sec', className)} data-el={el} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || meta || actions) && (
        <div className="rg-an-sech"><h2>{title}</h2>{meta && <span className="rg-an-sechm">{meta}</span>}{actions && <span className="rg-an-secha">{actions}</span>}</div>
      )}
      {children}
    </section>
  )
}

/* ------------------------------------------------------------------ 1 · family matrix */
function Family({ spec, dir, row, c }) {
  const { oracle, isMobile } = useRig()
  const R = useRuns(dir)
  const [mf, setMf] = useTabState(spec, 'mf', 'all')
  const [pg0, setPg] = useTabState(spec, 'pg', 0)
  const d = useMemo(() => {
    if (!R.data) return null
    const by = new Map()
    for (const r of R.data) { const k = r.model + '\u0000' + r.harness; if (!by.has(k)) by.set(k, []); by.get(k).push(r) }
    const cell = (m, h) => by.get(m + '\u0000' + h) || []
    // repeats per task, per model, over the families it was run in
    const reps = {}
    for (const m of row.models) {
      const s = new Set()
      for (const h of row.harnesses) { const per = {}; for (const r of cell(m, h)) per[r.task] = (per[r.task] || 0) + 1; for (const n of Object.values(per)) s.add(n) }
      reps[m] = [...s].sort((a, b) => a - b)
    }
    const ns = new Set()
    for (const m of row.models) for (const h of row.harnesses) { const n = cell(m, h).length; if (n) ns.add(n) }
    return { cell, reps, ns: [...ns].sort((a, b) => a - b) }
  }, [R.data, row])
  if (R.error) return <ErrorState error={R.error} onRetry={R.reload} what={`The run index of ${dir}`} />
  if (!d) return <Loading label={`Reading /api/results/${dir}/runs…`} />
  const H = row.harnesses
  const ps = isMobile ? 2 : 6
  const pages = Math.max(1, Math.ceil(H.length / ps))
  const pg = Math.max(0, Math.min(pg0, pages - 1))
  const cols = H.slice(pg * ps, pg * ps + ps)
  const models = row.models.filter((m) => mf === 'all' || m === mf)
  const maxRep = Math.max(0, ...Object.values(d.reps).flat())
  const fewer = row.models.filter((m) => d.reps[m].length && d.reps[m][d.reps[m].length - 1] < maxRep)
  const nObserved = row.models.reduce((a, m) => a + H.filter((h) => d.cell(m, h).length).length, 0)
  // the answer: highest and lowest observed family under the current suite, with their denominators
  const fams = row.models.flatMap((m) => H.map((h) => ({ m, h, o: tally(d.cell(m, h), oracle) }))).filter((x) => x.o.known > 0)
  const hi = [...fams].sort((a, b) => b.o.rate - a.o.rate || b.o.known - a.o.known)[0]
  const lo = [...fams].sort((a, b) => a.o.rate - b.o.rate || b.o.known - a.o.known)[0]
  const famTxt = (x) => <><b className="rg-mono">{short(x.m)}</b> under <b className="rg-mono">{x.h}</b> at {pct(x.o.rate)} ({x.o.p}/{x.o.known})</>
  const lead = !fams.length ? `No family in ${dir} has a ${oracle}-suite grade yet.`
    : fams.length === 1 ? <>One family was measured: {famTxt(hi)}, {oracle}-suite grades.</>
      : <>Highest: {famTxt(hi)}; lowest: {famTxt(lo)} — {oracle}-suite grades among known ones, over {plural(fams.length, 'family', 'families')}.</>
  const td = (m, h) => {
    const l = d.cell(m, h)
    if (!l.length) return <td key={h}><div className="rg-an-mc empty"><span className="v2">not run</span></div></td>
    const o = tally(l, oracle)
    const on = m === c.model && h === c.harness
    const nt = new Set(l.map((r) => r.task)).size
    const label = `${short(m)} · ${h} · ${dir}`
    return (
      <td key={h}>
        <div className={cx('rg-an-mcw', on && 'on')}>
          <Go spec={makeSpec('tasks', dir, short(m), h)} className={cx('rg-an-mc', on && 'on')} title={`Open the trajectories of ${short(m)} under ${h}`}>
            <span className="v1" aria-label={`${short(m)} under ${h}: ${pct(o.rate)}`}>{pct(o.rate)}</span>
            <span className="v2">{o.p} / {o.known} known{o.u ? ` · ${o.u} unknown` : ''}</span>
            <OutcomeBar p={o.p} f={o.f} u={o.u} />
            <span className="v2">n={l.length} · {plural(nt, 'task')}</span>
          </Go>
          <span className="pin"><PinButton compact item={{ id: `cell:${dir}:${m}:${h}`, kind: 'cell', label, value: `${pct(o.rate)} · ${o.p}/${o.known} known (${oracle})`, spec: makeSpec('ds', dir, short(m), h), dir, model: m, harness: h }} /></span>
        </div>
      </td>
    )
  }
  return (<>
    <Head eyebrow="Step 1 · who was measured" title="Family matrix" el="family-answer" lead={lead}
      sub={<>{int(R.data.length)} trajectories · {nObserved} observed families · {plural(row.models.length, 'model')} × {plural(row.harnesses.length, 'harness', 'harnesses')}</>}
      actions={<PinButton item={{ id: `figure:${dir}:family`, kind: 'figure', label: `Family matrix · ${dir}`, value: `${nObserved} families · ${oracle}-suite grades`, spec: makeSpec('an', dir, 'family'), dir }} />}
      about={<>
        <p>A family is one model under one harness. Each cell counts its runs under the suite chosen in the sentence above: the rate is among <b>known</b> grades, and unknown grades are shown beside it, never folded into fail.</p>
        <p>Select a cell to open its trajectories; the pin keeps it in the case file. Column headers open the focused model under that harness.</p>
      </>} />
    <div data-el="family-matrix">
      <div className="rg-row rg-wrap rg-an-bar">
        <label className="rg-fld">Model filter
          <select className="rg-selc" value={mf} onChange={(e) => { setMf(e.target.value) }} aria-label="Model filter">
            <option value="all">All models</option>
            {row.models.map((m) => <option key={m} value={m}>{short(m)}</option>)}
          </select>
        </label>
        <span className="rg-grow" />
        {pages > 1 ? (
          <span className="rg-row rg-wrap rg-an-pg" data-el="family-pages">
            <span className="rg-small rg-mute">harnesses {pg * ps + 1}–{pg * ps + cols.length} of {H.length}</span>
            {pg > 0 && <button type="button" className="rg-btn" onClick={() => setPg(pg - 1)}>← Previous harnesses</button>}
            {pg < pages - 1 && <button type="button" className="rg-btn" onClick={() => setPg(pg + 1)}>More harnesses →</button>}
          </span>
        ) : <span className="rg-small rg-mute" data-el="family-pages">all {plural(H.length, 'harness', 'harnesses')} shown</span>}
      </div>
      {d.ns.length > 1 && (
        <Note tone="amb" el="repeat-counts-differ"><span className="rg-amb" aria-hidden="true">▲</span><div><b>Repeat counts differ.</b> Trials per family range {d.ns[0]}–{d.ns[d.ns.length - 1]}
          {fewer.length ? <> ({fewer.map((m) => `${short(m)} has ${d.reps[m].join('–')} repeat${d.reps[m].join() === '1' ? '' : 's'} per task`).join('; ')})</> : null}, so intervals are wider where there are fewer.</div></Note>
      )}
      <div className="rg-pnl rg-an-mxp"><div className="rg-tblwrap">
        <table className="rg-an-mx">
          <thead><tr><th>model × harness</th>{cols.map((h) => (
            <th key={h}><Go spec={makeSpec('tasks', dir, short(c.model), h)} className="rg-an-colh" title={`Open ${short(c.model)} under ${h} (the focused model) in the trajectory explorer`}>{h}</Go></th>))}</tr></thead>
          <tbody>{models.map((m) => (
            <tr key={m}><td className="rh"><span className="rg-mono">{short(m)}</span>{m.includes('/') && <div className="rg-tiny rg-mute">{m.split('/')[0]}</div>}</td>{cols.map((h) => td(m, h))}</tr>
          ))}</tbody>
        </table>
      </div></div>
      <div className="rg-row rg-wrap rg-small rg-mute rg-an-foot">
        <span data-el="grade-basis">{models.length * H.length} families in the current filter · grade basis: <b className="rg-dim">{oracle}-suite grades, among known grades</b>; unknown is shown, never folded into fail.</span>
        <Go spec={makeSpec('field', dir)} className="rg-tlink">Every run, every span →</Go>
      </div>
    </div>
  </>)
}

/* ------------------------------------------------------------------ 2 · outcomes */
function Ribbon({ list, oracle, specOf, cur }) {
  return (
    <div className="rg-an-rib">
      {list.map((r, i) => {
        const v = verdict(r, oracle)
        const w = v === true ? 'pass' : v === false ? 'fail' : 'unknown'
        return (
          <Go key={r.id} spec={specOf(i)} className={cx('rg-an-c', v === true ? 'p' : v === false ? 'f' : 'u', cur === i && 'cur')}
            title={`Run ${String(i + 1).padStart(2, '0')} · ${w}${failureModeOf(r, oracle) ? ' · ' + failureModeOf(r, oracle).replace(/_/g, ' ') : ''} · ${r.id}`}>
            <span className="sr-only">{`Run ${i + 1} ${w}`}</span><span aria-hidden="true">{v === true ? '✓' : v === false ? '×' : '?'}</span>
          </Go>
        )
      })}
    </div>
  )
}
/** Count failure modes over a list; returns [[mode, n], …] + unexplained count. */
function modeCounts(list, oracle) {
  const counts = {}
  let unexplained = 0
  for (const r of list) {
    if (verdict(r, oracle) !== false) continue
    const m = failureModeOf(r, oracle)
    if (m) counts[m] = (counts[m] || 0) + 1; else unexplained++
  }
  return { rows: Object.entries(counts).sort((a, b) => b[1] - a[1]), unexplained }
}
function ModeChips({ list, oracle, dir }) {
  const { rows, unexplained } = modeCounts(list, oracle)
  if (!rows.length && !unexplained) return null
  return (
    <span className="rg-row rg-wrap rg-an-modes">
      {rows.map(([m, n]) => <span key={m} className="rg-row rg-an-mode"><FailureChip mode={m} dir={dir} /><span className="rg-mono rg-tiny">×{n}</span></span>)}
      {unexplained > 0 && <span className="rg-tiny rg-mute">{unexplained} mode not recorded</span>}
    </span>
  )
}
/** Repeats per task in a run list: "8 repeats" or, when uneven, "1–65 repeats per task". */
function repeatsText(list) {
  const per = {}
  for (const r of list) per[r.task] = (per[r.task] || 0) + 1
  const v = Object.values(per)
  if (!v.length) return '—'
  const lo = Math.min(...v), hi = Math.max(...v)
  return lo === hi ? plural(lo, 'repeat') : `${lo}–${hi} repeats per task`
}

function Outcomes({ dir, c }) {
  const { oracle } = useRig()
  const M = useMetrics(dir)
  const R = useRuns(dir)
  const T = useTasks()
  const d = useMemo(() => {
    if (!M.data || !R.data) return null
    const cell = metricsCell(M.data, c.model, c.harness)
    const list = conditionRuns(R.data, c.model, c.harness)
    const tasks = [...new Set([...Object.keys((cell && cell.tasks) || {}), ...list.map((r) => r.task)])].sort()
    const rows = tasks.map((t) => {
      const l = list.filter((r) => r.task === t).sort((a, b) => (a.rep ?? 0) - (b.rep ?? 0))
      return { t, l, o: tally(l, oracle), m: cell && cell.tasks ? cell.tasks[t] : null }
    })
    return { cell, list, rows, o: tally(list, oracle) }
  }, [M.data, R.data, c.model, c.harness, oracle])
  if (M.error) return <ErrorState error={M.error} onRetry={M.reload} what={`/api/results/${dir}/metrics`} />
  if (R.error) return <ErrorState error={R.error} onRetry={R.reload} what={`The run index of ${dir}`} />
  if (!d) return <Loading label={`Reading /api/results/${dir}/metrics…`} />
  const { cell } = d
  if (!cell) return (
    <Empty title="No metrics for this condition." actions={[
      { label: 'Pick an observed family in the matrix', spec: makeSpec('an', dir, 'family', short(c.model), c.harness), primary: true },
      { label: `Every run of ${dir}`, spec: makeSpec('field', dir) },
    ]}>{short(c.model)} was not run under {c.harness} in {dir}, so there is no pass@1 to estimate. The family matrix shows which combinations were run.</Empty>
  )
  const probe = Object.fromEntries(((Array.isArray(T.data) && T.data) || []).map((t) => [t.id, t.probe]))
  const nT = d.rows.length
  const [lo, hi] = cell.ci95 || [null, null]
  const flips = d.rows.filter((x) => x.o.p > 0 && x.o.f > 0).length
  const nCellTasks = Object.keys(cell.tasks || {}).length
  const mflips = Object.values(cell.tasks || {}).filter((t) => t.c > 0 && t.c < t.n).length
  const curve = (cell.passk_curve || []).filter((p) => [1, 2, 3, 5, 8, 10].includes(p.k))
  const p1 = cell['pass@1']
  // Strengthened figures only where a run of this condition carries a strengthened grade;
  // the backend reports 0.0 when none does, which is "not graded", not a collapse.
  const hasStrong = d.list.some((r) => r.str != null)
  const sd = !hasStrong || cell.pass1_strong == null || p1 == null ? null : cell.pass1_strong - p1
  const cs = [dir, short(c.model), c.harness]
  const reps = repeatsText(d.list)
  const nLine = `n = ${int(cell.runs)} · ${plural(nT, 'task')} × ${reps}`
  const figLabel = `pass@1 · ${short(c.model)} · ${c.harness} · ${dir}`
  const lead = <>pass@1 for <b className="rg-mono">{short(c.model)}</b> under <b className="rg-mono">{c.harness}</b> is <b>{pct(p1)}</b>, 95% CI {ciText(cell.ci95)}, on the hidden suite ({nLine}){nT > 1 ? <>; {flips} of {nT} tasks flip between pass and fail</> : null}.</>
  return (<>
    <Head eyebrow="Step 2 · an estimate with an interval, on a stated oracle" title="Outcomes" el="outcomes-answer" lead={lead}
      actions={<PinButton item={{ id: `figure:${dir}:outcomes:${c.model}:${c.harness}`, kind: 'figure', label: figLabel, value: `pass@1 ${pct(p1)} ${ciText(cell.ci95)} · n=${cell.runs} (hidden)`, spec: makeSpec('an', dir, 'outcomes', short(c.model), c.harness), dir, model: c.model, harness: c.harness }} />}
      about={<>
        <p>pass@1, its interval, pass^k and the flip rate come from <span className="rg-mono">/metrics</span>, graded against the hidden suite. The per-task strips follow the suite chosen in the sentence above.</p>
        <p><b>pass^k</b> is the chance that k attempts in a row all pass — what a user experiences. <b>pass@k</b> is the chance that at least one of k passes — a leaderboard with retries. The flip rate is the share of tasks whose repeats disagree.</p>
        <p>The strengthened suite adds cases to the hidden one; a drop means some passes did not survive it.</p>
      </>} />
    {oracle !== 'hidden' && (
      <Note tone="amb"><span className="rg-amb" aria-hidden="true">▲</span><div><b>The interval and pass^k are hidden-suite estimates.</b> Under the {oracle} suite this condition passes {d.o.p} of {d.o.known} known attempts ({pct(d.o.rate)}); the per-task strips use the {oracle} suite.</div></Note>
    )}
    <div className="rg-stats rg-an-okpis">
      <div className="rg-stat" data-el="outcomes-headline"><span className="rg-lbl">pass@1 · hidden suite</span>
        <span className="rg-fig">{p1 == null ? '—' : (p1 * 100).toFixed(1)}<span className="u">{p1 == null ? '' : '%'}</span></span>
        <CiBar lo={lo} hi={hi} pt={p1} />
        <span className="rg-mono rg-small">95% CI {ciText(cell.ci95)}{lo != null && hi != null && <> · <b className="rg-amb">{((hi - lo) * 100).toFixed(0)} points wide</b></>}</span>
        <span className="rg-mono rg-small rg-mute">n={int(cell.runs)} · {plural(nCellTasks, 'task')} × {reps}</span></div>
      <div className="rg-stat" data-el="pass-k"><span className="rg-lbl">pass^3 · three in a row</span><span className="rg-fig md">{pct(cell['pass^3'])}</span>
        <span className="rg-small rg-dim">pass@3 <span className="rg-mono">{pct(cell['pass@3'])}</span> with retries</span></div>
      <div className="rg-stat" data-el="flip-rate"><span className="rg-lbl">flip rate</span><span className="rg-fig md">{pct(cell.flip_rate, 0)}</span><span className="rg-small rg-dim">{mflips} of {nCellTasks} tasks have mixed outcomes</span></div>
      <div className="rg-stat" data-el="strengthened-delta"><span className="rg-lbl">strengthened suite</span>
        {hasStrong
          ? <><span className="rg-fig md">{pct(cell.pass1_strong)}</span><span className={cx('rg-mono rg-small', sd < 0 ? 'rg-red' : 'rg-dim')}>Δ {pp(sd)} vs hidden</span></>
          : <><span className="rg-fig md">—</span><span className="rg-small rg-dim">not graded: no run in this condition has a strengthened grade</span></>}</div>
    </div>
    <Sec title="Per task · repeats in order" meta={`${oracle} suite · ${flips} of ${nT} tasks flip`} el="per-task-repeats">
      <div className="rg-pnl">
        <div className="rg-an-tlist">{d.rows.map((x) => {
          const fl = x.o.p > 0 && x.o.f > 0
          const pr = probe[x.t]
          return (
            <div key={x.t} className="rg-an-trep">
              <Go spec={makeSpec('task', ...cs, x.t)} className="rg-an-tname rg-tlink" title={x.t}>{pr && pr !== 'none' && <span className="rg-amb" title={`probe: ${pr}`}>◆ </span>}{x.t}</Go>
              <Ribbon list={x.l} oracle={oracle} specOf={(i) => makeSpec('task', ...cs, x.t, i)} />
              <span className="rg-mono rg-small rg-an-cnt">{x.o.p}/{x.o.known}{x.o.u ? <span className="rg-mute"> · {x.o.u}?</span> : null}</span>
              <span className="rg-an-tags">{fl && <Tag tone="amb">flips</Tag>}<ModeChips list={x.l} oracle={oracle} dir={dir} /></span>
            </div>
          )
        })}</div>
      </div>
      <div className="rg-row rg-wrap rg-small rg-mute rg-an-legend">
        <span className="rg-row rg-an-lg"><Verdict v={true} /> pass</span><span className="rg-row rg-an-lg"><Verdict v={false} /> fail</span>
        <span className="rg-row rg-an-lg"><Verdict v={null} /> unknown, never a failure</span><span><span className="rg-amb">◆</span> task carries a probe</span>
        <span data-el="failure-summary">{failureSummary(d.list, oracle) || 'no failures in this condition'}</span>
      </div>
    </Sec>
    <Sec title="Retries against consistency" meta="pass@k and pass^k · hidden suite" el="passk-ladder">
      {curve.length ? (
        <div className="rg-pnl rg-an-ladw"><table className="rg-tbl rg-an-tbl"><thead><tr><th>k attempts</th><th className="rg-num">pass@k · any passes</th><th className="rg-num">pass^k · all pass</th></tr></thead>
          <tbody>{curve.map((p) => <tr key={p.k}><td className="rg-mono">{p.k}</td><td className="rg-num">{pct(p.pass_at_k)}</td><td className="rg-num">{pct(p.pass_pow_k)}</td></tr>)}</tbody></table></div>
      ) : (
        <Empty title="No pass@k curve for this condition." actions={[{ label: 'Open the per-task strips', onClick: scrollTo('per-task-repeats') }, { label: 'Every attempt in the explorer', spec: makeSpec('tasks', ...cs) }]}>
          The metrics cell carries no pass@k curve, so only pass@3 and pass^3 above are available. The per-task strips show every attempt.
        </Empty>
      )}
    </Sec>
  </>)
}

/* ------------------------------------------------------------------ 3 · comparison */
const VS_TONES = ['k0', 'k1', 'k2', 'k3', 'k4', 'k5']
function Variance({ E, dir, row, c }) {
  const alt = useAlt(dir, 'fit')
  const T = (E && E.anova && E.anova.table) || []
  if (!T.length) {
    const r = E && E.fit && E.fit.reason
    const S = shapeOf(row)
    const why = r === 'fewer_than_2_balanced_levels_a' ? `${shapeLine(row)}, so there is no model factor to split the variance by.`
      : r === 'fewer_than_2_balanced_levels_b' ? `${shapeLine(row)}, so there is no harness factor to split the variance by.`
        : `The experiment endpoint returned no ANOVA table for ${dir}${r ? ` (${String(r).replace(/_/g, ' ')})` : ''}.`
    return (
      <Sec title="Variance decomposition" el="variance-decomposition">
        <Empty title="No variance split for this dataset." actions={[
          { label: 'Cell grid with intervals', spec: makeSpec('an', dir, 'fit', short(c.model), c.harness), primary: true },
          S.nm > 1 && { label: 'Compare the models in the family matrix', spec: makeSpec('an', dir, 'family', short(c.model), c.harness) },
          alt && { label: `Open the variance split in ${alt.name}`, spec: makeSpec('an', alt.name, 'delta') },
        ]}>{why} A split needs at least two balanced models and two balanced harnesses.</Empty>
      </Sec>
    )
  }
  const tot = T.reduce((a, r) => a + (r.share || 0), 0) || 1
  const F = E.fit || {}
  return (
    <Sec title="Variance decomposition" meta={`sums of squares · SS total ${fx(E.anova.ss_total, 1)} · n ${int(E.anova.n)}`} el="variance-decomposition"
      actions={<PinButton compact item={{ id: `figure:${dir}:variance`, kind: 'figure', label: `Variance decomposition · ${dir}`, value: T.map((r) => `${r.term} ${pct(r.share)}`).join(' · '), spec: makeSpec('an', dir, 'delta'), dir }} />}>
      {F.model && F.harness && <p className="rg-an-p">Model carries {pct(F.model.share)} of the variance, harness {pct(F.harness.share)}; most of the rest is task-by-cell structure and repeat noise.</p>}
      <div className="rg-an-vstack" role="img" aria-label="variance shares">{T.map((r, i) => <span key={r.term} className={VS_TONES[i % 6]} title={`${r.term} ${pct(r.share)}`} style={{ width: `${((r.share || 0) / tot) * 100}%` }} />)}</div>
      <div className="rg-pnl rg-an-ladw"><table className="rg-tbl rg-an-tbl"><thead><tr><th>term</th><th className="rg-num">share of variance</th></tr></thead>
        <tbody>{T.map((r, i) => <tr key={r.term}><td><span className="rg-row rg-an-lg"><span className={cx('rg-an-sw', VS_TONES[i % 6])} />{r.term}</span></td><td className="rg-num">{pct(r.share)}</td></tr>)}</tbody></table></div>
    </Sec>
  )
}

function Delta({ spec, dir, row, c }) {
  const { openTab, isMobile } = useRig()
  const C = useComparisons(dir)
  const E = useExperiment(dir)
  const alt = useAlt(dir, 'harnesses')
  const [sel, setSel] = useTabState(spec, 'sel', null)
  if (C.error) return <ErrorState error={C.error} onRetry={C.reload} what={`/api/results/${dir}/comparisons`} />
  if (!C.data) return <Loading label={`Reading /api/results/${dir}/comparisons…`} />
  const base = C.data.baseline || 'baseline'
  const comp = C.data.comparisons || {}
  const hs = row.harnesses.filter((h) => h !== base && Object.values(comp).some((x) => x && x[h]))
  const ms = row.models.filter((m) => comp[m] && Object.keys(comp[m]).length)
  const all = ms.flatMap((m) => hs.map((h) => comp[m][h] && { m, h, ...comp[m][h] }).filter(Boolean))
  const seps = all.filter((x) => sepOf(x.ci95))
  const nSep = seps.length
  const mx = symMax(all.flatMap((x) => [...(x.ci95 || []), x.mean_diff]))
  const [sm, sh] = sel ? sel.split('|') : []
  const selModel = sm ? ms.find((m) => short(m) === sm) : null
  const x = selModel && comp[selModel] ? comp[selModel][sh] : null
  const S = shapeOf(row)
  const pick = (m, h) => {
    const key = short(m) + '|' + h
    setSel(sel === key ? null : key)
    if (sel !== key && !isMobile) openTab(makeSpec('tasks', dir, short(m), h), { side: true })
  }
  const big = [...seps].sort((a, b) => Math.abs(b.mean_diff) - Math.abs(a.mean_diff))[0]
  const lead = !all.length ? null
    : nSep ? <>{nSep} of {all.length} contrasts separate from <span className="rg-mono">{base}</span>; the largest is <b className="rg-mono">{short(big.m)}</b> under <b className="rg-mono">{big.h}</b>, Δ {pp(big.mean_diff)} [{pp(big.ci95[0])}, {pp(big.ci95[1])}].</>
      : all.length === 1 ? <>The one contrast, <b className="rg-mono">{short(all[0].m)}</b> under <b className="rg-mono">{all[0].h}</b> at Δ {pp(all[0].mean_diff)}, does not separate from <span className="rg-mono">{base}</span>: its 95% interval covers zero.</>
        : <>None of the {all.length} contrasts separates from <span className="rg-mono">{base}</span>: every 95% interval covers zero.</>
  const noAnova = !((E.data && E.data.anova && E.data.anova.table) || []).length
  return (<>
    <Head eyebrow="Step 3 · attribute the difference" title="Comparison" el="delta-answer" lead={lead}
      about={<>
        <p>Each cell is the paired Δ pass@1 of one model under one harness against <span className="rg-mono">{base}</span>, paired by task. A contrast <b>separates</b> when its 95% interval excludes zero; one that covers zero is not evidence of no effect, only of no detectable one at this n.</p>
        <p>Selecting a cell shows its per-task Δ and opens its trajectories in the split pane (<Kbd>{MOD} click</Kbd> on any link also opens to the side). Descriptive, over these tasks only.</p>
      </>} />
    <div data-el="delta-matrix">
      {!all.length ? (
        <Empty title={S.nh === 1 ? 'One harness, so nothing to compare against.' : `No runs paired against ${base}.`} actions={[
          S.nm > 1 && { label: `Compare its ${S.nm} models in the family matrix`, spec: makeSpec('an', dir, 'family', short(c.model), c.harness), primary: true },
          { label: 'Outcomes with CI for this condition', spec: makeSpec('an', dir, 'outcomes', short(c.model), c.harness), primary: S.nm < 2 },
          alt && { label: `Open the comparison in ${alt.name} (${altShape(alt)})`, spec: makeSpec('an', alt.name, 'delta') },
        ]}>{S.nh === 1
          ? <>{shapeLine(row)}. A Δ pairs a second harness against <span className="rg-mono">{base}</span> task by task, so this dataset cannot answer it; its {S.nm > 1 ? 'models' : 'runs'} can still be compared side by side.</>
          : <>No harness in {dir} has runs paired with <span className="rg-mono">{base}</span> on the same tasks, so no Δ can be estimated.</>}</Empty>
      ) : (<>
        <div className="rg-row rg-wrap rg-an-bar">
          <span className="rg-small rg-row rg-wrap rg-an-key"><b className="rg-mono" data-el="delta-count">{nSep} of {all.length} separate</b><span className="rg-mute"><span className="rg-acc">◆</span> separates</span><span className="rg-mute">○ covers 0</span><span className="rg-mute">| zero</span></span>
          <span className="rg-grow" />
          <PinButton item={{ id: `figure:${dir}:delta`, kind: 'figure', label: `Δ-matrix vs ${base} · ${dir}`, value: `${nSep} of ${all.length} contrasts separate`, spec: makeSpec('an', dir, 'delta'), dir }} /></div>
        <div className="rg-pnl rg-an-mxp"><div className="rg-tblwrap">
          <table className="rg-an-mx rg-an-dmx">
            <thead><tr><th>model · Δ vs {base}</th>{hs.map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>{ms.map((m) => (
              <tr key={m}><td className="rh"><span className="rg-mono">{short(m)}</span></td>{hs.map((h) => {
                const y = comp[m][h]
                if (!y) return <td key={h}><div className="rg-an-mc empty"><span className="v2">not paired</span></div></td>
                const sp = sepOf(y.ci95)
                const on = sel === short(m) + '|' + h
                return (
                  <td key={h}><button type="button" className={cx('rg-an-mc', sp && 'sep', on && 'on')} aria-pressed={on} onClick={() => pick(m, h)}
                    aria-label={`${short(m)} ${h}: delta ${pp(y.mean_diff)}, ${sp ? 'separates' : 'covers zero'}`}>
                    <span className="v1">{pp(y.mean_diff)}</span>
                    <CiBar lo={y.ci95 && y.ci95[0]} hi={y.ci95 && y.ci95[1]} pt={y.mean_diff} min={-mx} max={mx} zero={0} />
                    <span className="v2">[{pp(y.ci95 && y.ci95[0])}, {pp(y.ci95 && y.ci95[1])}]</span>
                    <span className={cx('v2', sp && 'rg-acc')}>{sp ? '◆ separates' : '○ covers 0'}</span>
                  </button></td>
                )
              })}</tr>))}</tbody>
          </table>
        </div></div>
        {x ? (
          <div className="rg-pnl rg-an-sel" data-el="delta-detail">
            <div className="rg-pnl-h"><span className="rg-lbl">{sm} · {sh} vs {base}</span><span className="meta">{x.n_tasks} tasks · paired by task
              <PinButton compact item={{ id: `cell:${dir}:${selModel}:${sh}`, kind: 'cell', label: `${sm} · ${sh} vs ${base} · ${dir}`, value: `Δ ${pp(x.mean_diff)} ${'[' + pp(x.ci95 && x.ci95[0]) + ', ' + pp(x.ci95 && x.ci95[1]) + ']'}`, spec: makeSpec('an', dir, 'delta', sm, sh), dir, model: selModel, harness: sh }} /></span></div>
            <div className="rg-pnl-b">
              <div className="rg-col rg-an-drows">{Object.entries(x.per_task || {}).map(([t, v]) => (
                <div key={t} className="rg-an-drow"><span className="rg-mono rg-small rg-ell" title={t}>{t}</span><DBar v={v} max={1} /><span className={cx('rg-mono rg-small rg-num', v < 0 ? 'rg-red' : v > 0 ? 'rg-sky' : 'rg-mute')}>{pp(v, 0)}</span></div>))}</div>
              <NextSteps actions={[
                { label: 'Open its trajectories to the side ⇥', spec: makeSpec('tasks', dir, sm, sh), side: true, primary: true },
                { label: 'Outcomes for this cell', spec: makeSpec('an', dir, 'outcomes', sm, sh) },
              ]} />
            </div>
          </div>
        ) : <p className="rg-small rg-mute rg-an-mt">Select a cell to see its per-task Δ; its trajectories open in the split pane.</p>}
      </>)}
    </div>
    {!(noAnova && !all.length) && (
      E.error ? <ErrorState error={E.error} onRetry={E.reload} what={`/api/results/${dir}/experiment`} /> : !E.data ? <Loading label="Reading the experiment fit…" /> : <Variance E={E.data} dir={dir} row={row} c={c} />
    )}
  </>)
}

/* ------------------------------------------------------------------ 5 · judge & integrity */
function integrityFlags(I, hasStrong) {
  if (!I) return []
  const leakage = I.leakage || []
  const L = (t) => leakage.find((x) => x.task === t)
  const out = []
  for (const x of leakage.filter((y) => y.probe === 'solution_leak')) {
    out.push({ k: 'contamination probe', body: <><b className="rg-mono">{x.task}</b> is the contamination probe: the issue text carries the fix. It scores {pct(x.pass1)} here, with patches echoing the issue at {pct(x.patch_issue_similarity, 0)}.</> })
  }
  // weak-test flags compare against strengthened grades; with none recorded they would be invented
  const weak = hasStrong ? ((I.weak_tests && I.weak_tests.per_task) || []).filter((x) => x.lost >= 0.05).sort((a, b) => b.lost - a.lost) : []
  if (weak.length) {
    out.push({ k: 'weak hidden tests', body: <>Hidden tests are weak on {weak.map((w, i) => <span key={w.task}>{i ? ', ' : ''}<span className="rg-mono">{w.task}</span></span>)}: {weak.map((w, i) => { const l = L(w.task); return <span key={w.task}>{i ? ' · ' : ''}<span className="rg-mono">{w.task.split('_')[0]}</span> {l ? `${pct(l.pass1, 0)} → ${pct(l.pass1_strong, 0)}` : `${pct(w.lost, 0)} of passes lost`}</span> })} under the strengthened suite.</> })
  }
  const over = [...(I.self_report || [])].sort((a, b) => b.overclaim - a.overclaim)[0]
  if (over && over.overclaim > 0) out.push({ k: 'over-claiming harness', body: <><b className="rg-mono">{over.harness}</b> over-claims most: in {pct(over.overclaim)} of its runs the agent's last test run passed and the hidden suite did not.</> })
  return out
}

function LlmJudge({ canCompare }) {
  const J = useApi(paths.judge())
  const ov = useOverview()
  const [msg, setMsg] = useState(null)
  const d = J.data || {}
  const rep = d.report
  const figs = rep && typeof rep === 'object' ? Object.entries(rep).filter(([, v]) => typeof v === 'number').slice(0, 4) : []
  const key = !!(ov.data && ov.data.key_present)
  const running = d.status === 'running'
  const past = (d.history || []).length
  const seeChecks = canCompare
    ? { label: 'See the oracle agreement (no key needed)', onClick: scrollTo('oracle-comparison') }
    : { label: 'See the integrity flags (no key needed)', onClick: scrollTo('integrity-flags') }
  const run = async () => {
    setMsg(null)
    try { await api('/judge/run', { method: 'POST', body: {} }); setMsg({ ok: true, t: 'The judge started. Its report appears here when it finishes.' }); invalidate('/judge') } catch (e) { setMsg({ ok: false, t: e.message }) }
  }
  return (
    <Sec title="LLM-as-judge · checked against the suites" meta={running ? <Tag tone="acc">running</Tag> : past ? `${plural(past, 'previous run')}` : null} el="llm-judge">
      <p className="rg-an-p">The same patches scored by a model judge, twice with the order swapped: its agreement with the suite (κ), how often the swap changes the verdict, and the pull of sheer length.</p>
      {J.error ? (
        <ErrorState error={J.error} onRetry={J.reload} what="/api/judge" />
      ) : figs.length ? (
        <div className="rg-stats rg-an-jfigs">{figs.map(([k, v]) => <div key={k} className="rg-stat"><span className="rg-lbl">{k.replace(/_/g, ' ')}</span><span className="rg-fig sm">{fx(v, 2)}</span></div>)}</div>
      ) : IS_STATIC ? (
        <Empty title="No judge report in this export." actions={[seeChecks]} el="empty-state read-only-export-state">
          This is a static export, so the judge cannot run here. Start the live app to run it: <CopyCmd cmd={LIVE_CMD} />
        </Empty>
      ) : !key ? (
        <Empty title="No judge report yet." actions={[{ label: "Set the lab's server key in Settings", spec: 'settings', primary: true }, seeChecks]}>
          Running the judge needs the lab's server key, which is not set on this server. The oracle agreement and integrity checks on this page need no key.
        </Empty>
      ) : (
        <Empty title={running ? 'The judge is running.' : 'No judge report yet.'} actions={[seeChecks]}>
          {running ? 'Its report appears here when it finishes.' : 'The lab key is set, so the judge can run now. It spends tokens on that key.'}
        </Empty>
      )}
      {!IS_STATIC && key && !J.error && (
        <div className="rg-row rg-wrap rg-an-mt">
          <ConfirmAction label={running ? 'Judge running…' : figs.length ? 'Run the judge again' : 'Run the judge'} confirmLabel="Start the judge" disabled={running}
            why="A judge run is in progress; its report appears here when it finishes."
            detail="Sends POST /api/judge/run with the lab key; it spends tokens." onConfirm={run} />
        </div>
      )}
      {msg && <Note tone={msg.ok ? 'acc' : 'red'} el={msg.ok ? 'judge-started' : 'judge-error'}><div>{msg.t}{!msg.ok && <NextSteps actions={[{ label: 'Retry', onClick: run }, { label: 'Check the lab key in Settings', spec: 'settings' }]} />}</div></Note>}
      {d.error && <Note tone="red" el="judge-error"><div><b>The last judge run failed:</b> {d.error}<NextSteps actions={[{ label: 'Check the lab key in Settings', spec: 'settings' }]} /></div></Note>}
    </Sec>
  )
}

function Judge({ dir, c }) {
  const O = useOracle(dir)
  const I = useIntegrity(dir, c.harness)
  const alt = useAlt(dir, 'models')
  const AO = useOracle(O.data && !O.data.n_eligible && alt ? alt.name : null)   // only asked when this dataset has no oracle pair
  if (O.error) return <ErrorState error={O.error} onRetry={O.reload} what={`/api/results/${dir}/oracle`} />
  if (!O.data) return <Loading label={`Reading /api/results/${dir}/oracle…`} />
  const o = O.data
  const cells = o.cells || {}
  const both = !!o.n_eligible
  const ra = o.rate_a && o.rate_a.rate, rb = o.rate_b && o.rate_b.rate
  const dd = ra == null || rb == null ? null : rb - ra
  const flags = integrityFlags(I.data, both)
  const leak = (I.data && I.data.leakage) || []
  const altOk = alt && AO.data && AO.data.n_eligible > 0
  const nFlags = I.data ? flags.length : null
  const flagTxt = nFlags == null ? '' : nFlags ? `; ${plural(nFlags, 'integrity flag')} ${nFlags === 1 ? 'is' : 'are'} open` : '; no integrity flag is open'
  const lead = both
    ? <>{dd != null && dd < 0 ? <>The strengthened suite lowers the pass rate by <b>{(Math.abs(dd) * 100).toFixed(1)} points</b></> : <>The strengthened suite does not lower the pass rate</>} ({pct(ra)} → {pct(rb)}, κ {fx(o.kappa)}, {int(o.n_eligible)} runs){flagTxt}.</>
    : <>Only the hidden suite graded {dir}: no run carries a strengthened grade, so its oracle cannot be checked against a stronger one{flagTxt}.</>
  const harness = I.data && I.data.harness ? I.data.harness : c.harness
  return (<>
    <Head eyebrow="More analysis · the oracle is an instrument too" title="Judge & integrity" el="judge-answer" lead={lead}
      about={<>
        <p>The hidden suite is what every pass rate here is graded by. The strengthened suite adds cases; runs graded by both show how much of the score survives a stronger oracle, and κ is the agreement between the two beyond chance.</p>
        <p>Integrity flags come from <span className="rg-mono">/integrity</span> for the <span className="rg-mono">{harness}</span> harness: a contamination probe (the issue text carries the fix), weak hidden tests (passes that the strengthened suite rejects), and harnesses whose agent claims a pass the hidden suite does not confirm.</p>
      </>} />
    <Sec title="Oracle comparison · hidden vs strengthened" meta={`${int(o.n_eligible)} runs · ${int(o.n_excluded)} excluded`} el="oracle-comparison"
      actions={both ? <PinButton compact item={{ id: `figure:${dir}:oracle`, kind: 'figure', label: `Oracle comparison · ${dir}`, value: `hidden ${pct(ra)} → strengthened ${pct(rb)} (Δ ${pp(dd)}), κ ${fx(o.kappa)}`, spec: makeSpec('an', dir, 'judge'), dir }} /> : null}>
      {!both ? (
        <Empty title="Only one suite graded these runs." actions={[
          { label: 'Per-task hidden pass rates below', onClick: scrollTo('per-task-oracle') },
          { label: 'Outcomes with CI', spec: makeSpec('an', dir, 'outcomes', short(c.model), c.harness) },
          altOk && { label: `Open the oracle comparison in ${alt.name}`, spec: makeSpec('an', alt.name, 'judge'), primary: true },
        ]}>None of the {int(o.n_excluded)} runs in {dir} has both a hidden and a strengthened grade, so the two suites cannot be compared here{altOk ? <>; {alt.name} has {int(AO.data.n_eligible)} runs graded by both</> : null}.</Empty>
      ) : (
        <div className="rg-pnl"><div className="rg-pnl-b rg-an-oracle">
          <div className="rg-stats rg-an-j4">
            <div className="rg-stat"><span className="rg-lbl">hidden</span><span className="rg-fig md">{pct(ra)}</span></div>
            <div className="rg-stat"><span className="rg-lbl">strengthened</span><span className="rg-fig md">{pct(rb)}</span></div>
            <div className="rg-stat"><span className="rg-lbl">Δ</span><span className={cx('rg-fig md', dd < 0 && 'rg-red')}>{pp(dd)}</span></div>
            <div className="rg-stat"><span className="rg-lbl">agreement κ</span><span className="rg-fig md">{fx(o.kappa)}</span>{o.kappa_undefined_reason && <span className="rg-tiny rg-mute">{String(o.kappa_undefined_reason).replace(/_/g, ' ')}</span>}</div>
          </div>
          <div className="rg-an-o2">
            <table className="rg-tbl rg-an-tbl rg-an-2x2"><thead><tr><th><span className="sr-only">hidden</span></th><th className="rg-num">strong ✓</th><th className="rg-num">strong ×</th></tr></thead>
              <tbody><tr><td className="rg-mono rg-small">hidden ✓</td><td className="rg-num">{int(cells.both_true)}</td><td className="rg-num rg-red">{int(cells.only_a_true)}</td></tr>
                <tr><td className="rg-mono rg-small">hidden ×</td><td className="rg-num">{int(cells.only_b_true)}</td><td className="rg-num">{int(cells.both_false)}</td></tr></tbody></table>
            <p className="rg-an-p">{dd != null && dd < 0
              ? <>Where the two suites disagree, the score was measuring the suite rather than the fix: {(Math.abs(dd) * 100).toFixed(1)} points of the reported pass rate do not survive a stronger oracle — {int(cells.only_a_true)} runs the hidden suite passed and the strengthened one did not.</>
              : <>The strengthened suite does not lower the pass rate here ({int(cells.only_a_true)} hidden-only passes, {int(cells.only_b_true)} strengthened-only).</>}</p>
          </div>
        </div></div>
      )}
    </Sec>
    <Sec title="Integrity flags" meta={I.data ? <Tag tone={flags.length ? 'red' : undefined}>{flags.length} open</Tag> : null} el="integrity-flags">
      {I.error ? <ErrorState error={I.error} onRetry={I.reload} what={`/api/results/${dir}/integrity`} /> : !I.data ? <div className="rg-skel" style={{ height: 80 }} />
        : flags.length ? <div className="rg-col rg-an-flags">{flags.map((f) => <Note key={f.k} tone="red"><span className="rg-red" aria-hidden="true">×</span><div><div className="rg-lbl rg-an-fk">{f.k}</div>{f.body}</div></Note>)}</div>
          : <p className="rg-an-p">No contamination probe{both ? ', weak-test' : ''} or over-claiming flag in {dir}.</p>}
      {I.data && !both && <p className="rg-small rg-mute rg-an-mt">The weak-test check is skipped: it compares hidden passes with strengthened grades, and no run here has one.</p>}
    </Sec>
    {!I.error && <Sec title="Per task · hidden vs strengthened, with probes" meta={I.data ? `pass@1 · ${harness} harness · all models · ${plural(leak.length, 'task')}` : null} el="per-task-oracle leakage-evidence">
      {!I.data ? <div className="rg-skel" style={{ height: 120 }} /> : !leak.length ? (
        <Empty title="No per-task grades for this harness." actions={[{ label: 'Every attempt in the explorer', spec: makeSpec('tasks', dir, short(c.model), c.harness), primary: true }, { label: 'Outcomes with CI', spec: makeSpec('an', dir, 'outcomes', short(c.model), c.harness) }]}>
          The integrity endpoint returned no per-task rows for <span className="rg-mono">{harness}</span>.
        </Empty>
      ) : (
        <div className="rg-pnl"><div className="rg-tblwrap"><table className="rg-tbl rg-an-tbl rg-an-ptbl">
          <thead><tr><th>task</th><th>probe</th><th>hidden</th><th>strengthened</th><th className="rg-num">Δ</th><th className="rg-num rg-hide-sm">patch↔issue</th><th className="rg-num rg-hide-md">steps</th><th className="rg-num rg-hide-md">tokens</th></tr></thead>
          <tbody>{leak.map((x) => {
            const dl = !both || x.pass1_strong == null || x.pass1 == null ? null : x.pass1_strong - x.pass1
            return (
              <tr key={x.task}><td className="rg-mono rg-an-tcell" title={x.task}>{x.task}</td><td><ProbeTag probe={x.probe} /></td>
                <td><RateBar v={x.pass1} /></td><td>{both ? <RateBar v={x.pass1_strong} tone="dim" /> : <span className="rg-small rg-mute">not graded</span>}</td>
                <td className={cx('rg-num', dl != null && dl < -0.005 ? 'rg-red' : 'rg-mute')}>{pp(dl, 0)}</td>
                <td className={cx('rg-num rg-hide-sm', x.patch_issue_similarity > 0.2 && 'rg-red')}>{pct(x.patch_issue_similarity, 0)}</td><td className="rg-num rg-hide-md">{fx(x.steps, 1)}</td><td className="rg-num rg-hide-md">{int(x.tokens)}</td></tr>
            )
          })}</tbody></table></div></div>
      )}
    </Sec>}
    <LlmJudge canCompare={both} />
  </>)
}

/* ------------------------------------------------------------------ 6 · experiment */
function Fit({ spec, dir, row, c }) {
  const E = useExperiment(dir)
  const alt = useAlt(dir, 'fit')
  const [only, setOnly] = useTabState(spec, 'only', 'all')
  const [cm, setCm] = useTabState(spec, 'cm', 'all')
  if (E.error) return <ErrorState error={E.error} onRetry={E.reload} what={`/api/results/${dir}/experiment`} />
  if (!E.data) return <Loading label={`Reading /api/results/${dir}/experiment…`} />
  const X = E.data
  const A = X.a_levels || [], B = X.b_levels || []
  const F = X.fit || {}
  const T = (X.anova && X.anova.table) || []
  const contrasts = X.contrasts || []
  const cons = contrasts.filter((x) => (only === 'all' || !x.covers_zero) && (cm === 'all' || x.a === cm))
  const nsep = contrasts.filter((x) => !x.covers_zero).length
  const mx = symMax(contrasts.flatMap((x) => [...(x.ci95 || []), x.delta]))
  const ok = !!(F.model && F.harness && T.length)
  const cellsOf = (m, h) => (X.cells || []).find((k) => k.a === m && k.b === h)
  const S = shapeOf(row)
  const reason = F.reason ? REASON[F.reason] || String(F.reason).replace(/_/g, ' ') : null
  const lead = ok
    ? <>The <b>{F.leading_factor}</b> is what moves this study: model levels span {(F.model.range * 100).toFixed(1)} points against the harness's {(F.harness.range * 100).toFixed(1)}, and the interaction carries {pct(F.interaction_share)}{F.interaction_share < Math.max(F.model.share, F.harness.share) / 2 ? ' — the effect is largely additive' : ''}.</>
    : null
  return (<>
    <Head eyebrow="More analysis · experiment" title="Two-factor fit" el="fit-answer" lead={lead}
      sub={<>Outcome <span className="rg-mono">{X.outcome || '—'}</span> · {plural(A.length, 'model')} × {plural(B.length, 'harness', 'harnesses')} · tasks as blocks{X.anova ? <> · n={int(X.anova.n)}{(X.anova.dropped || []).length ? ` · ${X.anova.dropped.length} dropped for balance` : ' · balanced, nothing dropped'}</> : null}</>}
      about={<>
        <p>A two-way ANOVA of the outcome on model and harness, with tasks as blocks. Each factor's <b>share</b> is its sum of squares over the total; its <b>spread</b> is the range of its level means. The interaction share says how far the two factors fail to add up.</p>
        <p>Pairwise contrasts compare harnesses within one model, each with a 95% interval; with many contrasts, some exclude zero by chance alone (the expected count is shown). Descriptive, over these tasks only.</p>
      </>} />
    <div data-el="two-factor-fit">
      {!ok ? (
        <Empty title="No two-factor fit for this dataset." actions={[
          S.nm > 1 && { label: `Compare its ${S.nm} models in the family matrix`, spec: makeSpec('an', dir, 'family', short(c.model), c.harness), primary: true },
          S.nh > 1 && { label: `Compare its ${S.nh} harnesses in Comparison`, spec: makeSpec('an', dir, 'delta', short(c.model), c.harness), primary: S.nm < 2 },
          alt && { label: `Open the fit for ${alt.name} (${altShape(alt)})`, spec: makeSpec('an', alt.name, 'fit') },
        ]}>{shapeLine(row)}, so a model × harness fit has nothing to split{reason ? <> (the fit reports {reason})</> : null}. A fit needs at least two balanced models and two balanced harnesses; the cell grid below still shows every observed cell with its interval.</Empty>
      ) : (<>
        <div className="rg-stats rg-an-f3">
          <div className="rg-stat"><span className="rg-lbl">model spread</span><span className="rg-fig md">{(F.model.range * 100).toFixed(1)}<span className="u">pp</span></span><span className="rg-small rg-dim">share {pct(F.model.share)}</span></div>
          <div className="rg-stat"><span className="rg-lbl">harness spread</span><span className="rg-fig md">{(F.harness.range * 100).toFixed(1)}<span className="u">pp</span></span><span className="rg-small rg-dim">share {pct(F.harness.share)}</span></div>
          <div className="rg-stat"><span className="rg-lbl">interaction</span><span className="rg-fig md">{pct(F.interaction_share)}</span><span className="rg-small rg-dim">of total variance</span></div>
        </div>
        <Sec title="ANOVA · sums of squares" meta={`SS total ${fx(X.anova.ss_total, 1)}`}
          actions={<PinButton compact item={{ id: `figure:${dir}:fit`, kind: 'figure', label: `Two-factor fit · ${dir}`, value: `model ${pct(F.model.share)} · harness ${pct(F.harness.share)} · interaction ${pct(F.interaction_share)}`, spec: makeSpec('an', dir, 'fit'), dir }} />}>
          <div className="rg-pnl"><div className="rg-tblwrap"><table className="rg-tbl rg-an-tbl"><thead><tr><th>term</th><th className="rg-num">SS</th><th className="rg-num">df</th><th className="rg-num">MS</th><th className="rg-num">F</th><th className="rg-num">share</th></tr></thead>
            <tbody>{T.map((r) => <tr key={r.term}><td className="rg-mono">{r.term}</td><td className="rg-num">{fx(r.SS, 2)}</td><td className="rg-num">{r.df ?? '—'}</td><td className="rg-num">{fx(r.MS, 3)}</td><td className="rg-num">{r.F == null ? '—' : fx(r.F, 1)}</td><td className="rg-num">{pct(r.share)}</td></tr>)}</tbody></table></div></div>
        </Sec>
      </>)}
    </div>
    <Sec title="Cell grid · pass@1 with 95% CI" meta={`${(X.cells || []).length} cells · hidden suite`} el="cell-grid-ci">
      <div className="rg-pnl rg-an-mxp"><div className="rg-tblwrap"><table className="rg-an-mx"><thead><tr><th>model</th>{B.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{A.map((m) => <tr key={m}><td className="rh"><span className="rg-mono">{short(m)}</span></td>{B.map((h) => {
          const x = cellsOf(m, h)
          if (!x || !x.n) return <td key={h}><div className="rg-an-mc empty"><span className="v2">not run</span></div></td>
          return <td key={h}><Go spec={makeSpec('an', dir, 'outcomes', short(m), h)} className="rg-an-mc" title={`Outcomes for ${short(m)} under ${h}`}>
            <span className="v1">{pct(x.pass1)}</span><CiBar lo={x.ci95 && x.ci95[0]} hi={x.ci95 && x.ci95[1]} pt={x.pass1} />
            <span className="v2">[{x.ci95 ? (x.ci95[0] * 100).toFixed(0) : '—'}, {x.ci95 ? (x.ci95[1] * 100).toFixed(0) : '—'}] · n={x.n}</span></Go></td>
        })}</tr>)}</tbody></table></div></div>
    </Sec>
    {contrasts.length > 0 && (
      <Sec title="Pairwise contrasts · within model" meta={`${plural(contrasts.length, 'contrast')} · ${nsep} exclude 0`} el="pairwise-contrasts">
        <div className="rg-row rg-wrap rg-an-bar">
          <Seg label="Contrast filter" options={[['all', `all ${contrasts.length}`], ['sep', `separating ${nsep}`]]} value={only} onChange={setOnly} />
          {A.length > 1 && <select className="rg-selc" value={cm} onChange={(e) => setCm(e.target.value)} aria-label="Model"><option value="all">all models</option>{A.map((m) => <option key={m} value={m}>{short(m)}</option>)}</select>}
          <span className="rg-grow" />
          {X.multiplicity && <span className="rg-small rg-mute">{plural(X.multiplicity.n, 'test')} at 95%: ~{fx(X.multiplicity.expected_false, 2)} expected to exclude zero by chance alone.</span>}
        </div>
        <div className="rg-pnl"><div className="rg-tblwrap rg-an-cscroll"><table className="rg-tbl rg-an-tbl"><thead><tr><th>model</th><th>from → to</th><th className="rg-num">Δ</th><th className="rg-an-ciw">95% CI</th><th><span className="sr-only">separates</span></th></tr></thead>
          <tbody>{cons.map((x, i) => (
            <tr key={i}><td className="rg-mono rg-small">{short(x.a)}</td><td className="rg-mono rg-small">{x.from} → {x.to}</td><td className={cx('rg-num', !x.covers_zero && 'rg-acc')}>{pp(x.delta)}</td>
              <td><CiBar lo={x.ci95 && x.ci95[0]} hi={x.ci95 && x.ci95[1]} pt={x.delta} min={-mx} max={mx} zero={0} /><div className="rg-tiny rg-mute rg-mono">[{pp(x.ci95 && x.ci95[0])}, {pp(x.ci95 && x.ci95[1])}]</div></td>
              <td>{x.covers_zero ? <span className="rg-small rg-mute">○ covers 0</span> : <span className="rg-small rg-acc">◆ separates</span>}</td></tr>))}</tbody></table></div></div>
        {!cons.length && <Empty title="No contrast matches this filter." actions={[{ label: 'Show all contrasts', onClick: () => { setOnly('all'); setCm('all') }, primary: true }]}>None of the {contrasts.length} contrasts{cm !== 'all' ? ` for ${short(cm)}` : ''} excludes zero.</Empty>}
      </Sec>
    )}
  </>)
}

/* ------------------------------------------------------------------ 7 · report card */
function Report({ dir, row, c }) {
  const { toast } = useRig()
  const Rp = useReport(dir, c.harness)
  const X = useExperiment(dir)
  const O = useOracle(dir)
  const R = useRuns(dir)
  const I = useIntegrity(dir, c.harness)
  const alt = useAlt(dir, 'fit')
  if (Rp.error) return <ErrorState error={Rp.error} onRetry={Rp.reload} what={`/api/results/${dir}/report`} />
  if (!Rp.data || !R.data) return <Loading label={`Reading /api/results/${dir}/report…`} />
  const card = Rp.data.card
  const S = shapeOf(row)
  if (!card || !card.outcome) return (
    <Empty title="No report card for this condition." actions={[
      S.nh > 1 && { label: 'Pick another harness in the family matrix', spec: makeSpec('an', dir, 'family', short(c.model), c.harness), primary: true },
      { label: 'Outcomes with CI', spec: makeSpec('an', dir, 'outcomes', short(c.model), c.harness), primary: S.nh < 2 },
      alt && { label: `Open the report for ${alt.name}`, spec: makeSpec('an', alt.name, 'report') },
    ]}>The report endpoint answered {Rp.data.error ? <>“<span className="rg-mono">{Rp.data.error}</span>”</> : 'without a card'} for <span className="rg-mono">{c.harness}</span> in {dir}.</Empty>
  )
  const E = X.data || {}
  const F = E.fit && E.fit.model ? E.fit : null
  const o = O.data
  const hasStrongDs = R.data.some((r) => r.str != null)
  const od = o && o.n_eligible && o.rate_a && o.rate_b && o.rate_a.rate != null && o.rate_b.rate != null ? o.rate_b.rate - o.rate_a.rate : null
  const oc = card.outcome
  const h = card.cell && card.cell.harness && card.cell.harness.id
  const n = card.cell && card.cell.runs
  const hr = R.data.filter((r) => r.harness === h)
  const own = hr.filter((r) => r.model === card.cell.model).length
  const nModels = new Set(hr.map((r) => r.model)).size
  const pooled = n != null && n > own && n === hr.length
  const scope = pooled ? `all ${nModels} models · ${h} (pooled)` : `${short(card.cell.model)} · ${h}`
  const hasStrong = hr.some((r) => r.str != null)
  const bnd = R.data.reduce((a, r) => a + (r.bnd || 0), 0)
  const best = [...(E.cells || [])].filter((x) => x.n).sort((a, b) => b.pass1 - a.pass1 || b.n - a.n)[0]
  const leak = ((I.data && I.data.leakage) || []).find((x) => x.probe === 'solution_leak')
  const nRuns = (E.anova && E.anova.n) || R.data.length
  const ladder = [['pass@1', oc.pass1, 'point estimate'], ...Object.entries(oc.pass_at_k || {}).map(([k, v]) => ['pass@' + k, v, 'with retries']), ...Object.entries(oc.pass_pow_k || {}).map(([k, v]) => ['pass^' + k, v, 'every attempt'])]
  const note = pooled ? `The card's header names ${card.cell.model}, but its outcome pools every model's ${h} runs (n = ${int(n)}; ${short(card.cell.model)} alone has ${int(own)}). Labelled here as "${scope}".` : null
  const exportMd = () => {
    const md = (note ? `> **Note (HarnessLab):** ${note}\n\n` : '') + (Rp.data.markdown || '')
    if (!download(`report-card-${dir}-${h}.md`, md, 'text/markdown')) toast('Download is not available in this browser.')
  }
  const exportJson = () => {
    const body = note ? { rig_note: note, rig_scope: scope, ...card } : card
    if (!download(`report-card-${dir}-${h}.json`, JSON.stringify(body, null, 2), 'application/json')) toast('Download is not available in this browser.')
  }
  const headline = F
    ? `Across ${plural((E.a_levels || []).length, 'model')} and ${plural((E.b_levels || []).length, 'harness', 'harnesses')}, the ${E.fit.leading_factor} is what moves this study: model levels span ${(F.model.range * 100).toFixed(1)} points against the harness's ${(F.harness.range * 100).toFixed(1)}.`
    : `pass@1 ${pct(oc.pass1)} ${ciText(oc.ci95)} over ${int(n)} ${h} runs — ${scope}.`
  return (
    <article data-el="report-card" className="rg-an-report">
      <div className="rg-row rg-wrap rg-an-bar"><span className="rg-lbl">Report card · generated from disk · {card.generated_at || '—'}</span><span className="rg-grow" />
        <PinButton item={{ id: `figure:${dir}:report:${h}`, kind: 'figure', label: `Report card · ${dir} · ${scope}`, value: `pass@1 ${pct(oc.pass1)} ${ciText(oc.ci95)} · n=${int(n)} · ${scope}`, spec: makeSpec('an', dir, 'report', short(c.model), c.harness), dir, harness: h }} />
        <Btn onClick={exportMd} disabled={!Rp.data.markdown} why="The report endpoint returned no Markdown for this card; export .json instead.">Export .md</Btn>
        <button type="button" className="rg-btn" onClick={exportJson}>Export .json</button></div>
      <h1 className="rg-an-rh1">{headline}</h1>
      <div className="rg-stats rg-an-r5">
        <div className="rg-stat"><span className="rg-lbl">runs</span><span className="rg-fig sm">{int(nRuns)}</span></div>
        <div className="rg-stat"><span className="rg-lbl">best cell</span><span className="rg-fig sm">{best ? pct(best.pass1) : '—'}</span>{best && <span className="rg-tiny rg-mute rg-mono">{short(best.a)} × {best.b} · n={best.n}</span>}</div>
        <div className="rg-stat"><span className="rg-lbl">model spread</span><span className="rg-fig sm">{F ? (F.model.range * 100).toFixed(1) + 'pp' : '—'}</span>{!F && <span className="rg-tiny rg-mute">no two-factor fit</span>}</div>
        <div className="rg-stat"><span className="rg-lbl">oracle Δ</span><span className={cx('rg-fig sm', od < 0 && 'rg-red')}>{pp(od)}</span>{od == null && !hasStrongDs && <span className="rg-tiny rg-mute">no strengthened grades</span>}</div>
        <div className="rg-stat"><span className="rg-lbl">boundary events</span><span className="rg-fig sm">{int(bnd)}</span></div>
      </div>
      <Sec title="Claims and caveats" el="report-claims">
        <div className="rg-col rg-an-claims">
          {F && <Note><div><div className="rg-lbl rg-an-fk">Claim · I</div>The {E.fit.leading_factor} is the larger variable here: the model carries {pct(F.model.share)} of the variance against the harness's {pct(F.harness.share)}, and its levels span {(F.model.range * 100).toFixed(1)}pp against {(F.harness.range * 100).toFixed(1)}pp.<div className="rg-tiny rg-mute rg-mono rg-an-mt">two-factor fit · {(E.cells || []).length} cells · tasks as blocks</div></div></Note>}
          {F && <Note><div><div className="rg-lbl rg-an-fk">Claim · II</div>{E.fit.interaction_share < F.model.share / 2 ? 'The effect is largely additive' : 'Model and harness interact'}: the interaction term carries {pct(E.fit.interaction_share)} of the variance, against the model's {pct(F.model.share)}.<div className="rg-tiny rg-mute rg-mono rg-an-mt">two-factor fit · interaction term</div></div></Note>}
          {!F && (
            <Empty title="No two-factor claims." actions={[
              S.nm > 1 && { label: `Compare its ${S.nm} models in the family matrix`, spec: makeSpec('an', dir, 'family', short(c.model), c.harness), primary: true },
              { label: 'Outcomes with CI per model', spec: makeSpec('an', dir, 'outcomes', short(c.model), c.harness), primary: S.nm < 2 },
              alt && { label: `Open the report for ${alt.name} (${altShape(alt)})`, spec: makeSpec('an', alt.name, 'report') },
            ]}>{shapeLine(row)}, so there is no model-against-harness fit and nothing is claimed in its place. The card's one estimate is the pass@1 above, labelled with its scope.</Empty>
          )}
          {od != null && od < 0 && <Note tone="red"><div><div className="rg-lbl rg-an-fk">Caveat · III</div>{(Math.abs(od) * 100).toFixed(1)} points of the reported pass rate do not survive the strengthened oracle, so part of what was scored was the suite rather than the fix.<div className="rg-tiny rg-mute rg-mono rg-an-mt">hidden vs strengthened · κ {fx(o.kappa)} · {int(o.n_eligible)} runs</div></div></Note>}
          {(leak || bnd > 0) && <Note tone="red"><div><div className="rg-lbl rg-an-fk">Caveat · IV</div>{leak ? <><span className="rg-mono">{leak.task}</span> is the contamination probe and scores {pct(leak.pass1)}; </> : null}{int(bnd)} boundary events were recorded inside the scratch copy.<div className="rg-tiny rg-mute rg-mono rg-an-mt">boundary_event · {int(bnd)} spans across {int(R.data.length)} runs</div></div></Note>}
        </div>
      </Sec>
      <Sec title="pass@k ladder · the claim cell" meta={`95% CI ${ciText(oc.ci95)} · ${int(n)} runs · ${plural((card.cell.tasks || []).length, 'task')}`}>
        <div className="rg-an-lad">
          {ladder.map(([k, v, s]) => <div key={k}><div className="rg-lbl">{k}</div><div className="rg-fig sm">{pct(v)}</div><div className="rg-tiny rg-mute">{s}</div></div>)}
          <div><div className="rg-lbl">flip rate</div><div className="rg-fig sm">{pct(oc.flip_rate, 0)}</div><div className="rg-tiny rg-mute">tasks mixed</div></div>
          <div><div className="rg-lbl">strengthened</div><div className="rg-fig sm">{hasStrong ? pct(oc.pass1_strong) : '—'}</div>
            {hasStrong ? <div className="rg-tiny rg-red-t">Δ {pp(oc.pass1_strong == null ? null : oc.pass1_strong - oc.pass1)}</div> : <div className="rg-tiny rg-mute">not graded</div>}</div>
        </div>
        <p className="rg-small rg-mute rg-an-mt">Scope: {scope}. Oracle: {card.cell.oracle || 'hidden suite'}.</p>
      </Sec>
      <Sec title="What this card does not cover">
        <ul className="rg-an-ul">{(card.missing || []).map((x, i) => <li key={i}>{x}</li>)}</ul>
      </Sec>
      <Sec title="Provenance" el="report-provenance">
        <dl className="rg-kv rg-an-kv"><dt>source</dt><dd>{card.source || `data/runs/${dir}`}</dd><dt>runs · cells</dt><dd>{int(nRuns)} runs · {(E.cells || []).length} cells</dd>
          <dt>oracle</dt><dd>{card.cell.oracle || 'hidden'}{hasStrongDs ? ' + strengthened' : ' · no strengthened grades'}</dd><dt>boundary</dt><dd>boundary_event · {int(bnd)} spans</dd><dt>generated</dt><dd>{card.generated_at || '—'}</dd>
          <dt>card cell</dt><dd data-el="report-cell-label">{scope} · {int(n)} runs</dd></dl>
        {pooled && <p className="rg-small rg-an-mt" data-el="report-pooled-note"><Tag tone="amb">note</Tag> <span className="rg-dim">The card's header names <span className="rg-mono">{card.cell.model}</span>, but its {int(n)} runs are every model under <span className="rg-mono">{h}</span> ({short(card.cell.model)} alone has {int(own)}). Shown as pooled; the exports carry this note.</span></p>}
      </Sec>
    </article>
  )
}

/* ------------------------------------------------------------------ 8 · run setup */
function Setup({ spec, dir, row, c }) {
  const ov = useOverview()
  const Mo = useModels()
  const J = useJobs()
  const R = useRuns(dir)
  const [selM, setSelM] = useTabState(spec, 'm', null)
  const [selH, setSelH] = useTabState(spec, 'h', null)
  const [selT, setSelT] = useTabState(spec, 't', null)
  const [rep, setRep] = useTabState(spec, 'rep', 3)
  const [par, setPar] = useTabState(spec, 'par', 3)
  const [out, setOut] = useTabState(spec, 'out', dir)
  const [msg, setMsg] = useState(null)
  if (Mo.error) return <ErrorState error={Mo.error} onRetry={Mo.reload} what="/api/models" />
  if (!ov.data || !Mo.data) return <Loading label="Reading /api/models…" />
  const list = Mo.data.models || []
  const observed = Mo.data.observed || {}
  const ids = [...new Set([...list.map((m) => m.id), ...Object.keys(observed)])]
  const H = ov.data.harnesses || []
  const Tk = ov.data.tasks || []
  const mS = selM || (ids.includes(c.model) ? [c.model] : ids.slice(0, 1))
  const hS = selH || (H.some((h) => h.id === c.harness) ? [c.harness] : H.slice(0, 1).map((h) => h.id))
  const tS = selT || Tk.map((t) => t.id).filter((t) => row.tasks.includes(t))
  const toggle = (arr, set, v) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v])
  const n = mS.length * hS.length * tS.length * rep
  let cost = 0, costKnown = true, wall = 0, wallKnown = true
  for (const m of mS) {
    const per = hS.length * tS.length * rep
    const o = observed[m]
    if (o && Number.isFinite(o.cost_per_run)) cost += o.cost_per_run * per; else costKnown = false
    const l = (R.data || []).filter((r) => r.model === m && r.wall != null)
    if (l.length) wall += (l.reduce((a, r) => a + r.wall, 0) / l.length) * per; else wallKnown = false
  }
  const perArm = tS.length * rep
  const mde = perArm > 0 ? 2.8 * Math.sqrt(0.5 / perArm) : null
  const price = (m) => { const x = list.find((y) => y.id === m); return x && Number.isFinite(x.price_in) ? `$${x.price_in} / $${x.price_out}` : m.startsWith('mock') ? 'free · offline' : '— unpriced' }
  const active = (J.data && J.data.active) || []
  const missing = [!mS.length && 'a model', !hS.length && 'a harness', !tS.length && 'a task'].filter(Boolean)
  const why = missing.length ? `Select ${missing.join(', ')} to launch.` : !out ? 'Name an output directory to launch.' : null
  const can = !IS_STATIC && n > 0 && !!out
  const outExists = !!(ov.data.results || []).find((r) => r.name === out)
  const launch = async () => {
    setMsg(null)
    try {
      const job = await api('/jobs', { method: 'POST', body: { out, models: mS, harnesses: hS, tasks: tS, repeats: rep, parallel: par, sentinel_ab: false } })
      setMsg({ ok: true, text: `Launched ${job && job.id ? job.id : 'the job'}: ${int(n)} runs → data/runs/${out}.`, out })
      invalidate(paths.jobs()); invalidate(paths.status())
    } catch (e) { setMsg({ ok: false, text: e.message }) }
  }
  const lead = n > 0 ? <>This cell is <b>{int(n)} runs</b> ({mS.length} × {hS.length} × {tS.length} × {rep}) into <span className="rg-mono">data/runs/{out || '…'}</span>, {costKnown ? `≈ ${usd(cost, 2)}` : cost ? `≥ ${usd(cost, 2)} plus unpriced models` : 'cost not yet observed'}; it can detect a difference of about {mde == null ? '—' : `${(mde * 100).toFixed(0)} points`} per arm.</> : <>Select at least one model, harness and task to compose a cell.</>
  return (<>
    <Head eyebrow="More analysis · compose a cell" title="Run setup" el="setup-answer" lead={lead}
      about={<>
        <p>A cell is model × harness × tasks × repeats. Launching sends one request (<span className="rg-mono">POST /api/jobs</span>) and always asks for a second click.</p>
        <p>Cost comes from each model's observed $/run (<span className="rg-mono">/api/models · observed</span>); time from the mean recorded wall time per run in {dir}; the detectable effect is the smallest difference decidable at 80% power per arm. None of it is a quote.</p>
      </>} />
    {active.length > 0 && <Note tone="acc" el="jobs-in-flight"><span className="rg-pulse" /><div><b>{plural(active.length, 'job')} in flight.</b> {active.map((j) => `${j.id} → ${j.out} (${j.done ?? 0}/${j.total ?? '?'})`).join(' · ')}</div></Note>}
    <div data-el="run-setup" className="rg-an-setup">
      <div className="rg-col rg-an-setmain">
      <Panel label="Models" meta={`${mS.length} selected · price per 1M tokens in / out`}>
        <div className="rg-col rg-an-chks">{ids.map((m) => (
          <label key={m} className="rg-row rg-an-chk"><input type="checkbox" checked={mS.includes(m)} onChange={() => toggle(mS, setSelM, m)} /><span className="rg-mono rg-grow rg-ell" title={m}>{short(m)}</span><span className="rg-mono rg-tiny rg-mute">{price(m)}</span></label>))}</div>
      </Panel>
      <Panel label="Harnesses · the variable" meta={`${hS.length} selected`}>
        <div className="rg-col rg-an-chks">{H.map((h) => (
          <label key={h.id} className="rg-row rg-an-chk top"><input type="checkbox" checked={hS.includes(h.id)} onChange={() => toggle(hS, setSelH, h.id)} /><span className="rg-grow"><span className="rg-mono">{h.id}</span>{h.notes && <span className="rg-tiny rg-mute rg-an-blk">{h.notes}</span>}</span></label>))}</div>
      </Panel>
      </div>
      <div className="rg-col rg-an-setside">
        <Panel label="Tasks · repeats" meta={`${tS.length} of ${Tk.length}`}>
          <div className="rg-row rg-wrap rg-an-tchips">{Tk.map((t) => (
            <label key={t.id} className={cx('rg-tag rg-an-tchip', tS.includes(t.id) ? 'acc' : 'dash')} title={t.title}><input type="checkbox" className="sr-only" checked={tS.includes(t.id)} onChange={() => toggle(tS, setSelT, t.id)} />{t.id.split('_')[0]}</label>))}</div>
          <div className="rg-grid rg-an-knobs">
            <label className="rg-fld">Repeats<input className="rg-inp" type="number" min="1" max="20" value={rep} onChange={(e) => setRep(Math.max(1, Math.min(20, +e.target.value || 1)))} /></label>
            <label className="rg-fld">Parallelism<input className="rg-inp" type="number" min="1" max="12" value={par} onChange={(e) => setPar(Math.max(1, Math.min(12, +e.target.value || 1)))} /></label>
            <label className="rg-fld rg-an-span2">Output dir · data/runs/<input className="rg-inp" value={out} placeholder="e.g. my_sweep" onChange={(e) => setOut(e.target.value.replace(/[^a-zA-Z0-9_-]/g, ''))} aria-label="Output directory" /></label>
          </div>
        </Panel>
        <Panel label="Estimate" el="run-estimate">
          <dl className="rg-kv"><dt>runs</dt><dd>{int(n)} <span className="rg-mute">= {mS.length}×{hS.length}×{tS.length}×{rep}</span></dd>
            <dt>cost</dt><dd>{!n ? '—' : costKnown ? '≈ ' + usd(cost, 2) : cost ? `≥ ${usd(cost, 2)} + unpriced` : '— not observed'}</dd>
            <dt>wall time</dt><dd>{n && wall ? `≈ ${Math.max(1, Math.round(wall / par / 60000))} min${wallKnown ? '' : ' + unmeasured'}` : '—'} <span className="rg-mute">@ {par} parallel</span></dd>
            <dt>detectable</dt><dd>{mde == null ? '—' : `${(mde * 100).toFixed(1)}pp`} <span className="rg-mute">per arm n={perArm}</span></dd></dl>
          {IS_STATIC ? (
            <Note el="read-only-export-state"><div>This is a static export, so runs cannot launch here. Start the live app to launch this cell: <CopyCmd cmd={LIVE_CMD} /></div></Note>
          ) : (
            <div className="rg-an-launch">
              <ConfirmAction el="launch-runs" label={`Launch ${int(n)} runs`} confirmLabel={`Start ${int(n)} runs`} disabled={!can} why={why}
                detail={`POST /api/jobs → data/runs/${out}. ${costKnown ? `≈ ${usd(cost, 2)}` : 'cost partly unknown'}.`} onConfirm={launch} />
            </div>
          )}
          {msg && (msg.ok
            ? <Note tone="acc" el="launch-result"><div>{msg.text}<NextSteps actions={[
              { label: 'Watch the run on the canvas', spec: 'canvas', primary: true },
              outExists && { label: `Open ${msg.out}`, spec: makeSpec('ds', msg.out) },
            ]} /></div></Note>
            : <Note tone="red" el="error-state"><div><b>The launch failed.</b> {msg.text}<NextSteps actions={[{ label: 'Retry', onClick: launch }, { label: 'Back to Outcomes', spec: makeSpec('an', dir, 'outcomes', short(c.model), c.harness) }]} /></div></Note>)}
        </Panel>
      </div>
    </div>
  </>)
}

const BODY = { family: Family, outcomes: Outcomes, delta: Delta, judge: Judge, fit: Fit, report: Report, setup: Setup }

/* ------------------------------------------------------------------ tasks: trajectory explorer */
const PAGE = 40
function TaskExplorer({ spec, args }) {
  const [dir, m, h] = args
  const { oracle, conditionFor } = useRig()
  const ds = useDataset(dir)
  const R = useRuns(dir)
  const T = useTasks()
  const [q, setQ] = useTabState(spec, 'q', '')
  const [qf, setQf] = useTabState(spec, 'qf', 'all')
  const [pf, setPf] = useTabState(spec, 'pf', 'all')
  const [mf, setMf] = useTabState(spec, 'mf', 'all')
  const [so, setSo] = useTabState(spec, 'so', 'priority')
  const [pg, setPg] = useTabState(spec, 'pg', 0)
  const row = ds.data
  const mem = conditionFor(dir)
  const c = row ? resolveCondition(row, m || mem.model, h || mem.harness) : null
  const meta = useMemo(() => Object.fromEntries(((Array.isArray(T.data) && T.data) || []).map((t) => [t.id, t])), [T.data])
  const d = useMemo(() => {
    if (!R.data || !c) return null
    const list = conditionRuns(R.data, c.model, c.harness)
    const tasks = [...new Set(list.map((r) => r.task))].sort()
    const rows = tasks.map((t) => {
      const l = list.filter((r) => r.task === t).sort((a, b) => (a.rep ?? 0) - (b.rep ?? 0))
      const o = tally(l, oracle)
      const modes = new Set(l.map((r) => failureModeOf(r, oracle)).filter(Boolean))
      return { t, l, o, tk: tokStats(l), mixed: o.p > 0 && o.f > 0, modes }
    })
    const modeN = {}
    for (const x of rows) for (const r of x.l) { const k = failureModeOf(r, oracle); if (k) modeN[k] = (modeN[k] || 0) + 1 }
    return { list, rows, modeN }
  }, [R.data, c && c.model, c && c.harness, oracle]) // eslint-disable-line react-hooks/exhaustive-deps
  if (ds.error) return <ErrorState error={ds.error} onRetry={ds.reload} what="The dataset index" />
  if (ds.missing) return <NotFound spec={spec} detail={`There is no dataset “${dir}” in data/runs on this machine.`} />
  if (R.error) return <ErrorState error={R.error} onRetry={R.reload} what={`The run index of ${dir}`} />
  if (!row || !d) return <Loading label={`Reading /api/results/${dir}/runs…`} />
  const cs = [dir, short(c.model), c.harness]
  const probeOf = (t) => (meta[t] ? meta[t].probe : null)
  const nMixed = d.rows.filter((x) => x.mixed).length, nFail = d.rows.filter((x) => x.o.f).length
  const nClean = d.rows.filter((x) => x.o.p > 0 && !x.o.f && !x.o.u).length
  const nNever = d.rows.filter((x) => !x.o.p && x.o.f).length
  const nUnk = d.rows.filter((x) => x.o.u).length
  const ql = q.trim().toLowerCase()
  let rows = d.rows.filter((x) => (qf === 'all' || (qf === 'mixed' ? x.mixed : x.o.f > 0))
    && (pf === 'all' || (pf === 'probe' ? probeOf(x.t) && probeOf(x.t) !== 'none' : !probeOf(x.t) || probeOf(x.t) === 'none'))
    && (mf === 'all' || x.modes.has(mf))
    && (!ql || (x.t + ' ' + (meta[x.t] ? meta[x.t].title : '')).toLowerCase().includes(ql)))
  rows = [...rows].sort(so === 'name' ? (a, b) => a.t.localeCompare(b.t) : so === 'fail' ? (a, b) => b.o.f - a.o.f || a.t.localeCompare(b.t) : (a, b) => (b.mixed - a.mixed) || (b.o.f - a.o.f) || a.t.localeCompare(b.t))
  const pages = Math.max(1, Math.ceil(rows.length / PAGE))
  const p = Math.min(pg, pages - 1)
  const shown = rows.slice(p * PAGE, p * PAGE + PAGE)
  const maxN = Math.max(1, ...d.rows.map((x) => x.l.length))
  const modeOpts = Object.entries(d.modeN).sort((a, b) => b[1] - a[1])
  const reset = (fn) => (v) => { fn(v); setPg(0) }
  const clear = () => { setQ(''); setQf('all'); setPf('all'); setMf('all'); setPg(0) }
  const fsum = failureSummary(d.list, oracle)
  const nT = d.rows.length
  const lead = !d.list.length ? null : <>{nClean} of {nT} tasks passed every attempt{nMixed ? `, ${nMixed} flip between pass and fail` : ''}{nNever ? `, ${nNever} never passed` : ''}{nUnk ? `, ${nUnk} have ungraded attempts` : ''} — {oracle}-suite grades{fsum ? `; ${fsum}` : ''}.</>
  return (
    <div className="rg-dpad rg-an-xp">
      <AskingSentence dir={dir} model={c.model} harness={c.harness} />
      <Head eyebrow="Trajectory explorer · task × attempts" title={`${plural(nT, 'task')}, ${int(d.list.length)} attempts`} el="tasks-answer" lead={lead}
        actions={<div className="rg-row rg-wrap"><PinButton item={{ id: `cell:${dir}:${c.model}:${c.harness}`, kind: 'cell', label: `${short(c.model)} · ${c.harness} · ${dir}`, value: `${pct(tally(d.list, oracle).rate)} · ${tally(d.list, oracle).p}/${tally(d.list, oracle).known} known (${oracle})`, spec: makeSpec('ds', ...cs), dir, model: c.model, harness: c.harness }} />
          <Go spec={makeSpec('an', dir, 'outcomes', ...cs.slice(1))} className="rg-btn">Outcomes with CI</Go></div>}
        about={d.list.length ? <>
          <p>One row per task for <span className="rg-mono">{short(c.model)}</span> under <span className="rg-mono">{c.harness}</span>; each square is one attempt, graded by the {oracle} suite. An unknown grade (?) is never counted as a failure.</p>
          <p data-el="run-numbering-note">Run 01–{String(maxN).padStart(2, '0')} are this condition's attempts ordered by <span className="rg-mono">repeat_index</span>. Run numbers are scoped to the condition: “Run 03” under another model is a different recording.</p>
          <p>“Priority” sorts tasks with mixed outcomes first, then by failures. ◆ marks a task that carries a probe (a planted leak, injection or temptation).</p>
        </> : null} />
      {!d.list.length ? (
        <Empty title="No runs in this condition." actions={[
          { label: 'Pick an observed family in the matrix', spec: makeSpec('an', dir, 'family', short(c.model), c.harness), primary: true },
          { label: `Every run of ${dir}`, spec: makeSpec('field', dir) },
        ]}>{short(c.model)} was not run under {c.harness} in {dir}. The family matrix shows which combinations were.</Empty>
      ) : (<>
        <div className="rg-row rg-wrap rg-an-bar rg-an-xbar">
          <div data-el="task-quick-filters"><Seg label="Quick filter" options={[['all', `All ${nT}`], ['mixed', `Mixed outcomes ${nMixed}`], ['fail', `With failures ${nFail}`]]} value={qf} onChange={reset(setQf)} /></div>
          <div className="rg-an-srch"><SearchBox value={q} onChange={reset(setQ)} placeholder="Search tasks" el="task-search" /></div>
          <label data-el="task-filter-select"><span className="sr-only">Probe filter</span><select className="rg-selc" value={pf} onChange={(e) => reset(setPf)(e.target.value)} aria-label="Probe filter">
            <option value="all">all tasks</option><option value="probe">probe tasks only</option><option value="plain">no probe</option></select></label>
          <label data-el="task-failure-filter"><span className="sr-only">Failure mode</span><select className="rg-selc" value={mf} onChange={(e) => reset(setMf)(e.target.value)} aria-label="Failure mode filter">
            <option value="all">any outcome</option>{modeOpts.map(([k, n]) => <option key={k} value={k}>fails: {k.replace(/_no_patch$/, ' · no patch').replace(/_/g, ' ')} ({n})</option>)}</select></label>
          <label data-el="task-sort"><span className="sr-only">Sort</span><select className="rg-selc" value={so} onChange={(e) => setSo(e.target.value)} aria-label="Sort tasks">
            <option value="priority">sort: priority</option><option value="name">sort: name</option><option value="fail">sort: most failures</option></select></label>
        </div>
        <div className="rg-row rg-wrap rg-small rg-an-legend" data-el="task-legend">
          <span className="rg-row rg-an-lg"><Verdict v={true} /> pass</span><span className="rg-row rg-an-lg"><Verdict v={false} /> fail</span>
          <span className="rg-row rg-an-lg"><Verdict v={null} /> unknown (never counted as fail)</span><Tag>{oracle}-test grades</Tag><span><span className="rg-amb">◆</span> probe task</span>
        </div>
        <div className="rg-pnl" data-el="task-outcome-table">
          {rows.length ? (<>
            <div className="rg-an-trow th" aria-hidden="true"><div className="t-name">task</div><div className="t-rib">attempts in order</div><div className="t-cnt rg-num">pass / fail</div><div className="t-bar">outcome</div><div className="t-tok rg-num">mean tokens</div><div className="t-probe">probe</div></div>
            {shown.map((x) => {
              const pr = probeOf(x.t)
              return (
                <div key={x.t} className="rg-an-trow">
                  <div className="t-name"><div className="rg-row rg-an-tn"><Go spec={makeSpec('task', ...cs, x.t)} className="rg-tlink"><b>{x.t}</b></Go>{x.mixed && <Tag tone="amb">mixed</Tag>}{pr && pr !== 'none' && <span className="rg-amb t-pm" title={`probe: ${pr}`}>◆</span>}</div>
                    {meta[x.t] && meta[x.t].title && <div className="rg-tiny rg-mute rg-ell">{meta[x.t].title}</div>}
                    <ModeChips list={x.l} oracle={oracle} dir={dir} /></div>
                  <div className="t-rib"><Ribbon list={x.l} oracle={oracle} specOf={(i) => makeSpec('task', ...cs, x.t, i)} />
                    {x.l.length <= 12 ? <div className="rg-an-rib ribn" aria-hidden="true">{x.l.map((_, i) => <span key={i}>{String(i + 1).padStart(2, '0')}</span>)}</div> : <div className="rg-tiny rg-mute rg-mono">{x.l.length} attempts</div>}</div>
                  <div className="t-cnt rg-num"><span className="rg-sky">{x.o.p}</span> / <span className="rg-red-t">{x.o.f}</span>{x.o.u ? <> / <span className="rg-mute">{x.o.u}?</span></> : null}</div>
                  <div className="t-bar"><OutcomeBar p={x.o.p} f={x.o.f} u={x.o.u} /></div>
                  <div className="t-tok rg-num">{x.tk.mean == null ? '—' : int(x.tk.mean)}{x.tk.rec < x.tk.n && <div className="rg-tiny rg-mute">{x.tk.rec}/{x.tk.n} recorded</div>}</div>
                  <div className="t-probe">{meta[x.t] ? <ProbeTag probe={pr} /> : <span className="rg-small rg-mute">no task card</span>}</div>
                </div>
              )
            })}
            {rows.length > PAGE && (
              <div className="rg-row rg-wrap rg-an-pager" data-el="task-pages"><span className="rg-small rg-mono rg-mute">{p * PAGE + 1}–{Math.min(rows.length, p * PAGE + PAGE)} of {rows.length} tasks</span><span className="rg-grow" />
                {p > 0 && <button type="button" className="rg-btn" onClick={() => setPg(p - 1)}>← Previous {PAGE}</button>}
                {p < pages - 1 && <button type="button" className="rg-btn" onClick={() => setPg(p + 1)}>Next {Math.min(PAGE, rows.length - (p + 1) * PAGE)} →</button>}</div>
            )}
          </>) : (
            <div className="rg-pnl-b"><Empty title="No task matches these filters." actions={[{ label: 'Clear filters', onClick: clear, primary: true }]}>
              {qf !== 'all' ? `No task in this condition has ${qf === 'mixed' ? 'mixed outcomes' : 'a recorded failure'}${q || pf !== 'all' || mf !== 'all' ? ' under the other filters' : ''}.` : 'The search and filters exclude every task.'}
            </Empty></div>
          )}
        </div>
      </>)}
    </div>
  )
}

function TasksActions({ args }) {
  const [dir, m, h] = args
  const { conditionFor } = useRig()
  const mem = conditionFor(dir)
  const cs = [dir, m || (mem.model && short(mem.model)), h || mem.harness]
  return (
    <div className="rg-row">
      <OracleActions />
      <div className="rg-seg rg-hide-sm" data-el="view-switch" role="group" aria-label="View">
        <Go spec={makeSpec('ds', ...cs)} replace className="rg-an-segb">Overview</Go>
        <button type="button" className="on" aria-pressed="true">Trajectories</button>
        <Go spec={makeSpec('an', dir, 'family', ...cs.slice(1))} replace className="rg-an-segb">Analysis</Go>
      </div>
    </div>
  )
}

export const views = {
  an: {
    tag: 'an',
    title: (a) => `${AN_LABEL[a[1] || 'family'] || a[1] || 'analysis'} · ${a[0] || ''}`,
    ctx: (a) => ({ dir: a[0], model: a[2] || null, harness: a[3] || null }),
    crumbs: (a) => [[a[0], makeSpec('ds', a[0])], ['analysis', a[1] && a[1] !== 'family' ? makeSpec('an', a[0], 'family', a[2], a[3]) : null], [AN_LABEL[a[1] || 'family'] || a[1] || 'analysis']],
    retarget: (a, c) => makeSpec('an', c.dir, a[1] || 'family', short(c.model), c.harness),
    outsideGuided: (a) => a[1] === 'setup',
    palette: (env) => env.datasets.flatMap((d) => AN_VIEWS.filter(([v]) => v !== 'traj').map(([v, , l]) => ({ group: 'view', title: l, detail: d.name, spec: makeSpec('an', d.name, v), keywords: PAL_KEYWORDS[v] || v }))),
    render: AnalysisDoc,
    Actions: OracleActions,
  },
  tasks: {
    tag: 'traj',
    title: (a) => `tasks · ${a[1] || a[0]}${a[2] ? ' · ' + a[2] : ''}`,
    ctx: (a) => ({ dir: a[0], model: a[1] || null, harness: a[2] || null }),
    crumbs: (a) => [[a[0], makeSpec('ds', a[0], a[1], a[2])], ['trajectories']],
    retarget: (a, c) => makeSpec('tasks', c.dir, short(c.model), c.harness),
    palette: (env) => env.datasets.map((d) => ({ group: 'view', title: 'Trajectory explorer', detail: d.name, spec: makeSpec('tasks', d.name), keywords: 'tasks attempts ribbon trajectories failures' })),
    render: TaskExplorer,
    Actions: TasksActions,
  },
}
