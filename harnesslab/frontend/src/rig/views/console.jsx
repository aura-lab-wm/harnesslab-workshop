/* ====================================================================================
   Rig · views/console.jsx — the run console: one agent run as an ordered list of tool calls
   (left), the failure-risk readout, detectors and counts at the playhead (right), a replay
   button underneath. Same anatomy as the console on traceharness.dev, but every number is
   read from the recorded run:

     rows        the ledger's execute_tool spans in order (+ a row for a model reply that made
                 no tool call — a cut-off reply is evidence, so it is kept)
     risk        GET /api/results/<dir>/runs/<id> .replay — one entry per MODEL CALL; a row
                 shows the risk of the reply that issued it
     detectors   the patterns on those replay entries; names, severity and nudge text come
                 from GET /api/sentinel .patterns. A live run that recorded a sentinel verdict
                 in its ledger (replay[k].recorded) shows THAT verdict, labelled "recorded";
                 otherwise the card says "replayed" — what Sentinel would have said.
     threshold   GET /api/sentinel .default_config.threshold (the run's own harness config wins)

   Colour comes only from var(--rig-*) tokens (console.css). Nothing here is invented: an
   absent replay is an empty rail, not a made-up curve.
   ==================================================================================== */
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { makeSpec } from '../route'
import { useApi, useSentinel, short, sfx, fmt } from '../data'
import { Go, Icon, PinButton } from '../ui'
import { replyNumbers, spanDesc, spanKind, finishOf, harnessOf, eventPin, TOOLCODE } from './trajectory'
import './console.css'

const { int } = fmt
const cx = (...a) => a.filter(Boolean).join(' ')
const pad2 = (n) => String(n).padStart(2, '0')
const asText = (v) => (typeof v === 'string' ? v : JSON.stringify(v, null, 2))
const callsOf = (s) => (s && Array.isArray(s.tool_calls) ? s.tool_calls : [])
const toolOf = (s) => (s && s['gen_ai.tool.name']) || 'tool'
const flat = (v, n) => { const t = String(v == null ? '' : v).replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t }
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** kind class of a tool name: i = look (read/list/search), e = change, t = tests, b = shell, s = submit */
const KIND_OF = { R: 'i', L: 'i', F: 'i', W: 'e', E: 'e', T: 't', B: 'b', S: 's' }

/** "3 failed" / "5 passed" from a run_tests observation; null when the text does not say. */
export function testResult(s) {
  const p = String((s && s.result_preview) || '')
  let m = /(\d+) failed/.exec(p)
  if (m && +m[1] > 0) return { tone: 'bad', text: `${m[1]} failed` }
  const zeroFailed = !!m                       // "0 failed": not a failure — fall through to the passed count
  m = /FAILED \(([^)]*)\)/.exec(p)
  if (m) {
    const n = (/failures=(\d+)/.exec(m[1]) || [0, 0])[1] * 1 + (/errors=(\d+)/.exec(m[1]) || [0, 0])[1] * 1
    return { tone: 'bad', text: n ? `${n} failed` : 'failed' }
  }
  m = /(\d+) passed/.exec(p)
  if (m) return { tone: 'ok', text: `${m[1]} passed` }
  m = /Ran (\d+) tests?[\s\S]*\bOK\b/.exec(p)
  if (m) return { tone: 'ok', text: `${m[1]} passed` }
  if (s && s.tests_passed != null) return s.tests_passed ? { tone: 'ok', text: 'passed' } : { tone: 'bad', text: 'failed' }
  const e = /^exit=(-?\d+)/.exec(p)
  if (e) return +e[1] === 0 ? { tone: 'ok', text: 'passed' } : { tone: 'bad', text: 'failed' }
  return zeroFailed ? { tone: 'ok', text: 'passed' } : null
}

/** An edit span's `+a −r` delta, or '' when the span does not carry counts (never an invented +0 −0).
 *  A 0/0 pair is dropped too: the nebius importer (backend/real_import.py) writes 0/0 as a placeholder
 *  for every SWE-agent edit, and a real no-op edit has nothing to show anyway. */
