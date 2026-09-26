/* ====================================================================================
   Rig · answers.js — the pre-answered questions (from the Inquiry prototype). PURE.

   Every answer is arithmetic over live API payloads; nothing is typed in. A question whose
   payload is missing or cannot support an answer returns { unanswered: <reason> } and the UI
   says so, rather than estimating.

   QUESTIONS[i] = { id, short, needs: [resource…], q(c) -> question text,
                    answer(input) -> Answer | { unanswered } }
   input = { dir, row, cond: {model, harness}, oracle, runs, metrics, experiment, oracleData,
             integrity, sentinel, report, outcomes }        (any resource may be null = not loaded)
   Answer = { sentence: [string | {mark}], num, numLabel, short,
              runs: { title, test(run) -> bool },           // "show me the runs"
              fig: {...}                                     // payload for the figure renderer
              note? }                                        // honesty caveat shown under the answer
   ==================================================================================== */
import { short, sfx, tally, verdict, metricsCell, isFailureMode, fmt, conditionRuns } from './data'

const { pct, pp, int, plural } = fmt
const humanReason = (r) => String(r || 'not computed').replace(/_/g, ' ').replace(/\blevels a\b/, 'models').replace(/\blevels b\b/, 'harnesses')

const MODE_PHRASE = {
  cutoff_no_patch: 'cut off at the per-call output limit before any edit',
  wrong_patch: 'a patch the hidden suite then failed',
  step_limit_no_patch: 'out of steps before producing any patch',
  no_patch: 'ended without producing a patch',
  harness_error: 'harness errors (the failure may not be the agent’s)',
}

/** Which sentinel model was trained on `dir` (meta.sources), preferring the active one. */
export function sentinelFor(sentinel, dir) {
  if (!sentinel || !Array.isArray(sentinel.models)) return null
  const trained = sentinel.models.filter((m) => ((m.meta && m.meta.sources) || []).includes(dir))
  return trained.find((m) => m.name === sentinel.active) || trained[0] || null
}
export function operatingPoint(model, threshold) {
  const ops = (model && model.meta && model.meta.operating_points) || []
  if (!ops.length) return null
  return ops.find((o) => Math.abs(o.threshold - threshold) < 1e-9) || [...ops].sort((a, b) => Math.abs(a.threshold - threshold) - Math.abs(b.threshold - threshold))[0]
}
/** Repeats per task, per model: min over that model's (harness, task) slots. */
export function repeatsPlan(runs) {
  if (!runs || !runs.length) return null
  const slots = new Map()
  for (const r of runs) { const k = `${r.model}|${r.harness}|${r.task}`; slots.set(k, (slots.get(k) || 0) + 1) }
  const byModel = new Map()
  for (const [k, n] of slots) { const m = k.split('|')[0]; byModel.set(m, Math.min(byModel.get(m) ?? Infinity, n)) }
  const models = [...byModel].map(([m, reps]) => ({ m, reps })).sort((a, b) => a.reps - b.reps || a.m.localeCompare(b.m))
  const max = Math.max(...models.map((x) => x.reps))
  return { models, low: models[0], max, even: models[0].reps === max }
}
/** Tasks under one condition with their repeats in order and a pass/fail tally. */
export function taskStrips(condRuns, oracle) {
  const by = new Map()
  for (const r of condRuns) { if (!by.has(r.task)) by.set(r.task, []); by.get(r.task).push(r) }
  return [...by].map(([task, list]) => {
    const rs = [...list].sort((a, b) => a.rep - b.rep || a.id.localeCompare(b.id))
    const t = tally(rs, oracle)
    return { task, runs: rs, ...t, mixed: t.p > 0 && t.f > 0 }
  }).sort((a, b) => a.task.localeCompare(b.task))
}

