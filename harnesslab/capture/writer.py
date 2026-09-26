"""Session -> ledger for captured work, with the corrections the importer's `convert()` lacks.

`importers.common.convert()` stays as it is for manual imports; existing corpora and tests depend on
its shape. This writer is what the capture spine uses, and it differs in five ways, each a defect
`convert()` has on live Claude Code sessions:

  1. one `chat` span per model call, grouped by `Event.request_id`, with usage counted once --
     Claude Code repeats a request's usage on every content-block row;
  2. prose is closed as its own `stop` call when the user speaks, and every span carries `turn`,
     so a turn's final answer is no longer attributed to the next turn's first tool call;
  3. cache-read and cache-write tokens are recorded and priced;
  4. edit spans carry real line counts from `structuredPatch`, and `patch.diff` is synthesised;
  5. the observed fingerprint goes on the END span, so the START span's harness is configuration
     only and its hash does not move as a session grows.

It writes ledger.jsonl, summary.json and patch.diff -- not messages.json (spec §7.3). It never reads
the clock, so the same Session always produces the same bytes.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from harnesslab.backend.importers import common as C
from harnesslab.core import providers as P

MODEL_KINDS = ("assistant", "reasoning", "tool_call")


ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def model_calls(events: list) -> list[dict]:
    """Group model output into calls: {"turn", "req", "texts", "tools", "usage", "ts"}.

    A call is the consecutive model-output events sharing a non-empty `request_id`; tool results in
    between do not close it. Events without ids keep the importer's rule: prose attaches to the tool
    calls that follow it. A `user` event always closes the open call, so trailing prose becomes its own
    call, and it starts a new turn once the current turn has produced output.

    A request id's usage is counted once even when its rows are split across more than one call --
    Claude Code repeats the same usage dict on every content-block row of a request, so if another
    request's rows land between two rows of this one (interleaving), the request re-opens as a second
    call. Only the first call to see a given request id may adopt its usage; any later call for that
    same id gets an explicit zero-usage dict instead of the repeated one, so downstream cost/token
    totals never double-count it.
    """
    calls: list[dict] = []
    cur: Optional[dict] = None
    turn, produced = -1, False
    usage_assigned: set[str] = set()

    def close() -> None:
        nonlocal cur
        if cur is not None and (cur["texts"] or cur["tools"]):
            if cur.pop("usage_real", False) and cur["req"]:  # only a surviving call claims the usage
                usage_assigned.add(cur["req"])
            calls.append(cur)
        cur = None

    for e in events:
        if e.kind == "user":
            close()
            if turn < 0 or produced:
                turn, produced = turn + 1, False
            continue
        if e.kind not in MODEL_KINDS:
            continue
        if turn < 0:
            turn = 0
        rid = e.request_id or ""
        if cur is not None:
            same_request = bool(rid) and rid == cur["req"]
            idless = not rid and not cur["req"] and not (cur["tools"] and e.kind != "tool_call")
            if not (same_request or idless):
                close()
        if cur is None:
            cur = {"turn": turn, "req": rid, "texts": [], "tools": [], "usage": {}, "ts": e.ts}
            if rid and rid in usage_assigned:
                cur["usage"] = dict(ZERO_USAGE)
        if e.kind == "tool_call":
            cur["tools"].append(e)
        elif (e.text or "").strip():
            cur["texts"].append(e.text.strip())
        if e.usage and not cur["usage"]:
            cur["usage"] = dict(e.usage)
            cur["usage_real"] = True
        produced = True
    close()
    return calls


def _usage_of(call: dict) -> dict:
    u = call["usage"] or {}
    return {"input": int(u.get("input_tokens") or 0), "output": int(u.get("output_tokens") or 0),
            "cache_read": int(u.get("cache_read_input_tokens") or 0),
            "cache_write": int(u.get("cache_creation_input_tokens") or 0),
            "estimated": not u}


def _hunk_counts(hunks: list) -> tuple[int, int]:
    added = removed = 0
    for h in hunks or []:
        for line in h.get("lines") or []:
            if isinstance(line, str) and line.startswith("+"):
                added += 1
            elif isinstance(line, str) and line.startswith("-"):
                removed += 1
    return added, removed


def _hunks_to_diff(path: str, hunks: list) -> str:
    rel = (path or "unknown").lstrip("/")
    out = [f"--- a/{rel}", f"+++ b/{rel}"]
    for h in hunks or []:
        out.append(f"@@ -{h.get('oldStart', 0)},{h.get('oldLines', 0)} "
                   f"+{h.get('newStart', 0)},{h.get('newLines', 0)} @@")
        out.extend(line for line in (h.get("lines") or []) if isinstance(line, str))
    return "\n".join(out) + "\n"


def write_run(sess, out_dir: str, run_id: str, task_id: str, harness: dict,
              extra_start: Optional[dict] = None, missing: frozenset = frozenset()):
    """Write one captured run into `out_dir`. Returns (RunSummary, extras) where extras carries the
    totals RunSummary has no field for: cache tokens and unknown record types.

    `missing` is what the source's descriptor declares it does not record (adapters.missing). With
    no tool spans, a requested tool stays on its chat span and nothing is written for its execution
    -- no status, no edit, no boundary event -- because none of it was observed. With no usage,
    tokens and cost are null rather than estimated. The summary fields either one would have filled
    are null, and the end span lists them under `unmeasured`: unknown, never zero.
    """
    from harnesslab.core.ledger import RunSummary

    tools_known = "tool_spans" not in missing
    usage_known = "usage" not in missing

    model = sess.model or "unknown"
    harness_id = harness["id"]
    os.makedirs(out_dir, exist_ok=True)
    summary = RunSummary(run_id=run_id, task_id=task_id, harness_id=harness_id, model=model,
                         provider=f"capture:{sess.source}", repeat_index=0,
                         started_at=next((e.ts for e in sess.events if e.ts), ""))
    summary.harness_hash = harness.get("hash", "")
    spans: list[dict] = []

    def rec(span: str, ts: str = "", **fields) -> None:
        r = {"run_id": run_id, "task_id": task_id, "harness_id": harness_id,
             "gen_ai.request.model": model, "seq": len(spans), "ts": ts or "", "span": span}
        r.update(fields)
        spans.append(r)

    rec("invoke_agent", ts=summary.started_at, status="start", harness=harness, seed=None, repeat_index=0,
        import_source=sess.source, source_path=os.path.basename(sess.path), session_id=sess.session_id,
        **(extra_start or {}))

    results: dict = {}
    for e in sess.events:
        if e.kind == "tool_result" and e.call_id:
            results.setdefault(e.call_id, e)

    calls = model_calls(sess.events)
    cache_read = cache_write = 0
    patch_parts: list[str] = []
    last_edit = last_test = -1
    submitted = False

    for step, call in enumerate(calls):
        u = _usage_of(call)
        text = "\n".join(call["texts"])
        mapped = [C.map_tool(t.name, t.args, sess.extra.get("tool_overrides")) for t in call["tools"]]
        if usage_known:
            out_tok = u["output"] if not u["estimated"] else max(1, len(text) // 4)
            cost = P.cost_usd(model, u["input"], out_tok,
                              cache_read_tokens=u["cache_read"], cache_write_tokens=u["cache_write"])
            usage = {"gen_ai.usage.input_tokens": u["input"], "gen_ai.usage.output_tokens": out_tok,
                     "gen_ai.usage.cache_read_input_tokens": u["cache_read"],
                     "gen_ai.usage.cache_creation_input_tokens": u["cache_write"],
                     "usage_estimated": u["estimated"]}
        else:
            # Not estimated: an estimate of a number the source never recorded is still a number,
            # and every aggregate would add it up as if it had been measured.
            cost = None
            usage = {"gen_ai.usage.input_tokens": None, "gen_ai.usage.output_tokens": None,
                     "gen_ai.usage.cache_read_input_tokens": None,
                     "gen_ai.usage.cache_creation_input_tokens": None, "usage_estimated": False}
        rec("chat", ts=call["ts"], **{
            "gen_ai.operation.name": "chat", **usage,
            "gen_ai.response.finish_reasons": ["tool_calls"] if call["tools"] else ["stop"],
            "duration_ms": 0, "cost_usd": cost, "step": step, "turn": call["turn"],
            "request_id": call["req"], "text": text[:2000],
            "tool_calls": [{"name": tool, "arguments": args} for tool, args in mapped]})
        summary.steps += 1
        if usage_known:
            summary.input_tokens += u["input"]
            summary.output_tokens += out_tok
            summary.cost_usd += cost
            cache_read += u["cache_read"]
            cache_write += u["cache_write"]

        if not tools_known:
            continue                # requested, on the chat span above; execution never recorded
        for t, (tool, args) in zip(call["tools"], mapped):
            res = results.get(t.call_id) if t.call_id else None
            obs = (res.text if res else "") or ""
            facts = (res.raw if res else {}) or {}
            ok = res.ok if res else None
            if facts.get("timed_out_ms") or facts.get("interrupted") or ok is False:
                status = "error"
            elif ok is True:
                status = "ok"
            else:
                status = "error" if (tool != "submit" and C._ERR_OBS.search(obs[:600])) else "ok"
            tp = C.tests_passed_for(tool, args, obs, ok)
            extra: dict = {"tests_passed": tp} if tp is not None else {}
            if facts.get("timed_out_ms"):
                extra["timed_out_ms"] = facts["timed_out_ms"]
            if facts.get("child_agent_id"):
                extra["child_agent_id"] = facts["child_agent_id"]
            rec("execute_tool", ts=(res.ts if res else t.ts), **{
                "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool, "args": args,
                "status": status, "duration_ms": 0, "result_preview": obs[:400],
                # An adapter may RE-NAME a call when the source proves what it really did (Codex
                # runs apply_patch through the shell, so the edit is only visible in the FileChange
                # record). `native_name` is where that adapter parks what was actually invoked, and
                # the ledger must report that rather than the derived name.
                "native_tool": (t.args or {}).get("native_name") or t.name,
                "import_source": sess.source,
                "step": step, "turn": call["turn"], **extra})
            summary.tool_calls += 1

            if tool in ("edit_file", "write_file") and status == "ok":
                hunks = facts.get("structured_patch") or []
                added, removed = _hunk_counts(hunks)
                path = args.get("path", "") or facts.get("file_path", "")
                rec("edit", ts=t.ts, path=path, lines_added=added, lines_removed=removed, tool=tool,
                    step=step, turn=call["turn"], line_counts_known=bool(hunks))
                summary.edits += 1
                summary.lines_added += added
                summary.lines_removed += removed
                if hunks:
                    patch_parts.append(_hunks_to_diff(path, hunks))
                if path and path not in summary.files_touched:
                    summary.files_touched.append(path)
                last_edit = step
                if C.is_test_path(path):
                    summary.tests_modified = True

            if tool == "bash":
                for kind in C.destructive_kinds(args.get("command", "")):
                    rec("boundary_event", ts=t.ts, kind=kind, status="allowed", tool="bash", args=args,
                        step=step, turn=call["turn"], note="observed in an external harness, not enforced")
                    summary.boundary_events += 1
                    if kind not in summary.boundary_kinds:
                        summary.boundary_kinds.append(kind)
            if tp is not None:
                summary.tests_run_by_agent += 1
                last_test = step
            if tool == "submit":
                submitted = True

    stopped_calling_tools = bool(calls) and not calls[-1]["tools"]
    summary.exit_reason = C._exit_reason(sess, submitted, summary.steps, stopped_calling_tools)
    summary.ran_tests_before_submit = last_test >= last_edit >= 0
    patch = sess.patch or "".join(patch_parts)
    summary.patch_bytes = len(patch.encode())
    summary.hidden_pass = sess.hidden_pass
    last_tp = next((s.get("tests_passed") for s in reversed(spans)
                    if s["span"] == "execute_tool" and "tests_passed" in s), None)
    summary.visible_pass = bool(last_tp) if last_tp is not None else None
    summary.strong_pass = None
    summary.error = "" if summary.exit_reason != "error" else (sess.exit_status or "")[:200]
    summary.finished_at = next((e.ts for e in reversed(sess.events) if e.ts), "")
    if missing:
        C.blank_unmeasured(summary, missing)

    meta = {"source": sess.source, "agent": sess.agent or sess.source, "agent_version": sess.agent_version,
            "model": sess.model, "temperature": sess.temperature, "declared_tools": sess.declared_tools,
            "system_prompt": sess.system_prompt, "max_steps_declared": sess.max_steps_declared,
            "tool_overrides": sess.extra.get("tool_overrides")}
    unknown = dict(sess.extra.get("unknown_record_types") or {})
    if sess.hidden_pass is not None:
        rec("grade", visible=summary.visible_pass, hidden=summary.hidden_pass, strong=None,
            tests_modified=summary.tests_modified, source=sess.outcome_source or f"{sess.source} trace")
    rec("invoke_agent", ts=summary.finished_at, status="end", exit_reason=summary.exit_reason,
        hidden_pass=summary.hidden_pass, cost_usd=summary.cost_usd,
        cost_priced=P.is_priced(model),
        total_tokens=(summary.input_tokens + summary.output_tokens) if usage_known else None,
        cache_read_input_tokens=cache_read if usage_known else None,
        cache_creation_input_tokens=cache_write if usage_known else None,
        observed_fingerprint=C.fingerprint(sess.events, meta),
        unknown_record_types=unknown,
        native_exit_status=sess.exit_status, outcome_known=sess.hidden_pass is not None,
        unmeasured=C.unmeasured_fields(missing))

    with open(os.path.join(out_dir, "ledger.jsonl"), "w", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    with open(os.path.join(out_dir, "patch.diff"), "w", encoding="utf-8") as f:
        f.write(patch)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        f.write(summary.to_json())
    return summary, {"cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_write,
                     "unknown_record_types": unknown, "unmeasured": C.unmeasured_fields(missing)}
