"""Identity for captured sessions: record-level dedup, conversational uuids, segments, and
resume/fork relations.

Pure functions over `Event` lists and uuid lists. Nothing here touches the filesystem, so every
identity rule in the spec (§5.2, §5.3) is testable without a session file.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Optional

CONV_KINDS = ("user", "assistant", "reasoning", "tool_call", "tool_result", "observation")


def dedup_events(events: list) -> list:
    """Drop events whose non-empty `record_id` was already seen. First occurrence wins.

    Claude Code re-emits whole rows with the same uuid when a session is relocated into a worktree,
    hundreds or thousands of lines apart. Events without a record id are never dropped: transport
    rows carry no uuid, and treating "" as an id would collapse them all into one.
    """
    seen: set[str] = set()
    out = []
    for e in events:
        rid = getattr(e, "record_id", "") or ""
        if rid:
            if rid in seen:
                continue
            seen.add(rid)
        out.append(e)
    return out


def conv_uuids(events: list) -> list[str]:
    """Row uuids of conversational events, in file order, without the ':<block>' suffix, each once."""
    out: list[str] = []
    seen: set[str] = set()
    for e in events:
        rid = getattr(e, "record_id", "") or ""
        if not rid or e.kind not in CONV_KINDS:
            continue
        uuid = rid.split(":", 1)[0]
        if uuid not in seen:
            seen.add(uuid)
            out.append(uuid)
    return out


def parse_ts(ts: str) -> Optional[float]:
    """ISO-8601 (with a trailing Z) to epoch seconds; None when absent or unparseable."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def segment_bounds(events: list, gap_s: float) -> list[tuple[int, int]]:
    """[start, end) index ranges, one per segment.

    A segment starts at a `user` event whose timestamp is at least `gap_s` after the latest timestamp
    seen so far. Two rules make this reproducible from the raw file alone:
      - only a user message can start a segment, so a long tool run never separates a call from its
        result;
      - the gap is measured from the LATEST time seen, not the previous row's, because timestamps are
        not monotonic in real sessions (backward jumps of hours) and file order is the only order.
    """
    if not events:
        return []
    bounds: list[tuple[int, int]] = []
    start, latest = 0, None
    for i, e in enumerate(events):
        if e.kind not in CONV_KINDS:
            continue
        t = parse_ts(e.ts)
        if t is None:
            continue
        if e.kind == "user" and latest is not None and i > start and t - latest >= gap_s:
            bounds.append((start, i))
            start = i
        latest = t if latest is None else max(latest, t)
    bounds.append((start, len(events)))
    return bounds


def split_session(sess, gap_s: float) -> list:
    """One shallow copy of `sess` per segment, with `extra["segment"] = n`. A session-level verdict,
    final patch and exit status describe how the session ENDED, so only the last segment keeps them."""
    bounds = segment_bounds(sess.events, gap_s)
    parts = []
    last = len(bounds) - 1
    for n, (a, b) in enumerate(bounds):
        part = copy.copy(sess)
        part.events = sess.events[a:b]
        part.extra = dict(sess.extra, segment=n)
        if n != last:
            part.hidden_pass, part.outcome_source, part.patch, part.exit_status = None, "", "", ""
        parts.append(part)
    return parts


def relations(runs: dict) -> dict:
    """Resume and fork relations between runs that share a root.

    `runs` maps run_id to that run's conversational uuids in file order (see conv_uuids). Runs are
    related only when their first uuid matches. For each related pair the rule is by content (spec
    §5.2); age and length are deliberately never used, because an abandoned fork is newer AND shorter:

      containment -- one uuid set contains the other: the container `supersedes` the contained run.
                     Identical sets keep the lexically first run id, so the outcome is deterministic.
      divergence  -- neither contains the other: both get `forked_from` the other plus the length of
                     their common prefix, and neither is superseded.

    Returns {run_id: {"supersedes": [...], "superseded_by": [...], "forked_from": [...]}} for every
    run with at least one relation.
    """
    by_root: dict[str, list[str]] = {}
    for rid, uuids in runs.items():
        if uuids:
            by_root.setdefault(uuids[0], []).append(rid)
    out: dict[str, dict] = {}

    def slot(rid: str) -> dict:
        return out.setdefault(rid, {"supersedes": [], "superseded_by": [], "forked_from": []})

    for rids in by_root.values():
        rids = sorted(rids)
        for i, a in enumerate(rids):
            for b in rids[i + 1:]:
                sa, sb = set(runs[a]), set(runs[b])
                if sa >= sb:
                    slot(a)["supersedes"].append(b)
                    slot(b)["superseded_by"].append(a)
                elif sb >= sa:
                    slot(b)["supersedes"].append(a)
                    slot(a)["superseded_by"].append(b)
                else:
                    shared = 0
                    for x, y in zip(runs[a], runs[b]):
                        if x != y:
                            break
                        shared += 1
                    slot(a)["forked_from"].append({"run_id": b, "shared_prefix_len": shared})
                    slot(b)["forked_from"].append({"run_id": a, "shared_prefix_len": shared})
    return out
