"""What the sniffer is doing, and how to talk back to it.

Two documents under <runs_root>/captured/, one per direction:

  presence.json   sniffer -> everyone. Counts, states and timestamps ONLY. A captured task id
                  embeds a slug of the first prompt (see results_scope), and this document is read
                  by a separate process and rendered into a menu title, so nothing that could carry
                  content is allowed in. Errors are recorded as a COUNT; the messages stay in the
                  capture report where they belong.
  control.json    console or menubar -> sniffer. Two fields: `paused`, and `nudge_at`, the epoch of
                  the most recent capture-now request.

Both are read defensively. A half-written document must read as "nothing yet" rather than take down
whatever is displaying it -- a status line that crashes is worse than one that says it does not know.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Optional

SCHEMA = 1

#: The whole vocabulary. `state` becomes a menu title in another process, so an unknown value is
#: clamped to "error" rather than passed through to be rendered.
STATES = ("never_started", "idle", "scanning", "capturing", "paused", "locked", "error", "stopped")

MAX_ERROR_CHARS = 200


def _dir(runs_root: str) -> str:
    return os.path.join(runs_root, "captured")


def path_for(runs_root: str) -> str:
    return os.path.join(_dir(runs_root), "presence.json")


def control_path(runs_root: str) -> str:
    return os.path.join(_dir(runs_root), "control.json")


def blank(interval_s: float = 60.0, roots_n: int = 0) -> dict:
    return {"schema": SCHEMA, "state": "never_started", "pid": 0, "interval_s": float(interval_s),
            "roots": int(roots_n), "last_tick_at": 0.0, "next_tick_at": 0.0, "heartbeat_at": 0.0, "last_full_sweep_at": 0.0,
            "last_capture_at": 0.0, "errors": 0, "error": "",
            # Crash debris in captured/ -- scratch left by a killed writer -- counted by the daily
            # full sweep (capture/sweep.py) and never removed by it. A count and when it was taken,
            # kept apart because "none" and "nobody has looked" are different answers. _clamp() drops
            # any field not named here, so a writer that publishes these needs them declared.
            "debris": 0, "debris_checked_at": 0.0,
            "totals": {"runs_indexed": 0, "open_runs": 0, "files_seen": 0,
                       "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "unmeasured_runs": 0},
            "progress": {"done": 0, "of": 0}}


def _clamp(doc: dict) -> dict:
    """A document safe to render. Unknown state -> error; error flattened to one short line."""
    out = blank()
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in out:
                out[k] = v
    if out.get("state") not in STATES:
        out["state"] = "error"
    err = str(out.get("error") or "")
    out["error"] = " ".join(err.split())[:MAX_ERROR_CHARS]
    out["schema"] = SCHEMA
    if not isinstance(out.get("totals"), dict):
        out["totals"] = blank()["totals"]
    else:
        merged = blank()["totals"]
        merged.update({k: v for k, v in out["totals"].items() if k in merged})
        out["totals"] = merged
    return out


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _atomic(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read(runs_root: str) -> dict:
    """The current document, always a dict. Absent or torn reads as never_started."""
    return _clamp(_read_json(path_for(runs_root), None) or {})


def schema_of(runs_root: str):
    raw = _read_json(path_for(runs_root), None)
    return raw.get("schema") if isinstance(raw, dict) else None


def write(runs_root: str, **fields) -> dict:
    """Merge `fields` into the document and persist it atomically."""
    doc = read(runs_root)
    for k, v in fields.items():
        if k == "totals" and isinstance(v, dict):
            doc["totals"].update(v)
        else:
            doc[k] = v
    doc = _clamp(doc)
    _atomic(path_for(runs_root), doc)
    return doc


def summarise(report: dict, at: float) -> dict:
    """What a capture PASS says about itself. Deliberately lossy: paths and messages do not come
    along.

    It reports no `totals`, and that is the whole point. `totals` describes the CORPUS -- every
    reader takes `runs_indexed` for the size of the lab -- and this function was filling those
    fields with the pass's own numbers, leaving the caller to "correct" them on the next write.
    Between the two writes presence.json on disk said a corpus of 20,538 runs held 27, and the
    readers are a menu-bar app polling every five seconds and GET /api/capture/status. It was
    caught in the wild: the menu's plot differences consecutive readings and drew a scan that had
    captured 20,511 runs.

    The corpus totals now travel with this in ONE write (see `record_capture(totals=...)`), so
    there is no moment in between for a reader to land in.
    """
    report = report or {}
    return {"last_capture_at": float(at),
            "errors": len(report.get("errors") or [])}


def record_capture(runs_root: str, report: dict, at: Optional[float] = None,
                   totals: Optional[dict] = None) -> dict:
    """One write: what the pass did, and what the corpus now holds.

    `totals` is the caller's corpus count -- the sniffer has it already, and passing it here rather
    than writing it a line later is what closes the window described in `summarise`.
    """
    fields = summarise(report, at if at is not None else time.time())
    if totals is not None:
        fields["totals"] = totals
    return write(runs_root, **fields)


# --------------------------------------------------------------------------- control
def read_control(runs_root: str) -> dict:
    doc = _read_json(control_path(runs_root), None)
    if not isinstance(doc, dict):
        doc = {}
    return {"paused": bool(doc.get("paused")), "nudge_at": float(doc.get("nudge_at") or 0)}


def write_control(runs_root: str, paused: bool, nudge_at: float) -> None:
    _atomic(control_path(runs_root), {"paused": bool(paused), "nudge_at": float(nudge_at)})


def nudge(runs_root: str, now: float) -> None:
    """Capture now. Never changes `paused`: asking for a pass is not asking to resume."""
    c = read_control(runs_root)
    write_control(runs_root, c["paused"], float(now))


def set_paused(runs_root: str, paused: bool) -> None:
    c = read_control(runs_root)
    write_control(runs_root, bool(paused), c["nudge_at"])


def sniffer_alive(runs_root: str):
    """True if a watcher holds the sniffer lock, False if none does, None if this cannot be known.

    Asked of the LOCK, not of presence.json: after kill -9 the document still says idle, but the
    kernel has released the flock. A shared, non-blocking probe on an existing file -- never created
    here, so asking has no side effect on disk.

    Three answers because two were wrong. Contention (BlockingIOError) means a watcher is there. Any
    other failure -- ENOLCK on an NFS or container mount -- says nothing about a watcher, and
    treating it as "held" reported a long-dead process as live.
    """
    import fcntl
    path = os.path.join(_dir(runs_root), ".sniffer.lock")
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False                           # no watcher has ever started in this lab
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return None
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def sniffer_pid(runs_root: str):
    """The pid of a watcher known to be LIVE, else None -- never a pid merely read from a file."""
    return (read(runs_root).get("pid") or None) if sniffer_alive(runs_root) is True else None
