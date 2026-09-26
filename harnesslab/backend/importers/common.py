"""Shared machinery for every external-harness importer.

One intermediate representation (`Session`) sits between the native logs and the lab's
ledger. Every adapter's only job is to turn its native format into a `Session`; the
conversion to `ledger.jsonl` + `summary.json` + `patch.diff` + `messages.json` + a
`RunSummary` row happens exactly once, here, so the six sources stay comparable by
construction.

The intermediate event stream is deliberately the same shape as the Trajectory v1
record set (letta-ai/trajectory, `schema/trajectory-v1.schema.json`): meta, system,
user, reasoning, assistant, tool-call, tool-result. That way `trajectory_fmt.py` is a
near-identity adapter and the other five are readable next to it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Iterable, Optional

# ------------------------------------------------------------------ lab tool surface
LAB_TOOLS = ["list_files", "read_file", "write_file", "edit_file", "run_tests", "bash", "submit"]

# Native tool name (lower-cased) -> lab tool. Sources agree more than they disagree;
# per-source overrides live in the adapters and are merged on top of this table.
TOOL_MAP: dict[str, str] = {
    # --- Claude Code
    "bash": "bash", "bashoutput": "bash", "killshell": "bash", "killbash": "bash",
    "read": "read_file", "notebookread": "read_file",
    "write": "write_file",
    "edit": "edit_file", "multiedit": "edit_file", "notebookedit": "edit_file", "applypatch": "edit_file",
    "grep": "list_files", "glob": "list_files", "ls": "list_files", "listdir": "list_files",
    "task": "bash",                       # subagent dispatch: an opaque unit of work
    "todowrite": "bash", "exitplanmode": "bash", "webfetch": "bash", "websearch": "bash",
    "slashcommand": "bash", "askuserquestion": "bash", "plan": "bash",
    # --- Codex CLI
    "shell": "bash", "local_shell": "bash", "exec_command": "bash", "container.exec": "bash",
    "apply_patch": "edit_file", "write_stdin": "bash", "update_plan": "bash",
    "view_image": "read_file", "web_search": "bash", "tool_search": "bash",
    # --- Inspect AI / generic agent tools
    "python": "bash", "think": "bash", "submit": "submit",
    "str_replace_editor": "edit_file", "text_editor": "edit_file", "editor": "edit_file",
    "computer": "bash", "web_browser": "bash",
    # --- OpenHands
    "execute_bash": "bash", "execute_ipython_cell": "bash", "run_ipython": "bash",
    "str_replace_edit": "edit_file", "edit_file": "edit_file", "read_file": "read_file",
    "browser": "bash", "browse": "bash", "finish": "submit", "delegate": "bash",
    "cmdrunaction": "bash", "ipythonruncellaction": "bash", "fileeditaction": "edit_file",
    "filewriteaction": "write_file", "filereadaction": "read_file", "agentfinishaction": "submit",
    "browseurlaction": "bash", "browseinteractiveaction": "bash", "agentdelegateaction": "bash",
    "agentthinkaction": "bash", "messageaction": "bash", "condensationaction": "bash",
    # --- SWE-agent command language (see harnesslab/core/real_traj.py)
    "open": "read_file", "goto": "read_file", "scroll_up": "read_file", "scroll_down": "read_file",
    "cat": "read_file", "head": "read_file", "tail": "read_file",
    "search_dir": "list_files", "search_file": "list_files", "find_file": "list_files", "find": "list_files",
    "create": "write_file", "insert": "edit_file", "str_replace": "edit_file",
}

# Argument keys that mean "a path", in preference order.
_PATH_KEYS = ("file_path", "filePath", "path", "file", "filename", "target_file", "notebook_path", "abs_path")
_CMD_KEYS = ("command", "cmd", "code", "input", "script", "commands")

_TEST_CMD = re.compile(r"\b(pytest|py\.test|unittest|tox|nose2?|jest|vitest|mocha|go\s+test|cargo\s+test|npm\s+(run\s+)?test|make\s+(check|test))\b")
_GREEN = re.compile(r"(\d+\s+passed|\bOK\b|\bPASSED\b|All tests passed|\btests? passed\b|0 failed|✓)", re.I)
_RED = re.compile(r"(\d+\s+failed|\bFAILED\b|\bERRORS?\b|Traceback|AssertionError|\bFAIL\b|✗)", re.I)
_ERR_OBS = re.compile(r"(command not found|No such file or directory|Traceback \(most recent call last\)|SyntaxError|Permission denied|exit code 1\b|ERROR:)", re.I)
_TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.(py|js|ts|go|rs)$|_test\.(py|go|js|ts)$")

# Commands that a strict lab policy would have blocked. We only ever *observe* them.
DESTRUCTIVE = [
    (re.compile(r"\brm\s+-[a-zA-Z]*[rf]"), "rm_rf"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f|push\s+--force|checkout\s+--\s)"), "git_destructive"),
    (re.compile(r"\bsudo\b"), "sudo"),
    (re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(ba)?sh"), "curl_pipe_sh"),
    (re.compile(r"\bchmod\s+777\b"), "chmod_777"),
    (re.compile(r"\b(dd|mkfs|shutdown|reboot)\b"), "system_level"),
    (re.compile(r">\s*/dev/(sd|nvme)"), "raw_device"),
    (re.compile(r"\bpip\s+install\b|\bnpm\s+install\b|\bapt(-get)?\s+install\b"), "network_install"),
]

# Text a harness leaves behind when it truncates or compacts context.
_TRUNC_HINTS = [
    (re.compile(r"\[observation elided", re.I), "observation_elided"),
    (re.compile(r"<response clipped>|truncated by harness|output truncated|\[truncated\]", re.I), "output_truncated"),
    (re.compile(r"compact(ed|ing)? (the )?(conversation|context)|context (was )?compacted|/compact", re.I), "context_compaction"),
    (re.compile(r"context (window )?(low|limit|exceeded|exhausted)", re.I), "context_pressure"),
    (re.compile(r"maximum context length|too many tokens", re.I), "context_pressure"),
]


# ------------------------------------------------------------------ intermediate representation
@dataclass
class Event:
    """One normalized record. `kind` mirrors Trajectory v1 `role` plus a split of
    assistant-with-tool_calls into `tool_call` (one per call)."""
    kind: str                                  # meta|system|user|reasoning|assistant|tool_call|tool_result|observation
    ts: str = ""
    text: str = ""
    call_id: str = ""
    name: str = ""                             # native tool name for tool_call
    args: dict = field(default_factory=dict)
    ok: Optional[bool] = None                  # tool_result: authoritative status when the source exposes one
    model: str = ""
    usage: dict = field(default_factory=dict)  # {"input_tokens": n, "output_tokens": n}
    raw: dict = field(default_factory=dict)    # small source-specific extras (never the whole record)
    record_id: str = ""                        # source record identity for dedup (Claude Code: "<uuid>:<block index>")
    request_id: str = ""                       # groups the content blocks of one model call (Claude Code: requestId)


@dataclass
class Session:
    """One importable run."""
    source: str                                # adapter name: claude_code | codex | ...
    session_id: str
    path: str = ""                             # the file it came from
    model: str = ""
    cwd: str = ""
    agent: str = ""                            # human name, e.g. "claude-code"
    agent_version: str = ""
    events: list = field(default_factory=list)
    declared_tools: list = field(default_factory=list)   # tools the trace says exist (may be empty)
    system_prompt: str = ""                    # NEVER stored; only hashed + measured
    temperature: Optional[float] = None
    max_steps_declared: int = 0
    task_id_hint: str = ""                     # SWE-bench instance id etc., when the source knows it
    hidden_pass: Optional[bool] = None         # only when the trace carries a real verdict
    outcome_source: str = ""                   # where that verdict came from
    exit_status: str = ""                      # native exit status string, if any
    patch: str = ""                            # final diff, if the source has one
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------------------ facts a source may not record
#: capability a source can lack -> the RunSummary fields that become UNKNOWN without it. The capture
#: descriptor declares which capabilities are missing (capture.adapters.missing); both writers --
#: capture's write_run and this module's convert -- turn that into null values plus an `unmeasured`
#: list on the end span. A zero here would be a claim: "no tool calls", "cost nothing".
UNMEASURED_FIELDS = {
    "tool_spans": ("tool_calls", "edits", "lines_added", "lines_removed", "files_touched",
                   "boundary_events", "boundary_kinds", "tests_run_by_agent",
                   "ran_tests_before_submit", "tests_modified", "patch_bytes"),
    "usage": ("input_tokens", "output_tokens", "cost_usd"),
    # a clockless run's start and end are unknown; RunSummary already spells that "" (never None,
    # which every string consumer of started_at would have to learn)
    "timestamps": ("started_at", "finished_at"),
}


def unmeasured_fields(missing) -> list:
    """Sorted RunSummary field names a run with these missing capabilities cannot know."""
    unknown = set(missing or ()) - set(UNMEASURED_FIELDS)
    if unknown:
        raise ValueError(f"unknown capabilities {sorted(unknown)}; known: {sorted(UNMEASURED_FIELDS)}")
    return sorted({f for cap in (missing or ()) for f in UNMEASURED_FIELDS[cap]})


def blank_unmeasured(summary, missing) -> None:
    """Overwrite every field the run could not measure with its unknown value."""
    for f in unmeasured_fields(missing):
        setattr(summary, f, "" if f in UNMEASURED_FIELDS["timestamps"] else None)


# ------------------------------------------------------------------ small helpers
def sha12(obj: Any) -> str:
    """The importers' own content hash. Deliberately not imported from harnesslab.core: another
    work-stream may add a hash there and we must not depend on its shape."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


