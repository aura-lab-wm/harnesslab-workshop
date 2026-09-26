"""Bundle builder for the AgentLab Console (the lab's web platform).

The console never computes statistics on the server side. It packs the raw
measurements (index rows, compact ledgers, per-run trajectory features, harness
configs, task metadata, runs in progress) into one JSON document; the browser
does pass@k, Wilson intervals, paired bootstraps, Ochiai, the factorial
decomposition, and the trajectory predicates. That keeps the served app and the
exported single-file app identical.

    python -m harnesslab.core.console --results data/runs/prerecorded_mock --out bundle.json
"""
from __future__ import annotations
import glob, json, os, time
from typing import Optional

from .analysis import load_index, load_ledger
from .trajtest import Trajectory
from harnesslab.backend.results_scope import list_results_dirs

# The engine now lives at <checkout>/harnesslab/core/, one level deeper than when it was
# <checkout>/agentlab/. Three dirname() calls, not two, or every task/harness path resolves
# inside the package and the loader raises FileNotFoundError.
LAB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOL_CODE = {"list_files": "L", "read_file": "R", "write_file": "W", "edit_file": "E", "run_tests": "T", "bash": "B", "submit": "S"}


def _cut(s, n):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + f"… [+{len(s) - n} chars]"


def compact_span(sp: dict, preview_chars: int = 900) -> dict:
    """Keep the fields the viewer renders, truncate the long ones."""
    kind = sp["span"]
    out = {"seq": sp["seq"], "span": kind, "ts": sp.get("ts", "")}
    if kind == "chat":
        out.update({"step": sp.get("step"), "in": sp.get("gen_ai.usage.input_tokens", 0), "out": sp.get("gen_ai.usage.output_tokens", 0),
                    "cost": sp.get("cost_usd", 0.0), "ms": sp.get("duration_ms", 0), "text": _cut(sp.get("text", ""), 400),
                    "finish": sp.get("gen_ai.response.finish_reasons", []),
                    "calls": [{"name": c.get("name"), "args": {k: _cut(v, 160) for k, v in (c.get("arguments") or {}).items()}} for c in sp.get("tool_calls", [])]})
    elif kind == "execute_tool":
        args = sp.get("args") or {}
        tool = sp.get("gen_ai.tool.name")
        keep = 6000 if tool in ("write_file", "edit_file") else 200      # full edit content so the viewer can diff
        out.update({"tool": tool, "args": {k: _cut(v, keep) for k, v in args.items()}, "status": sp.get("status"),
                    "ms": sp.get("duration_ms", 0), "preview": _cut(sp.get("result_preview", ""), preview_chars)})
        if sp.get("kind"):
            out["kind"] = sp["kind"]                                      # action class for imported real trajectories
    elif kind == "edit":
        out.update({"path": sp.get("path"), "la": sp.get("lines_added", 0), "lr": sp.get("lines_removed", 0), "tool": sp.get("tool")})
    elif kind == "boundary_event":
        out.update({"kind": sp.get("kind"), "tool": sp.get("tool"), "status": sp.get("status"), "args": {k: _cut(v, 200) for k, v in (sp.get("args") or {}).items()}})
    elif kind == "grade":
        out.update({"visible": sp.get("visible"), "hidden": sp.get("hidden"), "strong": sp.get("strong"), "tests_modified": sp.get("tests_modified")})
    elif kind == "invoke_agent":
        out.update({"status": sp.get("status"), "exit_reason": sp.get("exit_reason"), "total_tokens": sp.get("total_tokens"), "cost": sp.get("cost_usd")})
        # What this run could not measure, carried through: the console's predicates ask the end span
        # whether the run's tool execution was recorded at all, and without it every unwatched run
        # reads as one whose conduct was observed (web/src/core.js predicates()).
        if sp.get("unmeasured"):
            out["unmeasured"] = list(sp["unmeasured"])
    return out


