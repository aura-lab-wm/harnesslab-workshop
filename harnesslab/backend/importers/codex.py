"""OpenAI Codex CLI rollout JSONL -> lab ledger.

Input contract (`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`, one JSON object
per line). Field names verified against the reference decoder in letta-ai/trajectory
(`src/adapters/codex/`, bundled in the `agent-trajectory` wheel as
`trajectory/_vendor/trajectory-cli.mjs`), which is the only machine-readable spec we
could find for this format:

  {"type": "session_meta",  "timestamp": ..., "payload": {"id", "timestamp", "cwd",
                                                          "git": {"branch", ...},
                                                          "originator", "cli_version",
                                                          "base_instructions": {"text", ...}}}
  {"type": "turn_context",  "payload": {"cwd", "model", "approval_policy",
                                        "sandbox_policy": {"type", ...}, "effort", "summary"}}
  {"type": "event_msg",     "payload": {"type": "agent_reasoning", "text": ...}}
  {"type": "event_msg",     "payload": {"type": "token_count", "info": {"last_token_usage", ...}}}
  {"type": "response_item", "payload": {"type": "message", "role", "content": [blocks]}}
  {"type": "response_item", "payload": {"type": "function_call", "call_id", "name",
                                        "arguments": "<json string>"}}
  {"type": "response_item", "payload": {"type": "function_call_output", "call_id",
                                        "output": str | {"output": str, ...} | [blocks]}}
  plus "custom_tool_call" / "custom_tool_call_output", "web_search_call",
       "tool_search_call" / "tool_search_output", "reasoning".

Three generations live side by side on disk, and the exit code -- the one fact that says whether a
tool call worked -- sits somewhere different in each. `function_call_output.output` never carries
it: over 1,423 real rollouts that field is a str 32,187 times and a list 4,282 times and a dict
ZERO times, so the dict branch of `_output_text` is dead code and `ok` was None for every Codex
tool result ever imported. Where the code actually is:

  gen A (2026-01..03, 07)  nowhere. There is no exit code in the file, so `ok` stays None and the
                           writer's error-text heuristic decides. We do not guess.
  gen B (2026-04..06)      `event_msg/exec_command_end` and `event_msg/patch_apply_end`, each
                           naming its own `call_id`. An exact join: 14,055/14,055 and 2,788/2,788
                           resolve to a call in the same file.
  gen C (2026-08..)        `event_msg/item_completed`, whose `item` carries NO `call_id` -- zero
                           of 115,138 records do. The join is positional: an item belongs to the
                           most recently opened call whose `_output` has not arrived. A call is
                           followed by another before its own output only twice in 40,261 calls.
                           Measured, that gives an authoritative status to 87.0% of exec-shaped
                           tool calls over the newest 200 rollouts.

`item_completed/FileChange.changes` is also the only place a changed path appears -- apply_patch
runs THROUGH the shell (40,260 `exec` calls against one literally-named `apply_patch`), so without
reading it a Codex session edits nothing as far as the ledger is concerned. Its keys are absolute
paths, 10,537 of 10,537.

Schema variants we tolerate, because Codex's rollout format has moved more than once:
  * the payload may be flattened onto the record (no `payload` key) — we fall back to the
    record itself;
  * `arguments` may already be a dict rather than a JSON string;
  * `output` may be a string, an object with `output`/`content`, or a content-block list;
  * a bare `{"record_type": ...}` or `{"item": {...}}` envelope is unwrapped;
  * missing `session_meta` (chunked upload) — the session id then comes from the filename.

System-injected user turns (`<environment_context>`, `<user_instructions>`,
`<permissions instructions>`, `<turn_context>`) are dropped, matching the reference
decoder, so the first *real* user message is the one the task id is derived from.

Outcomes: a Codex rollout carries no verdict -> `hidden_pass` is None unless a sibling
`results.json`/`report.json` supplies one. `visible_pass` is the usual heuristic.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator, Optional

from .common import (Event, Session, blocks_text, peek_jsonl, read_jsonl,
                     sidecar_outcome)

NAME = "codex"
DESCRIPTION = "OpenAI Codex CLI rollout JSONL (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)"
PATTERNS = ["rollout-*.jsonl", "*.jsonl (records with type=session_meta / response_item)"]

INJECTED_PREFIXES = ("<environment_context>", "<user_instructions>",
                     "<permissions instructions>", "<turn_context>")

# Measured on the operator's real rollouts: `exec` alone is 43% of the observed tool surface and
# is in neither tool map, so nearly half of Codex's calls landed in the unnamed-tool fallback.
# These stay the ADAPTER's overrides rather than common.TOOL_MAP: `exec` is too generic a word to
# claim globally, and every other adapter would inherit it.
TOOL_OVERRIDES = {"shell": "bash", "local_shell": "bash", "exec_command": "bash",
                  "exec": "bash", "wait": "bash", "write_stdin": "bash",
                  "container.exec": "bash", "apply_patch": "edit_file",
                  "update_plan": "bash", "view_image": "read_file",
                  "list_mcp_resources": "bash",
                  # The multi-agent surface, counted over all 1,423 rollouts on this machine:
                  # send_message 998, spawn_agent 368, wait_agent 363, followup_task 182,
                  # list_agents 133, close_agent 123, request_user_input_async 65,
                  # interrupt_agent 7. `bash` is what common.TOOL_MAP already gives Claude
                  # Code's `task`: subagent dispatch is an opaque unit of work, not a file op.
                  "send_message": "bash", "spawn_agent": "bash", "wait_agent": "bash",
                  "close_agent": "bash", "interrupt_agent": "bash", "followup_task": "bash",
                  "list_agents": "bash", "request_user_input": "bash",
                  "request_user_input_async": "bash", "sleep": "bash",
                  # The app-control surface: get_app_state 78, click 36, press_key 21,
                  # set_value 14, list_apps 12, send_input 12, type_text 8, js 7, drag 3.
                  "get_app_state": "bash", "list_apps": "bash", "click": "bash",
                  "press_key": "bash", "type_text": "bash", "set_value": "bash",
                  "send_input": "bash", "drag": "bash", "js": "bash"}


def sniff(path: str) -> float:
    if os.path.isdir(path) or not path.endswith(".jsonl"):
        return 0.0
    rows = peek_jsonl(path, 40)
    if not rows:
        return 0.0
    types = {r.get("type") for r in rows}
    score = 0.0
    if "session_meta" in types:
        score += 0.55
    if "response_item" in types:
        score += 0.35
    if "turn_context" in types or "event_msg" in types:
        score += 0.15
    if os.path.basename(path).startswith("rollout-"):
        score += 0.1
    if score == 0.0:
        # flattened variant: payloads at the top level
        payload_types = {r.get("payload", {}).get("type") if isinstance(r.get("payload"), dict) else r.get("type") for r in rows}
        if {"function_call", "function_call_output"} & payload_types:
            score = 0.5
    return min(1.0, score)


def _payload(rec: dict) -> dict:
    if isinstance(rec.get("payload"), dict):
        return rec["payload"]
    if isinstance(rec.get("item"), dict):
        return rec["item"]
    return rec


def _args_str(v: Any) -> dict:
    """Codex writes `arguments` as a JSON string; older/newer builds sometimes write a dict."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {"input": d}
        except json.JSONDecodeError:
            return {"input": v}
    return {}


