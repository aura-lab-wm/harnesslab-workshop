"""Import real SWE-agent trajectories (nebius/SWE-agent-trajectories, CC-BY-4.0) into the ledger format.

Each run becomes data/runs/<dir>/<run_id>/{ledger.jsonl, summary.json, patch.diff, messages.json} plus a row in
index.jsonl, so every platform page — and the sentinel's training — works on real data. There is no grading:
`hidden_pass` is the dataset's `target` (resolved by the SWE-bench evaluation), `visible_pass` is a heuristic
("the agent's last test run looked green"), `strong_pass` is None.

Mapping SWE-agent's command language onto the lab's tool surface:
  view / search  -> read_file / bash          edit -> edit_file (path = the file the agent had open)
  run            -> bash (tests_passed inferred from the observation when the command looks like a test run)
  submit         -> submit                    other -> bash
"""
from __future__ import annotations
import json, os, re, time
from collections import Counter

from harnesslab.core.real_traj import load_nebius, parse_trajectory, patch_stats, classify_command, features as real_features
from harnesslab.core.ledger import RunSummary

MAX_STEPS = 75          # SWE-agent runs in this dataset are budgeted by cost, not steps; 75 covers >95% of them
_TEST_CMD = re.compile(r"\b(pytest|py\.test|unittest|tox|nose|python\s+-m\s+pytest|python\s+\S*test\S*\.py)\b")
_GREEN = re.compile(r"(\d+ passed|OK\b|PASSED|All tests passed)", re.I)
_RED = re.compile(r"(\d+ failed|FAILED|Error|Traceback|AssertionError|ERRORS?)", re.I)


def _harness_for(model: str) -> dict:
    return {"id": "swe-agent", "system_prompt": "(SWE-agent default system prompt; see the dataset card)",
            "tools": ["list_files", "read_file", "edit_file", "bash", "submit"], "policy": "permissive",
            "max_steps": MAX_STEPS, "max_total_tokens": 0, "context_window": 5, "observation_chars": 0,
            "temperature": 0.0, "max_tokens_per_call": 0, "include_file_listing": False,
            "notes": f"real SWE-agent trajectories, model {model}; imported by harnesslab", "sentinel": {}}


def _tests_passed(cmd: str, obs: str):
    if not _TEST_CMD.search(cmd):
        return None
    red, green = bool(_RED.search(obs)), bool(_GREEN.search(obs))
    if red and not green:
        return False
    if green and not red:
        return True
    if "failed" in obs.lower() or "error" in obs.lower():
        return False
    return True if green else None


def _exit_reason(status: str) -> str:
    s = (status or "").lower()
    if "context" in s:
        return "budget_exceeded"
    if s.startswith("submitted"):
        return "submitted"
    if "cost" in s or "budget" in s:
        return "budget_exceeded"
    if "max" in s and "step" in s:
        return "max_steps"
    if "early_exit" in s or "exit_error" in s or "error" in s:
        return "error"
    return "no_action" if not s else s[:24]


