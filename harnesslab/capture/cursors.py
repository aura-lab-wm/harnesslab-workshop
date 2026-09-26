"""What the sniffer remembers between passes, and nothing else.

A full pass over this machine's corpus reads 19,380 files and takes minutes. A cursor records what a
source looked like when it was last captured -- its size and mtime, the runs it produced, and when
its open run stops being open -- so an unchanged source is skipped without being opened. That is the
whole difference between a loop that can run every minute and one that cannot run at all.

Skipping on (size, mtime_ns) is deliberately cheap and deliberately fallible: a rewrite that lands on
the same size AND the same nanosecond timestamp would be missed. Hashing every source would cost the
read the cursor exists to avoid. The periodic full sweep is what catches that case.

Run ids are kept because the index outlives the source. When a source vanishes, the runs it produced
are still in index.jsonl, and only the cursor knows which ones were its -- forget them and they are
orphaned silently.
"""
from __future__ import annotations

import json
import os
import tempfile

SCHEMA = 1


def cur(size: int = -1, mtime_ns: int = -1, runs=None, open_until: float = 0.0,
        seeded: bool = False, dropped=None, gap_s: float = 0.0) -> dict:
    """One source's cursor. The defaults describe a source nothing is known about.

    `dropped` is every run this source USED to produce and no longer does -- a rewrite that left it
    with fewer segments. Its directory and index row are still in the lab, and without this memory
    the next capture overwrites `runs` and nothing remembers whose they were (see orphans.py).

    `gap_s` is the session gap these run ids were cut at. Reading the same file at a different gap
    yields different ids, so a prune that re-reads the corpus at its own gap and compares against
    these would call live runs missing (orphans.py refuses when the two disagree). 0 means a cursor
    written before this was recorded.
    """
    return {"size": int(size), "mtime_ns": int(mtime_ns), "runs": list(runs or []),
            "open_until": float(open_until), "seeded": bool(seeded), "gap_s": float(gap_s),
            "dropped": sorted(set(str(r) for r in (dropped or [])))}


def carry_dropped(previous, runs) -> list:
    """What a source dropped: everything its previous cursor produced or had dropped, less `runs`."""
    if not isinstance(previous, dict):
        return []
    before = set(previous.get("runs") or []) | set(previous.get("dropped") or [])
    return sorted(str(r) for r in before - set(runs or []))


def path_for(runs_root: str) -> str:
    return os.path.join(runs_root, "captured", "cursors.json")


def load(path: str) -> dict:
    """Every cursor, or {} when the file is absent or unreadable. A torn cursor file costs a full
    pass, which is slow; raising here would cost the sniffer, which is worse."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    if doc.get("schema", SCHEMA) != SCHEMA:
        # A file written by a newer build. Reading it as this schema could skip sources on fields
        # that mean something else; forgetting costs one full pass and misreads nothing.
        return {}
    got = doc.get("cursors")
    return got if isinstance(got, dict) else {}


def save(path: str, cursors: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"schema": SCHEMA, "cursors": cursors}, f, ensure_ascii=False,
                  indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def unchanged(cursor: dict, st: os.stat_result) -> bool:
    """True when this source is byte-for-byte the one the cursor describes."""
    if not isinstance(cursor, dict):
        return False
    size, mtime = cursor.get("size", -1), cursor.get("mtime_ns", -1)
    if size < 0 or mtime < 0:
        return False                       # nothing known: never claim it is unchanged
    return int(size) == int(st.st_size) and int(mtime) == int(st.st_mtime_ns)


def due(cursors: dict, now: float, gap_s: float) -> set:
    """Sources holding an open run whose quiet time has elapsed.

    An open run closes by the clock, not by anything happening to its file, so these must be
    rescanned even though nothing about them changed -- otherwise a finished session stays "open"
    forever and never reaches the index.
    """
    out = set()
    for path, c in (cursors or {}).items():
        if not isinstance(c, dict):
            continue
        until = float(c.get("open_until") or 0.0)
        if until and now >= until:
            out.add(path)
    return out


def claimed(cursors: dict) -> set:
    """Every run id any cursor still produces, vanished sources included. Dropped runs are not."""
    out: set = set()
    for c in (cursors or {}).values():
        if isinstance(c, dict):
            out.update(str(r) for r in (c.get("runs") or []))
    return out
