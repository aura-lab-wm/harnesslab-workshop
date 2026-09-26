"""Crash debris in captured/: find it, prove it is debris, and only then deal with it.

A writer killed at the wrong moment leaves three kinds of scratch behind, and nothing else here ever
removes them:

  <run>.tmp-<pid>/   regen.regenerate() writes a segment here before swapping it in.
  <run>.old-<pid>/   regen._swap_in() parks the previous run here for the length of the swap.
  .tmp-*             an atomic writer's mkstemp() file (index.jsonl, sidecars, cursors, presence).

Something is debris only when BOTH hold:

  * its writer is provably not running: the pid in its name is gone, or the capture lock is free --
    every writer into captured/ swaps under that lock, so a free lock means nobody is mid-swap. A
    `.tmp-*` file carries no pid, so for it only the lock can say so;
  * it is older than a safety age, judged by the NEWEST mtime anywhere inside it, so a directory
    still being written into is never called old.

A `<run>.old-<pid>/` whose `<run>/` is MISSING is the one case where removal would be data loss: the
process died between parking the old run and moving the new one in, and the old copy is the only
copy. That copy is restored to `<run>/`, never deleted. With more than one, the newest is restored
and the rest become ordinary debris. An old copy that is not a whole run (no ledger.jsonl) is kept.
A restore cannot tell a killed swap from a run something removed on purpose, so the remover is the
one that has to leave nothing behind: orphans.py deletes a pruned run's scratch with the run, under
the same lock.

Report-only unless `apply=True`, and removal happens only while this process holds the capture lock.
Only names matching the three patterns above, directly under captured/, are ever considered.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from typing import Optional

from . import presence

from .lock import CaptureLock, CaptureLocked

#: A scratch directory an hour old cannot belong to a swap in progress: a whole-file regeneration of
#: an 11 MB session measured 0.11 s.
DEBRIS_AGE_S = 3600.0

_SCRATCH_DIR = re.compile(r"^(?P<run>[^/]+)\.(?P<kind>tmp|old)-(?P<pid>\d+)$")
_TMP_FILE = re.compile(r"^\.tmp-[^/]+$")


def pid_alive(pid: int) -> bool:
    """False only when the pid is provably gone. Anything uncertain answers True."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:                  # EPERM: it exists, it is just not ours
        return True
    return True


def _newest_mtime(path: str) -> float:
    newest = os.lstat(path).st_mtime
    if os.path.isdir(path) and not os.path.islink(path):
        for d, dirs, files in os.walk(path):
            for n in dirs + files:
                try:
                    newest = max(newest, os.lstat(os.path.join(d, n)).st_mtime)
                except OSError:
                    continue
    return newest


#: Runs a prune deleted on purpose whose crash scratch it could not remove. Written by orphans.py,
#: read here: without it a restore puts a pruned run back with no index row and no cursor, invisible
#: to every later prune and to every aggregate. A file, not an inference, because only the prune knows.
PRUNED = ".pruned.json"


