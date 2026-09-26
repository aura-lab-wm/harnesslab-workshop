/* ====================================================================================
   Rig · views/questions.jsx — `q:<dir>` (the question stack), `q:<dir>:<qid>` (one answer:
   sentence + number + figure), `q:<dir>:<qid>:figure` (same, scrolled to the figure) and
   `q:<dir>:<qid>:runs` (the runs behind the answer). Owner: Phase 1.
   Every run opened from here carries the evidence chain (Answer → Figure → Runs → Run → Event).
   Exports useAnswers(dir) and <QuestionStack dir/> for the dataset tab.
   Round 3: an unanswered question is never a dead end. It offers the datasets that CAN answer
   it (probed live, only the endpoints the question needs), the view or condition that would
   make it answerable here, and the way back to every question.
   ==================================================================================== */
import { useEffect, useMemo, useRef } from 'react'
import { makeSpec, withChain } from '../route'
import { useDataset, useDatasets, useRuns, useMetrics, useExperiment, useOracle, useIntegrity, useSentinel, useReport, useOutcomes,
  short, sfx, fmt, verdict, tally, isFailureMode, resolveCondition, invalidate } from '../data'
import { QUESTIONS, QMAP, answerQuestion, operatingPoint } from '../answers'
import { useRig, useTabState } from '../context'
import { AskingSentence, PinButton, Go, Panel, HBar, OutcomeBar, CiBar, Verdict, FailureChip, Tag, Loading, ErrorState, NotFound, Empty, NextSteps, Seg, About } from '../ui'
import './questions.css'

const { pct, int, pp } = fmt

/* ---------------------------------------------------------------- data */
const status = (r) => (r.error ? 'error' : r.loading ? true : false)
/** Everything the nine answers need for one dataset, loaded in parallel, answered as it lands. */
export function useAnswers(dir, override) {
  const { oracle, conditionFor } = useRig()
  const row = useDataset(dir)
  const defH = row.data ? (row.data.harnesses.includes('baseline') ? 'baseline' : row.data.harnesses[0]) : null
  const runs = useRuns(dir)
  const metrics = useMetrics(dir)
  const experiment = useExperiment(dir)
  const oracleData = useOracle(dir)
  const integrity = useIntegrity(defH ? dir : null, defH)
  const sentinel = useSentinel()
  const report = useReport(dir)
  const outcomes = useOutcomes(dir)
  const mem = conditionFor(dir)
  const cond = useMemo(() => (override && row.data && (override.model || override.harness)
    ? resolveCondition(row.data, override.model || mem.model, override.harness || mem.harness) : mem),
  [override && override.model, override && override.harness, row.data, mem.model, mem.harness, mem.dir]) // eslint-disable-line react-hooks/exhaustive-deps
  return useMemo(() => {
    const input = { dir, row: row.data, cond, oracle, runs: runs.data, metrics: metrics.data, experiment: experiment.data, oracleData: oracleData.data,
      integrity: integrity.data, sentinel: sentinel.data, report: report.data, outcomes: outcomes.data }
    const loading = { runs: status(runs), metrics: status(metrics), experiment: status(experiment), oracle: status(oracleData),
      integrity: row.data ? status(integrity) : true, sentinel: status(sentinel), report: status(report), outcomes: status(outcomes) }
    const results = QUESTIONS.map((q) => (row.data ? { q, ...answerQuestion(q, input, loading) } : { q, state: 'loading' }))
    return { input, results, row: row.data, missing: row.missing, error: row.error, reload: row.reload }
  }, [dir, row.data, row.missing, row.error, row.reload, cond, oracle, runs.data, runs.loading, runs.error, metrics.data, metrics.loading, metrics.error, experiment.data, experiment.loading, experiment.error,
    oracleData.data, oracleData.loading, oracleData.error, integrity.data, integrity.loading, integrity.error, sentinel.data, sentinel.loading, sentinel.error, report.data, report.loading, report.error, outcomes.data, outcomes.loading, outcomes.error])
}

/* ---------------------------------------------------------------- pieces */
export function Sentence({ parts }) {
  return <>{(parts || []).map((p, i) => (typeof p === 'string' ? <span key={i}>{p}</span> : <mark key={i} className="rg-q-mark-t">{p.mark}</mark>))}</>
}
const plain = (parts) => (parts || []).map((p) => (typeof p === 'string' ? p : p.mark)).join('')

