"""Span and ledger builders for the live-run tests. Not a test module (unittest discover only
loads test_*.py); imported by the test files next to it, which discover puts on sys.path."""
from __future__ import annotations

import json
import os

RUN = {"run_id": "r1", "task_id": "t01_slugify", "harness_id": "baseline", "gen_ai.request.model": "mock"}


def span(seq: int, kind: str, **fields) -> dict:
    """A ledger record with the identity fields every real span carries (core/ledger.py:41-49)."""
    return {**RUN, "seq": seq, "ts": f"2026-09-19T00:00:{seq % 60:02d}.000Z", "span": kind, **fields}


def start(seq: int = 0, **fields) -> dict:
    return span(seq, "invoke_agent", status="start", harness={"id": "baseline", "max_steps": 20},
                harness_hash="c73ed79b7b02", seed=1, repeat_index=0, **fields)


def end(seq: int, exit_reason: str = "submitted", hidden_pass: bool = True) -> dict:
    return span(seq, "invoke_agent", status="end", exit_reason=exit_reason, hidden_pass=hidden_pass,
                cost_usd=0.0123, total_tokens=1234)


def chat(seq: int, step: int, text: str = "Reading the file.", calls=(("read_file", {"path": "a.py"}),)) -> dict:
    return span(seq, "chat", **{"gen_ai.operation.name": "chat", "gen_ai.usage.input_tokens": 100 + step,
                                "gen_ai.usage.output_tokens": 10, "gen_ai.response.finish_reasons": ["tool_calls"],
                                "duration_ms": 12, "cost_usd": 0.001, "step": step, "text": text,
                                "tool_calls": [{"name": n, "arguments": a} for n, a in calls]})


def tool(seq: int, name: str = "read_file", status: str = "ok", preview: str = "x = 1", **extra) -> dict:
    return span(seq, "execute_tool", **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
                                        "args": {"path": "a.py"}, "status": status, "duration_ms": 3,
                                        "result_preview": preview, **extra})


def edit(seq: int, path: str = "textkit/slug.py", added: int = 2, removed: int = 1) -> dict:
    return span(seq, "edit", path=path, lines_added=added, lines_removed=removed, tool="edit_file")


def boundary(seq: int, kind: str = "path_escape", status: str = "blocked", tool_name: str = "write_file") -> dict:
    rec = span(seq, "boundary_event", tool=tool_name, args={"path": "../x"}, status=status)
    rec["kind"] = kind          # the event's own "kind" collides with span()'s span-type parameter
    return rec


def grade(seq: int, visible=True, hidden=True, strong=False) -> dict:
    return span(seq, "grade", visible=visible, hidden=hidden, strong=strong, tests_modified=False)


def write(path: str, spans, trailing_newline: bool = True) -> str:
    """Write `spans` as JSONL. With trailing_newline=False the LAST line is left unterminated,
    which is exactly what a reader sees between a writer's write() and its newline."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [json.dumps(s, ensure_ascii=False) for s in spans]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if trailing_newline and lines else ""))
    return path


def append(path: str, spans) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
            f.flush()


def append_raw(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)
        f.flush()


def ledger(root: str, dirname: str, run_id: str, spans, trailing_newline: bool = True) -> str:
    return write(os.path.join(root, dirname, run_id, "ledger.jsonl"), spans, trailing_newline)
