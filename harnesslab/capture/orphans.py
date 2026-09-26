"""Orphan runs: captured runs that no source produces any more.

Two ways a run is orphaned, and both leave its directory AND its index.jsonl row behind -- a stale
row is counted by every metric and cost aggregate:

  segment_dropped  the source was rewritten with fewer segments. The watcher's cursor records the
                   runs a source stopped producing as `dropped` (sniffer.tick), because the capture
                   that notices overwrites `runs`, and nothing else remembers whose they were.
  source_deleted   the source is gone. Its cursor still lists every run it produced. REPORTED ONLY:
                   Claude Code deletes session files after `cleanupPeriodDays` (30 by default), and
                   the captured run plus its archived source (archive.py) are then the only record
                   of that work. Removing it needs `deleted_sources` asked for on top of `apply`.

Evidence, never a guess. A run is an orphan only when ALL of these hold:

  * a cursor names it, as dropped by a source that is still readable, or as produced by a source
    that is provably deleted;
  * a fresh read of every readable source under the given roots -- every source `discover` finds
    there, not only the ones a cursor names -- does not produce it;
  * no source whose state cannot be established right now names it at all, and no source of that
    name anywhere under the roots failed to be read.

The walk past the cursors is what makes a MOVED source safe. A run id is derived from the session
and the file's basename, not its directory, so the same file under a renamed project produces the
same runs -- while its old path is provably gone and its new path has no cursor yet. Reading only
cursored sources called those live runs orphans.

"Provably deleted" means the nearest ancestor of the missing path that still exists lists fine, sits
inside one of the roots, is not empty, and does not contain the next component (nor an iCloud
`.name.icloud` placeholder for it). A permission error, a directory that cannot be listed, a dangling
symlink, an empty directory (what an unmounted volume leaves at its mount point), or a root that is
itself missing (an unmounted network volume makes everything under it vanish at once) proves nothing:
those sources are `unreachable`, and their runs are left alone. So are sources that cannot be read
(`unreadable` -- including an uncursored source of the same name found anywhere under the roots), are
no longer claimed by an adapter (`unclaimed`), lie outside the roots (`out_of_scope`), or whose fresh read no
longer produces a run their cursor says they still produce (`inconsistent` -- a different gap, or a
changed adapter, says the READ differs, not the source). A run directory whose ledger names a
different source is `foreign_directory` and is never removed.

Report-only unless `apply=True`. The search runs under the sniffer lock alone -- the watcher rewrites
the cursors it reads, but a manual import must not wait on a full re-read -- and the capture lock is
taken only for the removal, where the short list is re-established before anything is deleted.

The removal goes directory first, one run at a time, so a run that cannot be deleted keeps its row and
its cursor and is found again; then the index, inflight and identity entries of the runs that really
went; then any `<run>.tmp-<pid>/` or `<run>.old-<pid>/` crash scratch of theirs (sweep.py would
otherwise RESTORE it over the hole the prune left); and the cursor memory last, so an interrupted
prune is found again by the next one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from typing import Optional

from . import cursors, presence
from .adapters import discover
from .lock import CaptureLock, CaptureLocked
from .regen import run_ids as _fresh_run_ids

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+~-]*$")


def _within(path: str, roots: list) -> bool:
    for r in roots:
        base = r.rstrip(os.sep) or os.sep
        if path == base or path.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


def _provably_deleted(path: str, roots: list) -> bool:
    """True only when a listing we trust says the path is not there."""
    cur = path
    while True:
        parent = os.path.dirname(cur)
        if parent == cur or not _within(parent, roots):
            return False                         # ran out of the roots: a missing root proves nothing
        try:
            names = os.listdir(parent)
        except (FileNotFoundError, NotADirectoryError):
            cur = parent                         # missing too; ask one level up
            continue
        except OSError:
            return False                         # exists but cannot be listed
        if not names:
            # An empty directory is what an unmounted volume leaves at its mount point, and what an
            # evicted or emptied directory leaves too. Deletion and absence look identical here.
            return False
        base = os.path.basename(cur)
        if f".{base}.icloud" in names:
            return False                         # iCloud evicted it; the bytes come back on demand
        # Present in the listing but not reachable is a dangling link or an offline mount point.
        return base not in names


def _under(directory: str, path: str) -> bool:
    """Whether `path` lies inside `directory` (or is it)."""
    d = os.path.realpath(directory or "")
    p = os.path.realpath(path or "")
    return bool(d) and (p == d or p.startswith(d + os.sep))


def _source_state(path: str, roots: list, gap_s: float) -> tuple:
    """(state, fresh run ids). state: read | deleted | unreachable | unreadable | unclaimed | out_of_scope."""
    if not _within(path, roots):
        return "out_of_scope", set()
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return ("deleted", set()) if _provably_deleted(path, roots) else ("unreachable", set())
    except OSError:
        return "unreachable", set()
    # Asked about the path AS IT SITS UNDER ITS ROOT, not as a bare file: discover([file]) is the
    # explicit-request branch, where every descriptor may answer, and that is how a source could be
    # read here by one adapter and captured by another.
    try:
        found, errors = discover([path], only_under=roots)
    except Exception:
        return "unreadable", set()
    if errors:
        return "unreadable", set()
    if not found:
        return "unclaimed", set()
    (_, adapter), = found[:1]
    try:
        return "read", set(_fresh_run_ids(path, gap_s, adapter=adapter))
    except Exception:
        return "unreadable", set()


def _ledger_source(run_dir: str) -> Optional[str]:
    try:
        with open(os.path.join(run_dir, "ledger.jsonl"), encoding="utf-8") as f:
            return (json.loads(f.readline() or "{}") or {}).get("source_path") or None
    except (OSError, ValueError, AttributeError):
        return None


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _atomic_write(path: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-",
                               suffix=os.path.splitext(path)[1])
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _indexed(index_path: str) -> set:
    out = set()
    try:
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.add(json.loads(line)["run_id"])
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return out


def find(roots: list, runs_root: str, gap_s: float, ignore_unreadable: bool = False) -> dict:
    """Orphans and refusals, reading sources and the lab, writing nothing."""
    real_roots = [os.path.realpath(r) for r in roots]
    cap = os.path.join(runs_root, "captured")
    cs = cursors.load(cursors.path_for(runs_root))
    # A run id is cut at a gap. Re-reading the corpus at a DIFFERENT gap yields different ids, so
    # every fresh read would disagree with every cursor and a live run would read as one nobody
    # produces any more -- proven to delete a live run's directory and its index row. Refuse instead.
    recorded = {float(c.get("gap_s") or 0) for c in cs.values()
                if isinstance(c, dict) and float(c.get("gap_s") or 0) > 0}
    if recorded and float(gap_s) not in recorded:
        return {"orphans": [], "refused": [{"reason": "gap_mismatch", "run_ids": []}],
                "sources_checked": len(cs), "gap_mismatch": sorted(recorded), "_cursors": cs,
                "_states": {}, "_claimants": {}}
    # Not short-circuited on stats alone, though a healthy lab could be spotted that way: the
    # refusals ARE the answer when a source cannot be read, out of scope, or disagrees with its
    # cursor, and none of those can be seen without reading. A faster pass that stopped reporting
    # them would exit 0 on a lab it never looked at.
    states: dict = {}
    fresh_all: set = set()
    for path, c in sorted(cs.items()):
        if not isinstance(c, dict):
            continue
        state, fresh = _source_state(path, real_roots, gap_s)
        states[path] = state
        fresh_all |= fresh
        if state == "read":
            c_runs = set(c.get("runs") or [])
            if c_runs - fresh:
                states[path] = "inconsistent"
            elif not fresh and (c_runs or c.get("dropped")):
                # Still there, still claimed, and it now produces NOTHING while this lab holds runs
                # it produced. A file damaged to that point is indistinguishable from one legitimately
                # emptied, and in the first case the capture is the last copy of that work -- so it is
                # never read as "the source dropped everything". The `inconsistent` check above cannot
                # catch this: by the time a capture has recorded the emptiness, `runs` is empty too.
                states[path] = "empty_read"

    # Every OTHER readable source under the roots, cursor or not. A cursor is not a census: a source
    # that moved (a renamed project directory), or one captured only by --backfill, has no cursor at
    # its new path, while the old path is provably gone -- and the run id is the same file's, so the
    # cursored path being deleted says nothing about the run. Without this walk those live runs are
    # "orphans". A source we cannot read here blocks its basename's candidates instead.
    unread_bases: set = set()
    scanned = True
    try:
        others, errors = discover(real_roots)
    except Exception:
        others, errors, scanned = [], [], False
    blocked_dirs: list = []
    for err in errors or ():
        if isinstance(err, dict) and err.get("kind") == "directory":
            # A directory nobody could enter hides the sources under it, and a run id is derived
            # from a session id and a BASENAME -- so a copy of any source could be in there,
            # producing runs this pass will never see. By default that stops the pass: nothing is an
            # orphan while part of the corpus is invisible.
            #
            # One permanently unreadable folder (~/Library under a ~ root is the usual one) would
            # otherwise make the prune inert for good, so `ignore_unreadable` is the operator saying
            # they accept that risk. A candidate whose OWN claiming source is in there is refused
            # either way, without a rule of its own: _source_state cannot stat it, so it reads as
            # unreachable and everything it names is blocked (there is a test).
            blocked_dirs.append(os.path.realpath(err.get("path") or ""))
            if not ignore_unreadable:
                scanned = False
            continue
        p = err.get("path") if isinstance(err, dict) else (err[0] if isinstance(err, (tuple, list)) else None)
        if p:
            unread_bases.add(os.path.basename(p))
    for path, adapter in others:
        if path in states:
            continue                              # already read above, with its cursor's evidence
        try:
            fresh_all |= set(_fresh_run_ids(path, gap_s, adapter=adapter))
        except Exception:
            unread_bases.add(os.path.basename(path))

    refused: list = []
    blocked: set = set()
    candidates: dict = {}                  # run_id -> {"reasons", "bases", "paths"}
    blocked_by: list = []
    for path, state in states.items():
        c = cs[path]
        named = set(c.get("runs") or []) | set(c.get("dropped") or [])
        if state in ("deleted", "read"):
            claims = named if state == "deleted" else set(c.get("dropped") or [])
            reason = "source_deleted" if state == "deleted" else "segment_dropped"
            for rid in claims:
                e = candidates.setdefault(rid, {"reasons": set(), "bases": set(), "paths": set()})
                e["reasons"].add(reason)
                e["bases"].add(os.path.basename(path))
                e["paths"].add(path)
        else:
            blocked |= named
            # A cursored source whose fresh content could not be established blocks its NAME, not just
            # the runs its own cursor lists: the file that still produces a run may be this one.
            if state in ("unreadable", "unreachable", "inconsistent"):
                unread_bases.add(os.path.basename(path))
            if named:
                blocked_by.append((state, named))

    # A refusal names the runs that were CANDIDATES, never a source's whole run list: printing every
    # run of an out-of-scope cursor read as a list of runs considered for deletion. None of them was.
    # Every source that could not be used is recorded -- that is the honest account of what this
    # pass could not establish. But run_ids means "runs this refusal kept", so a source we never
    # looked at (out of scope, unclaimed) lists only the ones something else had nominated. Listing
    # its whole run list read as a list of runs considered for deletion; none of them was.
    for state, named in blocked_by:
        blind = state in ("unreadable", "unreachable", "inconsistent")
        refused.append({"reason": state, "run_ids": sorted(named if blind else named & set(candidates))})

    index_ids = _indexed(os.path.join(cap, "index.jsonl"))
    found: list = []
    claimants: dict = {}
    for rid in sorted(candidates):
        # The most protective reason wins when several sources claim one run. Taking the first
        # claimant's reason let the directory names decide which gate applied.
        reasons, bases = candidates[rid]["reasons"], candidates[rid]["bases"]
        reason = "source_deleted" if "source_deleted" in reasons else "segment_dropped"
        if rid in fresh_all or rid in blocked:
            continue
        if not scanned:
            refused.append({"reason": "unreachable", "run_ids": [rid]})
            continue
        if bases & unread_bases:
            # A source with this name, somewhere under the roots, could not be read: it may be the
            # very file that still produces this run.
            refused.append({"reason": "unreadable", "run_ids": [rid]})
            continue
        if not _SAFE_RUN_ID.match(rid):
            refused.append({"reason": "bad_run_id", "run_ids": [rid]})
            continue
        run_dir = os.path.join(cap, rid)
        has_dir = os.path.lexists(run_dir)
        if has_dir:
            owner = _ledger_source(run_dir) if os.path.isdir(run_dir) and not os.path.islink(run_dir) else None
            if owner is None or owner not in bases:
                refused.append({"reason": "foreign_directory", "run_ids": [rid]})
                continue
        found.append({"run_id": rid, "reason": reason, "directory": has_dir, "indexed": rid in index_ids})
        claimants[rid] = sorted(candidates[rid]["paths"])
    out = {"orphans": found, "refused": refused, "sources_checked": len(states), "_cursors": cs,
           "_states": states, "_claimants": claimants}
    if blocked_dirs:
        # Relative to the root it was found under: the reports carry run ids, reasons and counts, and
        # an absolute path here would print part of somebody's home directory into stdout.
        def shown(d):
            for r in real_roots:
                if _under(r, d):
                    return os.path.relpath(d, r)
            return os.path.basename(d)
        out["unreadable_dirs"] = sorted({shown(d) for d in blocked_dirs if d})
    return out


_SCRATCH = re.compile(r"^(?P<run>.+)\.(?:tmp|old)-\d+$")


def _note_pruned(cap: str, run_ids: set) -> None:
    """Record runs this prune deleted whose crash scratch it could not remove (sweep.PRUNED).

    Best effort: a lab that will not take this file is the same lab that would not take the deletion,
    and the report already says what could not be removed.
    """
    from .sweep import PRUNED, pruned_runs
    try:
        _atomic_write(os.path.join(cap, PRUNED),
                      json.dumps({"run_ids": sorted(pruned_runs(cap) | set(run_ids))},
                                 ensure_ascii=False, indent=2, sort_keys=True))
    except OSError:
        pass


def _remove_from_lab(runs_root: str, doomed: set, cs: dict, states: dict) -> dict:
    """Remove each doomed run, and keep going past one that cannot be removed.

    The directories go FIRST, one run at a time. The previous order rewrote the index for the whole
    batch and then deleted; one EACCES aborted everything, so every other doomed run had already
    lost its index row while its directory was still there, the cursors were never saved and no
    report was printed. A run whose directory will not go is left exactly as it was -- row, cursor
    and all -- and the next pass finds it again.

    Killed between the deletions and the index rewrite, the lab is left with rows whose directory is
    gone; the next pass removes those rows, because the cursor memory is still there to name them.
    """
    cap = os.path.join(runs_root, "captured")
    done = {"rows_removed": 0, "removed": 0, "scratch_removed": 0, "failed": []}

    kept = set()
    for rid in sorted(doomed):
        run_dir = os.path.join(cap, rid)
        if not (os.path.isdir(run_dir) and not os.path.islink(run_dir)):
            continue                                    # nothing to delete: the row still goes
        try:
            shutil.rmtree(run_dir)
            done["removed"] += 1
        except OSError as e:
            kept.add(rid)
            done["failed"].append({"run_id": rid, "error": f"{type(e).__name__}: {e}"})
    doomed = doomed - kept

    index_path = os.path.join(cap, "index.jsonl")
    if os.path.exists(index_path):
        kept, dropped = [], 0
        try:
            # `for line in f`, like every other reader of this file: str.splitlines also splits on
            # \x0b, \x85 and U+2028, and a path carrying one of those would have been torn into two
            # fragments that both fail to parse and are written back as two broken rows. The guard
            # takes ValueError too, because a decode failure is the likely one and the deletions
            # above cannot be undone.
            with open(index_path, encoding="utf-8") as f:
                index_lines = list(f)
        except (OSError, ValueError) as e:
            index_lines = []
            done["failed"].append({"run_id": "", "what": "index.jsonl (read)",
                                   "error": f"{type(e).__name__}: {e}"})
        for line in index_lines:
            if not line.strip():
                continue
            try:
                rid = json.loads(line).get("run_id")
            except (ValueError, AttributeError):
                rid = None
            if rid in doomed:
                dropped += 1
            else:
                kept.append(line.rstrip("\n"))
        if dropped:
            try:
                _atomic_write(index_path, "\n".join(kept) + ("\n" if kept else ""))
                done["rows_removed"] = dropped
            except OSError as e:
                # These three writes follow deletions that cannot be undone, so raising here would
                # lose the cursor save and the report along with it. The row stays; the next pass
                # removes it, because the cursor still names the run.
                done["failed"].append({"run_id": "", "what": "index.jsonl",
                                       "error": f"{type(e).__name__}: {e}"})
        else:
            done["rows_removed"] = dropped

    inflight_path = os.path.join(cap, "inflight.json")
    doc = _read_json(inflight_path, None)
    if isinstance(doc, dict) and isinstance(doc.get("runs"), list):
        left = [r for r in doc["runs"] if not (isinstance(r, dict) and r.get("run_id") in doomed)]
        if len(left) != len(doc["runs"]):
            try:
                _atomic_write(inflight_path, json.dumps(dict(doc, runs=left), ensure_ascii=False,
                                                        indent=2, sort_keys=True))
            except OSError as e:
                done["failed"].append({"run_id": "", "what": "inflight.json",
                                       "error": f"{type(e).__name__}: {e}"})

    identity_path = os.path.join(cap, "identity.json")
    doc = _read_json(identity_path, None)
    if isinstance(doc, dict) and isinstance(doc.get("relations"), dict):
        rel = {}
        for rid, v in doc["relations"].items():
            if rid in doomed:
                continue
            if isinstance(v, dict):
                v = dict(v)
                for k in ("supersedes", "superseded_by"):
                    v[k] = [x for x in (v.get(k) or []) if x not in doomed]
                v["forked_from"] = [x for x in (v.get("forked_from") or [])
                                    if not (isinstance(x, dict) and x.get("run_id") in doomed)]
            rel[rid] = v
        if rel != doc["relations"]:
            try:
                _atomic_write(identity_path, json.dumps(dict(doc, relations=rel), ensure_ascii=False,
                                                        indent=2, sort_keys=True))
            except OSError as e:
                done["failed"].append({"run_id": "", "what": "identity.json",
                                       "error": f"{type(e).__name__}: {e}"})

    # A pruned run's crash scratch goes with it. `<run>.old-<pid>/` left beside a missing `<run>/`
    # is what sweep.py RESTORES -- it cannot tell a killed swap from a prune -- so leaving one
    # behind resurrects the run, with no index row, no cursor memory, and nothing able to find it
    # again. We hold the capture lock here, which is the same lock every swap holds.
    stranded: set = set()
    try:
        names = sorted(os.listdir(cap))
    except OSError:
        names = []
    for name in names:
        m = _SCRATCH.match(name)
        if not m or m.group("run") not in doomed:
            continue
        path = os.path.join(cap, name)
        if os.path.isdir(path) and not os.path.islink(path):
            try:
                shutil.rmtree(path)
                done["scratch_removed"] += 1
            except OSError as e:
                # Guarded like the run directory above: the index is already rewritten by now, so
                # raising here would lose the cursor save and the report with it. The scratch stays,
                # and the run is written down as pruned -- otherwise sweep --apply RESTORES this copy
                # into <run>/ and the lab has a run with no index row and no cursor, forever.
                done["failed"].append({"run_id": m.group("run"), "what": "scratch " + name,
                                       "error": f"{type(e).__name__}: {e}"})
                stranded.add(m.group("run"))

    if stranded:
        _note_pruned(cap, stranded)

    changed = False
    for path in list(cs):
        c = cs[path]
        if not isinstance(c, dict):
            continue
        runs = [r for r in (c.get("runs") or []) if r not in doomed]
        dropped = [r for r in (c.get("dropped") or []) if r not in doomed]
        if runs == (c.get("runs") or []) and dropped == (c.get("dropped") or []):
            continue
        changed = True
        if states.get(path) == "deleted" and not runs and not dropped:
            del cs[path]
        else:
            cs[path] = dict(c, runs=runs, dropped=dropped)
    if changed:
        try:
            cursors.save(cursors.path_for(runs_root), cs)
        except OSError as e:
            # The last two writes, and the same reasoning as the three above: the deletions cannot be
            # undone, so a raise here would replace the report with a traceback.
            done["failed"].append({"run_id": "", "what": "cursors.json",
                                   "error": f"{type(e).__name__}: {e}"})

    try:
        if os.path.exists(presence.path_for(runs_root)):
            from .sniffer import _corpus_totals
            presence.write(runs_root, totals=_corpus_totals(runs_root, cs))
    except Exception as e:
        # Not only OSError: _corpus_totals re-reads index.jsonl and raises AttributeError or
        # ValueError on a malformed row. This is the last thing the removal does, after deletions
        # that cannot be undone, so nothing here is worth the report.
        done["failed"].append({"run_id": "", "what": "presence.json",
                               "error": f"{type(e).__name__}: {e}"})
    return done


def _still_orphans(doomed: set, claimants: dict, roots: list, runs_root: str, gap_s: float) -> tuple:
    """Re-establish, under the capture lock, that each doomed run is still an orphan.

    `find` reads every source in the lab, which takes as long as a capture pass; holding the capture
    lock across it would block every manual import for that whole time, which is exactly what the
    lock's own documentation says must not happen. So the search runs unlocked and this re-checks the
    short list once the lock IS held: the cursors as they are now, and a fresh read of each claiming
    source. Anything that moved in between is kept, not removed.
    """
    real_roots = [os.path.realpath(r) for r in roots]
    cs = cursors.load(cursors.path_for(runs_root))
    keep: list = []
    still = set()
    for rid in sorted(doomed):
        paths = claimants.get(rid) or []
        ok = bool(paths)
        for path in paths:
            c = cs.get(path)
            state, fresh = _source_state(path, real_roots, gap_s)
            if state == "deleted":
                named = set((c or {}).get("runs") or []) | set((c or {}).get("dropped") or [])
                if c is None or rid not in named:
                    ok = False                     # the cursor moved on: no longer our evidence
            elif state == "read":
                if rid in fresh or c is None or rid not in set(c.get("dropped") or []):
                    ok = False                     # produced again, or no longer remembered as dropped
            else:
                ok = False                         # unreadable now: nothing is proven
            if not ok:
                break
        if ok:
            still.add(rid)
        else:
            keep.append({"reason": "changed_while_locking", "run_ids": [rid]})
    return still, keep


def prune(roots: list, runs_root: str, gap_s: float, apply: bool = False,
          now: Optional[float] = None, deleted_sources: bool = False,
          ignore_unreadable: bool = False) -> dict:
    """Report orphan runs; with `apply`, remove each one's directory, index row and sidecar entries.

    A `source_deleted` run is reported but NEVER removed unless `deleted_sources` is asked for as
    well: Claude Code deletes its session files after `cleanupPeriodDays` (30 by default), and the
    capture plus its archived source (archive.py) are then the only record of that work. That is the
    corpus, not debris.
    """
    now = time.time() if now is None else float(now)
    report = {"apply": bool(apply), "applied": False, "locked": False, "checked_at": now, "sources_checked": 0,
              "deleted_sources": bool(deleted_sources), "orphans": [], "refused": [],
              "counts": {"orphans": 0, "refused": 0, "removed": 0, "rows_removed": 0,
                         "scratch_removed": 0, "kept": 0}}
    held: list = []
    try:
        if apply:
            # The sniffer lock for the whole pass -- the watcher rewrites cursors.json between batches,
            # and that is the evidence being read. It does not block a manual import; the capture lock
            # does, so that one is taken only for the removal itself.
            try:
                lock = CaptureLock(runs_root, name="sniffer")
                lock.__enter__()
                held.append(lock)
            except CaptureLocked:
                report["locked"] = True
            except OSError as e:
                # A lab whose captured/ cannot be written cannot be locked either (sweep.py says the
                # same): a traceback here would replace the report with a stack trace.
                report["locked"] = True
                report["lock_error"] = f"{type(e).__name__}: {e}"
        result = find(roots, runs_root, gap_s, ignore_unreadable=ignore_unreadable)
        for o in result["orphans"]:
            o["removable"] = bool(deleted_sources or o["reason"] != "source_deleted")
        report.update(orphans=result["orphans"], refused=result["refused"],
                      sources_checked=result["sources_checked"])
        if result.get("gap_mismatch"):
            report["gap_mismatch"] = result["gap_mismatch"]
        if result.get("unreadable_dirs"):
            report["unreadable_dirs"] = result["unreadable_dirs"]
        doomed = {o["run_id"] for o in result["orphans"] if o["removable"]}
        # Nothing removable is still a pass that RAN: `applied` is how a caller tells that from a
        # pass that was refused the lab, and it used to say false for both.
        if apply and not report["locked"] and not doomed:
            report["applied"] = True
        if apply and not report["locked"] and doomed:
            try:
                lock = CaptureLock(runs_root, name="")
                lock.__enter__()
                held.append(lock)
            except CaptureLocked:
                report["locked"] = True
            except OSError as e:
                report["locked"] = True
                report["lock_error"] = f"{type(e).__name__}: {e}"
            if not report["locked"]:
                report["applied"] = True
                doomed, kept = _still_orphans(doomed, result.get("_claimants") or {}, roots, runs_root, gap_s)
                report["refused"].extend(kept)
                for o in report["orphans"]:
                    if o["removable"] and o["run_id"] not in doomed:
                        o["removable"] = False
                if doomed:
                    out = _remove_from_lab(runs_root, doomed, result["_cursors"], result["_states"])
                    report["failed"] = out.pop("failed", [])
                    report["counts"].update(out)
    finally:
        for lock in reversed(held):
            lock.__exit__(None, None, None)
    if apply and not report["applied"]:
        # Nothing ran. Saying "removable" here reads as "removed" in a report where removed is 0.
        for o in report["orphans"]:
            o["removable"] = False
    report.setdefault("failed", [])
    report["counts"].update(orphans=len(report["orphans"]), refused=len(report["refused"]),
                            failed=len(report["failed"]),
                            kept=sum(1 for o in report["orphans"] if not o["removable"]))
    return report
