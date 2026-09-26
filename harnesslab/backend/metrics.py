"""Analysis endpoints' logic: everything the exercises compute, as JSON-ready dicts.
Reuses harnesslab.core.analysis so the platform and the CLI exercises never disagree."""
from __future__ import annotations
import difflib, json, math, os, re, time
from collections import Counter, defaultdict

from harnesslab.core.analysis import (load_index, filter_rows, task_table, aggregate, bootstrap_ci, paired_bootstrap, flip_rate,
                               boundary_rate, exit_reasons, tokens_per_solve, cost_of_pass, mean, summarize, ochiai_attribution,
                               factorial, power_n, wrong_winner, per_task_rates, tokens_of,
                               cohens_kappa, comparisons_for, factor_summary)
from harnesslab.core.grader import load_task
from harnesslab.core.trajtest import load_runs
from harnesslab.core.real_traj import ledger_rows_as_features
from harnesslab.core import patterns as P
from harnesslab.core.reportcard import build_card, card_markdown, added_lines, similarity

from .paths import LAB_ROOT, RUNS_ROOT   # checkout or installed wheel
from .results_scope import list_results_dirs


def _clean(x):
    """Replace NaN/inf so the JSON is valid."""
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def results_dirs() -> list[dict]:
    out = []
    for name in list_results_dirs(RUNS_ROOT):
        idx = os.path.join(RUNS_ROOT, name, "index.jsonl")
        rows = load_index(os.path.join(RUNS_ROOT, name))
        out.append({"name": name, "runs": len(rows),
                    "harnesses": sorted({r["harness_id"] for r in rows}),
                    "models": sorted({r["model"] for r in rows}),
                    "tasks": sorted({r["task_id"] for r in rows}),
                    "pass_rate": mean(1.0 if r.get("hidden_pass") else 0.0 for r in rows) if rows else None,
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(idx)))})
    return _clean(out)


def rows_for(name: str, **filters) -> list[dict]:
    d = os.path.join(RUNS_ROOT, name)
    return filter_rows(load_index(d), **{k: v for k, v in filters.items() if v})


