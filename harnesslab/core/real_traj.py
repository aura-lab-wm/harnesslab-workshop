"""Real SWE-agent trajectories: loading, parsing, and per-run features.

Data source (CC-BY-4.0): nebius/SWE-agent-trajectories on the Hugging Face Hub,
80,036 SWE-agent runs on SWE-bench-extra and the SWE-bench dev split with a
per-run `target` (resolved or not), `exit_status`, `generated_patch`, `eval_logs`
and the full `trajectory` (system / user / ai turns; each `ai` turn is a thought
plus one fenced command in SWE-agent's command language).

Loading needs network and either `datasets` or `pandas` + `pyarrow` +
`huggingface_hub`:

    pip install datasets            # or: pip install pandas pyarrow huggingface_hub

Everything else here is pure Python so the parsing can be tested offline.
"""
from __future__ import annotations
import json, re
from collections import Counter
from typing import Iterable, Optional

DATASET = "nebius/SWE-agent-trajectories"

# SWE-agent command language -> coarse action types
_VIEW = ("open", "goto", "scroll_up", "scroll_down", "cat", "ls", "head", "tail", "less", "tree")
_SEARCH = ("search_dir", "search_file", "find_file", "grep", "find", "rg", "ag")
_EDIT = ("edit", "create", "sed", "insert", "str_replace", "echo")
_RUN = ("python", "python3", "pytest", "py.test", "tox", "make", "npm", "node", "bash", "sh", "pip")


def classify_command(cmd: str) -> str:
    """Map a shell/SWE-agent command to view | search | edit | run | submit | other."""
    c = cmd.strip()
    if not c:
        return "other"
    head = re.split(r"\s+", c, maxsplit=1)[0]
    if head == "submit":
        return "submit"
    if head in _EDIT or c.startswith("edit "):
        return "edit"
    if head in _SEARCH:
        return "search"
    if head in _VIEW:
        return "view"
    if head in _RUN or re.match(r"^(cd\s+\S+\s*&&\s*)?(python|pytest)", c):
        return "run"
    if head in ("cd", "export", "source", "git"):
        return "other"
    return "other"