def run_features(t: Trajectory) -> dict:
    """Numeric per-run trajectory features. The browser binarises them (thresholds are editable there)."""
    names = t.tool_names
    edits = [i for i, n in enumerate(names) if n in ("write_file", "edit_file")]
    s = t.summary
    blocked = sum(1 for b in t.boundary_events if b.get("status") == "blocked")
    # identical (name, args) call repeated with no edit in between: the same action on the same state
    seen, rep_no_edit = set(), 0
    for s_ in t.tool_calls:
        if s_["gen_ai.tool.name"] in ("write_file", "edit_file"):
            seen = set()
        sig = json.dumps({"n": s_["gen_ai.tool.name"], "a": s_["args"]}, sort_keys=True)
        if sig in seen:
            rep_no_edit += 1
        seen.add(sig)
    out = {
        "steps": len(names),
        "n_view": sum(n in ("read_file", "list_files") for n in names),
        "n_read": sum(n == "read_file" for n in names),
        "n_edit": len(edits),
        "n_test": sum(n == "run_tests" for n in names),
        "n_bash": sum(n == "bash" for n in names),
        "n_submit": sum(n == "submit" for n in names),
        "repeated": t.repeated_tool_calls(),
        "repeated_no_edit": rep_no_edit,
        "first_edit_step": edits[0] if edits else -1,
        "ran_after_last_edit": t.ran_tests_after_last_edit(),
        "read_before_write": t.read_before_write(),
        "submitted": s.get("exit_reason") == "submitted",
        "exit_reason": s.get("exit_reason"),
        # null in the summary = the source could not record it (importers.common.UNMEASURED_FIELDS)
        "files_edited": None if s.get("files_touched", []) is None else len(s.get("files_touched", [])),
        "lines_edited": (None if s.get("lines_added", 0) is None or s.get("lines_removed", 0) is None
                         else s.get("lines_added", 0) + s.get("lines_removed", 0)),
        "tests_modified": None if "tests_modified" in s and s["tests_modified"] is None else bool(s.get("tests_modified")),
        "boundary_events": s.get("boundary_events", 0),
        "boundary_blocked": blocked,
        "tokens": (None if s.get("input_tokens", 0) is None or s.get("output_tokens", 0) is None
                   else s.get("input_tokens", 0) + s.get("output_tokens", 0)),
        "cost": s.get("cost_usd", 0.0),
        "sequence": t.action_string,
    }
    if "tool_calls" in s and s["tool_calls"] is None:
        # Tool execution was never recorded, so an empty ledger here is not "no edit, never tested".
        # Null, which every threshold the browser applies reads as not-exhibited.
        for k in ("steps", "n_view", "n_read", "n_edit", "n_test", "n_bash", "n_submit", "repeated",
                  "repeated_no_edit", "first_edit_step", "ran_after_last_edit", "read_before_write",
                  "boundary_blocked"):
            out[k] = None
    return out


def load_tasks(lab_root: str = LAB_ROOT) -> dict:
    out = {}
    for d in sorted(glob.glob(os.path.join(lab_root, "tasks", "t*"))):
        meta_path = os.path.join(d, "task.json")
        if not os.path.exists(meta_path):
            continue
        meta = json.load(open(meta_path))
        issue = ""
        ip = os.path.join(d, "issue.md")
        if os.path.exists(ip):
            issue = open(ip, encoding="utf-8").read()
        strong = os.path.isdir(os.path.join(d, "hidden_tests_strong"))
        out[meta["id"]] = {"id": meta["id"], "title": meta.get("title", ""), "probe": meta.get("probe", "none"),
                           "src_files": meta.get("src_files", []), "issue": _cut(issue, 1800), "has_strong": strong}
    return out


def load_harness_files(lab_root: str = LAB_ROOT) -> dict:
    out = {}
    for p in sorted(glob.glob(os.path.join(lab_root, "harnesses", "*.json"))):
        try:
            h = json.load(open(p))
            out[h["id"]] = {**h, "_file": os.path.relpath(p, lab_root)}
        except Exception:
            pass
    return out


def running_runs(results_dir: str) -> list[dict]:
    """Directories with a ledger but no summary.json are runs in flight."""
    out = []
    for d in glob.glob(os.path.join(results_dir, "*")):
        if not os.path.isdir(d) or os.path.exists(os.path.join(d, "summary.json")):
            continue
        lp = os.path.join(d, "ledger.jsonl")
        if not os.path.exists(lp):
            continue
        try:
            spans = [json.loads(l) for l in open(lp) if l.strip()]
        except Exception:
            continue
        if not spans:
            continue
        first = spans[0]
        out.append({"run_id": first.get("run_id", os.path.basename(d)), "task_id": first.get("task_id"), "harness_id": first.get("harness_id"),
                    "model": first.get("gen_ai.request.model"), "steps": sum(1 for s in spans if s["span"] == "chat"),
                    "last_tool": next((s.get("gen_ai.tool.name") for s in reversed(spans) if s["span"] == "execute_tool"), None),
                    "age_s": int(time.time() - os.path.getmtime(lp)), "results": os.path.basename(results_dir.rstrip("/"))})
    return out


_CACHE: dict = {}   # results dir -> {"sig": (mtime, size), "runs": [...], "traj": {...}, "feats": {...}, "harnesses": {...}}