def cells(rows: list[dict]) -> list[dict]:
    """One summary per (model, harness) cell, plus the pass@k / pass^k curve and a task-bootstrap CI."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["harness_id"])].append(r)
    out = []
    for (model, h), R in sorted(groups.items()):
        s = summarize(R, label=h)
        n_rep = max(Counter(r["task_id"] for r in R).values())
        ks = list(range(1, n_rep + 1))
        agg = aggregate(R, k_values=tuple(ks))
        tt = task_table(R, k_values=tuple(ks))
        ci = bootstrap_ci([v["pass1"] for v in tt.values()])
        strong = aggregate(R, "strong_pass")
        s.update({"model": model, "harness": h, "repeats": n_rep,
                  "ci95": list(ci), "pass1_strong": strong.get("pass@1"),
                  "passk_curve": [{"k": k, "pass_at_k": agg.get(f"pass@{k}"), "pass_pow_k": agg.get(f"pass^{k}")} for k in ks],
                  "tasks": {t: {"n": v["n"], "c": v["c"], "pass1": v["pass1"], "outcomes": [bool(r.get("hidden_pass")) for r in sorted(R, key=lambda r: r["repeat_index"]) if r["task_id"] == t]} for t, v in tt.items()},
                  "boundary": boundary_rate(R),
                  "sentinel_interventions": mean(r.get("sentinel_interventions", 0) for r in R),
                  "mean_wall_s": mean(r["wall_ms"] for r in R) / 1000})
        # cost/efficiency detail (the console's Telemetry view): spread, not just the mean
        # over runs whose usage / tool execution was RECORDED: a null is unknown, and read as zero it
        # would pull every mean toward "calls no tools, spends nothing" (importers.common.UNMEASURED_FIELDS)
        toks = sorted(t for t in (tokens_of(r) for r in R) if t is not None)
        spent = [r for r in R if tokens_of(r) is not None]
        inn = sum(r["input_tokens"] for r in spent)
        outt = sum(r["output_tokens"] for r in spent)
        s.update({"p95_tokens": toks[min(len(toks) - 1, int(0.95 * len(toks)))] if toks else None,
                  "calls_per_run": mean(r.get("steps") or 0 for r in R),
                  "tool_calls_per_run": mean(r.get("tool_calls", 0) for r in R),
                  "in_out_ratio": (inn / outt) if outt else None})
        out.append(s)
    # "agency tax": cost-of-pass relative to the baseline cell, per model
    for model in {c["model"] for c in out}:
        peers = [c for c in out if c["model"] == model]
        ref = next((c for c in peers if c["harness"] == "baseline"), peers[0] if peers else None)
        base = ref and ref.get("cost_of_pass")
        for c in peers:
            cop = c.get("cost_of_pass")
            c["agency_tax"] = (cop / base) if (base and cop and math.isfinite(cop) and math.isfinite(base)) else None
    return _clean(out)


def harness_comparison(rows: list[dict], baseline: str, model: str | None = None) -> dict:
    R = filter_rows(rows, model=model) if model else rows
    base = filter_rows(R, harness_id=baseline)
    out = {}
    for h in sorted({r["harness_id"] for r in R}):
        if h == baseline:
            continue
        pb = paired_bootstrap(base, filter_rows(R, harness_id=h))
        out[h] = pb
    return _clean(out)


def comparisons(rows: list[dict], baseline: str | None) -> dict:
    """Every model's per-HARNESS paired comparison against the `baseline` HARNESS, in one response
    -- {model: {harness: paired_bootstrap result}}.

    This is exactly what /metrics?model=<m>&baseline=<b> returns for ONE model, batched over all
    of them: the attribution screen used to make one round trip per model to assemble its
    model x harness grid, and this replaces those N calls with one. It groups the rows by model
    in a single pass and hands each model's rows to `harness_comparison` -- the SAME function
    /metrics calls -- so the two endpoints share one call site for the statistic and cannot
    disagree about it. (docs/superpowers/specs/2026-09-21-study-aggregates-design.md)

    The first version of this compared MODELS against a baseline MODEL instead, pooling every
    harness a model ran under. That answers "which model is better", not "which harness moved
    which model", and it silently dropped the harness axis the attribution screen exists to show
    -- on llma4se_live it lost all four cells whose interval clears zero.
    """
    by_model: dict[str, list[dict]] = {}
    for r in rows:                                   # one grouping pass
        by_model.setdefault(r["model"], []).append(r)
    out = {} if baseline is None else {
        m: harness_comparison(rs, baseline) for m, rs in sorted(by_model.items())}
    return {"baseline": baseline, "comparisons": out}


def resolve_baseline_harness(rows: list[dict], requested: str | None) -> str | None:
    """The ONE rule for which harness a comparison is taken against, shared by the /comparisons
    route and the `comparisons` CLI. They each carried a copy of this, and both copies resolved
    against MODEL ids -- which is how the endpoint came to compare models when it should compare
    harnesses. `requested` wins when it is a real harness in this study; otherwise "baseline" when
    the study has one, else the alphabetically first harness."""
    harnesses = sorted({r["harness_id"] for r in rows})
    if requested in harnesses:
        return requested
    return "baseline" if "baseline" in harnesses else (harnesses[0] if harnesses else None)


def oracle(rows: list[dict]) -> dict:
    """Agreement between the hidden-test verdict and the strong-suite verdict, over every run in
    the study, via the canonical `cohens_kappa` -- the SAME null-safe agreement statistic the CLI's
    `oracle` command and (previously) the judge/report screens' own kappa math must resolve to."""
    pairs = [(r.get("hidden_pass"), r.get("strong_pass")) for r in rows]
    return _clean(cohens_kappa(pairs))