function deltaOf(ed) {
  if (!ed || ed.line_counts_known === false) return ''
  const a = num(ed.lines_added), r = num(ed.lines_removed)
  return a == null || r == null || (a === 0 && r === 0) ? '' : ` +${a} −${r}`
}

/** The rows, grouped replies and risk series of one run. Pure: (spans, replay) in, plain data out. */
export function consoleModel(spans, replay, verdict) {
  const nos = replyNumbers(spans)
  const byStep = new Map(), list = []
  ;(Array.isArray(replay) ? replay : []).forEach((r, i) => { byStep.set(r.step != null ? r.step : i, i); list.push(r) })
  const rows = []
  let chat = null, pend = null, chatIdx = -1, toolsInChat = 0, hasEdits = false
  const rIdxOf = (c) => (c ? (byStep.has(c.step) ? byStep.get(c.step) : (chatIdx >= 0 && chatIdx < list.length ? chatIdx : -1)) : -1)
  // a reply with no tool call, or whose calls produced no execute_tool span (cut off / blocked at the
  // boundary), still gets a row: its risk and interventions hang off the row of the reply
  const flush = () => {
    if (!chat) return
    const calls = callsOf(chat), none = !calls.length
    if (!none && toolsInChat) return
    const cut = finishOf(chat).includes('length')
    rows.push({ seq: chat.seq, span: chat, chat, reply: nos.get(chat.seq), rIdx: rIdxOf(chat),
      kind: 'x', letter: '·', name: none ? 'no tool call' : 'no result recorded',
      arg: none ? flat(chat.text, 70) : flat(calls.map((c) => (c && (c.name || c['gen_ai.tool.name'])) || 'tool').join(', '), 70),
      chip: cut ? { tone: 'bad', text: 'cut off' } : none ? null : { tone: 'bad', text: 'not run' }, cut })
  }
  // An edit span and its tool row come in either order: the native harness records the edit first,
  // a captured/imported ledger records the tool first. Pair them only within the same reply, by path
  // (an absent path matches), and only once — a stale edit is never carried onto a later row.
  const fits = (ed, row) => !!ed && !!row && row.chat === chat && !row.edit && (!row.path || !ed.path || row.path === ed.path)
  const attach = (row, ed) => {
    row.edit = ed
    row.arg = flat(`${ed.path || row.rawArg}${deltaOf(ed)}`, 90)
  }
  for (const s of spans || []) {
    if (s.span === 'chat') {
      flush()
      chat = s; chatIdx += 1; pend = null; toolsInChat = 0
    } else if (s.span === 'edit') {
      hasEdits = true
      const last = rows[rows.length - 1]
      if (last && last.kind === 'e' && fits(s, last)) { attach(last, s); pend = null } else pend = s
    } else if (s.span === 'execute_tool') {
      toolsInChat += 1
      const name = toolOf(s), a = s.args && typeof s.args === 'object' ? s.args : {}
      const letter = TOOLCODE[name] || (name[0] || '?').toUpperCase()
      const kind = KIND_OF[letter] || 'i'
      const rawArg = a.path || a.command || a.pattern || s.swe_agent_command || ''
      let chip = null
      if (name === 'run_tests') chip = testResult(s)
      else if (name === 'submit' && verdict != null) chip = verdict ? { tone: 'ok', text: 'passed' } : { tone: 'bad', text: 'failed' }
      else if (s.status && s.status !== 'ok') chip = { tone: 'bad', text: s.status === 'error' ? 'error' : String(s.status) }
      const row = { seq: s.seq, span: s, chat, edit: null, reply: chat ? nos.get(chat.seq) : null, rIdx: rIdxOf(chat),
        kind, letter, name, path: a.path, rawArg, arg: name === 'submit' ? '' : flat(rawArg, 90), chip }
      if (kind === 'e' && pend && fits(pend, { ...row, chat })) attach(row, pend)
      pend = null
      rows.push(row)
    }
  }
  flush()
  rows.forEach((r, i) => { r.n = i + 1; r.risk = r.rIdx >= 0 ? num(list[r.rIdx].recorded && num(list[r.rIdx].recorded.risk) != null ? list[r.rIdx].recorded.risk : list[r.rIdx].risk) : null })
  return { hasEdits, rows, steps: list.map((r) => {
    const rec = r.recorded && typeof r.recorded === 'object' ? r.recorded : null
    return { step: r.step, risk: num(rec && num(rec.risk) != null ? rec.risk : r.risk), patterns: (rec ? rec.patterns : r.patterns) || [], rec }
  }) }
}

