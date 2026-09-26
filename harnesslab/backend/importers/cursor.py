"""Cursor agent transcript JSONL -> lab ledger. An HONESTLY PARTIAL source.

(~/.cursor/projects/<project-slug>/agent-transcripts/<session>/<session>.jsonl, with child work under
<session>/subagents/<child>.jsonl.)

Measured with tools/census_cursor.py -- structure and counts only -- over 7 real files, 679 rows:

  * three row shapes: {role, message} x643, {type, status} x12, {type, status, error} x24. The last
    two are `type: turn_ended`, status success or error.
  * `message` carries exactly one key, `content`, always a list. User content is text blocks only.
  * content blocks are `text` x239 and `tool_use` x1137, and every tool_use carries exactly
    {type, name, input}. There is NO `id`, NO `tool_result` block anywhere, NO usage, NO model and
    NO timestamp at any depth (77 distinct key paths, none a clock).

So what the model ASKED for is observable -- 1137 tool requests -- and what happened when a tool
ran is not: no status, no output, no way to join a request to an outcome. This parser therefore
emits user, assistant and tool_call events, and never a tool_result, usage or ts. It invents none
of them. The capture descriptor declares the missing facts (has_tool_spans, has_usage and
has_timestamps all False), and the writer turns those declarations into UNKNOWN values rather
than zeros -- see common.UNMEASURED_FIELDS.
"""
from __future__ import annotations

import os
from typing import Iterator, Optional

from .common import Event, Session, peek_jsonl, read_jsonl

NAME = "cursor"
DESCRIPTION = "Cursor agent transcripts (~/.cursor/projects/<project>/agent-transcripts/<id>/<id>.jsonl)"
PATTERNS = ["*.jsonl (rows of {role, message.content} and {type: turn_ended})"]

#: Every native tool name the census found, lower-cased. Names already in common.TOOL_MAP (read,
#: write, grep, glob, shell, task, todowrite) are repeated so the table is the whole Cursor surface.
TOOL_OVERRIDES = {
    "read": "read_file", "write": "write_file", "strreplace": "edit_file",
    "grep": "list_files", "glob": "list_files", "shell": "bash", "awaitshell": "bash",
    "readlints": "bash", "task": "bash", "todowrite": "bash", "askquestion": "bash",
    "calldynamictool": "bash", "getdynamictools": "bash", "searchconversations": "bash",
    "setactivebranch": "bash", "switchmode": "bash", "updatecurrentstep": "bash",
}

#: Cursor names its arguments its own way, and map_tool reads the lab's names. Without this the
#: arguments were DROPPED while the tool name mapped fine: Write {"contents": ...} wrote an empty
#: file, and Glob {"glob_pattern", "target_directory"} listed ".". Renamed before mapping, never
#: invented: a key Cursor did not send stays absent.
ARG_ALIASES = {
    "contents": "content", "file_text": "content", "new_string": "new_str", "old_string": "old_str",
    "glob_pattern": "pattern", "target_directory": "path", "target_file": "path",
    "relative_workspace_path": "path", "search_path": "path", "query": "pattern",
}


def rename_args(args: dict) -> dict:
    """Cursor's argument names under the names map_tool reads. An alias never overwrites a key that
    is already there under its lab name."""
    if not isinstance(args, dict):
        return args
    out = dict(args)
    for native, lab in ARG_ALIASES.items():
        if native in out and lab not in out:
            out[lab] = out[native]
    return out


#: keys that belong to another format's envelope. A Cursor row carries none of them.
#:
#: `timestamp` is deliberately NOT here. The census is kept red against the day Cursor grows a clock,
#: and on that day a foreign-key test would stop the adapter claiming its own transcripts altogether
#: -- which is the failure the confidence comment below exists to prevent. The shape gates carry it.
_FOREIGN = ("uuid", "sessionId", "payload", "provenance", "parentUuid", "toolCalls")


def _is_message(r: dict) -> bool:
    m = r.get("message")
    return (r.get("role") in ("user", "assistant") and isinstance(m, dict)
            and isinstance(m.get("content"), list))