def convert(row: dict, out_root: str, idx: int) -> RunSummary:
    steps = parse_trajectory(row["trajectory"])
    inst = row["instance_id"]
    model = row.get("model_name") or "swe-agent"
    run_id = f"real-{idx:04d}-{re.sub(r'[^A-Za-z0-9]+', '_', inst)[:40]}"
    run_dir = os.path.join(out_root, run_id)
    os.makedirs(run_dir, exist_ok=True)
    harness = _harness_for(model)
    summary = RunSummary(run_id=run_id, task_id=inst, harness_id="swe-agent", model=model, provider="nebius/SWE-agent-trajectories",
                         repeat_index=0, started_at="")
    spans, messages, seq = [], [], 0
    def rec(span, **f):
        nonlocal seq
        r = {"run_id": run_id, "task_id": inst, "harness_id": "swe-agent", "gen_ai.request.model": model, "seq": seq, "ts": "", "span": span}
        r.update(f); spans.append(r); seq += 1
    rec("invoke_agent", status="start", harness=harness, seed=None, repeat_index=0)
    open_file, last_edit, last_test, submitted = "", -1, -1, False
    messages.append({"role": "system", "content": harness["system_prompt"]})
    messages.append({"role": "user", "content": f"Task id: {inst}\n\n(issue text is in the dataset; not stored here)"})
    for k, st in enumerate(steps[:MAX_STEPS]):
        cmd, act, obs = st["command"], st["action"], st.get("observation") or ""
        head = cmd.split()[0] if cmd.split() else ""
        m = re.match(r"^(open|cat|head|tail|less)\s+(\S+)", cmd)
        if m:
            open_file = m.group(2)
        if act == "view" and m:
            tool, args = "read_file", {"path": m.group(2)}
        elif act == "edit":
            tool, args = "edit_file", {"path": open_file or "(open file)", "old": cmd[:80], "new": ""}
            m2 = re.match(r"^create\s+(\S+)", cmd)
            if m2:
                open_file = m2.group(1); args["path"] = open_file
        elif act == "submit":
            tool, args = "submit", {"summary": st["thought"][:200]}
        else:
            tool, args = "bash", {"command": cmd[:300]}
        tp = _tests_passed(cmd, obs) if tool == "bash" else None
        status = "error" if (tool != "submit" and re.search(r"(command not found|No such file|Traceback|SyntaxError)", obs[:400])) else "ok"
        tc_id = f"call_{k}"
        rec("chat", **{"gen_ai.operation.name": "chat", "gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": max(1, len(st["thought"]) // 4),
                       "gen_ai.response.finish_reasons": ["tool_calls"], "duration_ms": 0, "cost_usd": 0.0, "step": k,
                       "text": st["thought"][:2000], "tool_calls": [{"name": tool, "arguments": {a: (v if len(str(v)) < 300 else str(v)[:300] + "...") for a, v in args.items()}}]})
        messages.append({"role": "assistant", "content": st["thought"][:2000], "tool_calls": [{"id": tc_id, "name": tool, "arguments": args}]})
        summary.steps += 1; summary.tool_calls += 1
        summary.output_tokens += max(1, len(st["thought"]) // 4); summary.input_tokens += len(obs) // 4
        extra = {"tests_passed": tp} if tp is not None else {}
        rec("execute_tool", **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool, "args": args, "status": status,
                               "duration_ms": 0, "result_preview": obs[:400], "swe_agent_action": act, "swe_agent_command": cmd[:300], **extra})
        messages.append({"role": "tool", "tool_call_id": tc_id, "content": ("exit=0\n" if tp else "exit=1\n" if tp is False else "") + obs[:1500]})
        if tool == "edit_file" and status == "ok":
            rec("edit", path=args["path"], lines_added=0, lines_removed=0, tool="edit_file")
            summary.edits += 1
            if args["path"] not in summary.files_touched:
                summary.files_touched.append(args["path"])
            last_edit = k
            if re.search(r"(^|/)tests?/|test_[^/]*\.py$", args["path"]):
                summary.tests_modified = True
        if tp is not None:
            summary.tests_run_by_agent += 1; last_test = k
        if tool == "submit":
            submitted = True
    summary.exit_reason = "submitted" if submitted else _exit_reason(row.get("exit_status"))
    summary.ran_tests_before_submit = last_test >= last_edit >= 0
    ps = patch_stats(row.get("generated_patch") or "")
    summary.lines_added = ps["lines_edited"]; summary.patch_bytes = len((row.get("generated_patch") or "").encode())
    if ps["test_files_edited"]:
        summary.tests_modified = True
    summary.hidden_pass = bool(row.get("target"))
    # visible_pass: the agent's own last test run looked green
    last_tp = next((s.get("tests_passed") for s in reversed(spans) if s["span"] == "execute_tool" and "tests_passed" in s), None)
    summary.visible_pass = bool(last_tp) if last_tp is not None else None
    summary.strong_pass = None
    summary.error = "" if summary.exit_reason != "error" else str(row.get("exit_status"))
    rec("grade", visible=summary.visible_pass, hidden=summary.hidden_pass, strong=None, tests_modified=summary.tests_modified,
        source="dataset label (SWE-bench evaluation)")
    rec("invoke_agent", status="end", exit_reason=summary.exit_reason, hidden_pass=summary.hidden_pass, cost_usd=0.0,
        total_tokens=summary.input_tokens + summary.output_tokens, swe_agent_exit_status=row.get("exit_status"))
    with open(os.path.join(run_dir, "ledger.jsonl"), "w") as f:
        for s in spans:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(os.path.join(run_dir, "patch.diff"), "w") as f:
        f.write(row.get("generated_patch") or "")
    with open(os.path.join(run_dir, "messages.json"), "w") as f:
        json.dump(messages, f, ensure_ascii=False)
    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        f.write(summary.to_json())
    return summary


def import_runs(n: int, out_root: str, seed: int = 0, model: str | None = None, progress=None) -> dict:
    os.makedirs(out_root, exist_ok=True)
    rows = load_nebius(n, seed=seed, model=model)
    feats = []
    with open(os.path.join(out_root, "index.jsonl"), "w") as idx:
        for i, r in enumerate(rows):
            s = convert(r, out_root, i)
            idx.write(json.dumps(s.__dict__) + "\n")
            feats.append(real_features(r))
            if progress and (i % 25 == 0 or i == len(rows) - 1):
                progress(i + 1, len(rows))
    with open(os.path.join(out_root, "real_features.jsonl"), "w") as f:
        for x in feats:
            f.write(json.dumps(x) + "\n")
    return {"n": len(rows), "resolved": sum(1 for r in rows if r["target"]), "models": dict(Counter(r["model_name"] for r in rows))}


# ---------------------------------------------------------------- the exercise-7 analysis, as JSON
CARD = {"steps": "31.3 vs 58.4", "files_edited": "1.33 vs 2.17", "lines_edited": "20.7 vs 61.0", "submitted": "94.6% vs 57.6%", "exit_context": "5.31% vs 30.4%"}


def analysis(out_root: str) -> dict:
    from harnesslab.core.analysis import bootstrap_ci, ochiai_attribution
    p = os.path.join(out_root, "real_features.jsonl")
    if not os.path.exists(p):
        return {"error": "no real_features.jsonl in this directory"}
    rows = [json.loads(l) for l in open(p)]
    ok = [r for r in rows if r["resolved"]]; ko = [r for r in rows if not r["resolved"]]
    def m(rs, k): return sum(r[k] for r in rs) / len(rs) if rs else None
    def rate(rs, k): return sum(1 for r in rs if r[k]) / len(rs) if rs else None
    table = []
    for label, key, kind in [("steps", "steps", "mean"), ("files edited", "files_edited", "mean"), ("lines edited", "lines_edited", "mean"),
                             ("repeated commands", "repeated_commands", "mean"), ("view actions", "n_view", "mean"), ("search actions", "n_search", "mean"),
                             ("edit actions", "n_edit", "mean"), ("run actions", "n_run", "mean"),
                             ("submitted", "submitted", "rate"), ("exit: context exhausted", "exit_context", "rate"),
                             ("ran something after last edit", "ran_after_last_edit", "rate")]:
        f = m if kind == "mean" else rate
        table.append({"metric": label, "key": key, "kind": kind, "resolved": f(ok, key), "unresolved": f(ko, key), "card": CARD.get(key)})
    table.append({"metric": "edited a test file", "key": "test_files_edited", "kind": "rate",
                  "resolved": (sum(1 for r in ok if r["test_files_edited"]) / len(ok)) if ok else None,
                  "unresolved": (sum(1 for r in ko if r["test_files_edited"]) / len(ko)) if ko else None, "card": None})
    ci = bootstrap_ci([r["steps"] for r in ko], B=1000) if ko else (None, None)
    exits = Counter(r["exit_status"] for r in rows)
    mix = {"resolved": Counter(), "unresolved": Counter()}
    for r in rows:
        for a in ("view", "search", "edit", "run", "submit", "other"):
            mix["resolved" if r["resolved"] else "unresolved"][a] += r[f"n_{a}"]
    def norm(c): t = sum(c.values()) or 1; return {k: v / t for k, v in c.items()}
    models = Counter(r["model"] for r in rows)
    by_model = [{"model": mdl, "n": c, "resolved": sum(1 for r in rows if r["model"] == mdl and r["resolved"]) / c} for mdl, c in models.items()]
    return {"n": len(rows), "resolved": len(ok), "unresolved": len(ko), "table": table, "steps_ci_unresolved": list(ci),
            "exit_status": dict(exits.most_common()), "action_mix": {k: norm(v) for k, v in mix.items()},
            "ochiai": ochiai_attribution(rows), "by_model": by_model}
