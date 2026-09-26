"""Qwen Code chat JSONL -> lab ledger.

(~/.qwen/projects/<path-slug>/chats/<uuid>.jsonl, with subagents/ and memory/ siblings.)

A Claude Code ENVELOPE around a GEMINI message body, which is exactly why claude_code.sniff
scored 0.95 on it and then parsed it to nothing: the envelope is
type/uuid/parentUuid/sessionId/cwd/gitBranch/version/timestamp, but the body is `message.parts`
(text | text+thought | functionCall) with role 'model' and `usageMetadata`, not
`message.content`. `provenance` (system | tool_result | assistant_output | real_user) is on every
row and is the discriminator against Claude Code.

Roughly 70% of a real chat file is type='system' telemetry (14,220 of 20,216 records over 24
files). Those rows are TRANSPORT, not conversation: they are named in TRANSPORT_SUBTYPES and
skipped, and any subtype not named there is COUNTED into unknown_record_types so a new Qwen build
shows up as a number rather than as silence.

Shapes below are measured on that corpus, not assumed:
  * `subtype` is a TOP-LEVEL key of a system row, beside `systemPayload` -- 14,220 of 14,220 carry
    it there and ZERO carry `systemPayload.subtype`.
  * a reasoning part is `{"text": ..., "thought": true}` -- `thought` is a BOOLEAN FLAG on a text
    part (2,613 of 2,613), never the reasoning string itself.
  * the edited file's path is `toolCallResult.resultDisplay.filePath`/`.fileName`;
    `toolCallResult.filePath` does not exist (0 of 3,098).
  * `model` is a real top-level key on every assistant row, and this corpus holds five distinct
    values -- the model is read, never hardcoded, because (model, harness) is the unit of compare.
  * `resultDisplay` is a STRING 2,217 times and a dict 880 times; `error` is a dict, not a string.
"""
from __future__ import annotations

import os
from typing import Iterator, Optional

from .common import Event, Session, hunks_from_line_counts, peek_jsonl, read_jsonl

NAME = "qwen_code"
DESCRIPTION = "Qwen Code chat JSONL (~/.qwen/projects/<project>/chats/<uuid>.jsonl)"
PATTERNS = ["*.jsonl (records with provenance and message.parts)"]

#: native tool name -> lab tool. The first block is what this corpus actually emits; the second is
#: the upstream Gemini-CLI tool surface a differently-configured Qwen build still ships, kept so a
#: rename does not drop a call into the unnamed-tool fallback.
TOOL_OVERRIDES = {
    # measured on the real corpus
    "run_shell_command": "bash", "read_file": "read_file", "write_file": "write_file",
    "edit": "edit_file", "grep_search": "list_files", "list_directory": "list_files",
    "glob": "list_files", "agent": "bash", "list_agents": "bash", "skill": "bash",
    "todo_write": "bash", "task_stop": "bash", "ask_user_question": "bash",
    "web_fetch": "bash", "web_search": "bash", "tool_search": "bash",
    "display_image": "bash", "zoom_image": "bash", "artifact": "bash", "send_message": "bash",
    # upstream Gemini-CLI names, for a build that still uses them
    "replace": "edit_file", "search_file_content": "list_files", "read_many_files": "read_file",
    "save_memory": "bash", "google_web_search": "bash", "shell": "bash",
}

#: system subtypes that carry no conversation. Anything else is counted, never dropped silently.
TRANSPORT_SUBTYPES = frozenset({"ui_telemetry", "attribution_snapshot", "file_history_snapshot",
                                "slash_command", "mid_turn_user_message", "notification",
                                "at_command"})

#: an approval decision that means the boundary was actually removed, not merely not hit
PERMISSIVE_DECISIONS = frozenset({"auto_accept", "yolo", "accept_always"})