def sentinel_pairs(rows: list[dict]) -> list[dict]:
    """Task-paired bootstrap of X vs X+sentinel for every (model, harness) that has a twin."""
    cells = {(r["model"], r["harness_id"]) for r in rows}
    out = []
    for model, h in sorted(cells):
        if h.endswith("+sentinel") or (model, h + "+sentinel") not in cells:
            continue
        A = [{**r, "boundary_any": 1 if (r.get("boundary_events") or 0) > 0 else 0} for r in filter_rows(rows, harness_id=h, model=model)]
        B = [{**r, "boundary_any": 1 if (r.get("boundary_events") or 0) > 0 else 0} for r in filter_rows(rows, harness_id=h + "+sentinel", model=model)]
        out.append({"model": model, "harness": h, "runs": [len(A), len(B)],
                    "hidden_pass": paired_bootstrap(A, B, "hidden_pass"),
                    "verified": paired_bootstrap(A, B, "ran_tests_before_submit"),
                    "boundary": paired_bootstrap(A, B, "boundary_any")})
    return _clean(out)


def leakage(name: str, rows: list[dict], harness: str) -> list[dict]:
    d = os.path.join(RUNS_ROOT, name)
    base = filter_rows(rows, harness_id=harness)
    tt = task_table(base)
    added, sim = added_lines, similarity
    out = []
    for tid in sorted(tt):
        tdir = os.path.join(LAB_ROOT, "tasks", tid)
        task = load_task(tdir) if os.path.exists(os.path.join(tdir, "task.json")) else None   # imported runs have no local task
        R = filter_rows(base, task_id=tid)
        sims = []
        if task:
            for r in R:
                p = os.path.join(d, r["run_id"], "patch.diff")
                if os.path.exists(p):
                    sims.append(sim(added(open(p).read()), task["issue"]))
        # pass1_strong: the same task under the strengthened suite, via the SAME aggregate weak_tests
        # uses -- the judge step's per-task "hidden vs strengthened" bars read it from here.
        out.append({"task": tid, "probe": task.get("probe") if task else None, "pass1": tt[tid]["pass1"],
                    "pass1_strong": aggregate(R, "strong_pass").get("pass@1"), "steps": mean(r["steps"] for r in R),
                    "tokens": mean(tokens_of(r) for r in R), "patch_issue_similarity": mean(sims) if sims else None})
    return _clean(out)


def integrity(name: str, rows: list[dict], harness: str) -> dict:
    """The judge step's integrity read, in ONE place: the /integrity route and `harnesslab
    integrity` both return exactly this. `harness` falls back to the alphabetically first harness
    when the study has no such harness, the same "legal default, not a 400" rule as elsewhere."""
    hs = {r["harness_id"] for r in rows}
    if harness not in hs and hs:
        harness = sorted(hs)[0]
    return {"leakage": leakage(name, rows, harness), "weak_tests": weak_tests(rows),
            "self_report": self_report(name, rows), "ochiai": ochiai(name, None),
            "ochiai_harness": ochiai(name, harness), "harness": harness}


def weak_tests(rows: list[dict]) -> dict:
    harnesses = sorted({r["harness_id"] for r in rows})
    hid = {h: aggregate(filter_rows(rows, harness_id=h), "hidden_pass").get("pass@1") for h in harnesses}
    strg = {h: aggregate(filter_rows(rows, harness_id=h), "strong_pass").get("pass@1") for h in harnesses}
    rank_h = {h: i + 1 for i, h in enumerate(sorted(harnesses, key=lambda h: -(hid[h] or 0)))}
    rank_s = {h: i + 1 for i, h in enumerate(sorted(harnesses, key=lambda h: -(strg[h] or 0)))}
    per_h = []
    for h in harnesses:
        R = filter_rows(rows, harness_id=h)
        passed = [r for r in R if r["hidden_pass"]]
        per_h.append({"harness": h, "hidden": hid[h], "strong": strg[h],
                      "survive": (sum(1 for r in passed if r["strong_pass"]) / len(passed)) if passed else None,
                      "rank_hidden": rank_h[h], "rank_strong": rank_s[h]})
    per_t = []
    for tid in sorted({r["task_id"] for r in rows}):
        R = [r for r in rows if r["task_id"] == tid and r["hidden_pass"]]
        per_t.append({"task": tid, "n_passed": len(R), "lost": (sum(1 for r in R if not r["strong_pass"]) / len(R)) if R else None})
    return _clean({"per_harness": per_h, "per_task": per_t})