def _load_dir(d: str, preview_chars: int) -> dict:
    """Parse one results directory; cached on the index file's (mtime, size) so a running batch only re-reads new runs."""
    label = os.path.basename(d.rstrip("/"))
    ip = os.path.join(d, "index.jsonl")
    st = os.stat(ip)
    sig = (st.st_mtime, st.st_size, preview_chars)
    c = _CACHE.get(d)
    if c and c["sig"] == sig:
        return c
    prev = c or {"runs": [], "traj": {}, "feats": {}, "harnesses": {}}
    seen = {r["run_id"] for r in prev["runs"]}
    runs, traj, feats, harnesses = list(prev["runs"]), dict(prev["traj"]), dict(prev["feats"]), dict(prev["harnesses"])
    for r in load_index(d):
        if r["run_id"] in seen:
            continue
        r = dict(r)
        r["results"] = label
        try:
            spans = load_ledger(d, r["run_id"])
        except FileNotFoundError:
            spans = []
        if spans and spans[0].get("span") == "invoke_agent" and spans[0].get("harness"):
            h = spans[0]["harness"]
            harnesses.setdefault(h["id"], h)
        key = f"{label}/{r['run_id']}"
        traj[key] = [compact_span(s, preview_chars) for s in spans]
        t = Trajectory(r["run_id"], r["task_id"], r["harness_id"], r["model"], r, spans)
        feats[key] = run_features(t)
        r["key"] = key
        runs.append(r)
    c = {"sig": sig, "runs": runs, "traj": traj, "feats": feats, "harnesses": harnesses}
    _CACHE[d] = c
    return c


def build_bundle(results_dirs: list[str], lab_root: str = LAB_ROOT, preview_chars: int = 900, jobs: Optional[list] = None) -> dict:
    from .providers import PRICES
    runs, traj, feats, harnesses, running = [], {}, {}, {}, []
    for d in results_dirs:
        if not os.path.exists(os.path.join(d, "index.jsonl")):
            if os.path.isdir(d):
                running.extend(running_runs(d))
            continue
        c = _load_dir(d, preview_chars)
        runs.extend(c["runs"]); traj.update(c["traj"]); feats.update(c["feats"])
        for hid, h in c["harnesses"].items():
            harnesses.setdefault(hid, h)
        running.extend(running_runs(d))
    for hid, h in load_harness_files(lab_root).items():
        harnesses.setdefault(hid, h)
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": f"{len(runs)}:{len(running)}:{sum(1 for j in (jobs or []) if j.get('status') == 'running')}",
        "results_dirs": [os.path.relpath(d, lab_root) if d.startswith(lab_root) else d for d in results_dirs],
        "runs": runs, "trajectories": traj, "features": feats, "harnesses": harnesses,
        "tasks": load_tasks(lab_root), "running": running, "jobs": jobs or [],
        "prices": {k: list(v) for k, v in PRICES.items()},
        "judge": load_judge(lab_root),
    }


def load_judge(lab_root: str = LAB_ROOT) -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(lab_root, "data", "judge", "*.json"))):
        try:
            d = json.load(open(p)); d["_file"] = os.path.relpath(p, lab_root); out.append(d)
        except Exception:
            pass
    return out


def default_results_dirs(lab_root: str = LAB_ROOT) -> list[str]:
    """Every results directory under <lab>/data/runs that a default view may show. Goes through
    results_scope, so private directories (captured sessions) are never included and a
    subdirectory without an index.jsonl is not treated as a results directory."""
    root = os.path.join(lab_root, "data", "runs")
    return [os.path.join(root, name) for name in list_results_dirs(root)]


def run_detail(results_dir: str, run_id: str) -> dict:
    """Everything about one run, untruncated: spans, patch, messages count."""
    d = os.path.join(results_dir, run_id)
    spans = load_ledger(results_dir, run_id)
    patch = open(os.path.join(d, "patch.diff"), encoding="utf-8", errors="replace").read() if os.path.exists(os.path.join(d, "patch.diff")) else ""
    summary = json.load(open(os.path.join(d, "summary.json"))) if os.path.exists(os.path.join(d, "summary.json")) else {}
    return {"run_id": run_id, "summary": summary, "spans": [compact_span(s, 4000) for s in spans], "patch": patch[:20000]}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=None, help="comma-separated results directories (default: every dir under data/runs)")
    ap.add_argument("--out", default="bundle.json")
    a = ap.parse_args()
    dirs = [p.strip() for p in a.results.split(",")] if a.results else default_results_dirs()
    b = build_bundle(dirs)
    json.dump(b, open(a.out, "w"))
    print(f"{len(b['runs'])} runs, {len(b['harnesses'])} harnesses, {len(b['tasks'])} tasks -> {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")
