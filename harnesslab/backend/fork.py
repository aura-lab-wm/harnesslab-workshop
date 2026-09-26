"""fork — where two trajectories that started identically stop agreeing.

The platform's A/B jobs run *seed-paired twins*: harness `X` and `X+sentinel` on the same task with the
same seed (`app.py::_seed_for` hashes the seed from the **base** harness id, so the twin shares it). For a
deterministic provider (the mock agent) the two runs are therefore byte-identical up to the first moment the
hook actually changes something. That makes the pair a controlled experiment with n=1: everything before the
fork is shared history, everything after it is the intervention's causal cone.

Real models are nondeterministic, so a fork can equally well come from sampling. This module never guesses:
it reports the tool-level divergence index, the *effect*-level divergence index (a blocked call diverges the
run before the tool sequence does), the text divergence index, and whether an intervention precedes the fork
— and the UI states which of those the evidence supports.

Endpoints (prefix /api/fork):
  GET /{dir}/pairs                    candidate pairs: seed-paired twins, repeats, cross-harness
  GET /{dir}/compare?a=&b=            aligned steps, divergence indices, per-step state vectors, outcomes
  GET /{dir}/state/{run_id}           the state vectors alone (for the time-travel scrubber)
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_ROOT = os.path.join(LAB, "data", "runs")

router = APIRouter(prefix="/api/fork")

SENTINEL_SUFFIX = "+sentinel"
TEST_TOOLS = {"run_tests"}
_TEST_CMD = re.compile(r"\b(pytest|unittest|nose2|tox)\b")


def hoist_api_routes(app) -> None:
    """Keep the SPA catch-all last so an appended router stays reachable.

    `app.py` registers `GET /{path:path}` (only when `frontend/dist` exists) before anything appended at
    the end of the file, and Starlette matches routes in registration order — so without this every
    `/api/...` route added afterwards is dead in a built deployment. `list.sort` is stable and this only
    moves the catch-all, so it is safe to call repeatedly and safe to combine with the same trick in
    other appended blocks. Note that this FastAPI version keeps an included router as a single opaque
    entry in `app.router.routes`, so demoting the catch-all is the only reordering that actually works.
    """
    CATCH_ALL = ("/{path:path}", "/{full_path:path}")
    app.router.routes.sort(key=lambda r: 1 if str(getattr(r, "path", "")) in CATCH_ALL else 0)


# --------------------------------------------------------------------------- io
def _dir(name: str) -> str:
    if "/" in name or name.startswith("."):
        raise HTTPException(400, "bad results dir name")
    d = os.path.join(RUNS_ROOT, name)
    if not os.path.exists(os.path.join(d, "index.jsonl")):
        raise HTTPException(404, f"no results dir {name}")
    return d


def load_index(d: str) -> list[dict]:
    p = os.path.join(d, "index.jsonl")
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


_SPAN_CACHE: dict[str, tuple[float, list[dict]]] = {}


def load_spans(d: str, run_id: str) -> list[dict]:
    """Parsed ledger for one run, memoised on (path, mtime). 75-step runs are ~200 spans."""
    p = os.path.join(d, run_id, "ledger.jsonl")
    if not os.path.exists(p):
        raise HTTPException(404, f"no ledger for {run_id}")
    mt = os.path.getmtime(p)
    hit = _SPAN_CACHE.get(p)
    if hit and hit[0] == mt:
        return hit[1]
    spans = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    spans.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if len(_SPAN_CACHE) > 256:
        _SPAN_CACHE.clear()
    _SPAN_CACHE[p] = (mt, spans)
    return spans


def _first_line_seed(d: str, run_id: str):
    """The seed from the invoke_agent start span — read without parsing the whole ledger."""
    p = os.path.join(d, run_id, "ledger.jsonl")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            rec = json.loads(f.readline() or "{}")
        return rec.get("seed")
    except Exception:
        return None


def base_harness(hid: str) -> str:
    return hid[: -len(SENTINEL_SUFFIX)] if hid.endswith(SENTINEL_SUFFIX) else hid


# --------------------------------------------------------------------------- canonicalisation
_HEXID = re.compile(r"\b[0-9a-f]{8,}\b")
_RUNID = re.compile(r"\b\d{8}-\d{6}-[0-9a-f]{6}\b")
_TMP = re.compile(r"/(?:tmp|var/folders)/[^\s\"']+")
_WS = re.compile(r"\s+")
# argument keys whose value is an identifier of *this* run rather than a choice the model made
VOLATILE_KEYS = {"run_id", "id", "uuid", "timestamp", "ts", "seed", "workdir", "cwd"}


def canon_value(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)
    s = _RUNID.sub("<id>", s)
    s = _TMP.sub("<tmp>", s)
    s = _HEXID.sub("<id>", s)
    s = _WS.sub(" ", s).strip()
    return s[:600]


def canon_args(args: Optional[dict]) -> str:
    """Whitespace-insensitive, id-insensitive, key-order-insensitive rendering of a tool's arguments."""
    if not isinstance(args, dict):
        return canon_value(args)
    parts = []
    for k in sorted(args):
        if k in VOLATILE_KEYS:
            continue
        parts.append(f"{k}={canon_value(args[k])}")
    return "|".join(parts)


