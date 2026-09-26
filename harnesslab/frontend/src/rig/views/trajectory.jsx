/* ====================================================================================
   Rig · views/trajectory.jsx — the trajectory family, on live data. Owner: Phase 2 (trajectory).

     task:<dir>:<model>:<harness>:<task>[:<runIndex>[:<facet>]]   task investigator
     run:<id>[:<seq>]      run ledger: facts, timeline scrubber, events, replay-as-live
     span:<id>:<seq>       one recorded event (model call / tool call / boundary / grade …)
     cmp:<a>:<b>           compare two runs: pair class, first divergence, aligned steps
     fork:<id>[:<seq>]     fork a step — NOT IMPLEMENTED, a proposed capability
     field:<dir>           every run of a dataset: search, scope, verdict + failure-mode filters
     dock `log`            Event log of the focused run

   Data: useRuns (index joined with /api/outcomes), useRunDetail (summary, spans, patch,
   messages, issue), useFindRun (id or 6-hex suffix). Failure findings are DERIVED from the
   ledger spans (finish_reasons, usage, the harness's max_tokens_per_call in the invoke_agent
   start span) — nothing is hard-coded per run. Notes share the classic app's browser-local
   review key (workspaceFacts.reviewKey) and Export review keeps its Markdown format, with the
   failure mode added.
   ==================================================================================== */
import { useEffect, useMemo, useRef, useState } from 'react'
import { makeSpec } from '../route'
import { useDataset, useOverview, useRuns, useOutcomes, useRunDetail, useFindRun, short, sfx, verdict, tally, tokStats, fmt, resolveCondition, paths } from '../data'
import { useRig, useTabState } from '../context'
import { Panel, Loading, ErrorState, Empty, NextSteps, Btn, About, FailureChip, PinButton, AskingSentence, Verdict, VerdictText, OutcomeBar, Tag, Note, Seg, SearchBox, Switch, Go, Icon, Kbd, MOD, failureModeOf, FailureSummary } from '../ui'
import { downloadText, useCaseFile } from '../caseFile'
import { reviewKey, reviewReport } from '../../workspace/workspaceFacts'
import { findings as checkpoints } from '../../directions/investigationFacts'
import './trajectory.css'

const { int, usd, plural } = fmt
const pad2 = (n) => String(n).padStart(2, '0')
const cx = (...a) => a.filter(Boolean).join(' ')
const text = (v) => (typeof v === 'string' ? v : JSON.stringify(v, null, 2))
const tokOf = (r) => (r && r.in != null && r.out != null ? r.in + r.out : null)
const vWord = (v) => (v === true ? 'passed' : v === false ? 'failed' : 'unknown')

/* ================================================================== ledger helpers (pure) */
export const finishOf = (s) => (s && Array.isArray(s['gen_ai.response.finish_reasons']) ? s['gen_ai.response.finish_reasons'] : [])
export const isCutoff = (s) => !!s && s.span === 'chat' && finishOf(s).includes('length')
const callsOf = (s) => (s && Array.isArray(s.tool_calls) ? s.tool_calls : [])
const callName = (t) => (t && (t.name || (t.function && t.function.name))) || 'tool'
const startSpan = (spans) => (spans || []).find((s) => s.span === 'invoke_agent' && s.status === 'start') || null
const endSpan = (spans) => [...(spans || [])].reverse().find((s) => s.span === 'invoke_agent' && s.status === 'end') || null
export const harnessOf = (spans) => { const s = startSpan(spans); return (s && s.harness && typeof s.harness === 'object') ? s.harness : null }
const seedOf = (spans) => { const s = startSpan(spans); return s && s.seed != null ? s.seed : null }
const exitCode = (s) => { const m = /^exit=(-?\d+)/.exec(String((s && s.result_preview) || '')); return m ? +m[1] : null }
function testsPassed(s) {
  if (!s) return null
  if (s.tests_passed != null) return !!s.tests_passed
  if (s['gen_ai.tool.name'] === 'run_tests') { const c = exitCode(s); return c == null ? null : c === 0 }
  return null
}
export function spanKind(s) {
  const k = s && s.span
  return k === 'chat' ? 'chat' : k === 'execute_tool' ? 'tool' : k === 'edit' ? 'edit' : k === 'grade' ? 'grade' : k === 'boundary_event' ? 'bnd' : k === 'sentinel' ? 'sent' : 'agent'
}
const KIND_WORD = { chat: 'model call', tool: 'tool call', edit: 'edit', grade: 'grade', bnd: 'boundary event', sent: 'sentinel', agent: 'run marker' }
const SCRUB_KINDS = ['chat', 'tool', 'edit', 'bnd', 'sent', 'grade', 'agent']
/** seq -> 1-based reply number, for every chat span. */
export function replyNumbers(spans) {
  const m = new Map(); let n = 0
  for (const s of spans || []) if (s.span === 'chat') m.set(s.seq, ++n)
  return m
}
const glyph = (b) => (b === true ? '✓' : b === false ? '×' : '?')
export function spanDesc(s, n) {
  if (!s) return ''
  if (s.span === 'invoke_agent') return s.status === 'start' ? `run started · seed ${s.seed ?? '—'} · harness ${s.harness_hash || '—'}` : `run ended · ${s.exit_reason || '—'} · ${int(s.total_tokens)} tokens`
  if (s.span === 'chat') {
    const tc = callsOf(s).map(callName)
    const head = `reply${n ? ' #' + n : ''}`
    if (tc.length) return `${head} → ${tc.join(', ')}`
    const t = String(s.text || '').trim()
    return `${head} · no tool call${t ? ` · “${t.slice(0, 56)}${t.length > 56 ? '…' : ''}”` : ''}`
  }
  if (s.span === 'execute_tool') {
    const a = s.args || {}
    const arg = a.path || a.command || a.pattern || (a.summary ? String(a.summary).slice(0, 50) + '…' : '')
    const tp = testsPassed(s)
    return `${s['gen_ai.tool.name'] || 'tool'}${arg ? ' ' + String(arg).slice(0, 80) : ''}${tp == null ? '' : tp ? ' · tests pass' : ` · tests fail${exitCode(s) != null ? ' (exit=' + exitCode(s) + ')' : ''}`}${s.status && s.status !== 'ok' ? ' · ' + s.status : ''}`
  }
  if (s.span === 'edit') return `edit ${s.path || '—'} +${s.lines_added ?? 0} −${s.lines_removed ?? 0}`
  if (s.span === 'grade') return `grade · visible ${glyph(s.visible)} hidden ${glyph(s.hidden)} strengthened ${glyph(s.strong)}`
  if (s.span === 'boundary_event') return `boundary · ${s.kind || '—'}${s.tool ? ' · ' + s.tool : ''}${s.status ? ' · ' + s.status : ''}`
  if (s.span === 'sentinel') return `sentinel · risk ${s.risk != null ? (+s.risk).toFixed(2) : '—'} · ${s.action || 'none'}`
  return s.span || 'span'
}
function spanTech(s) {
  if (s.span === 'execute_tool') return 'execute_tool · ' + (s['gen_ai.tool.name'] || '')
  if (s.span === 'chat') return 'chat · ' + (finishOf(s).join(',') || '—')
  return (s.span || '') + (s.status ? ' · ' + s.status : '')
}
const TOOLCODE = { list_files: 'L', read_file: 'R', search: 'F', grep: 'F', write_file: 'W', edit_file: 'E', str_replace: 'E', run_tests: 'T', bash: 'B', submit: 'S' }

/** Attempts of one cell on one task, in repeat order (Run 01 = lowest repeat index). */
export function attemptsOf(runs, model, harness, task) {
  return (runs || []).filter((r) => r.model === model && r.harness === harness && r.task === task)
    .sort((a, b) => (a.rep ?? 1e9) - (b.rep ?? 1e9) || String(a.id).localeCompare(String(b.id)))
}
/** Selected attempt: the explicit index, else the first hidden-suite failure, else Run 01. */
function pickIndex(att, ri, failed = (r) => r.hid === false) {
  const n = Number.parseInt(ri, 10)
  if (Number.isFinite(n) && n >= 0 && n < att.length) return n
  const f = att.findIndex(failed)
  return f >= 0 ? f : 0
}

/* ================================================================== failure finding */
/** A concrete sentence about how a failed run failed, derived from its ledger.
 *  Returns { mode, text, refs: [{seq, label}] } or null when the run did not fail. */
export function failureStory(run, detail, oracle, meta) {
  if (!run || verdict(run, oracle) !== false) return null
  const mode = failureModeOf(run, oracle)
  const spans = (detail && detail.spans) || []
  const summary = (detail && detail.summary) || {}
  const chats = spans.filter((s) => s.span === 'chat')
  const last = chats[chats.length - 1] || null
  const edits = spans.filter((s) => s.span === 'edit')
  const grade = spans.find((s) => s.span === 'grade') || null
  const end = endSpan(spans)
  const exit = (end && end.exit_reason) || run.exit || '—'
  const cfg = harnessOf(spans) || {}
  const refs = []
  const ref = (s, label) => { if (s && s.seq != null) refs.push({ seq: s.seq, label }) }
  const nCalls = chats.length || run.steps || 0
  const tests = spans.filter((s) => s['gen_ai.tool.name'] === 'run_tests')
  const lastTest = tests[tests.length - 1] || null
  let t
  if (!spans.length) {
    t = meta && meta.meaning ? meta.meaning : `Failed the ${oracle} suite.`
    t += ' The ledger for this run is not available, so the finding comes from the run index only.'
    return { mode, text: t, refs }
  }
  if (mode === 'cutoff_no_patch' && last && isCutoff(last)) {
    const n = chats.length
    const out = last['gen_ai.usage.output_tokens']
    const lim = cfg.max_tokens_per_call
    const who = exit === 'no_action' ? 'Run ended by the harness' : `Run ended (${exit})`
    const hit = lim != null && out != null && out >= lim ? `hit the ${int(lim)}-token output limit`
      : lim != null ? `stopped at ${int(out)} output tokens against a ${int(lim)}-token limit`
        : `hit the per-call output limit at ${int(out)} output tokens`
    t = `${who} after reply #${n} ${hit} (finish_reason length)${callsOf(last).length ? '' : ' with no tool call'}; ${edits.length ? `its ${plural(edits.length, 'edit')} left no patch` : 'no edit was made'}, so the hidden suite ran on the original code.`
    const cut = chats.filter(isCutoff).length
    if (cut > 1) t += ` ${cut} of ${n} replies hit the limit.`
    ref(last, `reply #${n} · length`); ref(end, `exit ${exit}`); ref(grade, 'grade')
  } else if (mode === 'wrong_patch') {
    const files = [...new Set(edits.map((e) => e.path).filter(Boolean))]
    const fl = files.length ? files : (summary.files_touched || [])
    const la = summary.lines_added ?? edits.reduce((a, e) => a + (e.lines_added || 0), 0)
    const lr = summary.lines_removed ?? edits.reduce((a, e) => a + (e.lines_removed || 0), 0)
    t = `The agent changed ${fl.length ? fl.join(', ') : 'the code'} (+${la} −${lr}) and the run ended ${exit} after ${plural(nCalls, 'model call')}; the hidden suite failed on that patch${run.vis === true ? ', although the visible suite passed.' : run.vis === false ? ', and the visible suite failed too.' : '.'}`
    if (lastTest) { const p = testsPassed(lastTest); t += ` Its last own test run ${p === true ? 'passed' : p === false ? `failed${exitCode(lastTest) != null ? ' (exit=' + exitCode(lastTest) + ')' : ''}` : 'has no recorded result'}.` }
    else t += ' It never ran the tests itself.'
    if (edits[0]) ref(edits[0], 'first edit')
    if (edits.length > 1) ref(edits[edits.length - 1], 'last edit')
    ref(lastTest, 'last run_tests'); ref(grade, 'grade')
  } else if (mode === 'step_limit_no_patch') {
    t = `The run used ${plural(nCalls, 'model call')}${cfg.max_steps != null ? ` — the harness allows ${int(cfg.max_steps)} steps —` : ''} and ended ${exit} without producing a patch, so the hidden suite ran on the original code.`
    ref(last, `reply #${chats.length}`); ref(end, `exit ${exit}`); ref(grade, 'grade')
  } else if (mode === 'no_patch' || mode === 'cutoff_no_patch') {
    const fr = last ? finishOf(last).join(',') : ''
    t = `The run ended ${exit} after ${plural(nCalls, 'model call')} without producing a patch${last && !callsOf(last).length ? `; reply #${chats.length} ended with finish_reason ${fr || '—'} and no tool call` : ''}, so the hidden suite ran on the original code.`
    ref(last, `reply #${chats.length}`); ref(end, `exit ${exit}`); ref(grade, 'grade')
  } else if (mode === 'harness_error') {
    const e = String(run.error || summary.error || '').trim()
    t = `The run recorded an error${e ? `: “${e.slice(0, 220)}${e.length > 220 ? '…' : ''}”` : ''}. The failure may not be the agent's.`
    ref(end, `exit ${exit}`)
  } else if (mode === 'strengthened_only') {
    t = `Passed the hidden suite, then failed the extra cases of the strengthened suite${grade ? '' : ' (no grade span recorded)'}.`
    ref(grade, 'grade')
  } else {
    t = `Failed the ${oracle} suite; /api/outcomes records no failure mode for this run. It ended ${exit} after ${plural(nCalls, 'model call')} and ${plural(edits.length, 'edit')}.`
    ref(grade, 'grade')
  }
  return { mode, text: t, refs }
}

/* ================================================================== shared hooks */
/** A run by key (full id or suffix) joined with outcomes, plus its detail record. */
function useRunBundle(key) {
  const found = useFindRun(key)
  const ov = useOverview()   // the lookup fails only when the overview does; its reload is the retry
  const f = { ...found, reload: ov.reload }
  const base = f.data ? f.data.run : null
  const R = useRuns(base ? base.ds : null)
  const run = useMemo(() => (base && R.data ? R.data.find((x) => x.id === base.id) || base : base), [base, R.data])
  const det = useRunDetail(base ? base.ds : null, base ? base.id : null)
  return { f, run, det, runs: R.data }
}
function useModeMeta(dir) {
  const out = useOutcomes(dir)
  return out.data ? out.data.byId : {}
}
const runPin = (r, oracle, meta) => {
  const m = failureModeOf(r, oracle)
  return {
    id: `run:${r.id}`, kind: 'run', label: `run ${sfx(r.id)} · ${r.task}`, spec: makeSpec('run', r.id),
    value: `${short(r.model)} · ${r.harness} · ${vWord(verdict(r, oracle))} (${oracle})${m ? ' · ' + ((meta && meta[m] && meta[m].label) || m.replace(/_/g, ' ')) : ''}`,
    dir: r.ds, run: r.id, model: r.model, harness: r.harness, task: r.task,
  }
}
const eventPin = (r, s, n) => ({
  id: `event:${r.id}:${s.seq}`, kind: 'event', label: `${sfx(r.id)} #${s.seq} · ${spanDesc(s, n)}`.slice(0, 120), spec: makeSpec('span', r.id, s.seq),
  value: spanTech(s), dir: r.ds, run: r.id, seq: s.seq, model: r.model, harness: r.harness, task: r.task,
})
const taskSpecOf = (r, idx, facet) => makeSpec('task', r.ds, short(r.model), r.harness, r.task, idx, facet)
/** Task-investigator spec for a run, with its attempt index when the run index is loaded. */
const taskSpecFor = (r, runs, facet) => {
  const i = runs ? attemptsOf(runs, r.model, r.harness, r.task).findIndex((x) => x.id === r.id) : -1
  return taskSpecOf(r, i >= 0 ? i : null, facet)
}

