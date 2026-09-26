"""Follow a run's ledger as it is written, and classify runs on disk by what their ledger says.

One mechanism for both launch paths (the web app's jobs and `python -m harnesslab run`): the
file `data/runs/<dir>/<run_id>/ledger.jsonl` is the only source of truth. `Ledger.record` writes
and flushes one line per span (core/ledger.py), so a reader that tails the file sees each span
within one poll interval of it being recorded. Stdlib only; nothing here imports the runner or
the backend.

  tail(path, ...)       generator: every complete span already in the file, then each appended one
  describe(path)        identity (task, harness, model, repeat) and state from the first and the
                        last complete line only -- never the whole file
  state_of(path)        running | finished | abandoned
  scan(runs_root, ...)  describe() over every run written recently, newest write first
  lanes(rec)            (agent lane, harness lane) text for one span
  render_line(rec)      one terminal line: `seq  agent │ harness` (the CLI `watch` view)

A run is *running* when its ledger has no `invoke_agent end` span and was written within the
last RUNNING_GRACE_S seconds; *abandoned* when it has no end span and has been silent longer
(the process died: a server restart, a Ctrl-C, a crash); *finished* when the end span is there.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Iterator, Optional

#: No end span and no write for this long -> abandoned, not running.
RUNNING_GRACE_S = 600.0
#: How far back from the end of a ledger describe() reads for the last complete line. A span is
#: bounded by the harness (text[:2000], result_preview[:400], args at 300 chars each), far under this.
_TAIL_CHUNK = 65536


def is_end(rec) -> bool:
    """The span that closes every ledger: `invoke_agent` with status `end`."""
    return isinstance(rec, dict) and rec.get("span") == "invoke_agent" and rec.get("status") == "end"


def is_start(rec) -> bool:
    return isinstance(rec, dict) and rec.get("span") == "invoke_agent" and rec.get("status") == "start"


def _parse(raw: bytes) -> Optional[dict]:
    try:
        rec = json.loads(raw.decode("utf-8"))
    except ValueError:            # JSONDecodeError and UnicodeDecodeError are both ValueErrors
        return None
    return rec if isinstance(rec, dict) else None


def tail(path: str, from_seq: int = 0, poll: float = 0.25, idle_timeout: float = RUNNING_GRACE_S,
         stop: Optional[Callable[[], bool]] = None) -> Iterator[dict]:
    """Yield every complete span in `path` with seq >= from_seq, then follow the file.

    A line without its trailing newline is held -- the file position is rewound to its start --
    until the writer finishes it: Ledger writes each span as one write+flush, but a reader can
    still land between the two. A line that is not a JSON object is reported once as
    `{"span": "live_status", "status": "unreadable_line", "line": n}` (1-based) and skipped, so
    a caller can count them without the stream dying -- but only once this call has actually
    resumed the stream: a bad line still sitting before `from_seq` (before the first good line
    with seq >= from_seq) is part of history a resuming caller already saw reported, on an
    earlier connection, so it is skipped silently instead of being re-yielded on every resume.

    Stops after yielding the `invoke_agent end` span; after `idle_timeout` seconds without the
    file growing, yielding `{"span": "live_status", "status": "abandoned"}` first; or as soon as
    `stop()` returns True (checked once per poll), yielding nothing more.
    """
    line_no = 0
    partial_len = 0
    last_growth = time.monotonic()
    resumed = from_seq <= 0     # from_seq=0 is the start: every line is already "after" it
    with open(path, "rb") as f:
        while True:
            pos = f.tell()
            raw = f.readline()
            if raw.endswith(b"\n"):
                last_growth = time.monotonic()
                partial_len = 0
                line_no += 1
                rec = _parse(raw)
                if rec is None:
                    if resumed:
                        yield {"span": "live_status", "status": "unreadable_line", "line": line_no}
                    continue
                seq = rec.get("seq")
                if isinstance(seq, int) and seq < from_seq:
                    if is_end(rec):
                        return                       # resumed past the end: nothing left to follow
                    continue
                resumed = True                        # this is the first good line with seq >= from_seq (or unseqed)
                yield rec
                if is_end(rec):
                    return
                continue
            if raw:                                   # a partial line: wait for the rest of it
                f.seek(pos)
                if len(raw) != partial_len:
                    partial_len = len(raw)
                    last_growth = time.monotonic()
            if stop is not None and stop():
                return
            if time.monotonic() - last_growth >= idle_timeout:
                yield {"span": "live_status", "status": "abandoned"}
                return
            time.sleep(poll)


def _first_and_last_lines(path: str):
    """(first complete line, last complete line) of `path`, each None when there is none, reading
    only the head and a bounded chunk of the tail. An unterminated last line is not complete."""
    with open(path, "rb") as f:
        first = f.readline()
        if not first.endswith(b"\n"):
            return None, None
        f.seek(0, os.SEEK_END)
        size = f.tell()
        chunk = min(size, _TAIL_CHUNK)
        f.seek(size - chunk)
        data = f.read(chunk)
    if data.endswith(b"\n"):
        complete = data[:-1]
    elif b"\n" in data:
        complete = data.rsplit(b"\n", 1)[0]
    else:
        return first, None
    if chunk < size and b"\n" not in complete:
        return first, None                    # the chunk starts inside the last line: do not guess
    return first, complete.rsplit(b"\n", 1)[-1]


def describe(path: str, now: Optional[float] = None, grace: float = RUNNING_GRACE_S) -> dict:
    """Identity and state of one run from its ledger's first and last complete line only."""
    first, last = _first_and_last_lines(path)
    head = _parse(first) if first else None
    tail_rec = _parse(last) if last else None
    mtime = os.path.getmtime(path)
    now = time.time() if now is None else now
    if tail_rec is not None and is_end(tail_rec):
        state = "finished"
    elif now - mtime < grace:
        state = "running"
    else:
        state = "abandoned"
    h = head or {}
    t = tail_rec or {}
    seq = t.get("seq")
    return {"task_id": h.get("task_id"), "harness_id": h.get("harness_id"),
            "model": h.get("gen_ai.request.model"), "repeat_index": h.get("repeat_index"),
            "started_ts": h.get("ts"), "last_ts": t.get("ts") or h.get("ts"),
            "last_seq": seq if isinstance(seq, int) else None, "state": state, "mtime": mtime}