def args_summary(tool: Optional[str], args: Optional[dict], n: int = 70) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    for k in ("path", "command", "file", "summary", "pattern"):
        if k in args:
            return _WS.sub(" ", str(args[k]))[:n]
    return _WS.sub(" ", json.dumps(args, ensure_ascii=False, default=str))[:n]


# --------------------------------------------------------------------------- steps & state
def _is_test_call(tool: Optional[str], args: Optional[dict]) -> bool:
    if tool in TEST_TOOLS:
        return True
    if tool == "bash":
        return bool(_TEST_CMD.search((args or {}).get("command", "") or ""))
    return False


def _usage(span: dict, key: str) -> Optional[int]:
    """None when the key is PRESENT and null (the source recorded no usage); 0 when it is absent."""
    if key in span and span[key] is None:
        return None
    return span.get(key) or 0


def steps_of(spans: list[dict]) -> list[dict]:
    """Group the ledger into steps. A step = one model call (`chat` span) plus everything it caused."""
    steps: list[dict] = []
    by_index: dict[int, dict] = {}

    def slot(k: int) -> dict:
        if k not in by_index:
            st = {"step": k, "text": "", "tools": [], "edits": [], "boundary": [], "sentinel": None,
                  "in_tokens": 0, "out_tokens": 0, "cost": 0.0, "duration_ms": 0, "finish": None}
            by_index[k] = st
            steps.append(st)
        return by_index[k]

    cur = -1
    for sp in spans:
        kind = sp.get("span")
        if kind == "chat":
            cur = sp.get("step", cur + 1)
            st = slot(cur)
            st["text"] = sp.get("text") or ""
            # `or 0` here made the twin view report a $0.00 run for any source that records no
            # usage (importers.common.UNMEASURED_FIELDS). Carry the null through instead: the step
            # timeline renders "unknown", and state_vectors below refuses to total an unknown.
            st["in_tokens"] = _usage(sp, "gen_ai.usage.input_tokens")
            st["out_tokens"] = _usage(sp, "gen_ai.usage.output_tokens")
            cost = sp.get("cost_usd")
            st["cost"] = None if ("cost_usd" in sp and cost is None) else float(cost or 0.0)
            st["duration_ms"] = sp.get("duration_ms", 0) or 0
            st["finish"] = (sp.get("gen_ai.response.finish_reasons") or [None])[0]
            st["requested"] = [t.get("name") for t in (sp.get("tool_calls") or [])]
        elif kind == "execute_tool" and cur >= 0:
            tool = sp.get("gen_ai.tool.name")
            args = sp.get("args") or {}
            slot(cur)["tools"].append({
                "tool": tool, "args": args_summary(tool, args), "canon": canon_args(args),
                "status": sp.get("status", "ok"), "tests_passed": sp.get("tests_passed"),
                "is_test": _is_test_call(tool, args),
                "result": (sp.get("result_preview") or "")[:160],
            })
        elif kind == "edit" and cur >= 0:
            slot(cur)["edits"].append({"path": sp.get("path"), "added": sp.get("lines_added", 0) or 0,
                                       "removed": sp.get("lines_removed", 0) or 0})
        elif kind == "boundary_event" and cur >= 0:
            slot(cur)["boundary"].append({"kind": sp.get("kind"), "status": sp.get("status"), "tool": sp.get("tool")})
        elif kind == "sentinel":
            st = slot(sp.get("step", cur if cur >= 0 else 0))
            st["sentinel"] = {"risk": sp.get("risk"), "action": sp.get("action") or "none",
                              "patterns": sp.get("patterns") or [], "reason": sp.get("reason"),
                              "text": sp.get("text")}
    steps.sort(key=lambda s: s["step"])
    for st in steps:
        st["key"] = [[t["tool"], t["canon"]] for t in st["tools"]]
        st["effect"] = [[t["tool"], t["canon"], t["status"], t["tests_passed"]] for t in st["tools"]]
    return steps


