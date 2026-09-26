"""Exercise 7: 500 real SWE-agent trajectories.

This exercise analyses real runs from
nebius/SWE-agent-trajectories (80,036 SWE-agent runs, CC-BY-4.0, each with a
resolved/unresolved label) and reproduces the dataset card's resolved-vs-
unresolved comparison on your own sample, then ranks trajectory features by
how strongly they associate with failure (Ochiai, i.e. spectrum-based fault
localisation applied to runs instead of statements).

    pip install datasets
    python exercises/ex7_real_trajectories.py --n 500            # needs network, ~1-3 minutes
    python exercises/ex7_real_trajectories.py --offline          # the 500 real trajectories bundled in data/runs/real_swe_agent_500
    python exercises/ex7_real_trajectories.py --n 300 --model swe-agent-llama-70b
"""
from _common import parse, try_matplotlib, hr
import json, os, statistics
from collections import Counter
from harnesslab.core.real_traj import load_nebius, features, ledger_rows_as_features, parse_trajectory
from harnesslab.core.analysis import ochiai_attribution, bootstrap_ci


def extra(ap):
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--model", default=None, help="restrict to one model_name in the dataset")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--offline", action="store_true", help="use the 500 real trajectories bundled in data/runs/real_swe_agent_500 instead of downloading")
    ap.add_argument("--cache", default=None, help="JSONL cache of features (written on first run, reused later)")
    ap.add_argument("--to-console", default=None, metavar="DIR",
                    help="also write the streamed trajectories as a results directory for the console, e.g. data/runs/nebius_500")


args = parse(__doc__, extra)

# ---------------------------------------------------------------- load
BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "runs", "real_swe_agent_500", "real_features.jsonl")
if args.offline and os.path.exists(BUNDLED):
    rows = [json.loads(l) for l in open(BUNDLED)]
    source = f"nebius/SWE-agent-trajectories, the 500 bundled in {os.path.relpath(BUNDLED)}"
    raw = None
elif args.offline:
    rows = ledger_rows_as_features(args.results)
    source = f"ledgers in {args.results}"
    raw = None