def _output_text(v: Any) -> tuple[str, Optional[bool]]:
    """-> (text, ok). Codex only exposes an authoritative status on some builds
    (`{"output": ..., "success": bool}` / `{"metadata": {"exit_code": n}}`)."""
    if v is None:
        return "", None
    if isinstance(v, str):
        return v, None
    if isinstance(v, list):
        return blocks_text(v), None
    if isinstance(v, dict):
        ok = None
        if isinstance(v.get("success"), bool):
            ok = v["success"]
        meta = v.get("metadata") if isinstance(v.get("metadata"), dict) else {}
        if ok is None and isinstance(meta.get("exit_code"), int):
            ok = meta["exit_code"] == 0
        if ok is None and isinstance(v.get("exit_code"), int):
            ok = v["exit_code"] == 0
        for k in ("output", "content", "text", "stdout"):
            if isinstance(v.get(k), str):
                return v[k], ok
        return json.dumps(v, ensure_ascii=False, default=str)[:4000], ok
    return str(v), None


def _usage_from_token_count(p: dict) -> dict:
    """`event_msg/token_count` -> the four usage keys the writer prices.

    `info.last_token_usage` is per model call; `info.total_token_usage` is cumulative and would
    double-count every call, so it is deliberately ignored.
    """
    info = p.get("info") if isinstance(p.get("info"), dict) else {}
    last = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
    if not last:
        return {}
    return {"input_tokens": int(last.get("input_tokens") or 0),
            "output_tokens": int(last.get("output_tokens") or 0),
            "cache_read_input_tokens": int(last.get("cached_input_tokens") or 0),
            "cache_creation_input_tokens": int(last.get("cache_write_input_tokens") or 0)}


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _hunks_from_unified(diff: str) -> list:
    """A unified diff -> the `structuredPatch` hunk shape the capture writer counts lines from
    and renders patch.diff from.

    The `---` / `+++` header lines are skipped because no hunk is open when they are read; if
    they were kept they would each be counted as an edited line.
    """
    hunks: list = []
    cur: Optional[dict] = None
    for line in (diff or "").splitlines():
        m = _HUNK_RE.match(line)
        if m:
            cur = {"oldStart": int(m.group(1)), "oldLines": int(m.group(2) or 1),
                   "newStart": int(m.group(3)), "newLines": int(m.group(4) or 1), "lines": []}
            hunks.append(cur)
            continue
        if cur is not None and line[:1] in ("+", "-", " "):
            cur["lines"].append(line)
    return hunks


