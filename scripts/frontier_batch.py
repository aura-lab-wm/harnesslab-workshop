"""Run a models x harnesses x tasks x repeats design against frontier models through OpenRouter, under a budget.

    export OPENROUTER_API_KEY=...
    python scripts/frontier_batch.py --estimate                       # what the default design would cost, no calls
    python scripts/frontier_batch.py --budget 60                      # run it, stop when the budget is reached
    python scripts/frontier_batch.py --models anthropic/claude-opus-5 --harnesses baseline,no_test_tool --repeats 2 --budget 20

Each model writes into data/runs/frontier_<model slug>/ so the console shows one results directory per model and the
Experiment view gets one column per harness and one line per model. The running total uses the cost OpenRouter
reports per call (exact), falling back to the price table. Runs are sequential; a 4 x 3 x 8 x 3 design takes about
an hour at 10-20 s per model call.
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, zlib
from dataclasses import asdict

LAB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB_ROOT)
from harnesslab.core.grader import load_task
from harnesslab.core.harness import HarnessConfig, run_task
from harnesslab.core.providers import make_provider, price_for
from harnesslab.core.runner import all_task_ids

DEFAULT_MODELS = "anthropic/claude-sonnet-5,openai/gpt-5.6-sol,deepseek/deepseek-v4-pro,z-ai/glm-5.3"
DEFAULT_HARNESSES = "baseline,no_test_tool,terse_prompt"
ASSUMED_IN, ASSUMED_OUT = 40_000, 3_000     # tokens per run, planning assumption for --estimate (8 calls, transcript re-sent)


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=DEFAULT_MODELS)
    ap.add_argument("--harnesses", default=DEFAULT_HARNESSES, help="harness ids under harnesses/")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--budget", type=float, default=50.0, help="USD; the batch stops when the running total passes it")
    ap.add_argument("--provider", default="openrouter", choices=["openrouter", "anthropic", "openai"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--estimate", action="store_true", help="print the planning estimate and exit")
    ap.add_argument("--out-prefix", default="data/runs/frontier_")
    a = ap.parse_args(argv)

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    harnesses = [HarnessConfig.load(os.path.join(LAB_ROOT, "harnesses", f"{h.strip()}.json")) for h in a.harnesses.split(",")]
    tasks = all_task_ids() if a.tasks == "all" else [t.strip() for t in a.tasks.split(",")]
    n_runs = len(models) * len(harnesses) * len(tasks) * a.repeats

    print(f"design: {len(models)} models x {len(harnesses)} harnesses x {len(tasks)} tasks x {a.repeats} repeats = {n_runs} runs")
    est_total = 0.0
    for m in models:
        pi, po = price_for(m)
        per_run = ASSUMED_IN / 1e6 * pi + ASSUMED_OUT / 1e6 * po
        runs_m = len(harnesses) * len(tasks) * a.repeats
        est_total += per_run * runs_m
        print(f"  {m:36s} ${pi:5.2f}/{po:5.2f} per M  ~${per_run:.3f}/run  x {runs_m:3d} = ~${per_run * runs_m:6.2f}")
    print(f"planning estimate ~${est_total:.2f} (assumes {ASSUMED_IN // 1000}k input + {ASSUMED_OUT // 1000}k output tokens per run; reasoning-heavy models can double it)")
    if a.estimate:
        return

    spent, done, t0 = 0.0, 0, time.time()
    for m in models:
        out = os.path.join(LAB_ROOT, a.out_prefix + slug(m))
        os.makedirs(out, exist_ok=True)
        index_path = os.path.join(out, "index.jsonl")
        provider = make_provider(a.provider, m, temperature=0.0)
        for harness in harnesses:
            for tid in tasks:
                task = load_task(os.path.join(LAB_ROOT, "tasks", tid))
                for r in range(a.repeats):
                    if spent >= a.budget:
                        print(f"budget reached (${spent:.2f} >= ${a.budget:.2f}); stopping after {done} runs")
                        return
                    seed = a.seed * 1_000_000 + zlib.crc32(f"{tid}|{harness.id}|{m}".encode()) % 10_000 * 100 + r
                    try:
                        s = run_task(task, harness, provider, out, repeat_index=r, seed=seed)
                    except Exception as e:  # keep the batch alive on a single failed call
                        print(f"  !! {m} {harness.id} {tid} r{r}: {type(e).__name__}: {str(e)[:160]}")
                        time.sleep(5)
                        continue
                    with open(index_path, "a") as f:
                        f.write(json.dumps(asdict(s)) + "\n")
                    spent += s.cost_usd; done += 1
                    flag = "PASS" if s.hidden_pass else "fail"
                    print(f"[{done:3d}/{n_runs}] {m:32s} {harness.id:13s} {tid:22s} r{r} {flag:4s} steps={s.steps:2d} "
                          f"tok={s.input_tokens + s.output_tokens:6d} ${s.cost_usd:.4f}  total ${spent:.2f}  exit={s.exit_reason}", flush=True)
    print(f"done: {done} runs, ${spent:.2f}, {(time.time() - t0) / 60:.0f} min")


if __name__ == "__main__":
    main()
