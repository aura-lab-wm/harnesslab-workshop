/* ====================================================================================
   Rig · views/_pending.jsx — placeholders for every kind a Phase 2 agent will build.

   Underscore module = lowest precedence in the registry. A real view module that exports
   the same kind (e.g. views/run.jsx exporting `views.run`) REPLACES the placeholder; do not
   edit this file to remove it (delete the kind here only if you want, it is not required).
   When you take a kind over, carry its `palette`, `ctx`, `retarget` and `outsideGuided`
   across (copy them from here) so the palette and the status bar keep working.
   ==================================================================================== */
import { makeSpec } from '../route'
import { short, useFindRun, verdict, fmt } from '../data'
import { useRig } from '../context'
import { Go, Tag, Verdict, FailureChip, Loading, NotFound, AskingSentence } from '../ui'

const AN_VIEWS = [['family', 'Family matrix'], ['outcomes', 'Outcomes · pass@1 with CI'], ['delta', 'Comparison · Δ-matrix vs baseline'],
  ['judge', 'Judge & integrity'], ['fit', 'Experiment · two-factor fit'], ['report', 'Report card'], ['setup', 'Run setup · compose a cell']]
const AN_LABEL = Object.fromEntries(AN_VIEWS)

function Pending({ spec, what, children, owner = 'Phase 2' }) {
  return (
    <div className="rg-dpad">
      <div className="rg-pnl rg-narrow" data-el="pending-view">
        <div className="rg-pnl-h"><span className="rg-lbl">coming in this build</span><span className="meta"><Tag tone="dash">{owner}</Tag></span></div>
        <div className="rg-pnl-b rg-col">
          <h2>{what}</h2>
          <p className="rg-small rg-dim">This document is part of the Rig workbench but is not built yet. The link is stable: <code>#/rig/{spec}</code> will open the finished view.</p>
          {children}
          <div className="rg-row rg-wrap"><Go spec="home" className="rg-btn">Datasets</Go></div>
        </div>
      </div>
    </div>
  )
}