else:
    cache = args.cache or os.path.join(args.out, f"nebius_features_n{args.n}_s{args.seed}.jsonl")
    if os.path.exists(cache):
        rows = [json.loads(l) for l in open(cache)]
        source = f"cache {cache}"
        raw = None
    else:
        print(f"streaming {args.n} trajectories from nebius/SWE-agent-trajectories ...")
        raw = load_nebius(args.n, seed=args.seed, model=args.model)
        rows = [features(r) for r in raw]
        if args.to_console:
            from harnesslab.core.real_traj import to_results_dir
            n_written = to_results_dir(raw, args.to_console)
            print(f"wrote {n_written} real trajectories as a results directory: {args.to_console} (open it in the console)")
        with open(cache, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        source = f"nebius/SWE-agent-trajectories (n={len(rows)}, cached to {cache})"

ok = [r for r in rows if r["resolved"]]
ko = [r for r in rows if not r["resolved"]]
hr(f"1. Resolved vs unresolved  ({source})")
if not rows:
    # Every run in this directory records what the agent asked for and not what ran, so none of them
    # can be expressed in the feature schema (core/real_traj.py). Nothing here to attribute.
    print(f"runs=0 -- no run in {args.results} records tool execution, which every feature below is about")
    raise SystemExit(0)
print(f"runs={len(rows)}  resolved={len(ok)} ({len(ok)/len(rows):.1%})  unresolved={len(ko)}")


def m(rs, k):
    return statistics.mean(r[k] for r in rs) if rs else float("nan")


def rate(rs, k):
    return sum(1 for r in rs if r[k]) / len(rs) if rs else float("nan")


print(f"{'metric':32s} {'resolved':>10s} {'unresolved':>12s}   dataset card (80,036 runs)")
for label, key, card in [("steps", "steps", "31.3 vs 58.4"), ("files edited", "files_edited", "1.33 vs 2.17"),
                         ("lines edited", "lines_edited", "20.7 vs 61.0"), ("repeated commands", "repeated_commands", "n/a"),
                         ("view actions", "n_view", "n/a"), ("search actions", "n_search", "n/a"),
                         ("edit actions", "n_edit", "n/a"), ("run actions", "n_run", "n/a")]:
    print(f"{label:32s} {m(ok, key):10.1f} {m(ko, key):12.1f}   {card}")
for label, key, card in [("submitted", "submitted", "94.6% vs 57.6%"), ("exit: context exhausted", "exit_context", "5.31% vs 30.4%"),
                         ("ran something after last edit", "ran_after_last_edit", "n/a"), ("edited a test file", "edited_tests" if False else "test_files_edited", "n/a")]:
    if key == "test_files_edited":
        print(f"{label:32s} {sum(1 for r in ok if r[key])/max(1,len(ok)):10.1%} {sum(1 for r in ko if r[key])/max(1,len(ko)):12.1%}   {card}")
    else:
        print(f"{label:32s} {rate(ok, key):10.1%} {rate(ko, key):12.1%}   {card}")
lo, hi = bootstrap_ci([r["steps"] for r in ko], B=1000)
print(f"\n95% bootstrap CI of mean steps in unresolved runs: [{lo:.1f}, {hi:.1f}]  (n={len(ko)})")
print("Failure costs about twice the steps, twice the context, three times the edited lines, and six times")
print("the rate of running out of context (dataset card). Check whether your sample agrees before quoting it.")

# ---------------------------------------------------------------- 2. exit statuses and action mix
hr("2. Exit status and action mix")
for name, rs in (("resolved", ok), ("unresolved", ko)):
    c = Counter(r["exit_status"] for r in rs)
    print(f"{name:11s} exit: " + ", ".join(f"{k}={v/len(rs):.2f}" for k, v in c.most_common(5)))
for name, rs in (("resolved", ok), ("unresolved", ko)):
    tot = sum(r["steps"] for r in rs) or 1
    print(f"{name:11s} action mix: " + ", ".join(f"{k}={sum(r['n_'+k] for r in rs)/tot:.2f}" for k in ("view", "search", "edit", "run", "submit", "other")))
print("Bouzenia & Pradel (ASE 2025) on 120 trajectories: generate-fix 23%, run-tests 19%, search 15%, explore 14%.")

# ---------------------------------------------------------------- 3. spectrum-based attribution
hr("3. Which trajectory features associate with failure? (Ochiai over runs)")
att = ochiai_attribution(rows)
print(f"{'feature':28s} {'ochiai':>7s} {'in failures':>12s} {'in successes':>13s}")
for a in att:
    print(f"{a['feature']:28s} {a['ochiai']:7.3f} {a['rate_in_failures']:12.2f} {a['rate_in_successes']:13.2f}")
print("\nRead the two rate columns, not only the score: a feature that is common in failures AND successes")
print("(high Ochiai from base rate alone) is not diagnostic. Correlation, not cause: an agent that runs out of")
print("context did not fail *because* of the exit status; both are symptoms of the same run going wrong.")

# ---------------------------------------------------------------- 4. one real trajectory
if raw:
    hr("4. One real unresolved trajectory, as actions")
    r = next((x for x in raw if not x["target"]), raw[0])
    steps = parse_trajectory(r["trajectory"])
    print(f"{r['instance_id']}  model={r['model_name']}  exit={r['exit_status']}  steps={len(steps)}")
    for i, s in enumerate(steps[:25]):
        print(f"  {i+1:3d}. [{s['action']:6s}] {s['command'][:90]!r}")
    if len(steps) > 25:
        print(f"  ... {len(steps)-25} more steps")

plt = try_matplotlib()
if plt and rows:
    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.hist([r["steps"] for r in ok], bins=30, alpha=0.7, label=f"resolved (n={len(ok)})")
    ax.hist([r["steps"] for r in ko], bins=30, alpha=0.7, label=f"unresolved (n={len(ko)})")
    ax.set_xlabel("steps"); ax.set_ylabel("runs"); ax.legend(frameon=False); ax.set_title("Trajectory length by outcome")
    p = os.path.join(args.out, "ex7_steps_by_outcome.png"); fig.tight_layout(); fig.savefig(p, dpi=150)
    print(f"\nfigure -> {p}")

hr("Questions")
print("""Q1. Which three features rank highest? For each, decide whether it is a cause of failure, a symptom, or
    a harness artefact (e.g. a step cap). What experiment would separate cause from symptom?
Q2. The dataset card says 83% of resolved runs opened a correct file vs 40% of unresolved; Majgaonkar et al.
    find localisation correct in 72-81% of trajectories even in failures for OpenHands/SWE-agent/Prometheus.
    Both can be true. What differs between the two settings, and what does that say about 'the' bottleneck?
Q3. Failed runs are longer. A step cap would shorten them and change pass rate by exactly zero for runs that
    were going to fail anyway. Where in your report card does a step cap show up, and where does it hide?
Q4. Restrict to one model (--model). Do the feature rankings change? Which features are model-specific and
    which are harness-specific? (All runs here use the same SWE-agent harness.)
Q5. Stretch: these trajectories were generated to train agents. Pick a feature that predicts failure and ask
    whether filtering training trajectories on it would teach the model to avoid the failure or to avoid the
    symptom.""")