def self_report(name: str, rows: list[dict]) -> list[dict]:
    d = os.path.join(RUNS_ROOT, name)
    traj = load_runs(d)
    out = []
    for h in sorted({r["harness_id"] for r in rows}):
        T = [t for t in traj if t.harness_id == h]
        if not T:
            continue
        claims = []
        for t in T:
            last = [s for s in t.tool_calls if s["gen_ai.tool.name"] == "run_tests"]
            claims.append(bool(last and last[-1].get("tests_passed")))
        n = len(T)
        out.append({"harness": h, "agent_says_pass": sum(claims) / n, "oracle_pass": sum(t.passed for t in T) / n,
                    "overclaim": sum(1 for t, c in zip(T, claims) if c and not t.passed) / n})
    return _clean(out)


def ochiai(name: str, harness: str | None) -> list[dict]:
    d = os.path.join(RUNS_ROOT, name)
    if harness:
        # ledger_rows_as_features has no harness column; re-filter through the index
        keep = {r["run_id"] for r in load_index(d) if r["harness_id"] == harness}
        feats = ledger_rows_as_features_filtered(d, keep)
    else:
        feats = ledger_rows_as_features(d)
    if not feats or sum(1 for f in feats if not f["resolved"]) == 0:
        return []
    return _clean(ochiai_attribution(feats))


def ledger_rows_as_features_filtered(d: str, keep: set) -> list[dict]:
    """The feature rows of `keep`, matched by run id.

    Zipping the rows against the run list assumed one row per run. A run whose tool calls were never
    measured has no row at all, so from the first such run onwards every row belonged to a different
    run than the one it was filtered by -- and the attribution below named the wrong runs.
    """
    from harnesslab.core.real_traj import ledger_rows_as_features_with_ids
    return [f for rid, f in ledger_rows_as_features_with_ids(d) if rid in keep]


def conduct(name: str, rows: list[dict], harness: str) -> dict:
    d = os.path.join(RUNS_ROOT, name)
    traj = load_runs(d, harness_id=harness)
    # Every rate below is a predicate over execute_tool spans. A run whose tool execution was never
    # captured has none, so it would vote "did not verify" and "read before writing" about a run in
    # which nothing was observed -- the guard reportcard.build_card and real_traj already apply.
    traj = [t for t in traj if not ("tool_calls" in t.summary and t.summary["tool_calls"] is None)]
    if not traj:
        return {}
    return _clean({
        "verified_after_last_edit": mean(1.0 if t.ran_tests_after_last_edit() else 0.0 for t in traj),
        "read_before_write": mean(1.0 if t.read_before_write() else 0.0 for t in traj),
        "repeated_tool_calls": mean(t.repeated_tool_calls() for t in traj),
        "tool_histogram": dict(Counter(n for t in traj for n in t.tool_names)),
    })


def report_card(name: str, rows: list[dict], harness: str) -> dict:
    """The exercise-6 card. Same builder and same markdown as `exercises/ex6_report_card.py`."""
    d = os.path.join(RUNS_ROOT, name)
    R = filter_rows(rows, harness_id=harness)
    if not R:
        return {"error": "no runs"}
    traj = load_runs(d, harness_id=harness)
    card = build_card(R, traj, source=f"data/runs/{name}")
    return _clean({"card": card, "markdown": card_markdown(card)})
# --------------------------------------------------------------------------- experiment (exercise 8)
FACTORS = {"model": "model", "harness_id": "harness", "task_id": "task", "results": "results dir"}