function RunStub({ spec, args, chain }) {
  const { oracle } = useRig()
  const f = useFindRun(args[0])
  if (f.loading) return <Loading label={`Looking up run ${args[0]} across every dataset…`} />
  if (!f.data) return <NotFound spec={spec} detail="Run ids resolve by full id or by a unique 6-character suffix across every dataset in data/runs." />
  const r = f.data.run
  const v = verdict(r, oracle)
  return (
    <Pending spec={spec} what={`Run ${r.id}`}>
      <dl className="rg-kv">
        <dt>dataset</dt><dd>{r.ds}</dd><dt>task</dt><dd>{r.task}</dd><dt>condition</dt><dd>{short(r.model)} · {r.harness} · repeat {r.rep}</dd>
        <dt>{oracle} tests</dt><dd className="rg-row"><Verdict v={v} /> <FailureChip run={r} /></dd>
        <dt>model calls</dt><dd>{r.steps}</dd><dt>exit</dt><dd>{r.exit}</dd>
        <dt>tokens</dt><dd>{r.in == null || r.out == null ? '— not recorded' : fmt.int(r.in + r.out)}</dd>
        {args[1] != null && <><dt>event</dt><dd>#{args[1]}</dd></>}
      </dl>
      {chain && <p className="rg-small rg-dim">Reached from the question “{chain.qid}” on {chain.dir}; the breadcrumb above is the evidence chain.</p>}
    </Pending>
  )
}

/** ctx for run:<id>[:seq] — the run's own dataset and condition once its index is cached. */
function runCtx(a, env) {
  const r = env.findRun(a[0])
  return r ? { dir: r.ds, model: r.model, harness: r.harness, task: r.task, run: r.id, seq: a[1] ?? null } : { run: a[0], seq: a[1] ?? null }
}

const stub = (tag, what, extra = {}) => ({
  tag,
  title: extra.title || (() => what),
  render: extra.render || (({ spec }) => <Pending spec={spec} what={what} />),
  ...extra,
})

export const views = {
  an: stub('an', 'Analysis', {
    title: (a) => `${AN_LABEL[a[1]] || a[1] || 'analysis'} · ${a[0] || ''}`,
    ctx: (a) => ({ dir: a[0], model: a[2] || null, harness: a[3] || null }),
    crumbs: (a) => [[a[0], makeSpec('ds', a[0])], [AN_LABEL[a[1]] || a[1] || 'analysis']],
    retarget: (a, c) => makeSpec('an', c.dir, a[1] || 'family', short(c.model), c.harness),
    outsideGuided: (a) => a[1] === 'setup',
    palette: (env) => env.datasets.flatMap((d) => AN_VIEWS.map(([v, l]) => ({ group: 'view', title: `${l}`, detail: d.name, spec: makeSpec('an', d.name, v), keywords: v === 'delta' ? 'compare delta matrix' : v === 'judge' ? 'oracle integrity leakage kappa' : v }))),
    render: ({ spec, args }) => <Pending spec={spec} what={`${AN_LABEL[args[1]] || 'Analysis'} · ${args[0]}`}><AskingSentence dir={args[0]} model={args[2]} harness={args[3]} /></Pending>,
  }),
  tasks: stub('traj', 'Trajectory explorer', {
    title: (a) => `tasks · ${a[1] || a[0]}${a[2] ? ' · ' + a[2] : ''}`,
    ctx: (a) => ({ dir: a[0], model: a[1] || null, harness: a[2] || null }),
    retarget: (a, c) => makeSpec('tasks', c.dir, short(c.model), c.harness),
  }),
  task: stub('task', 'Task investigator', {
    title: (a) => `${a[3] || 'task'} · ${a[1] || ''}`,
    ctx: (a) => ({ dir: a[0], model: a[1] || null, harness: a[2] || null, task: a[3] || null }),
    crumbs: (a) => [[a[0], makeSpec('ds', a[0], a[1], a[2])], [a[3] || 'task']],
    retarget: (a, c) => makeSpec('task', c.dir, short(c.model), c.harness, ...a.slice(3)),
    render: ({ spec, args }) => <Pending spec={spec} what={`Task ${args[3] || ''}`}><AskingSentence dir={args[0]} model={args[1]} harness={args[2]} /></Pending>,
  }),
  run: stub('run', 'Run', { title: (a) => `run ${String(a[0] || '').slice(-6)}`, ctx: runCtx, render: RunStub }),
  span: stub('evt', 'Event', { title: (a) => `${String(a[0] || '').slice(-6)} #${a[1] ?? ''}`, ctx: runCtx, render: RunStub }),
  cmp: stub('cmp', 'Compare two runs', { title: (a) => `${String(a[0] || '').slice(-6)} ↔ ${String(a[1] || '').slice(-6)}` }),
  fork: stub('fork', 'Fork a step · not implemented · proposed capability', { title: (a) => `fork ${String(a[0] || '').slice(-6)}`, outsideGuided: () => true }),
  field: stub('field', 'Every run, every span', {
    title: (a) => `runs · ${a[0] || ''}`, ctx: (a) => ({ dir: a[0] }),
    palette: (env) => env.datasets.map((d) => ({ group: 'dataset', title: `trajectories ${d.name}`, detail: 'field run list · search every run', spec: makeSpec('field', d.name) })),
  }),
  sources: stub('src', 'Sources & results on disk', { title: () => 'Sources', palette: () => [{ title: 'Sources & results on disk', detail: 'data/runs · import a trace', spec: 'sources', keywords: 'load trajectories import' }] }),
  capture: stub('cap', 'Session capture', { title: () => 'Capture', palette: () => [{ title: 'Session capture', detail: 'watcher · captured sessions', spec: 'capture' }] }),
  sentinel: stub('sent', 'Sentinel · early warning on a partial trajectory', { title: () => 'Sentinel', outsideGuided: () => true, palette: () => [{ title: 'Sentinel', detail: 'early warning on a partial trajectory', spec: 'sentinel' }] }),
  canvas: stub('cv', 'Canvas', { title: () => 'Canvas', outsideGuided: () => true, palette: () => [{ title: 'Canvas', detail: 'node canvas of studies, models, cells', spec: 'canvas' }] }),
  settings: stub('set', 'Settings', { title: () => 'Settings', palette: () => [{ title: 'Settings', detail: 'appearance, Student Lab, keys, paths', spec: 'settings' }] }),
  guide: stub('lab', 'Reading guide', { title: () => 'Reading guide', palette: () => [{ title: 'Reading guide', detail: 'Student Lab · start here', spec: 'guide' }] }),
  package: stub('pkg', 'Reference materials', { title: () => 'Package contents', palette: () => [{ title: 'Reference materials', detail: 'school replication package', spec: 'package' }] }),
}

const DockPending = ({ what }) => (
  <div className="rg-dpad" data-el="pending-view"><p className="rg-small rg-dim"><Tag tone="dash">coming in this build</Tag> {what}</p></div>
)
export const docks = {
  log: { label: 'Event log', icon: 'log', order: 20, side: true, render: () => <DockPending what="The event log follows the focused run's ledger." /> },
  capture: { label: 'Capture watcher', icon: 'cap', order: 30, side: true, render: () => <DockPending what="The capture watcher shows the session sniffer's status." /> },
}