/* ================================================================== small components */
function Ki({ s }) { return <span className={`rg-traj-ki ${spanKind(s)}`} aria-hidden="true" /> }
function Ev({ run, seq, children, title }) {
  return <Go spec={makeSpec('span', run.id, seq)} className="rg-traj-evl" title={title || `open event #${seq}`}>#{seq} {children}</Go>
}
/** Not-found document WITH the ways forward that fit this family (a run that exists, the pair
 *  picker, the dataset's run list) before Search / Datasets / Close. Same look as ui.jsx
 *  NotFound (rg-state-doc), which takes no `actions` — shared-change candidate: `<NotFound actions/>`. */
function Missing({ spec, title, detail, actions = [] }) {
  const { setPalette, state, closeTab } = useRig()
  const what = String(spec || '').split('~')[0]
  let at = null
  ;((state && state.panes) || []).forEach((p, pi) => { const i = p.tabs.indexOf(spec); if (i >= 0 && !at) at = [pi, i] })
  const own = actions.filter(Boolean)
  return (
    <div className="rg-dpad rg-traj-doc"><div className="rg-state-doc" data-el="not-found">
      <div className="rg-eyebrow">Not found · <span className="rg-mono">{what}</span></div>
      <h2>{title}</h2>
      <p className="rg-empty-why">{detail}</p>
      <NextSteps actions={[...own,
        { label: <>Search everything <Kbd>{MOD} K</Kbd></>, onClick: () => setPalette(true), primary: !own.length },
        !own.some((x) => x.spec === 'home') && { label: 'Datasets', spec: 'home' },
        at && { label: 'Close this tab', onClick: () => closeTab(at[0], at[1]) },
      ]} />
    </div></div>
  )
}
/** A run whose ledger.jsonl is absent. Says what is still real and where to go instead. */
function NoLedger({ run, what, actions, runs }) {
  const acts = actions || [
    { label: 'See it beside the other attempts', spec: taskSpecFor(run, runs), primary: true },
    { label: `Every run of ${run.ds}`, spec: makeSpec('field', run.ds) },
  ]
  return (
    <Empty title="No ledger recorded for this run." actions={acts}>The {what} comes from a run's <span className="rg-mono">ledger.jsonl</span>, and <span className="rg-mono">{sfx(run.id)}</span> has none. Its index row (verdict, tokens, calls, exit) is still real, and other attempts of the same task may carry a ledger.</Empty>
  )
}
function DetailGate({ det, run, what, children, actions, runs }) {
  if (det.error) return <ErrorState error={det.error} onRetry={det.reload} what={`The ledger of ${sfx(run.id)}`} />
  if (!det.data) return <Loading label={`Reading /api/results/${run.ds}/runs/${run.id}…`} />
  if (what && !(det.data.spans || []).length) return <NoLedger run={run} what={what} actions={actions} runs={runs} />
  return children
}

/** Expandable event list. `open` = seq expanded (or null). */
function EventList({ run, spans, open, onToggle, label = 'Recorded events', el = 'event-list', max, meta }) {
  const nos = useMemo(() => replyNumbers(spans), [spans])
  const list = max ? spans.slice(0, max) : spans
  return (
    <Panel label={label} meta={meta || `${spans.length} spans · click one to expand`} el={el} flush>
      <div className="rg-traj-evs">
        {list.map((s) => {
          const on = open != null && +open === s.seq
          return (
            <div key={s.seq} className={cx('rg-traj-ev', on && 'on')} data-seq={s.seq}>
              <button type="button" aria-expanded={on} onClick={() => onToggle(on ? null : s.seq)}>
                <span className="no">#{pad2(s.seq)}</span><Ki s={s} />
                <span className="de">{spanDesc(s, nos.get(s.seq))}</span>
                {isCutoff(s) && <Tag tone="red" nc>length</Tag>}
                {s.span === 'boundary_event' && <Tag tone="red" nc>{s.status || 'boundary'}</Tag>}
                <span className="tn rg-hide-sm">{spanTech(s)}</span>
              </button>
              {on && (
                <div className="rg-traj-xp">
                  <div className="rg-row rg-wrap rg-small rg-mono rg-dim" style={{ gap: 12 }}>
                    <span>{s.ts || 'no timestamp'}</span>
                    {s.duration_ms != null && <span>{int(s.duration_ms)} ms</span>}
                    {s['gen_ai.usage.input_tokens'] != null && <span>{int(s['gen_ai.usage.input_tokens'])} in / {int(s['gen_ai.usage.output_tokens'])} out</span>}
                    {s.cost_usd != null && <span>{usd(s.cost_usd, 6)}</span>}
                    {s.span === 'chat' && <span>finish {finishOf(s).join(', ') || '—'}</span>}
                  </div>
                  <pre className="rg-pre">{s.result_preview != null ? text(s.result_preview) : s.text ? s.text : s.args ? text(s.args) : callsOf(s).length ? text(callsOf(s)) : spanDesc(s, nos.get(s.seq))}</pre>
                  <div className="rg-row rg-wrap">
                    <Go spec={makeSpec('span', run.id, s.seq)} className="rg-btn">Open event #{s.seq} <Icon name="right" size={12} /></Go>
                    <PinButton item={eventPin(run, s, nos.get(s.seq))} />
                  </div>
                </div>
              )}
            </div>
          )
        })}
        {max && spans.length > max && <p className="rg-tiny rg-mute rg-traj-pad">{spans.length - max} more events in the run ledger.</p>}
      </div>
    </Panel>
  )
}

/** Timeline scrubber: one block per span placed by recorded time; a slider steps through them. */
function Scrubber({ spans, sel, onSel }) {
  const nos = useMemo(() => replyNumbers(spans), [spans])
  const geo = useMemo(() => {
    const st = spans.map((s) => { const t = Date.parse(s.ts); return Number.isFinite(t) ? t - (s.duration_ms || 0) : NaN })
    const en = spans.map((s) => Date.parse(s.ts))
    const ok = st.every(Number.isFinite) && en.every(Number.isFinite)
    const t0 = ok ? Math.min(...st) : 0, t1 = ok ? Math.max(...en) : spans.length
    const W = Math.max(1, t1 - t0)
    return spans.map((s, i) => ok
      ? { x: ((st[i] - t0) / W) * 100, w: Math.max(0.7, ((s.duration_ms || 0) / W) * 100) }
      : { x: (i / Math.max(1, spans.length)) * 100, w: Math.max(0.7, 100 / Math.max(1, spans.length) - 0.4) })
      .concat([{ t0, t1, ok }])
  }, [spans])
  const meta = geo[geo.length - 1]
  const idx = Math.max(0, spans.findIndex((s) => s.seq === +sel))
  const present = useMemo(() => SCRUB_KINDS.filter((k) => spans.some((s) => spanKind(s) === k)), [spans])
  if (!spans.length) return null
  return (
    <div className="rg-traj-scrub" data-el="timeline-scrubber">
      {/* Two rows: the model's replies on top, what the harness did with them underneath, so the
          waterfall reads as think → act → think even where tool calls are only milliseconds wide. */}
      <div className="rg-traj-lane" role="group" aria-label="Ledger timeline">
        <span className="rg-traj-lane-l m" aria-hidden="true">model</span>
        <span className="rg-traj-lane-l h" aria-hidden="true">harness</span>
        <div className="rg-traj-lane-in">
          {spans.map((s, i) => {
            const k = spanKind(s), w = Math.min(geo[i].w, 100 - geo[i].x)
            const no = nos.get(s.seq)
            return (
              <button key={s.seq} type="button" className={cx('rg-traj-blk', k, k === 'chat' ? 'r1' : 'r2', isCutoff(s) && 'cut', +sel === s.seq && 'on')} style={{ left: `${geo[i].x.toFixed(2)}%`, width: `${w.toFixed(2)}%` }}
                aria-label={`event ${s.seq}: ${spanDesc(s, no)}`} title={`#${s.seq} ${spanDesc(s, no)}`} onClick={() => onSel(s.seq)}>
                {k === 'chat' && w >= 7 && no != null && <span className="t">reply {no}</span>}
              </button>
            )
          })}
        </div>
      </div>
      <input className="rg-traj-range" type="range" min={0} max={spans.length - 1} value={idx} aria-label="Scrub through events"
        onChange={(e) => onSel(spans[+e.target.value].seq)} />
      <div className="rg-row rg-mono rg-tiny rg-mute rg-traj-times">
        <span>{meta.ok ? String(spans[0].ts).slice(11, 19) : 'order only'}</span>
        <span>#{spans[idx].seq} · {KIND_WORD[spanKind(spans[idx])]}{meta.ok ? ` · ${((meta.t1 - meta.t0) / 1000).toFixed(1)} s total` : ''}</span>
        <span>{meta.ok ? String(spans[spans.length - 1].ts).slice(11, 19) : ''}</span>
      </div>
      <div className="rg-traj-scrubkey" aria-hidden="true">
        {present.map((k) => <span key={k}><span className={cx('rg-traj-ki', k)} />{KIND_WORD[k]}</span>)}
        {spans.some(isCutoff) && <span><span className="rg-traj-ki cut" />cut off at the output limit</span>}
      </div>
    </div>
  )
}

/** The concrete failure finding (red note) with its evidence links. */
function WhyFailed({ run, detail, oracle, meta, el = 'failure-finding', chip = true }) {
  const story = detail ? failureStory(run, detail, oracle, meta) : null
  if (!story) return null
  return (
    <div className="rg-traj-why" data-el={el}>
      <div className="rg-row rg-wrap rg-traj-why-h">
        <span className="rg-lbl rg-red">How it failed</span>
        {chip && <FailureChip run={run} />}
      </div>
      <p className="rg-traj-why-t">{story.text}</p>
      {story.refs.length > 0 && <div className="rg-row rg-wrap rg-traj-why-ev"><span className="rg-small rg-mute">Evidence</span>{story.refs.map((r) => <Ev key={r.seq + r.label} run={run} seq={r.seq}>{r.label}</Ev>)}</div>}
    </div>
  )
}

/* ================================================================== task investigator */
const FACETS = [['findings', 'Findings'], ['timeline', 'Timeline'], ['patch', 'Patch'], ['task', 'Task'], ['notes', 'Notes'], ['messages', 'Messages'], ['raw', 'Raw record'], ['compare', 'Compare']]

function readNote(dir, id) {
  try {
    const p = JSON.parse(localStorage.getItem(reviewKey(dir, id)) || 'null')
    return { text: p && typeof p.text === 'string' ? p.text : '', status: p && p.status === 'reviewed' ? 'reviewed' : 'unreviewed', saved: (p && p.saved) || null }
  } catch { return { text: '', status: 'unreviewed', saved: null } }
}
function Notes({ dir, run, onExport }) {
  const [note, setNote] = useState(() => readNote(dir, run.id))
  const [msg, setMsg] = useState('')
  const { toast } = useRig()
  const save = (next = note) => {
    const v = { ...next, saved: new Date().toISOString().slice(0, 16).replace('T', ' ') }
    try { localStorage.setItem(reviewKey(dir, run.id), JSON.stringify(v)); setNote(v); setMsg('Saved in this browser.'); toast('Note saved in this browser') } catch { setMsg('Could not save — browser storage is blocked. Copy or export your note before leaving.') }
  }
  const hint = 'rg-traj-note-hint-' + run.id
  return (
    <div className="rg-traj-notes" data-el="notes">
      <label className="rg-fld rg-traj-note-l" htmlFor={'rg-traj-note-' + run.id}>Your note on run {sfx(run.id)}</label>
      <p className="rg-small rg-dim rg-traj-note-hint" id={hint}>What you observed, the event that supports it, and what is still uncertain.</p>
      <textarea id={'rg-traj-note-' + run.id} className="rg-inp" rows={7} value={note.text} aria-describedby={hint}
        placeholder="e.g. Reply #2 hit the output limit before any edit (event #4). Unsure whether a larger limit would change the outcome."
        onChange={(e) => { setNote({ ...note, text: e.target.value }); setMsg('Unsaved changes.') }} />
      <div className="rg-row rg-wrap rg-traj-note-acts">
        <Switch checked={note.status === 'reviewed'} label="Mark reviewed" onChange={(on) => save({ ...note, status: on ? 'reviewed' : 'unreviewed' })} />
        <span className="rg-grow" />
        <button type="button" className="rg-btn" onClick={() => onExport(note)}>Export review</button>
        <button type="button" className="rg-btn pri" onClick={() => save()}>Save note</button>
      </div>
      <p className="rg-small rg-mute" role="status">{msg || (note.saved ? `Last saved ${note.saved}.` : 'Not saved yet.')} Kept in this browser only.</p>
      <About summary="About notes">
        <p>Notes stay in this browser, one per run. They leave it only through Export review, which downloads a Markdown file with the run's facts, its failure finding and your note. A note does not edit the recording and does not establish a cause.</p>
      </About>
    </div>
  )
}

