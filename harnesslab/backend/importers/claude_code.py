"""Claude Code session JSONL -> lab ledger.

Input contract (`~/.claude/projects/<project-slug>/<sessionId>.jsonl`, one JSON object
per line). What we rely on, verified against the reference decoder in
letta-ai/trajectory (`src/adapters/claude-code/`, bundled in the `agent-trajectory`
wheel as `trajectory/_vendor/trajectory-cli.mjs`) and against real session files:

  record.type        "user" | "assistant" | "summary" | plus transport rows we skip
                     ("progress", "queue-operation", "file-history-snapshot", "summary",
                      "system", "pr-link", "last-prompt", "custom-title", "ai-title",
                      "agent-name", "permission-mode", "attachment", "mode")
  record.uuid        line identity; record.parentUuid links a line to its predecessor
  record.timestamp   ISO-8601
  record.cwd         working directory (present on conversational rows)
  record.version     the Claude Code CLI version -> harness version
  record.sessionId   session identity
  record.gitBranch   branch, when known
  record.isSidechain true on subagent (Task) rows
  record.message     an Anthropic Messages API message:
      .role, .model (assistant only), .usage (assistant only),
      .content: str | [ {type:"text",text} | {type:"thinking",thinking}
                      | {type:"tool_use",id,name,input}
                      | {type:"tool_result",tool_use_id,is_error,content} ]

Tool mapping (see common.TOOL_MAP): Bash -> bash, Read/NotebookRead -> read_file,
Edit/MultiEdit/NotebookEdit -> edit_file, Write -> write_file, Grep/Glob/LS -> list_files,
Task -> bash (an opaque subagent unit), WebFetch/WebSearch/TodoWrite/mcp__* -> bash.

Sidechain handling follows the reference decoder: if the file contains *only* sidechain
conversational rows it is a standalone subagent transcript and we import it as the
conversation; otherwise sidechain rows are dropped so a Task subagent's steps are not
double-counted inside its parent.

Outcomes: a Claude Code session carries no verdict, so `hidden_pass` is None unless a
sidecar `results.json` / `report.json` next to the file supplies one (see common ­+
`common.sidecar_outcome`). `visible_pass` is the usual "the last test the agent ran reported
passing".
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterator, Optional

from .common import Event, Session, blocks_text, peek_jsonl, sidecar_outcome

NAME = "claude_code"
DESCRIPTION = "Claude Code session JSONL (~/.claude/projects/<project>/<session>.jsonl)"
PATTERNS = ["*.jsonl (records with type=user/assistant and message.content blocks)"]

TRANSPORT_TYPES = {"progress", "queue-operation", "file-history-snapshot", "summary", "system",
                   "pr-link", "last-prompt", "custom-title", "ai-title", "agent-name",
                   "permission-mode", "attachment", "mode", "x-anthropic-hook"}


def sniff(path: str) -> float:
    """Confidence that `path` is a Claude Code session file (0..1)."""
    if os.path.isdir(path):
        return 0.0
    if not path.endswith(".jsonl"):
        return 0.0
    rows = peek_jsonl(path, 40)
    if not rows:
        return 0.0
    conv = [r for r in rows if r.get("type") in ("user", "assistant") and isinstance(r.get("message"), dict)]
    if not conv:
        return 0.0
    score = 0.35
    if any("uuid" in r and "parentUuid" in r for r in rows):
        score += 0.3
    if any("sessionId" in r for r in rows):
        score += 0.15
    if any("version" in r and "cwd" in r for r in rows):
        score += 0.15
    if any(r.get("type") == "summary" for r in rows):
        score += 0.05
    return min(1.0, score)


def _tool_use_facts(tur) -> dict:
    """The structured outcome Claude Code records beside a tool result (`toolUseResult`).
    Only what the capture writer uses; never the whole record, never `originalFile`."""
    if not isinstance(tur, dict):
        return {}
    out: dict = {}
    sp = tur.get("structuredPatch")
    if isinstance(sp, list) and sp:
        out["structured_patch"] = [h for h in sp if isinstance(h, dict)]
    if isinstance(tur.get("filePath"), str) and tur["filePath"]:
        out["file_path"] = tur["filePath"]
    if tur.get("interrupted") is True:
        out["interrupted"] = True
    if isinstance(tur.get("timedOutAfterMs"), (int, float)) and tur["timedOutAfterMs"]:
        out["timed_out_ms"] = int(tur["timedOutAfterMs"])
    if isinstance(tur.get("stderr"), str) and tur["stderr"].strip():
        out["stderr"] = tur["stderr"][:2000]
    if isinstance(tur.get("agentId"), str) and tur["agentId"]:
        out["child_agent_id"] = tur["agentId"]
    return out


def _content_events(rec: dict, ts: str, model: str) -> Iterator[Event]:
    msg = rec.get("message") or {}
    content = msg.get("content")
    usage = msg.get("usage") or {}
    use = {"input_tokens": int(usage.get("input_tokens") or 0),
           "output_tokens": int(usage.get("output_tokens") or 0),
           "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
           "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0)} if usage else {}
    uuid = rec.get("uuid") if isinstance(rec.get("uuid"), str) else ""

    def rid(i: int) -> str:
        return f"{uuid}:{i}" if uuid else ""

    if rec.get("type") == "user":
        if isinstance(content, str):
            if content.strip():
                yield Event("user", ts=ts, text=content, record_id=rid(0))
            return
        blocks = content if isinstance(content, list) else []
        n_results = sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result")
        facts = _tool_use_facts(rec.get("toolUseResult")) if n_results == 1 else {}
        texts, first_text = [], None
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_result":
                ok = (not b["is_error"]) if isinstance(b.get("is_error"), bool) else None
                yield Event("tool_result", ts=ts, text=blocks_text(b.get("content")),
                            call_id=b.get("tool_use_id") or "", ok=ok, raw=dict(facts), record_id=rid(i))
            elif b.get("type") == "text" and isinstance(b.get("text"), str):
                texts.append(b["text"])
                first_text = i if first_text is None else first_text
            elif b.get("type") == "image":
                texts.append("[image]")
                first_text = i if first_text is None else first_text
        if texts:
            yield Event("user", ts=ts, text="\n".join(texts), record_id=rid(first_text))
        return
    # assistant
    req = rec.get("requestId") if isinstance(rec.get("requestId"), str) else ""
    if not req and isinstance(msg.get("id"), str):
        req = msg["id"]
    if isinstance(content, str):
        if content.strip():
            yield Event("assistant", ts=ts, text=content, model=model, usage=use, record_id=rid(0), request_id=req)
        return
    for i, b in enumerate(content if isinstance(content, list) else []):
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "thinking":
            yield Event("reasoning", ts=ts, text=b.get("thinking") or "", model=model,
                        record_id=rid(i), request_id=req)
        elif t == "text":
            yield Event("assistant", ts=ts, text=b.get("text") or "", model=model, usage=use,
                        record_id=rid(i), request_id=req)
        elif t == "tool_use":
            yield Event("tool_call", ts=ts, call_id=b.get("id") or "", name=b.get("name") or "",
                        args=b.get("input") if isinstance(b.get("input"), dict) else {"input": b.get("input")},
                        model=model, usage=use, record_id=rid(i), request_id=req)


def _iter_rows(path: str, limit: Optional[int] = None) -> Iterator[dict]:
    """Decoded JSON objects one at a time, never materialising the file (a 269 MB session held as a
    list of dicts measured 462 MB RSS). With `limit`, stops at that many bytes, so repeated passes over
    a session that is still being written all see exactly the same rows."""
    consumed = 0
    with open(path, "rb") as f:
        for raw in f:
            consumed += len(raw)
            if limit is not None and consumed > limit:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(v, dict):
                yield v


def _is_conv(r: dict) -> bool:
    return r.get("type") in ("user", "assistant") and isinstance(r.get("message"), dict)


def parse_file(path: str) -> Optional[Session]:
    # Measure the size once: the session may still be growing (live backfill), and both passes
    # below must see the same bytes or a row appended between them gets silently absorbed.
    size = os.path.getsize(path)

    # pass 1: the whole-file sidechain predicate, without keeping rows
    n_conv = n_side = 0
    for r in _iter_rows(path, size):
        if _is_conv(r):
            n_conv += 1
            if r.get("isSidechain") is True:
                n_side += 1
    if not n_conv:
        return None
    standalone_sidechain = n_side == n_conv

    # pass 2: events and session facts
    events: list[Event] = []
    cwd, version, session_id, model, branch, entrypoint, agent_id = "", "", "", "", "", "", ""
    modes: list[str] = []
    unknown: dict[str, int] = {}
    hit_max_tokens = False
    for r in _iter_rows(path, size):
        t = r.get("type")
        msg = r.get("message") if isinstance(r.get("message"), dict) else None
        if msg is not None and "max_tokens" in (msg.get("stop_reason") or ""):
            hit_max_tokens = True
        if t == "permission-mode" and isinstance(r.get("permissionMode"), str) and r["permissionMode"] not in modes:
            modes.append(r["permissionMode"])
        if t not in TRANSPORT_TYPES and t not in ("user", "assistant"):
            unknown[str(t)] = unknown.get(str(t), 0) + 1
        if t in TRANSPORT_TYPES:
            continue
        if r.get("isSidechain") is True and not standalone_sidechain:
            continue                      # subagent steps live in their own transcript
        cwd = cwd or (r.get("cwd") or "")
        version = version or (r.get("version") or "")
        session_id = session_id or (r.get("sessionId") or "")
        branch = branch or (r.get("gitBranch") or "")
        entrypoint = entrypoint or (r.get("entrypoint") or "")
        if standalone_sidechain:
            agent_id = agent_id or (r.get("agentId") or "")
        if t not in ("user", "assistant") or msg is None:
            continue
        m = msg.get("model")
        if isinstance(m, str) and m and not model:
            model = m
        events += list(_content_events(r, r.get("timestamp") or "", m if isinstance(m, str) else ""))
    if not events:
        return None
    sid = agent_id or session_id or os.path.splitext(os.path.basename(path))[0]
    sess = Session(source=NAME, session_id=sid, path=path, model=model, cwd=cwd,
                   agent="claude-code", agent_version=version, events=events,
                   declared_tools=[], system_prompt="", task_id_hint="",
                   extra={"git_branch": branch, "standalone_sidechain": standalone_sidechain,
                          "permission_modes": modes, "entrypoint": entrypoint,
                          "unknown_record_types": unknown,
                          "parent_session_id": session_id if standalone_sidechain else ""})
    hp, why = sidecar_outcome(path, sid)
    sess.hidden_pass, sess.outcome_source = hp, why
    if hit_max_tokens:
        sess.exit_status = "max_tokens"       # Claude Code writes no exit status; this is all the trace shows
    return sess


def sessions(path: str) -> Iterator[Session]:
    """One Session per session file. A directory is walked for `*.jsonl`."""
    if os.path.isdir(path):
        for dp, dn, fn in os.walk(path):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in sorted(fn):
                if f.endswith(".jsonl"):
                    p = os.path.join(dp, f)
                    if sniff(p) >= 0.5:
                        s = parse_file(p)
                        if s:
                            yield s
        return
    s = parse_file(path)
    if s:
        yield s
