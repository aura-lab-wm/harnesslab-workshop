"""Inspect AI eval logs (.eval zip or .json) -> lab ledger.

Docs: https://inspect.aisi.org.uk/eval-logs.html. One *log* is one eval of one task under
one model; one *sample* is one task instance, so an Inspect log becomes N lab runs.

Needs `pip install inspect_ai`; the import is lazy and only happens when this adapter
actually runs, so the platform stays fastapi+uvicorn+stdlib.

What we read (verified against a real log generated in-container with the
`mockllm/model` provider — the fixture at
`tests_agentlab/fixtures/importers/tiny_swe.eval` is that log):

  log.eval.task / .model / .task_version / .created         the cell
  log.eval.config                                            message_limit, token_limit,
                                                             time_limit, epochs, temperature
  log.plan.steps[].solver / .params                          the solver chain -> harness id,
                                                             `use_tools` params -> declared tools
  log.plan.config.temperature                                sampling temperature, when set
  log.samples[].id / .epoch / .metadata / .target
  log.samples[].messages                                     ChatMessageSystem / User /
                                                             Assistant (.tool_calls: ToolCall
                                                             with .id/.function/.arguments) /
                                                             Tool (.tool_call_id, .function,
                                                             .content, .error)
  log.samples[].scores                                       {scorer: Score(value, answer,
                                                                            explanation)}
  log.samples[].model_usage                                  {model: ModelUsage(...)}

Outcome: Inspect scores *are* a real verdict, so `hidden_pass` is set from the sample's
score — "C"/CORRECT, True, or a numeric >= 0.5 for a single-value score. A score of "P"
(partial) or "N"/"I" maps to False; an unscored sample stays None. `visible_pass` remains
the usual "the agent's last test command looked green" heuristic, which is a different
question and frequently disagrees.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator, Optional

from .common import Event, Session

NAME = "inspect"
DESCRIPTION = "Inspect AI eval log (.eval zip or .json); each sample becomes one run, scores become the verdict"
PATTERNS = ["*.eval", "*.json (Inspect log with eval/plan/samples)"]

TOOL_OVERRIDES = {"bash": "bash", "python": "bash", "submit": "submit", "think": "bash",
                  "str_replace_editor": "edit_file", "text_editor": "edit_file",
                  "bash_session": "bash", "web_search": "bash", "web_browser": "bash"}


def sniff(path: str) -> float:
    if os.path.isdir(path):
        return 0.0
    if path.endswith(".eval"):
        # .eval is a zip; the header is enough, we do not need inspect_ai to recognise it
        try:
            with open(path, "rb") as f:
                if f.read(2) == b"PK":
                    return 0.95
        except OSError:
            return 0.0
        return 0.0
    if not path.endswith(".json"):
        return 0.0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4000)
    except OSError:
        return 0.0
    if '"eval"' in head and ('"samples"' in head or '"plan"' in head or '"results"' in head):
        return 0.8
    return 0.0


def _score_to_bool(value: Any) -> Optional[bool]:
    """Inspect's score values: 'C'/'I'/'P'/'N', bools, numbers, or dicts of them."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().upper()
        if v in ("C", "CORRECT", "TRUE", "PASS", "PASSED", "YES", "RESOLVED"):
            return True
        if v in ("I", "INCORRECT", "N", "FALSE", "FAIL", "FAILED", "NO", "P", "PARTIAL", "UNRESOLVED"):
            return False
        return None
    if isinstance(value, (int, float)):
        return bool(value >= 0.5)
    if isinstance(value, dict):
        vals = [b for b in (_score_to_bool(v) for v in value.values()) if b is not None]
        return all(vals) if vals else None
    if isinstance(value, list):
        vals = [b for b in (_score_to_bool(v) for v in value) if b is not None]
        return all(vals) if vals else None
    return None


def _solver_name(log) -> str:
    plan = getattr(log, "plan", None)
    steps = [getattr(s, "solver", "") for s in (getattr(plan, "steps", None) or [])]
    steps = [s for s in steps if s]
    if steps:
        return "+".join(steps[-3:])
    return getattr(getattr(log, "eval", None), "solver", None) or "solver"


def _declared_tools(log) -> list[str]:
    out: list[str] = []
    plan = getattr(log, "plan", None)
    for st in (getattr(plan, "steps", None) or []):
        params = getattr(st, "params", None) or {}
        tools = params.get("tools") if isinstance(params, dict) else None
        for t in tools or []:
            n = t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
            if isinstance(n, str) and n not in out:
                out.append(n)
    return out