/** The stack of nine answered questions (dataset tab, q:<dir>). */
export function QuestionStack({ dir, compact, model, harness }) {
  const A = useAnswers(dir, { model, harness })
  const cond = A.input.cond
  return (
    <ol className={`rg-q-stack${compact ? ' compact' : ''}`} aria-label="Questions, answered" data-el="question-stack">
      {A.results.map(({ q, state, answer, reason }, i) => (
        <li key={q.id}>
          <Go spec={makeSpec('q', dir, q.id)} className={`rg-q-card${state === 'unanswered' ? ' na' : ''}${i === 0 ? ' lead' : ''}`} el={`question-${q.id}`}>
            <span className="n">{i + 1}</span>
            <span className="qq">{cond && cond.model ? q.q(cond) : q.q({ model: '…', harness: '…' })}</span>
            {state === 'answered' && <span className="qv"><b>{answer.num}</b><small>{answer.numLabel}</small></span>}
            {state === 'answered' && <span className="qa"><Sentence parts={answer.sentence} /></span>}
            {state === 'loading' && <span className="qa"><span className="rg-skel" style={{ display: 'block', height: 12, width: '70%' }} /></span>}
            {state === 'unanswered' && <><span className="qv"><Tag tone="dash">not answered</Tag></span><span className="qa">{reason}</span></>}
          </Go>
        </li>
      ))}
    </ol>
  )
}