def experiment(rows: list[dict], a_key: str = "model", b_key: str = "harness_id",
               outcome: str = "hidden_pass", a_cell: str | None = None, b_cell: str | None = None) -> dict:
    """The exercise-8 factorial for whatever is in the filter, plus the two things a leaderboard hides:
    how many runs it would take to see the difference, and how often the ranking inverts at this n."""
    R = [r for r in rows if r.get(outcome) is not None]
    if not R:
        return {"error": "no graded runs under this oracle"}
    A = sorted({r[a_key] for r in R})
    B = sorted({r[b_key] for r in R})

    groups = defaultdict(list)
    for r in R:
        groups[(r[a_key], r[b_key])].append(r)

    def cell_stat(rs):
        tt = task_table(rs, outcome)
        rates = [v["pass1"] for v in tt.values()]
        lo, hi = bootstrap_ci(rates) if len(rates) > 1 else (float("nan"), float("nan"))
        c = sum(1 for r in rs if r.get(outcome))
        return {"n": len(rs), "passes": c, "pass1": c / len(rs), "ci95": [lo, hi], "tasks": len(tt)}

    cells_out = [{"a": a, "b": b, "key": f"{a}|{b}", **cell_stat(groups[(a, b)])}
                 for a in A for b in B if groups.get((a, b))]

    # the decomposition needs a balanced subset; say so rather than silently dropping cells
    bal_B = [b for b in B if all(groups.get((a, b)) for a in A)]
    bal_A = [a for a in A if all(groups.get((a, b)) for b in bal_B)]
    table = None
    if len(bal_A) >= 2 and len(bal_B) >= 2 and a_key != b_key:
        sub = [r for r in R if r[a_key] in bal_A and r[b_key] in bal_B]
        table = factorial(sub, a_key, b_key, "task_id", outcome)
        table["balanced_a"], table["balanced_b"] = bal_A, bal_B
        table["dropped"] = sorted((set(A) - set(bal_A)) | (set(B) - set(bal_B)))

    # `fit`: the pure summary of the ANOVA above (leading factor, each factor's share and level
    # range, interaction share), for the Experiment view -- it stopped deriving this client-side
    # once `factor_summary` became the one canonical implementation (docs/superpowers/specs/
    # 2026-09-21-study-aggregates-design.md). No anova table means no fit: explicit insufficient_data
    # with WHY, never an invented zero effect, same discipline as `comparisons_for` above.
    if table is not None:
        fit = factor_summary(table)
    elif a_key == b_key:
        fit = {"status": "insufficient_data", "reason": "same_factor"}
    elif len(bal_A) < 2:
        fit = {"status": "insufficient_data", "reason": "fewer_than_2_balanced_levels_a"}
    else:
        fit = {"status": "insufficient_data", "reason": "fewer_than_2_balanced_levels_b"}

    # every pairwise contrast of B, paired over tasks, inside each level of A
    contrasts = []
    for a in A:
        for i in range(len(B)):
            for j in range(i + 1, len(B)):
                ra, rb = groups.get((a, B[i])), groups.get((a, B[j]))
                if not ra or not rb:
                    continue
                pb_ = paired_bootstrap(ra, rb, outcome)
                if not pb_.get("n_tasks"):
                    continue
                lo, hi = pb_["ci95"]
                contrasts.append({"a": a, "from": B[i], "to": B[j], "delta": pb_["mean_diff"],
                                  "ci95": [lo, hi], "tasks": pb_["n_tasks"],
                                  "covers_zero": lo <= 0 <= hi})
    n_c = len(contrasts)
    out = {"factors": FACTORS, "a_key": a_key, "b_key": b_key, "outcome": outcome,
           "a_levels": A, "b_levels": B, "cells": cells_out, "anova": table, "fit": fit,
           "contrasts": contrasts,
           "multiplicity": {"n": n_c, "expected_false": n_c * 0.05,
                            "bonferroni_pct": (100 - 5 / n_c) if n_c else 95.0}}

    # the head-to-head the user picked
    ca, cb = groups.get(tuple(a_cell.split("|", 1)) if a_cell else None), groups.get(tuple(b_cell.split("|", 1)) if b_cell else None)
    if ca and cb:
        pa = sum(1 for r in ca if r.get(outcome)) / len(ca)
        pbv = sum(1 for r in cb if r.get(outcome)) / len(cb)
        paired = paired_bootstrap(cb, ca, outcome)
        p_lo, p_hi = paired.get("ci95", (float("nan"), float("nan")))
        ta, tb = task_table(ca, outcome), task_table(cb, outcome)
        shared = [t for t in ta if t in tb]
        sims = ([{**wrong_winner([ta[t]["pass1"] for t in shared], [tb[t]["pass1"] for t in shared], k)}
                 for k in (1, 2, 3, 5, 10)] if len(shared) >= 2 else [])
        base = max(0.05, min(0.95, pa))
        curve = [{"delta": d, "n_per_arm": power_n(base, max(0.01, min(0.99, base - d)))}
                 for d in (0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3)]
        out["head_to_head"] = {
            "a": {"key": a_cell, "n": len(ca), "pass1": pa}, "b": {"key": b_cell, "n": len(cb), "pass1": pbv},
            "paired_delta": paired.get("mean_diff"), "paired_ci95": [p_lo, p_hi],
            "paired_tasks": paired.get("n_tasks"),
            "n_needed_per_arm": power_n(pa, pbv), "have_per_arm": min(len(ca), len(cb)),
            "power_curve": curve, "wrong_winner": sims, "shared_tasks": len(shared)}
    return _clean(out)


