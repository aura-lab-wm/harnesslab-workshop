"""The capture adapter registry: which sources the spine can capture, and which file is whose.

Declarative about DISCOVERY and IDENTITY -- where a source's session files live under an
allow-listed root, which files belong to it, and how a tie between two sources is broken.
Never about PARSING: that stays in harnesslab/backend/importers/<name>.py, because the four
things these formats really differ in are control flow (a two-pass whole-file predicate,
matching a tool result to a call the source gave no id, replaying an update envelope, and
per-version field fallbacks INSIDE one adapter), and a DSL for control flow is a worse Python.

`resolve()` decides PER FILE. `importers.detect()` sums confidence over a whole tree and returns
one winner, which is the wrong shape here: the allow-list can hold ~/.claude, ~/.codex, ~/.qwen
~/.gemini and ~/.cursor at the same time.

This module is the ONLY registry. Nothing else in harnesslab/capture/ may import an importer
module by name.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from harnesslab.backend.importers import claude_code, codex, cursor, gemini_cli, qwen_code
from harnesslab.backend.importers.common import peek_jsonl

from .allowlist import is_allowed

#: rows read from the head of a candidate file when evaluating requires/rejects gates
PEEK_ROWS = 40

#: a source must reach this confidence before any file is handed to it
MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class CaptureAdapter:
    """One capturable source. `module` is the escape hatch to real parsing code."""
    name: str                         # must equal module.NAME; user-visible via provider=capture:<name>
    module: Any                       # sniff(path) -> float, sessions(path) -> Iterator[Session]
    description: str
    fixture: str                      # basename under tests_agentlab/fixtures/capture/
    golden: str                       # basename of its expected ledger
    globs: tuple = ("**/*.jsonl",)    # narrows the walk under an allow-listed root
    default_roots: tuple = ()         # what the Capture page offers to allow-list
    precedence: int = 0               # higher wins when two descriptors score EQUALLY
    requires: tuple = ()              # dotted key paths that must appear on some peeked row
    rejects: tuple = ()               # dotted key paths whose presence disqualifies the file
    # DECLARED facts about what the source records. The conformance kit holds each one to the
    # fixture in BOTH directions (capability_mismatches): claiming a fact the fixture cannot show is
    # rejected, and so is hiding one it has. A fact declared absent reaches the ledger as UNKNOWN --
    # null, never zero -- through missing() and common.UNMEASURED_FIELDS.
    has_call_ids: bool = True         # every tool call carries the id its result joins on
    has_tool_spans: bool = True       # tool EXECUTION is recorded: a result, not only a request
    has_usage: bool = True            # token counts are recorded, so cost can be priced
    has_timestamps: bool = True       # every conversational event carries a clock
    policy: str = "none"              # which capture_harness policy strategy this source uses


REGISTRY: tuple = (
    CaptureAdapter(
        name="claude_code", module=claude_code,
        description="Claude Code sessions",
        fixture="cc_session.jsonl", golden="cc_session.expected.ledger.jsonl",
        globs=("**/*.jsonl",),
        default_roots=("~/.claude/projects",),
        precedence=50,
        # Qwen Code wraps a GEMINI message body in exactly this envelope. Without the gate,
        # claude_code.sniff scores 0.95 and claude_code.parse_file then returns None: zero runs,
        # no error, no unknown-record count. Measured on the operator's real ~/.qwen chats.
        rejects=("provenance", "message.parts"),
        policy="claude_permission_modes",
    ),
    CaptureAdapter(
        name="codex", module=codex,
        description="OpenAI Codex CLI rollouts",
        fixture="cx_rollout.jsonl", golden="cx_rollout.expected.ledger.jsonl",
        globs=("**/rollout-*.jsonl", "**/*.jsonl"),
        default_roots=("~/.codex/sessions",),
        precedence=50,
        # every rollout record carries a top-level `type`; a file with none is not a rollout
        requires=("type",),
        has_call_ids=True,
        policy="codex_approval",
    ),
    CaptureAdapter(
        name="qwen_code", module=qwen_code,
        description="Qwen Code chats",
        fixture="qwen_chat.jsonl", golden="qwen_chat.expected.ledger.jsonl",
        # Deliberately chats/ only, though <project>/subagents/<parent-session-id>/ holds real
        # work: 160 such files on the corpus this was measured against, 4,143 tool calls, more tool
        # volume than the 24 chats. They resolve correctly now and their parent link survives into
        # the ledger, but `parent_session_id` is a start-span field, NOT a RunSummary field -- an
        # index.jsonl row cannot say a run is child work, and metrics, sentinel and fork all read
        # that index. Widening this glob first would silently inflate every aggregate over the
        # captured corpus. Widen it in the same change that puts the link on the row.
        globs=("**/chats/**/*.jsonl",),
        default_roots=("~/.qwen/projects",),
        # above gemini_cli: Qwen wraps a GEMINI message body, so on a tie the more specific
        # descriptor -- the one whose `requires` gate the other cannot pass -- must win.
        precedence=60,
        requires=(("provenance", "agentId"),),
        has_call_ids=True,
        policy="qwen_decision",
    ),
    CaptureAdapter(
        name="gemini_cli", module=gemini_cli,
        description="Gemini CLI sessions",
        fixture="gemini_session.jsonl", golden="gemini_session.expected.ledger.jsonl",
        globs=("**/chats/**/*.jsonl",),
        default_roots=("~/.gemini/tmp",),
        precedence=50,
        requires=("type",),
        has_call_ids=True,
        # a Gemini CLI session records no approval decision and no sandbox; "unknown" is the
        # honest answer and `none` is the strategy that gives it.
        policy="none",
    ),
    CaptureAdapter(
        name="cursor", module=cursor,
        description="Cursor agent transcripts (partial: messages and tool requests only)",
        fixture="cursor_transcript.jsonl", golden="cursor_transcript.expected.ledger.jsonl",
        # HONESTLY PARTIAL. tools/census_cursor.py over the real corpus: 7 files, 679 rows, 1137
        # tool_use blocks carrying only {type, name, input} -- no id, no tool_result anywhere, no
        # usage, no model, and no clock at any depth (77 key paths). So the model's tool REQUESTS
        # are captured on its chat spans, and everything that would need the execution, the usage or
        # a timestamp is declared missing and lands in the ledger as unknown, never zero. Openness,
        # which backfill otherwise decides from last_ts, comes from the transcript's mtime instead;
        # the mtime never reaches a span.
        #
        # Top-level transcripts only: agent-transcripts/<id>/<id>.jsonl. Child work lives at
        # agent-transcripts/<id>/subagents/<child>.jsonl (3 of the 7 real files) and is excluded
        # for the reason qwen_code excludes its subagents -- the parent link cannot reach an index
        # row, so children would count as peer runs. Nothing else under a project (agent-tools/,
        # canvases/, mcps/, terminals/) is a transcript.
        globs=("**/agent-transcripts/*/*.jsonl",),
        default_roots=("~/.cursor/projects",),
        precedence=50,
        has_call_ids=False, has_tool_spans=False, has_usage=False, has_timestamps=False,
        # the transcript records no approval mode, sandbox or decision
        policy="none",
    ),
)

BY_NAME = {a.name: a for a in REGISTRY}


def private_source_roots() -> list[str]:
    """Every directory on this machine that holds live agent session transcripts.

    Two sources of truth, both already here: what each descriptor declares as its source's home
    (`default_roots`), and whatever the operator actually told capture to watch (the allow-list).
    The first is always available -- the allow-list file has no writer yet and is usually absent --
    so the guard that uses this does not quietly become a no-op on a fresh machine.

    Realpath'd, because the question is what a path REFERS to: a symlink into ~/.claude/projects
    is the same session store under another name.
    """
    from .allowlist import load_paths
    roots, seen = [], set()
    for src in ([r for a in REGISTRY for r in a.default_roots], load_paths()):
        for raw in src:
            real = os.path.realpath(os.path.expanduser(raw))
            if real not in seen:
                seen.add(real)
                roots.append(real)
    return roots


def is_private_source(path: str) -> str:
    """The session-store root `path` lies in, or "" if it lies in none of them."""
    real = os.path.realpath(os.path.expanduser(path or ""))
    for root in private_source_roots():
        base = root.rstrip(os.sep) or os.sep
        if real == base or real.startswith(base.rstrip(os.sep) + os.sep):
            return root
    return ""

#: declared flag -> the capability name missing() reports when the flag is False. Call ids are not
#: here: a source without them still records every call, so nothing becomes unknown.
_MEASURES = (("has_tool_spans", "tool_spans"), ("has_usage", "usage"), ("has_timestamps", "timestamps"))
_CONV = ("user", "assistant", "reasoning", "tool_call", "tool_result", "observation")


def missing(ad: CaptureAdapter) -> frozenset:
    """The capabilities this descriptor declares its source does NOT record."""
    return frozenset(cap for flag, cap in _MEASURES if not getattr(ad, flag))


def observed_capabilities(events: list) -> dict:
    """What an event stream actually demonstrates, flag by flag."""
    calls = [e for e in events if e.kind == "tool_call"]
    conv = [e for e in events if e.kind in _CONV]
    return {
        "has_call_ids": bool(calls) and all(e.call_id for e in calls),
        # a request is not an execution: without a result there is no status and no output
        "has_tool_spans": bool(calls) and any(e.kind in ("tool_result", "observation") for e in events),
        "has_usage": any(e.usage for e in events),
        "has_timestamps": bool(conv) and all(e.ts for e in conv),
    }


def capability_mismatches(ad: CaptureAdapter, events: list) -> list:
    """Every declared flag that disagrees with `events`, as a readable line. Empty means honest."""
    seen = observed_capabilities(events)
    return [f"{ad.name}: declares {flag}={getattr(ad, flag)} but its fixture shows {flag}={seen[flag]}"
            for flag in ("has_call_ids", "has_tool_spans", "has_usage", "has_timestamps")
            if bool(getattr(ad, flag)) != seen[flag]]


def _dig(rec: dict, dotted: str):
    cur: Any = rec
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _gates_pass(ad: CaptureAdapter, rows: list) -> bool:
    for path in ad.rejects:
        if any(_dig(r, path) is not None for r in rows):
            return False
    for path in ad.requires:
        # A tuple means "any of these": one format can have two shapes that no single key spans.
        # Qwen writes `provenance` on chats and `agentId` on subagent sessions, and a gate naming
        # only the first silently excluded 160 files the parser reads perfectly.
        alts = path if isinstance(path, tuple) else (path,)
        if not any(_dig(r, alt) is not None for r in rows for alt in alts):
            return False
    return True


def resolve(path: str, only: Optional[set] = None) -> tuple:
    """(adapter name, confidence) for ONE file. ("", 0.0) when nothing reaches MIN_CONFIDENCE.

    `only` restricts the candidates to those descriptor names. discover() passes the descriptors
    whose OWN glob matched the path, so a narrow glob can exclude; called without it this stays the
    pure content question, which is what detect() and the conformance kit ask.
    """
    try:
        rows = peek_jsonl(path, PEEK_ROWS)
    except OSError:
        return "", 0.0
    if not rows:
        return "", 0.0
    best, score, prec = "", 0.0, -1
    for ad in REGISTRY:
        if only is not None and ad.name not in only:
            continue
        if not _gates_pass(ad, rows):
            continue
        try:
            s = float(ad.module.sniff(path))
        except Exception:
            s = 0.0
        if s > score or (s == score and s > 0.0 and ad.precedence > prec):
            best, score, prec = ad.name, s, ad.precedence
    return (best, score) if score >= MIN_CONFIDENCE else ("", 0.0)


def _walk(root: str) -> tuple:
    """Every file under `root` as a '/'-separated relative path, from ONE traversal.

    Mirrors what glob.glob(..., recursive=True) can reach, so matching against this list finds what
    globbing each pattern separately found: symlinked directories are followed (glob follows them
    too), hidden directories are not entered (glob's `*` and `**` never match a name starting with a
    dot, and no registry pattern names a hidden directory literally). Unlike glob this refuses to
    descend into a directory that is its own ancestor, so a symlink cycle cannot hang a watcher.
    """
    out: list = []
    blocked: list = []

    def visit(abs_dir: str, rel: str, ancestors: frozenset) -> None:
        try:
            st = os.stat(abs_dir)                    # follows a symlinked directory to its target
            key = (st.st_dev, st.st_ino)             # cheaper than realpath, and exact for a cycle
            if key in ancestors:
                return
            ancestors = ancestors | {key}
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except (FileNotFoundError, NotADirectoryError):
            # Not there is not the same as not readable: an allow-listed root a tool has never
            # created is simply empty, and a directory that vanished mid-walk took its files with it.
            return
        except OSError as e:
            # Reported, not swallowed. A directory that EXISTS and cannot be entered contributes no
            # files, and a caller that takes "no files" for "no sources" -- the orphan prune does --
            # would call the live runs under it orphans.
            blocked.append({"path": abs_dir, "error": f"{type(e).__name__}: {e}", "kind": "directory"})
            return
        for e in entries:
            child_rel = f"{rel}/{e.name}" if rel else e.name
            try:
                is_dir = e.is_dir()          # follows symlinks, as glob does
            except OSError:
                is_dir = False
            if is_dir:
                if not e.name.startswith("."):
                    visit(e.path, child_rel, ancestors)
            else:
                out.append(child_rel)

    visit(root, "", frozenset())
    return out, blocked


def _matches(rel: str, pattern: str) -> bool:
    """Whether a relative file path matches a glob pattern, with glob's recursive semantics.

    `**` is zero or more directory segments, `*` stays within one segment, and neither ever matches a
    name that starts with a dot unless the pattern segment does too -- glob's rule, reproduced
    because glob.translate() only exists from Python 3.13 and this project supports 3.10.
    """
    import fnmatch
    parts = rel.split("/")
    pats = pattern.replace(os.sep, "/").split("/")

    def m(i: int, j: int) -> bool:
        if j == len(pats):
            return i == len(parts)
        head = pats[j]
        if head == "**":
            if m(i, j + 1):                           # zero directories
                return True
            # consume one DIRECTORY segment -- never the file itself, never a hidden one
            return i < len(parts) - 1 and not parts[i].startswith(".") and m(i + 1, j)
        if i == len(parts):
            return False
        seg = parts[i]
        if seg.startswith(".") and not head.startswith("."):
            return False
        return fnmatch.fnmatchcase(seg, head) and m(i + 1, j + 1)

    return m(0, 0)


_COMPILED: dict = {}


def _compile(pattern: str):
    """A fast predicate for one pattern, equivalent to _matches(rel, pattern).

    _matches is the reference; this is it compiled to one regex, because matching 19,691 paths
    against five patterns through a recursive function was a third of a steady-state tick. Only
    `**`, `*`, `?` and literals are compiled -- anything else falls back to the reference.
    """
    import re
    if pattern in _COMPILED:
        return _COMPILED[pattern]
    pats = pattern.replace(os.sep, "/").split("/")
    if any(ch in seg for seg in pats for ch in "[]"):
        fn = lambda rel, _p=pattern: _matches(rel, _p)          # noqa: E731
        _COMPILED[pattern] = fn
        return fn
    out = []
    for k, seg in enumerate(pats):
        last = k == len(pats) - 1
        if seg == "**":
            # zero or more DIRECTORY segments, none hidden; as the last segment it would match files,
            # which no registry pattern needs, so it is not compiled that way
            out.append(r"(?:[^/.][^/]*/)*" if not last else r"(?:[^/.][^/]*/)*[^/.][^/]*")
            continue
        body = "".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in seg)
        guard = "" if seg.startswith(".") else r"(?!\.)"
        out.append(guard + body + ("" if last else "/"))
    rx = re.compile("".join(out) + r"\Z")
    fn = lambda rel, _rx=rx: _rx.match(rel) is not None          # noqa: E731
    _COMPILED[pattern] = fn
    return fn


def _claims_under(path: str, roots: list) -> Optional[set]:
    """The adapters whose glob claims `path` under one of `roots`, or None when it lies outside them.

    None means "unscoped", which is what an explicit file request means everywhere else; an empty set
    means the roots contain it and no glob claims it, which is a different answer.
    """
    real = os.path.realpath(path)
    for root in roots or ():
        r = os.path.realpath(root)
        if real == r or real.startswith(r + os.sep):
            rel = os.path.relpath(real, r).replace(os.sep, "/")
            names = set()
            for ad in REGISTRY:
                for pat in ad.globs:
                    if _compile(pat)(rel):
                        names.add(ad.name)
                        break
            return names
    return None


def discover(roots: list, skip: Optional[Callable[[str, os.stat_result], bool]] = None,
             only_under: Optional[list] = None) -> tuple:
    """[(realpath, adapter)] under `roots`, plus the errors met on the way.

    A candidate is globbed by at least one descriptor, then resolved on its own CONTENTS -- but only
    against the descriptors whose OWN glob matched it. The union used to decide the walk and contents
    alone decided the winner, which made every narrow glob void: claude_code and codex both glob
    `**/*.jsonl`, so qwen_code's deliberate `**/chats/**/*.jsonl` excluded nothing and its subagent
    siblings were claimed as peer runs. A glob now narrows the walk AND scopes the claim. Paths are realpath'd once and checked against
    the allow-list itself, so a symlink inside an allowed directory cannot pull in a file from
    outside it, and the same file reached through two roots is reported once.

    `skip(realpath, stat_result) -> bool` is the ONE extension point. The sniffer passes its
    cursor rule here -- a source whose (size, mtime_ns) still match what it already captured is
    skipped without being opened or sniffed -- so there is exactly one walk in the spine rather
    than one per caller. It is called for EVERY allow-listed candidate, before anything is
    opened, so a closure that records the path also learns which sources this pass saw.

    An unreadable or vanished candidate is reported by its realpath, never fatal: it is dropped
    into `errors` and every other candidate is still checked. The one-byte open below is what
    makes that true -- `resolve()` answers ("", 0.0) for a file it cannot read, which would
    otherwise drop an unreadable source silently, indistinguishable from an unclaimed one.
    """
    real_roots = [os.path.realpath(r) for r in roots]
    found: dict = {}
    seen: set = set()
    errors: list = []
    for root in real_roots:
        eligible: dict = {}
        if os.path.isfile(root):
            # An explicit file path is an explicit request: every descriptor may answer for it --
            # UNLESS the caller names the roots this file is supposed to sit under, in which case it
            # is answered by the descriptors whose glob claims it THERE, exactly as the walk would.
            eligible[root] = _claims_under(root, only_under) if only_under else None
        else:
            # ONE walk, every descriptor matched against it. Globbing per pattern walked the whole
            # tree once per pattern per pass: on this machine 401,000 scandir calls and 35 seconds of
            # a 43-second tick, to find five changed files.
            files, blocked = _walk(root)
            errors.extend(blocked)
            by_pattern: dict = {}
            for ad in REGISTRY:
                for pat in ad.globs:
                    by_pattern.setdefault(pat, set()).add(ad.name)
            for pat, names in by_pattern.items():          # each UNIQUE pattern matched once
                match = _compile(pat)
                for rel in files:
                    if match(rel):
                        hit = os.path.join(root, *rel.split("/"))
                        eligible.setdefault(hit, set()).update(names)
        candidates: set = set(eligible)
        for p in sorted(candidates):
            real = os.path.realpath(p)
            if real in seen:
                continue
            try:
                if not os.path.isfile(real) or not is_allowed(real, real_roots):
                    continue
                if skip is not None and skip(real, os.stat(real)):
                    seen.add(real)
                    continue
                with open(real, "rb"):          # readable? an unreadable source is an error
                    pass
                name, _ = resolve(real, only=eligible.get(p))
            except OSError as e:
                seen.add(real)
                errors.append({"path": real, "error": f"{type(e).__name__}: {e}"})
                continue
            seen.add(real)
            if name:
                found[real] = name
    return [(p, found[p]) for p in sorted(found)], errors


def describe() -> list:
    """One row per capturable source, for the Capture page's source list."""
    return [{"name": a.name, "description": a.description,
             "default_roots": [os.path.expanduser(r) for r in a.default_roots],
             "globs": list(a.globs), "has_call_ids": a.has_call_ids,
             "has_tool_spans": a.has_tool_spans, "has_usage": a.has_usage,
             "has_timestamps": a.has_timestamps, "policy": a.policy}
            for a in REGISTRY]
