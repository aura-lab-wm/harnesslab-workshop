"""The evaluation report card: one implementation, used by exercise 6 and by the platform.

The card is the lecture's closing recipe as an artefact — the cell, the outcome as a
distribution, conduct, cost, the integrity checks, and an explicit list of what it does not
tell you. It lived in three places once (the exercise script, the API, and the old console's
JavaScript), which meant three chances to disagree about what pass@1 meant. Now the numbers
come from `analysis.py` and the prose comes from here.

    from harnesslab.core.reportcard import build_card, card_markdown
    card = build_card(rows, traj, source="data/runs/mine")
    print(card_markdown(card))
"""
from __future__ import annotations
import json, math, time

from .analysis import (task_table, aggregate, bootstrap_ci, flip_rate, boundary_rate,
                       tokens_per_solve, cost_of_pass, mean, exit_reasons, flag_of)

MISSING = [
    "Nothing about the *security* of the produced code beyond test behaviour (no SAST, no CWE scan).",
    "The judge (exercise 4) is not part of the score here; if you add one, attach its kappa and swap-consistency.",
    "Eight toy tasks: the CI is wide on purpose. Do not read the point estimate without it.",
]


def build_card(rows: list[dict], traj: list, source: str = "") -> dict:
    """Structured card from index rows plus their Trajectories. Both must be the same cell."""
    if not rows:
        return {"error": "no runs"}
    h = traj[0].spans[0]["harness"] if traj else {}
    tt = task_table(rows)
    agg = aggregate(rows)
    strong = aggregate(rows, "strong_pass")
    ci = bootstrap_ci([v["pass1"] for v in tt.values()])
    br = boundary_rate(rows)
    has_sentinel = any(r.get("sentinel_interventions") for r in rows) or bool(h.get("sentinel", {}).get("enabled"))
    # Both conduct rates below are predicates over execute_tool spans. A source that records tool
    # REQUESTS but never their execution (importers.common.UNMEASURED_FIELDS; Cursor) has none, so
    # ran_tests_after_last_edit() comes back False and read_before_write() comes back vacuously
    # True -- two confident claims about a run in which no read, write or test was ever observed.
    # The same guard real_traj.ledger_rows_as_features already applies: leave those runs out.
    observed = [t for t in traj if not ("tool_calls" in t.summary and t.summary["tool_calls"] is None)]
    return {
        "source": source,
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "cell": {"model": rows[0]["model"], "provider": rows[0]["provider"], "harness": h,
                 "tasks": sorted(tt), "repeats": max(r["repeat_index"] for r in rows) + 1,
                 "runs": len(rows), "has_sentinel": has_sentinel,
                 "oracle": "hidden unittest suite run on a pristine copy of the workspace"},
        "outcome": {"pass1": agg.get("pass@1"), "ci95": list(ci),
                    "pass_at_k": {k: agg.get(f"pass@{k}") for k in (2, 3, 5) if f"pass@{k}" in agg},
                    "pass_pow_k": {k: agg.get(f"pass^{k}") for k in (2, 3, 5) if f"pass^{k}" in agg},
                    "flip_rate": flip_rate(rows), "pass1_strong": strong.get("pass@1"),
                    "exit": exit_reasons(rows),
                    "per_task": [{"task": t, **v} for t, v in tt.items()]},
        "conduct": {"boundary_any": br["any"], "boundary_by_kind": br["by_kind"],
                    "tests_modified": mean(flag_of(r, "tests_modified") for r in rows),
                    "verified_after_last_edit": mean(1.0 if t.ran_tests_after_last_edit() else 0.0 for t in observed) if observed else None,
                    "read_before_write": mean(1.0 if t.read_before_write() else 0.0 for t in observed) if observed else None,
                    "tool_calls": mean(r["tool_calls"] for r in rows), "edits": mean(r["edits"] for r in rows),
                    "sentinel_interventions": mean(r.get("sentinel_interventions", 0) for r in rows)},
        "cost": {"in_tokens": mean(r["input_tokens"] for r in rows), "out_tokens": mean(r["output_tokens"] for r in rows),
                 "tokens_per_solve": tokens_per_solve(rows), "cost_per_run": mean(r["cost_usd"] for r in rows),
                 "cost_of_pass": cost_of_pass(rows), "wall_s": mean(r["wall_ms"] for r in rows) / 1000},
        "integrity": {"leak_probe_task": next((t for t in tt if t.startswith("t05")), None),
                      "leak_probe_pass1": next((v["pass1"] for t, v in tt.items() if t.startswith("t05")), None),
                      "oracle_delta": (strong.get("pass@1") or 0) - (agg.get("pass@1") or 0)},
        "missing": list(MISSING),
    }


def _f(x, spec=".3f"):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x:{spec}}"