# --------------------------------------------------------------------------- patterns & queries (exercise 3)
def _paired(name: str, harness: str | None = None, task: str | None = None, model: str | None = None):
    """(Trajectory, index row) pairs, so pattern code can see both the spans and the outcome."""
    d = os.path.join(RUNS_ROOT, name)
    kw = {k: v for k, v in (("harness_id", harness), ("task_id", task), ("model", model)) if v}
    traj = load_runs(d, **kw)
    idx = {r["run_id"]: r for r in load_index(d)}
    out = []
    for t in traj:
        rid = t.spans[0].get("run_id") if t.spans else None
        if rid in idx:
            out.append((t, idx[rid]))
    return out


def patterns(name: str, harness: str | None = None, task: str | None = None, model: str | None = None,
             outcome: str = "hidden_pass", clusters: int = 4) -> dict:
    runs = _paired(name, harness, task, model)
    if not runs:
        return {"error": "no runs for this selection"}
    strings = [t.action_string for t, _ in runs]
    graded = [(t, r) for t, r in runs if r.get(outcome) is not None]
    pass_s = [t.action_string for t, r in graded if r.get(outcome)]
    fail_s = [t.action_string for t, r in graded if not r.get(outcome)]

    def first_edit_hist(ss):
        v = [P.first_index(s, "WE") for s in ss]
        return [x for x in v if x >= 0]

    return _clean({
        "n_runs": len(runs), "alphabet": [{"code": a, "label": P.ACTION_LABEL[a]} for a in P.ALPHABET],
        "sequences": P.sequences(runs, outcome)[:30],
        "transitions": {"pass": P.transition_matrix(pass_s), "fail": P.transition_matrix(fail_s)},
        "step_positions": P.step_positions(strings),
        "divergence": P.divergence(runs, outcome),
        "clusters": P.cluster(strings, clusters),
        "first_edit": {"pass": first_edit_hist(pass_s), "fail": first_edit_hist(fail_s)},
        "n_pass": len(pass_s), "n_fail": len(fail_s),
    })


def run_query(name: str, pattern: str, harness: str | None = None, model: str | None = None,
              outcome: str = "hidden_pass", steps_min=None, steps_max=None, exit: str | None = None) -> dict:
    runs = _paired(name, harness, None, model)
    if not runs:
        return {"error": "no runs for this selection"}
    q = P.query(runs, pattern, outcome, steps_min=steps_min, steps_max=steps_max, exit=exit)
    if "error" not in q:
        q["unittest"] = P.as_unittest(pattern, name=f"query_{abs(hash(pattern)) % 10000}")
        q["total"] = len(runs)
    return _clean(q)
