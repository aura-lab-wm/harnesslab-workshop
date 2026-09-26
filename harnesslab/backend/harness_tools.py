"""The harness as a first-class object: hash it, diff it, ablate it, version it, and ask
whether the model ranking survives it.

Everything the platform already claims about harnesses ("change one knob and the numbers
move") is only credible if a harness has an identity you can point at. This module gives it
one — `harnesslab.core.harness.content_hash` — and then builds four things on top:

  GET  /api/harness/diff        a vs b, field by field, plus every metric the platform
                                computes per cell with a task-paired bootstrap difference
  POST /api/harness/ablate      one-factor-at-a-time variants of a base harness, optionally
                                saved to harnesses/, plus a ready-to-POST /api/jobs body
  GET  /api/harness/ablate/plan dry run of the above
  GET  /api/harness/ranking     do the models rank the same way under every harness?
                                (Kendall tau between harness rankings, with a task bootstrap)
  GET  /api/harness/versions    which (harness_id, hash) pairs actually produced the runs in a
                                results dir, and whether harnesses/<id>.json still matches

Design notes
  * No new dependencies: fastapi + stdlib, reusing harnesslab.core.analysis for every estimator.
  * Nothing here mutates a results directory. `save=true` on /ablate writes new harness JSON
    files and never overwrites an existing one.
  * Every difference is reported with a 95% CI and an explicit "moved / did not move" flag.
    When the two sides do not share a task set the comparison falls back to unpaired CIs and
    says so in `pairing_note` — a wider, weaker claim, made visibly.
"""
from __future__ import annotations

import json
import math
import os
import random
from collections import Counter, defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from harnesslab.core.analysis import (aggregate, bootstrap_ci, filter_rows, flip_rate, load_index,
                               paired_bootstrap, summarize)
from harnesslab.core.harness import HarnessConfig, content_hash

from .results_scope import list_results_dirs

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_ROOT = os.path.join(LAB, "data", "runs")
HARNESS_DIR = os.path.join(LAB, "harnesses")
TASK_DIR = os.path.join(LAB, "tasks")

router = APIRouter(prefix="/api/harness")


# --------------------------------------------------------------------------- small helpers
def _clean(x):
    """NaN / inf are not JSON. Replace them with null so the UI can render an em dash."""
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _dir(name: str) -> str:
    d = os.path.join(RUNS_ROOT, name)
    if not os.path.exists(os.path.join(d, "index.jsonl")):
        raise HTTPException(404, f"no results dir {name}")
    return d


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def _safe_id(hid: str) -> str:
    if not hid or "/" in hid or "\\" in hid or hid.startswith(".") or "\x00" in hid:
        raise HTTPException(400, f"bad harness id {hid!r}")
    return hid


# --------------------------------------------------------------------------- knob glossary
#: What each HarnessConfig field actually does to the agent. Shown next to every diff row so a
#: reader does not have to have read harness.py to understand why a number moved.
KNOBS = {
    "system_prompt": "The standing instruction. Whether it tells the agent to verify, to leave tests alone, or nothing at all.",
    "tools": "The tool surface. A capability the agent does not have it cannot use — and cannot be blamed for not using.",
    "policy": "strict blocks flagged writes and commands; permissive records them and lets some through. Moves the boundary-event rate, and sometimes pass@1.",
    "max_steps": "Model-call cap. The stopping rule: runs that would have solved it on step 21 are recorded as failures.",
    "max_total_tokens": "Token budget across the whole run. Exhausting it exits with budget_exceeded, which grades as a failure.",
    "context_window": "0 = full history; N = only the last N tool observations are kept verbatim, the rest are elided. Cheap, but the agent forgets what it read.",
    "observation_chars": "Truncation limit on every tool result. Too small and the agent never sees the traceback that would have told it what broke.",
    "temperature": "Sampling temperature. Drives the flip rate (same task, different outcome) more than it drives pass@1.",
    "max_tokens_per_call": "Output cap per model call. Too small truncates long patches mid-edit.",
    "include_file_listing": "Whether the repository listing is pasted into the first user message. Free orientation, or an extra 1k tokens of noise.",
    "sentinel": "The early-warning hook: part of the harness, not an observer. If it nudges or blocks, it changed the trajectory.",
    "id": "Name only — excluded from the content hash.",
    "notes": "Human annotation — excluded from the content hash.",
}

#: Fields excluded from the hash, kept out of the "what changed" verdict for the same reason.
COSMETIC = ("id", "notes")


