"""Letta's *Trajectory* v1 interchange format -> lab ledger.

Spec source (checked, not guessed): https://www.letta.com/blog/trajectory and the JSON
Schema in the reference repository,
`https://raw.githubusercontent.com/letta-ai/trajectory/main/schema/trajectory-v1.schema.json`
(`$id: https://letta.ai/schemas/trajectory/v1.json`, "Normalized trajectory (v1)"), which
this module was written against. A copy of the record definitions, verbatim from that
schema:

    top level : a JSON *array* of records, minItems 1
    meta      : {role:"meta", source:str}                      + optional cwd, git_branch, model
    system    : {role:"system", content:str, timestamp}
    observation:{role:"observation", content:str, timestamp}   environment feedback with no owning call
    user      : {role:"user", content:str, timestamp}
    reasoning : {role:"reasoning", content:str, timestamp}
    assistant : {role:"assistant", content:str|null, timestamp} + optional tool_calls[]
                (content MUST be null when tool_calls is present, a non-empty string otherwise)
    tool_call : {id:str, name:str, args:str}                   args is a *stringified JSON object*
    tool      : {role:"tool", tool_call_id:str, content:str, timestamp} + optional ok:bool

`ok` is present only when the source exposed an authoritative structured outcome
(Claude Code `is_error`, OpenHands/Cursor `is_error`, Copilot CLI `success`, ...); the
reference implementation never infers it from result text and neither do we.

Accepted containers, because tools serialise this format three ways in the wild:
  1. a bare JSON array of records                          (the schema's own shape)
  2. `{"records": [...], "diagnostics": [...]}`             (the `normalizeTranscript` result)
  3. JSONL, one record per line

Bridging native formats through the reference implementation
------------------------------------------------------------
`pip install agent-trajectory` (PyPI name; imports as `trajectory`) is the official Python
wrapper. It shells out to a bundled Node runtime (Node >= 20) that contains the canonical
TypeScript adapters, so when it is installed we can also accept *native* Claude Code,
Codex, Letta Code, Cursor, Droid, Gemini CLI, OpenCode, OpenHands, pi, OMP, OpenClaw,
Copilot CLI and ATIF transcripts through it — `normalize_via_package(path, source)`. That
is optional: the platform's own `claude_code.py` / `codex.py` / `openhands.py` adapters do
not need it, and this module's direct reader does not need it either. It is imported
lazily and a missing package (or missing Node) raises a clear error.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator, Optional

from .common import Event, Session, read_jsonl

NAME = "trajectory"
DESCRIPTION = "Letta Trajectory v1 records (array / {records:[…]} / JSONL) — the cross-harness interchange format"
PATTERNS = ["*.json (array of {role: meta|user|reasoning|assistant|tool} records)",
            "*.trajectory.json", "*.jsonl (one v1 record per line)"]

ROLES = {"meta", "system", "observation", "user", "reasoning", "assistant", "tool"}

#: sources the bundled reference runtime can normalize (Letta trajectory v0.3)
PACKAGE_SOURCES = ["atif", "claude-code", "codex", "copilot-cli", "cursor", "droid", "gemini-cli",
                   "hermes", "letta-code", "omp", "openclaw", "opencode", "openhands", "pi"]


def _load_records(path: str) -> Optional[list[dict]]:
    """Accept all three containers: a bare array, a {"records": [...]} envelope, or JSONL.

    A JSONL file also starts with `{`, so we try whole-file JSON first and fall back to
    line-by-line rather than deciding from the first character.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    if data is not None:
        if isinstance(data, dict):
            data = data.get("records")
        if isinstance(data, list):
            recs = [r for r in data if isinstance(r, dict)]
            return recs or None
        return None
    rows = read_jsonl(path)
    return rows or None


def sniff(path: str) -> float:
    if os.path.isdir(path):
        return 0.0
    if not (path.endswith(".json") or path.endswith(".jsonl")):
        return 0.0
    recs = _load_records(path)
    if not recs:
        return 0.0
    roles = [r.get("role") for r in recs if isinstance(r.get("role"), str)]
    if not roles:
        return 0.0
    known = sum(1 for r in roles if r in ROLES)
    frac = known / len(recs)
    if frac < 0.8:
        return 0.0
    score = 0.55 + 0.25 * frac
    if recs and recs[0].get("role") == "meta" and isinstance(recs[0].get("source"), str):
        score += 0.2
    # a tool_call whose args is a *string* is the format's signature
    if any(isinstance(tc.get("args"), str) for r in recs for tc in (r.get("tool_calls") or []) if isinstance(tc, dict)):
        score += 0.1
    return min(1.0, score)