/* ---------------------------------------------------------------- figures */
function Figure({ fig, dir, answer }) {
  const { oracle } = useRig()
  switch (fig.kind) {
    case 'variance': {
      const { f } = fig
      return (<>
        <div className="rg-col" style={{ gap: 4 }}>
          <HBar label="model" value={f.model.share} text={pct(f.model.share)} />
          <HBar label="harness" value={f.harness.share} text={pct(f.harness.share)} />
          <HBar label="interaction" value={f.interaction_share} text={pct(f.interaction_share)} tone="dim" />
        </div>
        <p className="rg-small rg-dim" style={{ marginTop: 8 }}>Share of the sum of squares in a two-factor fit of hidden-suite pass (model × harness). Spread of level means: model {pp(f.model.range)}, harness {pp(f.harness.range)}.</p>
        <div className="rg-lbl" style={{ margin: '12px 0 6px' }}>{fig.nSep ? `Contrasts that separate from zero · ${fig.nSep} of ${fig.nAll}` : `Largest contrasts · none of ${fig.nAll} separates`}</div>
        <div className="rg-tblwrap"><table className="rg-tbl"><thead><tr><th>model</th><th>from → to (harness)</th><th className="rg-num">Δ pass@1</th><th className="rg-hide-sm">95% CI</th></tr></thead>
          <tbody>{fig.contrasts.map((c, i) => <tr key={i}><td className="rg-mono">{short(c.a)}</td><td className="rg-mono">{c.from} → {c.to}</td><td className={`rg-num rg-mono ${c.delta < 0 ? 'rg-red' : 'rg-sky'}`}>{pp(c.delta)}</td>
            <td className="rg-hide-sm"><CiBar lo={c.ci95[0]} hi={c.ci95[1]} pt={c.delta} min={-0.6} max={0.6} zero={0} /></td></tr>)}</tbody></table></div>
      </>)
    }
    case 'oracle': {
      const { o } = fig
      const c = o.cells
      return (<>
        <div className="rg-col" style={{ gap: 4 }}>
          <HBar label="hidden suite" value={o.rate_a.rate} text={pct(o.rate_a.rate)} tone="sky" />
          <HBar label="strengthened" value={o.rate_b.rate} text={pct(o.rate_b.rate)} tone="sky" />
        </div>
        <table className="rg-tbl rg-q-2x2" style={{ marginTop: 12 }}><caption className="rg-lbl" style={{ textAlign: 'left', padding: '0 0 6px' }}>Agreement over {int(o.n_eligible)} runs graded by both · κ {fmt.fx(o.kappa)}</caption>
          <thead><tr><th /><th className="rg-num">strengthened ✓</th><th className="rg-num">strengthened ×</th></tr></thead>
          <tbody><tr><th>hidden ✓</th><td className="rg-num rg-mono">{int(c.both_true)}</td><td className="rg-num rg-mono rg-red">{int(c.only_a_true)}</td></tr>
            <tr><th>hidden ×</th><td className="rg-num rg-mono">{int(c.only_b_true)}</td><td className="rg-num rg-mono">{int(c.both_false)}</td></tr></tbody></table>
        {o.n_excluded ? <p className="rg-small rg-dim">{int(o.n_excluded)} runs lack one of the two grades and are excluded, not counted as failures.</p> : null}
      </>)
    }
    case 'strips':
      return (<>
        <div className="rg-q-strips">{fig.strips.map((s) => (
          <div key={s.task} className="rg-q-strip">
            <span className="rg-mono rg-small rg-ell">{s.task}</span>
            <span className="rg-q-rib">{s.runs.map((r) => { const v = verdict(r, oracle); return <span key={r.id} className={`c ${v === true ? 'p' : v === false ? 'f' : 'u'}`} title={`repeat ${r.rep} · ${sfx(r.id)}`}>{v === true ? '✓' : v === false ? '×' : '?'}</span> })}</span>
            <span className="rg-mono rg-small">{s.p}/{s.known}{s.u ? ` · ${s.u}?` : ''}</span>
            {s.mixed ? <Tag tone="amb">mixed</Tag> : <span />}
          </div>))}</div>
        <p className="rg-small rg-dim">Repeats in order of repeat_index, graded by the {oracle} suite. A task is mixed when some repeats pass and others fail.</p>
      </>)
    case 'modes': {
      const max = Math.max(...fig.fails.map(([, n]) => n), 1)
      const label = (id) => (fig.modes.find((m) => m.id === id) || { label: id }).label
      const meaning = (id) => (fig.modes.find((m) => m.id === id) || {}).meaning
      const cf = Object.entries(fig.condCounts).sort((a, b) => b[1] - a[1])
      return (<>
        <div className="rg-lbl" style={{ marginBottom: 6 }}>Every failed run in {dir} · {int(fig.failures)}</div>
        <div className="rg-col" style={{ gap: 4 }}>{fig.fails.map(([m, n]) => <div key={m} title={meaning(m)}><HBar label={label(m)} value={n} max={max} text={`${int(n)} · ${pct(n / fig.failures, 0)}`} tone="red" /></div>)}</div>
        <div className="rg-lbl" style={{ margin: '14px 0 6px' }}>In {short(fig.cond.model)} · {fig.cond.harness} · {int(fig.condFailures)} failed</div>
        {cf.length ? <div className="rg-row rg-wrap">{cf.map(([m, n]) => <span key={m} className="rg-row" style={{ gap: 4 }}><FailureChip mode={m} dir={dir} /><span className="rg-mono rg-small">×{n}</span></span>)}</div>
          : <p className="rg-small rg-dim">No run in this condition failed the hidden suite.</p>}
      </>)
    }
    case 'leak': {
      const max = Math.max(...fig.rows.map((x) => x.patch_issue_similarity), 0.01)
      return (<>
        <div className="rg-col" style={{ gap: 4 }}>{fig.rows.map((x) => (
          <div key={x.task} className="rg-row" style={{ gap: 8 }}>
            <div className="rg-grow"><HBar label={x.task} value={x.patch_issue_similarity} max={max} text={pct(x.patch_issue_similarity, 0)} tone={x.probe && x.probe !== 'none' ? 'amb' : undefined} /></div>
            <span className="rg-q-probe">{x.probe && x.probe !== 'none' ? <Tag tone="amb">◆ {x.probe.replace(/_/g, ' ')}</Tag> : null}</span>
          </div>))}</div>
        <p className="rg-small rg-dim" style={{ marginTop: 8 }}>Similarity between each task's patches and its issue text. ◆ marks tasks built as probes (a planted leak, injection or temptation).</p>
      </>)
    }
    case 'run': {
      const r = fig.run
      return (
        <dl className="rg-kv">
          <dt>run</dt><dd>{r.id}</dd><dt>task</dt><dd>{r.task} · repeat {r.rep}</dd><dt>condition</dt><dd>{short(r.model)} · {r.harness}</dd>
          <dt>{oracle} tests</dt><dd className="rg-row"><Verdict v={verdict(r, oracle)} /><FailureChip run={r} /></dd>
          <dt>visible · hidden · strengthened</dt><dd className="rg-row"><Verdict v={r.vis} /><Verdict v={r.hid} /><Verdict v={r.str} /></dd>
          <dt>model calls · tool calls · edits</dt><dd>{r.steps} · {r.tools} · {r.edits}</dd>
          <dt>tokens</dt><dd>{r.in == null || r.out == null ? '— not recorded' : `${int(r.in + r.out)} (${int(r.in)} in / ${int(r.out)} out)`}</dd>
          <dt>exit</dt><dd>{r.exit}{r.lastFinish ? ` · last finish “${r.lastFinish}”` : ''}</dd>
        </dl>
      )
    }
    case 'sentinel': {
      const ops = (fig.model.meta.operating_points || [])
      const op = operatingPoint(fig.model, fig.threshold)
      return (<>
        <div className="rg-tblwrap"><table className="rg-tbl"><thead><tr><th className="rg-num">threshold</th><th className="rg-num">bad caught</th><th className="rg-num">good blocked</th><th className="rg-num">lead (steps)</th></tr></thead>
          <tbody>{ops.map((o) => <tr key={o.threshold} className={o === op ? 'sel' : ''}><td className="rg-num rg-mono">{fmt.fx(o.threshold)}</td><td className="rg-num rg-mono">{pct(o.recall, 0)}</td><td className="rg-num rg-mono rg-red">{pct(o.false_alarm, 0)}</td><td className="rg-num rg-mono">{fmt.fx(o.mean_lead_steps, 1)}</td></tr>)}</tbody></table></div>
        <p className="rg-small rg-dim" style={{ marginTop: 8 }}>{fig.model.name} · trained on {(fig.model.meta.sources || []).join(', ')} · {int(fig.model.meta.n_runs)} runs · run-weighted AUC {fmt.fx(fig.model.meta.auc_run_weighted)}. Highlighted row = the default threshold.</p>
      </>)
    }
    case 'report': {
      const o = fig.card.outcome
      const ks = Object.keys(o.pass_at_k || {})
      return (<>
        <div className="rg-row" style={{ gap: 12, marginBottom: 8 }}><span className="rg-fig md">{pct(o.pass1)}</span><div className="rg-grow"><CiBar lo={o.ci95[0]} hi={o.ci95[1]} pt={o.pass1} /></div></div>
        <p className="rg-small"><Tag tone={fig.pooled ? 'amb' : undefined} nc>{fig.scope}</Tag> <span className="rg-dim">n = {int(fig.card.cell.runs)} · {fig.card.cell.repeats} repeats · {fig.card.cell.tasks.length} tasks</span></p>
        <table className="rg-tbl"><thead><tr><th>k</th><th className="rg-num">pass@k (any of k)</th><th className="rg-num">pass^k (all of k)</th></tr></thead>
          <tbody>{ks.map((k) => <tr key={k}><td className="rg-mono">{k}</td><td className="rg-num rg-mono">{pct(o.pass_at_k[k])}</td><td className="rg-num rg-mono">{pct(o.pass_pow_k[k])}</td></tr>)}</tbody></table>
        {(fig.card.missing || []).length > 0 && <><div className="rg-lbl" style={{ margin: '12px 0 6px' }}>What this card does not cover</div><ul className="rg-small rg-dim rg-q-list">{fig.card.missing.map((m, i) => <li key={i}>{m}</li>)}</ul></>}
      </>)
    }
    case 'plan':
      return (<>
        <div className="rg-col" style={{ gap: 4 }}>{fig.p.models.map((x) => <HBar key={x.m} label={short(x.m)} value={x.reps} max={fig.p.max} text={`${x.reps} / task`} tone={x.reps < fig.p.max ? 'amb' : undefined} />)}</div>
        <p className="rg-small rg-dim" style={{ marginTop: 8 }}>Fewest repeats per task across each model's (harness, task) slots.</p>
      </>)
    default: return null
  }
}