def _is_subagent_row(r: dict) -> bool:
    """A row from <project>/subagents/<parent-session-id>/<uuid>.jsonl.

    Qwen Code writes subagent work to sibling files rather than inline, the way Claude Code does
    with isSidechain rows. They are child work, never independent runs: on the corpus this was
    measured against, 160 such files carried only 7 distinct sessionIds and every one named a
    parent chat.
    """
    return bool(isinstance(r, dict) and r.get("isSidechain") and (r.get("agentId") or r.get("agentName")))


def sniff(path: str) -> float:
    if os.path.isdir(path) or not path.endswith(".jsonl"):
        return 0.0
    rows = peek_jsonl(path, 40)
    if not rows:
        return 0.0
    score = 0.0
    if any("provenance" in r for r in rows):
        score += 0.6
    elif any(_is_subagent_row(r) for r in rows):
        # Subagent sessions carry the same message.parts body but no `provenance` -- that key is
        # written only on the chats variant. Without this branch they top out at 0.4, under the
        # registry floor, so a file the parser reads perfectly resolves to no adapter at all.
        score += 0.6
    if any(isinstance(r.get("message"), dict) and "parts" in r["message"] for r in rows):
        score += 0.3
    if any("usageMetadata" in r or "toolCallResult" in r for r in rows):
        score += 0.1
    return min(1.0, score)


def _usage(r: dict) -> dict:
    u = r.get("usageMetadata")
    if not isinstance(u, dict) or not u:
        return {}
    return {"input_tokens": int(u.get("promptTokenCount") or 0),
            "output_tokens": int(u.get("candidatesTokenCount") or 0),
            "cache_read_input_tokens": int(u.get("cachedContentTokenCount") or 0),
            "cache_creation_input_tokens": 0}


def _result_text(tcr: dict) -> str:
    """The observation. `resultDisplay` is the rendered output when it is a string; when the call
    failed, `error` is a DICT and its `message` is the useful part -- str(dict) is not."""
    err = tcr.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("type") or ""
        if msg:
            return str(msg)[:4000]
    elif isinstance(err, str) and err:
        return err[:4000]
    rd = tcr.get("resultDisplay")
    if isinstance(rd, str):
        return rd[:4000]
    if isinstance(rd, dict):
        for k in ("text", "result", "executionSummary"):
            if isinstance(rd.get(k), str) and rd[k]:
                return rd[k][:4000]
    return ""


