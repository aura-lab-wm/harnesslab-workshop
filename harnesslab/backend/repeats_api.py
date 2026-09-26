"""repeats — the backend for the "repeated commands over a run" chart (harnesslab.core.repeats).

Endpoints (prefix /api/repeats):
  GET /{dir}?harness=&limit=   study(): every run's cumulative-repeats series (including whether
                                its OWN ledger was cut short by its budget cap -- `truncated` on
                                the RUN, see repeats.py::study), the per-outcome median/p25/p75
                                bands, and the summary the CLI's report, the chart's finding
                                sentence, and its aria-label all read. `limit` (default 500,
                                matching the largest shipped dataset) caps how many runs' full
                                per-call arrays come back; the response also reports the true
                                `total` and its own top-level `truncated` (runs cut by `limit`,
                                not to be confused with a single run's own ledger-budget flag).
  GET /{dir}/run/{run_id}      per-call detail for one run: {i, tool, command_preview, repeat_of}
                                (fetched only for the opened/highlighted run, not every run).

`dir` and `run_id` are validated against the runs root the same way `fork.py` validates them
(reject a path separator, a leading dot, or a value that resolves outside the root) — a results
directory name is never allowed to walk the filesystem, and `run_id` is checked the same way even
though FastAPI's default path converter already refuses a literal "/" in one segment: a single
".." segment does not contain a slash and would otherwise climb one directory.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException

from harnesslab.core import repeats as R

from .paths import RUNS_ROOT

router = APIRouter(prefix="/api/repeats")

# real_swe_agent_500, this package's largest single-directory dataset, has exactly this many runs
# -- the default that never truncates IT. A live or captured corpus can run into the thousands
# (repeats.study()'s own docstring cites 20k), where an uncapped response put tens of MB of
# per-run arrays on the wire; those directories DO truncate at the default and the chart says so.
DEFAULT_LIMIT = 500


def _safe_name(name: str, what: str) -> None:
    # A null byte (or any other control character) must be rejected here, before it ever reaches an
    # os.path call: os.path.realpath() raises an uncaught ValueError on an embedded "\x00", which
    # surfaces as an unhandled 500 instead of the clean 400 every other malformed name gets.
    if (not name or "/" in name or "\\" in name or name in (".", "..") or name.startswith(".")
            or any(ord(c) < 0x20 or c == "\x7f" for c in name)):
        raise HTTPException(400, f"bad {what}")


def _dir(name: str) -> str:
    _safe_name(name, "results dir name")
    d = os.path.join(RUNS_ROOT, name)
    # Defense in depth: even a name that passes the segment check above (no slash, no leading dot)
    # must still resolve to somewhere actually inside RUNS_ROOT once symlinks are followed.
    real_root = os.path.realpath(RUNS_ROOT)
    real_d = os.path.realpath(d)
    if os.path.commonpath([real_root, real_d]) != real_root:
        raise HTTPException(400, "bad results dir name")
    if not os.path.exists(os.path.join(d, "index.jsonl")):
        raise HTTPException(404, f"no results dir {name}")
    return d


@router.get("/{name}")
def repeats_study(name: str, harness: Optional[str] = None, limit: int = DEFAULT_LIMIT):
    d = _dir(name)
    return R.study(d, harness=harness, limit=limit)


@router.get("/{name}/run/{run_id}")
def repeats_run(name: str, run_id: str):
    d = _dir(name)
    _safe_name(run_id, "run_id")
    rd = os.path.join(d, run_id)
    real_d = os.path.realpath(d)
    if os.path.commonpath([real_d, os.path.realpath(rd)]) != real_d or not os.path.isdir(rd):
        raise HTTPException(404, f"no run {run_id}")
    return R.run_detail(d, run_id)
