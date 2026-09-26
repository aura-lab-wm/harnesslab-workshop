/* ====================================================================================
   Rig · views/dataset.jsx — `ds:<dir>[:<model>:<harness>]`: dataset header, the answered
   questions (visible on open), then the condition overview from the classic app's overview:
   success with its known denominator, mixed-outcome tasks, visible ≠ hidden, mean tokens
   with coverage, model performance under one harness, where to look next, failure modes.
   Owner: Phase 1. Round 3: sections stack (model table, then where to look next); only the
   this-condition vs whole-dataset failure modes sit side by side, because they are compared.
   An empty condition offers the conditions that do have runs.
   ==================================================================================== */
import { useMemo } from 'react'
import { makeSpec } from '../route'
import { useDataset, useRuns, useOutcomes, short, fmt, tally, tokStats, conditionRuns, resolveCondition, isFailureMode, verdict } from '../data'
import { useRig } from '../context'
import { AskingSentence, Go, Kbd, Panel, OutcomeBar, Verdict, FailureChip, PinButton, Tag, Loading, ErrorState, NotFound, Empty, failureSummary } from '../ui'
import { QuestionStack } from './questions'
import './dataset.css'

const { pct, int, plural } = fmt

function Overview({ dir, c, row }) {
  const { oracle, openTab } = useRig()
  const R = useRuns(dir)
  const out = useOutcomes(dir)
  const d = useMemo(() => {
    if (!R.data) return null
    const list = conditionRuns(R.data, c.model, c.harness)
    const o = tally(list, oracle)
    const tasks = [...new Set(list.map((r) => r.task))].sort()
    const byTask = tasks.map((t) => { const l = list.filter((r) => r.task === t); return { t, l, o: tally(l, oracle) } })
    const mixed = byTask.filter((x) => x.o.p && x.o.f)
    const both = list.filter((r) => r.vis != null && r.hid != null)
    const dis = both.filter((r) => r.vis !== r.hid)
    const visOnly = dis.filter((r) => r.vis && !r.hid).length
    const tk = tokStats(list)
    const models = row.models.map((m) => { const l = conditionRuns(R.data, m, c.harness); return { m, l, o: tally(l, oracle), tk: tokStats(l), tasks: new Set(l.map((r) => r.task)).size } })
    const next = [...byTask].sort((a, b) => (!!(b.o.p && b.o.f) - !!(a.o.p && a.o.f)) || (b.o.f - a.o.f) || a.t.localeCompare(b.t)).slice(0, 8)
    const modes = {}
    for (const r of list) if (verdict(r, 'hidden') === false && isFailureMode(r.mode)) modes[r.mode] = (modes[r.mode] || 0) + 1
    return { list, o, tasks, byTask, mixed, both, dis, visOnly, tk, models, next, modes }
  }, [R.data, c.model, c.harness, oracle, row.models])
  if (R.error) return <ErrorState error={R.error} onRetry={R.reload} what={`The run index of ${dir}`} />
  if (!d) return <Loading label={`Reading /api/results/${dir}/runs…`} />
  if (!d.list.length) {
    const hs = [...new Set(R.data.filter((r) => r.model === c.model).map((r) => r.harness))].sort()
    const ms = [...new Set(R.data.filter((r) => r.harness === c.harness).map((r) => r.model))].sort()
    const go = (m, h) => ({ label: `${short(m)} under ${h}`, onClick: () => openTab(makeSpec('ds', dir, short(m), h), { replace: true }) })
    return (
      <section className="rg-ds-cond" aria-label="Condition overview">
        <Empty title="No runs in this condition." actions={[...hs.slice(0, 3).map((h) => go(c.model, h)), ...ms.slice(0, 3).map((m) => go(m, c.harness))].map((a, i) => ({ ...a, primary: i === 0 }))}>
          {short(c.model)} was not run under {c.harness} in {dir}. These conditions were:
        </Empty>
      </section>
    )
  }
  const cs = [dir, short(c.model), c.harness]
  const failSum = failureSummary(d.list, oracle)
  const modeRows = Object.entries(d.modes).sort((a, b) => b[1] - a[1])
  const dsModes = out.data ? Object.entries(out.data.counts || {}).filter(([k, v]) => isFailureMode(k) && v > 0).sort((a, b) => b[1] - a[1]) : []
  return (<section className="rg-ds-cond" aria-label="Condition overview">
    <div className="rg-dh">
      <div className="t" data-el="condition-summary"><div className="rg-eyebrow">Condition overview · one model × harness in focus</div>
        <h2><span className="rg-mono">{short(c.model)}</span> <span className="rg-dim" style={{ fontWeight: 400 }}>under</span> <span className="rg-mono">{c.harness}</span></h2>
        <p className="rg-mono rg-small">{dir} · {plural(d.tasks.length, 'task')} · {int(d.list.length)} attempts · {oracle}-suite grades</p></div>
      <PinButton item={{ id: `cell:${dir}:${c.model}:${c.harness}`, kind: 'cell', label: `${short(c.model)} · ${c.harness} · ${dir}`, value: `${pct(d.o.rate)} · ${d.o.p}/${d.o.known} known (${oracle})`, spec: makeSpec('ds', ...cs), dir, model: c.model, harness: c.harness }} />
      <Go spec={makeSpec('tasks', ...cs)} className="rg-btn pri lg" el="explore-trajectories-cta">Explore all trajectories <Kbd>↵</Kbd></Go>
    </div>
    <div className="rg-stats rg-ds-kpis">
      <div className="rg-stat" data-el="success-rate"><span className="rg-lbl">{oracle}-suite success</span>
        <div className="rg-row" style={{ alignItems: 'baseline', gap: 12 }}><span className="rg-fig">{d.o.rate == null ? '—' : (d.o.rate * 100).toFixed(1)}<span className="u">{d.o.rate == null ? '' : '%'}</span></span><span className="rg-mono rg-dim">{d.o.p} / {d.o.known} known</span></div>
        <OutcomeBar p={d.o.p} f={d.o.f} u={d.o.u} tall />
        <div className="rg-row rg-wrap rg-small rg-mono" style={{ gap: 12 }}><span className="rg-row" style={{ gap: 4 }}><Verdict v={true} /> {d.o.p} passed</span><span className="rg-row" style={{ gap: 4 }}><Verdict v={false} /> {d.o.f} failed</span><span className="rg-row" style={{ gap: 4 }}><Verdict v={null} /> {d.o.u} unknown</span></div>
        <span className="rg-small rg-mute">{d.o.u ? `${d.o.u} unknown grade${d.o.u > 1 ? 's' : ''} excluded from the rate.` : 'No unknown grades here.'}</span></div>
      <Go spec={makeSpec('tasks', ...cs)} className="rg-stat" el="mixed-outcomes-count"><span className="rg-lbl">Mixed outcomes</span><span className="rg-fig md">{d.mixed.length}<span className="u">/ {d.tasks.length} tasks</span></span><span className="rg-small rg-dim">Same condition — some attempts pass, others fail.</span>{d.mixed.length > 0 && <span className="rg-small rg-amb rg-mono">{d.mixed.slice(0, 3).map((x) => x.t).join(' · ')}</span>}</Go>
      <div className="rg-stat" data-el="visible-hidden-disagreements"><span className="rg-lbl">Visible ≠ hidden</span><span className="rg-fig md">{d.dis.length}<span className="u">/ {d.both.length}</span></span><span className="rg-small rg-dim">{d.visOnly} passed the visible tests and not the hidden suite.</span><span className="rg-small rg-mute">{d.list.length - d.both.length} attempts lack one of the two grades.</span></div>
      <div className="rg-stat" data-el="mean-tokens"><span className="rg-lbl">Mean tokens / run</span><span className="rg-fig md">{d.tk.mean == null ? '—' : int(d.tk.mean)}</span><span className="rg-small rg-dim rg-mono">usage recorded {d.tk.rec}/{d.tk.n}</span><span className="rg-small rg-mute">Not a savings estimate.</span></div>
    </div>
    <Panel label="Model performance · one harness, different models" meta={<Tag>{c.harness}</Tag>} el="model-performance-table" flush>
        <div className="rg-tblwrap"><table className="rg-tbl"><thead><tr><th>model</th><th className="rg-num">success</th><th className="rg-num rg-hide-sm">known · unknown</th><th className="rg-num">mean tokens</th><th className="rg-num rg-hide-sm">coverage</th><th><span className="sr-only">Open</span></th></tr></thead>
          <tbody>{d.models.map((x) => (
            <tr key={x.m} className={x.m === c.model ? 'sel' : ''}>
              <td className="rg-mono"><Go spec={makeSpec('ds', dir, short(x.m), c.harness)} className="rg-tlink">{short(x.m)}</Go><div className="rg-tiny rg-mute">{x.l.length} attempts</div></td>
              <td className="rg-num rg-mono"><b>{pct(x.o.rate)}</b><div style={{ marginTop: 4 }}><OutcomeBar p={x.o.p} f={x.o.f} u={x.o.u} /></div></td>
              <td className="rg-num rg-mono rg-small rg-hide-sm">{x.o.p}/{x.o.known} · {x.o.u}</td>
              <td className="rg-num rg-mono">{x.tk.mean == null ? '—' : int(x.tk.mean)}<div className="rg-tiny rg-mute">{x.tk.rec}/{x.tk.n} recorded</div></td>
              <td className="rg-num rg-mono rg-small rg-hide-sm">{x.tasks} tasks</td>
              <td className="rg-num"><Go spec={makeSpec('ds', dir, short(x.m), c.harness)} className="rg-btn ghost" title={`Focus ${short(x.m)}`}>→</Go></td>
            </tr>))}</tbody></table></div>
    </Panel>
    <Panel label="Where to look next" meta="mixed first, then failures" el="where-to-look-next" flush>
        <div className="rg-ds-nextlist">{d.next.map((x) => (
          <Go key={x.t} spec={makeSpec('task', ...cs, x.t)} className="rg-ds-next">
            <div className="rg-grow"><div className="rg-row" style={{ gap: 6 }}><span className="rg-mono">{x.t}</span>{x.o.p > 0 && x.o.f > 0 && <Tag tone="amb">mixed</Tag>}</div>
              <div style={{ marginTop: 6 }}><OutcomeBar p={x.o.p} f={x.o.f} u={x.o.u} /></div>
              <div className="rg-tiny rg-mute rg-mono" style={{ marginTop: 4 }}>{x.o.p} pass · {x.o.f} fail · {x.o.u} unknown{failureSummary(x.l, oracle) ? ` · ${failureSummary(x.l, oracle)}` : ''}</div></div>
            <span className="rg-mute">→</span>
          </Go>))}</div>
    </Panel>
    <Panel label="How runs fail" meta="/api/outcomes · hidden suite" el="failure-mode-summary">
      <div className="rg-grid rg-g2">
        <div><div className="rg-lbl" style={{ marginBottom: 6 }}>This condition</div>
          {modeRows.length ? <div className="rg-col" style={{ gap: 6 }}>{modeRows.map(([m, n]) => <div key={m} className="rg-row"><FailureChip mode={m} dir={dir} /><span className="rg-mono rg-small">×{n}</span></div>)}</div>
            : <p className="rg-small rg-dim">No run in this condition failed the hidden suite.</p>}
          {failSum && oracle !== 'hidden' && <p className="rg-small rg-dim" style={{ marginTop: 6 }}>Under the {oracle} suite: {failSum}.</p>}</div>
        <div><div className="rg-lbl" style={{ marginBottom: 6 }}>Whole dataset · {out.data ? `${int(out.data.failures)} failures` : '…'}</div>
          {out.error ? <div className="rg-col"><p className="rg-small rg-red">The failure modes did not load ({out.error.message}).</p><div><button type="button" className="rg-btn" onClick={out.reload}>Retry</button></div></div> : !out.data ? <div className="rg-skel" style={{ height: 40 }} />
            : dsModes.length ? <div className="rg-col" style={{ gap: 6 }}>{dsModes.map(([m, n]) => <div key={m} className="rg-row"><FailureChip mode={m} dir={dir} /><span className="rg-mono rg-small">{int(n)} · {pct(n / out.data.failures, 0)}</span></div>)}</div>
              : <p className="rg-small rg-dim">No failures recorded.</p>}
          <Go spec={makeSpec('q', dir, 'outcomes')} className="rg-tlink">How do runs fail? →</Go></div>
      </div>
    </Panel>
    <p className="rg-small rg-mute rg-ds-disc" data-el="descriptive-disclaimer">Descriptive comparisons of recorded attempts, not causal rankings. Rates are over known grades under the {oracle} suite; the denominator is always shown.</p>
  </section>)
}