export const QUESTIONS = [
  {
    id: 'variable', short: 'What moves success', needs: ['experiment'],
    q: () => 'Which variable moves success here?',
    answer({ experiment: e, runs }) {
      if (!e) return null
      const f = e.fit || {}
      if (!f.leading_factor || !f.model || !f.harness) return { unanswered: `The two-factor fit is not computed for this dataset (${humanReason(f.reason || f.status)}).` }
      const lead = f.leading_factor, other = lead === 'model' ? 'harness' : 'model'
      const cons = (e.contrasts || []).filter((c) => c.delta != null)
      const sep = cons.filter((c) => !c.covers_zero).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
      const pick = sep[0] || [...cons].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))[0] || null
      return {
        sentence: ['The ', { mark: lead }, `: it carries ${pct(f[lead].share)} of the outcome variance against the ${other}'s ${pct(f[other].share)} (interaction ${pct(f.interaction_share)}).`],
        num: pct(f[lead].share), numLabel: `share of variance · ${other} ${pct(f[other].share)}`,
        short: `the ${lead} — ${pct(f[lead].share)} of variance vs ${pct(f[other].share)}`,
        note: `${sep.length} of ${cons.length} contrasts separate from zero; with ${cons.length} contrasts about ${fmt.fx(e.multiplicity ? e.multiplicity.expected_false : null, 1)} would separate by chance alone. Descriptive, not causal.`,
        fig: { kind: 'variance', f, contrasts: sep.length ? sep.slice(0, 6) : cons.slice(0, 6), nSep: sep.length, nAll: cons.length },
        runs: pick && runs ? {
          title: `${short(pick.a)} under ${pick.from} vs ${pick.to} — the ${sep.length ? 'largest separating' : 'largest'} Δ (${pp(pick.delta)})`,
          test: (r) => r.model === pick.a && (r.harness === pick.from || r.harness === pick.to),
        } : null,
      }
    },
  },
  {
    id: 'grader', short: 'Trust the grader', needs: ['oracle'],
    q: () => 'Can I trust the grader?',
    answer({ oracleData: o }) {
      if (!o) return null
      if (o.kappa == null || !o.rate_a || o.rate_a.rate == null || o.rate_b.rate == null) {
        return { unanswered: o.kappa_undefined_reason === 'no_eligible_pairs'
          ? `No run carries both a hidden and a strengthened grade (${int(o.n_excluded)} excluded), so the two suites cannot be compared.`
          : `Agreement between the suites is undefined here (${humanReason(o.kappa_undefined_reason)}).` }
      }
      const d = o.rate_a.rate - o.rate_b.rate
      return {
        sentence: d > 0.0005
          ? [{ mark: `${(d * 100).toFixed(1)} points` }, ` of the hidden-suite pass rate do not survive the strengthened suite (κ ${fmt.fx(o.kappa)}).`]
          : ['The strengthened suite agrees with the hidden suite: ', { mark: 'no pass is lost' }, ` (κ ${fmt.fx(o.kappa)}).`],
        num: pp(-d), numLabel: `hidden ${pct(o.rate_a.rate)} → strengthened ${pct(o.rate_b.rate)} · n = ${int(o.n_eligible)}`,
        short: `${(d * 100).toFixed(1)} points don't survive the strengthened suite (κ ${fmt.fx(o.kappa)})`,
        fig: { kind: 'oracle', o },
        runs: { title: 'Runs the hidden suite passed and the strengthened suite failed', test: (r) => r.hid === true && r.str === false },
      }
    },
  },
  {
    id: 'repeats', short: 'Repeats disagree', needs: ['runs', 'metrics'],
    q: (c) => `Where do repeats of ${short(c.model)} under ${c.harness} disagree?`,
    answer({ runs, metrics, cond, oracle }) {
      if (!runs || !metrics) return null
      const cell = metricsCell(metrics, cond.model, cond.harness)
      const rs = conditionRuns(runs, cond.model, cond.harness)
      if (!rs.length) return { unanswered: `No runs of ${short(cond.model)} under ${cond.harness} in this dataset.` }
      const strips = taskStrips(rs, oracle)
      const mixed = strips.filter((x) => x.mixed).sort((a, b) => Math.abs(a.rate - 0.5) - Math.abs(b.rate - 0.5) || a.task.localeCompare(b.task))
      const top = mixed[0]
      const flip = cell ? cell.flip_rate : null
      return {
        sentence: top
          ? [`Repeats disagree on `, { mark: `${mixed.length} of ${strips.length} tasks` }, `; ${top.task} is closest to a coin flip — ${top.p} of ${top.known} repeats pass the ${oracle} suite.`]
          : ['Nowhere: ', { mark: `every task's repeats agree` }, ` under ${short(cond.model)} · ${cond.harness} (${oracle} suite).`],
        num: flip == null ? `${mixed.length}/${strips.length}` : pct(flip, 0),
        numLabel: flip == null ? 'tasks with mixed outcomes' : 'flip rate · hidden suite (/metrics)',
        short: top ? `${top.task} — ${top.p} of ${top.known} repeats pass` : 'no task flips in this condition',
        fig: { kind: 'strips', strips },
        runs: { title: top ? `${top.task} under ${short(cond.model)} · ${cond.harness}, repeats in order` : `Every attempt of ${short(cond.model)} · ${cond.harness}`, test: (r) => r.model === cond.model && r.harness === cond.harness && (!top || r.task === top.task) },
      }
    },
  },
  {
    id: 'outcomes', short: 'How runs fail', needs: ['outcomes'],
    q: () => 'How do runs fail?',
    answer({ outcomes, runs, cond, oracle }) {
      if (!outcomes) return null
      const counts = outcomes.counts || {}
      const fails = Object.entries(counts).filter(([k, v]) => isFailureMode(k) && v > 0).sort((a, b) => b[1] - a[1])
      const failures = fails.reduce((a, [, v]) => a + v, 0)
      const total = Object.values(counts).reduce((a, v) => a + v, 0)
      if (!failures) return { unanswered: `No run in this dataset failed the hidden suite${counts.ungraded ? ` (${counts.ungraded} are ungraded, which is not failure)` : ''}.` }
      const [topMode, topN] = fails[0]
      const meta = (outcomes.byId && outcomes.byId[topMode]) || { label: topMode }
      const inCond = runs ? conditionRuns(runs, cond.model, cond.harness) : null
      const condCounts = {}
      if (inCond) for (const r of inCond) if (isFailureMode(r.mode)) condCounts[r.mode] = (condCounts[r.mode] || 0) + 1
      return {
        sentence: [{ mark: `${int(topN)} of ${int(failures)} failures` }, ` were ${MODE_PHRASE[topMode] || meta.label}.`],
        num: pct(topN / failures, 0), numLabel: `${meta.label} · ${int(failures)} failures of ${int(total)} runs · hidden suite`,
        short: `${int(topN)} of ${int(failures)} failures: ${meta.label}`,
        note: `${counts.ungraded ? `${int(counts.ungraded)} ungraded runs are unknown, not failures. ` : ''}Modes are read from each run's ledger by /api/outcomes; they describe how a run stopped, not why the task was hard.${oracle !== 'hidden' ? ' Modes are defined on the hidden suite whatever suite grades the rest of the page.' : ''}`,
        fig: { kind: 'modes', fails, failures, modes: outcomes.modes || [], condCounts, cond, condFailures: Object.values(condCounts).reduce((a, v) => a + v, 0) },
        runs: { title: `Runs whose failure mode is “${meta.label}”`, test: (r) => r.mode === topMode },
      }
    },
  },
  {
    id: 'leak', short: 'Leaks into the task', needs: ['integrity'],
    q: () => 'Is anything leaking into the task?',
    answer({ integrity }) {
      if (!integrity) return null
      const rows = (integrity.leakage || []).filter((x) => x.patch_issue_similarity != null)
      if (!rows.length) return { unanswered: 'Patch ↔ issue overlap is not recorded for these runs (imported trajectories carry no issue text to compare).' }
      const sorted = [...rows].sort((a, b) => b.patch_issue_similarity - a.patch_issue_similarity)
      const top = sorted[0], next = sorted[1]
      const probe = top.probe && top.probe !== 'none' ? top.probe.replace(/_/g, ' ') : null
      return {
        sentence: [probe ? 'Yes, on purpose: ' : 'Possibly: ', { mark: top.task }, ` patches echo the issue text at ${pct(top.patch_issue_similarity, 0)}${next ? ` (next highest ${pct(next.patch_issue_similarity, 0)})` : ''}${probe ? ` — it is the ${probe} probe` : ''}, and it scores ${pct(top.pass1)}.`],
        num: pct(top.patch_issue_similarity, 0), numLabel: `patch ↔ issue overlap · ${integrity.harness || 'harness'} runs`,
        short: `${top.task} echoes the issue at ${pct(top.patch_issue_similarity, 0)}`,
        fig: { kind: 'leak', rows: sorted },
        runs: { title: `${top.task} under ${integrity.harness || 'the integrity harness'}`, test: (r) => r.task === top.task && (!integrity.harness || r.harness === integrity.harness) },
      }
    },
  },
  {
    id: 'run', short: 'One run, exactly', needs: ['runs'],
    q: () => 'What did this specific run actually do?',
    answer({ runs, cond, oracle, outcomes }) {
      if (!runs) return null
      const rs = conditionRuns(runs, cond.model, cond.harness).sort((a, b) => a.task.localeCompare(b.task) || a.rep - b.rep)
      if (!rs.length) return { unanswered: `No runs of ${short(cond.model)} under ${cond.harness} in this dataset.` }
      const run = rs.find((r) => verdict(r, oracle) === false && r.hasLedger) || rs.find((r) => verdict(r, oracle) === false) || rs[0]
      const v = verdict(run, oracle)
      const vw = v === true ? 'passed' : v === false ? 'failed' : 'have no grade'
      const mode = v === false && isFailureMode(run.mode) && outcomes && outcomes.byId && outcomes.byId[run.mode]
      return {
        sentence: [{ mark: `Run ${sfx(run.id)}` }, ` (${run.task}, repeat ${run.rep}) made ${plural(run.steps, 'model call')} and ${plural(run.edits, 'edit')}, then stopped with “${run.exit}”; the ${oracle} tests ${vw}${mode ? ` — ${mode.label}` : ''}.`],
        num: String(run.steps), numLabel: `model calls · exit ${run.exit}`,
        short: `run ${sfx(run.id)}: ${plural(run.steps, 'model call')}, ${run.exit}`,
        fig: { kind: 'run', run },
        run,
        runs: { title: `${run.task} under ${short(cond.model)} · ${cond.harness}`, test: (r) => r.model === cond.model && r.harness === cond.harness && r.task === run.task },
      }
    },
  },
  {
    id: 'sentinel', short: 'Early warning', needs: ['sentinel'],
    q: () => 'Would an early warning have caught the bad submits?',
    answer({ sentinel, dir, row }) {
      if (!sentinel) return null
      const m = sentinelFor(sentinel, dir)
      if (!m) {
        const act = (sentinel.models || []).find((x) => x.name === sentinel.active)
        return { unanswered: `No sentinel model was trained on ${dir}${act ? `; the active model (${act.name}) was trained on ${(act.meta.sources || []).join(', ')}` : ''}. Its scores would not describe these runs.` }
      }
      const thr = (sentinel.default_config && sentinel.default_config.threshold) ?? 0.5
      const op = operatingPoint(m, thr)
      if (!op) return { unanswered: `The ${m.name} model carries no operating points.` }
      const h = row && (row.harnesses.includes('baseline') ? 'baseline' : row.harnesses[0])
      return {
        sentence: [`At threshold ${fmt.fx(op.threshold)} the ${m.name} warning catches `, { mark: `${pct(op.recall, 0)} of bad submits` }, `, blocks ${pct(op.false_alarm, 0)} of good ones, ${fmt.fx(op.mean_lead_steps, 1)} steps ahead.`],
        num: pct(op.recall, 0), numLabel: `bad submits caught · trained on ${int(m.meta.n_runs)} runs (${m.name})`,
        short: `${pct(op.recall, 0)} caught, ${pct(op.false_alarm, 0)} of good blocked`,
        note: 'Replayed on recorded trajectories; an intervention changes what the agent does next, so this is an upper bound on what a live run would show.',
        fig: { kind: 'sentinel', model: m, threshold: thr },
        runs: h ? { title: `${h} runs that failed the hidden suite`, test: (r) => r.harness === h && r.hid === false } : null,
      }
    },
  },
  {
    id: 'report', short: 'What I would report', needs: ['report', 'runs'],
    q: () => 'What would I report?',
    answer({ report, runs }) {
      if (!report) return null
      const card = report.card
      if (!card || !card.outcome) return { unanswered: `The report card is not available for this dataset${report.error ? ` (the endpoint answered “${report.error}”)` : ''}.` }
      const h = card.cell && card.cell.harness && card.cell.harness.id
      const n = card.cell && card.cell.runs
      const own = runs && h ? runs.filter((r) => r.harness === h && r.model === card.cell.model).length : null
      const all = runs && h ? runs.filter((r) => r.harness === h).length : null
      const models = runs && h ? new Set(runs.filter((r) => r.harness === h).map((r) => r.model)).size : null
      const pooled = own != null && n != null && n > own && n === all
      const o = card.outcome
      const scope = pooled ? `all ${models} models · ${h} (pooled)` : `${short(card.cell.model)} · ${h}`
      return {
        sentence: ['pass@1 ', { mark: pct(o.pass1) }, ` [${pct(o.ci95 && o.ci95[0])}, ${pct(o.ci95 && o.ci95[1])}] over ${int(n)} ${h} runs${pooled ? `, all ${models} models pooled` : ''}; pass^3 ${pct(o.pass_pow_k && o.pass_pow_k['3'])}, strengthened ${pct(o.pass1_strong)}.`],
        num: pct(o.pass1), numLabel: `${scope} · n = ${int(n)}`,
        short: `pass@1 ${pct(o.pass1)} over ${int(n)} ${h} runs${pooled ? ' (all models pooled)' : ''}`,
        note: pooled ? `The card's header names ${card.cell.model}, but its outcome pools every model's ${h} runs (n = ${int(n)}; ${short(card.cell.model)} alone has ${int(own)}). Shown here as pooled.` : null,
        fig: { kind: 'report', card, pooled, scope },
        runs: h ? { title: `Every ${h} run behind the report card`, test: (r) => r.harness === h } : null,
      }
    },
  },
  {
    id: 'next', short: 'What to run next', needs: ['runs'],
    q: () => 'What should I run next?',
    answer({ runs }) {
      if (!runs) return null
      const p = repeatsPlan(runs)
      if (!p) return { unanswered: 'This dataset has no runs.' }
      if (p.even) {
        return {
          sentence: [`Every model already has ${p.max} repeats per task; the next informative run is a `, { mark: 'new harness or model' }, ` at the same ${p.max} repeats.`],
          num: `${p.max}×`, numLabel: 'repeats per task in every cell', short: `every cell has ${p.max} repeats — add a variable`,
          fig: { kind: 'plan', p }, runs: null,
        }
      }
      return {
        sentence: ['More repeats of ', { mark: short(p.low.m) }, `: it has ${p.low.reps} per task where the others reach ${p.max}, so its intervals are the widest.`],
        num: `${p.low.reps} vs ${p.max}`, numLabel: 'repeats per task (fewest vs most)',
        short: `${short(p.low.m)} has ${p.low.reps} repeats vs ${p.max}`,
        fig: { kind: 'plan', p },
        runs: { title: `Every recorded run of ${short(p.low.m)}`, test: (r) => r.model === p.low.m },
      }
    },
  },
]

export const QMAP = Object.fromEntries(QUESTIONS.map((q) => [q.id, q]))
/** id -> { short } for chain labels and palette text without computing anything. */
export const QUESTION_META = Object.fromEntries(QUESTIONS.map((q) => [q.id, { short: q.short, q: q.q }]))

/** Run one question. Returns { state: 'loading'|'unanswered'|'answered', answer?, reason? }. */
export function answerQuestion(q, input, loading = {}) {
  const pending = q.needs.filter((n) => loading[n])
  let a
  try { a = q.answer(input) } catch (e) { return { state: 'unanswered', reason: `Could not compute: ${e.message}` } }
  if (a == null) {
    const failed = q.needs.filter((n) => loading[n] === 'error')
    if (failed.length) return { state: 'unanswered', reason: `Needs ${failed.join(', ')}, which did not load.` }
    return pending.length ? { state: 'loading' } : { state: 'unanswered', reason: 'The data this answer needs is not available.' }
  }
  if (a.unanswered) return { state: 'unanswered', reason: a.unanswered }
  return { state: 'answered', answer: a }
}
