"""Repeated commands over a run — the one definition, shared everywhere it is used.

A call is a repeat when an identical KEY occurred at an earlier index in the same run. The key is
(tool name, arguments) rendered as ``json.dumps({"n": name, "a": args}, sort_keys=True)`` — the
same rendering ``trajtest.py`` built inline before this module existed. ``key()`` is now the one
place that does it: ``trajtest.Trajectory.repeated_tool_calls`` and ``real_traj.py``'s ledger
feature extractor both call it instead of repeating the literal.

The cumulative count at call k is the number of repeats among calls 0..k; the final value equals
``sum(count - 1 for count in ... if count > 1)`` over the whole run — exactly what
``trajtest.Trajectory.repeated_tool_calls`` returns, and exactly what a `Counter` over the same
keys would give, since ``series()`` below is just that count taken incrementally.

This module is deliberately import-light: `study()` is the only function that needs
`trajtest.load_runs`, and it imports it locally, so a caller that only wants `key`/`series`/
`crossing`/`loop_onset` (a synthetic-ledger unit test, the sentinel feature, a CLI --run) never
pays for or risks a circular import with trajtest.py (which calls back into `key`).

CLI:
    python -m harnesslab.core.repeats --results DIR [--harness H] [--run RUN_ID] [--csv PATH] [--json]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from typing import Optional

# The Ochiai binary feature `"repeated_commands>N"` in real_traj.binary_features imports this
# instead of hard-coding 3, so the threshold is a literal exactly once in the whole codebase.
THRESHOLD = 3

DEFINITION = ("a call is a repeat when an identical (tool, arguments) key occurred at an earlier "
              "index in the same run; the cumulative count at call k is the number of repeats "
              "among calls 0..k")


def key(call: dict) -> str:
    """The identity a repeat is judged against: tool name + arguments, order-insensitive.

    `call` is one `execute_tool` span (or anything with the same two fields): `gen_ai.tool.name`
    and `args`. `.get` rather than `[...]` so a malformed span degrades to a shared "unknown" key
    instead of raising mid-run — the same tolerance `load_runs` already extends to a summary row
    that predates a field.
    """
    return json.dumps({"n": call.get("gen_ai.tool.name"), "a": call.get("args")}, sort_keys=True)


def series(tool_calls: list[dict]) -> dict:
    """The repeat series for one run's tool calls.

    ``cumulative[i]`` is the number of repeats among calls ``0..i`` (inclusive); ``repeat_of[i]``
    is the index of the FIRST occurrence of call i's key when call i is itself a repeat, or -1
    when call i is that key's first occurrence. Summing ``repeat_of[i] != -1`` over the whole run
    equals ``cumulative[-1]``, which equals `Trajectory.repeated_tool_calls()` on the same calls.
    """
    seen: dict[str, int] = {}
    cumulative: list[int] = []
    repeat_of: list[int] = []
    running = 0
    for i, call in enumerate(tool_calls):
        k = key(call)
        first = seen.get(k)
        if first is None:
            seen[k] = i
            repeat_of.append(-1)
        else:
            running += 1
            repeat_of.append(first)
        cumulative.append(running)
    return {"cumulative": cumulative, "repeat_of": repeat_of, "n": len(tool_calls)}


def crossing(cumulative: list[int], threshold: int = THRESHOLD) -> Optional[int]:
    """First index where the cumulative count exceeds `threshold`, or None if it never does."""
    for i, c in enumerate(cumulative):
        if c > threshold:
            return i
    return None


def loop_onset(repeat_of: list[int], min_len: int = 5) -> Optional[tuple[int, int]]:
    """(start, end), inclusive, of the LONGEST streak of consecutive repeats >= min_len, or None.

    A streak is a run of consecutive indices whose `repeat_of` is not -1 (each one repeats some
    earlier call). Ties keep the first (leftmost) longest streak, matching how `min` on an
    (index, -length) key would resolve them — the annotation should point at where the loop
    actually starts, not at whichever tie the dict-ordering happened to visit last.
    """
    best: Optional[tuple[int, int]] = None
    start: Optional[int] = None
    for i, r in enumerate(list(repeat_of) + [-1]):        # sentinel flushes a streak touching the end
        if r != -1:
            if start is None:
                start = i
            continue
        if start is not None:
            end = i - 1
            if (end - start + 1) >= min_len and (best is None or (end - start) > (best[1] - best[0])):
                best = (start, end)
            start = None
    return best


def _percentile(xs: list[float], p: float) -> float:
    """Linear-interpolation percentile (numpy's default 'linear' method) over `xs`, `p` in [0,100].

    Pure Python on purpose: a chart's numbers should not depend on numpy being installed, and the
    CLI and the API must produce identical figures from identical inputs without it.
    """
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    rank = (p / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _outcome(summary: dict) -> str:
    """resolved | unresolved | unknown, from the one field both lab runs and imported real runs
    write: `hidden_pass` (for an imported SWE-agent run this IS the dataset's `resolved` label —
    real_import.py sets `summary.hidden_pass = bool(row["target"])` — so one field, two sources,
    same meaning: did the hidden oracle accept this run)."""
    hp = summary.get("hidden_pass")
    if hp is True:
        return "resolved"
    if hp is False:
        return "unresolved"
    return "unknown"


def _truncated(spans: list[dict], n_calls: int) -> bool:
    """True when this run's ledger reached the harness's OWN configured call budget, i.e. the
    ledger's tool-call history may be a hard-truncated PREFIX of a longer real trajectory, not the
    whole thing (F4). Read from the run's own `invoke_agent` start span (`harness.max_steps`,
    written by both harness.py for a live run and real_import.py for an imported one) rather than
    from `exit_reason`: `exit_reason` records why the run's SOURCE ended (which, for an imported
    dataset, reflects the ORIGINAL SWE-agent run's own budget, independent of whether OUR import
    actually cut anything off), so a run that legitimately finished in 40 calls with a source
    exit_reason of "budget_exceeded" is NOT truncated by us and must not be flagged as if it were.
    Only reaching the configured cap (n_calls >= max_steps) means OUR ledger is a truncated prefix.

    This is a materially different mechanism from the importer's per-command string truncation
    (which shortens one long command's TEXT, not the run's LENGTH): for real_swe_agent_500, every
    run capped at real_import.MAX_STEPS=75 has this flag set, and every one of them mismatches
    real_features.jsonl (which reflects the uncapped original trajectory) -- the chart and the CLI
    must not present a truncated run's crossing/loop/final-count as if it were the run's true,
    complete behaviour."""
    for sp in spans:
        if sp.get("span") == "invoke_agent" and sp.get("status") == "start":
            max_steps = (sp.get("harness") or {}).get("max_steps")
            return max_steps is not None and n_calls >= max_steps
    return False


def _preview(call: dict, n: int = 120) -> str:
    """A short human-readable stand-in for a tool call: its command, or its most identifying arg."""
    args = call.get("args")
    if isinstance(args, dict):
        for k in ("command", "path", "summary", "pattern"):
            v = args.get(k)
            if v:
                s = str(v)
                return s if len(s) <= n else s[: n - 1] + "…"
        if args:
            s = json.dumps(args, ensure_ascii=False, default=str)
            return s if len(s) <= n else s[: n - 1] + "…"
    return ""


def study(results_dir: str, harness: Optional[str] = None, limit: Optional[int] = None) -> dict:
    """Every run's repeat series in `results_dir`, plus per-outcome bands and a summary.

    `bands[outcome]` is the median/p25/p75 of the cumulative count at each call index, computed
    ONLY over the runs still running at that index (survivorship made explicit): a run with 12
    calls contributes to `bands[...][i]` for i < 12 and not beyond, and `n_alive` says how many
    runs that was so a chart (or a reader) can see the band thin out rather than silently drop.

    `limit`, when given, caps how many runs get a full `cumulative`/`repeat_of` array built and
    serialized -- unbounded, a 20k-run corpus puts tens of MB of those arrays in one response, and
    the chart used to spread one `Math.max` argument per run over the whole set. `total` is the
    true match count (before the cap) and `truncated` says whether `limit` actually cut anything,
    so a caller can say "showing N of M runs" instead of silently rendering a partial corpus as if
    it were the whole one. The bands and the summary are computed over the SAME capped set `runs`
    carries -- not the full corpus -- so nothing on screen mixes a distribution over runs it never
    draws a line for.
    """
    from .trajtest import load_runs                          # local: avoid a load-time cycle with trajtest -> repeats.key
    filters = {"harness_id": harness} if harness else {}
    trajs = load_runs(results_dir, **filters)
    total = len(trajs)
    if limit is not None and limit >= 0:
        trajs = trajs[:limit]

    runs = []
    for t in trajs:
        s = series(t.tool_calls)
        c = crossing(s["cumulative"], THRESHOLD)
        lp = loop_onset(s["repeat_of"], min_len=5)
        runs.append({
            "run_id": t.run_id, "task_id": t.task_id, "harness": t.harness_id,
            "outcome": _outcome(t.summary), "n_calls": s["n"],
            "cumulative": s["cumulative"], "repeat_of": s["repeat_of"],
            "crossing": c, "loop": {"start": lp[0], "end": lp[1]} if lp else None,
            "truncated": _truncated(t.spans, s["n"]),
        })

    max_n = max((r["n_calls"] for r in runs), default=0)
    bands: dict[str, list[dict]] = {}
    for outcome in ("resolved", "unresolved"):
        rows = [r for r in runs if r["outcome"] == outcome]
        band = []
        for i in range(max_n):
            alive = [r["cumulative"][i] for r in rows if r["n_calls"] > i]
            if not alive:
                continue
            band.append({"i": i, "p25": _percentile(alive, 25), "median": statistics.median(alive),
                        "p75": _percentile(alive, 75), "n_alive": len(alive)})
        bands[outcome] = band

    summary: dict[str, dict] = {}
    for outcome in ("resolved", "unresolved", "unknown"):
        rows = [r for r in runs if r["outcome"] == outcome]
        if not rows:
            summary[outcome] = {"n": 0, "median_final": None, "share_crossing": None}
            continue
        finals = [r["cumulative"][-1] if r["cumulative"] else 0 for r in rows]
        n_crossing = sum(1 for r in rows if r["crossing"] is not None)
        summary[outcome] = {"n": len(rows), "median_final": statistics.median(finals),
                            "share_crossing": n_crossing / len(rows)}

    return {"definition": DEFINITION, "threshold": THRESHOLD, "runs": runs, "bands": bands,
            "summary": summary, "total": total, "truncated": len(runs) < total}


def finding_sentence(data: dict) -> str:
    """The one-sentence finding the chart draws under itself and puts in its aria-label — the CLI
    and the chart read the same `summary`, so the numbers in both are always the same numbers."""
    parts = []
    for outcome in ("unresolved", "resolved"):
        s = data["summary"].get(outcome) or {}
        if not s.get("n"):
            continue
        parts.append(f"{outcome}: median {_fmt_num(s['median_final'])} repeats, "
                     f"{_fmt_pct(s['share_crossing'])} cross the threshold")
    if not parts:
        return "No runs to summarise."
    return "; ".join(parts) + "."


def _fmt_num(x) -> str:
    return "—" if x is None else (f"{x:.0f}" if float(x).is_integer() else f"{x:.1f}")


def _fmt_pct(x) -> str:
    # Round-half-UP, matching the JS port's Math.round (repeats.js::fmtPct) -- Python's builtin
    # round() is round-half-to-EVEN (banker's rounding), which silently disagrees with JS on any
    # share_crossing*100 that lands exactly on an x.5 boundary (round(12.5) == 12 in Python,
    # Math.round(12.5) === 13 in JS). The codebase's own invariant is that the CLI and the chart
    # are always the same numbers, so this side picks JS's rule explicitly rather than the
    # language default.
    return "—" if x is None else f"{math.floor(x * 100 + 0.5):.0f}%"


def run_detail(results_dir: str, run_id: str) -> list[dict]:
    """Per-call detail for one run: {i, tool, command_preview, repeat_of}.

    Reads only that run's ledger (not the whole directory) — the API route behind this is called
    once per opened run, and `study()` already paid for every run's series when the page loaded.
    """
    p = os.path.join(results_dir, run_id, "ledger.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        spans = [json.loads(line) for line in f if line.strip()]
    tool_calls = [s for s in spans if s.get("span") == "execute_tool"]
    s = series(tool_calls)
    return [{"i": i, "tool": call.get("gen_ai.tool.name"), "command_preview": _preview(call),
             "repeat_of": s["repeat_of"][i]} for i, call in enumerate(tool_calls)]


# --------------------------------------------------------------------------- CLI
def _print_summary(data: dict) -> None:
    print(f"definition: {data['definition']}")
    print(f"threshold: repeated_commands > {data['threshold']}")
    for outcome in ("resolved", "unresolved", "unknown"):
        s = data["summary"].get(outcome, {"n": 0})
        if not s["n"]:
            continue
        print(f"  {outcome:<10} n={s['n']:<4} median_final={_fmt_num(s['median_final']):>5}  "
              f"share_crossing={_fmt_pct(s['share_crossing']):>4}")
    print(finding_sentence(data))


def _print_run(row: dict, threshold: int) -> None:
    print(f"run {row['run_id']}  ({row['outcome']}, {row['n_calls']} calls)")
    if row["crossing"] is not None:
        print(f"  crossing: call {row['crossing']}  (repeated_commands > {threshold})")
    else:
        print(f"  crossing: never crosses (repeated_commands > {threshold})")
    if row["loop"]:
        print(f"  loop onset: calls {row['loop']['start']}–{row['loop']['end']}")
    else:
        print("  loop onset: none (no streak of ≥ 5 consecutive repeats)")
    if row.get("truncated"):
        print(f"  NOTE: this run's ledger hit its own harness's call budget -- "
              f"crossing/loop/final-count above are a truncated PREFIX of the run, not its full extent")


def _write_csv(data: dict, path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "outcome", "i", "cumulative", "is_repeat"])
        for r in data["runs"]:
            for i, c in enumerate(r["cumulative"]):
                w.writerow([r["run_id"], r["outcome"], i, c, int(r["repeat_of"][i] != -1)])


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harnesslab.core.repeats",
                                 description="Repeated commands over a run: per-outcome summary, "
                                             "one run's crossing/loop onset, or a long-form CSV.")
    ap.add_argument("--results", required=True, help="results directory (e.g. data/runs/real_swe_agent_500)")
    ap.add_argument("--harness", default=None)
    ap.add_argument("--run", default=None, help="run_id to report crossing index + loop onset for")
    ap.add_argument("--csv", default=None, help="write long-form rows (run_id, outcome, i, cumulative, is_repeat)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the human-readable report")
    args = ap.parse_args(argv)

    data = study(args.results, harness=args.harness)

    row = None
    if args.run:
        row = next((r for r in data["runs"] if r["run_id"] == args.run), None)
        if row is None:
            print(f"no run {args.run!r} in {args.results}", file=sys.stderr)
            return 1

    if args.csv:
        _write_csv(data, args.csv)

    if args.json:
        out = {"definition": data["definition"], "threshold": data["threshold"], "summary": data["summary"]}
        if row is not None:
            out["run"] = {"run_id": row["run_id"], "outcome": row["outcome"], "n_calls": row["n_calls"],
                          "crossing": row["crossing"], "loop": row["loop"], "truncated": row["truncated"]}
        print(json.dumps(out, indent=2))
    else:
        _print_summary(data)
        if row is not None:
            _print_run(row, data["threshold"])

    if args.csv:
        print(f"wrote {sum(len(r['cumulative']) for r in data['runs'])} rows to {args.csv}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
