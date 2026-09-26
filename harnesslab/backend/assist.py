"""The lab assistant: a real reading of the numbers currently on screen.

  GET  /api/assist/context   a DETERMINISTIC digest + findings, computed from the ledger.
                             No model, no key, no network. This is what makes the helper
                             worth opening on a plane.

The model half (POST /api/assist/ask) went with the retired console. The Rig's Buddy asks
OpenRouter from the browser, with the reader's own key.

The findings are computed here rather than asked for, because a claim about an interval
covering zero is arithmetic and must not be something a model can get wrong.
"""
from __future__ import annotations
import os, time
from typing import Optional

from fastapi import APIRouter, HTTPException

from . import metrics as M

router = APIRouter(prefix="/api/assist", tags=["assist"])


def _f(x, d=3):
    return None if x is None else round(float(x), d)


def _p(x, d=1, sign=False):
    """A percentage rendered exactly as JS `toFixed` renders it.

    Python's format() rounds half to even on the binary value (0.6625 -> "66.2"); the browser
    rounds the decimal half up ("66.3"). The same figure appearing twice on one screen with two
    different last digits reads as a bug, so the server speaks the client's dialect."""
    from decimal import Decimal, ROUND_HALF_UP
    v = Decimal(repr(float(x) * 100)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    return f"{'+' if sign and v >= 0 else ''}{v:.{d}f}"


def digest(results: str, harness: str = "baseline", oracle: str = "hidden_pass") -> dict:
    """Everything the assistant is allowed to know, straight off the index."""
    # A bare directory name, the way field.py:_results_dir already insists. Joining a
    # caller-supplied string onto RUNS_ROOT let `../../x` read any index.jsonl on the filesystem.
    if not results or os.path.basename(results) != results:
        raise HTTPException(400, "results must be a bare directory name")
    d = os.path.join(M.RUNS_ROOT, results)
    if not os.path.exists(os.path.join(d, "index.jsonl")):
        raise HTTPException(404, f"no results dir {results}")
    rows = M.rows_for(results)
    cells = M.cells(rows)
    comparison = M.harness_comparison(rows, harness, None)
    here = next((c for c in cells if c["harness"] == harness), cells[0] if cells else None)

    out = {
        "results_dir": results,
        "baseline_harness": harness,
        "oracle": oracle,
        "n_runs": len(rows),
        "models": sorted({r["model"] for r in rows}),
        "harnesses": sorted({r["harness_id"] for r in rows}),
        "tasks": sorted({r["task_id"] for r in rows}),
        "cells": [
            {
                "model": c["model"], "harness": c["harness"], "runs": c["runs"], "repeats": c["repeats"],
                "pass1": _f(c.get("pass@1")), "ci95": [_f(v) for v in (c.get("ci95") or [None, None])],
                "pass1_strong": _f(c.get("pass1_strong")), "flip_rate": _f(c.get("flip_rate")),
                "boundary_any": _f(c.get("boundary_any")), "tests_modified": _f(c.get("tests_modified")),
                "verified_before_submit": _f(c.get("ran_tests_before_submit")),
                "mean_steps": _f(c.get("mean_steps"), 2), "mean_cost_usd": _f(c.get("mean_cost"), 5),
                "cost_of_pass_usd": _f(c.get("cost_of_pass"), 5), "agency_tax": _f(c.get("agency_tax"), 2),
                "exit": {k: _f(v) for k, v in (c.get("exit") or {}).items()},
            }
            for c in cells
        ],
        "paired_vs_baseline": {
            h: {"n_tasks": v.get("n_tasks"), "mean_diff": _f(v.get("mean_diff")),
                "ci95": [_f(x) for x in (v.get("ci95") or [None, None])]}
            for h, v in comparison.items()
        },
    }
    out["findings"] = findings(out, here)
    return out


def findings(dg: dict, here: Optional[dict]) -> list[dict]:
    """Deterministic observations. Each is arithmetic over the digest, never a judgement call."""
    F: list[dict] = []
    add = lambda level, text, where: F.append({"level": level, "text": text, "where": where})

    # 1. the headline, always with its interval
    if here:
        lo, hi = (here.get("ci95") or [None, None])
        if here.get("pass@1") is not None and lo is not None:
            width = (hi - lo) * 100
            add("info",
                f"{dg['baseline_harness']} sits at {_p(here['pass@1'])}% pass@1, 95% CI "
                f"[{_p(lo)}, {_p(hi)}] — {width:.0f} points wide on {here['runs']} runs.",
                "outcome")
            if width > 25:
                add("warn",
                    f"That interval is {width:.0f} points wide. Any ranking read off the point "
                    f"estimate alone is not supported at this sample size.",
                    "outcome")

    # 2. paired comparisons: which ones actually separate
    sep, cov = [], []
    for h, v in dg["paired_vs_baseline"].items():
        lo, hi = (v.get("ci95") or [None, None])
        if lo is None or v.get("mean_diff") is None:
            continue
        (cov if lo <= 0 <= hi else sep).append((h, v["mean_diff"], lo, hi))
    for h, m, lo, hi in sorted(sep, key=lambda x: x[1]):
        add("serious" if m < 0 else "good",
            f"{h} moves pass@1 by {_p(m, sign=True)} points vs {dg['baseline_harness']}, 95% CI "
            f"[{_p(lo, sign=True)}, {_p(hi, sign=True)}] — the interval clears zero.",
            "harness")
    if cov:
        add("info",
            f"{len(cov)} harness(es) do not separate from the baseline at this n: "
            + ", ".join(h for h, *_ in cov) + ".",
            "harness")

    # 3. conduct: the things that should stop you regardless of the score
    for c in dg["cells"]:
        if (c.get("tests_modified") or 0) > 0:
            add("critical",
                f"{c['model']} / {c['harness']}: {_p(c['tests_modified'], 0)}% of runs modified the "
                f"tests. Those passes are not evidence of a fix.", "integrity")
        if (c.get("boundary_any") or 0) >= 0.10:
            add("serious",
                f"{c['model']} / {c['harness']}: {_p(c['boundary_any'], 0)}% of runs touched a policy "
                f"boundary.", "integrity")
        if c.get("verified_before_submit") is not None and c["verified_before_submit"] < 0.7:
            add("warn",
                f"{c['model']} / {c['harness']}: only {_p(c['verified_before_submit'], 0)}% of runs ran "
                f"the tests after their last edit before submitting.", "explorer")

    # 4. instability: the product's actual thesis
    if here and (here.get("flip_rate") or 0) >= 0.4:
        add("serious",
            f"{_p(here['flip_rate'], 0)}% of tasks in {dg['baseline_harness']} are not decided — they "
            f"both pass and fail across repeats. One run per task would have reported a coin flip as a result.",
            "outcome")

    # 5. the oracle gap
    if here and here.get("pass@1") is not None and here.get("pass1_strong") is not None:
        gap = here["pass@1"] - here["pass1_strong"]
        if abs(gap) >= 0.02:
            add("warn",
                f"Strengthening the suite moves {dg['baseline_harness']} by {_p(-gap, sign=True)} points "
                f"({_p(here['pass@1'])}% → {_p(here['pass1_strong'])}%): some passes are plausible "
                f"wrong fixes the held-out suite did not catch.", "integrity")
    return F


@router.get("/context")
def assist_context(results: str, harness: str = "baseline", oracle: str = "hidden_pass"):
    t0 = time.time()
    dg = digest(results, harness, oracle)
    return {"digest": dg, "findings": dg["findings"], "ms": int((time.time() - t0) * 1000)}