def parse_file(path: str) -> Optional[Session]:
    rows = read_jsonl(path)
    if not rows:
        return None
    events: list[Event] = []
    cwd = model = session_id = version = branch = ""
    instructions = ""
    approval = sandbox = ""
    temperature = None
    parent = ""
    unknown: dict = {}
    fallback_sid = _sid_from_name(path)
    sid_early = ""          # session_meta is row 1 in every real rollout
    req_n = 0
    group_start = 0         # index into `events` where the currently open request group began
    line_no = 0
    open_calls: list = []   # call ids whose *_output has not arrived yet
    last_call: str = ""     # the most recently OPENED call -- the gen-C window anchor
    facts: dict = {}        # call_id -> the authoritative records that call produced
    for rec in rows:
        rtype = rec.get("type") or rec.get("record_type") or ""
        p = _payload(rec)
        ts = rec.get("timestamp") or p.get("timestamp") or ""
        ptype = p.get("type") or ""
        line_no += 1
        # `ordinal` exists in Sept-2026 rollouts and not in Apr/May/Jun/Jul ones on this same
        # machine; the file line index is the fallback, and is stable for an append-only file.
        # The fallback is NAMESPACED because the two schemes coexist inside one file -- the shipped
        # fixture has a record with no ordinal between records that have them -- and an unnamespaced
        # fallback collides with a real ordinal whenever the build is not 1-based. A collision is
        # silent: identity.dedup_events drops the loser, and with it a whole tool call.
        raw = rec.get("ordinal")
        rid_base = (f"{sid_early or fallback_sid}#{raw}" if isinstance(raw, int)
                    else f"{sid_early or fallback_sid}#L{line_no}")
        if rtype and rtype not in ("session_meta", "turn_context", "event_msg", "response_item"):
            unknown[str(rtype)] = unknown.get(str(rtype), 0) + 1
        if rtype == "session_meta":
            cwd = cwd or (p.get("cwd") or "")
            session_id = session_id or (p.get("id") or "")
            sid_early = sid_early or session_id
            version = version or (p.get("cli_version") or p.get("version") or "")
            g = p.get("git") if isinstance(p.get("git"), dict) else {}
            branch = branch or (g.get("branch") or "")
            b = p.get("base_instructions") if isinstance(p.get("base_instructions"), dict) else {}
            if isinstance(b.get("text"), str) and b["text"].strip():
                instructions = instructions or b["text"]
            if isinstance(p.get("instructions"), str):           # builds before 2026-08
                instructions = instructions or p["instructions"]
            # Codex writes its own lineage. Ordinals restart per file, so uuid containment would
            # invent relations that are not there.
            parent = parent or (p.get("forked_from_id") or p.get("parent_thread_id") or "")
            continue
        if rtype == "turn_context":
            cwd = cwd or (p.get("cwd") or "")
            model = model or (p.get("model") or "")
            approval = approval or (p.get("approval_policy") or "")
            sp = p.get("sandbox_policy")
            if isinstance(sp, dict):
                sandbox = sandbox or (sp.get("type") or sp.get("mode") or "")
            elif isinstance(sp, str):
                sandbox = sandbox or sp
            if isinstance(p.get("temperature"), (int, float)):
                temperature = float(p["temperature"])
            continue
        if rtype == "event_msg":
            if ptype in ("agent_reasoning", "agent_reasoning_delta") and isinstance(p.get("text"), str) and p["text"].strip():
                events.append(Event("reasoning", ts=ts, text=p["text"],
                                    record_id=f"{rid_base}:0", request_id=f"{sid_early}-r{req_n}"))
                continue
            if ptype == "token_count":
                # A token_count closes one model call. Back-patch its usage onto the first
                # model-output event of the open group, then roll the request id so the next
                # call is grouped separately. No event is invented: a synthetic carrier would
                # show up as reasoning in the harness fingerprint.
                u = _usage_from_token_count(p)
                if u:
                    for e in events[group_start:]:
                        if e.kind in ("assistant", "reasoning", "tool_call") and not e.usage:
                            e.usage = u
                            break
                req_n += 1
                group_start = len(events)
                continue
            if ptype == "item_completed":
                # gen C. The item carries NO call_id -- 115,138 records on this machine, zero
                # occurrences -- so it belongs to the most recently OPENED call. Anchoring on the
                # calls still awaiting output instead looks equivalent and is not: this file's own
                # join runs after the fact precisely because an item and its `_output` arrive in
                # either order depending on the build, and on an output-first build the call has
                # already been popped, so every status reverted to None and the edits were dropped.
                # Well defined rather than hopeful either way: a custom_tool_call is followed by
                # another before its own output only twice in 40,261 real calls.
                item = p.get("item") if isinstance(p.get("item"), dict) else {}
                if item.get("type") in ("CommandExecution", "FileChange") and last_call:
                    facts.setdefault(last_call, []).append(item)
                continue
            if ptype in ("exec_command_end", "patch_apply_end"):
                # gen B (2026-04..06). The record names its own call, and it always resolves:
                # 14,055/14,055 and 2,788/2,788 on the real corpus. No window involved.
                cid = p.get("call_id") or ""
                if cid:
                    facts.setdefault(cid, []).append(
                        dict(p, type="CommandExecution" if ptype == "exec_command_end"
                                     else "FileChange"))
                continue
            if ptype:
                unknown[f"event_msg/{ptype}"] = unknown.get(f"event_msg/{ptype}", 0) + 1
            continue
        if rtype and rtype != "response_item":
            continue
        # --- response items
        if ptype == "message":
            role = p.get("role")
            text = blocks_text(p.get("content"))
            if role == "user":
                if text.lstrip().startswith(INJECTED_PREFIXES):
                    continue
                req_n += 1
                group_start = len(events) + 1
                events.append(Event("user", ts=ts, text=text, record_id=f"{rid_base}:0"))
            elif role == "assistant":
                events.append(Event("assistant", ts=ts, text=text, model=model,
                                    record_id=f"{rid_base}:0",
                                    request_id=f"{sid_early}-r{req_n}"))
            elif role == "system":
                instructions = instructions or text
            continue
        if ptype == "reasoning":
            txt = blocks_text(p.get("summary") or p.get("content"))
            if txt.strip():
                events.append(Event("reasoning", ts=ts, text=txt,
                                    record_id=f"{rid_base}:0",
                                    request_id=f"{sid_early}-r{req_n}"))
            continue
        if ptype in ("function_call", "custom_tool_call", "tool_search_call", "web_search_call"):
            if ptype == "custom_tool_call":
                args = {"input": p.get("input") or ""}
                name = p.get("name") or "custom_tool"
            elif ptype == "web_search_call":
                args = {k: v for k, v in p.items() if k not in ("type", "call_id", "status")}
                name = "web_search"
            elif ptype == "tool_search_call":
                args = _args_str(p.get("arguments"))
                name = "tool_search"
            else:
                args = _args_str(p.get("arguments"))
                name = p.get("name") or "function"
            events.append(Event("tool_call", ts=ts, call_id=p.get("call_id") or p.get("id") or "",
                                name=name, args=args, model=model,
                                record_id=f"{rid_base}:0",
                                request_id=f"{sid_early}-r{req_n}"))
            if p.get("call_id"):
                open_calls.append(p["call_id"])
                last_call = p["call_id"]
            continue
        if ptype in ("function_call_output", "custom_tool_call_output", "tool_search_output"):
            if ptype == "tool_search_output":
                text, ok = json.dumps(p.get("tools") or [], ensure_ascii=False)[:4000], None
            else:
                text, ok = _output_text(p.get("output"))
            events.append(Event("tool_result", ts=ts, call_id=p.get("call_id") or "", text=text,
                                ok=ok, record_id=f"{rid_base}:0",
                                request_id=f"{sid_early}-r{req_n}"))
            if (p.get("call_id") or "") in open_calls:
                open_calls.remove(p["call_id"])       # this call's window closes here
            continue
    # The join runs after the fact because an item and the matching `_output` arrive in either
    # order depending on the build, and a gen-B end event can arrive well after the output.
    calls_by_id = {e.call_id: e for e in events if e.kind == "tool_call" and e.call_id}
    for e in events:
        if e.kind != "tool_result" or not e.call_id:
            continue
        fx = facts.get(e.call_id) or []
        if not fx:
            continue                                  # nothing said: `ok` stays None, honestly
        codes = [f["exit_code"] for f in fx if isinstance(f.get("exit_code"), int)]
        states = [str(f.get("status") or "") for f in fx]
        failed = (any(st in ("failed", "error") for st in states)
                  or any(f.get("success") is False for f in fx))
        if codes:
            e.ok = (not failed) and all(c == 0 for c in codes)
        elif failed:
            e.ok = False
        elif any(st == "completed" for st in states):
            e.ok = True                               # a FileChange reports status, not an exit code
        err = "\n".join(str(f.get("stderr") or "") for f in fx if f.get("stderr")).strip()
        if err:
            e.raw = dict(e.raw or {}, stderr=err[:2000])
        ch: dict = {}
        for f in fx:
            if f.get("type") == "FileChange" and isinstance(f.get("changes"), dict):
                ch.update(f["changes"])
        if not ch:
            continue
        # The writer emits ONE edit span per tool call, so a multi-file patch reports the FIRST
        # path with the TOTAL line counts of every file it touched. patch.diff is not lossy the same
        # way: it is built below from each file's own unified diff.
        hunks: list = []
        for _p, c2 in ch.items():
            if isinstance(c2, dict):
                hunks += _hunks_from_unified(c2.get("unified_diff") or "")
        first_path = next(iter(ch))
        e.raw = dict(e.raw or {}, structured_patch=hunks, file_path=first_path)
        call = calls_by_id.get(e.call_id)
        if call is None:
            continue
        # `file_path` is the first entry of common._PATH_KEYS, so map_tool's edit_file branch
        # resolves a real path instead of "(unknown)" -- and "(unknown)" is truthy, so writer.py's
        # `args.get("path") or facts.get("file_path")` fallback never fires.
        call.args = dict(call.args or {}, file_path=first_path)
        if (call.name or "").lower() not in ("apply_patch", "edit_file", "write_file"):
            # 40,260 `exec` calls against exactly one literally-named `apply_patch`: the patch runs
            # through the shell, and only the FileChange record says files changed. Without this the
            # path lands on a bash span, the ledger has no `edit` span for Codex at all, and
            # conformance check 11 has nothing to check. The native name is kept, not discarded.
            call.args["native_name"] = call.name
            call.name = "apply_patch"
    patch_text = ""
    for _cid, fx in facts.items():
        for f in fx:
            ch = f.get("changes") if f.get("type") == "FileChange" else None
            if not isinstance(ch, dict):
                continue
            for fp, c2 in ch.items():
                if isinstance(c2, dict) and c2.get("unified_diff"):
                    rel = fp.lstrip("/")
                    d = c2["unified_diff"]
                    patch_text += d if d.startswith("---") else f"--- a/{rel}\n+++ b/{rel}\n{d}"
    if not events:
        return None
    sid = session_id or _sid_from_name(path)
    # approval_policy / sandbox_policy are the closest thing Codex has to the lab's `policy`
    policy_note = "; ".join(x for x in (f"approval={approval}" if approval else "",
                                        f"sandbox={sandbox}" if sandbox else "") if x)
    sess = Session(source=NAME, session_id=sid, path=path, model=model, cwd=cwd,
                   agent="codex", agent_version=version, events=events,
                   declared_tools=[], system_prompt=instructions, temperature=temperature,
                   patch=patch_text,
                   extra={"git_branch": branch, "approval_policy": approval,
                          "sandbox_policy": sandbox, "policy_note": policy_note,
                          "parent_session_id": parent, "unknown_record_types": unknown,
                          "tool_overrides": TOOL_OVERRIDES})
    sess.hidden_pass, sess.outcome_source = sidecar_outcome(path, sid)
    return sess


_ROLLOUT_RE = re.compile(r"rollout-(?:.*?)-([0-9a-fA-F-]{8,})\.jsonl$")


def _sid_from_name(path: str) -> str:
    m = _ROLLOUT_RE.search(os.path.basename(path))
    return m.group(1) if m else os.path.splitext(os.path.basename(path))[0]


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