/* ---------------------------------------------------------------- lesson (Student Lab) */
const LESSONS = {
  variable: ['A two-factor fit split the variance between model, harness and their interaction.', 'Find the contrast with the widest separation, then open its runs.', 'If the harness barely moves the outcome, the model choice is the decision that matters.'],
  grader: ['A second, stronger suite re-graded every run.', 'Open the runs the hidden suite passed and the strengthened suite failed.', 'If the oracle is weak, the pass rate measures the tests, not the fix.'],
  repeats: ['Identical model, harness and task, repeated, did not always agree.', 'Open the failing repeat and read the event where it stopped.', 'pass@1 hides flips; pass^k is what a user relying on one attempt experiences.'],
  outcomes: ['Each failed run was classified by how it stopped, from its ledger.', 'Open a cut-off run and find the last model reply.', 'A run cut off before any edit says nothing about whether the model could fix the bug.'],
  leak: ['One task hands the fix to the agent in the issue text, deliberately.', 'Compare its patch↔issue overlap with every other task.', 'A high score on a leak probe is copying, not solving.'],
  run: ['One recorded attempt, event by event.', 'Find the last model response and check what it asked for.', 'Exit reasons describe how a run stopped, not why the task failed.'],
  sentinel: ['A classifier scored partial trajectories step by step.', 'Compare the operating points.', 'Early warning trades good submits for bad ones.'],
  report: ['Every number condensed into one card.', 'Check the card’s scope against its n.', 'A card without its limits reads as a claim about everything.'],
  next: ['The design with the least information per repeat.', 'Compare repeat counts per model.', 'More repeats narrow an interval; a new variable answers a new question.'],
}
function Lesson({ qid, dir }) {
  const L = LESSONS[qid]; if (!L) return null
  return (
    <section className="rg-q-lesson" data-el="lesson-panel" aria-label="Why it matters">
      <div className="rg-lbl rg-acc">Student Lab · why it matters</div>
      <ol><li><b>What happened</b>{L[0]}</li><li><b>Investigate</b>{L[1]}</li><li><b>Why it matters</b>{L[2]}</li>
        <li><b>Evidence</b><Go spec={makeSpec('q', dir, qid, 'runs')} className="rg-tlink">Open the runs →</Go></li></ol>
    </section>
  )
}

