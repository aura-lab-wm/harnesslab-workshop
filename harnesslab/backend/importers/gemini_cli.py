"""Gemini CLI session JSONL -> lab ledger.

(~/.gemini/tmp/<project>/chats/session-<ISO>-<8hex>.jsonl, plus nested <uuid>/<uuid>.jsonl.)

The best-shaped of the four Plan 2 sources and the one needing the least inference: per-message
ids, timestamps everywhere, per-turn token counts including cached, and `toolCalls` carrying the
call, its result, a two-valued status and a diffStat inline.

TWO KNOWN LIMITATIONS, from the format census (tools/census_gemini.py):
  * NO VERSION STRING appears anywhere in a transcript, so agent_version stays "" and
    harness_id_for emits a bare "gemini-cli" with NO version suffix -- verified by calling it, not
    inferred: the census's phrasing of this note said "gemini", which is not the string it returns.
    The Harness lab therefore groups every CLI build together.
  * NO cwd. The project directory under ~/.gemini/tmp is the only hint, and it is a hash.

One toolCalls entry becomes TWO events -- a call and its result -- because the source fuses them.
The following `user` row repeats the same result as a `functionResponse`; emitting that too would
double-count every tool call, so it is deliberately dropped.

A `$set` envelope is a PATCH APPLIED AT THE POINT IT APPEARS, and its `messages` are emitted in file
order like any other row. The design question was originally framed as "does `$set` REPLACE the
message list or only patch metadata?", and both answers are wrong about the thing that matters --
neither asks WHERE the message belongs. Measured on the real corpus: 217 envelopes over 31 files, 30
of them carrying `messages`, **zero** replaying an id a typed row already carried, and 28 of those 30
FOLLOWED by later typed rows in 28 of the 31 files. So `$set.messages` is new content arriving in
place, and emitting it after every typed row -- which an earlier draft of this adapter did -- puts at
least one message out of order in nearly every real session.

A MESSAGE ID IS EMITTED TWICE, AND THE SECOND EMISSION IS THE ONE WITH THE TOOL CALLS. This is not
in the census and the plan did not anticipate it; it was found by parsing the real corpus and
getting zero tool calls out of it. Measured over the 31 real sessions: 253 typed rows carry only 184
distinct ids, 21 of the 31 files repeat at least one, and every repeated id appears EXACTLY twice.
Between the two emissions `content`, `tokens`, `type` and `timestamp` are identical -- the only
difference is `toolCalls`, absent on the first and present on the second, in all 69 groups. So the
CLI writes an assistant message when its text lands and rewrites it once the calls are dispatched.
Deduplicating first-wins, which is what the plan specified, keeps 0 of the corpus's 151 tool calls;
last-wins keeps all 151. The adapter therefore keeps ONE event stream per id, positioned where the
id FIRST appeared -- first-appearance order and last-appearance order are identical in all 31 files,
so position is not in question -- carrying the LAST version of the record. The same rule covers a
`$set` envelope that replays an id a typed row already carried: it rewrites that message in place
rather than adding a second one. On this corpus that never fires (zero replays over 217 envelopes).
"""
from __future__ import annotations

import os
from typing import Iterator, Optional

from .common import Event, Session, hunks_from_line_counts, peek_jsonl, read_jsonl

NAME = "gemini_cli"
DESCRIPTION = "Gemini CLI session JSONL (~/.gemini/tmp/<project>/chats/*.jsonl)"
PATTERNS = ["session-*.jsonl", "*.jsonl (records with type=gemini and toolCalls)"]

TOOL_OVERRIDES = {"replace": "edit_file", "write_file": "write_file", "read_file": "read_file",
                  "run_shell_command": "bash", "glob": "list_files",
                  "search_file_content": "list_files", "read_many_files": "read_file",
                  "save_memory": "bash", "web_fetch": "bash", "google_web_search": "bash"}


def sniff(path: str) -> float:
    if os.path.isdir(path) or not path.endswith(".jsonl"):
        return 0.0
    rows = peek_jsonl(path, 40)
    if not rows:
        return 0.0
    kinds = {r.get("type") for r in rows if isinstance(r, dict)}
    score = 0.0
    if "gemini" in kinds:
        score += 0.55
    if any(isinstance(r.get("toolCalls"), list) for r in rows if isinstance(r, dict)):
        score += 0.25
    if any(isinstance(r.get("tokens"), dict) for r in rows if isinstance(r, dict)):
        score += 0.15
    first = rows[0] if isinstance(rows[0], dict) else {}
    # The header row is the unambiguous Gemini CLI marker and carries the whole score on its own.
    # 7 of the operator's 31 real sessions are a header, one `user` row and metadata-only `$set`
    # envelopes -- a prompt with no model reply. They hold no `gemini` row, no `toolCalls` and no
    # `tokens`, so the content-derived terms score them 0.15 and the adapter walks past 23% of the
    # corpus. Nothing else in the registry comes near these files: claude_code and codex both
    # score 0.0 on all 31, measured.
    if (first.get("kind") in ("main", "subagent") and first.get("sessionId")
            and "projectHash" in first):
        score += 0.5
    return min(1.0, score)


def _usage(r: dict) -> dict:
    t = r.get("tokens")
    if not isinstance(t, dict) or not t:
        return {}
    return {"input_tokens": int(t.get("input") or 0),
            "output_tokens": int(t.get("output") or 0),
            "cache_read_input_tokens": int(t.get("cached") or 0),
            "cache_creation_input_tokens": 0}


