"""Exercise 4: calibrating an LLM judge instead of trusting it.

We have an oracle for these tasks (hidden tests). That lets us measure a judge
the way you would have to measure it *before* using it on tasks with no oracle:
agreement, chance-corrected agreement (kappa), test-retest consistency,
position bias under swapping, and a verbosity check.

    python exercises/ex4_judge.py                              # offline mock judge
    python exercises/ex4_judge.py --judge anthropic --model claude-haiku-4-5 --n 24
    python exercises/ex4_judge.py --judge openai --model gpt-5-mini --n 24
"""
import json, os, time
from _common import parse, hr, LAB_ROOT
import os, random
from harnesslab.core.analysis import load_index, filter_rows, cohen_kappa
from harnesslab.core.grader import load_task
from harnesslab.core.judge import (MockJudge, completer_from_provider, calibrate_single, calibrate_pairwise, judge_process)
from harnesslab.core.providers import make_provider
from harnesslab.core.trajtest import load_runs


def extra(ap):
    ap.add_argument("--judge", default="mock", choices=["mock", "openai", "anthropic", "openrouter"])
    ap.add_argument("--model", default="mock")
    ap.add_argument("--n", type=int, default=40, help="number of patches to judge")
    ap.add_argument("--repeats", type=int, default=2, help="repeated judgments per patch (test-retest)")
    ap.add_argument("--seed", type=int, default=0)


args = parse(__doc__, extra)
# `> 0` raises on a run whose patch size was never measured (importers/common.py UNMEASURED_FIELDS).
rows = [r for r in load_index(args.results) if (r.get("patch_bytes") or 0) > 0]
rng = random.Random(args.seed)
rng.shuffle(rows)
tasks = {tid: load_task(os.path.join(LAB_ROOT, "tasks", tid)) for tid in {r["task_id"] for r in rows}}


def read_patch(r):
    with open(os.path.join(args.results, r["run_id"], "patch.diff")) as f:
        return f.read()


