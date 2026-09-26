/* ====================================================================================
   Rig · views/buddy.jsx — the Buddy dock tab. Owner: Phase 1.
   Uses the app's real Buddy client (src/workspace/buddyClient.js: buddySettings, buddyEvidence,
   askBuddy, redactBuddy) and its BuddyModelPicker. Evidence to be sent = the case file: the
   first pinned run (and a second pinned run of the same condition and task, as a comparison)
   is packed with the client's own allowlist (buddyEvidence); every other pin travels as the
   label + value it showed when pinned. With no key in this browser Buddy shows the
   not-connected state and never an answer — no canned text, ever.
   ==================================================================================== */
import { useEffect, useMemo, useRef, useState } from 'react'
import { askBuddy, buddyEvidence, buddySettings, redactBuddy } from '../../workspace/buddyClient'
import BuddyModelPicker from '../../workspace/BuddyModelPicker'
import BuddyMessage from '../../workspace/BuddyMessage'
import { browserStores, writeModel } from '../../matekey'
import { useCaseFile } from '../caseFile'
import { useRig } from '../context'
import { useRuns, useRunDetail, short, sfx, tally, verdict } from '../data'
import { Tag, VerdictText, Icon, Btn } from '../ui'
import './dock.css'

/** Build the classic client's `data` / `group` / `run` shapes for one pinned run. */
function conditionShape(allRuns, run) {
  const list = allRuns.filter((r) => r.model === run.model && r.harness === run.harness)
  const t = tally(list, 'hidden')
  const tasks = [...new Set(list.map((r) => r.task))].sort()
  const groups = tasks.map((task) => {
    const rs = list.filter((r) => r.task === task).sort((a, b) => a.rep - b.rep || a.id.localeCompare(b.id))
    const g = tally(rs, 'hidden')
    return { task, runs: rs.map((r, i) => ({ ...r.raw, ordinal: i + 1 })), passed: g.p, failed: g.f, unknown: g.u }
  })
  return { data: { model: run.model, harness: run.harness, total: t.n, passed: t.p, failed: t.f, unknown: t.u, groups }, group: groups.find((g) => g.task === run.task) }
}

function useEvidence(items, ctx) {
  const pinnedRuns = items.filter((x) => x.kind === 'run' && x.run && x.dir)
  const first = pinnedRuns[0] || null
  const second = first ? pinnedRuns.find((x) => x !== first && x.dir === first.dir && x.model === first.model && x.harness === first.harness && x.task === first.task) || null : null
  const dir = first ? first.dir : ctx.dir
  const R = useRuns(first ? first.dir : null)
  const A = useRunDetail(first ? first.dir : null, first ? first.run : null)
  const B = useRunDetail(second ? second.dir : null, second ? second.run : null)
  return useMemo(() => {
    const caseFile = items.map((x) => ({ kind: x.kind, label: x.label, value: x.value || null, dataset: x.dir || null, run: x.run || null, event: x.seq ?? null, note: x.note || null }))
    const base = { case_file: caseFile, limitations: 'Pinned values are what the workbench showed when pinned. Unknown grades are not failures. Recorded observations, not root-cause proof.' }
    if (!first) return { evidence: { scope: { dataset: dir || null, view: 'case file' }, ...base }, runsIncluded: 0, loading: false, error: null }
    if (R.error || A.error || B.error) return { evidence: null, loading: false, error: 'Could not load a pinned recording. Unpin it or try again.' }
    if (!R.data || A.loading || (second && B.loading) || !A.data) return { evidence: null, loading: true, error: null }
    const run = R.data.find((r) => r.id === first.run)
    if (!run) return { evidence: null, loading: false, error: `Pinned run ${first.run} is not in ${first.dir}.` }
    const other = second ? R.data.find((r) => r.id === second.run) : null
    const { data, group } = conditionShape(R.data, run)
    const row = group.runs.find((r) => r.run_id === run.id)
    const otherRow = other ? group.runs.find((r) => r.run_id === other.id) : null
    try {
      const ev = buddyEvidence({ dataset: first.dir, data, scope: { view: 'tasks', task: run.task, run: run.id, compare: otherRow ? other.id : undefined }, group, run: row, other: otherRow, record: A.data, comparison: otherRow ? B.data : null })
      return { evidence: { ...ev, ...base }, runsIncluded: ev.runs.length, loading: false, error: null }
    } catch (e) { return { evidence: null, loading: false, error: e.message } }
  }, [items, dir, first, second, R.data, R.error, A.data, A.loading, A.error, B.data, B.loading, B.error])
}

