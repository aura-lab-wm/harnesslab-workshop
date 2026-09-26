"""One-shot capture of every allow-listed session file into data/runs/captured/.

  - Closed runs hold exactly one row each in index.jsonl, refreshed in place if the run has grown
    since, and the index is rewritten atomically: readers of index.jsonl do not dedup
    (analysis.load_index, sentinel, fork), so a second row would double-count (spec §5.6).
  - Runs whose last activity is newer than the segment gap are open. They go to inflight.json,
    rewritten atomically, and are indexed by a later backfill once they close.
  - Resume and fork relations go to identity.json, also rewritten atomically, because a resume found
    later changes the relations of a run that is already indexed. Both sidecars are MERGED by run_id
    rather than replaced, so a --path pass over part of the corpus leaves the rest untouched. What a
    scoped pass cannot do is notice a relation between a run it saw and one it did not: relations are
    pairwise, and it only has the uuids of what it just read. Full lineage still needs a full pass.
  - An unreadable or vanished source file is reported in `errors` and skipped; it is never fatal to
    the rest of the backfill.

Known limitation (Plan 2, with the sniffer's cursors): a source rewritten so that it now has FEWER
segments leaves the extra segment directories behind, and their index rows with them -- an orphan
directory is merely dead weight, but a stale row is still counted by every metric and cost aggregate.
Content-defined segments of an append-only session file never shrink, so this needs a rewritten
source to trigger. The watcher's cursors record such dropped runs, and `--prune-orphans` reports them
(and a deleted source's runs), removing directory and row together only with `--apply` -- and a
deleted source's runs only with `--prune-deleted-sources` as well, since a source Claude Code has
cleaned up leaves the capture as the record of that work (orphans.py).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from harnesslab.core import providers as P

from . import adapters, identity
from .lock import CaptureLock
from .regen import regenerate


def _iso(epoch: float) -> str:
    """Epoch seconds as the ISO-8601 Z form identity.parse_ts reads back."""
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: str, default):
    """Whatever is on disk, or `default`. A sidecar that is missing or torn must not stop a pass."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _atomic_json(path: str, data) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _append_index(index_path: str, rows: list[dict]) -> None:
    """Write rows into index.jsonl without ever leaving it torn, one row per run.

    Existing valid rows are kept in order, except where a new row carries the same run_id: that row
    is replaced, because a closed run can still grow (see run()) and readers of index.jsonl do not
    dedup (analysis.load_index, sentinel, fork), so a second row would double-count it. A run named
    twice within `rows` collapses the same way, to its last row. A line left half-written by an
    interrupted earlier run is dropped (its run is re-added, since _indexed_run_ids never counted
    it). Written to a temp file, fsynced, then swapped in."""
    kept: list[str] = []
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                except ValueError:
                    continue
                kept.append(line)
    # One row per run, whichever side the older copy is on. `rows` itself can name a run twice: the
    # same session file under two project directories is one run_id (it hashes the basename, not the
    # directory), and the second regeneration overwrote the first on disk, so the last row wins.
    latest = {r.get("run_id"): r for r in rows}
    kept = [line for line in kept if json.loads(line).get("run_id") not in latest]
    kept += [json.dumps(r, ensure_ascii=False, default=str) for r in latest.values()]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(index_path), prefix=".tmp-", suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, index_path)


def _indexed_run_ids(index_path: str) -> set[str]:
    out: set[str] = set()
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    out.add(json.loads(line)["run_id"])
                except (ValueError, KeyError):
                    continue
    return out


def source_files(roots: list[str], skip=None) -> tuple[list[tuple[str, str]], list[dict]]:
    """Capturable session files under the allow-listed roots, each tagged with the adapter that
    claims it.

    Discovery, adapter choice and allow-list enforcement all live in capture.adapters now. This
    used to glob `**/*.jsonl` and gate on `claude_code.sniff`, which made a Codex rollout under
    an allow-listed root structurally invisible even though codex.py already existed.

    `skip(realpath, stat_result) -> bool` is passed straight through to discover(): the sniffer uses
    it to drop a source whose cursor still matches, so an unchanged file is never opened.

    A directory that cannot be entered is dropped from the errors here. Capture is about the files it
    CAN read -- a home directory with one locked folder must not report an error on every pass, and a
    watcher must not sit at exit 1 forever. The orphan prune asks discover() directly, because for a
    deletion an unreadable directory is exactly what has to stop the pass (orphans.find).
    """
    found, errors = adapters.discover(roots, skip=skip)
    return found, [e for e in errors if not (isinstance(e, dict) and e.get("kind") == "directory")]