function Dataset({ spec, args }) {
  const [dir, m, h] = args
  const ds = useDataset(dir)
  const { conditionFor } = useRig()
  if (ds.error) return <ErrorState error={ds.error} onRetry={ds.reload} what="The dataset index (/api/overview)" />
  if (ds.missing) return <NotFound spec={spec} detail={`There is no dataset “${dir}” in data/runs on this machine.`} />
  if (!ds.data) return <Loading label="Reading /api/overview…" />
  const row = ds.data
  const mem = conditionFor(dir)
  const c = resolveCondition(row, m || mem.model, h || mem.harness)
  return (
    <div className="rg-dpad">
      <div className="rg-dh">
        <div className="t"><div className="rg-eyebrow">{row.kind === 'mock' ? 'Mock control' : 'Recorded dataset'} · updated {String(row.updated).slice(0, 10) || '—'}</div>
          <h1 className="rg-mono">{dir}</h1>
          <div className="rg-row rg-wrap" style={{ marginTop: 8 }} data-el="dataset-stats"><Tag tone={row.kind === 'mock' ? 'dash' : undefined} el="dataset-kind">{row.kind === 'mock' ? 'Mock control' : 'Recorded'}</Tag><Tag nc>{int(row.runs)} runs</Tag><Tag nc>{row.models.length} models</Tag><Tag nc>{row.harnesses.length} harnesses</Tag><Tag nc>{row.tasks.length} tasks</Tag></div>
          {row.kind === 'mock' && <p>Mock control: scripted agents with designed failure rates. All-pass or all-fail here would mean the grader broke, not the model.</p>}</div>
      </div>
      <AskingSentence dir={dir} model={c.model} harness={c.harness} />
      <section aria-label="Questions" className="rg-ds-questions">
        <div className="rg-row rg-ds-sh"><h2>Questions, already answered</h2><span className="rg-grow" /><Go spec={makeSpec('q', dir)} className="rg-tlink">Open as a document →</Go></div>
        <QuestionStack dir={dir} model={c.model} harness={c.harness} compact />
      </section>
      <Overview dir={dir} c={c} row={row} />
    </div>
  )
}

function Actions({ args }) {
  const [dir, m, h] = args
  const { conditionFor } = useRig()
  const mem = conditionFor(dir)
  const cs = [dir, m || (mem.model && short(mem.model)), h || mem.harness]
  return (
    <div className="rg-seg" data-el="view-switch" role="group" aria-label="View">
      <button type="button" className="on" aria-pressed="true">Overview</button>
      <Go spec={makeSpec('tasks', ...cs)} className="rg-ds-seg">Trajectories</Go>
      <Go spec={makeSpec('an', dir, 'family')} className="rg-ds-seg rg-hide-sm">Analysis</Go>
    </div>
  )
}

export const views = {
  ds: {
    tag: 'exp',
    title: (a) => (a[1] ? `${a[0]} · ${a[1]}` : a[0] || 'dataset'),
    ctx: (a) => ({ dir: a[0], model: a[1] || null, harness: a[2] || null }),
    crumbs: (a) => [[a[0], a[1] ? makeSpec('ds', a[0]) : null], ...(a[1] ? [[`${a[1]} · ${a[2] || ''}`]] : [['overview']])],
    retarget: (a, c) => makeSpec('ds', c.dir, short(c.model), c.harness),
    render: Dataset,
    Actions,
  },
}
