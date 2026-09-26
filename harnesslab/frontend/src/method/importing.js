/* ============================================================================
   The import road.

   There are two ways a trace becomes a run on this machine, and confusing them is the
   mistake this module exists to prevent:

     capture   the watcher follows five interactive CLIs by itself and writes what it
               sees (harnesslab/capture/adapters.py). Nobody types a path.
     import    everything else -- a batch of SWE-agent .traj, an Inspect .eval, an
               OpenHands run, a Trajectory v1 file, or a session store sitting where the
               watcher does not look. Somebody points at it.

   WATCHED below is the first list; a test in tests_agentlab/test_importers.py holds it
   against the capture registry, so the screen cannot keep claiming a source is watched
   after the spine stops watching it.
   ============================================================================ */

/** The sources the capture watcher follows on its own (capture/adapters.py REGISTRY). */
export const WATCHED = ['claude_code', 'codex', 'cursor', 'gemini_cli', 'qwen_code']

/** Which road a source arrives by. An unknown name is an import: never claim it is watched. */
export function arrival(source) {
  return WATCHED.includes(source) ? 'capture' : 'import'
}

/** Of a list of adapters, the ones import is the only road for -- order preserved. */
export function importOnly(names) {
  return (names || []).filter(n => arrival(n) === 'import')
}

const slug = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')

/** The study name a path suggests.
 *
 * Deliberately the same rule as `harnesslab.__main__.suggest_results_dir`: a person who detects
 * on the command line and imports from this screen has to land in one study, not two. */
export function suggestResultsDir(source, path) {
  // basename of the normalised path: "/a/b/" -> "b", "/a/b/../c.json" -> "c.json"
  const parts = String(path || '').split('/').filter(p => p !== '' && p !== '.')
  const stack = []
  for (const p of parts) { if (p === '..') stack.pop(); else stack.push(p) }
  const base = slug(stack[stack.length - 1] || '').slice(0, 24).replace(/_+$/, '')
  return `imported_${slug(source) || 'trace'}` + (base ? `_${base}` : '')
}

/** What the results-dir field lets through: one path segment, no leading dot. */
export function cleanResultsDir(raw) {
  return String(raw || '').replace(/[^A-Za-z0-9_-]/g, '').replace(/^\.+/, '')
}

const sessionsPhrase = (n) => (n == null ? '' : `${n} session${n === 1 ? '' : 's'}`)

/**
 * Everything the detector line renders, and the one answer the Import button reads: `ready`.
 *
 * `det` is the /api/import/detect payload (or null before one arrives); `forced` is the adapter
 * the operator picked by hand, '' for auto. A forced adapter always wins -- including over a
 * path the sniffer could not place at all -- but the line says so, because silently importing
 * a Codex rollout through the OpenHands adapter is exactly the kind of wrong number this
 * platform is about.
 */
export function detectState(det, forced) {
  const pick = forced || ''
  if (!det) {
    return { tone: 'idle', ready: false, source: pick, headline: 'point at a path to see what it is',
             detail: '', others: [] }
  }
  const others = (det.candidates || []).filter(c => c.source !== (det.source || pick))
  const detail = [sessionsPhrase(det.sessions), det.error || ''].filter(Boolean).join(' · ')
  if (!det.exists) {
    return { tone: 'bad', ready: false, source: pick, headline: 'no such file or directory',
             detail: det.path || '', others: [] }
  }
  if (!det.source) {
    return pick
      ? { tone: 'warn', ready: true, source: pick,
          headline: `nothing sniffed here — importing as ${pick} because you said so`,
          detail, others }
      : { tone: 'warn', ready: false, source: '',
          headline: 'no adapter recognises this path — pick one to force it',
          detail, others }
  }
  if (pick && pick !== det.source) {
    return { tone: 'warn', ready: true, source: pick,
             headline: `forcing ${pick} — the sniffer read this as ${det.source}`, detail, others }
  }
  return { tone: 'ok', ready: true, source: det.source, headline: det.source, detail, others }
}

/** A [done, total] pair as a bar width. The job starts at [0, 0]; 0/0 is not a width. */
export function progressPct(progress) {
  const [done, total] = Array.isArray(progress) ? progress : []
  if (!Number.isFinite(done) || !Number.isFinite(total) || total <= 0) return 0
  return Math.max(0, Math.min(100, (done / total) * 100))
}

/** Whether there is a fraction to draw at all. The importer walks a generator, so how many
 *  sessions are under a path is unknown until the last one is read: the server reports that as
 *  null rather than as the running count, which is what used to make the bar read 100% from the
 *  first session to the last. No total, no bar -- a count instead. */
export function progressKnown(progress) {
  const [done, total] = Array.isArray(progress) ? progress : []
  return Number.isFinite(done) && Number.isFinite(total) && total > 0
}

/** What the line beside the bar says. */
export function progressPhrase(progress) {
  const [done, total] = Array.isArray(progress) ? progress : []
  if (!Number.isFinite(done)) return 'reading'
  const s = done === 1 ? '' : 's'
  return progressKnown(progress) ? `${done} of ${total} session${s} read` : `${done} session${s} read`
}

/** The "real verdicts" tile: what it shows, what it says underneath, and whether it is a warning.
 *
 * `known` and `unknown` count RUNS THIS IMPORT WROTE. Re-importing a path that is already in the
 * study writes none, so both are zero -- and the tile read "0 / 0" under the note "every run
 * carries one", which is a claim about a population of none. A count the server did not report at
 * all is not zero either; it is unknown, and says so.
 */
export function verdictTile(r) {
  const known = r && r.outcomes_known
  const unknown = r && r.outcomes_unknown
  if (!Number.isFinite(known) || !Number.isFinite(unknown)) {
    return { value: '\u2014', note: 'this import reported no verdict counts', flagged: false }
  }
  const n = known + unknown
  if (n === 0) return { value: '\u2014', note: 'nothing was imported this time', flagged: false }
  return { value: `${known} / ${n}`,
           note: unknown ? 'the rest read as not passed' : 'every run carries one',
           flagged: unknown > 0 }
}