def _test_result(tool_rec: dict):
    """pass / fail / unknown for one tool call that ran tests."""
    if tool_rec.get("tests_passed") is not None:
        return "pass" if tool_rec["tests_passed"] else "fail"
    res = tool_rec.get("result") or ""
    if res.startswith("exit=0"):
        return "pass"
    if res.startswith("exit="):
        return "fail"
    return "unknown"


def _unmeasured(spans: list[dict]) -> list:
    """What the run's end span says it could not measure (writer.py / importers.common)."""
    for s in reversed(spans or ()):
        if s.get("span") == "invoke_agent" and s.get("status") == "end":
            return list(s.get("unmeasured") or [])
    return []


def state_vectors(steps: list[dict], budget_tokens: int = 0, max_steps: int = 0,
                  unmeasured: Optional[list] = None) -> list[dict]:
    """Cumulative state after each step. Every count here is non-decreasing by construction — that is the
    property the scrubber relies on when you drag backwards."""
    files: list[str] = []
    bkinds: list[str] = []
    edits = added = removed = tests_run = boundary = blocked = interventions = 0
    # A source that records no tool EXECUTION writes no execute_tool and no edit span, so every
    # count below would be a hard zero drawn as a measurement. One flag, read from the summary the
    # steps were built from, turns them all into "not recorded" instead.
    observed = "tool_calls" not in set(unmeasured or ())
    # Once a step's usage or cost is unknown, every cumulative total from that step on is unknown:
    # a running sum that quietly skips the unmeasured step is a partial sum drawn as a total.
    tokens: Optional[int] = 0
    cost: Optional[float] = 0.0
    last_test = "unknown"
    submitted = False
    risk = None
    out = []
    for st in steps:
        if st["in_tokens"] is None or st["out_tokens"] is None:
            tokens = None
        elif tokens is not None:
            tokens += st["in_tokens"] + st["out_tokens"]
        if st["cost"] is None:
            cost = None
        elif cost is not None:
            cost += st["cost"]
        for e in st["edits"]:
            edits += 1
            added += e["added"]
            removed += e["removed"]
            if e["path"] and e["path"] not in files:
                files.append(e["path"])
        step_tests = []
        for t in st["tools"]:
            if t["status"] == "sentinel_blocked":
                blocked += 1
            if t["is_test"] and t["status"] != "sentinel_blocked":
                tests_run += 1
                r = _test_result(t)
                step_tests.append(r)
                if r != "unknown":
                    last_test = r
                else:
                    last_test = "unknown"
            if t["tool"] == "submit" and t["status"] not in ("sentinel_blocked", "blocked"):
                submitted = True
        boundary += len(st["boundary"])
        for bv in st["boundary"]:
            if bv.get("kind") and bv["kind"] not in bkinds:
                bkinds.append(bv["kind"])
        sen = st.get("sentinel")
        if sen:
            if sen.get("risk") is not None:
                risk = sen["risk"]
            if sen.get("action") and sen["action"] != "none":
                interventions += 1
        out.append({
            "step": st["step"],
            "files_touched": list(files) if observed else None,
            "edits": edits if observed else None,
            "lines_added": added if observed else None,
            "lines_removed": removed if observed else None,
            "tests_run": tests_run if observed else None,
            "last_test": last_test if observed else None,
            "step_tests": step_tests if observed else None,
            "tokens": tokens,
            "token_frac": (tokens / budget_tokens) if (budget_tokens and tokens is not None) else None,
            "step_frac": ((st["step"] + 1) / max_steps) if max_steps else None,
            "cost": None if cost is None else round(cost, 6),
            "risk": risk, "risk_here": (sen or {}).get("risk"),
            "action": (sen or {}).get("action") or None,
            "patterns": (sen or {}).get("patterns") or [],
            "boundary_events": boundary if observed else None,
            "boundary_kinds": list(bkinds) if observed else None,
            "boundary_here": [b.get("kind") for b in st["boundary"]],
            "blocked_calls": blocked if observed else None,
            "interventions": interventions if observed else None,
            "submitted": submitted,
            "tools": [t["tool"] for t in st["tools"]],
            "blocked_here": [t["tool"] for t in st["tools"] if t["status"] == "sentinel_blocked"],
        })
    return out


