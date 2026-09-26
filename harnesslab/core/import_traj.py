"""Best-effort import of a mini-SWE-agent trajectory (.traj.json) into the ledger format.

mini-SWE-agent writes `{"info": {...}, "messages": [...], "trajectory_format": "mini-swe-agent-1.1"}`
where messages are OpenAI chat messages (system/user/assistant/tool), each with an `extra` dict.
Assistant messages carry the action either as `tool_calls` or as a fenced bash block in `content`.
We map: assistant -> chat span; the action -> execute_tool span (tool name `bash`), run the policy
classifier on the command to emit boundary_event spans; grading is not possible (no task oracle).

    python -m harnesslab.core.import_traj run.traj.json --out data/runs/imported --task-id swe-bench-xyz
"""
from __future__ import annotations
import argparse, json, os, re, time
from dataclasses import asdict

from .ledger import Ledger, RunSummary, new_run_id
from .tools import check_command


def _action_from_message(m: dict) -> list[str]:
    cmds = []
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        cmds.append(args.get("command") or json.dumps(args))
    if not cmds and isinstance(m.get("content"), str):
        cmds += re.findall(r"```(?:bash|sh)?\n(.*?)```", m["content"], re.S)
    return [c.strip() for c in cmds if c and c.strip()]


def import_traj(path: str, out_root: str, task_id: str = None, harness_id: str = "mini-swe-agent") -> RunSummary:
    with open(path) as f:
        data = json.load(f)
    info, messages = data.get("info", {}), data.get("messages", [])
    model = str(info.get("config", {}).get("model", {}).get("model_name") or info.get("model_stats", {}).get("model") or "unknown")
    task_id = task_id or str(info.get("instance_id") or os.path.basename(path).split(".")[0])
    run_id = new_run_id()
    run_dir = os.path.join(out_root, run_id)
    os.makedirs(run_dir, exist_ok=True)
    ledger = Ledger(os.path.join(run_dir, "ledger.jsonl"), run_id, task_id, harness_id, model)
    summ = RunSummary(run_id=run_id, task_id=task_id, harness_id=harness_id, model=model, provider="imported",
                      repeat_index=0, started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    ledger.record("invoke_agent", status="start", harness={"id": harness_id, "tools": ["bash"], "policy": "unknown",
                                                          "source": os.path.basename(path), "format": data.get("trajectory_format")})
    step = 0
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        extra = m.get("extra", {}) or {}
        usage = extra.get("usage") or extra.get("response", {}).get("usage") or {}
        inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        cost = float(extra.get("cost") or 0.0)
        cmds = _action_from_message(m)
        ledger.record("chat", **{"gen_ai.operation.name": "chat", "gen_ai.usage.input_tokens": inp, "gen_ai.usage.output_tokens": out,
                                 "cost_usd": cost, "step": step, "text": (m.get("content") or "")[:2000] if isinstance(m.get("content"), str) else "",
                                 "tool_calls": [{"name": "bash", "arguments": {"command": c[:300]}} for c in cmds]})
        summ.steps += 1; summ.input_tokens += inp; summ.output_tokens += out; summ.cost_usd += cost
        result = ""
        for j in range(i + 1, min(i + 3, len(messages))):
            if messages[j].get("role") in ("user", "tool"):
                result = messages[j].get("content") if isinstance(messages[j].get("content"), str) else json.dumps(messages[j].get("content"))[:400]
                break
        for c in cmds:
            v = check_command(c)
            if not v.allowed:
                ledger.record("boundary_event", kind=v.kind, tool="bash", args={"command": c[:300]}, status="allowed")
                summ.boundary_events += 1; summ.boundary_kinds.append(v.kind)
            ledger.record("execute_tool", **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "bash",
                                             "args": {"command": c[:300]}, "status": "ok", "duration_ms": 0, "result_preview": (result or "")[:400]})
            summ.tool_calls += 1
            if re.search(r"pytest|unittest|npm test|make test", c):
                summ.tests_run_by_agent += 1
        step += 1
    summ.exit_reason = str(info.get("exit_status") or "unknown").lower()
    if summ.exit_reason == "submitted":
        summ.exit_reason = "submitted"
    ledger.record("grade", visible=None, hidden=None, strong=None, tests_modified=False, note="imported trajectory: no oracle available")
    ledger.record("invoke_agent", status="end", exit_reason=summ.exit_reason, cost_usd=summ.cost_usd, total_tokens=summ.input_tokens + summ.output_tokens)
    ledger.close()
    with open(os.path.join(run_dir, "patch.diff"), "w") as f:
        f.write(str(info.get("submission") or ""))
    summ.patch_bytes = len(str(info.get("submission") or ""))
    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        f.write(summ.to_json())
    with open(os.path.join(out_root, "index.jsonl"), "a") as f:
        f.write(json.dumps(asdict(summ)) + "\n")
    return summ


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--task-id", default=None)
    ap.add_argument("--harness-id", default="mini-swe-agent")
    a = ap.parse_args(argv)
    for p in a.paths:
        s = import_traj(p, a.out, a.task_id, a.harness_id)
        print(f"{p} -> {s.run_id}: steps={s.steps} tokens={s.input_tokens + s.output_tokens} boundary={s.boundary_events} exit={s.exit_reason}")


if __name__ == "__main__":
    main()
