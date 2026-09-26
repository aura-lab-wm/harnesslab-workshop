"""OpenHands trajectories -> lab ledger.

Three shapes are accepted, because OpenHands has changed its serialisation and the
SWE-bench harness wraps it in a fourth:

1. **Classic event list** — a JSON array (or `{"items": [...]}` / `{"events": [...]}`)
   of events as written by `--save-trajectory-path` / the events API::

       {"id": 3, "timestamp": "...", "source": "agent", "action": "run",
        "args": {"command": "pytest -q", "thought": "let me test"},
        "message": "...", "tool_call_metadata": {...}}
       {"id": 4, "timestamp": "...", "source": "agent", "observation": "run",
        "content": "2 passed", "extras": {"command": "pytest -q", "exit_code": 0}}

   The action *name* lives either in `action` (`run`, `edit`, `write`, `read`,
   `run_ipython`, `browse`, `finish`, `message`, `think`, `delegate`) or, in older
   dumps, as a class name in `action`/`observation` (`CmdRunAction`, `FileEditAction`,
   `IPythonRunCellAction`, `AgentFinishAction`, `CmdOutputObservation`, ...). Both are
   handled. Observations are matched to their action by `cause` (the action's `id`) when
   present, and otherwise by order.

2. **v1 event export** — `MessageEvent` / `ActionEvent` / `ObservationEvent` /
   `AgentErrorEvent` / `UserRejectObservation` objects carrying `kind`, `tool_name`,
   `tool_call_id`, `action`, `observation`, `thought`, `llm_message`. This matches the
   contract documented by the reference decoder in letta-ai/trajectory
   (`src/adapters/openhands/`).

3. **SWE-bench `output.jsonl`** — one JSON object per line with `instance_id`,
   `history` (a list in either of the shapes above), `test_result` (whose
   `report.resolved` is a real verdict), `metrics`, `error`, `git_patch`. Each line
   becomes one run and keeps its SWE-bench instance id as the task id.

Outcome: `hidden_pass` only from `test_result.report.resolved` / `report.resolved` /
`resolved` in an output.jsonl row, or from a sidecar `results.json` / `report.json`.
A bare trajectory file carries no verdict, so it imports with `hidden_pass = None`.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator, Optional

from .common import Event, Session, blocks_text, read_jsonl, sidecar_outcome

NAME = "openhands"
DESCRIPTION = "OpenHands trajectory/event JSON, v1 event export, or a SWE-bench output.jsonl with `history`"
PATTERNS = ["*.json (event array or {items|events: […]})", "output.jsonl (SWE-bench eval output with history)"]

TOOL_OVERRIDES = {
    "run": "bash", "run_ipython": "bash", "execute_bash": "bash", "execute_ipython_cell": "bash",
    "read": "read_file", "write": "write_file", "edit": "edit_file", "str_replace_editor": "edit_file",
    "browse": "bash", "browse_interactive": "bash", "finish": "submit", "think": "bash",
    "delegate": "bash", "recall": "bash", "condensation": "bash", "message": "bash",
    "cmdrunaction": "bash", "ipythonruncellaction": "bash", "fileeditaction": "edit_file",
    "filewriteaction": "write_file", "filereadaction": "read_file", "agentfinishaction": "submit",
    "browseurlaction": "bash", "browseinteractiveaction": "bash", "agentdelegateaction": "bash",
    "agentthinkaction": "bash", "messageaction": "bash",
}

# class name -> action name for older dumps
_CLASS_RE = re.compile(r"^([A-Z][A-Za-z]*?)(Action|Observation)$")


def _action_name(v: Any) -> str:
    if not isinstance(v, str) or not v:
        return ""
    if _CLASS_RE.match(v):
        return v.lower()
    return v.lower()


def _load(path: str) -> Optional[Any]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            txt = f.read(4 * 1024 * 1024)
    except OSError:
        return None
    t = txt.lstrip()
    if t.startswith("[") or t.startswith("{"):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _events_list(data: Any) -> Optional[list[dict]]:
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        for k in ("items", "events", "history", "trajectory"):
            v = data.get(k)
            if isinstance(v, list):
                return [e for e in v if isinstance(e, dict)]
    return None


def _looks_openhands(events: list[dict]) -> float:
    if not events:
        return 0.0
    hits = 0
    for e in events:
        if "action" in e or "observation" in e:
            hits += 1
        elif e.get("kind") in ("MessageEvent", "ActionEvent", "ObservationEvent", "AgentErrorEvent", "UserRejectObservation"):
            hits += 1
    frac = hits / len(events)
    if frac < 0.5:
        return 0.0
    score = 0.4 + 0.4 * frac
    if any(e.get("source") in ("agent", "user", "environment") for e in events):
        score += 0.15
    return min(1.0, score)


def sniff(path: str) -> float:
    if os.path.isdir(path):
        return 0.0
    if path.endswith(".jsonl"):
        rows = read_jsonl(path, limit=3)
        if rows and any(isinstance(r.get("history"), list) for r in rows):
            return 0.9
        return 0.0
    if not path.endswith(".json"):
        return 0.0
    data = _load(path)
    ev = _events_list(data)
    if ev is None:
        return 0.0
    return _looks_openhands(ev)


# ------------------------------------------------------------------ event decoding
def _decode_events(raw: list[dict]) -> tuple[list[Event], str]:
    """-> (events, observed agent version hint). Handles both classic and v1 shapes."""
    events: list[Event] = []
    version = ""
    # pre-pass: map action event id -> call id, so an observation that arrives first still links
    call_for_id: dict[Any, str] = {}
    for e in raw:
        eid = e.get("id")
        if eid is None:
            continue
        if "action" in e or e.get("kind") == "ActionEvent":
            call_for_id[eid] = _call_id(e)
    n = 0
    for e in raw:
        ts = e.get("timestamp") or ""
        src = e.get("source") or ""
        kind = e.get("kind") or ""
        version = version or str(e.get("openhands_version") or e.get("version") or "")
        # --- v1 message events
        if kind == "MessageEvent":
            msg = e.get("llm_message") if isinstance(e.get("llm_message"), dict) else {}
            text = blocks_text(msg.get("content")) or (e.get("message") or "")
            role = (msg.get("role") or ("user" if src == "user" else "assistant"))
            events.append(Event("user" if role == "user" else "assistant", ts=ts, text=text))
            continue
        if kind in ("ObservationEvent", "AgentErrorEvent", "UserRejectObservation"):
            obs = e.get("observation") if isinstance(e.get("observation"), dict) else {}
            text = blocks_text(obs.get("content") if obs else None) or blocks_text(e.get("content")) or (e.get("message") or "")
            ok = None
            if isinstance(obs.get("is_error"), bool):
                ok = not obs["is_error"]
            elif kind in ("AgentErrorEvent", "UserRejectObservation"):
                ok = False
            events.append(Event("tool_result", ts=ts, text=text, ok=ok,
                                call_id=e.get("tool_call_id") or call_for_id.get(e.get("cause")) or ""))
            continue
        if kind == "ActionEvent":
            thought = blocks_text(e.get("thought")) or ""
            if thought.strip():
                events.append(Event("reasoning", ts=ts, text=thought))
            act = e.get("action") if isinstance(e.get("action"), dict) else {}
            name = e.get("tool_name") or act.get("kind") or "action"
            args = {k: v for k, v in act.items() if k != "kind"} or {}
            events.append(Event("tool_call", ts=ts, call_id=_call_id(e), name=str(name), args=args))
            n += 1
            continue
        # --- classic shape
        if "observation" in e:
            obs_name = _action_name(e.get("observation"))
            extras = e.get("extras") if isinstance(e.get("extras"), dict) else {}
            text = blocks_text(e.get("content")) or (e.get("message") or "")
            ok = None
            if isinstance(extras.get("exit_code"), int):
                ok = extras["exit_code"] == 0
            elif isinstance(e.get("success"), bool):
                ok = e["success"]
            elif obs_name in ("error", "errorobservation", "agenterrorobservation"):
                ok = False
            events.append(Event("tool_result", ts=ts, text=text, ok=ok,
                                call_id=call_for_id.get(e.get("cause")) or ""))
            continue
        if "action" in e:
            name = _action_name(e.get("action"))
            args = e.get("args") if isinstance(e.get("args"), dict) else {}
            thought = args.get("thought") or e.get("message") or ""
            if name in ("message", "messageaction"):
                # a chat turn, not a tool call
                text = args.get("content") or e.get("message") or ""
                events.append(Event("user" if src == "user" else "assistant", ts=ts, text=str(text)))
                continue
            if isinstance(thought, str) and thought.strip():
                events.append(Event("reasoning", ts=ts, text=thought))
            args = {k: v for k, v in args.items() if k != "thought"}
            events.append(Event("tool_call", ts=ts, call_id=_call_id(e), name=name or "action", args=args))
            n += 1
            continue
        if isinstance(e.get("message"), str) and e["message"].strip():
            events.append(Event("user" if src == "user" else "assistant", ts=ts, text=e["message"]))
    return events, version


def _call_id(e: dict) -> str:
    meta = e.get("tool_call_metadata") if isinstance(e.get("tool_call_metadata"), dict) else {}
    for v in (e.get("tool_call_id"), meta.get("tool_call_id"), meta.get("function_name")):
        if isinstance(v, str) and v:
            return v
    eid = e.get("id")
    return f"oh_{eid}" if eid is not None else ""


def _outcome_from_row(row: dict) -> tuple[Optional[bool], str]:
    tr = row.get("test_result")
    if isinstance(tr, dict):
        rep = tr.get("report")
        if isinstance(rep, dict) and isinstance(rep.get("resolved"), bool):
            return rep["resolved"], "OpenHands test_result.report.resolved (SWE-bench evaluation)"
        if isinstance(tr.get("resolved"), bool):
            return tr["resolved"], "OpenHands test_result.resolved"
    rep = row.get("report")
    if isinstance(rep, dict) and isinstance(rep.get("resolved"), bool):
        return rep["resolved"], "OpenHands report.resolved"
    if isinstance(row.get("resolved"), bool):
        return row["resolved"], "OpenHands resolved flag"
    return None, ""


def _session_from_row(row: dict, path: str, idx: int) -> Optional[Session]:
    hist = row.get("history")
    raw = hist if isinstance(hist, list) else _events_list(row)
    if not raw:
        return None
    events, version = _decode_events([e for e in raw if isinstance(e, dict)])
    if not events:
        return None
    inst = row.get("instance_id") or row.get("instance") or ""
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    model = ""
    for k in ("model", "model_name", "llm_model"):
        if isinstance(row.get(k), str) and row[k]:
            model = row[k]
            break
    if not model and isinstance(row.get("metadata"), dict):
        md = row["metadata"]
        llm = md.get("llm_config") if isinstance(md.get("llm_config"), dict) else {}
        model = llm.get("model") or md.get("model") or ""
        version = version or str(md.get("openhands_version") or md.get("agent_version") or "")
    hp, why = _outcome_from_row(row)
    err = row.get("error") if isinstance(row.get("error"), str) else ""
    sess = Session(source=NAME, session_id=str(inst or row.get("session_id") or f"{os.path.basename(path)}#{idx}"),
                   path=path, model=str(model or ""), cwd=str(row.get("cwd") or ""),
                   agent="openhands", agent_version=str(version or ""), events=events,
                   task_id_hint=str(inst or ""), hidden_pass=hp, outcome_source=why,
                   exit_status=err or ("submitted" if hp is not None else ""),
                   patch=str(row.get("git_patch") or row.get("model_patch") or ""),
                   extra={"tool_overrides": TOOL_OVERRIDES,
                          "agent_class": row.get("agent_class") or (row.get("metadata") or {}).get("agent_class", ""),
                          "metrics_cost": metrics.get("accumulated_cost")})
    return sess


def parse_file(path: str) -> list[Session]:
    if path.endswith(".jsonl"):
        out = []
        for i, row in enumerate(read_jsonl(path)):
            s = _session_from_row(row, path, i)
            if s:
                out.append(s)
        return out
    data = _load(path)
    raw = _events_list(data)
    if raw is None:
        return []
    if isinstance(data, dict) and (data.get("history") or data.get("instance_id")):
        s = _session_from_row(data, path, 0)
        return [s] if s else []
    events, version = _decode_events(raw)
    if not events:
        return []
    sid = os.path.splitext(os.path.basename(path))[0]
    sess = Session(source=NAME, session_id=sid, path=path, model="", cwd="",
                   agent="openhands", agent_version=version, events=events,
                   extra={"tool_overrides": TOOL_OVERRIDES})
    sess.hidden_pass, sess.outcome_source = sidecar_outcome(path, sid)
    return [sess]


def sessions(path: str) -> Iterator[Session]:
    if os.path.isdir(path):
        for dp, dn, fn in os.walk(path):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in sorted(fn):
                p = os.path.join(dp, f)
                if f.endswith((".json", ".jsonl")) and sniff(p) >= 0.5:
                    yield from parse_file(p)
        return
    yield from parse_file(path)
