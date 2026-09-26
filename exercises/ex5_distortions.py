"""Exercise 5: two distortions that inflate published results.

(a) Contamination / solution leakage: task t05's issue contains the fix.
    How does that show up in outcomes, effort, and patch similarity?
(b) Weak tests: the hidden tests are the benchmark's oracle; a strengthened
    suite (UTBoost-style) re-grades the same patches. How many "resolved"
    runs survive, and does the harness ranking survive?
(c) Self-reported success: the agent's own last test run versus the oracle.

    python exercises/ex5_distortions.py
"""
from _common import parse, try_matplotlib, hr, LAB_ROOT
import difflib, os, re
from collections import defaultdict
from harnesslab.core.analysis import load_index, filter_rows, aggregate, task_table, mean, tokens_of
from harnesslab.core.grader import load_task
from harnesslab.core.reportcard import added_lines, similarity
from harnesslab.core.trajtest import load_runs

args = parse(__doc__)
rows = load_index(args.results)
harnesses = sorted({r["harness_id"] for r in rows})
tasks = {tid: load_task(os.path.join(LAB_ROOT, "tasks", tid)) for tid in {r["task_id"] for r in rows}}

# ---------------------------------------------------------------- (a) leakage
hr("(a) Solution leakage: t05_leaky_duration versus the other tasks")
base = filter_rows(rows, harness_id=args.harness)
tt = task_table(base)
print(f"{'task':24s} {'pass@1':>7s} {'steps':>6s} {'tokens':>7s} {'patch~issue':>12s}   (similarity of the added lines to the issue text)")




for tid in sorted(tt):
    R = filter_rows(base, task_id=tid)
    sims = []
    for r in R:
        with open(os.path.join(args.results, r["run_id"], "patch.diff")) as f:
            sims.append(similarity(added_lines(f.read()), tasks[tid]["issue"]))
    flag = "  <-- solution in the issue" if tasks[tid]["probe"] == "solution_leak" else ""
    toks = [t for t in (tokens_of(r) for r in R) if t is not None]
    tok_txt = f"{mean(toks):7.0f}" if toks else f"{'—':>7s}"
    print(f"{tid:24s} {tt[tid]['pass1']:7.2f} {mean(r['steps'] for r in R):6.1f} {tok_txt} {mean(sims):12.2f}{flag}")
print("\nSWE-bench+ (Aleithan et al. 2024): 32.67% of passed patches had the solution in the issue or comments.")
print("Leakage inflates pass rate AND deflates cost, so it also distorts cost-of-pass comparisons.")

# ---------------------------------------------------------------- (b) weak tests
hr("(b) Re-grading with strengthened tests")
print(f"{'harness':16s} {'pass(hidden)':>13s} {'pass(strong)':>13s} {'survive':>9s}   rank(hidden) -> rank(strong)")
hid = {h: aggregate(filter_rows(rows, harness_id=h), "hidden_pass")["pass@1"] for h in harnesses}
strg = {h: aggregate(filter_rows(rows, harness_id=h), "strong_pass")["pass@1"] for h in harnesses}
rank_h = {h: i + 1 for i, h in enumerate(sorted(harnesses, key=lambda h: -hid[h]))}
rank_s = {h: i + 1 for i, h in enumerate(sorted(harnesses, key=lambda h: -strg[h]))}
for h in harnesses:
    R = filter_rows(rows, harness_id=h)
    passed = [r for r in R if r["hidden_pass"]]
    survive = sum(1 for r in passed if r["strong_pass"]) / len(passed) if passed else float("nan")
    print(f"{h:16s} {hid[h]:13.3f} {strg[h]:13.3f} {survive:9.2f}   {rank_h[h]} -> {rank_s[h]}")
print("\nPer task: share of hidden-passing runs that fail the strengthened suite (all harnesses pooled)")
for tid in sorted(tasks):
    R = [r for r in rows if r["task_id"] == tid and r["hidden_pass"]]
    if R:
        lost = sum(1 for r in R if not r["strong_pass"]) / len(R)
        print(f"   {tid:24s} n={len(R):3d} lost={lost:.2f}")
print("UTBoost (Yu et al. 2025): 345 patches on SWE-bench were mislabelled as passing; 24.4% of Verified leaderboard entries affected.")
print("Caveat from the same paper: strengthened tests can themselves be wrong. Read t01's strong test and decide whether you agree with it.")

# ---------------------------------------------------------------- (c) self-report
hr("(c) Self-reported success versus the oracle")
traj = load_runs(args.results)
print(f"{'harness':16s} {'agent says pass':>16s} {'oracle pass':>12s} {'overclaim':>10s}")
for h in harnesses:
    T = [t for t in traj if t.harness_id == h]
    claims = []
    for t in T:
        last = [s for s in t.tool_calls if s["gen_ai.tool.name"] == "run_tests"]
        claims.append(bool(last and last[-1].get("tests_passed")))
    n = len(T)
    said = sum(claims) / n
    oracle = sum(t.passed for t in T) / n
    over = sum(1 for t, c in zip(T, claims) if c and not t.passed) / n
    print(f"{h:16s} {said:16.2f} {oracle:12.2f} {over:10.2f}")
print("'overclaim' = runs where the agent's own visible tests passed but the hidden tests did not.")
print("Visible tests are part of the *task presentation*, and therefore part of the cell you are measuring.")

plt = try_matplotlib()
if plt:
    fig, ax = plt.subplots(figsize=(6, 3.6))
    xs = range(len(harnesses))
    ax.bar([x - 0.2 for x in xs], [hid[h] for h in harnesses], width=0.4, label="hidden tests")
    ax.bar([x + 0.2 for x in xs], [strg[h] for h in harnesses], width=0.4, label="strengthened tests")
    ax.set_xticks(list(xs)); ax.set_xticklabels(harnesses, rotation=20, fontsize=8); ax.set_ylim(0, 1)
    ax.set_ylabel("pass@1"); ax.set_title("Same patches, two oracles"); ax.legend(frameon=False); ax.grid(axis="y", alpha=0.3)
    p = os.path.join(args.out, "ex5_oracles.png"); fig.tight_layout(); fig.savefig(p, dpi=150)
    print(f"\nfigure -> {p}")

hr("Questions")
print("""Q1. Propose two cheap leakage detectors that work at benchmark-construction time (no agent runs needed).
    Would either have flagged t05? What would they miss?
Q2. Did the harness ranking change between the hidden and strengthened oracle? If a leaderboard had
    used the hidden suite, which claims in a paper would now be wrong?
Q3. Pick a task where 'lost' is high. Is the strengthened test checking the issue's intent, or a stricter
    spec of your own? Who gets to decide, and how would a benchmark record that decision?
Q4. Design a task-retirement rule for a living benchmark (SWE-bench-Live, SWE-rebench, LiveCodeBench all
    have one). What signal triggers retirement, and what do you do with the historical scores?""")