# --------------------------------------------------------------------------- harness resolution
def _harness_files() -> dict:
    out = {}
    if not os.path.isdir(HARNESS_DIR):
        return out
    for fn in sorted(os.listdir(HARNESS_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(HARNESS_DIR, fn)) as f:
                d = json.load(f)
            out[d.get("id") or fn[:-5]] = {"file": fn, "config": _normalise(d)}
        except Exception:
            continue
    return out


def _normalise(d: dict) -> dict:
    """A raw harness dict widened to the full HarnessConfig field set (defaults for anything absent)."""
    from dataclasses import asdict, fields as dc_fields
    known = {f.name for f in dc_fields(HarnessConfig)}
    return asdict(HarnessConfig(**{k: v for k, v in d.items() if k in known}))


def _first_span(results_dir: str, run_id: str) -> Optional[dict]:
    """The invoke_agent start span (which carries the whole harness config) without reading the ledger."""
    p = os.path.join(results_dir, run_id, "ledger.jsonl")
    try:
        with open(p, encoding="utf-8") as f:
            line = f.readline()
        return json.loads(line) if line.strip() else None
    except Exception:
        return None


_LEDGER_CACHE: dict = {}


def _recorded_harnesses(results_dir: str, limit_per_id: int = 2000) -> dict:
    """{harness_id: {hash: {"config":…, "runs":[run_id…]}}} read from the ledgers of a results dir.

    Cached on (dir, index mtime) because it touches one line of every ledger file.
    """
    idx = os.path.join(results_dir, "index.jsonl")
    key = (results_dir, os.path.getmtime(idx))
    if key in _LEDGER_CACHE:
        return _LEDGER_CACHE[key]
    out: dict = defaultdict(lambda: defaultdict(lambda: {"config": None, "runs": [], "seeds": {}}))
    seen = Counter()
    for r in load_index(results_dir):
        hid = r["harness_id"]
        if seen[hid] >= limit_per_id:
            continue
        seen[hid] += 1
        h, cfg, seed = r.get("harness_hash") or "", None, None
        span = _first_span(results_dir, r["run_id"])
        if span:
            cfg = span.get("harness")
            seed = span.get("seed")
            if not h:
                h = span.get("harness_hash") or (content_hash(cfg) if isinstance(cfg, dict) else "")
        if not h:
            h = "unknown"
        slot = out[hid][h]
        if slot["config"] is None and isinstance(cfg, dict):
            slot["config"] = _normalise(cfg)
        slot["runs"].append(r["run_id"])
        slot["seeds"][(r["task_id"], r.get("repeat_index", 0))] = seed
    res = {k: dict(v) for k, v in out.items()}
    _LEDGER_CACHE.clear()
    _LEDGER_CACHE[key] = res
    return res


def resolve_harness(hid: str, results_dir: Optional[str] = None) -> dict:
    """The config for a harness id: the file on disk if there is one, else the config recorded in
    the ledgers of `results_dir` (imported harnesses such as `swe-agent`, or `<id>+sentinel`
    twins synthesised at job time, exist only there)."""
    _safe_id(hid)
    files = _harness_files()
    if hid in files:
        cfg = files[hid]["config"]
        return {"id": hid, "config": cfg, "hash": content_hash(cfg), "source": "file", "file": files[hid]["file"]}
    if results_dir:
        # callers pass either a results-dir NAME ("demo_mock") or an absolute path; accept both
        rd = results_dir if os.path.isdir(results_dir) else _dir(results_dir)
        rec = _recorded_harnesses(rd).get(hid) or {}
        # the variant with the most runs is the representative one
        best = max(rec.items(), key=lambda kv: len(kv[1]["runs"]), default=None)
        if best and best[1]["config"]:
            return {"id": hid, "config": best[1]["config"], "hash": best[0], "source": "ledger", "file": None}
    raise HTTPException(404, f"no harness {hid} (not in harnesses/ and not recorded in that results dir)")


# --------------------------------------------------------------------------- paired statistics
def _per_task(rows: list[dict], value) -> dict:
    """{task_id: mean of `value(row)` over that task's runs}."""
    acc = defaultdict(list)
    for r in rows:
        v = value(r)
        if v is not None:
            acc[r["task_id"]].append(float(v))
    return {t: sum(v) / len(v) for t, v in acc.items() if v}


def _flip_per_task(rows: list[dict], outcome: str = "hidden_pass") -> dict:
    """{task_id: 1.0 if the task's outcome is not constant across repeats}. Tasks with <2 runs are dropped."""
    by = defaultdict(set)
    n = Counter()
    for r in rows:
        by[r["task_id"]].add(bool(r.get(outcome)))
        n[r["task_id"]] += 1
    return {t: (1.0 if len(v) > 1 else 0.0) for t, v in by.items() if n[t] >= 2}


def _paired_diff(a_by_task: dict, b_by_task: dict, B: int = 5000, seed: int = 0) -> dict:
    """Task-paired percentile bootstrap of mean(b) - mean(a). Resamples tasks, the unit that was
    held fixed across the two harnesses (same design as harnesslab.core.analysis.paired_bootstrap,
    generalised from binary outcomes to any per-task number)."""
    tasks = sorted(set(a_by_task) & set(b_by_task))
    if not tasks:
        return {"n_tasks": 0, "mean_diff": None, "ci95": None, "moved": None}
    diffs = [b_by_task[t] - a_by_task[t] for t in tasks]
    lo, hi = bootstrap_ci(diffs, B=B, seed=seed)
    return {"n_tasks": len(tasks), "mean_diff": sum(diffs) / len(diffs), "ci95": [lo, hi],
            "moved": bool(lo > 0 or hi < 0), "paired": True,
            "per_task": {t: d for t, d in zip(tasks, diffs)}}


def _unpaired_diff(a_by_task: dict, b_by_task: dict, B: int = 5000, seed: int = 0) -> dict:
    """Difference of two independent task bootstraps. Used when the two sides did not run the
    same tasks: the interval is wider and the claim is weaker, on purpose."""
    A, Bv = list(a_by_task.values()), list(b_by_task.values())
    if not A or not Bv:
        return {"n_tasks": 0, "mean_diff": None, "ci95": None, "moved": None, "paired": False}
    rng = random.Random(seed)
    boots = sorted((sum(Bv[rng.randrange(len(Bv))] for _ in Bv) / len(Bv))
                   - (sum(A[rng.randrange(len(A))] for _ in A) / len(A)) for _ in range(B))
    lo, hi = boots[int(0.025 * B)], boots[int(0.975 * B) - 1]
    return {"n_tasks": min(len(A), len(Bv)), "mean_diff": (sum(Bv) / len(Bv)) - (sum(A) / len(A)),
            "ci95": [lo, hi], "moved": bool(lo > 0 or hi < 0), "paired": False}


def _boundary_any(r: dict):
    if "boundary_events" in r and r["boundary_events"] is None:
        return None                               # the run could not observe tool execution
    return 1.0 if (r.get("boundary_events") or 0) > 0 else 0.0


def _known(r: dict, key: str, fn):
    """None for a fact the run could not measure, so _per_task leaves it out instead of averaging a 0."""
    return None if key in r and r[key] is None else fn(r)


#: Every metric the platform computes per cell, as (key, label, extractor, higher_is_better, unit).
#: `extractor` maps a run row to a number; `None` means the metric is task-level (flip rate).
METRICS = [
    ("pass1_hidden", "pass@1 (hidden tests)", lambda r: 1.0 if r.get("hidden_pass") else 0.0, True, "rate"),
    ("pass1_visible", "pass@1 (visible tests)", lambda r: 1.0 if r.get("visible_pass") else 0.0, True, "rate"),
    ("pass1_strong", "pass@1 (strengthened tests)", lambda r: 1.0 if r.get("strong_pass") else 0.0, True, "rate"),
    ("verified", "ran tests before submit", lambda r: _known(r, "ran_tests_before_submit", lambda r: 1.0 if r.get("ran_tests_before_submit") else 0.0), True, "rate"),
    ("boundary_rate", "runs with >=1 boundary event", _boundary_any, False, "rate"),
    ("tests_modified", "runs that modified tests", lambda r: _known(r, "tests_modified", lambda r: 1.0 if r.get("tests_modified") else 0.0), False, "rate"),
    ("flip_rate", "flip rate (mixed outcome across repeats)", None, False, "rate"),
    ("cost_per_run", "cost per run", lambda r: _known(r, "cost_usd", lambda r: float(r.get("cost_usd") or 0.0)), False, "usd"),
    ("steps", "model calls per run", lambda r: float(r.get("steps") or 0), None, "count"),
    ("tokens", "tokens per run", lambda r: _known(r, "input_tokens", lambda r: float((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0))), False, "count"),
]


# --------------------------------------------------------------------------- 1. diff
@router.get("/diff")
def harness_diff(a: str, b: str, dir: Optional[str] = None, model: Optional[str] = None, B: int = 4000):
    """Field-by-field diff of two harnesses, plus — when a results dir is given — the a-vs-b
    difference in every per-cell metric with a 95% task-paired bootstrap CI."""
    _safe_id(a)
    _safe_id(b)
    ha = resolve_harness(a, dir)
    hb = resolve_harness(b, dir)

    fields_out = []
    for k in sorted(set(ha["config"]) | set(hb["config"])):
        va, vb = ha["config"].get(k), hb["config"].get(k)
        changed = json.dumps(va, sort_keys=True) != json.dumps(vb, sort_keys=True)
        if k == "tools":
            changed = sorted(va or []) != sorted(vb or [])
        fields_out.append({"field": k, "a": va, "b": vb, "changed": changed,
                           "cosmetic": k in COSMETIC, "knob": KNOBS.get(k, "")})
    changed_fields = [f["field"] for f in fields_out if f["changed"] and not f["cosmetic"]]

    out = {
        "a": {"id": a, "hash": ha["hash"], "source": ha["source"], "file": ha["file"], "config": ha["config"]},
        "b": {"id": b, "hash": hb["hash"], "source": hb["source"], "file": hb["file"], "config": hb["config"]},
        "same_hash": ha["hash"] == hb["hash"],
        "changed_fields": changed_fields,
        "fields": fields_out,
        "dir": dir, "model": model, "metrics": [], "runs": {"a": 0, "b": 0},
    }
    if not dir:
        out["note"] = "No results directory given: field diff only, no measured effect."
        return _clean(out)

    d = _dir(dir)
    rows = load_index(d)
    if model:
        rows = filter_rows(rows, model=model)
    A = filter_rows(rows, harness_id=a)
    Bq = filter_rows(rows, harness_id=b)
    out["runs"] = {"a": len(A), "b": len(Bq)}
    out["models"] = {"a": sorted({r["model"] for r in A}), "b": sorted({r["model"] for r in Bq})}
    if not A or not Bq:
        out["note"] = f"'{a}' has {len(A)} runs and '{b}' has {len(Bq)} runs in {dir}: nothing to compare."
        return _clean(out)

    ta, tb = {r["task_id"] for r in A}, {r["task_id"] for r in Bq}
    shared = sorted(ta & tb)
    paired = bool(shared) and ta == tb
    notes = []
    if ta != tb:
        notes.append(f"task sets differ ({len(ta)} vs {len(tb)} tasks, {len(shared)} shared)")
    if out["models"]["a"] != out["models"]["b"]:
        notes.append(f"model sets differ ({out['models']['a']} vs {out['models']['b']})")

    # seeds: recorded in the invoke_agent start span. Matching seeds per (task, repeat) means the
    # only thing that differs between the two sides is the harness.
    seeds_match = None
    try:
        rec = _recorded_harnesses(d)
        sa = {k: v for h in (rec.get(a) or {}).values() for k, v in h["seeds"].items()}
        sb = {k: v for h in (rec.get(b) or {}).values() for k, v in h["seeds"].items()}
        common = set(sa) & set(sb)
        if common and all(x is not None for x in list(sa.values())[:1] + list(sb.values())[:1]):
            seeds_match = all(sa[k] == sb[k] for k in common)
            if not seeds_match:
                notes.append("seeds differ per (task, repeat): the pair is not seed-matched, so some of the difference is sampling noise")
    except Exception:
        seeds_match = None
    out["seeds_match"] = seeds_match
    out["paired"] = paired
    out["shared_tasks"] = shared
    out["pairing_note"] = ("Task-paired bootstrap: both harnesses ran the same "
                           f"{len(shared)} tasks." if paired else
                           "NOT paired — " + "; ".join(notes) +
                           (". Falling back to unpaired CIs, which are wider and assume nothing was held fixed."
                            if not shared else
                            ". Pairing on the shared tasks only; read the interval, not the point."))

    sa_sum, sb_sum = summarize(A, label=a), summarize(Bq, label=b)
    out["summary"] = {"a": _clean(sa_sum), "b": _clean(sb_sum)}

    for key, label, extract, hib, unit in METRICS:
        if extract is None:
            pa, pb = _flip_per_task(A), _flip_per_task(Bq)
        else:
            pa, pb = _per_task(A, extract), _per_task(Bq, extract)
        if key == "pass1_hidden" and paired:
            # use the canonical estimator from harnesslab.core.analysis for the headline metric
            pbst = paired_bootstrap(A, Bq, "hidden_pass", B=B)
            stat = {"n_tasks": pbst.get("n_tasks", 0), "mean_diff": pbst.get("mean_diff"),
                    "ci95": list(pbst["ci95"]) if pbst.get("ci95") else None, "paired": True,
                    "moved": bool(pbst.get("ci95") and (pbst["ci95"][0] > 0 or pbst["ci95"][1] < 0)),
                    "per_task": pbst.get("per_task")}
        else:
            stat = (_paired_diff(pa, pb, B=B) if (paired or shared) else _unpaired_diff(pa, pb, B=B))
        out["metrics"].append({
            "key": key, "label": label, "unit": unit, "higher_is_better": hib,
            "a": _mean(pa.values()) if pa else None, "b": _mean(pb.values()) if pb else None,
            **stat,
        })
    return _clean(out)


# --------------------------------------------------------------------------- 2. ablation
def _terse(prompt: str) -> str:
    lines = [l for l in (prompt or "").splitlines() if l.strip()]
    return "\n".join(lines[:2]) if lines else "Fix the issue. Call submit when done."


#: The default one-factor-at-a-time grid. Each entry yields zero or more variants of the base.
FACTORS = ["policy", "max_steps", "max_total_tokens", "context_window", "observation_chars",
           "tools", "system_prompt", "include_file_listing", "temperature"]

FACTOR_DESC = {
    "policy": "flip strict <-> permissive",
    "max_steps": "halve and double the step cap",
    "max_total_tokens": "halve the token budget",
    "context_window": "full history vs last-5 vs last-10 observations",
    "observation_chars": "tight (1500) vs roomy (6000) tool-output truncation",
    "tools": "remove one tool at a time (run_tests, bash, edit_file)",
    "system_prompt": "terse variant: the first two lines only",
    "include_file_listing": "toggle the repository listing in the first user message",
    "temperature": "greedy (0) vs sampled (0.7)",
}


def _variants(base_cfg: dict, factors: list[str]) -> list[dict]:
    bid = base_cfg["id"]
    out = []

    def add(factor, suffix, **changes):
        cfg = {**base_cfg, **changes, "id": f"{bid}~{suffix}"}
        cfg["notes"] = (f"one-factor ablation of {bid}: " +
                        ", ".join(f"{k} {base_cfg.get(k)!r} -> {v!r}" for k, v in changes.items()))
        out.append({"id": cfg["id"], "factor": factor, "suffix": suffix,
                    "change": "; ".join(f"{k}: {_short(base_cfg.get(k))} -> {_short(v)}" for k, v in changes.items()),
                    "knob": KNOBS.get(factor, ""), "config": cfg, "hash": content_hash(cfg)})

    if "policy" in factors:
        other = "permissive" if base_cfg.get("policy") == "strict" else "strict"
        add("policy", f"policy={other}", policy=other)
    if "max_steps" in factors:
        for mult, tag in ((0.5, "x0.5"), (2.0, "x2")):
            v = max(1, int(round(base_cfg.get("max_steps", 20) * mult)))
            if v != base_cfg.get("max_steps"):
                add("max_steps", f"max_steps={v}", max_steps=v)
    if "max_total_tokens" in factors:
        v = int(base_cfg.get("max_total_tokens", 0) * 0.5)
        if v and v != base_cfg.get("max_total_tokens"):
            add("max_total_tokens", f"max_total_tokens={v}", max_total_tokens=v)
    if "context_window" in factors:
        for v in (0, 5, 10):
            if v != base_cfg.get("context_window"):
                add("context_window", f"context_window={v}", context_window=v)
    if "observation_chars" in factors:
        for v in (1500, 6000):
            if v != base_cfg.get("observation_chars"):
                add("observation_chars", f"observation_chars={v}", observation_chars=v)
    if "tools" in factors:
        for t in ("run_tests", "bash", "edit_file"):
            cur = list(base_cfg.get("tools") or [])
            if t in cur:
                add("tools", f"tools=-{t}", tools=[x for x in cur if x != t])
    if "system_prompt" in factors:
        t = _terse(base_cfg.get("system_prompt", ""))
        if t != base_cfg.get("system_prompt"):
            add("system_prompt", "system_prompt=terse", system_prompt=t)
    if "include_file_listing" in factors:
        v = not bool(base_cfg.get("include_file_listing"))
        add("include_file_listing", f"include_file_listing={str(v).lower()}", include_file_listing=v)
    if "temperature" in factors:
        for v in (0.0, 0.7):
            if abs(float(base_cfg.get("temperature", 0.0)) - v) > 1e-9:
                add("temperature", f"temperature={v}", temperature=v)
    return out


def _short(v, n=48):
    if isinstance(v, str) and (len(v) > n or "\n" in v):
        return f"{len(v.splitlines())} lines / {len(v)} chars: “{' '.join(v.split())[:28]}…”"
    if isinstance(v, list) and len(json.dumps(v)) > n:
        return f"{len(v)} items: {', '.join(map(str, v))}"[:120]
    s = json.dumps(v) if not isinstance(v, str) else v
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _all_task_ids() -> list[str]:
    if not os.path.isdir(TASK_DIR):
        return []
    return sorted(t for t in os.listdir(TASK_DIR)
                  if not t.startswith("_") and os.path.exists(os.path.join(TASK_DIR, t, "task.json")))


def _observed_cost_per_run() -> dict:
    """Mean recorded cost per run by model, over every results directory (same source as /api/models)."""
    agg: dict = {}
    for name in list_results_dirs(RUNS_ROOT):
        try:
            for r in load_index(os.path.join(RUNS_ROOT, name)):
                if "cost_usd" in r and r["cost_usd"] is None:
                    continue                      # unpriceable, not free: never averaged in as $0
                a = agg.setdefault(r["model"], [0.0, 0])
                a[0] += float(r.get("cost_usd") or 0.0)
                a[1] += 1
        except Exception:
            continue
    return {m: {"cost_per_run": s / n, "runs": n} for m, (s, n) in agg.items() if n}


def _plan(base: str, factors: Optional[list[str]], models: list[str], tasks: Optional[list[str]],
          repeats: int, out_name: str, parallel: int, seed: int, dir: Optional[str] = None) -> dict:
    bh = resolve_harness(base, dir)
    fs = [f for f in (factors or FACTORS) if f in FACTORS]
    if not fs:
        raise HTTPException(400, f"no known factors in {factors!r}; known: {FACTORS}")
    variants = _variants(bh["config"], fs)
    files = _harness_files()
    by_hash = {content_hash(v["config"]): hid for hid, v in files.items()}
    for v in variants:
        v["exists"] = v["id"] in files
        # a variant can rediscover a harness that already exists under another name — the content
        # hash is what says so, and it means the sweep can reuse runs already in the ledger
        dup = by_hash.get(v["hash"])
        v["duplicate_of"] = dup if dup and dup != v["id"] else None
    tasks = tasks or _all_task_ids()
    # a variant that hashes to an existing harness is that harness: run it under the name it
    # already has instead of measuring the same cell twice
    harness_ids, seen = [base], {base}
    for v in variants:
        hid = v["duplicate_of"] or v["id"]
        if hid not in seen:
            seen.add(hid)
            harness_ids.append(hid)
    n_runs = len(models) * len(harness_ids) * len(tasks) * max(1, repeats)
    obs = _observed_cost_per_run()
    est, observed, priced = 0.0, 0, 0
    for m in models:
        if m.startswith("mock"):
            continue
        priced += 1
        cells = len(harness_ids) * len(tasks) * max(1, repeats)
        if obs.get(m, {}).get("runs", 0) >= 3:
            est += obs[m]["cost_per_run"] * cells
            observed += 1
    job = {"out": out_name, "models": models, "harnesses": harness_ids, "tasks": tasks,
           "repeats": max(1, repeats), "parallel": parallel, "seed": seed, "sentinel_ab": False}
    return {
        "base": base, "base_hash": bh["hash"], "base_source": bh["source"], "base_config": bh["config"],
        "factors": fs, "factor_desc": {f: FACTOR_DESC[f] for f in fs},
        "variants": variants, "n_variants": len(variants),
        "n_harnesses": len(harness_ids), "n_tasks": len(tasks), "repeats": max(1, repeats),
        "n_runs": n_runs, "n_models": len(models),
        "estimate": {"usd": est, "observed_models": observed, "priced_models": priced,
                     "basis": "mean observed cost per run for these models" if observed and observed == priced
                              else ("no cost history for these models — mock runs are free" if not priced else "partial cost history")},
        "job": job,
        "caveat": ("One factor at a time answers 'what does this knob do, holding the rest fixed'. "
                   "It does not find interactions: two knobs that each do nothing alone can still matter together. "
                   f"A full 2^{len(fs)} grid would be {2 ** len(fs)} cells."),
    }


class AblateIn(BaseModel):
    base: str
    factors: Optional[list[str]] = None
    save: bool = False
    models: list[str] = ["mock"]
    tasks: Optional[list[str]] = None
    repeats: int = 3
    parallel: int = 4
    seed: int = 0
    out: Optional[str] = None
    dir: Optional[str] = None          # results dir to resolve a base harness that has no file


@router.get("/ablate/plan")
def ablate_plan(base: str, factors: Optional[str] = None, models: str = "mock", tasks: Optional[str] = None,
                repeats: int = 3, out: Optional[str] = None, parallel: int = 4, seed: int = 0,
                dir: Optional[str] = None):
    """Dry run: what /ablate would generate, without writing anything."""
    fl = [f.strip() for f in factors.split(",") if f.strip()] if factors else None
    ml = [m.strip() for m in models.split(",") if m.strip()] or ["mock"]
    tl = [t.strip() for t in tasks.split(",") if t.strip()] if tasks else None
    p = _plan(base, fl, ml, tl, repeats, out or f"ablate_{base}".replace("~", "_"), parallel, seed, dir)
    p["saved"] = []
    p["skipped_existing"] = [v["id"] for v in p["variants"] if v["exists"]]
    return _clean(p)


@router.post("/ablate")
def ablate(a: AblateIn):
    """Generate one-factor-at-a-time variants of a base harness. With save=true they are written
    to harnesses/ (never overwriting an existing file) and become selectable in the Command
    center; the returned `job` is a ready-to-POST /api/jobs body for the whole sweep."""
    p = _plan(a.base, a.factors, a.models or ["mock"], a.tasks, a.repeats,
              a.out or f"ablate_{a.base}".replace("~", "_"), a.parallel, a.seed, a.dir)
    saved, skipped = [], []
    if a.save:
        os.makedirs(HARNESS_DIR, exist_ok=True)
        for v in p["variants"]:
            fn = f"{_safe_id(v['id'])}.json"
            path = os.path.join(HARNESS_DIR, fn)
            if os.path.exists(path):
                skipped.append(v["id"])
                v["saved"] = False
                continue
            HarnessConfig(**v["config"]).save(path)
            saved.append(v["id"])
            v["saved"] = True
            v["exists"] = True
    else:
        skipped = [v["id"] for v in p["variants"] if v["exists"]]
    p["saved"] = saved
    p["skipped_existing"] = skipped
    p["launchable"] = all(v["exists"] or v["duplicate_of"] for v in p["variants"])
    if not p["launchable"]:
        p["launch_note"] = "Save the variants first — /api/jobs resolves harnesses by file in harnesses/."
    return _clean(p)


# --------------------------------------------------------------------------- 3. ranking stability
def kendall_tau(a: list[float], b: list[float]) -> Optional[float]:
    """Kendall tau-b between two score vectors over the same items (ties handled)."""
    n = len(a)
    if n < 2:
        return None
    conc = disc = ta = tb = 0
    for i in range(n):
        for j in range(i + 1, n):
            da, db = a[i] - a[j], b[i] - b[j]
            if da == 0 and db == 0:
                ta += 1
                tb += 1
            elif da == 0:
                ta += 1
            elif db == 0:
                tb += 1
            elif (da > 0) == (db > 0):
                conc += 1
            else:
                disc += 1
    denom = math.sqrt((conc + disc + ta) * (conc + disc + tb))
    return (conc - disc) / denom if denom else None


def _ranks(scores: dict) -> dict:
    """Competition ranks (1 = best) from {item: score}, higher score = better rank."""
    order = sorted(scores.items(), key=lambda kv: (-(kv[1] if kv[1] is not None else -1e9), kv[0]))
    out, prev, prev_rank = {}, None, 0
    for i, (k, v) in enumerate(order, start=1):
        if prev is not None and v == prev:
            out[k] = prev_rank
        else:
            out[k] = i
            prev_rank = i
        prev = v
    return out


def _cell_pass(rows: list[dict], outcome: str, tasks: Optional[list[str]] = None) -> Optional[float]:
    """pass@1 = mean over tasks of the per-task pass rate. `tasks` may repeat (bootstrap resample)."""
    if tasks is None:
        agg = aggregate(rows, outcome)
        return agg.get("pass@1")
    by = defaultdict(list)
    for r in rows:
        by[r["task_id"]].append(1.0 if r.get(outcome) else 0.0)
    vals = [sum(by[t]) / len(by[t]) for t in tasks if by.get(t)]
    return sum(vals) / len(vals) if vals else None


@router.get("/ranking")
def ranking(dir: str, outcome: str = "hidden_pass", B: int = 300, seed: int = 0):
    """Does the model ranking survive a change of harness? Kendall tau between every pair of
    harness rankings, a task bootstrap of the mean tau, per-model rank range, and per-model
    harness sensitivity (max - min pass@1 across harnesses)."""
    d = _dir(dir)
    rows = load_index(d)
    harnesses = sorted({r["harness_id"] for r in rows})
    models = sorted({r["model"] for r in rows})
    tasks = sorted({r["task_id"] for r in rows})
    out = {"dir": dir, "outcome": outcome, "harnesses": harnesses, "models": models,
           "n_tasks": len(tasks), "runs": len(rows), "insufficient": None}
    if len(models) < 2 or len(harnesses) < 2:
        out["insufficient"] = (
            f"{len(models)} model(s) and {len(harnesses)} harness(es) in {dir}. Ranking stability needs at "
            "least 2 of each: with one model there is no ranking, and with one harness there is nothing to "
            "compare it against. Launch the same harnesses on a second model (Command center) and come back.")
        return _clean(out)

    cellrows = {(h, m): filter_rows(rows, harness_id=h, model=m) for h in harnesses for m in models}
    grid = {h: {m: _cell_pass(cellrows[(h, m)], outcome) for m in models if cellrows[(h, m)]} for h in harnesses}
    grid = {h: v for h, v in grid.items() if len(v) >= 2}
    if len(grid) < 2:
        out["insufficient"] = ("Fewer than two harnesses have at least two models with runs in this "
                               "directory, so no two rankings can be compared.")
        out["grid"] = _clean({h: {m: v for m, v in mm.items()} for h, mm in grid.items()})
        return _clean(out)

    ranks = {h: _ranks(v) for h, v in grid.items()}
    hs = sorted(grid)

    def mean_tau(g: dict) -> tuple:
        pairs = []
        for i in range(len(hs)):
            for j in range(i + 1, len(hs)):
                ha, hb = hs[i], hs[j]
                common = sorted(set(g[ha]) & set(g[hb]))
                if len(common) < 2:
                    continue
                t = kendall_tau([g[ha][m] for m in common], [g[hb][m] for m in common])
                if t is not None:
                    pairs.append((ha, hb, t, len(common)))
        return (sum(p[2] for p in pairs) / len(pairs) if pairs else None), pairs

    mt, pairs = mean_tau(grid)
    out["tau_pairs"] = [{"a": a, "b": b, "tau": t, "n_models": n} for a, b, t, n in pairs]
    out["mean_tau"] = mt

    # bootstrap the whole pipeline over tasks: resample tasks, recompute every cell, re-rank, re-tau
    rng = random.Random(seed)
    boots = []
    for _ in range(max(20, B)):
        samp = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        g = {h: {m: _cell_pass(cellrows[(h, m)], outcome, samp) for m in grid[h]} for h in hs}
        g = {h: {m: v for m, v in mm.items() if v is not None} for h, mm in g.items()}
        t, _ = mean_tau(g)
        if t is not None:
            boots.append(t)
    boots.sort()
    out["mean_tau_ci95"] = ([boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]]
                            if len(boots) >= 20 else None)
    out["bootstrap_B"] = len(boots)

    per_model = []
    for m in models:
        rr = [ranks[h][m] for h in hs if m in ranks[h]]
        vv = [grid[h][m] for h in hs if m in grid[h] and grid[h][m] is not None]
        if not rr:
            continue
        # sensitivity CI: bootstrap tasks, recompute this model's max-min pass@1 across harnesses
        sboots = []
        rng2 = random.Random(seed + 1)
        for _ in range(max(20, B)):
            samp = [tasks[rng2.randrange(len(tasks))] for _ in tasks]
            xs = [_cell_pass(cellrows[(h, m)], outcome, samp) for h in hs if cellrows.get((h, m))]
            xs = [x for x in xs if x is not None]
            if len(xs) >= 2:
                sboots.append(max(xs) - min(xs))
        sboots.sort()
        per_model.append({
            "model": m, "by_harness": {h: grid[h].get(m) for h in hs},
            "ranks": {h: ranks[h].get(m) for h in hs},
            "best_rank": min(rr), "worst_rank": max(rr), "rank_range": max(rr) - min(rr),
            "pass1_min": min(vv) if vv else None, "pass1_max": max(vv) if vv else None,
            "sensitivity": (max(vv) - min(vv)) if len(vv) >= 2 else None,
            "sensitivity_ci95": ([sboots[int(0.025 * len(sboots))], sboots[int(0.975 * len(sboots)) - 1]]
                                 if len(sboots) >= 20 else None),
        })
    out["per_model"] = per_model
    out["grid"] = {h: grid[h] for h in hs}
    out["ranks"] = ranks
    worst = max((p["rank_range"] for p in per_model), default=0)
    out["rank_flips"] = sum(1 for p in per_model if p["rank_range"] > 0)
    ci = out["mean_tau_ci95"]
    ci_txt = f", 95% CI [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else ""
    if mt is None:
        out["finding"] = "Not enough overlapping cells to compute a rank correlation."
    elif mt >= 0.8:
        out["finding"] = (f"Model ranking agrees across harnesses (mean Kendall tau = {mt:.2f}{ci_txt}). "
                          f"Largest rank swing for any model: {worst} place(s).")
    elif mt >= 0.4:
        out["finding"] = (f"Model ranking only partly survives a change of harness (mean Kendall tau = {mt:.2f}{ci_txt}). "
                          f"Largest rank swing: {worst} place(s). A single-harness leaderboard is not reproducible here.")
    else:
        out["finding"] = (f"Model ranking disagrees across harnesses (mean Kendall tau = {mt:.2f}{ci_txt}). "
                          f"Largest rank swing: {worst} place(s). Which model 'wins' is a property of the harness, "
                          "not only of the models.")
    if ci and ci[0] < 0.4 <= (mt or 0):
        out["finding"] += (" The interval reaches down into disagreement, so this directory does not have enough "
                           "tasks or models to settle the question — read it as 'not yet decided', not as 'stable'.")
    if len(models) == 2:
        out["finding"] += (" With exactly two models Kendall tau can only be +1 or -1, so the point estimate carries "
                           "almost no information; the bootstrap interval is the honest reading.")
    out["caveat"] = (f"{len(models)} models over {len(tasks)} tasks: Kendall tau on {len(models)} items is a coarse "
                     "statistic and the CI is wide on purpose. The bootstrap resamples tasks, so it captures "
                     "task-selection noise but not provider-side nondeterminism.")
    return _clean(out)