function BuddyDock({ ctx = {} }) {
  const items = useCaseFile()
  const { openTab, oracle, setDock } = useRig()
  const [settings, setSettings] = useState(() => buddySettings())
  const [question, setQuestion] = useState('')
  const [consent, setConsent] = useState(false)
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const abort = useRef(null)
  useEffect(() => () => abort.current && abort.current.abort(), [])
  useEffect(() => {  // a key saved in Settings (any tab) connects Buddy without a reload
    const on = () => setSettings(buddySettings())
    window.addEventListener('storage', on); window.addEventListener('focus', on)
    return () => { window.removeEventListener('storage', on); window.removeEventListener('focus', on) }
  }, [])
  const ev = useEvidence(items, ctx)
  const R = useRuns(ctx.dir || null)
  const focusRun = ctx.run && R.data ? R.data.find((r) => r.id === ctx.run || sfx(r.id) === ctx.run) : null
  const sugg = focusRun
    ? [`Why did run ${sfx(focusRun.id)} ${verdict(focusRun, oracle) === false ? 'fail' : 'pass'} when other attempts on ${focusRun.task} did not?`, `Is this attempt typical of ${short(focusRun.model)} under ${focusRun.harness}?`, 'What should I inspect next in this ledger?']
    : ctx.model ? [`Which task in ${short(ctx.model)} × ${ctx.harness} has the most mixed outcomes, and why might that be?`, 'Is the gap between models here larger than the interval?', 'What would the strengthened suite change for this condition?']
      : ['Where should I start in this dataset?', 'What does pass^3 mean for a user?', 'Why is an unknown grade never counted as a failure?']
  const chooseModel = (model) => {
    writeModel(browserStores(), model)
    const next = buddySettings()
    if (next.model !== model) { setError('Could not remember this model. Allow browser storage and try again.'); return false }
    setSettings(next); setConsent(false); setError('')
  }
  const send = async (e) => {
    e.preventDefault()
    if (busy || !consent || !ev.evidence || !settings.keyPresent || !question.trim()) return
    const controller = new AbortController(); abort.current = controller
    setBusy(true); setError('')
    const asked = question.trim(), history = messages.map((m) => ({ role: m.role, content: m.content }))
    const timer = setTimeout(() => controller.abort(), 60000)
    try {
      const answer = await askBuddy({ key: settings.key, model: settings.model, evidence: ev.evidence, question: asked, history, signal: controller.signal })
      if (controller.signal.aborted) return
      setMessages((prev) => [...prev, { role: 'user', content: redactBuddy(asked, settings.key) }, { role: 'assistant', content: answer.answer, usage: answer }])
      setQuestion('')
    } catch (err) { if (!controller.signal.aborted) setError(err.message) } finally {
      clearTimeout(timer)
      if (abort.current === controller) { setBusy(false); abort.current = null }
    }
  }
  const task = (focusRun && focusRun.task) || ctx.task
  const runId = focusRun ? focusRun.id : ctx.run
  const askWhy = busy ? null : !settings.keyPresent ? 'Connect a key first (Set up OpenRouter)' : !question.trim() ? 'Type or pick a question first'
    : !consent ? 'Tick the sharing box first' : ev.loading ? 'Loading the pinned recording…' : !ev.evidence ? 'The evidence did not load — see below' : null
  return (
    <div className="rg-bud" data-el="buddy-panel">
      <div>
        <div className="rg-lbl rg-bud-h">Looking at your selection</div>
        {ctx.dir ? (
          <dl className="rg-kv rg-bud-sel">
            <dt>dataset</dt><dd>{ctx.dir}</dd>
            {ctx.model && <><dt>model · harness</dt><dd>{short(ctx.model)} · {ctx.harness}</dd></>}
            {task && <><dt>task</dt><dd>{task}</dd></>}
            {runId && <><dt>run</dt><dd className="rg-mono">{runId}</dd></>}
            {focusRun && <><dt>verdict</dt><dd><VerdictText v={verdict(focusRun, oracle)} /></dd></>}
          </dl>
        ) : (
          <div className="rg-bud-sel">
            <p className="rg-small rg-dim">Nothing in focus yet. Buddy looks at the dataset or run in the focused tab, plus everything in the case file.</p>
            <button type="button" className="rg-btn" onClick={() => openTab('home')}>Open a dataset</button>
          </div>
        )}
        {ctx.dir && !runId && <p className="rg-tiny rg-mute rg-bud-sel">No run in focus — open one to ask about a single attempt.</p>}
        {!settings.keyPresent ? (
          <div className="rg-note red" data-el="buddy-key-state"><span className="rg-dot off" style={{ marginTop: 4 }} />
            <div><b>Not connected.</b> Buddy needs an OpenRouter key saved in this browser. Nothing is sent and no answer is shown until one exists — never a canned one.
              <div className="rg-row rg-wrap" style={{ marginTop: 8 }}><button type="button" className="rg-btn" onClick={() => openTab('settings')}>Set up OpenRouter →</button></div></div></div>
        ) : (
          <div className="rg-note sky" data-el="buddy-key-state"><span className="rg-dot" style={{ marginTop: 4 }} /><div><b>Connected</b> · key {settings.keyHint || 'saved'} in this browser.</div></div>
        )}
      </div>
      <div>
        <div className="rg-lbl rg-bud-h">Ask about the case file</div>
        {messages.length > 0 && (
          <div className="rg-bud-log" role="log" aria-label="Buddy conversation" aria-live="polite">
            {messages.map((m, i) => (
              <article key={i} className={`rg-bud-msg ${m.role}`}><strong>{m.role === 'user' ? 'You' : 'Buddy'}</strong>
                {m.role === 'assistant' ? <BuddyMessage text={m.content} /> : <p>{m.content}</p>}
                {m.usage && <small className="rg-mute">{m.usage.model} · {m.usage.input ?? 'unknown'} in / {m.usage.output ?? 'unknown'} out tokens{m.usage.cost != null ? ` · $${m.usage.cost.toFixed(5)}` : ' · cost not reported'}{m.usage.incomplete ? ' · cut off at the output limit' : ''}</small>}
              </article>))}
          </div>
        )}
        <div className="rg-bud-sugg" data-el="buddy-suggestions">{sugg.map((s) => <button type="button" key={s} onClick={() => setQuestion(s)}>{s}</button>)}</div>
        <form data-el="buddy-input" style={{ marginTop: 8 }} onSubmit={send}>
          <textarea className="rg-inp" rows={2} aria-label="Ask Buddy about the case file" placeholder="Ask about the pinned evidence…" maxLength={2000} value={question} disabled={busy}
            onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send(e) }} />
          {settings.keyPresent && <label className="rg-row rg-small" style={{ margin: '6px 0', alignItems: 'flex-start' }}><input type="checkbox" checked={consent} disabled={busy} onChange={(e) => setConsent(e.target.checked)} /><span>Send this evidence and chat to OpenRouter and its model provider. Charges may apply.</span></label>}
          <div className="rg-row rg-wrap rg-bud-send">
            <div className="rg-bud-picker" data-el="buddy-model-picker"><BuddyModelPicker value={settings.model} onChange={chooseModel} disabled={busy} /></div>
            <span className="rg-grow" />
            {busy && <button type="button" className="rg-btn ghost" onClick={() => { abort.current && abort.current.abort(); setBusy(false) }}>Stop</button>}
            <Btn type="submit" className="pri" disabled={busy || !!askWhy} why={askWhy}>{busy ? 'Asking…' : 'Send question'}</Btn>
          </div>
          {error && <p className="rg-small rg-red" role="alert" style={{ marginTop: 6 }}>{error}</p>}
        </form>
      </div>
      <div>
        <div className="rg-lbl rg-bud-h">Evidence to be sent</div>
        <div className="rg-small" data-el="buddy-evidence">
          <b>{items.length} pinned item{items.length === 1 ? '' : 's'}</b> · {ev.runsIncluded || 0} recording{ev.runsIncluded === 1 ? '' : 's'} with ledger excerpts
          {ev.loading && <div className="rg-tiny rg-mute">loading the pinned recording…</div>}
          {ev.error && <div className="rg-tiny rg-red">{ev.error}</div>}
          <ul className="rg-bud-ev">{items.slice(0, 8).map((x) => <li key={x.id}><Tag>{x.kind}</Tag> <span className="rg-mono rg-tiny">{x.label}</span></li>)}{items.length > 8 && <li className="rg-tiny rg-mute">+ {items.length - 8} more</li>}</ul>
          {!items.length && <p className="rg-tiny rg-mute">The case file is empty: Buddy would see only the dataset in focus. Pin runs, events, cells or figures to give it evidence.</p>}
          <button type="button" className="rg-btn ghost" onClick={() => setDock('case')}><Icon name="case" size={13} /> Open case file</button>
          {ev.evidence && <details className="rg-bud-preview"><summary className="rg-tiny">Preview the exact payload</summary><pre className="rg-pre">{redactBuddy(ev.evidence, settings.key)}</pre></details>}
        </div>
        <p className="rg-tiny rg-mute" style={{ marginTop: 6 }} data-el="buddy-disclaimer">AI interpretation, not recorded fact. Buddy’s words are never written into the ledger, the case file or a report.</p>
      </div>
    </div>
  )
}

export const docks = {
  buddy: { label: 'Buddy', icon: 'buddy', order: 10, side: true, render: BuddyDock },
}
