/* ====================================================================================
   Rig · views/home.jsx — `home`: the dataset library (every dataset from /api/overview).
   Owner: Phase 1. Kind, stats, search, filter, open, load trajectories.
   Round 3 (breathing): one stacked table — the side "Selected" panel only repeated the row
   it described. Each row carries its models under the name; the mock-control explanation
   lives behind "About". Empty states always offer the way forward.
   ==================================================================================== */
import { useMemo } from 'react'
import { makeSpec } from '../route'
import { useDatasets, short, fmt } from '../data'
import { useRig, useTabState } from '../context'
import { Go, Seg, SearchBox, Tag, Empty, Loading, ErrorState, About, Icon } from '../ui'
import './home.css'

function Home({ spec }) {
  const ds = useDatasets()
  const { openTab } = useRig()
  const [q, setQ] = useTabState(spec, 'q', '')
  const [f, setF] = useTabState(spec, 'f', 'all')
  const all = ds.data || []
  const list = useMemo(() => all
    .filter((r) => (f === 'all' || r.kind === f) && (!q || `${r.name} ${r.models.join(' ')} ${r.harnesses.join(' ')}`.toLowerCase().includes(q.toLowerCase())))
    .sort((a, b) => (a.kind === b.kind ? b.runs - a.runs : a.kind === 'recorded' ? -1 : 1)), [all, f, q])
  if (ds.error) return <ErrorState error={ds.error} onRetry={ds.reload} what="The dataset library" />
  if (!ds.data) return <Loading label="Reading the dataset library (/api/overview)…" />
  const total = all.reduce((a, r) => a + (r.runs || 0), 0)
  const cnt = (k) => all.filter((r) => k === 'all' || r.kind === k).length
  if (!all.length) {
    return (
      <div className="rg-dpad">
        <div className="rg-dh"><div className="t"><div className="rg-eyebrow">Library · data/runs</div><h1>Datasets</h1></div></div>
        <Empty title="No datasets yet." actions={[{ label: 'Load trajectories', spec: 'sources', primary: true }, { label: 'Capture sessions on this machine', spec: 'capture' }]}>
          data/runs on this machine holds no trajectory sets. Import another harness's traces, or capture the coding-agent sessions already on this machine.
        </Empty>
      </div>
    )
  }
  return (
    <div className="rg-dpad">
      <div className="rg-dh">
        <div className="t"><div className="rg-eyebrow">Library · data/runs · {fmt.int(total)} runs</div><h1>Datasets</h1>
          <p>{fmt.plural(all.length, 'trajectory set')}. Open one to see its questions already answered, then follow any number down to the recorded action behind it.</p></div>
        <Go spec="sources" className="rg-btn pri lg" el="load-trajectories-cta">Load trajectories</Go>
      </div>
      <div className="rg-row rg-wrap rg-home-bar">
        <SearchBox value={q} onChange={setQ} placeholder="Find a dataset, model or harness" el="dataset-search" />
        <Seg el="dataset-filter" label="Dataset kind" value={f} onChange={setF}
          options={[['all', `All ${cnt('all')}`], ['recorded', `Recorded agents ${cnt('recorded')}`], ['mock', `Mock controls ${cnt('mock')}`]]} />
      </div>
      <div className="rg-pnl rg-home-list" data-el="dataset-list">
        {list.length ? (
          <div className="rg-tblwrap"><table className="rg-tbl rg-home-tbl">
            <thead><tr><th>dataset</th><th className="rg-num">runs</th><th className="rg-num rg-hide-sm">tasks</th><th className="rg-num rg-hide-sm">harnesses</th><th className="rg-num rg-hide-sm">models</th>
              <th className="rg-num rg-hide-sm" title="pass_rate as indexed by /api/overview: hidden suite, every condition pooled">pass (index)</th><th className="rg-hide-md">updated</th><th><span className="sr-only">Open</span></th></tr></thead>
            <tbody>{list.map((r) => (
              <tr key={r.name} tabIndex={0} className="hov"
                onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) openTab(makeSpec('ds', r.name), { side: e.metaKey || e.ctrlKey }) }}
                onClick={(e) => { if (!e.target.closest('button')) openTab(makeSpec('ds', r.name), { side: e.metaKey || e.ctrlKey }) }}>
                <td className="rg-home-name">
                  <div className="rg-row rg-home-nl"><Go spec={makeSpec('ds', r.name)} className="rg-tlink"><b>{r.name}</b></Go>
                    <Tag tone={r.kind === 'mock' ? 'dash' : undefined} el="dataset-kind">{r.kind === 'mock' ? 'Mock control' : 'Recorded'}</Tag></div>
                  <div className="rg-home-models rg-ell" data-el="dataset-models" title={r.models.join(', ')}>{r.models.map(short).join(' · ')}</div>
                </td>
                <td className="rg-num" data-el="dataset-stats">{fmt.int(r.runs)}</td>
                <td className="rg-num rg-hide-sm">{r.tasks.length}</td><td className="rg-num rg-hide-sm">{r.harnesses.length}</td><td className="rg-num rg-hide-sm">{r.models.length}</td>
                <td className="rg-num rg-hide-sm"><span className="rg-home-pr"><span className="rg-home-prt" aria-hidden="true">{r.pass_rate != null && <i style={{ width: `${Math.max(0, Math.min(1, r.pass_rate)) * 100}%` }} />}</span>{fmt.pct(r.pass_rate)}</span></td>
                <td className="rg-mono rg-small rg-dim rg-hide-md rg-home-date">{String(r.updated || '').slice(0, 10) || 'not recorded'}</td>
                <td className="rg-num rg-home-go"><Go spec={makeSpec('ds', r.name)} className="rg-rowgo" el="dataset-open" label={`Open ${r.name}`}><Icon name="right" size={15} /></Go></td>
              </tr>))}</tbody>
          </table></div>
        ) : (
          <div className="rg-pnl-b">
            <Empty title="No dataset matches." actions={[{ label: 'Clear search & filter', onClick: () => { setQ(''); setF('all') }, primary: true }]}>
              Nothing in the {f === 'all' ? '' : f === 'mock' ? 'mock-control ' : 'recorded '}library matches “{q}”{q ? '' : ' under this filter'}.
            </Empty>
          </div>
        )}
      </div>
      <About summary="About recorded datasets and mock controls" el="dataset-kind-about">
        <p><b>Recorded</b> datasets hold real agent trajectories: each run is a ledger of every model call and tool action, graded by visible, hidden and (where the task has one) strengthened test suites.</p>
        <p><b>Mock controls</b> are scripted agents with designed failure rates. They check the measurement, not a model: all-pass or all-fail on a mock control means the grader broke.</p>
        <p>“pass (index)” is the hidden-suite pass rate the library index reports, pooled over every model and harness in the dataset; open a dataset for rates per condition, with their denominators.</p>
      </About>
    </div>
  )
}

export const views = {
  home: {
    tag: 'lib',
    title: () => 'Datasets',
    crumbs: () => [['datasets']],
    render: Home,
    palette: (env) => [{ title: 'Datasets', detail: `library · ${env.datasets.length || ''} trajectory sets`, spec: 'home' }],
  },
}