/* ---------------------------------------------------------------- unanswered: ways forward */
/* Questions whose answer depends only on per-dataset endpoints, so another dataset can be
   probed cheaply (the run-level questions depend on the condition instead; see condSwitches). */
const PROBED = ['variable', 'grader', 'leak', 'sentinel', 'report', 'outcomes']
const defHarness = (row) => (row ? (row.harnesses.includes('baseline') ? 'baseline' : row.harnesses[0]) : null)
/** Renders "Ask it of <dir>" when `qid` IS answerable for `dir`, else nothing. Loads only what
 *  that question needs (one endpoint), from the shared cache. */
function AskElsewhere({ qid, row, sentinel }) {
  const { oracle } = useRig()
  const need = QMAP[qid].needs
  const dir = row.name
  const experiment = useExperiment(need.includes('experiment') ? dir : null)
  const oracleData = useOracle(need.includes('oracle') ? dir : null)
  const integrity = useIntegrity(need.includes('integrity') ? dir : null, defHarness(row))
  const report = useReport(need.includes('report') ? dir : null)
  const outcomes = useOutcomes(need.includes('outcomes') ? dir : null)
  const input = { dir, row, cond: { model: null, harness: null }, oracle, runs: null, metrics: null, experiment: experiment.data, oracleData: oracleData.data,
    integrity: integrity.data, sentinel, report: report.data, outcomes: outcomes.data }
  let r = { state: 'loading' }
  try { r = answerQuestion(QMAP[qid], input, {}) } catch { r = { state: 'unanswered' } }
  if (r.state !== 'answered') return null
  return <Go spec={makeSpec('q', dir, qid)} className="rg-btn" el="ask-elsewhere">Ask it of {dir}</Go>
}
/** Condition switches for a run-level question with no runs in the current condition. */
function condSwitches(runs, dir, cond, setCondition) {
  if (!runs || !cond || !cond.model) return []
  const hs = [...new Set(runs.filter((r) => r.model === cond.model).map((r) => r.harness))].filter((h) => h !== cond.harness).sort().slice(0, 2)
  const ms = [...new Set(runs.filter((r) => r.harness === cond.harness).map((r) => r.model))].filter((m) => m !== cond.model).sort().slice(0, 2)
  return [...hs.map((h) => ({ label: `${short(cond.model)} under ${h}`, onClick: () => setCondition({ dir, model: cond.model, harness: h }) })),
    ...ms.map((m) => ({ label: `${short(m)} under ${cond.harness}`, onClick: () => setCondition({ dir, model: m, harness: cond.harness }) }))]
}
/** What would make `qid` answerable here, or the view that says more. */
function localWays(qid, dir, reason, A, setCondition) {
  const acts = []
  if (/did not load/.test(reason || '')) acts.push({ label: 'Retry loading', onClick: () => invalidate(`/results/${encodeURIComponent(dir)}`), primary: true })
  if (qid === 'sentinel') acts.push({ label: 'Open Sentinel (trained models)', spec: 'sentinel' })
  if (qid === 'variable') acts.push({ label: 'What should I run next?', spec: makeSpec('q', dir, 'next') })
  if (qid === 'report') acts.push({ label: 'Open the report view', spec: makeSpec('an', dir, 'report') })
  if (qid === 'outcomes') acts.push({ label: 'What did one run do?', spec: makeSpec('q', dir, 'run') })
  if (qid === 'leak') acts.push({ label: 'How do runs fail?', spec: makeSpec('q', dir, 'outcomes') })
  if (qid === 'next') acts.push({ label: 'Load trajectories', spec: 'sources' })
  if (qid === 'repeats' || qid === 'run') acts.push(...condSwitches(A.input.runs, dir, A.input.cond, setCondition))
  return acts
}
function Unanswered({ dir, qid, reason, A }) {
  const { setCondition } = useRig()
  const all = useDatasets().data || []
  const sentinel = useSentinel().data
  const others = PROBED.includes(qid)
    ? all.filter((r) => r.name !== dir).sort((a, b) => (a.kind === b.kind ? b.runs - a.runs : a.kind === 'recorded' ? -1 : 1)).slice(0, 3) : []
  const local = localWays(qid, dir, reason, A, setCondition)
  return (
    <Empty title="Not answered for this dataset." el="empty-state question-unanswered"
      more={<>
        {others.length > 0 && <div className="rg-col rg-q-else"><span className="rg-lbl">Datasets that can answer it</span>
          <div className="rg-next">{others.map((r) => <AskElsewhere key={r.name} qid={qid} row={r} sentinel={sentinel} />)}</div></div>}
        <NextSteps actions={[...local, { label: `All questions about ${dir}`, spec: makeSpec('q', dir) }]} />
      </>}>
      {reason}
    </Empty>
  )
}