function Findings({ run, detail, oracle }) {
  const spans = (detail && detail.spans) || []
  const nos = replyNumbers(spans)
  const chats = spans.filter((s) => s.span === 'chat')
  const tools = spans.filter((s) => s.span === 'execute_tool')
  const edits = spans.filter((s) => s.span === 'edit')
  const tests = tools.filter((s) => s['gen_ai.tool.name'] === 'run_tests')
  const grade = spans.find((s) => s.span === 'grade')
  const end = endSpan(spans)
  const firstFail = tests.find((s) => testsPassed(s) === false)
  const firstEdit = edits[0]
  const lastTest = tests[tests.length - 1]
  const bnd = spans.filter((s) => s.span === 'boundary_event')
  const cut = chats.filter(isCutoff)
  const v = verdict(run, oracle)
  const cps = useMemo(() => (detail ? checkpoints(detail) : []), [detail])
  const lines = []
  if (!spans.length) {
    lines.push(<>From the run index: {plural(run.steps ?? 0, 'model call')}, {plural(run.tools ?? 0, 'tool call')}, {plural(run.edits ?? 0, 'edit')}, exited <span className="rg-mono">{run.exit}</span>.</>)
  } else {
    lines.push(<>The agent made <b>{plural(chats.length, 'model call')}</b> and <b>{plural(tools.length, 'tool call')}</b>. {chats[0] && <Ev run={run} seq={chats[0].seq}>first reply</Ev>}</>)
    if (cut.length) lines.push(<><b className="rg-red">{cut.length} of {chats.length} replies</b> ended with finish_reason <span className="rg-mono">length</span> — cut off at the output limit{harnessOf(spans) && harnessOf(spans).max_tokens_per_call != null ? ` (max_tokens_per_call ${int(harnessOf(spans).max_tokens_per_call)})` : ''}. {cut.map((s) => <Ev key={s.seq} run={run} seq={s.seq}>reply #{nos.get(s.seq)}</Ev>)}</>)
    if (firstFail && (!firstEdit || firstFail.seq < firstEdit.seq)) lines.push(<>Its first test run failed before any edit — the bug was reproduced. <Ev run={run} seq={firstFail.seq}>run_tests{exitCode(firstFail) != null ? ` exit=${exitCode(firstFail)}` : ''}</Ev></>)
    if (edits.length) lines.push(<>It edited {edits.map((e, i) => <span key={e.seq}>{i ? ', ' : ''}<span className="rg-mono">{e.path}</span> (+{e.lines_added ?? 0} −{e.lines_removed ?? 0}) <Ev run={run} seq={e.seq}>edit</Ev></span>)}.</>)
    else lines.push(<>It never edited a file. No patch was recorded.</>)
    if (lastTest) lines.push(<>Last test run by the agent: {testsPassed(lastTest) === true ? <span className="rg-sky">passed</span> : testsPassed(lastTest) === false ? <span className="rg-red">failed</span> : <span className="rg-mute">no result</span>} <Ev run={run} seq={lastTest.seq}>run_tests</Ev></>)
    else lines.push(<>The agent never ran the tests itself.</>)
    if (bnd.length) lines.push(<>{plural(bnd.length, 'boundary event')} ({[...new Set(bnd.map((b) => b.kind))].join(', ')}). {bnd.slice(0, 4).map((b) => <Ev key={b.seq} run={run} seq={b.seq}>{b.kind}</Ev>)}</>)
    if (grade) lines.push(<>Grade: visible {glyph(grade.visible)}, hidden {glyph(grade.hidden)}, strengthened {glyph(grade.strong)}; exit <span className="rg-mono">{(end && end.exit_reason) || run.exit}</span>. <Ev run={run} seq={grade.seq}>grade</Ev></>)
  }
  const pc = tools.map((s) => ({ c: TOOLCODE[s['gen_ai.tool.name']] || '?', seq: s.seq, n: s['gen_ai.tool.name'] }))
  const used = [...new Map(pc.map((p) => [p.c, p.n])).entries()]
  return (
    <div data-el="findings" className="rg-traj-facet">
      <section>
        <h3 className="rg-traj-h">What happened, step by step</h3>
        <ol className="rg-traj-fl">{lines.map((l, i) => <li key={i}>{l}</li>)}</ol>
        {v == null && <p className="rg-small rg-mute">The {oracle} suite recorded no result for this run. An unknown grade is not a failure.</p>}
      </section>
      {pc.length > 0 && (
        <section>
          <h3 className="rg-traj-h">Recorded path <span className="rg-traj-h-m">one letter per tool call</span></h3>
          <div className="rg-row rg-wrap rg-traj-pcodes">{pc.map((p) => <Go key={p.seq} spec={makeSpec('span', run.id, p.seq)} className="rg-traj-pcode" title={`#${p.seq} ${p.n}`}>{p.c}</Go>)}</div>
          <p className="rg-tiny rg-mute rg-traj-legend">{used.map(([c, n]) => `${c} ${n}`).join(' · ')}</p>
        </section>
      )}
      {cps.length > 0 && (
        <section data-el="checkpoints">
          <h3 className="rg-traj-h">Recorded checkpoints</h3>
          <ul className="rg-traj-cps">{cps.map((f, i) => { const s = spans[f.index]; return <li key={i}><b>{f.title}</b> {s && <Ev run={run} seq={s.seq}>{s.span}</Ev>}<div className="rg-small rg-dim">{f.detail}</div></li> })}</ul>
          <p className="rg-tiny rg-mute">Each claim links to the span that records it. The checkpoints alone do not establish a root cause.</p>
        </section>
      )}
    </div>
  )
}

/** Why a run has no diff, in plain words, and the event where the story ends.
 *  Derived from the ledger (cut-off reply, end span, edit spans); falls back to the index row. */
export function noPatchWhy(run, detail) {
  const spans = (detail && detail.spans) || []
  const chats = spans.filter((s) => s.span === 'chat')
  const last = chats[chats.length - 1] || null
  const edits = spans.filter((s) => s.span === 'edit')
  const end = endSpan(spans)
  const exit = (end && end.exit_reason) || run.exit || 'an unrecorded exit'
  const lim = (harnessOf(spans) || {}).max_tokens_per_call
  const nEdits = spans.length ? edits.length : (run.edits ?? 0)
  if (last && isCutoff(last) && !edits.length) {
    const n = chats.length
    return {
      title: 'No patch: the run was cut off before it reached an edit.',
      text: `Reply #${n} hit the ${lim != null ? int(lim) + '-token ' : ''}output limit${callsOf(last).length ? '' : ' with no tool call'}, and the harness ended the run (${exit}). The agent never edited a file, so there is nothing to diff and the hidden suite ran on the original code.`,
      stop: last, stopLabel: `Open reply #${n}, where it stopped`,
    }
  }
  if (!nEdits) {
    const at = end || last
    return {
      title: 'No patch: the agent never edited a file.',
      text: `It made ${plural(spans.length ? chats.length : (run.steps ?? 0), 'model call')} and the run ended ${exit} without an edit, so there is nothing to diff and the hidden suite ran on the original code.`,
      stop: at, stopLabel: at ? `Open event #${at.seq}, where it ended` : null,
    }
  }
  return {
    title: 'No patch recorded.',
    text: `The agent made ${plural(nEdits, 'edit')}, but this recording holds no final diff: the edits may have been undone before the run ended (${exit}), or the recorder did not keep the patch. The edit events themselves are in the ledger.`,
    stop: edits[0] || null, stopLabel: edits[0] ? `Open the first edit (#${edits[0].seq})` : null,
  }
}
function PatchView({ run, detail, partner, partnerLabel }) {
  const patch = detail.patch
  const sm = detail.summary || {}
  const { setDock } = useRig()
  if (!patch) {
    const w = noPatchWhy(run, detail)
    return (
      <div data-el="patch-view">
        <Empty title={w.title} actions={[
          w.stop && { label: w.stopLabel, spec: makeSpec('span', run.id, w.stop.seq), primary: true },
          partner && { label: `${partnerLabel} ${sfx(partner.id)}`, spec: makeSpec('cmp', run.id, partner.id) },
          { label: 'Ask Buddy about this run', onClick: () => setDock('buddy') },
        ]}>{w.text}</Empty>
      </div>
    )
  }
  const lines = String(patch).split('\n')
  return (
    <div data-el="patch-view">
      <div className="rg-row rg-wrap rg-traj-bodyh"><h3 className="rg-traj-h">Recorded diff <span className="rg-traj-h-m rg-mono">{(sm.files_touched || []).join(', ') || '—'}</span></h3><span className="rg-grow" />
        <span className="rg-mono rg-small"><span className="rg-sky">+{sm.lines_added ?? '—'}</span> <span className="rg-red">−{sm.lines_removed ?? '—'}</span> · {int(sm.patch_bytes)} bytes</span></div>
      <div className="rg-traj-diff">{lines.map((l, i) => <div key={i} className={l.startsWith('+++') || l.startsWith('---') ? 'rg-traj-dm' : l.startsWith('@@') ? 'rg-traj-dhk' : l.startsWith('+') ? 'rg-traj-da' : l.startsWith('-') ? 'rg-traj-dr' : undefined}>{l || ' '}</div>)}</div>
    </div>
  )
}
function Messages({ detail, actions }) {
  const ms = Array.isArray(detail.messages) ? detail.messages : []
  if (!ms.length) return <div data-el="messages"><Empty title="No conversation captured." actions={actions}>This recording has no messages.json, so the prompt and replies were not saved as a conversation. The ledger still records every reply and tool call in order.</Empty></div>
  return (
    <div data-el="messages"><Panel label="Captured messages" meta={plural(ms.length, 'message')} flush>
      {ms.map((m, i) => (
        <div key={i} className="rg-traj-msg">
          <div className={cx('role', m.role)}>{m.role || 'unknown'}</div>
          <div className="body">{text(m.content ?? '') || <span className="rg-mute">No textual content.</span>}
            {callsOf(m).map((tc, j) => <div key={j} className="tc">→ {callName(tc)}({String(text(tc.arguments ?? (tc.function && tc.function.arguments) ?? '')).slice(0, 200)})</div>)}</div>
        </div>
      ))}
    </Panel></div>
  )
}
function RawRecord({ run, detail }) {
  const [full, setFull] = useState(false)
  const { toast } = useRig()
  const body = detail ? (full ? detail : detail.summary || run.raw) : run.raw
  const json = text(body)
  return (
    <div data-el="raw-record">
      <div className="rg-row rg-wrap rg-traj-bodyh">
        <span className="rg-lbl">{full ? `full recording · /api/results/${run.ds}/runs/${run.id}` : detail ? 'summary record' : 'run index row'}</span><span className="rg-grow" />
        {detail && <Seg label="Record" options={[['s', 'summary'], ['f', 'full recording']]} value={full ? 'f' : 's'} onChange={(v) => setFull(v === 'f')} />}
        <button type="button" className="rg-btn" onClick={() => { try { navigator.clipboard.writeText(json).then(() => toast('Raw record copied'), () => toast('Copy failed — select the text instead')) } catch { toast('Copy failed — select the text instead') } }}>Copy JSON</button>
      </div>
      <pre className="rg-pre rg-traj-raw">{json}</pre>
    </div>
  )
}
function ChooseAttempt({ att, idx, oracle, runs }) {
  const cur = att[idx]
  if (att.length < 2) {
    const cross = candidatesFor(cur, runs, 'cross')
    const pick = cross.find((r) => verdict(r, oracle) != null && verdict(r, oracle) !== verdict(cur, oracle)) || cross[0]
    return (
      <div data-el="choose-attempt">
        <Empty title="No other attempt of this cell." actions={[
          pick && { label: `Compare with ${sfx(pick.id)} under ${pick.harness}`, spec: makeSpec('cmp', cur.id, pick.id), primary: true },
          { label: `Every run of ${cur.ds}`, spec: makeSpec('field', cur.ds) },
        ]}>{short(cur.model)} ran {cur.task} once under {cur.harness}, so there is no repeat to compare with. {pick ? 'The same model ran this task under another harness; that pair is a cross-harness contrast, not run-to-run variance.' : 'Pick any other run from the dataset’s run list instead.'}</Empty>
      </div>
    )
  }
  return (
    <div data-el="choose-attempt">
      <h3 className="rg-traj-h">Compare run {pad2(idx + 1)} with another attempt</h3>
      <p className="rg-small rg-dim rg-traj-lede">Same task, model and harness: any difference between these is run-to-run variance. {MODKEY}-click Compare to open it beside this tab.</p>
      <Panel label="Attempts of this cell" meta={plural(att.length - 1, 'other')} flush>
        <div className="rg-tblwrap"><table className="rg-tbl rg-traj-choose"><thead><tr><th>run</th><th>verdict</th><th className="rg-num rg-hide-sm">tokens</th><th className="rg-num rg-hide-sm">calls</th><th className="rg-hide-sm">exit</th><th /></tr></thead>
          <tbody>{att.map((r, i) => i === idx ? null : (
            <tr key={r.id}><td className="rg-mono">RUN {pad2(i + 1)} <span className="rg-mute rg-small">{sfx(r.id)}</span></td>
              <td><span className="rg-row" style={{ gap: 6 }}><VerdictText v={verdict(r, oracle)} /><FailureChip run={r} /></span></td>
              <td className="rg-num rg-hide-sm">{int(tokOf(r))}</td><td className="rg-num rg-hide-sm">{r.steps ?? '—'}</td><td className="rg-mono rg-small rg-hide-sm">{r.exit}</td>
              <td className="rg-num"><Go spec={makeSpec('cmp', cur.id, r.id)} className="rg-btn">Compare</Go></td></tr>))}</tbody></table></div>
      </Panel>
    </div>
  )
}
const MODKEY = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '') ? '⌘' : 'Ctrl'

function Lesson({ run, detail, att, oracle }) {
  const spans = (detail && detail.spans) || []
  const v = verdict(run, oracle)
  const o = tally(att, oracle)
  const ed = spans.find((s) => s.span === 'edit')
  const gr = spans.find((s) => s.span === 'grade')
  const cut = spans.filter(isCutoff).length
  return (
    <section className="rg-pnl rg-traj-lesson" data-el="lesson-panel">
      <div className="rg-pnl-h"><span className="rg-lbl rg-acc">Lesson · read this attempt</span><span className="meta">Student Lab</span></div>
      <div className="rg-pnl-b rg-grid rg-g3">
        <div><div className="rg-lbl">1 · What happened</div><p className="rg-small">{spans.length ? `${plural(spans.filter((s) => s.span === 'chat').length, 'model call')}, ${ed ? `an edit to ${ed.path}` : 'no edit at all'}${cut ? `, ${cut} cut off at the output limit` : ''}` : `${plural(run.steps ?? 0, 'model call')}, ${plural(run.edits ?? 0, 'edit')}`}; graded {vWord(v)} on the {oracle} suite.</p></div>
        <div><div className="rg-lbl">2 · Investigate</div><p className="rg-small">The same condition passed {o.p} of {o.known} known attempts on this task. {o.p && o.f ? 'Open a passing attempt and find the first step where the two ledgers differ.' : 'Compare its path with a task where attempts disagree.'}</p></div>
        <div><div className="rg-lbl">3 · Why it matters</div><p className="rg-small">A single pass@1 hides this spread. {o.p && o.f ? 'Mixed outcomes under a fixed harness are variance, not a harness effect.' : 'Consistent outcomes are what pass^k rewards.'}</p>
          {(ed || gr) && <Go spec={makeSpec('span', run.id, (ed || gr).seq)} className="rg-btn">Evidence · event #{(ed || gr).seq} →</Go>}</div>
      </div>
    </section>
  )
}

/** One attempt tile: run number and verdict glyph on top, then how it ended, then tokens. */
function AttemptTile({ r, i, on, oracle, onPick }) {
  const rv = verdict(r, oracle)
  const k = rv === true ? 'rg-traj-v-p' : rv === false ? 'rg-traj-v-f' : 'rg-traj-v-u'
  return (
    <button type="button" className={cx('rg-traj-att', k, on && 'on')} aria-pressed={on} aria-label={`Run ${i + 1}, ${vWord(rv)}`} onClick={onPick}>
      <span className="rg-traj-att-h">Run {pad2(i + 1)}<Verdict v={rv} /></span>
      <span className="rg-traj-att-v">{failureModeOf(r, oracle) ? <FailureChip run={r} /> : <span className={cx('rg-small', rv === true ? 'rg-sky' : rv === false ? 'rg-red' : 'rg-mute')}>{rv === true ? 'Passed' : rv === false ? 'Failed' : 'Unknown grade'}</span>}</span>
      <span className="rg-traj-att-t">{int(tokOf(r))} tok{r.hasLedger === false ? ' · no ledger' : ''}</span>
    </button>
  )
}

/** The task list of the task investigator, as it sits in the sidebar under "Trajectories" (the page
 *  itself then needs no second navigation column). args = the task tab's [dir, model, harness, task]. */
export function TaskSideNav({ args }) {
  const [dir, m, h, task] = args
  const { oracle, replaceTab, openTab } = useRig()
  const ds = useDataset(dir)
  const R = useRuns(dir)
  const row = ds.data
  const c = row ? resolveCondition(row, m, h) : null
  const model = c && c.model, harness = c && c.harness
  const cellRuns = useMemo(() => (R.data ? R.data.filter((r) => r.model === model && r.harness === harness) : []), [R.data, model, harness])
  const tasks = useMemo(() => [...new Set(cellRuns.map((r) => r.task))].sort(), [cellRuns])
  const listRef = useRef(null)
  useEffect(() => {
    const on = listRef.current && listRef.current.querySelector('.on')
    if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest' })
  }, [task, tasks.length])
  if (!row || !R.data || !tasks.length) return null
  const b = [dir, short(model), harness]
  const go = (e, s) => (e.metaKey || e.ctrlKey ? openTab(s, { side: true }) : replaceTab(s))
  return (
    <div className="rg-nav-sub rg-nav-tasks" role="group" aria-label="Tasks" data-el="nav-tasks" ref={listRef}>
      <button type="button" className="rg-nav-sub-it rg-nav-sub-all" onClick={(e) => go(e, makeSpec('tasks', ...b))}><span className="lbl">All tasks · {short(model)}</span></button>
      {tasks.map((t) => {
        const oo = tally(attemptsOf(cellRuns, model, harness, t), oracle)
        return (
          <button key={t} type="button" className={cx('rg-nav-sub-it', t === task && 'on')} aria-current={t === task ? 'page' : undefined}
            title={`${t}: ${oo.p} passed, ${oo.f} failed${oo.u ? `, ${oo.u} unknown` : ''}`} onClick={(e) => go(e, makeSpec('task', ...b, t))}>
            <span className="lbl rg-mono">{t}</span>
            <span className={cx('ct', oo.f > 0 && 'bad')}>{oo.p}/{oo.p + oo.f + oo.u}</span>
          </button>
        )
      })}
    </div>
  )
}

function TaskDoc({ spec, args }) {
  const [dir, m, h, task, ri, facet0] = args
  const { oracle, lab, replaceTab, toast } = useRig()
  const ds = useDataset(dir)
  const R = useRuns(dir)
  const ov = useOverview()
  const meta = useModeMeta(dir)
  const row = ds.data
  const c = row ? resolveCondition(row, m, h) : null
  const model = c && c.model, harness = c && c.harness
  const cellRuns = useMemo(() => (R.data ? R.data.filter((r) => r.model === model && r.harness === harness) : []), [R.data, model, harness])
  const tasks = useMemo(() => [...new Set(cellRuns.map((r) => r.task))].sort(), [cellRuns])
  const att = useMemo(() => attemptsOf(cellRuns, model, harness, task), [cellRuns, model, harness, task])
  const idx = pickIndex(att, ri)
  const run = att[idx] || null
  const det = useRunDetail(run ? dir : null, run ? run.id : null)
  const [nq, setNq] = useTabState('tasknav:' + dir, 'q', '')
  const [open, setOpen] = useTabState(spec, 'open', null)
  const facet = FACETS.some(([k]) => k === facet0) ? facet0 : 'findings'
  const navRef = useRef(null)
  useEffect(() => {
    const l = navRef.current
    const on = l && l.querySelector('.rg-traj-nav-it.on')
    if (on && l.scrollWidth > l.clientWidth + 1) l.scrollLeft = Math.max(0, on.offsetLeft - l.offsetLeft - 12)
  }, [task, row, R.data])
  if (ds.missing) return <Missing spec={spec} title={`There is no dataset named ${dir}.`} detail={`Nothing in data/runs on this machine is called ${dir}, so there is no task to investigate. It may have been renamed or not copied here.`} actions={[{ label: 'Choose a dataset', spec: 'home', primary: true }]} />
  if (ds.error || R.error) return <ErrorState error={ds.error || R.error} onRetry={ds.error ? ds.reload : R.reload} what={`The run index of ${dir}`} />
  if (!row || !R.data) return <Loading label={`Reading /api/results/${dir}/runs…`} />
  const b = [dir, short(model), harness]
  if (!run) return (
    <Missing spec={spec} title={`${short(model)} has no attempts at ${task || 'this task'} under ${harness}.`}
      detail={`${dir} records ${plural(tasks.length, 'task')} for this model and harness${tasks.length ? ` (${tasks.slice(0, 4).join(', ')}${tasks.length > 4 ? ', …' : ''})` : ''}, but none called ${task || '(no task named)'}.`}
      actions={[tasks.length && { label: 'All task outcomes of this condition', spec: makeSpec('tasks', ...b), primary: true }, tasks[0] && { label: `Open ${tasks[0]}`, spec: makeSpec('task', ...b, tasks[0]) }, { label: `Every run of ${dir}`, spec: makeSpec('field', dir) }]} />
  )
  const tspec = (i, f = facet) => makeSpec('task', ...b, task, i, f)
  const o = tally(att, oracle)
  const tmeta = ((ov.data && ov.data.tasks) || []).find((t) => t.id === task) || null
  const v = verdict(run, oracle)
  const known = att.filter((r) => r !== run && verdict(r, oracle) != null)
  const partner = (v === false && known.find((r) => verdict(r, oracle) === true)) || (v === true && known.find((r) => verdict(r, oracle) === false)) || known[0] || null
  const partnerLabel = !partner ? null : v === false && verdict(partner, oracle) === true ? 'Compare with passing run' : v === true && verdict(partner, oracle) === false ? 'Compare with failed run' : 'Compare with another run'
  const mode = failureModeOf(run, oracle)
  const nq2 = nq.trim().toLowerCase()
  const navTasks = tasks.filter((t) => !nq2 || t.toLowerCase().includes(nq2) || String((((ov.data && ov.data.tasks) || []).find((x) => x.id === t) || {}).title || '').toLowerCase().includes(nq2))
  const spans = (det.data && det.data.spans) || []
  const doExport = (note = readNote(dir, run.id)) => {
    if (!det.data) return
    let md = reviewReport({ dataset: dir, run: run.raw, record: det.data, note, url: location.href })
    const story = failureStory(run, det.data, oracle, meta)
    if (story) {
      const lab2 = (story.mode && meta[story.mode] && meta[story.mode].label) || (story.mode || 'not recorded').replace(/_/g, ' ')
      md = md.replace(/^(Exit: .*)$/m, `$1\nFailure mode (${oracle} suite): ${lab2}${story.mode && meta[story.mode] ? ' — ' + meta[story.mode].meaning : ''}\nFinding: ${story.text}${story.refs.length ? ' (events ' + story.refs.map((r) => '#' + r.seq).join(', ') + ')' : ''}`)
    }
    const ok = downloadText(`harnesslab-review-${run.id.replace(/[^a-zA-Z0-9_-]/g, '_')}.md`, md, 'text/markdown;charset=utf-8')
    toast(ok ? 'Review exported. Browser-only notes are included.' : 'Export failed: the browser refused the download')
  }
  const copyLink = () => {
    try { navigator.clipboard.writeText(location.href).then(() => toast('Link copied'), () => toast('Copy the address bar to share this view')) } catch { toast('Copy the address bar to share this view') }
  }
  /* ways forward when the selected attempt has no ledger: the index-row findings, a sibling, the dataset */
  const noLedgerActs = [
    facet !== 'findings' && { label: 'Findings from the run index', onClick: () => replaceTab(tspec(idx, 'findings')), primary: true },
    partner && { label: `${partnerLabel} ${sfx(partner.id)}`, spec: makeSpec('cmp', run.id, partner.id), primary: facet === 'findings' },
    { label: 'Raw index row', onClick: () => replaceTab(tspec(idx, 'raw')) },
  ]
  let body
  if (facet === 'findings') body = <DetailGate det={det} run={run}><Findings run={run} detail={det.data} oracle={oracle} /></DetailGate>
  else if (facet === 'timeline') body = <DetailGate det={det} run={run} what="timeline" actions={noLedgerActs}><div data-el="timeline"><EventList run={run} spans={spans} open={open} onToggle={setOpen} label="Recorded events" el="event-list" /></div></DetailGate>
  else if (facet === 'patch') body = <DetailGate det={det} run={run}><PatchView run={run} detail={det.data} partner={partner} partnerLabel={partnerLabel} /></DetailGate>
  else if (facet === 'task') {
    const issue = (det.data && det.data.issue) || (tmeta && tmeta.issue)
    body = (
      <div data-el="task-text">{issue ? <>
        <div className="rg-row rg-wrap rg-traj-bodyh"><h3 className="rg-traj-h">The issue the agent was given</h3><span className="rg-grow" />{tmeta && tmeta.src_files && <span className="rg-mono rg-small rg-mute">src: {tmeta.src_files.join(', ')}</span>}</div>
        <pre className="rg-pre rg-traj-issue">{issue}</pre></> : det.data || det.error
        ? <Empty title="No task text recorded." actions={[{ label: 'What the agent did (Findings)', onClick: () => replaceTab(tspec(idx, 'findings')), primary: true }, { label: 'Raw record', onClick: () => replaceTab(tspec(idx, 'raw')) }]}>Neither this recording nor the benchmark index carries the issue text for <span className="rg-mono">{task}</span>. The agent's own first reply and tool calls show how it read the task.</Empty>
        : <Loading />}</div>
    )
  } else if (facet === 'notes') body = <Notes key={run.id} dir={dir} run={run} onExport={doExport} />
  else if (facet === 'messages') body = <DetailGate det={det} run={run}><Messages detail={det.data} actions={[{ label: 'Replies and tool calls (Timeline)', onClick: () => replaceTab(tspec(idx, 'timeline')), primary: true }, { label: 'Raw record', onClick: () => replaceTab(tspec(idx, 'raw')) }]} /></DetailGate>
  else if (facet === 'raw') body = <RawRecord run={run} detail={det.data} />
  else body = <ChooseAttempt att={att} idx={idx} oracle={oracle} runs={R.data} />
  const exportWhy = det.error ? 'Needs the ledger, which did not load. Retry above.' : 'Waiting for the ledger to load.'
  return (
    <div className="rg-traj-split">
      <aside className="rg-traj-nav" data-el="task-nav" aria-label="Tasks">
        <div className="rg-traj-nav-top">
          <Go spec={makeSpec('tasks', ...b)} className="rg-btn ghost rg-traj-back" el="back-to-tasks">← All task outcomes</Go>
          <div className="rg-traj-nav-q"><SearchBox value={nq} onChange={setNq} placeholder="Filter tasks" /></div>
        </div>
        <div className="rg-lbl rg-traj-nav-sh" title="The task stays selected when you switch model or harness in the sentence above. Run numbers are scoped to each condition.">{plural(tasks.length, 'task')} · {short(model)}</div>
        <div className="rg-traj-nav-list" ref={navRef}>
          {navTasks.map((t) => {
            const l = attemptsOf(cellRuns, model, harness, t)
            const oo = tally(l, oracle)
            return (
              <button key={t} type="button" className={cx('rg-traj-nav-it', t === task && 'on')} aria-current={t === task ? 'page' : undefined} onClick={() => replaceTab(makeSpec('task', ...b, t))}>
                <span className="rg-row"><span className="rg-mono rg-ell rg-grow">{t}</span><span className="n">{oo.p}✓ {oo.f}×{oo.u ? ` ${oo.u}?` : ''}</span></span>
                <OutcomeBar p={oo.p} f={oo.f} u={oo.u} />
              </button>
            )
          })}
          {!navTasks.length && (
            <div className="rg-traj-nav-none">
              <p className="rg-small rg-mute">No task name or title contains “{nq}”.</p>
              <button type="button" className="rg-btn" onClick={() => setNq('')}>Clear filter</button>
            </div>
          )}
        </div>
      </aside>
      <div className="rg-dpad rg-traj-main">
        <div className="rg-traj-lead">
          <AskingSentence dir={dir} model={model} harness={harness} />
          <header className="rg-traj-taskh" data-el="task-heading">
            <div className="rg-eyebrow">Task investigation</div>
            <div className="rg-traj-taskid" data-el="task-details"><h1 className="rg-mono">{task}</h1><span className="rg-small rg-mute">task {tasks.indexOf(task) + 1} of {tasks.length} in {dir}</span></div>
            {tmeta && tmeta.title && <p className="rg-traj-tasktitle">{tmeta.title}</p>}
            <p className="rg-traj-tally">
              <span>{plural(att.length, 'attempt')}: <span className="rg-sky">{o.p} passed</span>, <span className="rg-red">{o.f} failed</span>{o.u ? <>, <span className="rg-mute">{o.u} unknown</span></> : ''}</span>
              {o.p && o.f ? <Tag tone="amb">Mixed outcomes</Tag> : o.f && !o.u ? <Tag tone="red">× all failed</Tag> : o.p && !o.f && !o.u ? <Tag tone="sky">✓ all passed</Tag> : null}
              {tmeta && tmeta.probe && tmeta.probe !== 'none' && <Tag tone="amb" title="probe task">◆ {tmeta.probe.replace(/_/g, ' ')}</Tag>}
              <FailureSummary runs={att} />
            </p>
          </header>
        </div>
        {lab && <Lesson run={run} detail={det.data} att={att} oracle={oracle} />}
        <section className="rg-traj-attsec" aria-label="Attempts">
          <h2 className="rg-traj-sh" title="Run numbers follow the repeat index and are scoped to this model and harness.">Attempts</h2>
          <div className={cx('rg-traj-atts', att.length <= 10 && 'rg-traj-atts-fit')} style={{ '--rig-traj-n': Math.max(4, att.length) }} data-el="attempt-strip" role="group" aria-label="Attempts">
            {att.map((r, i) => <AttemptTile key={r.id} r={r} i={i} on={i === idx} oracle={oracle} onPick={() => replaceTab(tspec(i))} />)}
          </div>
        </section>
        <section className="rg-traj-sel" data-el="run-summary" aria-label="Selected attempt">
          <div className="rg-traj-sel-h">
            <h2 className="rg-traj-sel-t"><Verdict v={v} /><span>Run {pad2(idx + 1)} <span className="rg-mute">of {att.length}</span></span><span className={v === true ? 'rg-sky' : v === false ? 'rg-red' : 'rg-mute'}>{v === true ? 'passed' : v === false ? 'failed' : 'unknown grade'}</span>
              {mode && <span data-el="failure-mode"><FailureChip run={run} /></span>}</h2>
            <span className="rg-traj-acts" data-el="run-actions">
              {partner && <Go spec={makeSpec('cmp', run.id, partner.id)} className="rg-btn pri"><Icon name="cmp" size={13} />{partnerLabel}</Go>}
              <Go spec={makeSpec('run', run.id)} className="rg-btn">Open run ledger</Go>
              <PinButton item={runPin(run, oracle, meta)} />
            </span>
          </div>
          {v === false
            ? (det.data ? <WhyFailed run={run} detail={det.data} oracle={oracle} meta={meta} chip={false} />
              : det.error ? <p className="rg-small rg-traj-verdict-t">The ledger of this run did not load, so how it failed cannot be read from it yet. <button type="button" className="rg-btn" onClick={det.reload}>Retry</button></p>
                : <p className="rg-small rg-mute">Reading the ledger to explain how it failed…</p>)
            : <p className="rg-traj-verdict-t">{v === true ? `Passed the ${oracle} suite.` : `The ${oracle} suite recorded no result for this run. An unknown grade is not a failure.`}{v === true && o.f ? ` ${plural(o.f, 'other attempt')} of this task failed; compare them to see where the paths part.` : ''}</p>}
          <div className="rg-traj-quiet">
            <dl className="rg-traj-qf">
              <div><dt>hidden</dt><dd><VerdictText v={run.hid} words={['passed', 'failed', 'unknown']} /></dd></div>
              <div><dt>visible</dt><dd><VerdictText v={run.vis} words={['passed', 'failed', 'unknown']} /></dd></div>
              <div><dt>tokens</dt><dd className="rg-mono">{int(tokOf(run))}</dd></div>
              <div><dt>model calls</dt><dd className="rg-mono">{run.steps ?? '—'}</dd></div>
              <div><dt>exit</dt><dd className="rg-mono">{run.exit || '—'}</dd></div>
              <div className="rg-traj-qf-id"><dt>run id</dt><dd className="rg-mono rg-traj-id">{run.id}</dd></div>
            </dl>
            <span className="rg-traj-quiet-acts">
              <button type="button" className="rg-btn ghost" onClick={copyLink}>Copy link</button>
              {facet !== 'notes' && <Btn className="ghost" disabled={!det.data} why={exportWhy} onClick={() => doExport()} data-el="export-review">Export review</Btn>}
            </span>
          </div>
        </section>
        <section className="rg-traj-evsec">
          <div className="rg-traj-facets" role="tablist" data-el="inv-tabs" aria-label="Evidence">
            {FACETS.map(([k, l]) => (
              <button key={k} type="button" role="tab" aria-selected={k === facet} className={k === facet ? 'on' : ''} onClick={() => replaceTab(tspec(idx, k))}>
                {l}{k === 'timeline' && det.data && <span className="n">{spans.length}</span>}
              </button>
            ))}
          </div>
          <div className="rg-traj-body" role="tabpanel">{body}</div>
        </section>
      </div>
    </div>
  )
}

/* ================================================================== run document */
function ReplayLanes({ run, spans, onExit }) {
  const [st, setSt] = useTabState('replay:' + run.id, 'st', { i: 1, playing: true })
  const n = Math.min(st.i, spans.length)
  const done = n >= spans.length
  useEffect(() => {
    if (!st.playing || done) return
    const t = setInterval(() => setSt((p) => { const i = Math.min(spans.length, p.i + 1); return { i, playing: i < spans.length } }), 650)
    return () => clearInterval(t)
  }, [st.playing, done, spans.length, setSt])
  const nos = replyNumbers(spans)
  const cur = spans[Math.max(0, n - 1)]
  return (
    <div data-el="live-run" className="rg-traj-live">
      <div className="rg-traj-banner" role="note"><b>REPLAY</b><span>Nothing is running. This is the live two-lane layout replaying the <b>recorded</b> ledger of <span className="rg-mono">{run.id}</span> in its recorded order, one event per tick — not at recorded speed.</span></div>
      <div className="rg-row rg-wrap rg-traj-replayh">
        {st.playing && !done ? <><span className="rg-pulse" aria-hidden="true" /><b className="rg-acc rg-mono">REPLAYING</b></> : done ? <><Verdict v={true} /><b className="rg-mono">REPLAY COMPLETE</b></> : <><span className="rg-dot off" /><b className="rg-mono rg-mute">STOPPED</b></>}
        <span className="rg-mono rg-small" role="status">step {n} / {spans.length}</span>
        <span className="rg-mono rg-small rg-mute rg-hide-sm">recorded {String((cur && cur.ts) || '').slice(11, 23)}</span>
        <span className="rg-grow" />
        {st.playing && !done
          ? <button type="button" className="rg-btn" onClick={() => setSt((p) => ({ ...p, playing: false }))}>Stop</button>
          : <button type="button" className="rg-btn pri" onClick={() => setSt((p) => ({ i: p.i >= spans.length ? 1 : p.i, playing: true }))}>{done ? 'Replay again' : 'Resume'}</button>}
        <button type="button" className="rg-btn ghost" onClick={onExit}>Back to the ledger</button>
      </div>
      <OutcomeBar p={n} f={0} u={0} />
      <div className="rg-traj-lanes">
        <div className="hd" /><div className="hd rg-lbl">Actions · model decisions</div><div className="hd rg-lbl">Effects · tools, edits, grade</div>
        {spans.slice(0, n).map((s, j) => {
          const act = s.span === 'chat' || s.span === 'invoke_agent'
          const cell = (
            <><Ki s={s} /> {spanDesc(s, nos.get(s.seq))}{isCutoff(s) && <> <Tag tone="red" nc>length</Tag></>}
              {s.span === 'execute_tool' && s.result_preview && <div className="rg-tiny rg-mute rg-traj-prev">{String(s.result_preview).slice(0, 140)}{String(s.result_preview).length > 140 ? '…' : ''}</div>}</>
          )
          const nw = j === n - 1 ? 'new' : ''
          return [
            <div key={s.seq + 'n'} className="st">#{pad2(s.seq)}</div>,
            <div key={s.seq + 'a'} className={cx('ce', act && nw)}>{act ? cell : null}</div>,
            <div key={s.seq + 'e'} className={cx('ce', !act && nw)}>{act ? null : cell}</div>,
          ]
        })}
      </div>
    </div>
  )
}

/** Not-found for a run key, shared by run/span/fork/log. */
function NoSuchRun({ spec, id }) {
  return <Missing spec={spec} title={`No run matches “${id}”.`} detail="A run is found by its full id or by the last 6 characters of it, across every dataset in data/runs. Nothing on this machine matches this one. It may belong to a dataset that was not copied here." actions={[{ label: 'Choose a dataset', spec: 'home', primary: true }]} />
}

function RunDoc({ spec, args }) {
  const { oracle } = useRig()
  const listRef = useRef(null)
  const { f, run, det, runs } = useRunBundle(args[0])
  const meta = useModeMeta(run && run.ds)
  const [sel, setSel] = useTabState(spec, 'sel', args[1] != null && args[1] !== '' ? +args[1] : null)
  const [replay, setReplay] = useTabState(spec, 'replay', false)
  if (f.error) return <ErrorState error={f.error} onRetry={f.reload} what="The run lookup" />
  if (f.loading) return <Loading label={`Looking up run ${args[0]} across every dataset…`} />
  if (!run) return <NoSuchRun spec={spec} id={args[0]} />
  const spans = (det.data && det.data.spans) || []
  const att = attemptsOf(runs, run.model, run.harness, run.task)
  const ai = att.findIndex((x) => x.id === run.id)
  const v = verdict(run, oracle)
  const other = att.find((x) => x.id !== run.id && verdict(x, oracle) != null && verdict(x, oracle) !== v) || att.find((x) => x.id !== run.id)
  const cfg = harnessOf(spans)
  const groups = [
    ['Grades', [['hidden', <VerdictText key="h" v={run.hid} />], ['visible', <VerdictText key="v" v={run.vis} />], ['strengthened', <VerdictText key="s" v={run.str} />]]],
    ['Usage', [['tokens in / out', run.in != null ? `${int(run.in)} / ${int(run.out)}` : '—'], ['cost', run.cost != null ? usd(run.cost, 5) : '—'], ['wall', run.wall != null ? (run.wall / 1000).toFixed(1) + ' s' : '—']]],
    ['Behaviour', [['calls · tools · edits', `${run.steps ?? '—'} · ${run.tools ?? '—'} · ${run.edits ?? '—'}`], ['boundary', `${run.bnd ?? 0}${run.bkinds && run.bkinds.length ? ' · ' + run.bkinds.join(', ') : ''}`], ['exit reason', run.exit || '—']]],
    ['Recorded setup', [['seed', det.data ? (seedOf(spans) ?? '—') : '…'], ['max tokens / call', cfg && cfg.max_tokens_per_call != null ? int(cfg.max_tokens_per_call) : '—'], ['max steps', cfg && cfg.max_steps != null ? int(cfg.max_steps) : '—']]],
  ]
  const onSel = (s) => { setSel(s); requestAnimationFrame(() => { const e = listRef.current && listRef.current.querySelector(`.rg-traj-ev[data-seq="${s}"]`); if (e && e.scrollIntoView) e.scrollIntoView({ block: 'nearest' }) }) }
  return (
    <div className="rg-dpad rg-traj-doc rg-traj-stack">
      <header className="rg-dh rg-traj-dh">
        <div className="t"><div className="rg-eyebrow">{run.task} · {short(run.model)} · {run.harness} · run {pad2(ai + 1)} of {att.length || '—'}</div>
          <h1 className="rg-mono rg-row rg-wrap rg-traj-h1"><Verdict v={v} /><span className="rg-traj-id">{run.id}</span><FailureChip run={run} /></h1></div>
        <div className="rg-row rg-wrap">
          {spans.length > 0 && <button type="button" className={cx('rg-btn', replay ? 'pri' : '')} aria-pressed={replay} onClick={() => setReplay(!replay)}>▶ Replay as live</button>}
          {other && <Go spec={makeSpec('cmp', run.id, other.id)} className="rg-btn">Compare with {sfx(other.id)}</Go>}
          <Go spec={taskSpecOf(run, ai >= 0 ? ai : null)} className="rg-btn">Task investigator</Go>
          <PinButton item={runPin(run, oracle, meta)} />
        </div>
      </header>
      {replay && spans.length ? <ReplayLanes run={run} spans={spans} onExit={() => setReplay(false)} /> : (<>
        {v === false && det.data && <WhyFailed run={run} detail={det.data} oracle={oracle} meta={meta} />}
        <section data-el="run-facts" aria-label="Run facts">
          <h2 className="rg-traj-sh">Run facts <span className="rg-traj-h-m">{run.ds} · repeat {run.rep ?? '—'}</span></h2>
          <div className="rg-traj-fgroups">{groups.map(([g, fs]) => (
            <dl key={g} className="rg-traj-fg"><div className="rg-traj-fg-h">{g}</div>{fs.map(([l, x]) => <div key={l} className="rg-traj-fg-r"><dt>{l}</dt><dd className="rg-mono">{x}</dd></div>)}</dl>
          ))}</div>
        </section>
        <DetailGate det={det} run={run} what="event list" runs={runs}>
          <section className="rg-traj-stack-s" aria-label="Recorded events">
            <h2 className="rg-traj-sh">What it did <span className="rg-traj-h-m">{plural(spans.length, 'recorded event')} in ledger order</span></h2>
            <Scrubber spans={spans} sel={sel} onSel={onSel} />
            <div ref={listRef}><EventList run={run} spans={spans} open={sel} onToggle={setSel} label="Events" meta="click one to expand, or scrub the timeline" /></div>
          </section>
        </DetailGate>
      </>)}
    </div>
  )
}
function RunActions({ args }) {
  const { toggleDock, state } = useRig()
  return <button type="button" className={cx('rg-btn ghost', state && state.dock === 'log' && 'on')} onClick={() => toggleDock('log')} title="Event log of the focused run"><Icon name="log" size={13} /><span className="rg-hide-sm">Event log</span></button>
}

/* ================================================================== span document */
function SpanDoc({ spec, args }) {
  const { oracle } = useRig()
  const { f, run, det, runs } = useRunBundle(args[0])
  const { replaceTab } = useRig()
  if (f.error) return <ErrorState error={f.error} onRetry={f.reload} what="The run lookup" />
  if (f.loading) return <Loading label={`Looking up run ${args[0]}…`} />
  if (!run) return <NoSuchRun spec={spec} id={args[0]} />
  if (det.error) return <ErrorState error={det.error} onRetry={det.reload} what={`The ledger of ${sfx(run.id)}`} />
  if (!det.data) return <Loading label={`Reading the ledger of ${run.id}…`} />
  const spans = det.data.spans || []
  if (!spans.length) return <div className="rg-dpad rg-traj-doc"><NoLedger run={run} what="event you asked for" runs={runs} /></div>
  const s = spans.find((x) => String(x.seq) === String(args[1]))
  if (!s) {
    const first = spans[0], last = spans[spans.length - 1]
    return <Missing spec={spec} title={`Run ${sfx(run.id)} has no event #${args[1] ?? ''}.`}
      detail={`Its ledger records ${plural(spans.length, 'event')}, numbered #${first.seq} to #${last.seq}. The link may point past the end of the run, or at a different run.`}
      actions={[{ label: `Open the last event (#${last.seq})`, spec: makeSpec('span', run.id, last.seq), primary: true }, { label: `Open run ${sfx(run.id)}`, spec: makeSpec('run', run.id) }]} />
  }
  const nos = replyNumbers(spans)
  const i = spans.indexOf(s)
  const cnt = (k) => spans.filter((x) => x.span === k).length
  const K = [['chat', 'model call', 'chat'], ['tool', 'tool call', 'execute_tool'], ['bnd', 'boundary event', 'boundary_event'], ['grade', 'grade', 'grade']]
  const cfg = harnessOf(spans) || {}
  const nChat = cnt('chat')
  const fields = []
  if (s.span === 'chat') fields.push(['reply', `#${nos.get(s.seq)} of ${nChat}`], ['tokens in / out', `${int(s['gen_ai.usage.input_tokens'])} / ${int(s['gen_ai.usage.output_tokens'])}`], ['tool calls', callsOf(s).map(callName).join(', ') || 'none'], ['max tokens / call', cfg.max_tokens_per_call != null ? int(cfg.max_tokens_per_call) : '—'], ['cost', usd(s.cost_usd, 6)], ['duration', s.duration_ms != null ? int(s.duration_ms) + ' ms' : '—'], ['model step', s.step ?? '—'])
  if (s.span === 'execute_tool') fields.push(['tool', s['gen_ai.tool.name'] || '—'], ['status', s.status || '—'], ['tests passed', testsPassed(s) == null ? '—' : testsPassed(s) ? '✓ yes' : `× no${exitCode(s) != null ? ' (exit=' + exitCode(s) + ')' : ''}`], ['duration', s.duration_ms != null ? int(s.duration_ms) + ' ms' : '—'])
  if (s.span === 'edit') fields.push(['path', s.path || '—'], ['lines', `+${s.lines_added ?? 0} −${s.lines_removed ?? 0}`], ['via', s.tool || '—'])
  if (s.span === 'grade') fields.push(['visible', s.visible === true ? '✓ pass' : s.visible === false ? '× fail' : '? unknown'], ['hidden', s.hidden === true ? '✓ pass' : s.hidden === false ? '× fail' : '? unknown'], ['strengthened', s.strong === true ? '✓ pass' : s.strong === false ? '× fail' : '? unknown'], ['tests modified', String(s.tests_modified ?? '—')])
  if (s.span === 'boundary_event') fields.push(['boundary kind', s.kind || '—'], ['tool', s.tool || '—'], ['status', s.status || '—'])
  if (s.span === 'invoke_agent') fields.push(['status', s.status || '—'], ...(s.status === 'start' ? [['seed', s.seed ?? '—'], ['harness hash', s.harness_hash || '—'], ['repeat', s.repeat_index ?? '—']] : [['exit', s.exit_reason || '—'], ['total tokens', int(s.total_tokens)], ['cost', usd(s.cost_usd, 6)]]))
  if (s.span === 'sentinel') fields.push(['step', s.step ?? '—'], ['risk', s.risk != null ? (+s.risk).toFixed(3) : '—'], ['action', s.action || 'none'], ['patterns', (s.patterns || []).join(', ') || '—'], ['layer', s.layer || '—'])
  const fr = finishOf(s)
  const cut = isCutoff(s)
  const payload = { ...s }
  if (payload.harness && typeof payload.harness === 'object') payload.harness = `{…harness config, ${Object.keys(payload.harness).length} keys — shown above}`
  const forkable = ['chat', 'execute_tool', 'edit'].includes(s.span)
  const step = (d) => { const j = Math.max(0, Math.min(spans.length - 1, i + d)); replaceTab(makeSpec('span', args[0], spans[j].seq)) }
  return (
    <div className="rg-dpad rg-traj-doc rg-traj-stack">
      <header className="rg-dh rg-traj-dh">
        <div className="t"><div className="rg-eyebrow">{run.task} · {short(run.model)} · {run.harness} · run {sfx(run.id)} · event {i + 1} of {spans.length}</div>
          <h1 className="rg-mono rg-row rg-wrap rg-traj-h1"><Ki s={s} />Event #{s.seq} · {KIND_WORD[spanKind(s)]}</h1></div>
        <div className="rg-row rg-wrap">
          {i > 0 && <button type="button" className="rg-btn" onClick={() => step(-1)}>↑ prev</button>}
          {i < spans.length - 1 && <button type="button" className="rg-btn" onClick={() => step(1)}>↓ next</button>}
          <Go spec={makeSpec('run', run.id)} className="rg-btn">Open run</Go>
          <PinButton item={eventPin(run, s, nos.get(s.seq))} />
        </div>
      </header>
      <div className="rg-traj-spangrid">
        <section className="rg-pnl" data-el="span-detail" aria-label={`Span ${s.seq}`}>
          <div className="rg-pnl-h"><span className="rg-lbl">Span #{s.seq}</span><span className="meta"><Ki s={s} />{s.span}</span></div>
          <div className="rg-pnl-b rg-col rg-traj-spanb">
            <div className="rg-row rg-wrap rg-traj-kinds" role="list" aria-label="Span kinds in this ledger">
              {K.map(([k, l, n]) => <span key={k} role="listitem" className={cx('rg-tag', spanKind(s) === k ? 'acc' : cnt(n) ? '' : 'dash')}><span className={`rg-traj-ki ${k}`} aria-hidden="true" />{l} · {cnt(n)}</span>)}
            </div>
            {!cnt('boundary_event') && <p className="rg-tiny rg-mute rg-traj-m0">No boundary_event span in this ledger (run index: {plural(run.bnd ?? 0, 'boundary event')}). Dashed kinds were not recorded here.</p>}
            {s.span === 'chat' && (
              <div className={cx('rg-traj-finish', cut && 'cut')} data-el="finish-reason">
                <span className="rg-lbl">finish_reason</span>
                <span className="rg-traj-finish-v">{fr.join(', ') || '— not recorded'}</span>
                {cut && <Tag tone="red">cut off at output limit</Tag>}
                {cut && <p className="rg-small rg-traj-m0 rg-traj-full">This reply stopped because it reached the per-call output limit: {int(s['gen_ai.usage.output_tokens'])} output tokens{cfg.max_tokens_per_call != null ? ` against max_tokens_per_call ${int(cfg.max_tokens_per_call)}` : ''}, {callsOf(s).length ? `with ${plural(callsOf(s).length, 'tool call')}` : 'with no tool call'}.{!callsOf(s).length && i === spans.findLastIndex((x) => x.span === 'chat') ? ' It was the last reply of the run.' : ''}</p>}
              </div>
            )}
            <h3 className="rg-mono rg-traj-h3">{spanDesc(s, nos.get(s.seq))}</h3>
            <dl className="rg-kv">{fields.map(([k, x]) => <FragmentKV key={k} k={k} v={x} />)}<dt>ts</dt><dd>{s.ts || '—'}</dd></dl>
            {s.text ? <div><div className="rg-lbl rg-traj-lblgap">model text</div><pre className="rg-pre">{s.text}</pre></div> : s.span === 'chat' ? <p className="rg-tiny rg-mute rg-traj-m0">No model text recorded for this reply.</p> : null}
            {callsOf(s).length > 0 && <div><div className="rg-lbl rg-traj-lblgap">requested tools</div><pre className="rg-pre">{text(callsOf(s))}</pre></div>}
            {s.args && Object.keys(s.args).length > 0 && <div><div className="rg-lbl rg-traj-lblgap">arguments</div><pre className="rg-pre">{text(s.args)}</pre></div>}
            {s.result_preview != null && <div><div className="rg-lbl rg-traj-lblgap">result (preview — the recorder may truncate)</div><pre className="rg-pre">{text(s.result_preview)}</pre></div>}
            {s.harness && typeof s.harness === 'object' && <div><div className="rg-lbl rg-traj-lblgap">recorded harness configuration</div><pre className="rg-pre">{text(s.harness)}</pre></div>}
            <details><summary className="rg-lbl rg-traj-sum-t">payload · raw span</summary><pre className="rg-pre rg-traj-mt">{text(payload)}</pre></details>
            <div className="rg-row rg-wrap">
              {forkable && <Go spec={makeSpec('fork', run.id, s.seq)} className="rg-btn"><Icon name="fork" size={13} />Fork at this step</Go>}
            </div>
          </div>
        </section>
        <Panel label="All events" meta={`${spans.length} spans`} flush className="rg-traj-spanlist">
          <div className="rg-traj-evs">
            {spans.map((x) => (
              <div key={x.seq} className={cx('rg-traj-ev', x.seq === s.seq && 'on')}>
                <button type="button" aria-current={x.seq === s.seq ? 'true' : undefined} onClick={() => replaceTab(makeSpec('span', args[0], x.seq))}>
                  <span className="no">#{pad2(x.seq)}</span><Ki s={x} /><span className="de">{spanDesc(x, nos.get(x.seq))}</span>{isCutoff(x) && <Tag tone="red" nc>length</Tag>}
                </button>
              </div>
            ))}
          </div>
        </Panel>
      </div>
      <p className="rg-small rg-mute">The {oracle} suite graded this run {vWord(verdict(run, oracle)) === 'unknown' ? 'unknown (not a failure)' : vWord(verdict(run, oracle))}.</p>
    </div>
  )
}
function FragmentKV({ k, v }) { return <><dt>{k}</dt><dd>{v}</dd></> }

/* ================================================================== compare */
function stepsOf(spans) {
  return (spans || []).filter((s) => ['chat', 'execute_tool', 'edit'].includes(s.span)).map((s) => {
    if (s.span === 'chat') { const tc = callsOf(s).map(callName); return { key: 'chat:' + tc.join(','), label: tc.length ? 'reply → ' + tc.join(', ') : `reply · no tool call${isCutoff(s) ? ' · length' : ''}`, seq: s.seq } }
    if (s.span === 'edit') return { key: 'edit', label: `edit ${s.path} +${s.lines_added ?? 0} −${s.lines_removed ?? 0}`, seq: s.seq }
    const a = s.args || {}
    const arg = a.path ? `path=${a.path}` : a.command ? `command=${String(a.command).slice(0, 36)}${String(a.command).length > 36 ? '…' : ''}` : ''
    return { key: 'tool:' + s['gen_ai.tool.name'], label: `${s['gen_ai.tool.name']} ${arg}`.trim(), seq: s.seq }
  })
}
export function pairClass(A, B) {
  if (!A || !B) return 'other'
  if (A.ds === B.ds && A.model === B.model && A.harness === B.harness && A.task === B.task) return 'same'
  if (A.ds === B.ds && A.model === B.model && A.task === B.task) return 'cross'
  return 'other'
}
export function pairCounts(runs) {
  const g = {}
  for (const r of runs || []) { const k = r.model + '\u0000' + r.harness + '\u0000' + r.task; g[k] = (g[k] || 0) + 1 }
  let same = 0
  const mt = {}
  for (const k in g) { same += (g[k] * (g[k] - 1)) / 2; const [m, , t] = k.split('\u0000'); (mt[m + '\u0000' + t] = mt[m + '\u0000' + t] || []).push(g[k]) }
  let cross = 0
  for (const k in mt) { const a = mt[k]; for (let i = 0; i < a.length; i++) for (let j = i + 1; j < a.length; j++) cross += a[i] * a[j] }
  return { same, cross }
}
function candidatesFor(A, runs, cls) {
  const l = (runs || []).filter((r) => r.id !== A.id && (cls === 'same'
    ? r.model === A.model && r.harness === A.harness && r.task === A.task
    : r.model === A.model && r.task === A.task && r.harness !== A.harness))
  return l.sort((x, y) => (cls === 'cross' ? ((x.rep === A.rep ? 0 : 1) - (y.rep === A.rep ? 0 : 1)) || x.harness.localeCompare(y.harness) : 0) || (x.rep ?? 0) - (y.rep ?? 0) || x.id.localeCompare(y.id))
}

function PairPicker() {
  const items = useCaseFile()
  const { openTab, setDock, condition, datasets } = useRig()
  const ids = [...new Set(items.filter((x) => (x.kind === 'run' || x.kind === 'event') && x.run).map((x) => x.run))]
  const [pick, setPick] = useState(null)   // null = untouched: two pins are pre-ticked
  const sel = pick ? pick.filter((x) => ids.includes(x)) : ids.length === 2 ? ids : []
  const pair = sel.length === 2 ? sel : null
  const dir = (condition && condition.dir) || (datasets && datasets[0] && datasets[0].name) || null
  const why = ids.length < 2 ? `Pin ${ids.length ? 'one more run' : 'two runs'} first.` : 'Tick two of the pinned runs.'
  return (
    <div className="rg-dpad rg-traj-doc"><div className="rg-narrow rg-traj-stack" data-el="pair-class">
      <header className="rg-dh rg-traj-dh"><div className="t"><div className="rg-eyebrow">Compare</div><h1>Compare two runs</h1>
        <p>Pick two runs from your case file. The comparison shows where their recorded actions first differ.</p></div></header>
      {ids.length ? (
        <section className="rg-traj-stack-s">
          <h2 className="rg-traj-sh">Pinned runs <span className="rg-traj-h-m">{ids.length > 2 ? 'tick two' : plural(ids.length, 'run')}</span></h2>
          <div className="rg-traj-picks">{ids.map((id) => (
            <label key={id} className="rg-row rg-small rg-mono rg-traj-pick"><input type="checkbox" checked={sel.includes(id)} onChange={() => setPick(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id].slice(-2))} />{id}</label>
          ))}</div>
          {ids.length === 1 && <p className="rg-small rg-dim">One run is pinned. Pin a second one (a repeat of the same task works best), or open this run and use its Compare button.</p>}
          <div className="rg-row rg-wrap">
            <Btn className="pri" disabled={!pair} why={why} onClick={() => pair && openTab(makeSpec('cmp', pair[0], pair[1]), { replace: true })}>Compare {pair ? pair.map(sfx).join(' ↔ ') : ''}</Btn>
            {ids.length === 1 && <Go spec={makeSpec('run', ids[0])} className="rg-btn">Open run {sfx(ids[0])}</Go>}
          </div>
        </section>
      ) : (
        <Empty title="No runs pinned yet." actions={[
          dir && { label: `Browse every run of ${dir}`, spec: makeSpec('field', dir), primary: true },
          { label: 'Open the case file', onClick: () => setDock('case') },
        ]}>The pair comes from your case file, which holds no run yet. Pin two runs from any run list or run page, then come back here. Every run page also has its own Compare button.</Empty>
      )}
    </div></div>
  )
}

function CmpHead({ r, spans, tag, oracle }) {
  const st = spans ? stepsOf(spans) : null
  return (
    <section className="rg-pnl rg-traj-head" aria-label={`Run ${tag}`}>
      <div className="rg-pnl-h"><span className={cx('rg-tag', tag === 'A' ? 'acc' : undefined)}>{tag}</span>
        <Go spec={makeSpec('run', r.id)} className="rg-tlink rg-ell"><b>{sfx(r.id)}</b> · {r.harness}</Go>
        <span className="meta"><VerdictText v={verdict(r, oracle)} /></span></div>
      <div className="rg-pnl-b rg-col" style={{ gap: 6 }}>
        <div className="rg-mono rg-small rg-dim rg-ell">{short(r.model)} · {r.task} · repeat {r.rep ?? '—'} · seed {spans ? (seedOf(spans) ?? '—') : '…'}</div>
        <div className="rg-row rg-wrap rg-mono rg-small" style={{ gap: 12 }}>
          <span>{st ? st.length : '—'} actions</span><span>{plural(r.edits ?? 0, 'edit')}</span><span>{r.bnd ? plural(r.bnd, 'boundary event') : 'no boundary events'}</span><span>{int(tokOf(r))} tok</span>
          {spans && spans.some(isCutoff) && <span className="rg-red">{spans.filter(isCutoff).length} cut off</span>}
        </div>
        <div className="rg-row rg-wrap" style={{ gap: 6 }}><FailureChip run={r} /><PinButton compact item={runPin(r, oracle)} /></div>
      </div>
    </section>
  )
}

function CmpDoc({ spec, args }) {
  const { oracle, replaceTab, toast } = useRig()
  const a = useRunBundle(args[0] || null)
  const b = useRunBundle(args[1] || null)
  if (!args[0] || !args[1]) return <PairPicker />
  if (a.f.error || b.f.error) return <ErrorState error={a.f.error || b.f.error} onRetry={a.f.reload} what="The run lookup" />
  if (a.f.loading || b.f.loading) return <Loading label="Looking up both runs…" />
  if (!a.run || !b.run) {
    const have = a.run || b.run, gone = !a.run ? args[0] : args[1]
    const alt = have ? candidatesFor(have, a.runs || b.runs, 'same') : []
    const pick = alt.find((r) => verdict(r, oracle) != null && verdict(r, oracle) !== verdict(have, oracle)) || alt[0]
    return <Missing spec={spec} title={`No run matches “${gone}”, so there is nothing to compare${have ? ` ${sfx(have.id)} with` : ''}.`}
      detail={`A run is found by its full id or its last 6 characters, across every dataset in data/runs.${have ? ` The other side, ${have.id}, exists.` : ''}`}
      actions={[
        have && pick && { label: `Compare ${sfx(have.id)} with ${sfx(pick.id)} instead`, spec: makeSpec('cmp', have.id, pick.id), primary: true },
        have && { label: `Open run ${sfx(have.id)}`, spec: makeSpec('run', have.id) },
        { label: 'Pick a pair from the case file', spec: 'cmp' },
      ]} />
  }
  const A = a.run, B = b.run
  const cls = pairClass(A, B)
  const pc = pairCounts(a.runs)
  const la = a.det.data && a.det.data.spans && a.det.data.spans.length ? a.det.data.spans : null
  const lb = b.det.data && b.det.data.spans && b.det.data.spans.length ? b.det.data.spans : null
  const sa = seedOf(la), sb = seedOf(lb)
  const seedState = la && lb ? (sa != null && sa === sb ? 'same' : 'diff') : 'unknown'
  const pickCls = (k) => {
    if (k === 'seed') { toast(seedState === 'same' ? 'This pair already shares a seed' : 'Seeds are recorded per ledger, not in the run index — no seed-paired search is available'); return }
    const cand = candidatesFor(A, a.runs, k)
    const pref = cand.find((r) => verdict(r, oracle) != null && verdict(r, oracle) !== verdict(A, oracle)) || cand[0]
    if (pref) replaceTab(makeSpec('cmp', A.id, pref.id)); else toast(`No ${k === 'same' ? 'other repeat of this cell' : 'cross-harness partner'} for ${sfx(A.id)}`)
  }
  const cand = cls === 'other' ? [] : candidatesFor(A, a.runs, cls)
  const nx = cand.length ? cand[(cand.findIndex((r) => r.id === B.id) + 1) % cand.length] : null
  let div = null
  if (la && lb) {
    const xa = stepsOf(la), xb = stepsOf(lb)
    const n = Math.max(xa.length, xb.length)
    let first = -1
    const rows = []
    for (let i = 0; i < n; i++) {
      const x = xa[i], y = xb[i]
      const same = !!(x && y && x.key === y.key)
      if (!same && first < 0) first = i
      rows.push(
        <tr key={i} className={!same && i === first ? 'sel' : ''}>
          <td className="rg-num rg-mono">{i + 1}</td>
          <td className="rg-mono rg-small">{x ? <Go spec={makeSpec('span', A.id, x.seq)} className="rg-tlink rg-traj-cell">{x.label}</Go> : <span className="rg-mute">— (ended)</span>}</td>
          <td className="rg-mono rg-small">{y ? <Go spec={makeSpec('span', B.id, y.seq)} className="rg-tlink rg-traj-cell">{y.label}</Go> : <span className="rg-mute">— (ended)</span>}</td>
          <td>{same ? <span className="rg-small rg-mute">= same</span> : <span className="rg-small rg-amb">≠ diverged</span>}</td>
        </tr>,
      )
    }
    const fa = xa[first], fb = xb[first]
    div = (
      <div className="rg-traj-divgrid">
        <div className="rg-traj-stack-s">
          <div className="rg-pnl rg-traj-verdict"><div className="rg-pnl-h"><span className="rg-lbl rg-amb">First divergence</span></div>
            <div className="rg-pnl-b"><div className="rg-fig md amb">{first < 0 ? 'no fork' : 'step ' + (first + 1)}</div>
              <p className="rg-small rg-dim rg-traj-lede">{first < 0 ? 'The two ledgers take the same actions in the same order.' : <>Run A did <span className="rg-mono">{fa ? fa.label : 'nothing (ended)'}</span>, run B did <span className="rg-mono">{fb ? fb.label : 'nothing (ended)'}</span> — the first step where the span kind or tool name differs.</>}</p>
              {fa && <Go spec={makeSpec('fork', A.id, fa.seq)} className="rg-btn">Fork A at this step →</Go>}</div></div>
          <Note><div><b>What this pair does not license.</b> One pair is one anecdote. A claim about {cls === 'same' ? 'run-to-run variance' : 'the difference'} comes from the {int(cls === 'cross' ? pc.cross : pc.same)} pairs of this class read as a paired set, not from this pair.</div></Note>
        </div>
        <Panel label="Aligned steps" meta={`${xa.length} vs ${xb.length} actions`} flush>
          <div className="rg-tblwrap rg-traj-aligned"><table className="rg-tbl"><thead><tr><th className="rg-num">step</th><th>A · {sfx(A.id)}</th><th>B · {sfx(B.id)}</th><th>aligned</th></tr></thead><tbody>{rows}</tbody></table></div>
          <p className="rg-tiny rg-mute rg-traj-pad">Steps align by position; a step matches when its span kind and tool name match, so “same” means the same action, not the same text.</p>
        </Panel>
      </div>
    )
  }
  return (
    <div className="rg-dpad rg-traj-doc rg-traj-stack">
      <div className="rg-traj-stack-s">
      <header className="rg-dh rg-traj-dh"><div className="t"><div className="rg-eyebrow">Compare · the first step where two ledgers differ</div><h1 className="rg-mono">{sfx(A.id)} ↔ {sfx(B.id)}</h1></div></header>
      <div className="rg-row rg-wrap rg-traj-pairrow" data-el="pair-class"><span className="rg-lbl">pair class</span>
        <div className="rg-seg rg-traj-pairseg" role="group" aria-label="Pair class">
          {[['seed', `seed-paired · ${seedState === 'same' ? 'this pair' : seedState === 'diff' ? 'not this pair' : 'checking…'}`], ['same', `repeat of same cell · ${int(pc.same)}`], ['cross', `cross-harness · ${int(pc.cross)}`]].map(([k, l]) => (
            <button key={k} type="button" className={(k === 'seed' ? seedState === 'same' : cls === k) ? 'on' : ''} aria-pressed={k === 'seed' ? seedState === 'same' : cls === k} onClick={() => pickCls(k)}>{l}</button>
          ))}
        </div>
        {cls === 'other' && <Tag tone="dash">this pair: {A.ds !== B.ds ? 'cross-dataset' : A.task !== B.task ? 'different tasks' : 'cross-model'} · outside the paired classes</Tag>}
        <button type="button" className="rg-btn" onClick={() => replaceTab(makeSpec('cmp', B.id, A.id))}>⇄ Swap</button>
        {nx && nx.id !== B.id && <button type="button" className="rg-btn" title={`Next ${cls === 'same' ? 'repeat' : 'harness'} for A: ${sfx(nx.id)}`} onClick={() => replaceTab(makeSpec('cmp', A.id, nx.id))}>Next pair →</button>}
      </div>
      <p className="rg-dim rg-traj-lede">{cls === 'same' ? 'Same task, harness and model, a different repeat: any gap here is run-to-run variance, not the harness.' : cls === 'cross' ? 'Same model and task under two harnesses.' : 'Not a paired design: a descriptive contrast only.'}</p>
      <About summary="How pairs are counted">
        <p>Pair counts are over the {A.ds} run index. Seeds are recorded in each ledger, not in the index, so seed pairing is checked for this pair only{seedState !== 'unknown' ? ` (seeds ${sa ?? '—'} / ${sb ?? '—'})` : ''}. Steps align by position; a step matches when its span kind and tool name match.</p>
      </About>
      </div>
      <div className="rg-traj-heads" data-el="pair-headers"><CmpHead r={A} spans={la} tag="A" oracle={oracle} /><CmpHead r={B} spans={lb} tag="B" oracle={oracle} /></div>
      <div data-el="divergence">
        {a.det.error || b.det.error ? <ErrorState error={a.det.error || b.det.error} what="A ledger" onRetry={() => { a.det.reload(); b.det.reload() }} />
          : !a.det.data || !b.det.data ? <Loading label="Reading both ledgers…" />
            : div || <NoLedger run={la ? B : A} what="aligned step table" runs={a.runs} actions={[
              (la || lb) && { label: `Open run ${sfx((la ? A : B).id)}, which has a ledger`, spec: makeSpec('run', (la ? A : B).id), primary: true },
              nx && nx.id !== B.id && { label: `Try the next pair (${sfx(nx.id)})`, spec: makeSpec('cmp', A.id, nx.id), primary: !(la || lb) },
              { label: `Every run of ${A.ds}`, spec: makeSpec('field', A.ds) },
            ]} />}
      </div>
    </div>
  )
}

/* ================================================================== fork (proposed) */
function ForkDoc({ spec, args }) {
  const { toast, oracle } = useRig()
  const { f, run, det, runs } = useRunBundle(args[0])
  const ds = useDataset(run && run.ds)
  const spans = (det.data && det.data.spans) || []
  const steps = spans.filter((s) => ['chat', 'execute_tool', 'edit'].includes(s.span))
  const asked = args[1] != null && args[1] !== '' ? String(args[1]) : null
  const seq = asked != null && steps.some((s) => String(s.seq) === asked) ? +asked : ((steps.find((s) => s.span === 'execute_tool') || steps[0] || {}).seq ?? null)
  const s = steps.find((x) => x.seq === seq) || null
  const key = `${run ? run.id : ''}:${seq}`
  const [mode, setMode] = useTabState('fork:' + key, 'mode', 'args')
  const [draft, setDraft] = useTabState('fork:' + key, 'draft', null)
  const [hh, setHh] = useTabState('fork:' + key, 'h', null)
  const [mm, setMm] = useTabState('fork:' + key, 'm', null)
  const [reps, setReps] = useTabState('fork:' + key, 'k', 8)
  const { replaceTab } = useRig()
  if (f.error) return <ErrorState error={f.error} onRetry={f.reload} what="The run lookup" />
  if (f.loading) return <Loading label={`Looking up run ${args[0]}…`} />
  if (!run) return <NoSuchRun spec={spec} id={args[0]} />
  if (det.error) return <ErrorState error={det.error} onRetry={det.reload} what={`The ledger of ${sfx(run.id)}`} />
  if (!det.data) return <Loading label={`Reading the ledger of ${run.id}…`} />
  if (!s) return <div className="rg-dpad rg-traj-doc"><NoLedger run={run} what="list of steps to fork" runs={runs} /></div>
  const nos = replyNumbers(spans)
  const recorded = text(s.args || (s.tool_calls ? s.tool_calls : {}))
  const row = ds.data
  const sibs = candidatesFor(run, runs, 'same')
  const sib = sibs.find((r) => verdict(r, oracle) != null && verdict(r, oracle) !== verdict(run, oracle)) || sibs[0]
  return (
    <div className="rg-dpad rg-traj-doc rg-traj-stack" data-el="fork-step">
      <div className="rg-traj-stack-s">
        <div className="rg-traj-banner warn" role="note"><b>NOT IMPLEMENTED · PROPOSED CAPABILITY</b><span>Nothing here runs or is sent anywhere. The page shows what a fork would record, so the design can be discussed.</span></div>
        <header className="rg-dh rg-traj-dh"><div className="t"><div className="rg-eyebrow">Fork · change exactly one step of {sfx(run.id)}</div><h1>Edit the call, keep the history</h1>
          <p>The steps before the edit would be replayed from the recorded ledger; everything after it would be a new live run under the harness you choose.</p></div></header>
        {asked != null && String(seq) !== asked && <Note>Run {sfx(run.id)} has no model call, tool call or edit at #{asked}, so the fork starts at step #{seq} instead. Pick any step on the left.</Note>}
      </div>
      <div className="rg-traj-forkgrid">
        <Panel label="Pick the step to change" meta={`${steps.length} steps`} flush>
          <div className="rg-traj-evs">
            {steps.map((x) => {
              const dropped = x.seq > seq
              return (
                <div key={x.seq} className={cx('rg-traj-ev', x.seq === seq && 'on', dropped && 'dropped')}>
                  <button type="button" aria-current={x.seq === seq ? 'true' : undefined} onClick={() => replaceTab(makeSpec('fork', run.id, x.seq))}>
                    <span className="no">#{pad2(x.seq)}</span><Ki s={x} /><span className="de">{spanDesc(x, nos.get(x.seq))}</span><span className="tn">{dropped ? 'dropped' : x.seq === seq ? 'edit here' : 'replayed'}</span>
                  </button>
                </div>
              )
            })}
          </div>
          <p className="rg-tiny rg-mute rg-traj-pad">Steps after the fork are dropped: the recorded ending is not evidence about a branch the run never took.</p>
        </Panel>
        <div className="rg-traj-stack-s">
          <Panel label={`Step #${seq} · ${KIND_WORD[spanKind(s)]}`} meta="the one step that would change">
            <div className="rg-traj-stack-s">
              <div><div className="rg-lbl rg-traj-lblgap">As recorded</div>
                <pre className="rg-pre">{spanDesc(s, nos.get(s.seq))}{s.result_preview ? '\n→ ' + String(s.result_preview).slice(0, 220) : ''}</pre></div>
              <Seg label="Change" options={[['tool', 'change the tool'], ['args', 'change the arguments'], ['drop', 'drop the step'], ['inject', 'inject a new step']]} value={mode} onChange={setMode} />
              {mode === 'drop'
                ? <p className="rg-small rg-dim">Dropping removes step #{seq}; there is nothing to edit. The run would continue from step #{(steps.find((x) => x.seq > seq) || {}).seq ?? '—'} as if #{seq} never happened.</p>
                : <label className="rg-fld">Your change (held in this tab only, never sent)
                  <textarea className="rg-inp" rows={5} value={draft ?? recorded} onChange={(e) => setDraft(e.target.value)} /></label>}
              <div className="rg-grid rg-g3 rg-traj-forkopts">
                <label className="rg-fld">continue under<select className="rg-selc" value={hh || run.harness} onChange={(e) => setHh(e.target.value)}>{(row ? row.harnesses : [run.harness]).map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
                <label className="rg-fld">model<select className="rg-selc" value={mm || run.model} onChange={(e) => setMm(e.target.value)}>{(row ? row.models : [run.model]).map((x) => <option key={x} value={x}>{short(x)}</option>)}</select></label>
                <label className="rg-fld">repeats<input className="rg-inp" type="number" min={1} max={50} value={reps} onChange={(e) => setReps(Math.max(1, Math.min(50, +e.target.value || 1)))} /></label>
              </div>
              <div className="rg-row"><button type="button" className="rg-btn" onClick={() => { setDraft(null); setMode('args'); setHh(null); setMm(null); setReps(8); toast('Fork draft discarded') }}>Discard draft</button></div>
            </div>
          </Panel>
          <section className="rg-traj-unavail" data-el="fork-unavailable" aria-label="Running a fork">
            <div className="rg-row rg-wrap"><b>Running this fork is not available.</b><Tag tone="red">not implemented · proposed capability</Tag></div>
            <p className="rg-small rg-dim">The backend's <span className="rg-mono">/api/fork</span> routes only compare recorded runs, and the agentlab CLI has no fork runner, so no edited step can be executed yet. The closest real measurements are another recorded repeat of the same cell, or new runs of this condition:</p>
            <NextSteps actions={[
              sib && { label: `Compare with repeat ${sfx(sib.id)}`, spec: makeSpec('cmp', run.id, sib.id), primary: true },
              { label: 'Plan new runs of this condition', spec: makeSpec('an', run.ds, 'setup', short(run.model), run.harness) },
              { label: `Open event #${seq}`, spec: makeSpec('span', run.id, seq) },
            ]} />
          </section>
          <About summary="What a fork could and could not show">
            <ul className="rg-small rg-traj-ul">
              <li>The branch would have its own outcome over its {reps} repeats, with an interval: a real measurement, once the branch actually runs.</li>
              <li>Conduct and trajectory tests would apply to the branch exactly as to any recorded run.</li>
              <li>It is not a paired twin: the prefix is shared by construction, so its fork index is trivially the step you edited.</li>
            </ul>
            <dl className="rg-kv rg-traj-forkkv"><dt>would record to</dt><dd>data/runs/forks/{sfx(run.id)}_s{seq}/ (proposed)</dd><dt>parent</dt><dd>{run.id}</dd><dt>fork_step</dt><dd>{seq}</dd><dt>prefix</dt><dd>replayed · {plural(spans.filter((x) => x.seq < seq).length, 'span')}</dd></dl>
          </About>
        </div>
      </div>
    </div>
  )
}

/* ================================================================== field: every run */
const PAGE = 20
function FieldDoc({ spec, args }) {
  const dir = args[0]
  const { oracle } = useRig()
  const ds = useDataset(dir)
  const R = useRuns(dir)
  const meta = useModeMeta(dir)
  const [q, setQ] = useTabState(spec, 'q', '')
  const [mf, setMf] = useTabState(spec, 'm', 'all')
  const [hf, setHf] = useTabState(spec, 'h', 'all')
  const [vf, setVf] = useTabState(spec, 'v', 'all')
  const [fm, setFm] = useTabState(spec, 'fm', 'all')
  const [pg0, setPg] = useTabState(spec, 'pg', 0)
  const scope = useMemo(() => (R.data || []).filter((r) => (mf === 'all' || r.model === mf) && (hf === 'all' || r.harness === hf)), [R.data, mf, hf])
  const modeCounts = useMemo(() => { const c = {}; for (const r of scope) { const m = failureModeOf(r, oracle); if (m) c[m] = (c[m] || 0) + 1 } return c }, [scope, oracle])
  const list = useMemo(() => {
    const ql = q.toLowerCase().trim()
    return scope.filter((r) => {
      if (ql && !`${r.id} ${r.task} ${r.model} ${r.harness} ${r.exit}`.toLowerCase().includes(ql)) return false
      const v = verdict(r, oracle)
      if (vf !== 'all' && !(vf === 'p' ? v === true : vf === 'f' ? v === false : v == null)) return false
      if (fm !== 'all' && failureModeOf(r, oracle) !== fm) return false
      return true
    })
  }, [scope, q, vf, fm, oracle])
  if (ds.missing) return <Missing spec={spec} title={`There is no dataset named ${dir}.`} detail={`Nothing in data/runs on this machine is called ${dir}, so there are no runs to list. It may have been renamed or not copied here.`} actions={[{ label: 'Choose a dataset', spec: 'home', primary: true }]} />
  if (ds.error || R.error) return <ErrorState error={ds.error || R.error} onRetry={ds.error ? ds.reload : R.reload} what={`The run index of ${dir}`} />
  if (!ds.data || !R.data) return <Loading label={`Reading /api/results/${dir}/runs…`} />
  const row = ds.data
  const o = tally(scope, oracle)
  const tk = tokStats(scope)
  const pages = Math.max(1, Math.ceil(list.length / PAGE))
  const pg = Math.min(pg0, pages - 1)
  const shown = list.slice(pg * PAGE, pg * PAGE + PAGE)
  const reset = (fn) => (v) => { fn(v); setPg(0) }
  const nNoLedger = scope.filter((r) => r.hasLedger === false).length
  const filtered = q || vf !== 'all' || fm !== 'all' || mf !== 'all' || hf !== 'all'
  const clearAll = () => { setQ(''); setVf('all'); setFm('all'); setMf('all'); setHf('all'); setPg(0) }
  const modes = Object.entries(modeCounts).sort((a, b) => b[1] - a[1])
  const label = (m) => (meta[m] && meta[m].label) || (m === 'strengthened_only' ? 'strengthened only' : m.replace(/_/g, ' '))
  return (
    <div className="rg-dpad rg-traj-doc rg-traj-stack">
      <header className="rg-dh rg-traj-dh"><div className="t"><div className="rg-eyebrow">{dir} · recorded trajectories</div><h1>Every run, every span</h1>
        <p>Open any run to follow its outcome into the recorded actions.{nNoLedger ? <> {int(nNoLedger)} of {int(scope.length)} runs in scope have no ledger and are marked <span className="rg-traj-led">no ledger</span>.</> : ''}</p></div></header>
      <div data-el="field-run-list" className="rg-traj-stack-s">
        <div className="rg-row rg-wrap rg-traj-filters">
          <SearchBox value={q} onChange={reset(setQ)} placeholder="Search run id, task, model, harness, exit" el="field-search" />
          <select className="rg-selc" aria-label="Model scope" value={mf} onChange={(e) => reset(setMf)(e.target.value)}><option value="all">All models</option>{row.models.map((m) => <option key={m} value={m}>{short(m)}</option>)}</select>
          <select className="rg-selc" aria-label="Harness scope" value={hf} onChange={(e) => reset(setHf)(e.target.value)}><option value="all">All harnesses</option>{row.harnesses.map((h) => <option key={h} value={h}>{h}</option>)}</select>
          <Seg label="Verdict" options={[['all', 'all'], ['p', '✓ pass'], ['f', '× fail'], ['u', '? unknown']]} value={vf} onChange={reset(setVf)} />
        </div>
        <div className="rg-row rg-wrap rg-mono rg-small rg-traj-totals" data-el="field-totals">
          <span>{int(scope.length)} in scope</span><span className="rg-sky">✓ {int(o.p)} passed</span><span className="rg-red">× {int(o.f)} failed</span><span className="rg-mute">? {int(o.u)} unknown</span>
          <span className="rg-mute">· {oracle} suite · mean {tk.mean == null ? '—' : int(tk.mean)} tok ({tk.rec}/{tk.n} recorded)</span>
        </div>
        {modes.length > 0 && (
          <div className="rg-row rg-wrap rg-traj-mchips" role="group" aria-label="Failure mode filter" data-el="failure-mode-filter">
            <span className="rg-lbl">failure mode</span>
            <button type="button" className={cx('rg-traj-mchip all', fm === 'all' && 'on')} aria-pressed={fm === 'all'} onClick={() => reset(setFm)('all')}>any</button>
            {modes.map(([m, n]) => (
              <button key={m} type="button" className={cx('rg-traj-mchip', m === 'wrong_patch' || m === 'strengthened_only' ? 'wp' : m === 'harness_error' ? 'he' : 'np', fm === m && 'on')} aria-pressed={fm === m} title={(meta[m] && meta[m].meaning) || ''} onClick={() => reset(setFm)(fm === m ? 'all' : m)}>{label(m)} <b>{int(n)}</b></button>
            ))}
          </div>
        )}
        <Panel label="Runs" meta={`${int(list.length)} match`} flush>
          <div className="rg-tblwrap">{shown.length ? (
            <table className="rg-tbl rg-traj-field"><thead><tr><th>run</th><th>task</th><th className="rg-hide-sm">model · harness</th><th>verdict</th><th className="rg-num rg-hide-sm">calls</th><th className="rg-num rg-hide-sm">tokens</th><th className="rg-hide-sm" /></tr></thead>
              <tbody>{shown.map((r) => (
                <tr key={r.id}>
                  <td className="rg-mono rg-small"><Go spec={makeSpec('run', r.id)} className="rg-tlink" title={r.id}><span className="rg-hide-sm rg-mute">{String(r.id).slice(0, -6)}</span>{sfx(r.id) === r.id ? r.id : sfx(r.id)}</Go> {r.hasLedger === false && <span className="rg-traj-led">no ledger</span>}</td>
                  <td className="rg-mono rg-small"><Go spec={taskSpecOf(r)} className="rg-tlink">{r.task}</Go><div className="rg-show-sm rg-tiny rg-mute">{short(r.model)} · {r.harness}</div></td>
                  <td className="rg-mono rg-small rg-hide-sm">{short(r.model)} · {r.harness}</td>
                  <td><span className="rg-row rg-wrap" style={{ gap: 6 }}><VerdictText v={verdict(r, oracle)} /><FailureChip run={r} /></span></td>
                  <td className="rg-num rg-mono rg-hide-sm">{r.steps ?? '—'}</td>
                  <td className="rg-num rg-mono rg-hide-sm">{int(tokOf(r))}</td>
                  <td className="rg-hide-sm"><PinButton compact item={runPin(r, oracle, meta)} /></td>
                </tr>))}</tbody></table>
          ) : <div className="rg-pnl-b"><Empty title="No run matches." actions={filtered ? [{ label: 'Clear search and filters', onClick: clearAll, primary: true }] : [{ label: 'Choose another dataset', spec: 'home', primary: true }]}>
            {scope.length ? <>{int(scope.length)} runs are in scope, but none match{q ? ` “${q}”` : ''}{vf !== 'all' ? ' with this verdict' : ''}{fm !== 'all' ? ` with failure mode ${label(fm)}` : ''}.</> : <>No run of {dir} is recorded{mf !== 'all' ? ` for ${short(mf)}` : ''}{hf !== 'all' ? ` under ${hf}` : ''}.</>}</Empty></div>}</div>
          {list.length > 0 && (
            <div className="rg-row rg-wrap rg-traj-pager" aria-label="Pages">
              <span className="rg-mono rg-small rg-mute">{`${int(pg * PAGE + 1)}–${int(Math.min(list.length, pg * PAGE + PAGE))} of ${int(list.length)}`}</span><span className="rg-grow" />
              {pg > 0 && <button type="button" className="rg-btn" aria-label="First page" onClick={() => setPg(0)}>⇤</button>}
              {pg > 0 && <button type="button" className="rg-btn" onClick={() => setPg(pg - 1)}>← prev</button>}
              {pages > 1 && <span className="rg-mono rg-small">page {pg + 1} / {pages}</span>}
              {pg + 1 < pages && <button type="button" className="rg-btn" onClick={() => setPg(pg + 1)}>next →</button>}
              {pg + 1 < pages && <button type="button" className="rg-btn" aria-label="Last page" onClick={() => setPg(pages - 1)}>⇥</button>}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}

/* ================================================================== event log dock */
function EventLogDock({ ctx }) {
  const key = ctx && ctx.run
  const { f, run, det, runs } = useRunBundle(key || null)
  const { openTab, setPalette, condition, datasets, oracle } = useRig()
  const dir = (ctx && ctx.dir) || (condition && condition.dir) || (datasets && datasets[0] && datasets[0].name) || null
  const R = useRuns(!key ? dir : null)
  if (!key) {
    const fail = R.data ? R.data.find((r) => verdict(r, oracle) === false) : null
    return (
      <div className="rg-traj-logpad" data-el="event-log">
        <Empty title="No run is open in the focused tab." actions={[
          fail && { label: `Open a failed run of ${dir} (${sfx(fail.id)})`, spec: makeSpec('run', fail.id), primary: true },
          dir && { label: `Every run of ${dir}`, spec: makeSpec('field', dir), primary: !fail },
          { label: `Find a run (${MOD} K)`, onClick: () => setPalette(true) },
        ]}>The event log lists the recorded events of whichever run the focused tab is about: a run, one of its events, or an attempt in the task investigator.</Empty>
      </div>
    )
  }
  if (f.error) return <ErrorState error={f.error} onRetry={f.reload} what="The run lookup" />
  if (f.loading) return <Loading label={`Looking up run ${key}…`} />
  if (!run) return (
    <div className="rg-traj-logpad" data-el="event-log">
      <Empty title={`No run matches “${key}”.`} actions={[
        dir && { label: `Every run of ${dir}`, spec: makeSpec('field', dir), primary: true },
        { label: `Find a run (${MOD} K)`, onClick: () => setPalette(true) },
      ]}>The focused tab names a run that is not in any dataset in data/runs on this machine, so there is no ledger to list.</Empty>
    </div>
  )
  if (det.error) return <ErrorState error={det.error} onRetry={det.reload} what={`The ledger of ${sfx(run.id)}`} />
  if (!det.data) return <Loading label={`Reading the ledger of ${run.id}…`} />
  const spans = det.data.spans || []
  const nos = replyNumbers(spans)
  return (
    <div data-el="event-log">
      <div className="rg-row rg-mono rg-small rg-traj-logh"><b className="rg-ell">{run.id}</b><span className="rg-mute">{plural(spans.length, 'span')} · ledger.jsonl</span><span className="rg-grow" /><Go spec={makeSpec('run', run.id)} className="rg-tlink">open run</Go></div>
      {!spans.length ? <div className="rg-traj-logpad"><NoLedger run={run} what="event log" runs={runs} /></div> : spans.map((s) => (
        <button key={s.seq} type="button" className={cx('rg-traj-logit', ctx.seq != null && String(ctx.seq) === String(s.seq) && 'on')} onClick={(e) => openTab(makeSpec('span', run.id, s.seq), { side: e.metaKey || e.ctrlKey })}>
          <span className="rg-mute">#{s.seq}</span><span className="rg-mute rg-hide-sm rg-traj-logts">{String(s.ts || '').slice(11, 23)}</span><Ki s={s} /><span className="rg-ell rg-grow">{spanDesc(s, nos.get(s.seq))}</span>{isCutoff(s) && <Tag tone="red" nc>length</Tag>}
        </button>
      ))}
    </div>
  )
}

/* ================================================================== registration */
/** Raw index rows of a cell's task, from cache (sync; for title/ctx/crumbs). */
function cachedAttempts(env, dir, m, h, task) {
  const row = env.dataset(dir)
  const raw = env.peek(paths.runs(dir))
  if (!row || !Array.isArray(raw)) return null
  const c = resolveCondition(row, m, h)
  return raw.filter((r) => r.model === c.model && r.harness_id === c.harness && r.task_id === task)
    .sort((a, b) => (a.repeat_index ?? 1e9) - (b.repeat_index ?? 1e9) || String(a.run_id).localeCompare(String(b.run_id)))
}
function runCtx(a, env) {
  const r = env.findRun(a[0])
  return r ? { dir: r.ds, model: r.model, harness: r.harness, task: r.task, run: r.id, seq: a[1] ?? null } : { run: a[0], seq: a[1] ?? null }
}
function runCrumbs(a, env, tail) {
  const r = env.findRun(a[0])
  if (!r) return [['run ' + String(a[0] || '').slice(-6)], ...tail]
  const att = cachedAttempts(env, r.ds, r.model, r.harness, r.task)
  const i = att ? att.findIndex((x) => x.run_id === r.id) : -1
  return [[r.ds, makeSpec('field', r.ds)], [r.task, taskSpecOf(r, i >= 0 ? i : null)], ...tail(r)]
}

export const views = {
  task: {
    tag: 'task',
    title: (a, env) => {
      const att = cachedAttempts(env, a[0], a[1], a[2], a[3])
      if (!att || !att.length) return `${a[3] || 'task'} · ${a[1] || ''}`
      return `${a[3]} · run ${pad2(pickIndex(att, a[4], (r) => r.hidden_pass === false) + 1)}`
    },
    ctx: (a, env) => {
      const att = cachedAttempts(env, a[0], a[1], a[2], a[3])
      const r = att && att.length ? att[pickIndex(att, a[4], (x) => x.hidden_pass === false)] : null
      return { dir: a[0], model: a[1] || null, harness: a[2] || null, task: a[3] || null, run: r ? r.run_id : null }
    },
    crumbs: (a) => [[a[0], makeSpec('ds', a[0], a[1], a[2])], ['trajectories', makeSpec('tasks', a[0], a[1], a[2])], [a[3] || 'task']],
    retarget: (a, c) => (c.dir && c.dir !== a[0] ? makeSpec('ds', c.dir, short(c.model), c.harness) : makeSpec('task', c.dir || a[0], short(c.model), c.harness, ...a.slice(3))),
    render: TaskDoc,
  },
  run: {
    tag: 'run',
    title: (a) => `run ${String(a[0] || '').slice(-6)}`,
    ctx: runCtx,
    crumbs: (a, env) => runCrumbs(a, env, (r) => [['run ' + sfx(r.id)]]),
    render: RunDoc,
    Actions: RunActions,
  },
  span: {
    tag: 'evt',
    title: (a) => `${String(a[0] || '').slice(-6)} #${a[1] ?? ''}`,
    ctx: runCtx,
    crumbs: (a, env) => runCrumbs(a, env, (r) => [['run ' + sfx(r.id), makeSpec('run', r.id)], ['#' + (a[1] ?? '')]]),
    render: SpanDoc,
    Actions: RunActions,
  },
  cmp: {
    tag: 'cmp',
    title: (a) => (a[0] && a[1] ? `${String(a[0]).slice(-6)} ↔ ${String(a[1]).slice(-6)}` : 'compare'),
    ctx: (a, env) => { const r = a[0] && env.findRun(a[0]); return r ? { dir: r.ds, model: r.model, harness: r.harness, task: r.task, run: r.id } : {} },
    crumbs: (a, env) => { const r = a[0] && env.findRun(a[0]); return [...(r ? [[r.ds, makeSpec('field', r.ds)]] : []), ['compare'], ...(a[1] ? [[`${String(a[0]).slice(-6)} ↔ ${String(a[1]).slice(-6)}`]] : [])] },
    palette: () => [{ group: 'action', title: 'Compare two runs', detail: 'pair class · first divergence · from pinned runs', spec: 'cmp', keywords: 'pair diff divergence case file' }],
    render: CmpDoc,
  },
  fork: {
    tag: 'fork',
    title: (a) => `fork ${String(a[0] || '').slice(-6)}${a[1] != null ? ' @' + a[1] : ''}`,
    ctx: runCtx,
    crumbs: (a, env) => runCrumbs(a, env, (r) => [['run ' + sfx(r.id), makeSpec('run', r.id)], ['fork' + (a[1] != null ? ' @ step ' + a[1] : '')]]),
    outsideGuided: () => true,
    render: ForkDoc,
  },
  field: {
    tag: 'runs',
    title: (a) => `runs · ${a[0] || ''}`,
    ctx: (a) => ({ dir: a[0] }),
    crumbs: (a) => [[a[0], makeSpec('ds', a[0])], ['recorded trajectories']],
    palette: (env) => env.datasets.map((d) => ({ group: 'dataset', title: `trajectories ${d.name}`, detail: 'field run list · search every run', spec: makeSpec('field', d.name) })),
    render: FieldDoc,
  },
}

export const docks = {
  log: { label: 'Event log', icon: 'log', order: 20, side: true, render: EventLogDock },
}