/** Where Sentinel intervened: {after: last row seq of that reply, verdict, id, text, source}. */
export function interventions(model, pats) {
  const out = [], seen = new Set()
  model.steps.forEach((st, k) => {
    const prev = k ? model.steps[k - 1].patterns : []
    const rowsOf = model.rows.filter((r) => r.rIdx === k)
    if (!rowsOf.length) return
    const after = rowsOf[rowsOf.length - 1].seq
    if (st.rec) {
      if (st.rec.action && st.rec.action !== 'none') {
        const id = st.patterns[0] || st.rec.reason || 'sentinel'
        out.push({ k, after, verdict: st.rec.action, id, text: st.rec.text || (pats[id] && pats[id].nudge) || '', sev: pats[id] && pats[id].severity, source: 'recorded', n: rowsOf[0].n })
      }
    } else {
      // a replayed detector is flagged once, where it first fires: one that drops out and fires
      // again is the same finding, not a new intervention
      st.patterns.filter((p) => !prev.includes(p)).forEach((id) => {
        if (seen.has(id)) return
        seen.add(id)
        out.push({ k, after, verdict: 'flag', id, text: (pats[id] && pats[id].nudge) || '', sev: pats[id] && pats[id].severity, source: 'replayed', n: rowsOf[0].n })
      })
    }
  })
  return out
}

const SEV_TONE = { critical: 'bad', high: 'amb', medium: 'on', low: '' }   // the site's .sev scale: bad · amber · accent · dim
const tone = (r, thr) => (r == null ? '' : r >= thr ? 'hot' : r >= thr / 2 ? 'warm' : '')
const fx2 = (v) => (v == null ? '—' : v.toFixed(2))

/* ------------------------------------------------------------------ sparkline */
function Spark({ steps, thr, head, hot }) {
  const gid = 'rgcxg' + useId().replace(/[^a-zA-Z0-9]/g, '')
  const W = 264, H = 78, PX = 6, PT = 8, PB = 16, n = steps.length
  const x = (i) => (n < 2 ? W / 2 : PX + (i * (W - 2 * PX)) / (n - 1))
  const y = (v) => PT + (1 - Math.max(0, Math.min(1, v))) * (H - PT - PB)
  const pts = steps.map((s, i) => (s.risk == null ? null : [x(i), y(s.risk)]))
  const ok = pts.filter(Boolean)
  const line = ok.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')
  const area = ok.length > 1 ? `M${ok[0][0].toFixed(1)},${H - PB} L${line.split(' ').join(' L')} L${ok[ok.length - 1][0].toFixed(1)},${H - PB} Z` : ''
  const tickAt = n <= 12 ? steps.map((_, i) => i) : [0, Math.round((n - 1) / 4), Math.round((n - 1) / 2), Math.round(((n - 1) * 3) / 4), n - 1]
  return (
    <svg className="rg-cx-spark" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0" className="rg-cx-g0" /><stop offset="1" className="rg-cx-g1" /></linearGradient></defs>
      {[0, 0.5, 1].map((v) => <line key={v} className="grid" x1={PX} x2={W - PX} y1={y(v)} y2={y(v)} />)}
      <line className="thr" x1={PX} x2={W - PX} y1={y(thr)} y2={y(thr)} />
      {area && <path className="area" d={area} fill={`url(#${gid})`} />}
      {ok.length > 1 && <polyline className="ln" points={line} />}
      {pts.map((p, i) => p && (n <= 40 || hot.has(i)) && <circle key={i} className={cx('pt', hot.has(i) && 'hot')} cx={p[0]} cy={p[1]} r={3} />)}
      {tickAt.map((i) => <text key={i} x={x(i)} y={H - 3} textAnchor={i === 0 && n > 1 ? 'start' : i === n - 1 && n > 1 ? 'end' : 'middle'}>{(steps[i].step != null ? steps[i].step : i) + 1}</text>)}
      {head != null && pts[head] && <circle className="head" cx={pts[head][0]} cy={pts[head][1]} r={4.5} />}
    </svg>
  )
}