def sha256_hex(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def read_jsonl(path: str, limit: Optional[int] = None) -> list[dict]:
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(v, dict):
                out.append(v)
            if limit and len(out) >= limit:
                break
    return out


def peek_jsonl(path: str, n: int = 12) -> list[dict]:
    return read_jsonl(path, limit=n)


def blocks_text(content) -> str:
    """Anthropic/OpenAI style content blocks -> plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for k in ("text", "content", "output", "result"):
            if isinstance(content.get(k), str):
                return content[k]
        return json.dumps(content, ensure_ascii=False)[:4000]
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("type")
                if t in ("text", "output_text", "input_text") and isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif t == "thinking" and isinstance(b.get("thinking"), str):
                    parts.append(b["thinking"])
                elif t in ("image", "input_image", "output_image"):
                    parts.append("[image]")
                elif isinstance(b.get("text"), str):
                    parts.append(b["text"])
        return "\n".join(p for p in parts if p)
    return str(content)


def first_str(d: dict, keys: Iterable[str]) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def slugify(s: str, n: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:n] or "session"


SWEBENCH_ID = re.compile(r"^[A-Za-z0-9][\w.-]*__[\w.-]+-\d+$")


def derive_task_id(sess: "Session") -> str:
    """SWE-bench-style instance ids are kept verbatim. Otherwise the task id is
    `<repo-or-cwd slug>-<first user message slug>-<8 hex>`, where the hex is over the
    (cwd, first user message) pair so the same prompt in the same directory always maps
    to the same task across harnesses — which is exactly what the matrix needs."""
    hint = (sess.task_id_hint or "").strip()
    if hint and SWEBENCH_ID.match(hint):
        return hint
    if hint:
        return slugify(hint, 60)
    first_user = next((e.text for e in sess.events if e.kind == "user" and e.text.strip()), "")
    base = os.path.basename(os.path.normpath(sess.cwd)) if sess.cwd else ""
    key = f"{sess.cwd}|{first_user.strip()[:600]}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    parts = [p for p in (slugify(base, 20), slugify(first_user, 28)) if p]
    return ("-".join(parts) or "task") + "-" + h


def sidecar_outcome(path: str, session_id: str) -> tuple[Optional[bool], str]:
    """A verdict only if a sibling `results.json` / `report.json` says so.

    Format-agnostic on purpose: it reads a file NEXT TO the trace and never the trace itself,
    so it belongs to every adapter rather than to whichever one happened to need it first.
    It is the ONLY way `hidden_pass` is ever set to something other than None -- the honesty
    rule in the package docstring: a verdict we did not observe is unknown, not a failure.

    Accepted shapes (all seen in SWE-bench-style eval harnesses):
      {"<id>": {"resolved": true}}      {"resolved": ["<id>", ...]}
      {"resolved": true}                {"<id>": true}
    """
    d = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    for fn in ("results.json", "report.json", f"{stem}.results.json", f"{stem}.report.json"):
        p = os.path.join(d, fn)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for key in (stem, session_id):
            if isinstance(data, dict) and key in data:
                v = data[key]
                if isinstance(v, bool):
                    return v, f"sidecar {fn}"
                if isinstance(v, dict) and isinstance(v.get("resolved"), bool):
                    return v["resolved"], f"sidecar {fn}"
        if isinstance(data, dict) and isinstance(data.get("resolved"), bool):
            return data["resolved"], f"sidecar {fn}"
        if isinstance(data, dict) and isinstance(data.get("resolved"), list):
            return (stem in data["resolved"] or session_id in data["resolved"]), f"sidecar {fn}"
    return None, ""


def hunks_from_line_counts(added: int, removed: int) -> list:
    """A `structuredPatch`-shaped hunk carrying only COUNTS.

    Qwen Code and Gemini CLI report `model_added_lines` / `model_removed_lines` rather than the
    edited lines themselves. The counts are real, so `line_counts_known` stays True; the line
    TEXT is not available and is deliberately left empty rather than invented. A source that
    ships a real unified diff should be parsed into hunks instead of coming through here.
    """
    if not (added or removed):
        return []
    lines = ["-"] * int(removed or 0) + ["+"] * int(added or 0)
    return [{"oldStart": 0, "oldLines": int(removed or 0),
             "newStart": 0, "newLines": int(added or 0), "lines": lines}]


def map_tool(name: str, args: dict, overrides: Optional[dict] = None) -> tuple[str, dict]:
    """Native (name, args) -> (lab tool, lab args). Unknown tools become `bash` with the
    native name in the command, so nothing is silently dropped from the trajectory."""
    args = args if isinstance(args, dict) else {"input": args}
    raw = (name or "").strip()
    key = raw.lower()
    tool = (overrides or {}).get(key) or TOOL_MAP.get(key)
    if tool is None:
        if key.startswith("mcp__") or key.startswith("mcp."):
            tool = "bash"
        elif key.endswith("action"):
            tool = "bash"
        else:
            tool = "bash"
    path = first_str(args, _PATH_KEYS)
    cmd = first_str(args, _CMD_KEYS)
    if tool == "read_file":
        return "read_file", {"path": path or cmd or raw}
    if tool == "list_files":
        la = {"path": path or args.get("dir") or args.get("directory") or "."}
        for k in ("pattern", "glob"):
            if isinstance(args.get(k), str) and args[k]:
                la["pattern"] = args[k]
                break
        if isinstance(args.get("pattern"), str) and key == "grep":
            la["pattern"] = args["pattern"]
        return "list_files", la
    if tool == "write_file":
        content = args.get("content") or args.get("file_text") or args.get("new_str") or ""
        return "write_file", {"path": path or "(unknown)", "content": _clip(content, 400)}
    if tool == "edit_file":
        old = args.get("old_string") or args.get("old_str") or args.get("oldText") or ""
        new = args.get("new_string") or args.get("new_str") or args.get("newText") or args.get("file_text") or ""
        if not (old or new) and (args.get("edits") or args.get("patch") or args.get("input")):
            new = json.dumps(args.get("edits") or args.get("patch") or args.get("input"), default=str)[:400]
        return "edit_file", {"path": path or "(unknown)", "old": _clip(old, 200), "new": _clip(new, 200)}
    if tool == "submit":
        return "submit", {"summary": _clip(first_str(args, ("summary", "message", "answer", "final_answer", "outputs", "thought")), 300)}
    # bash: keep the native name when it is not literally a shell
    command = cmd if cmd else (json.dumps(args, ensure_ascii=False, default=str)[:300] if args else "")
    if key not in ("bash", "shell", "local_shell", "exec", "exec_command", "execute_bash",
                   "container.exec", "cmdrunaction"):
        command = f"{raw}: {command}" if command else raw
    return "bash", {"command": _clip(command, 400)}


def _clip(s: Any, n: int) -> str:
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + " …"


def tests_passed_for(tool: str, args: dict, result: str, ok: Optional[bool] = None) -> Optional[bool]:
    """The same heuristic real_import.py uses: only a command that *looks like a test run*
    gets a verdict, and only when the output is unambiguous. `ok` (an authoritative status
    from the source) breaks ties but never invents a test run."""
    if tool not in ("bash", "run_tests"):
        return None
    cmd = args.get("command", "") or ""
    if not _TEST_CMD.search(cmd):
        return None
    obs = result or ""
    red, green = bool(_RED.search(obs)), bool(_GREEN.search(obs))
    if red and not green:
        return False
    if green and not red:
        return True
    if ok is not None:
        return bool(ok)
    if "failed" in obs.lower() or "error" in obs.lower():
        return False
    return True if green else None


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path or ""))


def destructive_kinds(command: str) -> list[str]:
    return [name for rx, name in DESTRUCTIVE if rx.search(command or "")]


def truncation_hints(text: str) -> list[str]:
    return sorted({name for rx, name in _TRUNC_HINTS if rx.search(text or "")})


# ------------------------------------------------------------------ harness fingerprint
def fingerprint(events: list, meta: dict) -> dict:
    """Extract what the trace itself tells us about the harness.

    Everything here is *observed*, not assumed. The system prompt of a proprietary harness
    is never stored: only its sha256 and its length, so two runs can be proven to share (or
    not share) a prompt without the prompt leaving the machine it was recorded on.

    `events` is a list of `Event`; `meta` carries the session-level fields.
    """
    ev = list(events)
    native_tools, lab_tools, destructive, hints = [], [], [], set()
    steps = set()
    n_reasoning = 0
    n_results = 0
    n_errors = 0
    max_result_chars = 0
    step = -1
    for e in ev:
        if e.kind in ("assistant", "reasoning"):
            if e.kind == "reasoning":
                n_reasoning += 1
        if e.kind == "tool_call":
            step += 1
            steps.add(step)
            if e.name and e.name not in native_tools:
                native_tools.append(e.name)
            lt, la = map_tool(e.name, e.args, meta.get("tool_overrides"))
            if lt not in lab_tools:
                lab_tools.append(lt)
            if lt == "bash":
                destructive += destructive_kinds(la.get("command", ""))
        if e.kind in ("tool_result", "observation"):
            n_results += 1
            max_result_chars = max(max_result_chars, len(e.text or ""))
            if e.ok is False:
                n_errors += 1
            hints |= set(truncation_hints(e.text))
        hints |= set(truncation_hints(e.text)) if e.kind in ("system", "user") else set()
    sp = meta.get("system_prompt") or ""
    fp = {
        "agent": meta.get("agent") or meta.get("source") or "",
        "agent_version": meta.get("agent_version") or "",
        "source_format": meta.get("source") or "",
        "model": meta.get("model") or "",
        "temperature": meta.get("temperature"),
        "declared_tools": sorted(meta.get("declared_tools") or []),
        "observed_tools": sorted(native_tools),
        "lab_tools": [t for t in LAB_TOOLS if t in lab_tools],
        "observed_max_steps": len(steps),
        "declared_max_steps": int(meta.get("max_steps_declared") or 0),
        "system_prompt_sha256": sha256_hex(sp) if sp else "",
        "system_prompt_chars": len(sp),
        "reasoning_exposed": n_reasoning > 0,
        "tool_results": n_results,
        "tool_errors": n_errors,
        "max_observation_chars": max_result_chars,
        "context_hints": sorted(hints),
        "destructive_executed": sorted(set(destructive)),
        "policy_hint": "permissive" if destructive else "unknown",
    }
    return fp


def harness_from_fingerprint(harness_id: str, fp: dict, notes: str = "") -> dict:
    """A HarnessConfig-shaped dict. Unknowns are 0 / "" / False rather than invented, and
    the observed evidence is kept whole under `fingerprint`."""
    h = {
        "id": harness_id,
        "system_prompt": "",                       # never stored for an external harness
        "tools": fp.get("lab_tools") or [],
        "policy": fp.get("policy_hint") if fp.get("policy_hint") == "permissive" else "unknown",
        "max_steps": int(fp.get("declared_max_steps") or fp.get("observed_max_steps") or 0),
        "max_total_tokens": 0,
        "context_window": 0,
        "observation_chars": int(fp.get("max_observation_chars") or 0),
        "temperature": float(fp["temperature"]) if isinstance(fp.get("temperature"), (int, float)) else 0.0,
        "max_tokens_per_call": 0,
        "include_file_listing": False,
        "notes": notes,
        "sentinel": {},
        "fingerprint": fp,
    }
    h["hash"] = sha12(fp)
    return h


def harness_id_for(sess: Session) -> str:
    """`claude-code@1.0.x`, `codex@0.4.x`, `openhands@0.x`, `swe-agent`, `inspect:<solver>`."""
    agent = (sess.agent or sess.source).replace("_", "-")
    if sess.source == "inspect":
        solver = sess.extra.get("solver") or "solver"
        return f"inspect:{solver}"
    v = (sess.agent_version or "").strip().lstrip("v")
    if not v:
        return agent
    parts = re.split(r"[.\-+]", v)
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{agent}@{parts[0]}.{parts[1]}.x"
    if parts[0].isdigit():
        return f"{agent}@{parts[0]}.x"
    return f"{agent}@{v[:12]}"


# ------------------------------------------------------------------ Session -> ledger
def run_id_for(sess: Session, task_id: str) -> str:
    """Deterministic, so re-importing the same file is a no-op (see import_path)."""
    key = f"{sess.source}|{sess.session_id}|{task_id}|{os.path.basename(sess.path)}"
    return f"imp-{slugify(sess.source, 12)}-{hashlib.sha256(key.encode()).hexdigest()[:12]}"


def _exit_reason(sess: Session, submitted: bool, n_steps: int, stopped_calling_tools: bool = False) -> str:
    """Map a native stopping reason onto the lab's vocabulary.

    An external harness usually records *no* stopping reason at all. We refuse to invent
    one: a session that simply ran out of transcript gets `no_action` when its last turn
    was prose with no tool call (the lab's own meaning: the model stopped calling tools)
    and `unknown` otherwise. It is never silently reported as `max_steps`.
    """
    s = (sess.exit_status or "").lower()
    if "context" in s:
        return "budget_exceeded"
    if s.startswith("submitted") or s in ("success", "completed", "finished", "resolved"):
        return "submitted"
    if "cost" in s or "budget" in s or "token" in s:
        return "budget_exceeded"
    if "max" in s and ("step" in s or "iteration" in s or "turn" in s or "message" in s):
        return "max_steps"
    if "cancel" in s or "abort" in s or "interrupt" in s:
        return "error"
    if "error" in s or "fail" in s:
        return "error"
    if submitted:
        return "submitted"
    if s:
        return s[:24]
    if n_steps == 0 or stopped_calling_tools:
        return "no_action"
    return "unknown"


def convert(sess: Session, out_root: str, task_id: str, harness: dict, model_override: str = "",
            missing: frozenset = frozenset()) -> Any:
    """Write one run's four files and return its RunSummary.

    `missing` names capabilities the source does not record (see UNMEASURED_FIELDS). Without tool
    spans a tool request is kept on its chat span but no execute_tool/edit/boundary span is
    written for it; without usage no token count or cost is estimated. Either way the fields they
    would have filled are null in the summary, never zero.
    """
    tools_known = "tool_spans" not in missing
    usage_known = "usage" not in missing
    from harnesslab.core.ledger import RunSummary   # local import: keeps this module importable without the lab on sys.path

    model = model_override or sess.model or "unknown"
    harness_id = harness["id"]
    run_id = run_id_for(sess, task_id)
    run_dir = os.path.join(out_root, run_id)
    os.makedirs(run_dir, exist_ok=True)

    summary = RunSummary(run_id=run_id, task_id=task_id, harness_id=harness_id, model=model,
                         provider=f"import:{sess.source}", repeat_index=0,
                         started_at=next((e.ts for e in sess.events if e.ts), ""))
    spans: list[dict] = []
    messages: list[dict] = []
    seq = 0
    first_ts = summary.started_at

    def rec(span: str, ts: str = "", **f):
        nonlocal seq
        r = {"run_id": run_id, "task_id": task_id, "harness_id": harness_id,
             "gen_ai.request.model": model, "seq": seq, "ts": ts or "", "span": span}
        r.update(f)
        spans.append(r)
        seq += 1

    rec("invoke_agent", ts=first_ts, status="start", harness=harness, seed=None, repeat_index=0,
        import_source=sess.source, source_path=os.path.basename(sess.path), session_id=sess.session_id)

    # results indexed by call id, so a tool_call span can carry its observation even when the
    # source interleaves them differently
    results: dict[str, Event] = {}
    for e in sess.events:
        if e.kind == "tool_result" and e.call_id:
            results.setdefault(e.call_id, e)
    orphan_results = [e for e in sess.events if e.kind in ("tool_result", "observation") and not e.call_id]

    if sess.system_prompt:
        messages.append({"role": "system", "content": "(external harness system prompt withheld; "
                                                      f"sha256={sha256_hex(sess.system_prompt)[:16]}…, {len(sess.system_prompt)} chars)"})
    step = -1
    pending_text = ""
    last_edit, last_test = -1, -1
    submitted = False
    unmatched = iter(orphan_results)

    for e in sess.events:
        if e.kind == "user":
            messages.append({"role": "user", "content": e.text[:4000]})
        elif e.kind == "reasoning":
            pending_text = (pending_text + "\n" + e.text).strip()
        elif e.kind == "assistant":
            pending_text = (pending_text + "\n" + e.text).strip()
        elif e.kind == "tool_call":
            step += 1
            tool, args = map_tool(e.name, e.args, sess.extra.get("tool_overrides"))
            res_ev = results.get(e.call_id) if e.call_id else None
            if res_ev is None:
                res_ev = next(unmatched, None)
            obs = (res_ev.text if res_ev else "") or ""
            ok = res_ev.ok if res_ev else None
            tp = tests_passed_for(tool, args, obs, ok)
            if ok is False:
                status = "error"
            elif ok is True:
                status = "ok"
            else:
                status = "error" if (tool != "submit" and _ERR_OBS.search(obs[:600])) else "ok"
            if usage_known:
                in_tok = int(e.usage.get("input_tokens") or 0)
                out_tok = int(e.usage.get("output_tokens") or 0) or max(1, len(pending_text) // 4)
            else:
                in_tok = out_tok = None
            rec("chat", ts=e.ts, **{
                "gen_ai.operation.name": "chat",
                "gen_ai.usage.input_tokens": in_tok,
                "gen_ai.usage.output_tokens": out_tok,
                "gen_ai.response.finish_reasons": ["tool_calls"],
                "duration_ms": 0, "cost_usd": 0.0 if usage_known else None, "step": step,
                "text": pending_text[:2000],
                "tool_calls": [{"name": tool, "arguments": args}]})
            tc_id = e.call_id or f"call_{step}"
            messages.append({"role": "assistant", "content": pending_text[:2000],
                             "tool_calls": [{"id": tc_id, "name": tool, "arguments": args}]})
            pending_text = ""
            summary.steps += 1
            if usage_known:
                summary.input_tokens += in_tok or len(obs) // 4
                summary.output_tokens += out_tok
            if not tools_known:
                continue            # the request is on the chat span; its execution was never recorded
            summary.tool_calls += 1
            extra = {"tests_passed": tp} if tp is not None else {}
            rec("execute_tool", ts=(res_ev.ts if res_ev else e.ts), **{
                "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool, "args": args,
                "status": status, "duration_ms": 0, "result_preview": obs[:400],
                "native_tool": e.name, "import_source": sess.source, **extra})
            messages.append({"role": "tool", "tool_call_id": tc_id,
                             "content": ("exit=0\n" if tp else "exit=1\n" if tp is False else "") + obs[:1500]})
            if tool in ("edit_file", "write_file") and status == "ok":
                rec("edit", ts=e.ts, path=args.get("path", ""), lines_added=0, lines_removed=0, tool=tool)
                summary.edits += 1
                if args.get("path") and args["path"] not in summary.files_touched:
                    summary.files_touched.append(args["path"])
                last_edit = step
                if is_test_path(args.get("path", "")):
                    summary.tests_modified = True
            if tool == "bash":
                kinds = destructive_kinds(args.get("command", ""))
                for k in kinds:
                    rec("boundary_event", ts=e.ts, kind=k, status="allowed", tool="bash",
                        args=args, note="observed in an external harness; this lab's strict policy would have blocked it")
                    summary.boundary_events += 1
                    if k not in summary.boundary_kinds:
                        summary.boundary_kinds.append(k)
            if tp is not None:
                summary.tests_run_by_agent += 1
                last_test = step
            if tool == "submit":
                submitted = True

    stopped_calling_tools = bool(pending_text.strip())
    if pending_text.strip():
        step += 1
        rec("chat", **{"gen_ai.operation.name": "chat",
                       "gen_ai.usage.input_tokens": 0 if usage_known else None,
                       "gen_ai.usage.output_tokens": max(1, len(pending_text) // 4) if usage_known else None,
                       "gen_ai.response.finish_reasons": ["stop"], "duration_ms": 0,
                       "cost_usd": 0.0 if usage_known else None,
                       "step": step, "text": pending_text[:2000], "tool_calls": []})
        messages.append({"role": "assistant", "content": pending_text[:2000]})
        summary.steps += 1
        if usage_known:
            summary.output_tokens += max(1, len(pending_text) // 4)

    summary.exit_reason = _exit_reason(sess, submitted, summary.steps, stopped_calling_tools)
    summary.ran_tests_before_submit = last_test >= last_edit >= 0
    # Another work-stream added RunSummary.harness_hash + harnesslab.core.harness.content_hash.
    # Populate it when both exist so their views work on imported runs, but never depend on it.
    if hasattr(summary, "harness_hash"):
        try:
            from harnesslab.core.harness import content_hash as _ch
            summary.harness_hash = _ch(harness)
        except Exception:
            summary.harness_hash = harness.get("hash", "")
    if sess.patch:
        from harnesslab.core.real_traj import patch_stats
        ps = patch_stats(sess.patch)
        summary.lines_added = ps["lines_edited"]
        summary.patch_bytes = len(sess.patch.encode())
        if ps["test_files_edited"]:
            summary.tests_modified = True
    summary.hidden_pass = sess.hidden_pass                  # None unless the trace carries a verdict
    last_tp = next((s.get("tests_passed") for s in reversed(spans)
                    if s["span"] == "execute_tool" and "tests_passed" in s), None)
    summary.visible_pass = bool(last_tp) if last_tp is not None else None
    summary.strong_pass = None
    summary.error = "" if summary.exit_reason != "error" else (sess.exit_status or "")[:200]
    summary.finished_at = next((e.ts for e in reversed(sess.events) if e.ts), "")
    if missing:
        blank_unmeasured(summary, missing)

    if sess.hidden_pass is not None:
        rec("grade", visible=summary.visible_pass, hidden=summary.hidden_pass, strong=None,
            tests_modified=summary.tests_modified, source=sess.outcome_source or f"{sess.source} trace")
    rec("invoke_agent", ts=summary.finished_at, status="end", exit_reason=summary.exit_reason,
        hidden_pass=summary.hidden_pass, cost_usd=0.0 if usage_known else None,
        total_tokens=(summary.input_tokens + summary.output_tokens) if usage_known else None,
        native_exit_status=sess.exit_status, outcome_known=sess.hidden_pass is not None,
        unmeasured=unmeasured_fields(missing))

    with open(os.path.join(run_dir, "ledger.jsonl"), "w", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    with open(os.path.join(run_dir, "patch.diff"), "w", encoding="utf-8") as f:
        f.write(sess.patch or "")
    with open(os.path.join(run_dir, "messages.json"), "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        f.write(summary.to_json())
    return summary