def _args_obj(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {"input": d}
        except json.JSONDecodeError:
            return {"input": v}
    return {}


def records_to_session(recs: list[dict], path: str = "", session_id: str = "") -> Optional[Session]:
    """v1 records -> Session. `meta` supplies source/cwd/model; nothing else is assumed."""
    meta = next((r for r in recs if r.get("role") == "meta"), {})
    events: list[Event] = []
    system_prompt = ""
    for r in recs:
        role = r.get("role")
        ts = r.get("timestamp") or ""
        if role == "meta":
            continue
        if role == "system":
            system_prompt = system_prompt or (r.get("content") or "")
            continue
        if role in ("user", "reasoning"):
            events.append(Event(role, ts=ts, text=r.get("content") or ""))
        elif role == "observation":
            events.append(Event("observation", ts=ts, text=r.get("content") or ""))
        elif role == "assistant":
            calls = r.get("tool_calls")
            if isinstance(calls, list) and calls:
                for tc in calls:
                    if not isinstance(tc, dict):
                        continue
                    events.append(Event("tool_call", ts=ts, call_id=tc.get("id") or "",
                                        name=tc.get("name") or "", args=_args_obj(tc.get("args")),
                                        model=meta.get("model") or ""))
            elif isinstance(r.get("content"), str) and r["content"].strip():
                events.append(Event("assistant", ts=ts, text=r["content"], model=meta.get("model") or ""))
        elif role == "tool":
            ok = r["ok"] if isinstance(r.get("ok"), bool) else None
            events.append(Event("tool_result", ts=ts, call_id=r.get("tool_call_id") or "",
                                text=r.get("content") or "", ok=ok))
    if not events:
        return None
    upstream = meta.get("source") or "unknown"
    sid = session_id or os.path.splitext(os.path.basename(path))[0] or upstream
    return Session(source=NAME, session_id=sid, path=path,
                   model=meta.get("model") or "", cwd=meta.get("cwd") or "",
                   agent=str(upstream), agent_version="", events=events,
                   system_prompt=system_prompt,
                   extra={"trajectory_source": upstream, "git_branch": meta.get("git_branch") or "",
                          "spec": "letta trajectory v1"})


def parse_file(path: str) -> Optional[Session]:
    recs = _load_records(path)
    if not recs:
        return None
    return records_to_session(recs, path=path)


# ------------------------------------------------------------------ optional: the reference runtime
def package_available() -> bool:
    try:
        import trajectory  # noqa: F401
    except Exception:
        return False
    return True


def normalize_via_package(path: str, source: str) -> Session:
    """Normalize a *native* transcript through the reference implementation.

    Requires `pip install agent-trajectory` (imports as `trajectory`) and Node >= 20 on
    PATH; the wrapper spawns the vendored `trajectory-cli.mjs`. Raises RuntimeError with
    an actionable message when either is missing.
    """
    try:
        import trajectory as _t
    except ImportError as e:                                       # pragma: no cover - env dependent
        raise RuntimeError("the Trajectory reference runtime is not installed: "
                           "pip install agent-trajectory   (it also needs Node >= 20 on PATH)") from e
    if source not in PACKAGE_SOURCES:
        raise RuntimeError(f"trajectory runtime has no adapter for {source!r}; known: {', '.join(PACKAGE_SOURCES)}")
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    try:
        res = _t.normalize_transcript(source=source, transcript=raw)
    except Exception as e:                                          # pragma: no cover - env dependent
        raise RuntimeError(f"trajectory runtime failed on {os.path.basename(path)}: {type(e).__name__}: {e}") from e
    recs = res.get("records") if isinstance(res, dict) else res
    sess = records_to_session(list(recs or []), path=path)
    if sess is None:
        raise RuntimeError(f"trajectory runtime produced no records for {os.path.basename(path)}")
    sess.extra["diagnostics"] = [d.get("code") for d in (res.get("diagnostics") or []) if isinstance(d, dict)]
    sess.extra["normalized_by"] = "agent-trajectory (letta-ai/trajectory reference runtime)"
    return sess


def sessions(path: str) -> Iterator[Session]:
    if os.path.isdir(path):
        for dp, dn, fn in os.walk(path):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in sorted(fn):
                if f.endswith((".json", ".jsonl")):
                    p = os.path.join(dp, f)
                    if sniff(p) >= 0.5:
                        s = parse_file(p)
                        if s:
                            yield s
        return
    s = parse_file(path)
    if s:
        yield s