/** prefers-reduced-motion, read live: flipping the OS setting while the page is open takes effect. */
function useReducedMotion() {
  const get = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)').matches : false)
  const [r, setR] = useState(get)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const on = () => setR(mq.matches)
    on()
    if (mq.addEventListener) { mq.addEventListener('change', on); return () => mq.removeEventListener('change', on) }
    if (mq.addListener) { mq.addListener(on); return () => mq.removeListener(on) }
  }, [])
  return r
}

/* ------------------------------------------------------------------ the console */
/** `sel` = span seq at the playhead (null = the end of the run), `onSel(seq)` moves it. */
export function RunConsole({ run, spans, replay, sel, onSel, verdictOf }) {
  const sent = useSentinel()
  const plug = useApi('/sentinel/plugins')
  // the whole detector catalogue: the built-in patterns plus rule and python plugins, by id
  const pats = useMemo(() => {
    const m = { ...((sent.data && sent.data.patterns && !Array.isArray(sent.data.patterns)) ? sent.data.patterns : {}) }
    for (const d of (plug.data && Array.isArray(plug.data.detectors) ? plug.data.detectors : [])) if (d && d.id) m[d.id] = { ...d, ...m[d.id] }
    return m
  }, [sent.data, plug.data])
  const cfg = harnessOf(spans)
  const thr = num(cfg && cfg.sentinel && cfg.sentinel.threshold) ?? num(sent.data && sent.data.default_config && sent.data.default_config.threshold) ?? 0.6
  const model = useMemo(() => consoleModel(spans, replay, verdictOf), [spans, replay, verdictOf])
  const { rows, steps } = model
  const ivs = useMemo(() => interventions(model, pats), [model, pats])
  const recordedMode = steps.some((s) => s.rec)
  const N = rows.length
  const listRef = useRef(null)
  const [rp, setRp] = useState(null)               // replay: { i, playing } | null
  const [open, setOpen] = useState(false)
  const reduce = useReducedMotion()

  // the playhead: replay overrides the selection; no selection = the end of the run
  const idxOfSeq = (seq) => {
    if (seq == null || !N) return N - 1
    let i = rows.findIndex((r) => r.seq === +seq)
    if (i < 0) i = rows.findIndex((r) => r.chat && r.chat.seq === +seq)
    // evidence such as "first edit" cites the edit span, which sits beside its tool row in either order
    if (i < 0) i = rows.findIndex((r) => r.edit && r.edit.seq === +seq)
    if (i < 0) { for (let j = N - 1; j >= 0; j--) if (rows[j].seq <= +seq) { i = j; break } }
    return i < 0 ? 0 : i
  }
  const cur = rp ? Math.min(rp.i, N - 1) : idxOfSeq(sel)
  const row = rows[cur]
  useEffect(() => { if (sel != null) setOpen(true) }, [sel])
  // a selection that changes from outside (an evidence chip in the run document) takes the playhead
  // back from a replay; the first render and a replay the user just started are not changes
  const selSeen = useRef(sel)
  useEffect(() => { if (selSeen.current !== sel) { selSeen.current = sel; setRp(null) } }, [sel])

  useEffect(() => {
    if (!rp || !rp.playing) return
    if (rp.i >= N - 1) {
      // the replay reached the last row: hand the playhead back so the console is interactive again
      const last = rows[N - 1]
      setRp(null)
      if (last && onSel) onSel(last.seq)
      return
    }
    const t = setTimeout(() => setRp((p) => (p && p.playing ? { ...p, i: Math.min(N - 1, p.i + 1) } : p)), 750)
    return () => clearTimeout(t)
  }, [rp, N])

  useEffect(() => {
    const el = listRef.current && listRef.current.querySelector(`[data-seq="${row ? row.seq : ''}"]`)
    if (el && el.scrollIntoView && (rp || sel != null)) el.scrollIntoView({ block: 'nearest' })
  }, [row && row.seq, rp && rp.i])   // eslint-disable-line react-hooks/exhaustive-deps

  const replaying = !!rp && rp.playing
  const onReplay = () => {
    if (!N) return
    if (reduce) {
      // one step per press, on the shared selection, so the playhead never locks
      if (onSel) pick(cur < N - 1 ? cur + 1 : 0)
      else setRp((p) => ({ i: p && p.i < N - 1 ? p.i + 1 : 0, playing: false }))
      return
    }
    setRp({ i: 0, playing: true })
  }
  const pick = (i, focus) => {
    const r = rows[Math.max(0, Math.min(N - 1, i))]
    if (!r) return
    setRp(null)
    onSel && onSel(r.seq)
    if (focus) requestAnimationFrame(() => { const b = listRef.current && listRef.current.querySelector(`[data-seq="${r.seq}"] .rg-cx-rowb`); if (b) b.focus() })
  }
  const onKey = (e) => {
    if (!(e.target && e.target.closest && e.target.closest('.rg-cx-rowb'))) return   // keys typed inside an expanded detail stay with it
    const k = e.key
    const d = k === 'ArrowDown' ? 1 : k === 'ArrowUp' ? -1 : 0
    if (!d && k !== 'Home' && k !== 'End') return
    e.preventDefault()
    pick(k === 'Home' ? 0 : k === 'End' ? N - 1 : cur + d, true)
  }

  // right rail, at the playhead
  const risk = row ? row.risk : null
  const hotSteps = useMemo(() => new Set(ivs.map((v) => v.k)), [ivs])
  const headStep = row && row.rIdx >= 0 ? row.rIdx : null
  const firstFire = useMemo(() => {
    const m = new Map()
    steps.forEach((s, k) => s.patterns.forEach((p) => { if (!m.has(p)) { const r = rows.find((x) => x.rIdx === k); m.set(p, r ? r.n : k + 1) } }))
    return m
  }, [steps, rows])
  const known = Object.keys(pats)
  const fired = [...firstFire.keys()]
  const dets = [...fired.map((id) => [id, firstFire.get(id)]), ...known.filter((id) => !firstFire.has(id)).slice(0, Math.max(0, 5 - fired.length)).map((id) => [id, null])]
  const upto = rows.filter((r) => (rp || sel != null ? r.n <= (row ? row.n : N) : true))
  // distinct files edited: rows paired with a recorded edit span (a blocked or errored edit has none).
  // Only a ledger with no edit spans at all falls back to edit tools that finished ok, by args.path.
  const edited = new Set(upto.filter((r) => r.kind === 'e' && (model.hasEdits ? r.edit : r.span.status === 'ok'))
    .map((r) => (r.edit && r.edit.path) || r.path).filter(Boolean)).size
  const tests = upto.filter((r) => r.name === 'run_tests').length
  const ivDone = ivs.filter((v) => (rp || sel != null ? v.n <= (row ? row.n : N) : true)).length
  const sOn = cfg && cfg.sentinel && typeof cfg.sentinel.enabled === 'boolean' ? cfg.sentinel.enabled : null
  const sentLabel = recordedMode || sOn === true ? 'sentinel on' : sOn === false ? 'sentinel off' : 'sentinel replay'
  const live = run && (run.exit === 'running' || run.exit === 'live')

  return (
    <div className="rg-cx-stage">
    <div className={cx('rg-cx', replaying && 'playing')} data-el="run-console" role="group" aria-label={`Run console for ${run.id}`}>
      <div className="rg-cx-bar">
        <span className="rg-cx-live"><i className={live ? 'on' : ''} aria-hidden="true" />{live ? 'live' : 'recorded'}</span>
        <span className="rg-cx-sep" aria-hidden="true">/</span><span className="rg-cx-idv">run {sfx(run.id)}</span>
        <span className="rg-cx-sep hide-sm" aria-hidden="true">/</span><span className="hide-sm rg-cx-task">{run.task}</span>
        <span className="rg-cx-sep hide-sm" aria-hidden="true">/</span><span className="hide-sm rg-cx-model">{short(run.model)}</span>
        <span className="rg-cx-grow" />
        <span className={cx('rg-cx-chip', sentLabel === 'sentinel on' && 'on')}>{sentLabel}</span>
        <span className="rg-cx-chip" data-el="console-step">{N ? `step ${row.n} / ${N}` : 'no steps'}</span>
      </div>
      <div className="rg-cx-body">
        <ol className="rg-cx-steps" ref={listRef} aria-label="Tool calls, in order" onKeyDown={onKey}>
          {rows.map((r, i) => {
            const isCur = i === cur, on = isCur && open && !rp
            const future = !!rp && i > cur
            const iv = ivs.filter((x) => x.after === r.seq)
            const showIv = !rp || i <= cur
            return [
              <li key={r.seq} className={cx('rg-cx-st', isCur && 'cur', future && 'future', r.kind === 'x' && 'none', iv.length && 'held')} data-seq={r.seq} data-n={r.n}>
                <button type="button" className="rg-cx-rowb" tabIndex={isCur ? 0 : -1} aria-current={isCur ? 'step' : undefined} aria-expanded={on}
                  aria-label={`Step ${r.n}: ${spanDesc(r.span, r.reply)}${r.chip ? ' · ' + r.chip.text : ''}`}
                  onClick={() => { if (isCur && !rp) setOpen(!open); else { setOpen(true); pick(i) } }}>
                  <span className="n">{pad2(r.n)}</span>
                  <span className={cx('rg-cx-kc', 'k-' + r.kind)} aria-hidden="true">{r.letter}</span>
                  <span className="c">{r.name} {r.arg && <i>{r.arg}</i>}</span>
                  <span className="r">{r.chip && <span className={cx('rg-cx-chip', r.chip.tone)}>{r.chip.text}</span>}</span>
                </button>
                {on && <RowDetail run={run} row={r} />}
              </li>,
              ...(showIv ? iv.map((x, j) => (
                <li key={r.seq + 'iv' + j} className={cx('rg-cx-iv', x.source)} data-el="intervention">
                  <div className="rg-cx-ivh">{x.verdict} · <code>{x.id}</code>
                    {x.sev && <span className={cx('rg-cx-chip', 'rg-cx-sev', SEV_TONE[x.sev])} data-sev={x.sev}>{x.sev}</span>}
                    <span className="rg-cx-src">{x.source}</span></div>
                  <p className={x.text ? '' : 'none'}>{x.text || 'No nudge text is defined for this detector.'}</p>
                </li>)) : []),
            ]
          })}
          {!N && <li className="rg-cx-empty">No tool calls were recorded in this ledger.</li>}
        </ol>
        <div className="rg-cx-side">
          <div className="rg-cx-sec">
            <div className="rg-cx-lbl"><span>Failure risk</span><span>threshold {fx2(thr)}</span></div>
            {steps.length ? (<>
              <div className="rg-cx-risk" aria-live={replaying ? 'polite' : 'off'} aria-atomic="true" data-el="console-risk">
                <b className={tone(risk, thr)}>{fx2(risk)}</b>
                <span>{row && row.reply != null ? `after reply ${row.reply}` : 'at this step'}{row && row.rIdx >= 0 && steps[row.rIdx].patterns.length ? ` · ${steps[row.rIdx].patterns[0]}` : ''}</span>
              </div>
              <div className="rg-cx-meter">{risk != null && <i style={{ width: `${Math.max(4, risk * 100).toFixed(0)}%` }} />}</div>
              <Spark steps={steps} thr={thr} head={headStep} hot={hotSteps} />
            </>) : <p className="rg-cx-none">No Sentinel replay is available for this run, so there is no risk curve to draw.</p>}
          </div>
          <div className="rg-cx-sec">
            <div className="rg-cx-lbl"><span>Detectors</span><span>{known.length ? `${known.length} active` : '—'}</span></div>
            <ul className="rg-cx-dets">
              {dets.map(([id, at]) => <li key={id} className={at != null ? 'fired' : ''}><span className="d">{id}</span><span>{at != null ? `step ${at}` : '—'}</span></li>)}
              {!dets.length && <li><span className="d">none reported</span><span>—</span></li>}
            </ul>
          </div>
          <div className="rg-cx-sec">
            <div className="rg-cx-lbl"><span>Run</span></div>
            <div className="rg-cx-kv">
              <div><span>steps</span><b>{upto.length}{upto.length !== N ? ` / ${N}` : ''}</b></div>
              <div><span>tests run</span><b>{tests}</b></div>
              <div><span>files edited</span><b>{edited}</b></div>
              <div><span>interventions</span><b>{ivDone ? `${ivDone} ${recordedMode ? 'recorded' : 'replayed'}` : 'none'}</b></div>
            </div>
          </div>
        </div>
      </div>
      <div className="rg-cx-foot">
        <span>{!steps.length ? 'Recorded run · no Sentinel replay is available for it.' : recordedMode ? 'Recorded run · risk and interventions as Sentinel recorded them.' : 'Recorded run · risk and detectors replayed by Sentinel.'}</span>
        <span className="rg-cx-grow" />
        <button type="button" className="rg-cx-replay" disabled={!N} onClick={onReplay} data-el="console-replay"
          title={reduce ? 'Reduced motion is on: each press moves the playhead one step' : undefined}>
          {reduce ? 'Step playhead' : replaying ? 'Replaying…' : rp ? 'Replay again' : 'Replay run'}
        </button>
      </div>
    </div>
    </div>
  )
}