def run(roots: list[str], runs_root: str, gap_s: float, now: Optional[float] = None,
        archive_root: Optional[str] = None, skip=None, stop=None, progress=None) -> dict:
    """Capture every source under `roots` once.

    `progress(done, of)` is called as each source is finished. `of` is the real count -- the walk
    has the whole list before the loop starts -- and it does not move during the pass. The watcher
    publishes it so the menu can say "Capturing -- 812 of 19,576 files"; before this the progress
    field existed, was rendered, and was only ever written as zeros, so a first full sweep looked
    identical to an idle one for twenty-five minutes.
    """
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    out_root = os.path.join(runs_root, "captured")
    report = {"lab_runs_root": os.path.realpath(runs_root), "files": 0, "runs": 0, "closed_new": 0,
              "closed_updated": 0, "open": 0,
              "superseded": 0, "forks": 0, "archived": 0, "errors": [],
              "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0, "cost_usd": 0.0, "lines_added": 0, "lines_removed": 0,
              "unknown_record_types": {}, "models": {}, "unpriced_models": [], "by_source": {},
              # runs whose source records no usage: their spend is UNKNOWN and is in none of the
              # totals above, which would otherwise read as if those runs had cost nothing
              "unmeasured_runs": 0, "sources": {}}

    with CaptureLock(runs_root):
        os.makedirs(out_root, exist_ok=True)
        index_path = os.path.join(out_root, "index.jsonl")
        indexed = _indexed_run_ids(index_path)
        all_runs: dict[str, list[str]] = {}
        inflight: list[dict] = []
        new_rows: list[dict] = []

        files, skipped = source_files(roots, skip=skip)
        report["errors"].extend(skipped)
        for path, adapter in files:
            if stop is not None and stop():
                # Between files, never mid-file: what was captured is written out below exactly as
                # if the pass had ended here, and the sources not reached keep stale cursors, so the
                # next pass reads them. A first pass is minutes; a stop request cannot wait it out.
                report["interrupted"] = True
                break
            report["files"] += 1
            report["by_source"][adapter] = report["by_source"].get(adapter, 0) + 1
            if progress is not None:
                progress(report["files"], len(files))
            if archive_root:
                try:                                    # a failed archive must not cost us the capture
                    from .archive import archive_source
                    report["archived"] += int(archive_source(path, archive_root))
                except Exception as e:
                    report["errors"].append({"path": path, "error": f"{type(e).__name__}: {e}"})
            # A source with no clock of its own (Cursor: zero timestamp keys in the whole format)
            # cannot say when its session ended, and openness is a time question. The file's mtime is
            # when the session last wrote, so it answers it -- for openness only. It never reaches a
            # span: writer.py is clock-free by contract, and every Cursor span carries ts "".
            clockless = "timestamps" in adapters.missing(adapters.BY_NAME[adapter])
            mtime_ts = ""
            if clockless:
                try:
                    mtime_ts = _iso(os.stat(path).st_mtime)
                except OSError:
                    mtime_ts = ""
            try:
                produced = regenerate(path, out_root, gap_s, adapter=adapter)
                # Per-source detail, for whoever has to remember what this source produced. The
                # aggregates cannot answer "which runs are this file's", and a cursor that does not
                # know that orphans them the moment the file disappears.
                report["sources"][path] = {
                    "runs": [r.run_id for r in produced],
                    "last_ts": max((r.last_ts or "" for r in produced), default=""),
                    # what a clockless source's openness was decided from, so the watcher can
                    # schedule the same recheck without reading the file again
                    "last_activity": mtime_ts,
                    "adapter": adapter,
                    # Every conversational uuid this source carries. A resume or fork contains its
                    # parent's root uuid, which is how a partial pass finds the parent it must
                    # re-read in order to link the two (see sniffer.tick).
                    "uuids": sorted({u for r in produced for u in (r.uuids or [])})}
            except Exception as e:                          # one bad file never stops the others
                report["errors"].append({"path": path, "error": f"{type(e).__name__}: {e}"})
                continue
            for r in produced:
                s = r.summary
                report["runs"] += 1
                all_runs[r.run_id] = r.uuids
                # `or 0` is not enough on its own: a null is a fact the source never recorded, so
                # the run is counted as unmeasured rather than summed in as a zero.
                # "runs whose source records no usage" -- asked the way sniffer._corpus_totals and
                # the capture API ask it. `extras["unmeasured"]` lists FIELDS, so any gap at all made
                # a source with a clock problem and a perfectly good meter count as unmetered.
                if s.cost_usd is None:
                    report["unmeasured_runs"] += 1
                report["input_tokens"] += s.input_tokens or 0
                report["output_tokens"] += s.output_tokens or 0
                report["cache_read_input_tokens"] += r.extras["cache_read_input_tokens"] or 0
                report["cache_creation_input_tokens"] += r.extras["cache_creation_input_tokens"] or 0
                report["cost_usd"] += s.cost_usd or 0.0
                report["lines_added"] += s.lines_added or 0
                report["lines_removed"] += s.lines_removed or 0
                report["models"][s.model] = report["models"].get(s.model, 0) + 1
                for kind, count in r.extras["unknown_record_types"].items():
                    report["unknown_record_types"][kind] = report["unknown_record_types"].get(kind, 0) + count
                last = identity.parse_ts(r.last_ts) or identity.parse_ts(mtime_ts)
                # No timestamp and no file clock means we cannot tell the session has ended, so an
                # unparseable or missing last activity waits in inflight.json rather than being
                # called closed.
                if last is None or now - last < gap_s:
                    row = {"run_id": r.run_id, "task_id": s.task_id, "last_ts": r.last_ts, "steps": s.steps}
                    if not r.last_ts and mtime_ts:
                        # the run says nothing about its own time; say what decided it was open
                        row.update(last_activity=mtime_ts, clock="source_mtime")
                    inflight.append(row)
                    report["open"] += 1
                else:
                    # Every closed run contributes its CURRENT row, not just a first-seen one: a run
                    # closed by a long-running tool call can still grow afterwards (segments split
                    # only at a user message), and a row written from the shorter version would
                    # under-report it forever. _append_index replaces, so this never duplicates.
                    new_rows.append(dict(asdict(s), root_uuid=r.uuids[0] if r.uuids else ""))
                    if r.run_id in indexed:
                        report["closed_updated"] += 1
                    else:
                        indexed.add(r.run_id)
                        report["closed_new"] += 1

        if new_rows:
            _append_index(index_path, new_rows)
        rel = identity.relations(all_runs)
        report["superseded"] = sum(1 for v in rel.values() if v["superseded_by"])
        report["forks"] = sum(1 for v in rel.values() if v["forked_from"])

        # A pass may legitimately cover part of the corpus: --path is a documented, repeatable flag.
        # index.jsonl is merged across passes, so these two sidecars must be as well -- written
        # wholesale they silently discarded the open runs and the resume lineage of every source the
        # pass did not rescan, which is precisely what keeping identity.json is for. A run this pass
        # produced is authoritative; a run it never saw is left exactly as it was found.
        seen = set(all_runs)
        inflight_path = os.path.join(out_root, "inflight.json")
        prior_open = (_read_json(inflight_path, {}) or {}).get("runs") or []
        kept_open = [r for r in prior_open if r.get("run_id") not in seen]
        _atomic_json(inflight_path, {"runs": kept_open + inflight})

        identity_path = os.path.join(out_root, "identity.json")
        prior_rel = (_read_json(identity_path, {}) or {}).get("relations") or {}
        merged = dict(prior_rel)
        for run_id, found in rel.items():
            # An EMPTY entry means this pass saw no partner for the run, which for a scoped pass
            # means it could not look -- the partner's source was never read. That is absence of
            # evidence, not evidence of absence, so it must not erase a lineage already established.
            # A pass that actually observed a relation is believed and overwrites.
            if any(found.get(k) for k in ("supersedes", "superseded_by", "forked_from")):
                merged[run_id] = found
            else:
                merged.setdefault(run_id, found)
        _atomic_json(identity_path, {"relations": merged})

    report["cost_usd"] = round(report["cost_usd"], 6)
    report["unpriced_models"] = sorted(m for m in report["models"] if not P.is_priced(m))
    return report