# --------------------------------------------------------------------------- 4. versions & drift
@router.get("/versions")
def versions(dir: str):
    """Every distinct (harness_id, harness_hash) that actually produced runs in a results dir,
    with run counts and whether harnesses/<id>.json still hashes to the same thing."""
    d = _dir(dir)
    rows = load_index(d)
    rec = _recorded_harnesses(d)
    files = _harness_files()
    current = {hid: content_hash(v["config"]) for hid, v in files.items()}

    by_run = {}
    for hid, hashes in rec.items():
        for h, slot in hashes.items():
            for rid in slot["runs"]:
                by_run[rid] = (hid, h)

    per_run_meta = defaultdict(lambda: {"models": Counter(), "tasks": set(), "first": None, "last": None, "pass": [0, 0]})
    for r in rows:
        key = by_run.get(r["run_id"])
        if not key:
            continue
        m = per_run_meta[key]
        m["models"][r["model"]] += 1
        m["tasks"].add(r["task_id"])
        s = r.get("started_at") or ""
        m["first"] = s if m["first"] is None or (s and s < m["first"]) else m["first"]
        m["last"] = s if m["last"] is None or (s and s > m["last"]) else m["last"]
        m["pass"][1] += 1
        m["pass"][0] += 1 if r.get("hidden_pass") else 0

    out_versions, drift = [], []
    for hid in sorted(rec):
        cur = current.get(hid)
        hashes = rec[hid]
        for h, slot in sorted(hashes.items(), key=lambda kv: -len(kv[1]["runs"])):
            meta = per_run_meta.get((hid, h), {"models": Counter(), "tasks": set(), "first": None, "last": None, "pass": [0, 0]})
            matches = (cur == h) if cur else None
            out_versions.append({
                "harness_id": hid, "hash": h, "runs": len(slot["runs"]),
                "models": sorted(meta["models"]), "n_tasks": len(meta["tasks"]),
                "first_seen": meta["first"], "last_seen": meta["last"],
                "pass1": (meta["pass"][0] / meta["pass"][1]) if meta["pass"][1] else None,
                "has_file": hid in files, "file": files.get(hid, {}).get("file"),
                "current_hash": cur, "matches_current": matches,
                "config": slot["config"],
            })
            if cur and not matches:
                drift.append({"harness_id": hid, "hash": h, "runs": len(slot["runs"]), "current_hash": cur,
                              "changed_fields": _changed_fields(slot["config"], files[hid]["config"]),
                              "message": (f"{len(slot['runs'])} run(s) in {dir} were made with an older `{hid}` "
                                          f"({h}); harnesses/{files[hid]['file']} now hashes to {cur}. "
                                          "Those runs are not directly comparable to new ones.")})
    unseen = sorted(set(files) - set(rec))
    return _clean({
        "dir": dir, "runs": len(rows),
        "versions": out_versions,
        "n_versions": len(out_versions),
        "multi_version": sorted({v["harness_id"] for v in out_versions
                                 if sum(1 for w in out_versions if w["harness_id"] == v["harness_id"]) > 1}),
        "drift": drift,
        "files_never_run_here": unseen,
        "no_hash_runs": sum(v["runs"] for v in out_versions if v["hash"] == "unknown"),
        "note": ("`harness_hash` is stamped into every new run's index row and invoke_agent span. "
                 "For runs recorded before that field existed the hash is recomputed from the harness "
                 "config the ledger already carried, so old directories still version correctly."),
    })