def state_of(path: str, now: Optional[float] = None, grace: float = RUNNING_GRACE_S) -> str:
    return describe(path, now=now, grace=grace)["state"]


def scan(runs_root: str, recent: float = 86400.0, exclude=("captured",), now: Optional[float] = None,
         grace: float = RUNNING_GRACE_S) -> list:
    """Every run under `<runs_root>/<dir>/<run_id>/ledger.jsonl` written within `recent` seconds,
    newest write first: {dir, run_id, task_id, harness_id, model, repeat_index, started_ts,
    last_ts, last_seq, state}.

    Directories named in `exclude` are never entered -- the backend and the CLI pass
    results_scope.PRIVATE_DIRS, so captured sessions stay out of every listing (compared
    case-insensitively, as results_scope does) -- and neither is any symlinked directory at
    either level, so an alias of a private directory is skipped too. Only a stat is taken
    before a ledger is opened, so a large corpus costs one stat per run, not one read.
    """
    now = time.time() if now is None else now
    skip = {n.casefold() for n in exclude}
    try:
        dirs = [d for d in os.scandir(runs_root) if d.is_dir(follow_symlinks=False) and not d.name.startswith(".")]
    except FileNotFoundError:
        return []
    out = []
    for d in sorted(dirs, key=lambda e: e.name):
        if d.name.casefold() in skip:
            continue
        for r in os.scandir(d.path):
            if not r.is_dir(follow_symlinks=False) or r.name.startswith("."):
                continue
            p = os.path.join(r.path, "ledger.jsonl")
            try:
                st = os.stat(p)
            except OSError:
                continue
            if now - st.st_mtime > recent:
                continue
            out.append({"dir": d.name, "run_id": r.name, **describe(p, now=now, grace=grace)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    for x in out:
        del x["mtime"]
    return out


def _short(s, n: int) -> str:
    s = " ".join(str(s if s is not None else "").split())
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def _args(args, n: int = 60) -> str:
    items = (args or {}).items() if isinstance(args, dict) else []
    return _short(", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in items), n)


def _yn(v) -> str:
    return "—" if v is None else ("pass" if v else "fail")


def lanes(rec: dict):
    """(agent lane, harness lane) for one span; either may be empty.

    The same split the run page draws: the agent lane is what the model said and asked for, the
    harness lane is what the harness did about it -- a tool result, an edit, a policy verdict, a
    grade, a sentinel verdict, and finally the end of the run."""
    kind = rec.get("span")
    if kind == "chat":
        calls = [c for c in (rec.get("tool_calls") or []) if isinstance(c, dict)]
        asked = "; ".join(f"{c.get('name')}({_args(c.get('arguments'), 50)})" for c in calls)
        text = _short(rec.get("text"), 80)
        if text and asked:
            return f"{text}  → {asked}", ""
        return (f"→ {asked}" if asked else text or "(no text)"), ""
    if kind == "execute_tool":
        head = f"{rec.get('gen_ai.tool.name')}: {rec.get('status', '')}"
        if "tests_passed" in rec:
            head += " · tests " + ("pass" if rec["tests_passed"] else "fail")
        if rec.get("duration_ms") is not None:
            head += f" · {rec['duration_ms']} ms"
        prev = _short(rec.get("result_preview"), 60)
        return "", head + (f" · {prev}" if prev else "")
    if kind == "edit":
        return "", f"edit {rec.get('path')} +{rec.get('lines_added', 0)} −{rec.get('lines_removed', 0)}"
    if kind == "boundary_event":
        verb = "blocked" if rec.get("status") == "blocked" else "flagged"
        return "", f"{verb}: {rec.get('kind')} ({rec.get('tool')})"
    if kind == "grade":
        return "", (f"grade visible={_yn(rec.get('visible'))} hidden={_yn(rec.get('hidden'))} "
                    f"strong={_yn(rec.get('strong'))}")
    if kind == "sentinel":
        return "", f"sentinel: {rec.get('action') or rec.get('status') or 'scored'} risk={rec.get('risk', '—')}"
    if kind == "invoke_agent":
        if rec.get("status") == "start":
            return (f"run {rec.get('run_id')} · {rec.get('task_id')} · {rec.get('harness_id')} · "
                    f"{rec.get('gen_ai.request.model')} · repeat {rec.get('repeat_index')}"), ""
        cost = rec.get("cost_usd") or 0.0
        return "", (f"end: {rec.get('exit_reason')} · hidden {'pass' if rec.get('hidden_pass') else 'fail'} · "
                    f"${cost:.4f} · {rec.get('total_tokens') or 0} tok")
    if kind == "live_status":
        line = rec.get("line")
        return "", f"[{rec.get('status')}]" + (f" line {line}" if line else "")
    return "", str(kind)


def render_line(rec: dict, width: int = 46) -> str:
    """One terminal line: `seq  agent lane │ harness lane`, the agent lane padded to `width`."""
    a, h = lanes(rec)
    seq = rec.get("seq")
    left = f"{seq:>4}" if isinstance(seq, int) else "    "
    return f"{left}  {_short(a, width):<{width}} │ {_short(h, width)}"
