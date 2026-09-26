"""The watcher's own activity log: what the corpus held, tick by tick.

Nothing else in this spine remembers what it looked like a minute ago. `presence.json` is a snapshot
-- it says what is true now and nothing about what was true before -- so no reader can answer "how
many today", "is it faster than it was", or "what happened while I was not looking". The menu-bar app
kept a ring in memory, which goes on every restart and cannot answer any of those either.

The WATCHER keeps this, not a reader. It ticks anyway, it is the single writer under the capture
lock, and a file it owns outlives every app that reads it. A reader keeping its own parallel record
of the corpus is a second source of truth, free to drift from the one the watcher writes.

Counts only, exactly as presence.json: no path, no task id, no message. A captured task id embeds a
slug of the first prompt, so nothing here may carry one.

Append-only JSONL, the same shape as index.jsonl, trimmed from the front when it grows past
MAX_ROWS. A torn line -- a write interrupted by a kill -- is skipped rather than costing the file.
"""
from __future__ import annotations

import json
import os
import tempfile

#: One row per tick. At the default 60-second interval that is a little over a day of history,
#: which is what the longest range a reader offers needs; beyond that the file is trimmed from the
#: oldest end. Each row is about 90 bytes, so the whole log is well under 2 MB.
MAX_ROWS = 20_000

#: How far past MAX_ROWS the file may grow before it is rewritten. Trimming on every append would
#: rewrite the whole file once a minute for no gain.
SLACK = 500


def path_for(runs_root: str) -> str:
    return os.path.join(runs_root, "captured", "activity.jsonl")


def record(runs_root: str, at: float, runs: int, open_runs: int, files: int,
           cost_usd: float) -> None:
    """Append one sample. Never raises: a watcher must not die because a log line would not go."""
    path = path_for(runs_root)
    row = {"t": float(at), "runs": int(runs), "open": int(open_runs),
           "files": int(files), "cost": round(float(cost_usd), 6)}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return
    _trim(path)


def read(runs_root: str, limit: int = MAX_ROWS) -> list[dict]:
    """Every sample, oldest first. An absent log is no history -- which is not the same as a
    history of zeros, and a reader is expected to say so."""
    path = path_for(runs_root)
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue                      # a torn write, not a reason to lose the rest
                if isinstance(row, dict) and isinstance(row.get("t"), (int, float)):
                    out.append(row)
    except OSError:
        return []
    return out[-limit:] if limit and len(out) > limit else out


def _trim(path: str) -> None:
    """Keep the newest MAX_ROWS, rewritten atomically, and only once the file has earned it."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) <= MAX_ROWS + SLACK:
        return
    keep = lines[-MAX_ROWS:]
    d = os.path.dirname(path)
    try:
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-activity-", suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)                        # never leave scratch behind for the sweep to find
        except (OSError, UnboundLocalError):
            pass