def _cfg(log, *names, default=None):
    for holder in (getattr(getattr(log, "eval", None), "config", None),
                   getattr(getattr(log, "plan", None), "config", None)):
        for n in names:
            v = getattr(holder, n, None)
            if v is not None:
                return v
    return default


def parse_file(path: str) -> list[Session]:
    try:
        from inspect_ai.log import read_eval_log            # lazy: optional dependency
    except ImportError as e:
        raise RuntimeError("Inspect logs need the inspect_ai package: "
                           "pip install --break-system-packages inspect_ai") from e
    log = read_eval_log(path)
    ev = getattr(log, "eval", None)
    task = getattr(ev, "task", "") or os.path.basename(path)
    model = getattr(ev, "model", "") or ""
    version = str(getattr(ev, "packages", {}).get("inspect_ai", "") or getattr(ev, "task_version", "") or "")
    solver = _solver_name(log)
    declared = _declared_tools(log)
    max_msgs = _cfg(log, "message_limit", default=0) or 0
    temperature = _cfg(log, "temperature")
    log_id = getattr(ev, "run_id", None) or os.path.splitext(os.path.basename(path))[0]
    scorers = [getattr(s, "name", "") for s in (getattr(getattr(log, "results", None), "scores", None) or [])]

    out: list[Session] = []
    for sample in (getattr(log, "samples", None) or []):
        events: list[Event] = []
        system_prompt = ""
        for m in (getattr(sample, "messages", None) or []):
            role = getattr(m, "role", "")
            content = getattr(m, "text", None)
            if not isinstance(content, str):
                content = _content_str(getattr(m, "content", ""))
            if role == "system":
                system_prompt = system_prompt or content
                continue
            if role == "user":
                events.append(Event("user", text=content))
            elif role == "assistant":
                if content and content.strip():
                    events.append(Event("assistant", text=content, model=getattr(m, "model", "") or model))
                for tc in (getattr(m, "tool_calls", None) or []):
                    args = getattr(tc, "arguments", None)
                    events.append(Event("tool_call", call_id=getattr(tc, "id", "") or "",
                                        name=getattr(tc, "function", "") or "",
                                        args=args if isinstance(args, dict) else {"input": args},
                                        model=getattr(m, "model", "") or model))
            elif role == "tool":
                err = getattr(m, "error", None)
                events.append(Event("tool_result", call_id=getattr(m, "tool_call_id", "") or "",
                                    text=content, ok=(False if err else None)))
        if not events:
            continue
        scores = getattr(sample, "scores", None) or {}
        hp, why = None, ""
        for sname, sc in scores.items():
            b = _score_to_bool(getattr(sc, "value", None))
            if b is not None:
                hp = b if hp is None else (hp and b)
                why = f"inspect score `{sname}`" if not why else f"{why} + `{sname}`"
        sid_raw = getattr(sample, "id", None)
        epoch = getattr(sample, "epoch", 1) or 1
        meta = getattr(sample, "metadata", None) or {}
        hint = ""
        for k in ("instance_id", "instance", "task_id", "id"):
            if isinstance(meta.get(k), str) and meta[k]:
                hint = meta[k]
                break
        hint = hint or (str(sid_raw) if sid_raw is not None else "")
        sess = Session(source=NAME, session_id=f"{log_id}:{sid_raw}:{epoch}", path=path,
                       model=model, cwd=str(meta.get("cwd") or ""),
                       agent="inspect", agent_version=version, events=events,
                       declared_tools=declared, system_prompt=system_prompt,
                       temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
                       max_steps_declared=int(max_msgs or 0),
                       task_id_hint=hint, hidden_pass=hp, outcome_source=why,
                       exit_status=str(getattr(getattr(sample, "error", None), "message", "") or
                                       getattr(log, "status", "") or ""),
                       extra={"solver": solver, "task": task, "epoch": epoch, "scorers": scorers,
                              "log_id": log_id, "tool_overrides": TOOL_OVERRIDES})
        out.append(sess)
    return out


def _content_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            t = getattr(c, "text", None)
            if isinstance(t, str):
                parts.append(t)
            elif isinstance(c, dict) and isinstance(c.get("text"), str):
                parts.append(c["text"])
        return "\n".join(parts)
    return "" if content is None else str(content)


def sessions(path: str) -> Iterator[Session]:
    if os.path.isdir(path):
        for dp, dn, fn in os.walk(path):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in sorted(fn):
                p = os.path.join(dp, f)
                if sniff(p) >= 0.7:
                    yield from parse_file(p)
        return
    yield from parse_file(path)