def extract_command(ai_text: str) -> str:
    """SWE-agent formats an action as a fenced block after the thought."""
    m = re.findall(r"```(?:bash|sh)?\n(.*?)```", ai_text, re.S)
    if m:
        return m[-1].strip()
    # fallback: last non-empty line
    lines = [l for l in ai_text.strip().splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def parse_trajectory(traj) -> list[dict]:
    """Return a list of steps: {thought, command, action, observation} from the raw
    trajectory (a JSON string or a list of {role, text, ...} dicts)."""
    if isinstance(traj, str):
        traj = json.loads(traj)
    steps, pending = [], None
    for entry in traj:
        role = entry.get("role")
        text = entry.get("text") or ""
        if role == "ai":
            if pending:
                steps.append(pending)
            cmd = extract_command(text)
            pending = {"thought": text.split("```")[0].strip()[:500], "command": cmd,
                       "action": classify_command(cmd), "observation": ""}
        elif role == "user" and pending is not None:
            pending["observation"] = text[:2000]
    if pending:
        steps.append(pending)
    return steps


def patch_stats(patch: str) -> dict:
    files = set(re.findall(r"^\+\+\+ b/(\S+)", patch or "", re.M))
    added = sum(1 for l in (patch or "").splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in (patch or "").splitlines() if l.startswith("-") and not l.startswith("---"))
    return {"files_edited": len(files), "lines_edited": added + removed, "test_files_edited": sum(1 for f in files if "test" in f.lower())}


def features(row: dict) -> dict:
    """Per-run features used by the exercise (and by the Ochiai attribution)."""
    steps = parse_trajectory(row["trajectory"])
    actions = [s["action"] for s in steps]
    hist = Counter(actions)
    cmds = [s["command"] for s in steps]
    repeated = sum(v - 1 for v in Counter(cmds).values() if v > 1)
    first_edit = next((i for i, a in enumerate(actions) if a == "edit"), None)
    last_edit = max([i for i, a in enumerate(actions) if a == "edit"] or [-1])
    ran_after_edit = any(a == "run" for a in actions[last_edit + 1:]) if last_edit >= 0 else False
    ps = patch_stats(row.get("generated_patch") or "")
    exit_status = (row.get("exit_status") or "").lower()
    return {
        "instance_id": row.get("instance_id"), "model": row.get("model_name"),
        "resolved": bool(row.get("target")), "exit_status": exit_status,
        "steps": len(steps), **{f"n_{k}": hist.get(k, 0) for k in ("view", "search", "edit", "run", "submit", "other")},
        "repeated_commands": repeated,
        "first_edit_step": first_edit if first_edit is not None else -1,
        "ran_after_last_edit": ran_after_edit,
        "submitted": exit_status == "submitted",
        "exit_context": "context" in exit_status,
        **ps,
    }


def binary_features(f: dict) -> dict:
    """Boolean view of the features for spectrum-based attribution."""
    from .repeats import THRESHOLD as _REPEAT_THRESHOLD   # the one literal, shared with the repeated-commands chart
    return {
        "no_test_run": f["n_run"] == 0,
        "no_search": f["n_search"] == 0,
        "never_verified_after_edit": not f["ran_after_last_edit"],
        "edited_tests": f["test_files_edited"] > 0,
        f"repeated_commands>{_REPEAT_THRESHOLD}": f["repeated_commands"] > _REPEAT_THRESHOLD,
        "steps>40": f["steps"] > 40,
        "many_files(>2)": f["files_edited"] > 2,
        "big_patch(>60 lines)": f["lines_edited"] > 60,
        "late_first_edit(>15)": f["first_edit_step"] > 15,
        "no_edit_at_all": f["n_edit"] == 0,
        "exit_context": f["exit_context"],
        "did_not_submit": not f["submitted"],
    }


# ---------------------------------------------------------------- loading
def load_nebius(n: int = 500, seed: int = 0, model: Optional[str] = None) -> list[dict]:
    """Stream the first `n` rows (optionally for one model name). Needs network."""
    try:
        from datasets import load_dataset  # type: ignore
    except ImportError as e:
        raise RuntimeError("pip install datasets   (or pass --offline to use the mock ledgers)") from e
    ds = load_dataset(DATASET, split="train", streaming=True)
    if seed:
        ds = ds.shuffle(seed=seed, buffer_size=2000)
    rows = []
    for r in ds:
        if model and r.get("model_name") != model:
            continue
        rows.append({k: r[k] for k in ("instance_id", "model_name", "target", "trajectory", "exit_status", "generated_patch")})
        if len(rows) >= n:
            break
    return rows


_SEARCH_CMD = re.compile(r"^\s*(grep|egrep|rg|ag|ack|find|find_file|search_file|search_dir|locate)\b")


def _action_of(span: dict) -> str:
    """Best available action label for one tool span.

    Imported trajectories carry the source harness's own label in ``swe_agent_action``; the lab's
    ledgers do not, so a shell command is classified by its verb. Anything unrecognised is "other",
    never silently counted as a search.
    """
    a = span.get("swe_agent_action")
    if a:
        return a
    name = span.get("gen_ai.tool.name", "")
    if name == "bash":
        cmd = (span.get("args") or {}).get("command", "")
        return "search" if _SEARCH_CMD.match(cmd) else "run"
    return {"read_file": "view", "list_files": "view", "write_file": "edit", "edit_file": "edit",
            "run_tests": "run", "submit": "submit"}.get(name, "other")


def to_results_dir(rows: list[dict], out_dir: str, harness_id: str = "swe-agent") -> int:
    """Write real trajectories as a results directory the console (and trajtest) can read.

    Each row becomes one run: an `invoke_agent` span carrying a nominal harness, one `chat` + one `execute_tool`
    span per SWE-agent step (tool `bash`, with `kind` = view | search | edit | run | submit | other), a `grade`
    span with the dataset's resolved label as the hidden oracle, and an index row. Tokens and cost are unknown
    (0); visible and strengthened oracles are absent (null)."""
    import os
    from .ledger import RunSummary
    from dataclasses import asdict
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    with open(os.path.join(out_dir, "index.jsonl"), "a", encoding="utf-8") as index:
        for i, row in enumerate(rows):
            steps = parse_trajectory(row["trajectory"])
            actions = [st["action"] for st in steps]
            ps = patch_stats(row.get("generated_patch") or "")
            exit_status = (row.get("exit_status") or "").lower() or "unknown"
            edits = [k for k, a in enumerate(actions) if a == "edit"]
            last_edit = edits[-1] if edits else -1
            model = str(row.get("model_name") or "unknown")
            run_id = f"{row.get('instance_id', 'row')}-{i:04d}".replace("/", "_")
            rd = os.path.join(out_dir, run_id)
            os.makedirs(rd, exist_ok=True)
            summ = RunSummary(run_id=run_id, task_id=str(row.get("instance_id")), harness_id=harness_id, model=model, provider="nebius",
                              repeat_index=0, started_at="", finished_at="", exit_reason="submitted" if exit_status == "submitted" else exit_status,
                              steps=len(steps), tool_calls=len(steps), edits=len(edits), lines_added=ps["lines_edited"], lines_removed=0,
                              files_touched=re.findall(r"^\+\+\+ b/(\S+)", row.get("generated_patch") or "", re.M),
                              tests_run_by_agent=sum(a == "run" for a in actions),
                              ran_tests_before_submit=any(a == "run" for a in actions[last_edit + 1:]) if last_edit >= 0 else False,
                              hidden_pass=bool(row.get("target")), visible_pass=None, strong_pass=None,
                              tests_modified=ps["test_files_edited"] > 0, patch_bytes=len(row.get("generated_patch") or ""))
            base = {"run_id": run_id, "task_id": summ.task_id, "harness_id": harness_id, "gen_ai.request.model": model}
            spans, seq = [], 0
            def rec(span, **f):
                nonlocal seq
                spans.append({**base, "seq": seq, "ts": "", "span": span, **f}); seq += 1
            rec("invoke_agent", status="start", harness={"id": harness_id, "system_prompt": "SWE-agent default system prompt (not stored in the dataset)",
                "tools": ["bash: SWE-agent command language (open, goto, scroll, search_*, find_file, edit, create, submit, shell)"],
                "policy": "n/a", "max_steps": None, "max_total_tokens": None, "context_window": None, "observation_chars": None,
                "temperature": None, "notes": "imported from nebius/SWE-agent-trajectories"})
            for k, st in enumerate(steps):
                rec("chat", **{"gen_ai.operation.name": "chat", "gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0, "cost_usd": 0.0,
                    "duration_ms": 0, "step": k, "text": st["thought"], "tool_calls": [{"name": "bash", "arguments": {"command": st["command"]}}]})
                rec("execute_tool", **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "bash", "kind": st["action"],
                    "args": {"command": st["command"]}, "status": "ok", "duration_ms": 0, "result_preview": st["observation"][:1200]})
            rec("grade", visible=None, hidden=summ.hidden_pass, strong=None, tests_modified=summ.tests_modified)
            rec("invoke_agent", status="end", exit_reason=summ.exit_reason, hidden_pass=summ.hidden_pass, cost_usd=0.0, total_tokens=0)
            with open(os.path.join(rd, "ledger.jsonl"), "w", encoding="utf-8") as f:
                for sp in spans:
                    f.write(json.dumps(sp, ensure_ascii=False) + "\n")
            with open(os.path.join(rd, "summary.json"), "w") as f:
                f.write(summ.to_json())
            with open(os.path.join(rd, "patch.diff"), "w", encoding="utf-8") as f:
                f.write(row.get("generated_patch") or "")
            index.write(json.dumps(asdict(summ)) + "\n")
            n += 1
    return n