def replay_risk(spans: list[dict]) -> dict:
    """What the *currently active* sentinel model would have said at each step. Lets a plain run (no hook)
    still have a risk curve, so a twin pair is comparable on the same scale. Fails soft."""
    try:
        from . import sentinel as S            # lazy: keeps fork.py importable on its own
        return {r["step"]: {"risk": r["risk"], "patterns": r.get("patterns") or []}
                for r in S.replay(spans, S.load_model())}
    except Exception:
        return {}


def with_replay(state: list[dict], spans: list[dict]) -> list[dict]:
    """Attach the replayed sentinel's score to each step -- unless the run has no tool record.

    The replay reads tool calls and edits. A source that records neither scored 0.995 with the
    patterns "no_action" and "idle_no_edit": a confident reading of a run in which nothing was
    observed, which the Explorer then draws as the run's risk.
    """
    if "tool_calls" in set(_unmeasured(spans)):
        for sv in state:
            sv["risk_replay"] = None
            sv["replay_patterns"] = []
        return state
    rr = replay_risk(spans)
    for sv in state:
        r = rr.get(sv["step"]) or {}
        sv["risk_replay"] = r.get("risk")
        sv["replay_patterns"] = r.get("patterns") or []
    return state


def harness_of(spans: list[dict]) -> dict:
    for sp in spans:
        if sp.get("span") == "invoke_agent" and sp.get("status") == "start":
            return sp.get("harness") or {}
    return {}


