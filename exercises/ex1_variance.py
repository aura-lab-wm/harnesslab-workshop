"""Exercise 1: the artifact under test does not hold still.

Same model, same harness, same task, repeated runs. How much do outcomes,
patches, and trajectories differ? What does one passing run actually tell you?

    python exercises/ex1_variance.py                         # pre-recorded data
    python exercises/ex1_variance.py --results data/runs/mine --harness baseline
"""
from _common import parse, try_matplotlib, hr, LAB_ROOT
import hashlib, os
from collections import defaultdict
from harnesslab.core.analysis import (load_index, load_ledger, filter_rows, task_table, aggregate, bootstrap_ci, tokens_of,
                               flip_rate, pass_at_k, pass_pow_k)

args = parse(__doc__)
rows = filter_rows(load_index(args.results), harness_id=args.harness)
if not rows:
    raise SystemExit(f"no runs for harness {args.harness!r} in {args.results}")
n_rep = max(r["repeat_index"] for r in rows) + 1

# ---------------------------------------------------------------- 1. outcome grid
hr(f"1. Outcome grid  (harness={args.harness}, model={rows[0]['model']}, {n_rep} repeats per task)")
by_task = defaultdict(dict)
for r in rows:
    by_task[r["task_id"]][r["repeat_index"]] = r
print(f"{'task':24s} " + " ".join(f"r{i}" for i in range(n_rep)) + "   pass@1")
for tid in sorted(by_task):
    cells = ["✓ " if by_task[tid].get(i, {}).get("hidden_pass") else "✗ " for i in range(n_rep)]
    p = sum(1 for i in range(n_rep) if by_task[tid].get(i, {}).get("hidden_pass")) / n_rep
    print(f"{tid:24s} " + " ".join(cells) + f"   {p:.2f}")

# ---------------------------------------------------------------- 2. pass@k vs pass^k
hr("2. pass@k (any of k succeeds) versus pass^k (all k succeed), averaged over tasks")
ks = [k for k in range(1, n_rep + 1)]
tt = task_table(rows, k_values=ks)
print(f"{'k':>3} {'pass@k':>8} {'pass^k':>8}")
curve_any, curve_all = [], []
for k in ks:
    a = sum(v[f"pass@{k}"] for v in tt.values()) / len(tt)
    b = sum(v[f"pass^{k}"] for v in tt.values()) / len(tt)
    curve_any.append(a); curve_all.append(b)
    print(f"{k:>3} {a:8.3f} {b:8.3f}")
print("\nRead this as: pass@k is what a leaderboard with retries reports; pass^k is what a user")
print("who needs the agent to work every time experiences (Yao et al. 2024).")

# ---------------------------------------------------------------- 3. uncertainty
hr("3. How sure are we about pass@1?")
outcomes = [1.0 if r["hidden_pass"] else 0.0 for r in rows]
lo, hi = bootstrap_ci(outcomes, B=3000)
task_rates = [v["pass1"] for v in tt.values()]
tlo, thi = bootstrap_ci(task_rates, B=3000)
print(f"run-level  pass@1 = {sum(outcomes)/len(outcomes):.3f}   95% CI over runs  [{lo:.3f}, {hi:.3f}]  (n={len(rows)} runs)")
print(f"task-level pass@1 = {sum(task_rates)/len(task_rates):.3f}   95% CI over tasks [{tlo:.3f}, {thi:.3f}]  (n={len(tt)} tasks)")
print(f"flip rate = {flip_rate(rows):.2f}  (fraction of tasks whose outcome is not constant across repeats)")
print("The task-level interval is the honest one for a benchmark claim: tasks are the sampling unit.")

# ---------------------------------------------------------------- 4. the patches differ too
hr("4. Do passing runs produce the same software?")
print(f"{'task':24s} {'distinct patches':>16s} {'distinct tool sequences':>24s} {'steps (min-max)':>16s} {'tokens (min-max)':>18s}")
for tid in sorted(by_task):
    runs = list(by_task[tid].values())
    patches = set()
    seqs = set()
    for r in runs:
        with open(os.path.join(args.results, r["run_id"], "patch.diff")) as f:
            patches.add(hashlib.sha1(f.read().encode()).hexdigest()[:8])
        seqs.add(tuple(s["gen_ai.tool.name"] for s in load_ledger(args.results, r["run_id"]) if s["span"] == "execute_tool"))
    steps = [r["steps"] for r in runs]
    toks = [t for t in (tokens_of(r) for r in runs) if t is not None]   # None = nobody metered it
    span = f"{min(toks):>8d}-{max(toks):<9d}" if toks else f"{'unmeasured':>18s}"
    print(f"{tid:24s} {len(patches):>16d} {len(seqs):>24d} {min(steps):>7d}-{max(steps):<8d} {span}")

# ---------------------------------------------------------------- 5. figure
plt = try_matplotlib()
if plt:
    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.plot(ks, curve_any, marker="o", label="pass@k  (any of k)")
    ax.plot(ks, curve_all, marker="s", label="pass^k  (all k)")
    ax.set_xlabel("k"); ax.set_ylabel("probability"); ax.set_ylim(0, 1.02); ax.set_xticks(ks)
    ax.set_title(f"{args.harness}: retries look good, reliability decays"); ax.legend(frameon=False); ax.grid(alpha=0.3)
    p = os.path.join(args.out, f"ex1_passk_{args.harness}.png"); fig.tight_layout(); fig.savefig(p, dpi=150)
    print(f"\nfigure -> {p}")

hr("Questions")
print("""Q1. Pick a task with a flip. Open its two run directories (patch.diff, messages.json). Where do the
    trajectories first diverge? Is the divergence in *what* the agent did or in *when* it did it?
Q2. If you had run each task once, which number in section 2 would you have reported? How far is it
    from pass^5? Which one would you want in a paper you are reviewing?
Q3. The run-level and task-level CIs differ. Why is the task-level one wider, and which one is the
    right one for the claim "agent X resolves p% of issues"?
Q4. How many repeats would you need for the CI on pass@1 to be narrower than 10 points? Estimate it
    from the binomial variance and check by subsampling.""")
