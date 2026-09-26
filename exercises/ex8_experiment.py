"""Exercise 8: from benchmark to experiment.

An 8 x 2 factorial on real recorded runs: eight models (llma4se_live + live, all through
OpenRouter) x two harnesses (baseline, no_test_tool), eight tasks as blocks = 688 runs. Fit

    Y_ijk = mu + alpha_i(model) + beta_j(harness) + (alpha beta)_ij + u_k(task) + eps_ijk

by sums of squares, read the variance shares, then test the harness effect
within each model with a paired-task bootstrap. Then add your own factor.

    python exercises/ex8_experiment.py
    python exercises/ex8_experiment.py --results data/runs/prerecorded_mock --results2 data/runs/prerecorded_mock_weak   # the 2 x 2 mock version
"""
from _common import parse, try_matplotlib, hr, LAB_ROOT
import os
from harnesslab.core.analysis import load_index, filter_rows, factorial, paired_bootstrap, bootstrap_ci, per_task_rates


def extra(ap):
    ap.add_argument("--results2", default=os.path.join(LAB_ROOT, "data", "runs", "live"))
    ap.add_argument("--harnesses", default="baseline,no_test_tool")


args = parse(__doc__, extra)
hs = args.harnesses.split(",")
rows = [r for r in load_index(args.results) + load_index(args.results2) if r["harness_id"] in hs]
models = sorted({r["model"] for r in rows})

hr("1. The design")
print(f"models={models}  harnesses={hs}  tasks={len({r['task_id'] for r in rows})}  runs={len(rows)}")
print(f"{'cell':32s} {'pass@1':>7s} {'95% task-bootstrap CI':>24s}")
for m in models:
    for h in hs:
        cell = filter_rows(rows, harness_id=h, model=m)
        rates = list(per_task_rates(cell).values())
        lo, hi = bootstrap_ci(rates, B=2000)
        print(f"{m + ' | ' + h:32s} {sum(rates)/len(rates):7.3f} {'[' + f'{lo:.3f}, {hi:.3f}' + ']':>24s}")

hr("2. Y_ijk = mu + model_i + harness_j + (model x harness)_ij + task_k + eps_ijk")
f = factorial(rows)
print(f"{'term':24s} {'SS':>8s} {'df':>4s} {'MS':>8s} {'F':>8s} {'share':>7s}")
for t in f["table"]:
    print(f"{t['term']:24s} {t['SS']:8.2f} {t['df']:4d} {t['MS']:8.3f} {t['F']:8.2f} {t['share']:7.1%}")
print(f"\ngrand mean {f['grand_mean']:.3f}; model means {{{', '.join(f'{k}: {v:.2f}' for k, v in f['a_means'].items())}}}; "
      f"harness means {{{', '.join(f'{k}: {v:.2f}' for k, v in f['b_means'].items())}}}")
print("Read the share column. On the recorded runs, the task block and the repeat residual dwarf both treatments: most of the")
print("variance in a resolve rate is *which task* and *which run*, not which model or harness. That is why single runs on")
print("few tasks cannot rank harnesses, and why paired designs (next section) recover power.")

hr("3. The harness effect, paired on tasks, within each model")
for m in models:
    a = filter_rows(rows, harness_id=hs[0], model=m); b = filter_rows(rows, harness_id=hs[1], model=m)
    pb = paired_bootstrap(a, b)
    print(f"{m:12s} {hs[1]} − {hs[0]}: Δ = {pb['mean_diff']:+.3f}   95% CI [{pb['ci95'][0]:+.3f}, {pb['ci95'][1]:+.3f}]   tasks={pb['n_tasks']}")
d_strong = paired_bootstrap(filter_rows(rows, harness_id=hs[0], model=models[0]), filter_rows(rows, harness_id=hs[1], model=models[0]))["mean_diff"]
d_weak = paired_bootstrap(filter_rows(rows, harness_id=hs[0], model=models[-1]), filter_rows(rows, harness_id=hs[1], model=models[-1]))["mean_diff"]
print(f"\ninteraction estimate (difference of harness effects across models) = {d_weak - d_strong:+.3f}")
print("If the interaction is not distinguishable from zero, the harness effect is (on this evidence) additive across models;")
print("HarnessBank's claim that the optimal harness is model-specific is a claim that this number is non-zero.")

hr("4. A bad ablation, reconstructed")
print("""Suppose a paper reports: 'our new harness improves pass@1 from 0.59 to 0.66'. Check the ledger's span 0 for the two
runs: if the new harness also changed the prompt, added a tool, doubled max_steps, and changed the retry policy, then
Δ_H is the sum of four effects and their interactions, and 'the harness helps' is not a claim the data can carry.
Design the controlled version: one factor at a time (4 cells), or a 2^4 factorial (16 cells) if you can afford it,
blocked on task, ≥5 repeats, paired bootstrap per contrast. Write down the cell table before running anything.""")

plt = try_matplotlib()
if plt:
    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    for i, m in enumerate(models):
        ys = [f["cell_means"][f"{m} | {h}"] for h in hs]
        ax.plot(range(len(hs)), ys, marker="o", label=m)
    ax.set_xticks(range(len(hs))); ax.set_xticklabels(hs); ax.set_ylabel("pass@1"); ax.set_ylim(0, 1)
    ax.set_title("Interaction plot: parallel lines = no interaction"); ax.legend(frameon=False); ax.grid(alpha=0.3)
    p = os.path.join(args.out, "ex8_interaction.png"); fig.tight_layout(); fig.savefig(p, dpi=150)
    print(f"\nfigure -> {p}")

hr("Questions")
print("""Q1. Which term explains the most variance? What would have to be true of the task set for the 'task' share to shrink?
Q2. The residual share is the run-to-run noise. How many repeats would halve its contribution to the CI on a cell mean?
Q3. Add a third harness (any of the six) and refit. Does the harness share grow, and does the interaction appear?
Q4. Live: run 2 tasks x 2 harnesses x 3 repeats with your model and add it as a third model level. Is the harness effect
    the same sign for your model as for the recorded ones? What would it take to claim that it generalises?
Q5. State the hypothesis a paper is testing when it says 'harness B beats harness A', in the form H0: E[Y|M,B] = E[Y|M,A],
    and list the nuisance variables it must hold fixed or block on. Which of them does a leaderboard hold fixed?""")