def parse_file(path: str) -> Optional[Session]:
    rows = read_jsonl(path)
    if not rows:
        return None
    events: list[Event] = []
    unknown: dict = {}
    session_id = model = ""
    ordered: list = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if "$set" in r:
            # IN FILE ORDER, at the envelope's own position -- not appended after everything typed.
            # 30 of 217 real envelopes carry `messages`, 28 of those are followed by later typed
            # rows in 28 of 31 files, and none replays an id a typed row already carried. So these
            # are new messages arriving in place; collecting them for the end would put at least one
            # message out of order in nearly every real session.
            env = r["$set"] if isinstance(r["$set"], dict) else {}
            for m in (env.get("messages") or []):
                if not isinstance(m, dict):
                    continue
                # A patch needs an id to merge onto; an envelope message with neither an id nor a
                # conversational type is not a message, and rendering it as assistant prose while
                # never counting it in unknown_record_types hides it twice over.
                if m.get("id") or m.get("type") in ("user", "gemini"):
                    ordered.append(m)
                elif m.get("type"):
                    unknown[str(m["type"])] = unknown.get(str(m["type"]), 0) + 1
            continue
        if r.get("kind") in ("main", "subagent") and r.get("sessionId"):
            session_id = session_id or str(r["sessionId"])
            continue
        if r.get("type") in ("user", "gemini"):
            ordered.append(r)
            continue
        if r.get("type"):
            unknown[str(r["type"])] = unknown.get(str(r["type"]), 0) + 1

    # ONE emission per message id, at the position of its FIRST appearance, carrying the LAST
    # version of the record. Gemini CLI writes an assistant message when its text lands and then
    # REWRITES the same id once its tool calls are dispatched -- see the module docstring. Keeping
    # the first version instead, as an earlier draft did, discards every tool call in the corpus.
    collapsed: list = []
    at: dict = {}
    for r in ordered:
        mid = str(r.get("id") or "")
        if not mid:
            collapsed.append(r)
            continue
        if mid in at:
            # MERGE, do not replace. `$set` is a patch envelope: the rewrite that adds tool calls
            # carries the whole message, but an envelope carrying only `tokens` carries only tokens,
            # and replacing wholesale then deletes the text, the thoughts, the tool call and its
            # result. Later keys win; keys the patch does not mention survive.
            prior = collapsed[at[mid]]
            collapsed[at[mid]] = {**prior, **r} if isinstance(prior, dict) else r
            continue
        at[mid] = len(collapsed)
        collapsed.append(r)

    for r in collapsed:
        mid = str(r.get("id") or "")
        ts = r.get("timestamp") or ""
        if r.get("type") == "user":
            # functionResponse blocks repeat a result already emitted from the gemini row's
            # toolCalls entry. Emitting them too would double-count every tool call.
            text = "".join(b.get("text") or "" for b in (r.get("content") or [])
                           if isinstance(b, dict) and b.get("text"))
            if text.strip():
                events.append(Event("user", ts=ts, text=text, record_id=f"{mid}:0"))
            continue

        model = model or str(r.get("model") or "")
        usage = _usage(r)
        for i, th in enumerate(r.get("thoughts") or []):
            if isinstance(th, dict):
                txt = " ".join(x for x in (th.get("subject"), th.get("description")) if x)
                if txt.strip():
                    events.append(Event("reasoning", ts=th.get("timestamp") or ts, text=txt,
                                        model=model, record_id=f"{mid}:t{i}", request_id=mid,
                                        usage=usage))
                    usage = {}
        content = r.get("content")
        if isinstance(content, str) and content.strip():
            events.append(Event("assistant", ts=ts, text=content, model=model,
                                record_id=f"{mid}:0", request_id=mid, usage=usage))
            usage = {}
        for i, tc in enumerate(r.get("toolCalls") or []):
            if not isinstance(tc, dict):
                continue
            cid = str(tc.get("id") or f"{mid}-t{i}")
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            events.append(Event("tool_call", ts=tc.get("timestamp") or ts, call_id=cid,
                                name=str(tc.get("name") or "tool"), args=args, model=model,
                                record_id=f"{mid}:c{i}", request_id=mid, usage=usage))
            usage = {}
            st = tc.get("status")
            ds = (tc.get("resultDisplay") or {}).get("diffStat") if isinstance(
                tc.get("resultDisplay"), dict) else None
            raw: dict = {}
            if isinstance(ds, dict):
                raw["structured_patch"] = hunks_from_line_counts(ds.get("model_added_lines") or 0,
                                                                 ds.get("model_removed_lines") or 0)
                if args.get("file_path"):
                    raw["file_path"] = str(args["file_path"])
            events.append(Event("tool_result", ts=tc.get("timestamp") or ts, call_id=cid,
                                text=str(tc.get("result") or "")[:4000],
                                # anything outside {success, error} is UNKNOWN, never a failure
                                ok=(st == "success") if st in ("success", "error") else None,
                                record_id=f"{mid}:r{i}", raw=raw))

    if not events:
        return None
    return Session(source=NAME,
                   session_id=session_id or os.path.splitext(os.path.basename(path))[0],
                   path=path, model=model or "gemini", cwd="", agent="gemini-cli",
                   agent_version="",          # no version string exists in a transcript
                   events=events, declared_tools=[], system_prompt="",
                   extra={"unknown_record_types": unknown, "tool_overrides": TOOL_OVERRIDES})


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
