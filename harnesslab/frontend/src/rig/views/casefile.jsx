/* ====================================================================================
   Rig · views/casefile.jsx — the Case file dock tab. Owner: Phase 1.
   Lists everything pinned (PinButton anywhere), reopens it, takes a note per item, exports a
   Markdown review (Blob download), compares two pinned runs (cmp:<a>:<b>) and forks one
   (fork:<id>, a proposed capability). Buddy's "evidence to be sent" is this list.
   Dead ends closed (round 3): the empty case file offers ways to pin (the focused run itself,
   the focused dataset's questions, its run list); Compare and Fork say what enables them;
   Export ends in a next step (Ask Buddy).
   ==================================================================================== */
import { useState } from 'react'
import { makeSpec, parseSpec } from '../route'
import { useCaseFile, unpin, setNote, clearCase, caseMarkdown, downloadText, storageOk, togglePin } from '../caseFile'
import { useRig } from '../context'
import { useRuns, verdict, short, sfx } from '../data'
import { Icon, Tag, ConfirmAction, Btn, Empty } from '../ui'
import './dock.css'

const KIND_LABEL = { answer: 'answer', figure: 'figure', cell: 'cell', run: 'run', event: 'event' }
const vWord = (v) => (v === true ? 'pass' : v === false ? 'fail' : 'unknown')

/** Ways to put the first thing in the case file, from what the focused tab is about. */
function useFirstPins(ctx) {
  const { oracle, toast, setDock, condition } = useRig()
  const dir = ctx.dir || (condition && condition.dir) || null
  const R = useRuns(ctx.run && ctx.dir ? ctx.dir : null)
  const run = ctx.run && R.data ? R.data.find((r) => r.id === ctx.run || sfx(r.id) === ctx.run) : null
  const acts = []
  if (run) {
    const item = { id: `run:${run.id}`, kind: 'run', label: `run ${sfx(run.id)} · ${run.task}`, spec: makeSpec('run', run.id),
      value: `${short(run.model)} · ${run.harness} · ${vWord(verdict(run, oracle))} (${oracle})`, dir: run.ds, run: run.id, model: run.model, harness: run.harness, task: run.task }
    acts.push({ label: `Pin run ${sfx(run.id)} (in focus)`, primary: true, onClick: () => { togglePin(item); toast(`Pinned ${item.label} to the case file`, { label: 'Ask Buddy about it', onClick: () => setDock('buddy') }) } })
  }
  if (dir) {
    acts.push({ label: `Answer questions about ${dir}`, spec: makeSpec('q', dir), primary: !run })
    acts.push({ label: `Browse ${dir} runs`, spec: makeSpec('field', dir) })
  }
  acts.push({ label: dir ? 'Other datasets' : 'Open a dataset', spec: 'home', primary: !run && !dir })
  return acts
}

