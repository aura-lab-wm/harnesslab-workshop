"""sentinel_ext — the sentinel as a platform: plugins, portable models, a leaderboard.

Three things the core `sentinel.py` deliberately does not do, exposed as one router:

  /api/sentinel/plugins            the detector catalogue (built-in | rule | python); `?reload=true` re-reads it
  /api/sentinel/export/{name}      a trained risk model as one self-describing JSON file with provenance
  /api/sentinel/import             the same file back, refusing anything whose feature list has drifted
  /api/sentinel/leaderboard        every model in the registry (plus the untrained prior and the rules-only
                                   layer) replayed on a results dir and scored with EW-AUC@k, recall,
                                   false alarm and lead time, all with run-bootstrap CIs

EW-AUC@k ("early-warning AUC at step k") is the AUC of the risk assigned at step k among the runs still
alive at step k. Conditioning on survival is the point: within that cohort the step index is constant, so
"failing runs are longer" — the length confound that inflates an AUC pooled over all prefixes — cannot
contribute anything. See harnesslab/docs/early_warning_metric.md.

Everything is stdlib + fastapi. Nothing here writes to a results directory.
"""
from __future__ import annotations
import hashlib, json, math, os, random, statistics, time
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import sentinel as S

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_ROOT = os.path.join(LAB, "data", "runs")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache", "leaderboard")
HARNESSLAB_VERSION = "0.1"
BUNDLE_FORMAT = "harnesslab.sentinel.model/1"
DEFAULT_KS = (10, 15, 20)

router = APIRouter(prefix="/api/sentinel")


def _dir(name: str) -> str:
    if not name or "/" in name or name.startswith("."):
        raise HTTPException(400, "bad results dir name")
    d = os.path.join(RUNS_ROOT, name)
    if not os.path.exists(os.path.join(d, "index.jsonl")):
        raise HTTPException(404, f"no results dir {name}")
    return d


def _clean(x):
    """NaN/inf -> null, so the JSON is valid (same convention as metrics._clean)."""
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# =========================================================================== 1. plugins
@router.get("/plugins")
def plugins(reload: bool = False):
    """The whole detector catalogue: built-ins first, then whatever is in harnesslab/plugins/."""
    st = S.load_plugins(force=reload)
    builtin = [{"id": k, "source": "builtin", "file": "sentinel.py", **v} for k, v in S.PATTERNS.items()]
    return {"dir": S.PLUGINS_DIR, "detectors": builtin + S.plugin_catalogue(), "errors": st["errors"],
            "loaded_at": st["loaded_at"], "n_builtin": len(builtin), "n_plugin": len(st["detectors"]),
            "features": S.FEATURE_NAMES,
            "rule_env_extra": ["steps_frac", "tokens_frac", "edits_since_test", "max_steps", "harness_policy"]}