/* ---------------------------------------------------------------- documents */
function QuestionsDoc({ spec, args }) {
  const [dir, qid, sub] = args
  const A = useAnswers(dir)
  const { lab } = useRig()
  const figRef = useRef(null)
  useEffect(() => { if (sub === 'figure' && figRef.current) { figRef.current.scrollIntoView({ block: 'start' }); figRef.current.focus({ preventScroll: true }) } }, [sub, A.results])
  if (A.error) return <ErrorState error={A.error} onRetry={A.reload} what="The dataset index (/api/overview)" />
  if (A.missing) return <NotFound spec={spec} detail={`There is no dataset “${dir}” in data/runs.`} />
  if (qid && !QMAP[qid]) return <NotFound spec={spec} detail={`There is no question “${qid}”. The questions are: ${QUESTIONS.map((q) => q.id).join(', ')}.`} />
  if (!A.row) return <Loading label="Reading /api/overview…" />
  if (!qid) {
    return (
      <div className="rg-dpad">
        <div className="rg-dh"><div className="t"><div className="rg-eyebrow">{A.row.kind === 'mock' ? 'Mock control' : 'Recorded dataset'} · {int(A.row.runs)} runs</div>
          <h1>What do you want to know about <span className="rg-mono">{dir}</span>?</h1>
          <p>Nine questions, each answered from the recorded runs with one sentence and one number. Every answer leads down to the runs and the exact recorded event behind it.</p></div></div>
        <AskingSentence dir={dir} />
        <QuestionStack dir={dir} />
      </div>
    )
  }
  const res = A.results.find((r) => r.q.id === qid)
  const q = res.q
  const cond = A.input.cond
  if (sub === 'runs') return <RunsDoc dir={dir} res={res} runs={A.input.runs} runsLoading={!A.input.runs} spec={spec} />
  return (
    <div className="rg-dpad">
      <AskingSentence dir={dir} />
      <div className="rg-q-ask"><span className="rg-q-mark" aria-hidden="true">?</span><h1>{q.q(cond)}</h1></div>
      {res.state === 'loading' && <Loading label="Answering from the live endpoints…" />}
      {res.state === 'unanswered' && <Unanswered dir={dir} qid={qid} reason={res.reason} A={A} />}
      {res.state === 'answered' && (<>
        <div className="rg-q-answer" data-el="answer">
          <p className="a"><Sentence parts={res.answer.sentence} /></p>
          <div className="big"><div className="v">{res.answer.num}</div><div className="l">{res.answer.numLabel}</div></div>
        </div>
        <div className="rg-row rg-wrap rg-q-pinrow">
          <PinButton item={{ id: `answer:${dir}:${qid}`, kind: 'answer', label: q.q(cond), value: `${res.answer.num} — ${plain(res.answer.sentence)}`, spec: makeSpec('q', dir, qid), dir, model: cond.model, harness: cond.harness }} />
          {res.answer.note && <span className="rg-small rg-dim rg-grow rg-q-note">{res.answer.note}</span>}
        </div>
        {lab && <Lesson qid={qid} dir={dir} />}
        <div ref={figRef} tabIndex={-1} id="rg-q-figure">
          <Panel label="Figure" el="question-figure" meta={res.answer.short}
            actions={<PinButton compact item={{ id: `figure:${dir}:${qid}`, kind: 'figure', label: `Figure · ${q.short} · ${dir}`, value: res.answer.short, spec: makeSpec('q', dir, qid, 'figure'), dir }} />}>
            <Figure fig={res.answer.fig} dir={dir} answer={res.answer} />
          </Panel>
        </div>
        {res.answer.runs && <ShowRuns dir={dir} qid={qid} res={res} runs={A.input.runs} />}
        <p className="rg-small rg-mute rg-q-disc" data-el="descriptive-disclaimer">Descriptive, not causal. Every number is read from the live API for {dir}; unknown grades are never counted as failures.</p>
      </>)}
    </div>
  )
}