def card_markdown(c: dict, source: str = "") -> str:
    """The same card as markdown. This is the lab hand-in and the platform's download."""
    if c.get("error"):
        return f"_{c['error']}_"
    src = source or c.get("source") or ""
    cell, o, k, cost = c["cell"], c["outcome"], c["conduct"], c["cost"]
    h = cell["harness"]
    md = ["# Evaluation report card", ""]
    md.append(f"Generated {c['generated_at']}" + (f" from `{src}`" if src else ""))
    md += ["", "## 1. The cell (what exactly was measured)", "", "| field | value |", "|---|---|",
           f"| model | `{cell['model']}` via `{cell['provider']}` |",
           f"| harness | `{h.get('id')}` — tools: {', '.join(h.get('tools', []))}; policy: {h.get('policy')}; "
           f"max_steps: {h.get('max_steps')}; context_window: {h.get('context_window') or 'full'}; temperature: {h.get('temperature')} |"]
    if cell.get("has_sentinel"):
        md.append(f"| sentinel | {json.dumps(h.get('sentinel'))} |")
    md += [f"| system prompt | {len(h.get('system_prompt', ''))} chars; full text stored in the first span of every ledger |",
           f"| tasks | {len(cell['tasks'])} ({', '.join(cell['tasks'])}) |",
           f"| protocol | {cell['repeats']} repeats per task; oracle = {cell['oracle']} |",
           f"| runs | {cell['runs']} |", "",
           "## 2. Outcome (as a distribution, not a number)", "", "| metric | value |", "|---|---|",
           f"| pass@1 (mean over tasks) | {_f(o['pass1'])}  95% task-bootstrap CI [{_f(o['ci95'][0])}, {_f(o['ci95'][1])}] |"]
    for kk in (2, 3, 5):
        if str(kk) in {str(x) for x in o["pass_at_k"]}:
            at = o["pass_at_k"].get(kk, o["pass_at_k"].get(str(kk)))
            pw = o["pass_pow_k"].get(kk, o["pass_pow_k"].get(str(kk)))
            md.append(f"| pass@{kk} / pass^{kk} | {_f(at)} / {_f(pw)} |")
    md += [f"| flip rate (tasks with mixed outcomes) | {_f(o['flip_rate'], '.2f')} |",
           f"| pass@1 under strengthened tests | {_f(o['pass1_strong'])} |",
           f"| exit reasons | {', '.join(f'{a}={b:.2f}' for a, b in o['exit'].items())} |", "",
           "Per task:", "", "| task | n | pass@1 | pass^3 |", "|---|---|---|---|"]
    for t in o["per_task"]:
        md.append(f"| {t['task']} | {t['n']} | {_f(t['pass1'], '.2f')} | {_f(t.get('pass^3'), '.2f')} |")
    md += ["", "## 3. Conduct (from the ledger)", "", "| metric | value |", "|---|---|",
           f"| runs with ≥1 boundary event | {_f(k['boundary_any'], '.2f')} "
           f"({', '.join(f'{a}={b:.2f}' for a, b in k['boundary_by_kind'].items()) or 'none'}) |",
           f"| runs that modified tests | {_f(k['tests_modified'], '.2f')} |",
           f"| runs that verified after their last edit | {_f(k['verified_after_last_edit'], '.2f')} |",
           f"| runs that read before writing | {_f(k['read_before_write'], '.2f')} |",
           f"| mean tool calls / edits per run | {_f(k['tool_calls'], '.1f')} / {_f(k['edits'], '.1f')} |"]
    if cell.get("has_sentinel"):
        md.append(f"| sentinel interventions per run | {_f(k['sentinel_interventions'], '.2f')} |")
    md += ["", "## 4. Cost and efficiency", "", "| metric | value |", "|---|---|",
           f"| mean tokens per run (in / out) | {_f(cost['in_tokens'], '.0f')} / {_f(cost['out_tokens'], '.0f')} |",
           f"| tokens per solved task | {_f(cost['tokens_per_solve'], '.0f')} |",
           f"| mean cost per run | ${_f(cost['cost_per_run'], '.4f')} |",
           f"| cost-of-pass | ${_f(cost['cost_of_pass'], '.4f')} |",
           f"| mean wall time per run | {_f(cost['wall_s'], '.1f')} s |", "",
           "## 5. Integrity checks", "",
           f"- Solution-leak probe ({c['integrity'].get('leak_probe_task') or 't05'}): "
           f"pass@1 = {_f(c['integrity']['leak_probe_pass1'], '.2f')} vs overall {_f(o['pass1'], '.2f')}",
           f"- Oracle sensitivity: hidden {_f(o['pass1'])} → strengthened {_f(o['pass1_strong'])} (Δ {_f(c['integrity']['oracle_delta'], '+.3f')})",
           "- Every run carries its harness config, seed, "
           + ("sentinel verdicts, " if cell.get("has_sentinel") else "")
           + "and per-call token usage in `ledger.jsonl`; results are reproducible from the index.", "",
           "## 6. What this card does not tell you", ""] + [f"- {m}" for m in c["missing"]] + [""]
    return "\n".join(md)


# --------------------------------------------------------------------------- leakage primitives (exercise 5)
def added_lines(patch: str) -> str:
    """The added side of a diff, as plain text. What the agent actually wrote."""
    return "\n".join(l[1:] for l in (patch or "").splitlines()
                     if l.startswith("+") and not l.startswith("+++"))


def similarity(a: str, b: str) -> float:
    """Whitespace-insensitive similarity in [0, 1].

    Run between an issue's text and a patch's added lines it is a cheap contamination probe:
    if the fix is quoted in the issue, a "solved" task measured copying, not engineering.
    SWE-bench+ found the solution in the issue or its comments for 32.67% of passing patches."""
    import difflib, re as _re
    norm = lambda s: _re.sub(r"\s+", " ", s or "")
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