# =========================================================================== 2. export / import
def _model_bundle(name: str) -> dict:
    if "/" in name or name.startswith("."):
        raise HTTPException(400, "bad model name")
    if not os.path.exists(os.path.join(S.MODELS_DIR, name + ".json")):
        raise HTTPException(404, f"no model {name} (registry: {', '.join(m['name'] for m in S.list_models()) or 'empty'})")
    m = S.load_model(name)
    meta = dict(m.meta or {})
    cv_keys = ("auc_all_prefixes", "auc_run_weighted", "auc_final_prefix", "auc_ci", "auc_baselines",
               "auc_by_step", "auc_by_prefix", "operating_points", "ece", "fail_rate", "folds",
               "oracle_invisible", "oracle_invisible_failure_share")
    model_json = m.to_json()
    bundle = {
        "format": BUNDLE_FORMAT,
        "name": meta.get("name", name or "sentinel_model"),
        "model": model_json,
        "provenance": {
            "training_dirs": meta.get("sources", []),
            "harness_filter": meta.get("harness_filter"),
            "n_runs": meta.get("n_runs"),
            "n_prefixes": meta.get("n_examples"),
            "run_counts": _run_counts(meta.get("sources", [])),
            "features": list(model_json["names"]),
            "n_features": len(model_json["names"]),
            "cv_metrics": {k: meta[k] for k in cv_keys if k in meta},
            "created_at": meta.get("trained_at"),
            "trained": bool(meta.get("trained")),
            "singletree_version": HARNESSLAB_VERSION,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }
    bundle["sha256"] = _sha({"model": bundle["model"], "provenance": bundle["provenance"], "name": bundle["name"]})
    return bundle


def _run_counts(dirs: list[str]) -> dict:
    """How many runs each training dir holds *now* — a reader can check the model against the data."""
    out = {}
    for d in dirs or []:
        p = os.path.join(RUNS_ROOT, os.path.basename(d), "index.jsonl")
        try:
            with open(p) as fh:
                out[os.path.basename(d)] = sum(1 for line in fh if line.strip())
        except OSError:
            out[os.path.basename(d)] = None
    return out


@router.get("/export/{name}")
def export_model(name: str):
    """The trained model as one portable JSON file: weights + standardisation + provenance + checksum."""
    b = _clean(_model_bundle(name))
    return JSONResponse(b, headers={"Content-Disposition": f'attachment; filename="{name}.sentinel.json"'})


class ImportIn(BaseModel):
    bundle: dict
    name: Optional[str] = None
    activate: bool = False
    ignore_checksum: bool = False


@router.post("/import")
def import_model(inp: ImportIn):
    """Accept an exported bundle. Refuses anything whose feature list is not exactly the current one —
    a model whose inputs mean something different is worse than no model."""
    b = inp.bundle or {}
    if isinstance(b.get("model"), dict) and "names" in b["model"]:
        model_json, prov = b["model"], b.get("provenance", {})
    elif "names" in b and "w" in b:                     # a bare RiskModel.to_json(), still importable
        model_json, prov, b = b, {}, {"model": b, "name": b.get("meta", {}).get("name")}
    else:
        raise HTTPException(400, "not a sentinel model bundle: expected {'model': {'names', 'mean', 'std', 'w', 'b'}}")
    for k in ("names", "mean", "std", "w"):
        if not isinstance(model_json.get(k), list):
            raise HTTPException(400, f"bundle.model.{k} is missing or not a list")
    n = len(model_json["names"])
    if not (len(model_json["mean"]) == len(model_json["std"]) == len(model_json["w"]) == n):
        raise HTTPException(400, "bundle.model arrays have different lengths (names/mean/std/w must agree)")
    got, want = list(model_json["names"]), list(S.FEATURE_NAMES)
    if got != want:
        missing = [f for f in want if f not in got]
        extra = [f for f in got if f not in want]
        detail = "feature list does not match this build of harnesslab. "
        if missing:
            detail += f"missing: {', '.join(missing)}. "
        if extra:
            detail += f"unknown: {', '.join(extra)}. "
        if not missing and not extra:
            detail += "same features in a different order. "
        detail += (f"this build expects {len(want)} features in this order: {', '.join(want)}")
        raise HTTPException(400, detail)
    if b.get("sha256") and not inp.ignore_checksum:
        calc = _sha({"model": b["model"], "provenance": b.get("provenance", {}), "name": b.get("name")})
        if calc != b["sha256"]:
            raise HTTPException(400, "checksum mismatch: the bundle was edited after export. "
                                     "Re-export it, or POST again with ignore_checksum=true if that is intended.")
    name = inp.name or b.get("name") or "imported_model"
    name = "".join(ch if ch.isalnum() or ch in "-_+@." else "_" for ch in str(name))[:60] or "imported_model"
    m = S.RiskModel.from_json(model_json)
    m.meta = dict(model_json.get("meta") or {})
    m.meta.update({"imported": True, "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "imported_from": {k: prov.get(k) for k in ("training_dirs", "created_at", "singletree_version", "holdstill_version", "n_runs")},
                   "sha256": b.get("sha256")})
    S.save_model(m, name, activate=bool(inp.activate))
    return {"name": name, "active": S.active_model_name(), "trained": bool(m.meta.get("trained")),
            "n_features": n, "provenance": m.meta.get("imported_from")}


# =========================================================================== 3. leaderboard
def _prefix_table(dir_path: str, harness: Optional[str] = None, max_runs: Optional[int] = None) -> list[dict]:
    """One pass over the ledgers: for every run, the prefix features at every step, plus what the rule
    layer says there. Scoring N models is then N cheap dot products, not N replays."""
    runs = []
    idx = os.path.join(dir_path, "index.jsonl")
    with open(idx) as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    for row in rows:
        if harness and row.get("harness_id") != harness:
            continue
        if row.get("hidden_pass") is None:
            continue
        p = os.path.join(dir_path, row["run_id"], "ledger.jsonl")
        try:
            with open(p) as fh:
                spans = [json.loads(line) for line in fh if line.strip()]
        except OSError:
            continue
        hcfg = next((s.get("harness", {}) for s in spans if s.get("span") == "invoke_agent" and s.get("status") == "start"), {})
        events = S.events_from_spans(spans)
        steps = sorted({e["step"] for e in events})
        if not steps:
            continue
        chat_tokens = {s["step"]: (s.get("gen_ai.usage.input_tokens") or 0) + (s.get("gen_ai.usage.output_tokens") or 0)
                       for s in spans if s.get("span") == "chat"}
        tokens, feats, rules, crit, swv = 0, [], [], [], []
        for k in steps:
            tokens += chat_tokens.get(k, 0)
            before = [e for e in events if e["step"] < k]
            pending = [{"name": e["tool"], "arguments": e["args"]} for e in events if e["step"] == k and e["tool"]]
            f = S.prefix_features(before, hcfg, tokens, pending=pending, step=k)
            pats = S.detect_all(f, before, hcfg)
            feats.append(f)
            rules.append(S._severity_score(pats))
            crit.append(any(p["severity"] == "critical" for p in pats))
            swv.append(any(p["id"] == "submit_without_verify" for p in pats))
        runs.append({"run_id": row["run_id"], "label": 0 if row["hidden_pass"] else 1, "steps": steps,
                     "feats": feats, "rules": rules, "crit": crit, "swv": swv})
        if max_runs and len(runs) >= max_runs:
            break
    return runs


def _risks_for(entrant: dict, runs: list[dict]) -> list[list[float]]:
    """Per-run risk curves for one entrant, exactly as the deployed sentinel would have produced them."""
    kind = entrant["kind"]
    if kind == "rules":
        return [list(r["rules"]) for r in runs]
    m: S.RiskModel = entrant["model"]
    boost = not m.meta.get("trained")          # sentinel.score() lifts an untrained prior on critical patterns
    out = []
    for r in runs:
        curve = []
        for i, f in enumerate(r["feats"]):
            v = m.predict(f)
            if boost:
                if r["crit"][i]:
                    v = max(v, 0.9)
                elif r["swv"][i]:
                    v = max(v, 0.7)
            curve.append(round(v, 4))
        out.append(curve)
    return out


def _median(xs):
    return statistics.median(xs) if xs else None


def _pct_ci(vals: list[float], alpha: float = 0.05):
    vals = sorted(v for v in vals if v is not None and v == v)
    if len(vals) < 20:
        return None
    return [vals[int((alpha / 2) * len(vals))], vals[max(0, int((1 - alpha / 2) * len(vals)) - 1)]]


def _resamples(n: int, bootstrap: int, seed: int) -> list[list[int]]:
    """Deterministic bootstrap resamples of positions 0..n-1. The same (n, seed) gives the same resamples
    for every entrant, so the leaderboard's intervals are paired across models."""
    rng = random.Random(seed)
    return [[rng.randrange(n) for _ in range(n)] for _ in range(int(bootstrap))] if (bootstrap and n) else []


def _entrant_stats(risks: list[list[float]], runs: list[dict], ks, threshold: float, bootstrap: int, seed: int) -> dict:
    labels = [r["label"] for r in runs]
    per_k = []
    for k in ks:
        cohort = [i for i, r in enumerate(runs) if k in r["steps"]]      # runs still alive at step k
        if not cohort:
            per_k.append({"k": k, "n_alive": 0, "ew_auc": None, "ew_auc_ci": None, "fail_share": None,
                          "recall": None, "false_alarm": None, "median_lead": None,
                          "recall_ci": None, "false_alarm_ci": None, "median_lead_ci": None})
            continue
        pos = {i: runs[i]["steps"].index(k) for i in cohort}
        sc = [risks[i][pos[i]] for i in cohort]
        ys = [labels[i] for i in cohort]
        # recall / false alarm / lead at k use only what was visible at or before step k
        first = {}
        for i in cohort:
            j = next((j for j in range(pos[i] + 1) if risks[i][j] >= threshold), None)
            first[i] = None if j is None else runs[i]["steps"][j]
        lead = {i: (runs[i]["steps"][-1] - first[i]) for i in cohort if first[i] is not None and labels[i]}

        def block(member_slots):        # member_slots index into `cohort`
            f_idx = [cohort[s] for s in member_slots if labels[cohort[s]]]
            o_idx = [cohort[s] for s in member_slots if not labels[cohort[s]]]
            rec = (sum(1 for i in f_idx if first[i] is not None) / len(f_idx)) if f_idx else None
            fa = (sum(1 for i in o_idx if first[i] is not None) / len(o_idx)) if o_idx else None
            ld = _median([lead[cohort[s]] for s in member_slots if cohort[s] in lead])
            return rec, fa, ld

        recall, fa, med_lead = block(range(len(cohort)))
        b_auc, b_rec, b_fa, b_lead = [], [], [], []
        for bs in _resamples(len(cohort), bootstrap, seed + k):
            w = [0.0] * len(cohort)
            for s in bs:
                w[s] += 1.0
            v = S._auc(sc, ys, w)
            if v == v:
                b_auc.append(v)
            r_, f_, l_ = block(bs)
            if r_ is not None:
                b_rec.append(r_)
            if f_ is not None:
                b_fa.append(f_)
            if l_ is not None:
                b_lead.append(l_)
        per_k.append({"k": k, "n_alive": len(cohort), "fail_share": (sum(ys) / len(ys)) if ys else None,
                      "ew_auc": S._auc(sc, ys), "ew_auc_ci": _pct_ci(b_auc),
                      "recall": recall, "recall_ci": _pct_ci(b_rec),
                      "false_alarm": fa, "false_alarm_ci": _pct_ci(b_fa),
                      "median_lead": med_lead, "median_lead_ci": _pct_ci(b_lead)})

    # ---- whole-run operating point: a run is flagged if ANY prefix crosses the threshold ---------------
    first_all, lead_all = {}, {}
    for i, r in enumerate(runs):
        j = next((j for j, v in enumerate(risks[i]) if v >= threshold), None)
        first_all[i] = None if j is None else r["steps"][j]
        if j is not None and r["label"]:
            lead_all[i] = r["steps"][-1] - r["steps"][j]

    def overall(members):
        f_idx = [i for i in members if labels[i]]
        o_idx = [i for i in members if not labels[i]]
        return ((sum(1 for i in f_idx if first_all[i] is not None) / len(f_idx)) if f_idx else None,
                (sum(1 for i in o_idx if first_all[i] is not None) / len(o_idx)) if o_idx else None,
                _median([lead_all[i] for i in members if i in lead_all]))

    o_rec, o_fa, o_lead = overall(range(len(runs)))
    b_rec, b_fa, b_lead = [], [], []
    for bi in _resamples(len(runs), bootstrap, seed):
        r_, f_, l_ = overall(bi)
        if r_ is not None:
            b_rec.append(r_)
        if f_ is not None:
            b_fa.append(f_)
        if l_ is not None:
            b_lead.append(l_)
    return {"per_k": per_k,
            "overall": {"recall": o_rec, "recall_ci": _pct_ci(b_rec), "false_alarm": o_fa,
                        "false_alarm_ci": _pct_ci(b_fa), "median_lead": o_lead, "median_lead_ci": _pct_ci(b_lead),
                        "n_runs": len(runs), "n_fail": sum(labels)}}


def _entrants() -> list[dict]:
    out = []
    for m in S.list_models():
        mdl = S.load_model(m["name"])
        out.append({"name": m["name"], "kind": "model", "model": mdl, "active": m["active"],
                    "trained": bool(mdl.meta.get("trained")),
                    "sha": _sha(mdl.to_json()), "sources": mdl.meta.get("sources", [])})
    prior = S.RiskModel.default()
    out.append({"name": "prior (untrained)", "kind": "model", "model": prior, "active": False, "trained": False,
                "sha": _sha(prior.to_json()), "sources": []})
    out.append({"name": "rules only", "kind": "rules", "model": None, "active": False, "trained": False,
                "sha": _sha([d["id"] for d in S.plugin_catalogue()] + sorted(S.PATTERNS)), "sources": []})
    return out


def _dir_sig(d: str) -> str:
    """A cheap fingerprint of the results dir: the index and the dir's own mtimes plus the run count."""
    idx = os.path.join(d, "index.jsonl")
    try:
        with open(idx) as fh:
            n = sum(1 for line in fh if line.strip())
    except OSError:
        n = -1
    return _sha([os.path.getmtime(idx), os.path.getmtime(d), n])


def leaderboard(dir_name: str, ks=DEFAULT_KS, threshold: float = 0.6, harness: Optional[str] = None,
                bootstrap: int = 200, max_runs: Optional[int] = None, seed: int = 0, use_cache: bool = True) -> dict:
    d = _dir(dir_name)
    ents = _entrants()
    key = _sha({"dir": dir_name, "sig": _dir_sig(d), "ks": list(ks), "thr": threshold, "harness": harness,
                "B": bootstrap, "max_runs": max_runs, "seed": seed,
                "entrants": [[e["name"], e["sha"]] for e in ents], "v": 3})
    cache_path = os.path.join(CACHE_DIR, key + ".json")
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path) as fh:
                out = json.load(fh)
            out["cached"] = True
            return out
        except (OSError, json.JSONDecodeError):
            pass
    t0 = time.time()
    runs = _prefix_table(d, harness=harness, max_runs=max_runs)
    if not runs:
        raise HTTPException(404, f"no labelled runs in {dir_name}" + (f" for harness {harness}" if harness else ""))
    rows = []
    for e in ents:
        risks = _risks_for(e, runs)
        st = _entrant_stats(risks, runs, ks, threshold, bootstrap, seed)
        rows.append({"model": e["name"], "kind": e["kind"], "active": e["active"], "trained": e["trained"],
                     "sha256": e["sha"][:12], "trained_on": e["sources"],
                     # a model replayed on the directory it was fitted on is scored IN SAMPLE here; the
                     # out-of-fold numbers for that pairing are the ones in the training report.
                     "in_sample": dir_name in [os.path.basename(str(s)) for s in (e["sources"] or [])],
                     **st})
    def _mean_auc(r):
        vals = [p["ew_auc"] for p in r["per_k"] if p["ew_auc"] is not None]
        return sum(vals) / len(vals) if vals else -1.0

    rows.sort(key=lambda r: -_mean_auc(r))
    out = {"dir": dir_name, "harness": harness, "k": list(ks), "threshold": threshold, "bootstrap": bootstrap,
           "n_runs": len(runs), "n_fail": sum(r["label"] for r in runs),
           "n_prefixes": sum(len(r["steps"]) for r in runs),
           "metric": "EW-AUC@k = AUC among runs still alive at step k (see harnesslab/docs/early_warning_metric.md)",
           "rows": rows, "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "seconds": round(time.time() - t0, 2), "cached": False, "cache_key": key[:12]}
    out = _clean(out)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as fh:
            json.dump(out, fh)
    except OSError:
        pass
    return out


def _parse_ks(k: str) -> tuple:
    try:
        ks = tuple(sorted({int(x) for x in str(k).replace(" ", "").split(",") if x != ""}))
    except ValueError:
        raise HTTPException(400, "k must be a comma-separated list of integers, e.g. k=10,15,20")
    if not ks or len(ks) > 8 or any(x < 0 or x > 500 for x in ks):
        raise HTTPException(400, "k must be 1-8 integers in [0, 500]")
    return ks


@router.get("/leaderboard")
def leaderboard_api(dir: str, k: str = "10,15,20", threshold: float = 0.6, harness: Optional[str] = None,
                    bootstrap: int = 200, max_runs: Optional[int] = None, refresh: bool = False):
    return leaderboard(dir, _parse_ks(k), threshold, harness, max(0, min(2000, bootstrap)), max_runs,
                       use_cache=not refresh)


def leaderboard_markdown(lb: dict) -> str:
    def n2(x, d=2):
        return "—" if x is None else f"{x:.{d}f}"

    def num_ci(x, ci, d=2):
        return n2(x, d) + ("" if not ci else f" [{ci[0]:.{d}f}, {ci[1]:.{d}f}]")

    def pct_ci(x, ci):
        s = "—" if x is None else f"{x*100:.0f}%"
        return s + ("" if not ci else f" [{ci[0]*100:.0f}, {ci[1]*100:.0f}]")

    def pc(x):
        return "—" if x is None else f"{x*100:.0f}%"

    ks = lb["k"]
    head = ["model"] + [f"EW-AUC@{k}" for k in ks] + [f"recall@{lb['threshold']}", "false alarm", "median lead (steps)"]
    lines = [f"**Sentinel leaderboard — `{lb['dir']}`**"
             + (f" (harness `{lb['harness']}`)" if lb.get("harness") else "")
             + f", {lb['n_runs']} runs ({lb['n_fail']} eventual failures), {lb['n_prefixes']} prefixes. ",
             f"EW-AUC@k = AUC among runs still alive at step k; brackets are 95% percentile intervals from "
             f"{lb['bootstrap']} bootstrap resamples over runs. Recall / false alarm / lead are whole-run, at "
             f"risk ≥ {lb['threshold']}.", "",
             "| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for r in lb["rows"]:
        cells = [("**" + r["model"] + "** *(active)*" if r["active"] else r["model"]) + (" †" if r.get("in_sample") else "")]
        for k in ks:
            p = next((p for p in r["per_k"] if p["k"] == k), None)
            cells.append(num_ci(p["ew_auc"], p["ew_auc_ci"]) if p else "—")
        o = r["overall"]
        cells += [pct_ci(o["recall"], o["recall_ci"]), pct_ci(o["false_alarm"], o["false_alarm_ci"]),
                  num_ci(o["median_lead"], o["median_lead_ci"], 1)]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "_Alive-at-k cohort sizes: " + ", ".join(
        f"k={p['k']}: n={p['n_alive']} ({pc(p['fail_share'])} fail)" for p in lb["rows"][0]["per_k"]) + "._"]
    if any(r.get("in_sample") for r in lb["rows"]):
        lines.append("_† trained on this directory: these numbers are in-sample and optimistic. The "
                     "out-of-fold figures for that pairing are in the model's own training report._")
    lines.append(f"_Computed {lb['computed_at']} by harnesslab {HARNESSLAB_VERSION}._")
    return "\n".join(lines)