function ShowRuns({ dir, qid, res, runs }) {
  const list = runs ? runs.filter(res.answer.runs.test) : null
  return (
    <div className="rg-q-next">
      <p>Show me the runs behind this answer: <b>{res.answer.runs.title}</b>{list ? <> · <span className="rg-mono">{int(list.length)}</span> runs</> : ' · loading the run index…'}</p>
      <Go spec={makeSpec('q', dir, qid, 'runs')} className="rg-btn pri lg" el="show-me-the-runs">Show me the runs →</Go>
    </div>
  )
}

const PAGE = 50
function RunsDoc({ dir, res, runs, spec }) {
  const { oracle } = useRig()
  const out = useOutcomes(dir)
  const [mode, setMode] = useTabState(spec, 'mode', 'all')
  const [pg, setPg] = useTabState(spec, 'pg', 0)
  const q = res.q
  const chain = { dir, qid: q.id }
  if (res.state === 'loading' || !runs) return <Loading label="Loading the runs behind this answer…" />
  if (res.state !== 'answered' || !res.answer.runs) {
    return (
      <div className="rg-dpad"><Empty title="No runs behind this answer."
        actions={[{ label: 'Back to the answer', spec: makeSpec('q', dir, q.id), primary: true }, { label: `All questions about ${dir}`, spec: makeSpec('q', dir) }, { label: `Browse ${dir} runs`, spec: makeSpec('field', dir) }]}>
        {res.reason || 'This answer is not computed from individual runs, so there is no run list to open.'}
      </Empty></div>
    )
  }
  const all = runs.filter(res.answer.runs.test).sort((a, b) => a.task.localeCompare(b.task) || a.model.localeCompare(b.model) || a.harness.localeCompare(b.harness) || a.rep - b.rep)
  const modes = [...new Set(all.map((r) => r.mode).filter(isFailureMode))]
  const modeLabel = (m) => ((out.data && out.data.byId[m]) || { label: m.replace(/_/g, ' ') }).label
  const list = mode === 'all' ? all : mode === 'failed' ? all.filter((r) => verdict(r, oracle) === false) : all.filter((r) => r.mode === mode)
  const t = tally(all, oracle)
  const shown = list.slice(pg * PAGE, pg * PAGE + PAGE)
  return (
    <div className="rg-dpad">
      <div className="rg-dh"><div className="t"><div className="rg-eyebrow">Runs behind the answer · {q.short}</div><h1>{res.answer.runs.title}</h1>
        <p className="rg-mono rg-small">{int(all.length)} runs · {t.p} pass · {t.f} fail · {t.u} unknown under the {oracle} suite</p></div>
        <div className="rg-q-runsbar"><OutcomeBar p={t.p} f={t.f} u={t.u} tall /></div></div>
      <div className="rg-row rg-wrap" style={{ marginBottom: 10 }}>
        <Seg label="Filter runs" el="failure-mode-filter" value={mode} onChange={(v) => { setMode(v); setPg(0) }}
          options={[['all', `All ${all.length}`], ['failed', `Failed ${t.f}`], ...modes.map((m) => [m, `${modeLabel(m)} ${all.filter((r) => r.mode === m).length}`])]} />
      </div>
      <div className="rg-pnl" data-el="question-runs"><div className="rg-tblwrap"><table className="rg-tbl">
        <thead><tr><th>{oracle}</th><th>run</th><th>task</th><th className="rg-hide-sm">model · harness</th><th className="rg-num rg-hide-sm">rep</th><th className="rg-num">calls</th><th className="rg-num rg-hide-sm">tokens</th><th>how it failed</th><th><span className="sr-only">Pin</span></th></tr></thead>
        <tbody>{shown.map((r) => (
          <tr key={r.id}>
            <td><Verdict v={verdict(r, oracle)} /></td>
            <td><Go spec={withChain(makeSpec('run', r.id), chain)} className="rg-tlink" title={r.id}>{sfx(r.id)}</Go></td>
            <td className="rg-mono rg-small">{r.task}</td>
            <td className="rg-mono rg-small rg-hide-sm">{short(r.model)} · {r.harness}</td>
            <td className="rg-num rg-mono rg-hide-sm">{r.rep}</td>
            <td className="rg-num rg-mono">{r.steps}</td>
            <td className="rg-num rg-mono rg-hide-sm">{r.in == null || r.out == null ? '—' : int(r.in + r.out)}</td>
            <td><FailureChip run={r} /></td>
            <td><PinButton compact item={{ id: `run:${r.id}`, kind: 'run', label: `run ${sfx(r.id)} · ${r.task}`, value: `${short(r.model)} · ${r.harness} · ${verdict(r, oracle) === true ? 'pass' : verdict(r, oracle) === false ? 'fail' : 'unknown'} (${oracle})`, spec: withChain(makeSpec('run', r.id), chain), dir, run: r.id, model: r.model, harness: r.harness, task: r.task }} /></td>
          </tr>))}</tbody>
      </table></div>
      {!list.length && <div className="rg-pnl-b"><Empty title="No run matches this filter." actions={[{ label: `Show all ${all.length} runs`, onClick: () => { setMode('all'); setPg(0) }, primary: true }]}>None of the runs behind this answer has this outcome under the {oracle} suite.</Empty></div>}
      {list.length > PAGE && <div className="rg-row rg-q-pager"><span className="rg-small rg-mono rg-mute">{pg * PAGE + 1}–{Math.min(list.length, pg * PAGE + PAGE)} of {list.length}</span><span className="rg-grow" />
        {pg > 0 && <button type="button" className="rg-btn" onClick={() => setPg(pg - 1)}>← previous {PAGE}</button>}{(pg + 1) * PAGE < list.length && <button type="button" className="rg-btn" onClick={() => setPg(pg + 1)}>next {Math.min(PAGE, list.length - (pg + 1) * PAGE)} →</button>}</div>}
      </div>
      <About summary="Where the failure modes come from">Failure modes are read from each run's ledger by /api/outcomes, on the hidden suite whatever suite grades the rest of the page. Open a run to follow the chain down to the event where it stopped.</About>
    </div>
  )
}

export const views = {
  q: {
    tag: 'q',
    title: (a) => (a[1] ? `${QMAP[a[1]] ? QMAP[a[1]].short : a[1]}${a[2] === 'runs' ? ' · runs' : ''} · ${a[0]}` : `questions · ${a[0]}`),
    ctx: (a) => ({ dir: a[0] }),
    crumbs: (a) => [[a[0], makeSpec('ds', a[0])], ['questions', a[1] ? makeSpec('q', a[0]) : null], ...(a[1] ? [[QMAP[a[1]] ? QMAP[a[1]].short : a[1]]] : [])],
    retarget: (a, c) => (c.dir !== a[0] ? makeSpec('q', c.dir, ...a.slice(1)) : null),
    outsideGuided: (a) => a[1] === 'sentinel',
    render: QuestionsDoc,
  },
}
