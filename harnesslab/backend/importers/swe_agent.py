"""Native SWE-agent `.traj` files -> lab ledger.

Input contract (one JSON object per run, written by SWE-agent as
`trajectories/<user>/<run>/<instance_id>/<instance_id>.traj`)::

    {"trajectory": [ {"action": "open foo.py",
                      "observation": "...",
                      "response": "DISCUSSION\\n...\\n```\\nopen foo.py\\n```",
                      "thought": "...",
                      "state": "{\\"open_file\\": \\"foo.py\\", \\"working_dir\\": \\"/x\\"}",
                      "execution_time": 0.3,
                      "query": [...]}, ... ],
     "history": [ {"role": "system"|"user"|"assistant", "content": ..., "agent": "main"} ],
     "info": {"exit_status": "submitted",
              "submission": "<unified diff>",
              "model_stats": {"instance_cost", "tokens_sent", "tokens_received", "api_calls"},
              "swe_agent_hash"/"swe_agent_version": ...}}

The command language ("open", "goto", "search_dir", "edit", "create", "submit", raw
shell, ...) is already mapped in `harnesslab/core/real_traj.py::classify_command`, which the
nebius importer uses; we reuse it verbatim so a `.traj` and a nebius row land on the same
tool surface and are comparable in the matrix.

The `state` blob (SWE-agent's window state) gives the *open file*, which is what an
`edit`/`insert`/`str_replace` command actually touches — better than the nebius importer's
regex guess, so we use it when present.

Outcome: `info.exit_status` is a *stopping reason*, never a verdict, so it never sets
`hidden_pass`. A verdict is taken only from `info.resolved` / `info.report.resolved` when
the trace itself carries one, or from a sibling `results.json` / `report.json`
(SWE-bench's `{"resolved": ["instance_id", ...]}` and friends). Otherwise it is None.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator, Optional

from .common import Event, Session, sidecar_outcome

NAME = "swe_agent"
DESCRIPTION = "Native SWE-agent .traj JSON (trajectory + info.exit_status/submission/model_stats)"
PATTERNS = ["*.traj", "*.traj.json", "*.json with a top-level `trajectory` list and `info`"]

TOOL_OVERRIDES = {"open": "read_file", "goto": "read_file", "scroll_up": "read_file",
                  "scroll_down": "read_file", "search_dir": "list_files", "search_file": "list_files",
                  "find_file": "list_files", "edit": "edit_file", "create": "write_file",
                  "insert": "edit_file", "str_replace": "edit_file", "submit": "submit"}


def sniff(path: str) -> float:
    if os.path.isdir(path):
        return 0.0
    if not path.endswith((".traj", ".json")):
        return 0.0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(3000)
    except OSError:
        return 0.0
    if '"trajectory"' not in head:
        return 0.0
    score = 0.5 if path.endswith(".traj") else 0.3
    if '"observation"' in head or '"exit_status"' in head or '"info"' in head:
        score += 0.35
    if '"thought"' in head or '"response"' in head or '"state"' in head:
        score += 0.2
    return min(1.0, score)


def _state(step: dict) -> dict:
    s = step.get("state")
    if isinstance(s, dict):
        return s
    if isinstance(s, str) and s.strip().startswith("{"):
        try:
            d = json.loads(s)
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _instance_from_path(path: str) -> str:
    base = os.path.basename(path)
    for suf in (".traj.json", ".traj", ".json"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    from .common import SWEBENCH_ID
    if SWEBENCH_ID.match(base):
        return base
    if SWEBENCH_ID.match(parent):
        return parent
    return base


def _outcome(info: dict, inst: str, path: str) -> tuple[Optional[bool], str]:
    for key, why in (("resolved", "SWE-agent info.resolved"),):
        if isinstance(info.get(key), bool):
            return info[key], why
    rep = info.get("report")
    if isinstance(rep, dict):
        if isinstance(rep.get("resolved"), bool):
            return rep["resolved"], "SWE-agent info.report.resolved"
        if isinstance(rep.get(inst), dict) and isinstance(rep[inst].get("resolved"), bool):
            return rep[inst]["resolved"], "SWE-agent info.report[instance].resolved"
    return sidecar_outcome(path, inst)


def parse_file(path: str) -> Optional[Session]:
    from harnesslab.core.real_traj import classify_command
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("trajectory"), list):
        return None
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    inst = _instance_from_path(path)
    hist = data.get("history") if isinstance(data.get("history"), list) else []
    system_prompt = ""
    first_user = ""
    for h in hist:
        if not isinstance(h, dict):
            continue
        c = h.get("content")
        c = c if isinstance(c, str) else json.dumps(c, default=str)[:4000] if c else ""
        if h.get("role") == "system" and not system_prompt:
            system_prompt = c
        elif h.get("role") == "user" and not first_user:
            first_user = c
    events: list[Event] = []
    if first_user:
        events.append(Event("user", text=first_user))
    open_file = ""
    for i, step in enumerate(data["trajectory"]):
        if not isinstance(step, dict):
            continue
        st = _state(step)
        open_file = str(st.get("open_file") or open_file or "")
        thought = step.get("thought") or ""
        if not isinstance(thought, str):
            thought = ""
        if not thought and isinstance(step.get("response"), str):
            thought = step["response"].split("```")[0].strip()
        if thought.strip():
            events.append(Event("reasoning", text=thought[:4000]))
        cmd = step.get("action")
        cmd = cmd.strip() if isinstance(cmd, str) else ""
        if not cmd:
            continue
        kind = classify_command(cmd)
        head = re.split(r"\s+", cmd, maxsplit=1)[0]
        args: dict[str, Any] = {"command": cmd[:400]}
        if kind == "view":
            m = re.match(r"^(open|cat|head|tail|less)\s+(\S+)", cmd)
            if m:
                open_file = m.group(2)
            args = {"path": open_file or (m.group(2) if m else cmd)}
            head = "open"
        elif kind == "edit":
            m = re.match(r"^create\s+(\S+)", cmd)
            if m:
                open_file = m.group(1)
                head = "create"
                args = {"path": open_file, "content": ""}
            else:
                head = head if head in TOOL_OVERRIDES else "edit"
                args = {"path": open_file or "(open file)", "old_str": cmd[:200], "new_str": ""}
        elif kind == "search":
            args = {"path": st.get("working_dir") or ".", "pattern": cmd[:200]}
            head = head if head in TOOL_OVERRIDES else "search_dir"
        elif kind == "submit":
            head = "submit"
            args = {"summary": thought[:200]}
        else:
            head = "bash"
        events.append(Event("tool_call", call_id=f"traj_{i}", name=head, args=args))
        obs = step.get("observation")
        obs = obs if isinstance(obs, str) else (json.dumps(obs, default=str) if obs else "")
        events.append(Event("tool_result", call_id=f"traj_{i}", text=obs[:4000]))
    if not events:
        return None
    stats = info.get("model_stats") if isinstance(info.get("model_stats"), dict) else {}
    model = str(info.get("model") or info.get("model_name") or data.get("model_name") or "")
    version = str(info.get("swe_agent_version") or info.get("version") or
                  (info.get("swe_agent_hash") or "")[:7] or "")
    hp, why = _outcome(info, inst, path)
    sess = Session(source=NAME, session_id=inst, path=path, model=model, cwd=str(info.get("cwd") or ""),
                   agent="swe-agent", agent_version=version, events=events,
                   system_prompt=system_prompt, task_id_hint=inst,
                   hidden_pass=hp, outcome_source=why,
                   exit_status=str(info.get("exit_status") or ""),
                   patch=str(info.get("submission") or info.get("model_patch") or ""),
                   extra={"tool_overrides": TOOL_OVERRIDES,
                          "api_calls": stats.get("api_calls"), "instance_cost": stats.get("instance_cost"),
                          "tokens_sent": stats.get("tokens_sent"), "tokens_received": stats.get("tokens_received")})
    return sess


def sessions(path: str) -> Iterator[Session]:
    if os.path.isdir(path):
        for dp, dn, fn in os.walk(path):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in sorted(fn):
                p = os.path.join(dp, f)
                if f.endswith((".traj", ".json")) and sniff(p) >= 0.5:
                    s = parse_file(p)
                    if s:
                        yield s
        return
    s = parse_file(path)
    if s:
        yield s
