"""Periodic capture: the half that makes the spine ambient instead of a chore.

`--backfill` reads every allow-listed source every time. On this machine that is 19,380 files and
several minutes, which is fine once and impossible on a cadence. A tick reads only what changed
since the last one, which is usually nothing, and the loop runs ticks until told to stop.

Three rules the design turns on:

  * The capture lock is held for a BATCH, never for the sniffer's life. Holding it for hours would
    block every manual import on the machine. A separate named lock (`sniffer`) is held for life,
    so a second sniffer refuses to start without contending for the lock writers need.
  * A tick that cannot take the capture lock is not an error. Something else is writing; say
    `locked` and come back.
  * Presence is written on BOTH sides of a pass. A status that is only written afterwards is
    indistinguishable from a sniffer that died mid-pass.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, Optional

# `activity` is a local name inside tick() (a source's last-seen clock), so the module
# comes in under its own: shadowing it there cost an AttributeError at the one line that
# matters.
from . import activity as activity_log
from . import cursors, presence
from .backfill import run as _capture
from .identity import parse_ts
from .lock import CaptureLock, CaptureLocked


def _skipper(cs: dict, due: set, stats: dict) -> Callable:
    """Skip a source whose cursor still matches, unless its open run is due a recheck.

    discover() calls this for every candidate BEFORE the file is opened, with a stat it just took.
    That stat is kept, and it is the one the cursor records. Statting again after the read looked
    equivalent and was not: a pass over the real corpus takes minutes, a session being written grows
    during one, and the late stat recorded those appended bytes as captured without ever reading
    them. If what WAS read looked finished, no recheck was scheduled and the source was skipped for
    good.
    """
    def skip(real: str, st: os.stat_result) -> bool:
        stats[real] = (st.st_size, st.st_mtime_ns)
        if real in due:
            return False
        c = cs.get(real)
        return bool(c and cursors.unchanged(c, st))
    return skip


def _open_until(last_ts: str, gap_s: float, now: float) -> float:
    """When this source's newest run stops being open, or 0 if it already has.

    Only a run that is STILL open needs a recheck: nothing about the file changes when a session
    goes quiet, so the clock is the only thing that can close it.
    """
    at = parse_ts(last_ts or "")
    if at is None:
        return 0.0
    closes = at + gap_s
    return closes if closes > now else 0.0


def _corpus_totals(runs_root: str, cs: dict) -> dict:
    """How much is CAPTURED, counted off disk -- not how much the last pass happened to do.

    EVERY total, not the two an earlier cut happened to cover. A pass usually finds nothing, so
    reporting the pass leaves a menubar reading `totals.cost_usd` showing $0.00 for good after the
    first quiet tick, with the whole corpus sitting in the index. `files_seen` is the number of
    sources the sniffer knows, which is a property of the corpus; what one pass looked at belongs in
    the report, not here.
    """
    import json as _json
    cap = os.path.join(runs_root, "captured")
    indexed = cost = tin = tout = unmeasured = 0
    cost = 0.0
    try:
        with open(os.path.join(cap, "index.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except ValueError:
                    continue
                indexed += 1
                # a null is a run whose source records no usage: not $0, and counted as such
                unmeasured += int("cost_usd" in row and row["cost_usd"] is None)
                cost += float(row.get("cost_usd") or 0.0)
                tin += int(row.get("input_tokens") or 0)
                tout += int(row.get("output_tokens") or 0)
    except OSError:
        pass
    open_runs = 0
    try:
        with open(os.path.join(cap, "inflight.json"), encoding="utf-8") as f:
            open_runs = len((_json.load(f) or {}).get("runs") or [])
    except (OSError, ValueError):
        pass
    return {"runs_indexed": indexed, "open_runs": open_runs, "files_seen": len(cs or {}),
            "cost_usd": round(cost, 6), "input_tokens": tin, "output_tokens": tout,
            "unmeasured_runs": unmeasured}


def _related_sources(runs_root: str, cs: dict, report: dict) -> set:
    """Sources this pass SKIPPED whose runs can relate to a run it READ.

    Relations are pairwise and backfill only relates what one pass read, so a resume arriving as a
    new file -- while its parent sits unchanged and skipped -- would never be linked. A resume or a
    fork carries its parent's root uuid, and every index row records its own `root_uuid`, so the
    parents are findable without reparsing the corpus: index rows whose root appears among the uuids
    just read, mapped back to a source through the cursors that remember producing them.

    Covers the direction that happens -- a new file continuing an old one. A source rewritten to
    become a PREFIX of another it never read is not found this way; the periodic full sweep is what
    catches that.
    """
    import json as _json
    read = report.get("sources") or {}
    seen_uuids = {u for info in read.values() for u in (info.get("uuids") or [])}
    if not seen_uuids:
        return set()
    read_runs = {rid for info in read.values() for rid in (info.get("runs") or [])}
    wanted_runs = set()
    try:
        with open(os.path.join(runs_root, "captured", "index.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except ValueError:
                    continue
                if row.get("root_uuid") in seen_uuids and row.get("run_id") not in read_runs:
                    wanted_runs.add(row.get("run_id"))
    except OSError:
        return set()
    return {path for path, c in cs.items()
            if path not in read and wanted_runs.intersection(c.get("runs") or [])}


def _said(e: BaseException) -> str:
    """What goes in presence.json for a failure, and where the rest of it goes.

    presence.json is the one document this process publishes to everything else -- the Capture
    page, /api/capture/status, and a menubar process that renders it into a menu title -- and its
    whole contract is counts, states and timestamps and nothing that could carry prompt text. An
    exception MESSAGE breaks that on its own: the paths this watcher fails on are session
    transcripts under ~/.claude/projects, whose directory component is a slug of the working
    directory the session was recorded in. The class name says what kind of thing went wrong and
    carries none of it.

    The message is not lost. It goes to stderr, which under launchd is the watcher's own log
    (~/Library/Logs/harnesslab-capture.log, see capture/launchd.py) -- a file on this machine,
    which is where a path belongs.
    """
    print(f"capture: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    return type(e).__name__


def tick(roots: list, runs_root: str, gap_s: float, now: Optional[float] = None,
         archive_root: Optional[str] = None, full: bool = False, stop=None) -> dict:
    """One pass over whatever changed. Returns the capture report, plus `state`.

    `full` reads every source whatever its cursor says: the periodic sweep that catches what a
    (size, mtime) cursor cannot -- a rewrite landing on the same size and timestamp, and a relation
    the targeted re-read below does not look for.
    """
    now = time.time() if now is None else now
    cs = cursors.load(cursors.path_for(runs_root))
    due = cursors.due(cs, now, gap_s)
    stats: dict = {}

    # Publishing every file would be 19,576 atomic writes on a real corpus, and a pass is minutes:
    # a figure that moves twice a second is as live as anyone can read. The last file always
    # publishes, so the document does not finish a source short of its own total.
    last_published = [0.0]

    def publish(done: int, of: int) -> None:
        t = time.time()
        if done != of and t - last_published[0] < 0.5:
            return
        last_published[0] = t
        presence.write(runs_root, progress={"done": int(done), "of": int(of)})

    presence.write(runs_root, state="scanning", pid=os.getpid())
    try:
        skip = None if full else _skipper(cs, due, stats)
        if full:
            def skip(real, st, _stats=stats):         # read everything, but still keep the stat
                _stats[real] = (st.st_size, st.st_mtime_ns)
                return False
        report = _capture(roots, runs_root, gap_s, now=now, archive_root=archive_root,
                          skip=skip, stop=stop, progress=publish)
        related = set() if full else _related_sources(runs_root, cs, report)
        if related and not (stop and stop()):
            # Read again, this time the changed sources AND the skipped ones they relate to, so the
            # pass can link them. Only these; the rest of the corpus stays unopened.
            wanted = set(report.get("sources") or {}) | related
            def only_wanted(real, st, _stats=stats):
                _stats[real] = (st.st_size, st.st_mtime_ns)
                return real not in wanted
            report = _capture(roots, runs_root, gap_s, now=now, archive_root=archive_root,
                              skip=only_wanted, stop=stop, progress=publish)
            report["related_reread"] = len(related)
    except CaptureLocked:
        # Somebody else is writing. Not an error, and not a reason to burn a cursor.
        presence.write(runs_root, state="locked")
        return {"state": "locked", "files": 0, "errors": []}
    except Exception as e:
        presence.write(runs_root, state="error", error=_said(e))
        raise

    changed = False
    for path, info in (report.get("sources") or {}).items():
        pre = stats.get(path)
        if pre is None:
            continue                       # never offered to skip(): no pre-read stat, no cursor
        size, mtime_ns = pre
        runs = info.get("runs") or []
        # a clockless source's openness was decided from its mtime (backfill.run); the recheck it
        # needs is scheduled from the same value, or it would never be looked at again
        activity = info.get("last_ts") or info.get("last_activity") or ""
        fresh = cursors.cur(size=size, mtime_ns=mtime_ns, runs=runs, gap_s=gap_s,
                            open_until=_open_until(activity, gap_s, now),
                            dropped=cursors.carry_dropped(cs.get(path), runs))
        if cs.get(path) != fresh:
            cs[path] = fresh
            changed = True
    if changed:
        # At corpus size this file is megabytes. Fsyncing it every minute to record that nothing
        # moved -- the overwhelmingly common pass -- is pure cost.
        cursors.save(cursors.path_for(runs_root), cs)

    # ONE write. This used to be two: record_capture() put the pass's own counts into `totals`,
    # and the line after it corrected them to the corpus. A reader landing between the two saw a
    # corpus of 20,538 reported as 27 -- and the readers poll, so one did.
    presence.record_capture(runs_root, report, at=now, totals=_corpus_totals(runs_root, cs))
    # `last_tick_at` is when a SCAN completed, which is not when a capture happened: a pass that
    # found nothing is still a pass, and that difference is how "alive but the lab is quiet" is told
    # apart from "stuck". Nothing in the repo wrote it, so the menu's "Last scan" row said "not yet"
    # for the whole life of the watcher.
    presence.write(runs_root, state="idle", last_tick_at=float(now),
                   progress={"done": 0, "of": 0})
    # One sample per completed scan, so something in this spine remembers what it looked like a
    # minute ago. presence.json is a snapshot; no reader of it can say "how many today" or "what
    # happened while I was not looking", and a reader keeping its own record would be a second
    # source of truth free to drift from this one.
    t = _corpus_totals(runs_root, cs)
    activity_log.record(runs_root, at=float(now), runs=int(t.get("runs_indexed") or 0),
                    open_runs=int(t.get("open_runs") or 0), files=int(t.get("files_seen") or 0),
                    cost_usd=float(t.get("cost_usd") or 0.0))
    report["state"] = "idle"
    return report


def _hold_sniffer_lock(runs_root: str, attempts: int = 6, pause_s: float = 0.05) -> CaptureLock:
    """The sniffer lock, retried briefly before concluding another watcher holds it.

    presence.sniffer_alive() -- which the Capture page calls -- takes a SHARED lock on the same file
    for an instant. A watcher starting inside that instant would otherwise be told, wrongly, that
    another watcher exists. A real holder keeps it for its whole life, so a quarter of a second of
    retries changes nothing for that case.
    """
    last = None
    for i in range(attempts):
        lock = CaptureLock(runs_root, name="sniffer")
        try:
            lock.__enter__()
            return _Held(lock)
        except CaptureLocked as e:
            last = e
            if i + 1 < attempts:
                time.sleep(pause_s)
    raise last


class _Held:
    def __init__(self, lock):
        self._lock = lock

    def __enter__(self):
        return self._lock

    def __exit__(self, *exc):
        return self._lock.__exit__(*exc)


def _wait(runs_root: str, interval_s: float, last_nudge: float, sleep, stop, paused: bool = False) -> None:
    """Sleep out the interval in short steps, returning early for a nudge, a stop, or a pause change.

    One long sleep made capture-now meaningless: the request was read, and then the caller waited
    the full interval anyway. A pause or resume is the same kind of request -- a menubar click, left
    unapplied for a whole interval, is a label and a report that disagree for that long.
    """
    step = min(2.0, interval_s) if interval_s > 0 else 0.0
    waited = 0.0
    while waited < interval_s:
        sleep(step)
        waited += step
        if stop():
            return
        control = presence.read_control(runs_root)
        if control["nudge_at"] > last_nudge or control["paused"] != paused:
            return
    if interval_s <= 0:
        sleep(0)


def _count_debris(runs_root: str, now) -> None:
    """Publish how much crash debris sits in captured/. Counts only: the watcher never removes it.

    Removal is an operator's decision (`--sweep --apply`); a watcher deleting things in the lab on
    its own every day is exactly the ambient destructive behaviour this must not have. A sweep that
    fails leaves the previous count standing and the watcher running.
    """
    from . import sweep
    # A CLOCK, not a timestamp. The pass that precedes this takes minutes on a large corpus, so the
    # time it began dated the check before it happened and made every age_s inside sweep() short by
    # the length of the pass.
    now = now() if callable(now) else float(now)
    try:
        report = sweep.sweep(runs_root, apply=False, now=now)
    except Exception:
        return
    if report.get("locked"):
        # sweep() refuses everything it cannot judge while a capture holds the lock, so its count is a
        # floor. Publishing it as just-checked replaced the last true count with a smaller one.
        return
    presence.write(runs_root, debris=int(report["counts"]["debris"]), debris_checked_at=float(now))


#: How often a watcher reads every source regardless of its cursor. A (size, mtime) cursor cannot
#: see a rewrite that lands on the same size and timestamp, and the targeted re-read in tick() only
#: looks for relations in the direction resumes actually happen. A daily full read catches both,
#: at the cost of one pass of the kind --backfill does every time.
FULL_SWEEP_S = 24 * 3600.0


def loop(roots: list, runs_root: str, gap_s: float, interval_s: float,
         stop: Optional[Callable[[], bool]] = None, now: Optional[Callable[[], float]] = None,
         archive_root: Optional[str] = None, sleep=time.sleep,
         full_sweep_s: float = FULL_SWEEP_S) -> None:
    """Tick until `stop()` says otherwise, obeying the control file between passes.

    Holds the `sniffer` lock for its whole life so a second one cannot start. `stop`, `now` and
    `sleep` are injected so this is testable without a clock or a signal.

    A failed TICK is recorded and survived: a full disk or an unreadable file is a reason to say
    `error` and try again next interval, not to end the watcher -- nothing restarts it yet. A failure
    of the loop ITSELF propagates, and is left on the page as `error`, never painted over as a clean
    stop.
    """
    stop = stop or (lambda: False)
    now = now or time.time
    os.makedirs(os.path.join(runs_root, "captured"), exist_ok=True)

    with _hold_sniffer_lock(runs_root):
        clean = False
        try:
            # Inside the guard: anything that fails once this process owns the watcher must leave
            # a final state behind, or the page reads the last "idle" forever.
            presence.write(runs_root, state="idle", pid=os.getpid(), interval_s=interval_s,
                           roots=len(roots), error="")
            last_nudge = presence.read_control(runs_root)["nudge_at"]
            while not stop():
                control = presence.read_control(runs_root)
                # Consumed whether or not it is acted on. A nudge while paused is not a resume, and
                # left unconsumed it cut every later wait short, spinning the loop every two seconds.
                last_nudge = max(last_nudge, control["nudge_at"])
                if control["paused"]:
                    presence.write(runs_root, state="paused", next_tick_at=0.0, heartbeat_at=now())
                    _wait(runs_root, interval_s, last_nudge, sleep, stop, paused=True)
                    continue
                t = now()
                last_full = float(presence.read(runs_root).get("last_full_sweep_at") or 0.0)
                full = full_sweep_s > 0 and (t - last_full) >= full_sweep_s
                try:
                    rep = tick(roots, runs_root, gap_s, now=t, archive_root=archive_root, full=full, stop=stop)
                    # A sweep stopped part-way did not read everything, so it does not count. Recording
                    # it skipped the next real one for a day.
                    # tick() absorbs CaptureLocked and RETURNS "locked", so a sweep that read nothing
                    # because another writer held the lock arrives here looking like a finished one.
                    done = (full and not (rep or {}).get("interrupted")
                            and (rep or {}).get("state") != "locked")
                    presence.write(runs_root, error="", **({"last_full_sweep_at": t} if done else {}))
                    if done:
                        _count_debris(runs_root, now)
                except CaptureLocked:
                    pass                                   # tick already said "locked"
                except Exception as e:                     # recorded by tick(); survive it
                    presence.write(runs_root, state="error", error=_said(e))
                presence.write(runs_root, next_tick_at=now() + interval_s, heartbeat_at=now())
                _wait(runs_root, interval_s, last_nudge, sleep, stop)
            clean = True
        except KeyboardInterrupt:
            clean = True                                   # an operator stopping it is a clean stop
            raise
        finally:
            if clean:
                presence.write(runs_root, state="stopped", pid=0, next_tick_at=0.0)
            else:
                # Leave "error" standing. Writing "stopped" here told the page the watcher went
                # away cleanly when it had died.
                presence.write(runs_root, state="error", pid=0, next_tick_at=0.0)


#: Fields an index row copies from its run's summary.json. If they disagree, the folder was rewritten
#: after the row was -- a re-capture killed before it rewrote index.jsonl -- and the row is stale.
_AGREE = ("steps", "input_tokens", "output_tokens", "cost_usd", "lines_added", "lines_removed")


def _captured_runs(runs_root: str) -> dict:
    """basename of source -> {"runs": [...], "captured_ns": newest capture time} from the lab itself.

    Every captured run directory's ledger opens with a start span naming its source by basename
    (writer.py), and summary.json is written at capture, so its mtime is when that run was last
    captured. Reading these is reading the LAB, which is small; it is not reading the sources.
    """
    import json as _json
    cap = os.path.join(runs_root, "captured")
    out: dict = {}
    try:
        names = os.listdir(cap)
    except OSError:
        return out
    import re as _re
    scratch = _re.compile(r"\.(tmp|old)-\d+$")
    for name in names:
        rdir = os.path.join(cap, name)
        ledger = os.path.join(rdir, "ledger.jsonl")
        # regen writes <run>.tmp-<pid>/ and parks <run>.old-<pid>/ during a swap. A leftover one is
        # not a captured run: counting it lists the run twice and moves the capture time forward,
        # which can make a source changed after the REAL capture look fresh.
        if name.startswith(".") or scratch.search(name) or not os.path.isfile(ledger):
            continue
        try:
            with open(ledger, encoding="utf-8") as f:
                start = _json.loads(f.readline() or "{}")
            summary_path = os.path.join(rdir, "summary.json")
            captured_ns = os.stat(summary_path).st_mtime_ns
            with open(summary_path, encoding="utf-8") as f:
                summary = _json.load(f)
        except (OSError, ValueError):
            continue
        base = start.get("source_path") or ""
        if not base:
            continue
        slot = out.setdefault(base, {"runs": [], "captured_ns": None, "identities": set(), "summaries": {}})
        slot["runs"].append(start.get("run_id") or name)
        slot["summaries"][start.get("run_id") or name] = {k: summary.get(k) for k in _AGREE}
        # Which session this run came from. The basename alone cannot tell two sources apart; the
        # session id can. Not the project: that is the basename of each SEGMENT's cwd, so one session
        # that changed directory has two, and keying on it called one session ambiguous.
        slot["identities"].add(start.get("session_id") or "")
        # The EARLIEST write, not the latest: every summary.json is written after the file was read,
        # and the first is the closest to that read. The latest let a change landing mid-capture
        # count as captured.
        slot["captured_ns"] = captured_ns if slot["captured_ns"] is None else min(slot["captured_ns"], captured_ns)
    return out


#: A source must have been quiet this long before its capture was written to be adopted. summary.json is
#: written after the whole source is parsed and split, which on a large session takes seconds, so a
#: change inside that gap may be bytes nobody read. A minute is far past any parse, and a session
#: touched within a minute of its capture is live and about to be read by the watcher anyway -- being
#: wrong the safe way costs one read.
SEED_MARGIN_NS = 60_000_000_000


def seed(roots: list, runs_root: str, gap_s: float, now: Optional[float] = None) -> dict:
    """Give every already-captured source a cursor, reading no source at all.

    A lab captured by --backfill has runs and no cursors, so a first --watch would read the whole
    corpus again. Seeding stats each source, finds the runs the lab already holds for it, and records
    a cursor -- but only where the lab's own records show the capture happened AFTER the source was
    last modified. A source that changed since would otherwise be recorded as captured with bytes
    nobody read; it is left without a cursor so the watcher reads it.

    Never overwrites a cursor the watcher already wrote. A run still open when it was captured gets
    a recheck scheduled from its last activity, so it closes on the clock like any other.

    What seeding can NOT detect, because it reads no source -- each is recovered by the daily full
    sweep, which seeding defers by a day:

      * a MISSING segment. Every check here is on runs the lab holds. If a re-capture rewrote segment
        A unchanged and then failed writing A-s1, A still agrees with its row and the source is
        adopted with A-s1 never captured.
      * a lab whose file times were not preserved. Freshness is judged by summary.json's mtime; a
        copy or restore that resets it makes every capture look newer than every source, so sources
        appended to after the real capture look fully read.

    Seed a lab only where it was captured, with its timestamps intact.
    """
    os.makedirs(os.path.join(runs_root, "captured"), exist_ok=True)
    # Held for the whole seed. Seeding loads cursors, walks for tens of seconds, then saves the whole
    # file; a watcher ticking in between loses on one side or the other, silently. So seeding refuses
    # while a watcher runs, and no watcher can start until it is done.
    with _hold_sniffer_lock(runs_root):
        # And the capture lock: a backfill mid-pass has written run folders it has not yet recorded,
        # and seeding must not read the lab while it is in that state.
        with CaptureLock(runs_root):
            return _seed_locked(roots, runs_root, gap_s, now)


def _seed_locked(roots: list, runs_root: str, gap_s: float, now: Optional[float]) -> dict:
    import json as _json
    now = time.time() if now is None else now
    from .adapters import discover
    cs = cursors.load(cursors.path_for(runs_root))
    captured = _captured_runs(runs_root)

    last_ts: dict = {}
    try:
        with open(os.path.join(runs_root, "captured", "inflight.json"), encoding="utf-8") as f:
            for r in (_json.load(f) or {}).get("runs") or []:
                # a clockless run records what its openness was decided from instead
                last_ts[r.get("run_id")] = r.get("last_ts") or r.get("last_activity") or ""
    except (OSError, ValueError):
        pass
    # What the lab RECORDED, as opposed to what folders happen to exist. A backfill writes run
    # folders as it goes and records them in index.jsonl / inflight.json only at the end of its pass,
    # so ctrl-c part-way leaves folders nothing lists. Adopting their source meant the watcher skipped
    # it for good and those runs were never indexed.
    rows: dict = {}
    try:
        with open(os.path.join(runs_root, "captured", "index.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except ValueError:
                    continue
                rows[row.get("run_id")] = row

    except OSError:
        pass

    def current(run_id: str, summary: dict) -> bool:
        """Recorded, and recorded AS IT IS NOW. An open run is listed in inflight.json and rechecked on
        the clock; a closed one must have an index row that still agrees with its folder's summary."""
        if run_id in last_ts:
            return True
        row = rows.get(run_id)
        return row is not None and all(row.get(k) == summary.get(k) for k in _AGREE)

    stats: dict = {}
    def record_only(real, st):                 # stat-only: every candidate is skipped, none is opened
        stats[real] = st
        return True
    _, errors = discover(roots, skip=record_only)
    # Seeding is capture-side, like backfill.source_files: a directory it cannot enter is not an
    # error of the pass, and reporting it put --seed-cursors at exit 1 on any machine with one
    # TCC-protected folder under an allow-listed root.
    errors = [e for e in errors if not (isinstance(e, dict) and e.get("kind") == "directory")]

    # `candidates`, not sources: every file a registry glob matched, before any adapter has claimed
    # it -- telling an unrelated .jsonl from a session means opening it, which seeding never does. An
    # unrelated file sharing a session's basename therefore makes that session ambiguous, and it is
    # read instead of adopted. That is the safe direction, and the price of never opening a source.
    report = {"candidates": len(stats), "seeded": 0, "already": 0, "stale": 0, "uncaptured": 0,
              "ambiguous": 0, "unrecorded": 0, "errors": len(errors)}
    # The lab records a source by basename only. Where two sources share one, it cannot say which was
    # captured, so neither is adopted: the watcher reads both, which costs a read and misleads nobody.
    counts: dict = {}
    for path in stats:
        counts[os.path.basename(path)] = counts.get(os.path.basename(path), 0) + 1
    for path, st in stats.items():
        if path in cs:
            report["already"] += 1
            continue
        if counts[os.path.basename(path)] > 1:
            report["ambiguous"] += 1
            continue
        known = captured.get(os.path.basename(path))
        if not known:
            report["uncaptured"] += 1
            continue
        if not all(current(r, known["summaries"].get(r) or {}) for r in known["runs"]):
            report["unrecorded"] += 1
            continue
        if len(known["identities"]) > 1:
            # The lab holds runs of more than one session under this basename -- from a root not
            # being seeded now, or a source since moved -- so it cannot say which ones are this file's.
            report["ambiguous"] += 1
            continue
        if st.st_mtime_ns + SEED_MARGIN_NS > known["captured_ns"]:
            report["stale"] += 1
            continue
        open_at = [parse_ts(last_ts[r]) for r in known["runs"] if last_ts.get(r)]
        open_at = [t for t in open_at if t is not None]
        cs[path] = cursors.cur(size=st.st_size, mtime_ns=st.st_mtime_ns, runs=known["runs"],
                               open_until=(max(open_at) + gap_s) if open_at else 0.0, seeded=True,
                               gap_s=gap_s)
        report["seeded"] += 1
    if report["seeded"]:
        cursors.save(cursors.path_for(runs_root), cs)
        # Every adopted source was just checked against its capture, which is what a full sweep
        # would have established. Leaving the sweep clock at zero made the first --watch ignore every
        # cursor seeding wrote and read the whole corpus anyway.
        presence.write(runs_root, last_full_sweep_at=now)
    return report