def outcome_of(d: str, run_id: str, spans: list[dict], index_row: Optional[dict] = None) -> dict:
    s = index_row
    if s is None:
        p = os.path.join(d, run_id, "summary.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
    if s is None:
        g = next((x for x in spans if x.get("span") == "grade"), {})
        end = next((x for x in spans if x.get("span") == "invoke_agent" and x.get("status") == "end"), {})
        s = {"visible_pass": g.get("visible"), "hidden_pass": g.get("hidden"), "strong_pass": g.get("strong"),
             "exit_reason": end.get("exit_reason")}
    return {"run_id": run_id, "task_id": s.get("task_id"), "harness_id": s.get("harness_id"), "model": s.get("model"),
            "repeat_index": s.get("repeat_index"), "exit_reason": s.get("exit_reason"),
            "visible_pass": s.get("visible_pass"), "hidden_pass": s.get("hidden_pass"), "strong_pass": s.get("strong_pass"),
            "steps": s.get("steps"), "tool_calls": s.get("tool_calls"), "edits": s.get("edits"),
            "cost_usd": s.get("cost_usd"), "tokens": (None if "input_tokens" in s and s["input_tokens"] is None
                                              else (s.get("input_tokens") or 0) + (s.get("output_tokens") or 0)),
            "boundary_events": s.get("boundary_events"), "sentinel_interventions": s.get("sentinel_interventions"),
            "ran_tests_before_submit": s.get("ran_tests_before_submit"), "tests_modified": s.get("tests_modified")}


# --------------------------------------------------------------------------- alignment & divergence
def align(sa: list[dict], sb: list[dict]) -> dict:
    """Align two step lists by index (steps are model calls; a seed-paired twin shares its whole prefix)
    and locate the fork at three levels of strictness."""
    n = max(len(sa), len(sb))
    rows = []
    tool_div = text_div = effect_div = None
    for i in range(n):
        a = sa[i] if i < len(sa) else None
        b = sb[i] if i < len(sb) else None
        if a is None or b is None:
            same = same_text = same_effect = False
        else:
            same = a["key"] == b["key"]
            same_effect = a["effect"] == b["effect"]
            same_text = _WS.sub(" ", a["text"]).strip() == _WS.sub(" ", b["text"]).strip()
        if tool_div is None and not same:
            tool_div = i
        if effect_div is None and not same_effect:
            effect_div = i
        if text_div is None and not same_text:
            text_div = i
        rows.append({
            "step": i, "same": bool(same), "same_effect": bool(same_effect), "same_text": bool(same_text),
            "a": _row(a), "b": _row(b),
        })
    return {"steps": rows, "n": n, "tool_divergence": tool_div, "effect_divergence": effect_div,
            "text_divergence": text_div}


def _row(st: Optional[dict]) -> Optional[dict]:
    if st is None:
        return None
    return {"step": st["step"], "text": st["text"][:220],
            "tools": [{"tool": t["tool"], "args": t["args"], "status": t["status"], "tests_passed": t["tests_passed"]}
                      for t in st["tools"]],
            "edits": st["edits"], "boundary": st["boundary"], "sentinel": st["sentinel"],
            "tokens": (None if st["in_tokens"] is None or st["out_tokens"] is None
                       else st["in_tokens"] + st["out_tokens"])}


def first_intervention(steps: list[dict]) -> Optional[dict]:
    for st in steps:
        sen = st.get("sentinel")
        if sen and sen.get("action") and sen["action"] != "none":
            return {"step": st["step"], "action": sen["action"], "reason": sen.get("reason"),
                    "patterns": sen.get("patterns") or [],
                    "tools": [t["tool"] for t in st["tools"]]}
    return None


def classify(al: dict, iva: Optional[dict], ivb: Optional[dict], det: bool,
             harness_a: str, harness_b: str) -> dict:
    """Name the fork and say what the evidence actually supports."""
    tool_div, eff_div, txt_div = al["tool_divergence"], al["effect_divergence"], al["text_divergence"]
    candidates = [x for x in (tool_div, eff_div) if x is not None]
    fork = min(candidates) if candidates else None
    iv = min([x["step"] for x in (iva, ivb) if x], default=None)
    iv_rec = next((x for x in (iva, ivb) if x and x["step"] == iv), None)

    if fork is None and txt_div is None:
        return {"fork_step": None, "cause": "identical", "confident": True,
                "label": "identical trajectories",
                "detail": "Every step requested the same tools with the same arguments, and every model text matched."}
    if fork is None:
        return {"fork_step": None, "cause": "text_only", "confident": True,
                "label": f"same actions throughout — text differs from step {txt_div}",
                "detail": "The two runs took the same tool calls with the same arguments at every step; only the "
                          "model's prose differs. Any outcome difference is in the patch, not the process."}

    precedes = iv is not None and iv <= fork
    if precedes:
        what = f"{iv_rec['action']}ed {', '.join(iv_rec['tools']) or 'the pending call'}" if iv_rec else "intervened"
        return {"fork_step": fork, "cause": "intervention", "confident": True,
                "intervention_step": iv,
                "label": f"fork at step {fork} — sentinel {what}" + (f" ({iv_rec['reason']})" if iv_rec and iv_rec.get("reason") else ""),
                "detail": f"A sentinel verdict with action != none fired at step {iv}, at or before the fork. "
                          "The hook is the only difference between the two harnesses, so on a deterministic "
                          "provider this fork is attributable to the intervention."}
    if base_harness(harness_a) != base_harness(harness_b):
        return {"fork_step": fork, "cause": "harness", "confident": True,
                "label": f"fork at step {fork} — different harnesses ({harness_a} vs {harness_b})",
                "detail": "The two runs use different base harnesses, so tools, prompt, budget and seed all differ. "
                          "The shared prefix is a coincidence of the task, not shared history, and the fork is not "
                          "attributable to any single intervention."}
    if txt_div is not None and txt_div <= fork:
        return {"fork_step": fork, "cause": "sampling", "confident": True,
                "label": f"fork at step {fork} — sampling: the model said something different first",
                "detail": f"The models' texts already differed at step {txt_div}, before any action did, and no "
                          "intervention precedes the fork. This is a sampling difference, not an effect of the harness."}
    return {"fork_step": fork, "cause": "unexplained", "confident": False,
            "label": f"fork at step {fork} — same text, different action",
            "detail": ("Both runs use the deterministic mock provider yet took different actions after identical "
                       "text, so they did not really share a seed — check the pair before reading anything into it."
                       if det else
                       "The model produced the same text but a different tool call, and no intervention precedes the "
                       "fork. On a nondeterministic provider that is sampling.")}


# --------------------------------------------------------------------------- endpoints
@router.get("/{name}/pairs")
def pairs(name: str, limit: int = 200, task: Optional[str] = None, model: Optional[str] = None):
    """Candidate pairs of runs in a results dir, by what makes them comparable."""
    d = _dir(name)
    rows = [r for r in load_index(d) if (not task or r["task_id"] == task) and (not model or r["model"] == model)]
    seeds = {r["run_id"]: _first_line_seed(d, r["run_id"]) for r in rows}

    twins, repeats, cross = [], [], []

    # (a) seed-paired twins: X vs X+sentinel, same task, same model, same seed.
    by_key: dict[tuple, dict[str, list[dict]]] = {}
    for r in rows:
        base = base_harness(r["harness_id"])
        seed = seeds[r["run_id"]]
        key = (r["task_id"], r["model"], base, seed if seed is not None else f"r{r.get('repeat_index')}")
        slot = by_key.setdefault(key, {"plain": [], "sent": []})
        slot["sent" if r["harness_id"].endswith(SENTINEL_SUFFIX) else "plain"].append(r)
    for (tid, mdl, base, seed), slot in sorted(by_key.items(), key=lambda kv: str(kv[0])):
        for a, b in zip(slot["plain"], slot["sent"]):
            twins.append(_pair(a, b, "twin", seeds, seed_matched=seeds[a["run_id"]] is not None
                               and seeds[a["run_id"]] == seeds[b["run_id"]]))

    # (b) repeats: same task, same harness, same model — a pure sampling comparison.
    by_cell: dict[tuple, list[dict]] = {}
    for r in rows:
        by_cell.setdefault((r["task_id"], r["harness_id"], r["model"]), []).append(r)
    for key, rs in sorted(by_cell.items()):
        rs = sorted(rs, key=lambda r: (r.get("repeat_index", 0), r["run_id"]))
        for a, b in zip(rs, rs[1:]):
            repeats.append(_pair(a, b, "repeat", seeds))

    # (c) same task + same model, different base harness — the harness as the variable.
    by_tm: dict[tuple, list[dict]] = {}
    for r in rows:
        by_tm.setdefault((r["task_id"], r["model"]), []).append(r)
    for (tid, mdl), rs in sorted(by_tm.items()):
        first: dict[str, dict] = {}
        for r in sorted(rs, key=lambda r: (r["harness_id"], r.get("repeat_index", 0))):
            first.setdefault(base_harness(r["harness_id"]), r)
        bases = sorted(first)
        for i in range(len(bases)):
            for j in range(i + 1, len(bases)):
                cross.append(_pair(first[bases[i]], first[bases[j]], "harness", seeds))

    counts = {"twin": len(twins), "repeat": len(repeats), "harness": len(cross)}
    return {"dir": name, "counts": counts, "runs": len(rows),
            "seeds_present": sum(1 for v in seeds.values() if v is not None),
            "pairs": {"twin": twins[:limit], "repeat": repeats[:limit], "harness": cross[:limit]},
            "truncated": {k: v > limit for k, v in counts.items()}}


def _pair(a: dict, b: dict, kind: str, seeds: dict, seed_matched: Optional[bool] = None) -> dict:
    return {"kind": kind, "a": a["run_id"], "b": b["run_id"], "task": a["task_id"], "model": a["model"],
            "harness_a": a["harness_id"], "harness_b": b["harness_id"],
            "seed_a": seeds.get(a["run_id"]), "seed_b": seeds.get(b["run_id"]),
            "seed_matched": bool(seeds.get(a["run_id"]) is not None and seeds.get(a["run_id"]) == seeds.get(b["run_id"]))
            if seed_matched is None else seed_matched,
            "repeat_a": a.get("repeat_index"), "repeat_b": b.get("repeat_index"),
            "steps_a": a.get("steps"), "steps_b": b.get("steps"),
            "pass_a": a.get("hidden_pass"), "pass_b": b.get("hidden_pass"),
            "exit_a": a.get("exit_reason"), "exit_b": b.get("exit_reason"),
            "interventions_b": b.get("sentinel_interventions") or 0,
            "interventions_a": a.get("sentinel_interventions") or 0,
            "flip": bool(a.get("hidden_pass")) != bool(b.get("hidden_pass")),
            "deterministic": str(a.get("model", "")).startswith("mock") and str(b.get("model", "")).startswith("mock")}


@router.get("/{name}/state/{run_id}")
def state(name: str, run_id: str):
    """Per-step state vectors for one run — the data behind the time-travel scrubber."""
    d = _dir(name)
    spans = load_spans(d, run_id)
    h = harness_of(spans)
    steps = steps_of(spans)
    idx = {r["run_id"]: r for r in load_index(d)}
    return {"run_id": run_id, "dir": name, "harness": {"id": h.get("id"), "max_steps": h.get("max_steps"),
                                                       "max_total_tokens": h.get("max_total_tokens"),
                                                       "tools": h.get("tools"),
                                                       "sentinel_enabled": bool((h.get("sentinel") or {}).get("enabled"))},
            "seed": next((s.get("seed") for s in spans if s.get("span") == "invoke_agent" and s.get("status") == "start"), None),
            "outcome": outcome_of(d, run_id, spans, idx.get(run_id)),
            "state": with_replay(state_vectors(steps, int(h.get("max_total_tokens") or 0),
                                              int(h.get("max_steps") or 0), _unmeasured(spans)), spans),
            "steps": [_row(s) for s in steps]}


@router.get("/{name}/compare")
def compare(name: str, a: str, b: str):
    """Align two ledgers step by step, find the fork, and say what caused it."""
    d = _dir(name)
    if a == b:
        raise HTTPException(400, "a and b must be different runs")
    sa_spans, sb_spans = load_spans(d, a), load_spans(d, b)
    ha, hb = harness_of(sa_spans), harness_of(sb_spans)
    sa, sb = steps_of(sa_spans), steps_of(sb_spans)
    idx = {r["run_id"]: r for r in load_index(d)}
    oa, ob = outcome_of(d, a, sa_spans, idx.get(a)), outcome_of(d, b, sb_spans, idx.get(b))
    al = align(sa, sb)
    iva, ivb = first_intervention(sa), first_intervention(sb)
    seed_a = next((s.get("seed") for s in sa_spans if s.get("span") == "invoke_agent" and s.get("status") == "start"), None)
    seed_b = next((s.get("seed") for s in sb_spans if s.get("span") == "invoke_agent" and s.get("status") == "start"), None)
    det = str(oa.get("model") or "").startswith("mock") and str(ob.get("model") or "").startswith("mock")
    verdict = classify(al, iva, ivb, det, oa.get("harness_id") or "", ob.get("harness_id") or "")
    same_task = oa.get("task_id") == ob.get("task_id")
    return {
        "dir": name, "a": a, "b": b,
        "pair": {
            "same_task": same_task, "same_model": oa.get("model") == ob.get("model"),
            "same_base_harness": base_harness(oa.get("harness_id") or "") == base_harness(ob.get("harness_id") or ""),
            "seed_a": seed_a, "seed_b": seed_b, "seed_matched": seed_a is not None and seed_a == seed_b,
            "deterministic_provider": det,
            "twin": (base_harness(oa.get("harness_id") or "") == base_harness(ob.get("harness_id") or "")
                     and (oa.get("harness_id") or "").endswith(SENTINEL_SUFFIX) != (ob.get("harness_id") or "").endswith(SENTINEL_SUFFIX)),
        },
        "verdict": verdict,
        "divergence": {"tool": al["tool_divergence"], "effect": al["effect_divergence"], "text": al["text_divergence"]},
        "interventions": {"a": iva, "b": ivb},
        "steps": al["steps"], "n_steps": al["n"],
        "state": {
            "a": with_replay(state_vectors(sa, int(ha.get("max_total_tokens") or 0),
                                          int(ha.get("max_steps") or 0), _unmeasured(sa_spans)), sa_spans),
            "b": with_replay(state_vectors(sb, int(hb.get("max_total_tokens") or 0),
                                          int(hb.get("max_steps") or 0), _unmeasured(sb_spans)), sb_spans),
        },
        "harness": {"a": {"id": ha.get("id"), "max_steps": ha.get("max_steps"), "sentinel_enabled": bool((ha.get("sentinel") or {}).get("enabled")), "threshold": (ha.get("sentinel") or {}).get("threshold")},
                    "b": {"id": hb.get("id"), "max_steps": hb.get("max_steps"), "sentinel_enabled": bool((hb.get("sentinel") or {}).get("enabled")), "threshold": (hb.get("sentinel") or {}).get("threshold")}},
        "outcome": {"a": oa, "b": ob},
        "caveat": ("Both runs use the offline mock provider, which is deterministic given the seed, so a shared prefix "
                   "is genuine shared history." if det else
                   "At least one run used a real model. Real models are nondeterministic, so a fork is NOT evidence of "
                   "a sentinel effect unless an intervention precedes it; treat this as one paired sample, not a result."),
    }