def ledger_rows_as_features(results_dir: str) -> list[dict]:
    """Offline fallback: express the lab's mock ledgers in the same feature schema."""
    return [f for _, f in ledger_rows_as_features_with_ids(results_dir)]


def ledger_rows_as_features_with_ids(results_dir: str) -> list[tuple]:
    """The same rows, each beside the run it describes.

    Runs that cannot be described are skipped, so the rows no longer line up with the run list
    position by position; anything filtering them needs the run id, not an index.
    """
    from .trajtest import load_runs
    out = []
    for t in load_runs(results_dir):
        if "tool_calls" in t.summary and t.summary["tool_calls"] is None:
            # The source recorded tool REQUESTS but no execution (importers.common.UNMEASURED_FIELDS).
            # Every feature below is about execution, so this run would read as "never tested, never
            # edited" -- a claim nobody observed. It cannot be described in this schema; leave it out.
            continue
        names = t.tool_names
        edits = [i for i, n in enumerate(names) if n in ("write_file", "edit_file")]
        # Imported runs keep the source harness's own action label (see harnesslab/backend/importers);
        # for the lab's own ledgers there is no search tool, so fall back to sniffing the shell command.
        acts = [_action_of(s) for s in t.tool_calls]
        n_search = sum(a == "search" for a in acts)
        out.append((t.run_id, {
            "instance_id": t.task_id, "model": t.model, "resolved": t.passed, "exit_status": t.summary["exit_reason"],
            "steps": len(names),
            "n_view": sum(n in ("read_file", "list_files") for n in names), "n_search": n_search,
            "n_edit": len(edits), "n_run": sum(n in ("run_tests", "bash") for n in names),
            "n_submit": sum(n == "submit" for n in names), "n_other": 0,
            "repeated_commands": t.repeated_tool_calls(),
            "first_edit_step": edits[0] if edits else -1,
            "ran_after_last_edit": t.ran_tests_after_last_edit(),
            "submitted": t.summary["exit_reason"] == "submitted",
            "exit_context": t.summary["exit_reason"] == "budget_exceeded",
            "files_edited": len(t.summary["files_touched"]),
            "lines_edited": t.summary["lines_added"] + t.summary["lines_removed"],
            "test_files_edited": int(t.summary["tests_modified"]),
        }))
    return out