def sniff(path: str) -> float:
    if os.path.isdir(path) or not path.endswith(".jsonl"):
        return 0.0
    rows = [r for r in peek_jsonl(path, 40) if isinstance(r, dict)]
    if not rows or any(k in r for r in rows for k in _FOREIGN):
        return 0.0
    messages = sum(1 for r in rows if _is_message(r))
    if not messages:
        return 0.0
    turns = sum(1 for r in rows if r.get("type") == "turn_ended")
    known = messages + turns
    # Everything peeked is a message or a turn marker: the whole format, and nothing else's.
    if known == len(rows):
        return 0.8
    # A row this parser has not seen is what `unknown_record_types` is FOR -- a Cursor release
    # adding a shape must still be read AS Cursor. Degrading to 0.5 here put the file below
    # trajectory_fmt (which scores ~0.76 on these same rows because "user"/"assistant" are its role
    # names too), so detect() handed a transcript full of real tool requests to a generic reader
    # that is absent from the capture registry -- imported with no declared gaps, and written as
    # steps/tool_calls/tokens/cost all ZERO. Stay dominant while the file is still recognisably
    # Cursor's; fall back only when the majority of rows are something else.
    frac = known / len(rows)
    # Below the majority this is not a Cursor transcript, and the caller's threshold is `>= 0.5`:
    # returning exactly 0.5 was accepted, so the declining branch never declined.
    return max(0.78, 0.8 * frac) if frac > 0.5 else 0.8 * frac


def parse_file(path: str) -> Optional[Session]:
    rows = read_jsonl(path)
    events: list[Event] = []
    unknown: dict = {}
    last_turn = ""

    def count(kind: str) -> None:
        unknown[kind] = unknown.get(kind, 0) + 1

    for r in rows:
        if r.get("type") == "turn_ended":
            last_turn = str(r.get("status") or "")
            continue
        if "role" in r and r.get("role") not in ("user", "assistant"):
            count(f"role:{r.get('role')}")
            continue
        if not _is_message(r):
            count(f"type:{r.get('type')}" if "type" in r else "shape:" + ",".join(sorted(r)))
            continue
        role = r["role"]
        for b in r["message"]["content"]:
            t = b.get("type") if isinstance(b, dict) else None
            if t == "text":
                txt = b.get("text") if isinstance(b.get("text"), str) else ""
                events.append(Event(kind="user" if role == "user" else "assistant", text=txt))
            elif t == "tool_use" and role == "assistant":
                inp = b.get("input")
                events.append(Event(kind="tool_call", name=str(b.get("name") or ""),
                                    args=rename_args(inp) if isinstance(inp, dict) else {"input": inp}))
            else:
                count(f"block:{t}")
    if not events:
        return None
    parent_dir = os.path.basename(os.path.dirname(path))
    parent = (os.path.basename(os.path.dirname(os.path.dirname(path)))
              if parent_dir == "subagents" else "")
    return Session(source=NAME, session_id=os.path.splitext(os.path.basename(path))[0], path=path,
                   model="", cwd="", agent="cursor", agent_version="", events=events,
                   declared_tools=[], system_prompt="",
                   # Only a FINAL error is an exit status. A turn that ended "success" is the end of a
                   # reply, not a submitted task, and must not be mapped onto `submitted`.
                   exit_status="error" if last_turn == "error" else "",
                   extra={"unknown_record_types": unknown, "tool_overrides": TOOL_OVERRIDES,
                          "is_subagent": bool(parent), "parent_session_id": parent})


def sessions(path: str) -> Iterator[Session]:
    """Every Cursor transcript under `path`, EXCLUDING subagent children.

    The capture glob (adapters.py) claims `**/agent-transcripts/*/*.jsonl` and leaves
    `subagents/<child>.jsonl` out, because a child's link to its parent is a start-span field an
    index row cannot carry and counting children as peers inflates every aggregate. A directory
    import walked them in anyway, so the two entry points disagreed about the same invariant.
    A file named explicitly is still read: that is an explicit request, as everywhere else.
    """
    if os.path.isdir(path):
        for dp, dn, fn in os.walk(path):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in sorted(fn):
                if f.endswith(".jsonl"):
                    p = os.path.join(dp, f)
                    # The capture glob is **/agent-transcripts/*/*.jsonl. Excluding subagents/ alone
                    # still walked canvases/, terminals/ and agent-tools/ in, so an import of the same
                    # tree produced four times the runs the watcher captures from it.
                    if os.path.basename(os.path.dirname(os.path.dirname(p))) != "agent-transcripts":
                        continue
                    if os.path.basename(os.path.dirname(p)) == "subagents":
                        continue
                    if sniff(p) >= 0.5:
                        s = parse_file(p)
                        if s:
                            yield s
        return
    s = parse_file(path)
    if s:
        yield s