def forget_pruned(cap: str, run_ids) -> None:
    """Drop runs from the pruned record once nothing of them is left in the lab.

    Run ids repeat -- they hash the session and the source's name -- so a record kept forever would
    refuse to restore a LATER capture of the same id from a genuinely killed swap.
    """
    keep = pruned_runs(cap) - set(run_ids or ())
    path = os.path.join(cap, PRUNED)
    try:
        if keep:
            fd, tmp = tempfile.mkstemp(dir=cap, prefix=".tmp-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"run_ids": sorted(keep)}, f, indent=2, sort_keys=True)
            os.replace(tmp, path)
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def pruned_runs(cap: str) -> set:
    try:
        with open(os.path.join(cap, PRUNED), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return set()
    return {str(r) for r in doc.get("run_ids") or []} if isinstance(doc, dict) else set()


def _classify(cap: str, now: float, min_age_s: float, lock_free: bool) -> tuple[list, list]:
    debris: list = []
    refused: list = []
    try:
        names = sorted(os.listdir(cap))
    except OSError:
        return debris, refused
    olds_by_run: dict = {}
    # A run that is BACK -- captured again under the same id, which ids do repeat -- is not the run
    # that was pruned, and the record must not outlive it or a killed swap could never be restored.
    pruned = {r for r in pruned_runs(cap) if not os.path.lexists(os.path.join(cap, r))}
    for name in names:
        path = os.path.join(cap, name)
        m = _SCRATCH_DIR.match(name)
        if m and os.path.isdir(path) and not os.path.islink(path):
            kind, pid = f"{m.group('kind')}_dir", int(m.group("pid"))
            entry = {"name": name, "kind": kind, "pid": pid, "run_id": m.group("run")}
            writer_gone = (not pid_alive(pid)) or lock_free
        elif _TMP_FILE.match(name) and os.path.isfile(path) and not os.path.islink(path):
            entry = {"name": name, "kind": "tmp_file"}
            writer_gone = lock_free
        else:
            continue
        try:
            entry["age_s"] = round(now - _newest_mtime(path), 3)
        except OSError:
            continue                                   # vanished while looking: not ours to judge
        if not writer_gone:
            refused.append(dict(entry, reason="writer_alive" if "pid" in entry else "lock_busy"))
            continue
        if entry["kind"] == "old_dir":
            # Age is judged per run below: a copy whose run is MISSING is the run, and the writer is
            # already proven gone, so waiting an hour to put it back only hides it from every reader.
            olds_by_run.setdefault(entry["run_id"], []).append(entry)
            continue
        if entry["age_s"] < min_age_s:
            refused.append(dict(entry, reason="young"))
            continue
        debris.append(dict(entry, action="delete"))

    for run_id, olds in olds_by_run.items():
        if os.path.lexists(os.path.join(cap, run_id)):
            # The run is there; these are spare copies, and deleting waits out the age guard.
            young = [e for e in olds if e["age_s"] < min_age_s]
            refused.extend(dict(e, reason="young") for e in young)
            debris.extend(dict(e, action="delete") for e in olds if e not in young)
            continue
        # The run itself is missing: the newest whole copy is the run -- IF the lab still knows the
        # run. A prune that could not remove a run's scratch leaves exactly this shape behind, and
        # restoring it there recreated a run with no index row and no cursor: invisible to every
        # later prune and to every aggregate. A killed swap always has its row, because the run it
        # was replacing was already indexed.
        olds.sort(key=lambda e: e["age_s"])
        if run_id in pruned:
            # A prune deleted this run and could not remove this copy of it. Restoring it would
            # recreate a run the lab has no row and no cursor for -- so the copy goes instead, which
            # is what the prune wanted. Refusing to delete it too left it in the lab for good.
            debris.extend(dict(e, action="delete", pruned=True) for e in olds)
            continue
        restore = next((e for e in olds
                        if os.path.isfile(os.path.join(cap, e["name"], "ledger.jsonl"))), None)
        if restore is None:
            refused.extend(dict(e, reason="unrestorable") for e in olds)
            continue
        debris.append(dict(restore, action="restore"))
        debris.extend(dict(e, action="delete") for e in olds if e is not restore)
    debris.sort(key=lambda e: (e["action"] != "restore", e["name"]))
    return debris, refused


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _capture_lock_free(runs_root: str):
    """True when no capture writer holds the lock, False when one does, None when it cannot be asked.

    A shared, non-blocking probe, released at once: it answers the question without making anybody
    wait on it, and it never creates the file.
    """
    import fcntl
    path = os.path.join(runs_root, "captured", ".lock")
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return True                        # no writer has ever run in this lab
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    except OSError:
        return None
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def sweep(runs_root: str, apply: bool = False, now: Optional[float] = None,
          min_age_s: float = DEBRIS_AGE_S) -> dict:
    """Find crash debris under <runs_root>/captured/. Remove or restore it only with `apply`."""
    now = time.time() if now is None else float(now)
    cap = os.path.join(runs_root, "captured")
    report = {"captured": os.path.realpath(cap), "apply": bool(apply), "locked": False,
              "min_age_s": float(min_age_s), "debris": [], "refused": [],
              "counts": {"debris": 0, "restore": 0, "refused": 0, "removed": 0, "restored": 0}}
    if not os.path.isdir(cap):
        return report

    # A REPORT takes no lock. Holding the exclusive capture lock while walking every scratch
    # directory made a read block every manual import -- and when a writer held it, the report
    # refused to judge anything and still said success. It asks instead (shared, non-blocking, the
    # same question presence.sniffer_alive asks) and lets go before looking.
    lock = None
    if apply:
        lock = CaptureLock(runs_root)
        try:
            lock.__enter__()
        except CaptureLocked:
            lock = None
            report["locked"] = True
        except OSError as e:
            # A lab whose captured/ cannot be written cannot be locked either, and dying here left
            # the operator with a traceback instead of a report saying what it found.
            lock = None
            report["locked"] = True
            report["lock_error"] = f"{type(e).__name__}: {e}"
        free = lock is not None
    else:
        free = _capture_lock_free(runs_root)
        # None is "cannot be asked" -- an unreadable lock file, or a mount with no flock. Everything
        # is then refused, so the report is a floor; calling that an unlocked lab made the CLI exit 0
        # "clean" and let the watcher publish that floor over the last true count.
        report["locked"] = free is not True
    lock_free = bool(free)
    try:
        debris, refused = _classify(cap, now, min_age_s, lock_free=lock_free)
        if apply and lock is not None:
            for e in debris:
                path = os.path.join(cap, e["name"])
                try:
                    if e["action"] == "restore":
                        target = os.path.join(cap, e["run_id"])
                        if os.path.lexists(target):
                            raise FileExistsError(target)      # appeared since: never overwrite it
                        os.rename(path, target)
                        report["counts"]["restored"] += 1
                    else:
                        _remove(path)
                        report["counts"]["removed"] += 1
                    e["done"] = True
                except OSError as err:
                    e["done"] = False
                    e["error"] = f"{type(err).__name__}: {err}"
        else:
            for e in debris:
                e["done"] = False
        # Both of these READ and WRITE .pruned.json, whose other writer (orphans._note_pruned) holds
        # the capture lock to do it. Run unlocked, a prune landing in between could add a run this
        # then dropped -- and a later sweep would restore what the prune deleted.
        if apply and lock is not None:
            back = {r for r in pruned_runs(cap) if os.path.lexists(os.path.join(cap, r))}
            if back:
                forget_pruned(cap, back)
            try:
                done = {e["run_id"] for e in debris
                        if e.get("pruned") and e.get("done") and e.get("run_id")}
                left = {m.group("run") for name in os.listdir(cap)
                        for m in [_SCRATCH_DIR.match(name)] if m}
                if done - left:
                    forget_pruned(cap, done - left)
            except OSError:
                pass                 # bookkeeping: never worth losing a report that has the deletions in it
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)

    if apply and lock is not None:
        # The count on the Capture page is written by the watcher's daily hook. After a removal by
        # hand it would otherwise keep publishing debris that is no longer there, for up to a day.
        try:
            # Counted the way the watcher's report-only pass counts it: entries still to be deleted.
            left = sum(1 for e in debris if e["action"] == "delete" and not e.get("done"))
            if os.path.exists(presence.path_for(runs_root)):
                presence.write(runs_root, debris=int(left), debris_checked_at=float(now))
        except Exception:
            pass                     # a report of real deletions is worth more than its bookkeeping

    report["debris"], report["refused"] = debris, refused
    restores = sum(1 for e in debris if e["action"] == "restore")
    # `debris` is what is to be DELETED. Counting a run waiting to be restored as debris published a
    # recoverable run to the Capture page as junk sitting in the lab.
    report["counts"].update(debris=len(debris) - restores, refused=len(refused), restore=restores)
    return report