def _changed_fields(a: Optional[dict], b: Optional[dict]) -> list[str]:
    if not a or not b:
        return []
    out = []
    for k in sorted(set(a) | set(b)):
        if k in COSMETIC:
            continue
        va, vb = a.get(k), b.get(k)
        if k == "tools":
            if sorted(va or []) != sorted(vb or []):
                out.append(k)
        elif json.dumps(va, sort_keys=True) != json.dumps(vb, sort_keys=True):
            out.append(k)
    return out


@router.get("/knobs")
def knobs():
    """The glossary the diff table shows next to each field."""
    return {"knobs": KNOBS, "factors": FACTOR_DESC, "hash_excludes": list(COSMETIC)}


@router.get("/list")
def harness_list(dir: Optional[str] = None):
    """Every harness id selectable here: files in harnesses/, plus ids that only exist in the
    ledgers of `dir` (imported harnesses, `+sentinel` twins)."""
    files = _harness_files()
    out = [{"id": hid, "hash": content_hash(v["config"]), "source": "file", "file": v["file"],
            "notes": v["config"].get("notes", "")} for hid, v in sorted(files.items())]
    if dir:
        try:
            rec = _recorded_harnesses(_dir(dir))
        except HTTPException:
            rec = {}
        for hid in sorted(rec):
            if hid in files:
                continue
            best = max(rec[hid].items(), key=lambda kv: len(kv[1]["runs"]))
            out.append({"id": hid, "hash": best[0], "source": "ledger", "file": None,
                        "notes": (best[1]["config"] or {}).get("notes", ""), "runs": len(best[1]["runs"])})
    return _clean(out)