# balance the sample: half passing, half failing where possible
passing = [r for r in rows if r["hidden_pass"]][: args.n // 2]
failing = [r for r in rows if not r["hidden_pass"]][: args.n - len(passing)]
sample = passing + failing
rng.shuffle(sample)
items = [{"id": r["run_id"], "issue": tasks[r["task_id"]]["issue"], "patch": read_patch(r), "gold": bool(r["hidden_pass"]), "task": r["task_id"]} for r in sample]

if args.judge == "mock":
    oracle = {MockJudge.key(it["patch"].strip("\n")): it["gold"] for it in items}
    oracle.update({MockJudge.key(it["patch"]): it["gold"] for it in items})
    complete = MockJudge(oracle, seed=args.seed, accuracy=0.85, position_bias=0.15, verbosity_bias=0.10, retest_noise=0.08)
else:
    complete = completer_from_provider(make_provider(args.judge, args.model, temperature=0.0))

# ---------------------------------------------------------------- 1. single-patch verdicts vs the oracle
hr(f"1. Single-patch judging: {len(items)} patches ({sum(i['gold'] for i in items)} pass hidden tests), judge={args.judge}:{args.model}, repeats={args.repeats}")
cal = calibrate_single(complete, items, repeats=args.repeats)
print(f"raw agreement with oracle   = {cal['agreement']:.3f}")
print(f"Cohen's kappa (chance-corr.) = {cal['kappa']:.3f}")
print(f"test-retest consistency      = {cal['test_retest']:.3f}   (same verdict on all {args.repeats} repeats)")
print(f"confusion (oracle x judge)   = {cal['confusion']}")
print(f"mean patch length judged correct / incorrect = {cal['mean_len_judged_correct']:.0f} / {cal['mean_len_judged_incorrect']:.0f} chars")
print("\nZheng et al. 2023 reported 'over 80% agreement'; Norman et al. 2026 show the same judges sit at kappa 0.38-0.51")
print("once chance agreement is removed. Compare the first two lines above.")

# ---------------------------------------------------------------- 2. why agreement overstates: the base-rate trap
hr("2. The base-rate trap: a judge that always says 'correct'")
gold = [it["gold"] for it in items]
always_yes = [True] * len(gold)
print(f"always-'correct' judge: agreement={sum(g == a for g, a in zip(gold, always_yes))/len(gold):.3f}  kappa={cohen_kappa(gold, always_yes):.3f}")
skew = [it for it in rows if it['hidden_pass']][:36] + [it for it in rows if not it['hidden_pass']][:4]
g2 = [bool(r["hidden_pass"]) for r in skew]
print(f"on a 90%-passing sample the same lazy judge gets agreement={sum(g2)/len(g2):.3f}, kappa={cohen_kappa(g2, [True]*len(g2)):.3f}")

# ---------------------------------------------------------------- 3. pairwise with position swap
hr("3. Pairwise judging with position swap")
pairs = []
by_task = {}
for it in items:
    by_task.setdefault(it["task"], []).append(it)
for tid, its in by_task.items():
    good = [i for i in its if i["gold"]]; bad = [i for i in its if not i["gold"]]
    for a, b in zip(good, bad):
        if rng.random() < 0.5:
            pairs.append({"id": f"{a['id']}|{b['id']}", "issue": a["issue"], "patch_a": a["patch"], "patch_b": b["patch"], "gold": "A"})
        else:
            pairs.append({"id": f"{b['id']}|{a['id']}", "issue": a["issue"], "patch_a": b["patch"], "patch_b": a["patch"], "gold": "B"})
if pairs:
    pc = calibrate_pairwise(complete, pairs[:20])
    print(f"pairs={pc['n']}  P(pick A) first={pc['p_pick_A_first']:.2f}  after swap={pc['p_pick_A_after_swap']:.2f}  (0.50 = no position bias)")
    print(f"position-consistent verdicts = {pc['position_consistent']:.2f}")
    print(f"accuracy vs oracle: first-order only = {pc['accuracy_first_only']:.2f}   consistent-only = {pc['accuracy_position_consistent']:.2f}")
    print("A verdict that flips when you swap A and B is not a judgment about the patches.")
else:
    print("(not enough passing/failing pairs per task in this sample)")

# ---------------------------------------------------------------- 4. process judging
hr("4. Judging the process (trajectory), not only the patch")
traj = {t.run_id: t for t in load_runs(args.results)}
shown = 0
for it in items[:6]:
    t = traj[it["id"]]
    text = "\n".join(f"{i+1}. {s['gen_ai.tool.name']}({', '.join(f'{k}={str(v)[:40]!r}' for k, v in s['args'].items())}) -> {s['status']}"
                     for i, s in enumerate(t.tool_calls))
    v = judge_process(complete, it["issue"], text, it["patch"])
    ledger_says = {"verified": t.ran_tests_after_last_edit(), "in_scope": not t.boundary_events and not t.summary["tests_modified"], "correct": it["gold"]}
    print(f"{it['id']}  judge={ {k: v.get(k) for k in ('inspected','verified','in_scope','correct')} }  ledger={ledger_says}")
    shown += 1
print("Where the judge and the ledger disagree on 'verified' or 'in_scope', which one do you believe, and why?")
print(f"\njudge spend: ~{getattr(complete, 'tokens', 0):,} tokens, ~${getattr(complete, 'cost', 0.0):.3f}")

# ---------------------------------------------------------------- save for the console (Integrity view, "Judge calibration")
save_dir = os.path.join(LAB_ROOT, "data", "judge")
os.makedirs(save_dir, exist_ok=True)
save_path = os.path.join(save_dir, f"{args.judge}_{(args.model or 'mock').replace('/', '_')}.json")
with open(save_path, "w") as f:
    json.dump({"judge": args.judge, "model": args.model or "mock", "results": args.results, "n": len(items), "repeats": args.repeats,
               "agreement": cal["agreement"], "kappa": cal["kappa"], "test_retest": cal["test_retest"], "confusion": cal["confusion"],
               "mean_len_judged_correct": cal["mean_len_judged_correct"], "mean_len_judged_incorrect": cal["mean_len_judged_incorrect"],
               "pairwise": (pc if pairs else None), "generated_at": time.strftime("%Y-%m-%d %H:%M")}, f, indent=1)
print(f"saved calibration -> {save_path}")

hr("Questions")
print("""Q1. Fill in the sentence: "Judge X agreed with the tests p% of the time" hides ___; kappa of k means ___.
Q2. Was the judge more lenient on longer patches? Would you notice this without an oracle? Design a
    check that needs no oracle at all (hint: pad a patch with a harmless comment block).
Q3. Test-retest was high. Does that make the judge trustworthy? (Norman et al. call the combination of
    high consistency and severe bias the consistency-bias paradox.)
Q4. Write the minimum calibration protocol you would require in a paper that uses an LLM judge to grade
    patches on tasks with no tests. Which numbers, over how many items, with what oracle?
Q5. (live keys) Run with a real judge (--judge anthropic/openai, --n 24). Compare kappa with the mock.
    Then re-run with --repeats 3. What changed?""")
