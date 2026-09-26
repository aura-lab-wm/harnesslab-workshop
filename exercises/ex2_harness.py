"""Exercise 2: the harness is a hidden variable.

Same model, same tasks, different harnesses (tool surface, permission policy,
context management, step budget, system prompt). Which scores move, which
costs move, and can you tell the difference from a paired comparison?

    python exercises/ex2_harness.py
    python exercises/ex2_harness.py --results data/runs/mine --baseline baseline --treatment no_test_tool
"""
from _common import parse, try_matplotlib, hr
import os
from harnesslab.core.analysis import load_index, filter_rows, summarize, fmt_summary, paired_bootstrap, exit_reasons, tokens_per_solve, aggregate


def extra(ap):
    ap.add_argument("--baseline", default="baseline")
    ap.add_argument("--treatment", default="no_test_tool")


args = parse(__doc__, extra)
rows = load_index(args.results)
harnesses = sorted({r["harness_id"] for r in rows})

# ---------------------------------------------------------------- 1. one row per harness
hr("1. Same model, six harnesses (outcome = hidden tests)")
summaries = {h: summarize(filter_rows(rows, harness_id=h), h) for h in harnesses}
for h in harnesses:
    print(fmt_summary(summaries[h]))
print("\nColumns: tok/solve = total tokens / solved runs (Vats & Golev 2026); $/pass = cost-of-pass (Erol et al. 2025);")
print("boundary = share of runs with >= 1 policy event; verified = share of runs that ran tests after their last edit.")

# ---------------------------------------------------------------- 2. exit reasons
hr("2. How runs end, by harness (failure modes are harness-specific)")
reasons = sorted({k for h in harnesses for k in exit_reasons(filter_rows(rows, harness_id=h))})
print(f"{'harness':16s}" + "".join(f"{r:>16s}" for r in reasons))
for h in harnesses:
    er = exit_reasons(filter_rows(rows, harness_id=h))
    print(f"{h:16s}" + "".join(f"{er.get(r, 0):16.2f}" for r in reasons))

# ---------------------------------------------------------------- 3. paired comparison
hr(f"3. Paired, task-level bootstrap: {args.treatment} minus {args.baseline}")
pb = paired_bootstrap(filter_rows(rows, harness_id=args.baseline), filter_rows(rows, harness_id=args.treatment))
print(f"tasks={pb['n_tasks']}  mean difference in pass rate = {pb['mean_diff']:+.3f}   95% CI [{pb['ci95'][0]:+.3f}, {pb['ci95'][1]:+.3f}]")
for t, d in pb["per_task"].items():
    print(f"   {t:24s} {d:+.2f}")
a, b = filter_rows(rows, harness_id=args.baseline), filter_rows(rows, harness_id=args.treatment)
print(f"\ntokens per solve: {args.baseline}={tokens_per_solve(a):.0f}  {args.treatment}={tokens_per_solve(b):.0f}  "
      f"ratio={tokens_per_solve(b)/tokens_per_solve(a) if tokens_per_solve(a) else float('nan'):.2f}x")
print("If the CI includes zero, the harnesses are not distinguishable on pass rate with this many tasks,")
print("even when the cost columns differ by a large factor. That is the scaffold effect in miniature.")

# ---------------------------------------------------------------- 4. figure: the response surface, two axes of it
plt = try_matplotlib()
if plt:
    fig, ax = plt.subplots(figsize=(6, 3.8))
    for h in harnesses:
        s = summaries[h]
        ax.scatter(s["tokens_per_solve"], s["pass@1"], s=60)
        ax.annotate(h, (s["tokens_per_solve"], s["pass@1"]), textcoords="offset points", xytext=(5, 4), fontsize=8)
    ax.set_xlabel("tokens per solved task"); ax.set_ylabel("pass@1 (hidden tests)"); ax.set_ylim(0, 1.02)
    ax.set_title("Same model, different harnesses"); ax.grid(alpha=0.3)
    p = os.path.join(args.out, "ex2_harness_surface.png"); fig.tight_layout(); fig.savefig(p, dpi=150)
    print(f"\nfigure -> {p}")

hr("Questions")
print("""Q1. Which harness change moved pass@1 the most? Which moved tokens per solve the most? Are they the same?
Q2. `no_test_tool` removes the agent's ability to verify. Look at `verified` and `flip` for it. What does
    an agent that cannot run tests do with a buggy first attempt, and how does that show up in pass^k?
Q3. `permissive` has the same pass rate as `baseline` on most tasks. Which column tells you they are
    not the same agent? Would a leaderboard have shown the difference?
Q4. Write a new harness JSON (copy harnesses/baseline.json) that you predict will *raise* tokens per
    solve without changing pass@1. Run it with --repeats 3 and check your prediction.
Q5. (live keys) Run the same two harnesses with a real model on 3 tasks x 3 repeats. Does the ranking
    of harnesses agree with the recorded runs'? What would you need to claim that it generalises?""")
