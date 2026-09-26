"""Exercise 4 as a service: calibrate a judge (offline mock, or any OpenRouter model) against the oracle.

Returns everything the script prints, plus one thing it only hints at: the verbosity-padding test — the same
patch judged again with a large harmless comment block appended. That check needs no oracle at all."""
from __future__ import annotations
import os, random, time
from typing import Optional

from harnesslab.core.analysis import load_index, cohen_kappa
from harnesslab.core.grader import load_task
from harnesslab.core.judge import MockJudge, completer_from_provider, calibrate_single, calibrate_pairwise, judge_process, judge_single
from harnesslab.core.providers import make_provider
from harnesslab.core.trajtest import load_runs

LAB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAD = "\n".join(["+# " + ("-" * 70)] + [f"+# NOTE {i}: this comment intentionally documents nothing; it only makes the patch longer." for i in range(28)] + ["+# " + ("-" * 70)])


def run(results_dir: str, judge: str = "mock", model: str = "mock", n: int = 24, repeats: int = 2, seed: int = 0,
        api_key: Optional[str] = None, base_url: Optional[str] = None, progress=None) -> dict:
    rows = [r for r in load_index(results_dir) if (r.get("patch_bytes") or 0) > 0 and r.get("hidden_pass") is not None]
    rng = random.Random(seed)
    rng.shuffle(rows)
    tasks = {}
    def issue_for(tid):
        if tid not in tasks:
            p = os.path.join(LAB_ROOT, "tasks", tid)
            tasks[tid] = load_task(p)["issue"] if os.path.isdir(p) else f"(issue text not stored for {tid})"
        return tasks[tid]
    def read_patch(r):
        with open(os.path.join(results_dir, r["run_id"], "patch.diff")) as f:
            return f.read()
    passing = [r for r in rows if r["hidden_pass"]][: n // 2]
    failing = [r for r in rows if not r["hidden_pass"]][: n - len(passing)]
    sample = passing + failing
    rng.shuffle(sample)
    items = [{"id": r["run_id"], "issue": issue_for(r["task_id"]), "patch": read_patch(r), "gold": bool(r["hidden_pass"]), "task": r["task_id"]} for r in sample]
    if not items:
        raise ValueError("no graded runs with a non-empty patch in this directory")
    if judge == "mock":
        oracle = {MockJudge.key(it["patch"].strip("\n")): it["gold"] for it in items}
        oracle.update({MockJudge.key(it["patch"]): it["gold"] for it in items})
        complete = MockJudge(oracle, seed=seed, accuracy=0.85, position_bias=0.15, verbosity_bias=0.10, retest_noise=0.08)
    else:
        prov = make_provider("openai", model, temperature=0.0, api_key=api_key, base_url=base_url)
        complete = completer_from_provider(prov)
    t0 = time.time()
    if progress: progress("single-patch verdicts")
    cal = calibrate_single(complete, items, repeats=repeats)
    gold = [it["gold"] for it in items]
    always_yes_kappa = cohen_kappa(gold, [True] * len(gold)) if len(set(gold)) > 1 else None
    skew = [r for r in rows if r["hidden_pass"]][:36] + [r for r in rows if not r["hidden_pass"]][:4]
    g2 = [bool(r["hidden_pass"]) for r in skew]
    base_rate = {"agreement_balanced": sum(gold) / len(gold), "kappa_balanced": always_yes_kappa,
                 "agreement_skewed": (sum(g2) / len(g2)) if g2 else None, "kappa_skewed": cohen_kappa(g2, [True] * len(g2)) if len(set(g2)) > 1 else None}
    # ---- pairwise with swap
    if progress: progress("pairwise with position swap")
    pairs, by_task = [], {}
    for it in items:
        by_task.setdefault(it["task"], []).append(it)
    for tid, its in by_task.items():
        good = [i for i in its if i["gold"]]; bad = [i for i in its if not i["gold"]]
        for a, b in zip(good, bad):
            if rng.random() < 0.5:
                pairs.append({"id": f"{a['id']}|{b['id']}", "issue": a["issue"], "patch_a": a["patch"], "patch_b": b["patch"], "gold": "A"})
            else:
                pairs.append({"id": f"{b['id']}|{a['id']}", "issue": a["issue"], "patch_a": b["patch"], "patch_b": a["patch"], "gold": "B"})
    pc = calibrate_pairwise(complete, pairs[:20]) if pairs else None
    if pc:
        pc.pop("results", None)
    # ---- verbosity padding: no oracle needed
    if progress: progress("verbosity padding")
    pad = []
    for it in items[:6]:
        before = judge_single(complete, it["issue"], it["patch"])["verdict_bool"]
        after = judge_single(complete, it["issue"], it["patch"] + "\n" + PAD)["verdict_bool"]
        pad.append({"id": it["id"], "gold": it["gold"], "before": before, "after": after, "len_before": len(it["patch"]), "len_after": len(it["patch"]) + len(PAD)})
    # ---- process judging vs the ledger
    if progress: progress("process judging")
    traj = {t.run_id: t for t in load_runs(results_dir)}
    proc = []
    for it in items[:6]:
        t = traj.get(it["id"])
        if not t:
            continue
        text = "\n".join(f"{i+1}. {s['gen_ai.tool.name']}({', '.join(f'{k}={str(v)[:40]!r}' for k, v in (s.get('args') or {}).items())}) -> {s['status']}" for i, s in enumerate(t.tool_calls))
        v = judge_process(complete, it["issue"], text, it["patch"])
        ledger = {"verified": t.ran_tests_after_last_edit(), "in_scope": not t.boundary_events and not t.summary["tests_modified"], "correct": it["gold"]}
        proc.append({"id": it["id"], "task": it["task"], "judge": {k: v.get(k) for k in ("inspected", "verified", "in_scope", "correct")}, "ledger": ledger,
                     "disagree": [k for k in ("verified", "in_scope", "correct") if v.get(k) is not None and bool(v.get(k)) != bool(ledger[k])]})
    return {"judge": judge, "model": model, "n": len(items), "repeats": repeats, "results": os.path.basename(results_dir),
            "single": {k: v for k, v in cal.items() if k != "per_item"}, "per_item": cal["per_item"], "base_rate_trap": base_rate,
            "pairwise": pc, "padding": pad, "process": proc,
            "spend": {"tokens": getattr(complete, "tokens", 0), "cost_usd": getattr(complete, "cost", 0.0), "seconds": round(time.time() - t0, 1)}}