/** What the row expands to: the recorded arguments, the observation, the reasoning before it. */
function RowDetail({ run, row }) {
  const s = row.span, ch = row.chat
  const reason = ch && String(ch.text || '').trim()
  const args = s.span === 'execute_tool' ? s.args : null
  const obs = s.result_preview != null ? asText(s.result_preview) : ''
  return (
    <div className="rg-cx-xp">
      <div className="rg-cx-meta">
        <span>event #{s.seq}</span>
        {row.reply != null && <span>reply #{row.reply}</span>}
        {s.duration_ms != null && <span>{int(s.duration_ms)} ms</span>}
        {s.status && s.status !== 'ok' && <span>status {s.status}</span>}
      </div>
      {reason && <><div className="rg-cx-h">Reasoning before this call</div><pre className="rg-cx-pre">{reason}</pre></>}
      {args && Object.keys(args).length > 0 && <><div className="rg-cx-h">Arguments</div><pre className="rg-cx-pre">{asText(args)}</pre></>}
      {obs && <><div className="rg-cx-h">Recorded observation</div><pre className="rg-cx-pre">{obs}</pre></>}
      <div className="rg-cx-acts">
        <Go spec={makeSpec('span', run.id, s.seq)} className="rg-btn">Open event #{s.seq} <Icon name="right" size={12} /></Go>
        <PinButton item={eventPin(run, s, row.reply)} />
      </div>
    </div>
  )
}