def parse_file(path: str) -> Optional[Session]:
    rows = read_jsonl(path)
    if not rows:
        return None
    is_subagent = any(_is_subagent_row(r) for r in rows)
    events: list[Event] = []
    unknown: dict = {}
    decisions: list[str] = []
    cwd = session_id = version = branch = model = ""
    for r in rows:
        if not isinstance(r, dict):
            continue
        rtype = r.get("type") or ""
        uid = str(r.get("uuid") or "")
        ts = r.get("timestamp") or ""
        session_id = session_id or str(r.get("sessionId") or "")
        cwd = cwd or str(r.get("cwd") or "")
        version = version or str(r.get("version") or "")
        branch = branch or str(r.get("gitBranch") or "")
        if isinstance(r.get("model"), str) and r["model"] and not model:
            model = r["model"]          # first observed wins, as claude_code does
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}
        parts = msg.get("parts") if isinstance(msg.get("parts"), list) else []

        if rtype == "user":
            if r.get("provenance") not in (None, "real_user"):
                continue
            text = "".join(b.get("text") or "" for b in parts if isinstance(b, dict))
            if text.strip():
                events.append(Event("user", ts=ts, text=text, record_id=f"{uid}:0"))
            continue

        if rtype == "assistant":
            usage = _usage(r)
            row_model = r.get("model") if isinstance(r.get("model"), str) else ""
            row_model = row_model or model
            for i, b in enumerate(parts):
                if not isinstance(b, dict):
                    continue
                # `thought` is a BOOLEAN flag on a text part in this corpus. An older build that
                # put the reasoning string there directly is still honoured, second branch.
                if b.get("thought") is True and isinstance(b.get("text"), str) and b["text"].strip():
                    events.append(Event("reasoning", ts=ts, text=b["text"], model=row_model,
                                        record_id=f"{uid}:{i}", request_id=uid, usage=usage))
                    usage = {}          # one model call per row: the first carrier keeps it
                elif isinstance(b.get("thought"), str) and b["thought"].strip():
                    events.append(Event("reasoning", ts=ts, text=b["thought"], model=row_model,
                                        record_id=f"{uid}:{i}", request_id=uid, usage=usage))
                    usage = {}
                elif isinstance(b.get("text"), str) and b["text"].strip():
                    events.append(Event("assistant", ts=ts, text=b["text"], model=row_model,
                                        record_id=f"{uid}:{i}", request_id=uid, usage=usage))
                    usage = {}
                elif isinstance(b.get("functionCall"), dict):
                    fc = b["functionCall"]
                    events.append(Event("tool_call", ts=ts, call_id=str(fc.get("id") or ""),
                                        name=str(fc.get("name") or "tool"),
                                        args=fc.get("args") if isinstance(fc.get("args"), dict) else {},
                                        model=row_model, record_id=f"{uid}:{i}", request_id=uid,
                                        usage=usage))
                    usage = {}
            continue

        if rtype == "tool_result":
            tcr = r.get("toolCallResult") if isinstance(r.get("toolCallResult"), dict) else {}
            status = tcr.get("executionStatus")
            rd = tcr.get("resultDisplay") if isinstance(tcr.get("resultDisplay"), dict) else {}
            ds = rd.get("diffStat") if isinstance(rd.get("diffStat"), dict) else None
            raw: dict = {}
            if isinstance(ds, dict):
                raw["structured_patch"] = hunks_from_line_counts(ds.get("model_added_lines") or 0,
                                                                 ds.get("model_removed_lines") or 0)
            # the edited path lives INSIDE resultDisplay; toolCallResult.filePath does not exist
            fp = rd.get("filePath") or rd.get("fileName") or tcr.get("filePath")
            if isinstance(fp, str) and fp:
                raw["file_path"] = fp
            events.append(Event("tool_result", ts=ts, call_id=str(tcr.get("callId") or ""),
                                text=_result_text(tcr),
                                ok=(status == "success") if status in ("success", "error") else None,
                                record_id=f"{uid}:0", raw=raw))
            continue

        if rtype == "system":
            sp = r.get("systemPayload") if isinstance(r.get("systemPayload"), dict) else {}
            # `subtype` is a TOP-LEVEL key of a system row, beside systemPayload -- 14,220 of 14,220
            # real rows carry it there and none carries systemPayload.subtype. The nested read is a
            # fallback only, so a future build that moves it does not silently land 70% of every
            # file in unknown_record_types.
            sub = str(r.get("subtype") or sp.get("subtype") or "")
            ev = sp.get("uiEvent") if isinstance(sp.get("uiEvent"), dict) else {}
            if ev.get("decision"):
                decisions.append(str(ev["decision"]))
            if sub not in TRANSPORT_SUBTYPES:
                key = f"system/{sub}" if sub else "system"
                unknown[key] = unknown.get(key, 0) + 1
            continue

        if rtype:
            unknown[str(rtype)] = unknown.get(str(rtype), 0) + 1

    if not events:
        return None
    return Session(source=NAME, session_id=session_id or os.path.splitext(os.path.basename(path))[0],
                   path=path, model=model or "qwen", cwd=cwd, agent="qwen-code",
                   agent_version=version, events=events, declared_tools=[], system_prompt="",
                   extra={"git_branch": branch, "decisions": sorted(set(decisions)),
                          "unknown_record_types": unknown, "tool_overrides": TOOL_OVERRIDES,
                          # Child work must stay attributable to its parent. A subagent session
                          # counted as a peer would inflate the run count against the real chats.
                          "is_subagent": is_subagent,
                          "parent_session_id": (session_id or "") if is_subagent else ""})


def sessions(path: str) -> Iterator[Session]:
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