function CaseFile({ ctx = {} }) {
  const items = useCaseFile()
  const { openTab, oracle, toast, setDock } = useRig()
  const runs = items.filter((x) => x.kind === 'run' || (x.kind === 'event' && x.run))
  const runIds = [...new Set(runs.map((x) => x.run).filter(Boolean))]
  const [picked, setPicked] = useState([])
  const first = useFirstPins(ctx)
  const sel = picked.filter((id) => runIds.includes(id))
  const pair = sel.length === 2 ? sel : runIds.length === 2 ? runIds : null
  const one = sel.length === 1 ? sel[0] : runIds.length === 1 ? runIds[0] : null
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.filter((x) => runIds.includes(x)), id].slice(-2)))
  const exportMd = () => {
    const ok = downloadText(`case-file-${new Date().toISOString().slice(0, 10)}.md`, caseMarkdown(items, { oracle }))
    toast(ok ? 'Exported the case file as Markdown' : 'Export failed: the browser refused the download', ok ? { label: 'Ask Buddy about it', onClick: () => setDock('buddy') } : null)
  }
  const cmpWhy = runIds.length === 0 ? 'Pin two runs to compare them' : runIds.length === 1 ? 'Pin a second run to compare' : 'Tick two runs below'
  const forkWhy = runIds.length === 0 ? 'Pin a run to fork it' : 'Tick one run below'
  // one plain line instead of a hint beside each disabled button
  const hint = pair && one ? null : runIds.length === 0 ? 'Pin runs to compare or fork them.'
    : runIds.length === 1 ? (one ? 'Pin a second run to compare.' : 'Tick the run to fork it; pin a second run to compare.')
      : pair ? 'Tick one run to fork it.' : 'Tick two runs to compare them, or one to fork it.'
  if (!items.length) {
    return (
      <div className="rg-case" data-el="case-file">
        <div className="rg-case-pad">
          <Empty title="Nothing pinned yet." actions={first}
            about="Use Pin on an answer, a figure, a condition, a run or an event anywhere in the workbench. Pins stay in this browser. Export review writes them as Markdown; Compare opens two pinned runs side by side; Fork starts from one; Buddy sends exactly this list as its evidence.">
            The case file collects the evidence for a review. Pin something to start one.
          </Empty>
          {!storageOk() && <p className="rg-small rg-red">Browser storage is unavailable: pins will last for this visit only.</p>}
        </div>
      </div>
    )
  }
  return (
    <div className="rg-case" data-el="case-file">
      <div className="rg-case-bar">
        <div className="rg-case-row">
        <span className="rg-lbl">{items.length} pinned</span>
        {!storageOk() && <Tag tone="red" nc>kept for this visit only — browser storage is unavailable</Tag>}
        <span className="rg-grow" />
        <Btn onClick={exportMd} data-el="export-review"><Icon name="down" size={14} /> Export review</Btn>
        </div>
        <div className="rg-case-row">
        <Btn disabled={!pair} why={cmpWhy} title={pair ? `Compare ${pair.map((x) => x.slice(-6)).join(' ↔ ')}` : undefined}
          onClick={() => pair && openTab(makeSpec('cmp', pair[0], pair[1]))}><Icon name="cmp" size={14} /> Compare</Btn>
        <Btn disabled={!one} why={forkWhy} title={one ? `Fork run ${one.slice(-6)} (proposed capability)` : undefined}
          onClick={() => one && openTab(makeSpec('fork', one))}><Icon name="fork" size={14} /> Fork</Btn>
        <span className="rg-grow" />
        <ConfirmAction label="Clear" confirmLabel="Clear all pins" danger onConfirm={() => { clearCase(); setPicked([]) }} />
        </div>
        {hint && <p className="rg-case-hint">{hint}</p>}
      </div>
      <ul className="rg-case-list" aria-label="Pinned evidence">
        {items.map((x) => (
          <li key={x.id} className="rg-case-it">
            {x.run && (x.kind === 'run' || x.kind === 'event')
              ? <input type="checkbox" aria-label={`Select ${x.label} for compare or fork`} checked={sel.includes(x.run)} onChange={() => toggle(x.run)} />
              : <span className="rg-case-sp" />}
            <Tag tone={x.kind === 'answer' || x.kind === 'figure' ? 'acc' : undefined}>{KIND_LABEL[x.kind] || x.kind}</Tag>
            <div className="rg-grow">
              {x.spec && parseSpec(x.spec).kind
                ? <button type="button" className="rg-tlink rg-case-open" onClick={(e) => openTab(x.spec, { side: e.metaKey || e.ctrlKey })}>{x.label}</button>
                : <span className="rg-case-open">{x.label}</span>}
              {x.value && <div className="rg-small rg-dim rg-mono">{x.value}</div>}
              <input className="rg-inp rg-case-note" aria-label={`Note on ${x.label}`} placeholder="Add a note…" value={x.note || ''} onChange={(e) => setNote(x.id, e.target.value)} />
            </div>
            <button type="button" className="rg-btn ghost" aria-label={`Unpin ${x.label}`} title="Unpin" onClick={() => unpin(x.id)}>×</button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export const docks = {
  case: { label: 'Case file', icon: 'case', order: 40, side: true, render: CaseFile },
}
