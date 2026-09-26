"""Interoperability exports: get a harnesslab run out of harnesslab.

A measurement platform that can only be read by its own UI is a dead end for reuse. These endpoints
render the same ledgers into formats other tools already read:

  GET /api/export/{dir}/runs/{run_id}.trajectory.jsonl   Letta *Trajectory* v1 (agent-experience data)
  GET /api/export/{dir}/runs/{run_id}.inspect.json       Inspect AI ``EvalLog`` (UK AISI's eval format)
  GET /api/export/{dir}/index.csv                        one row per run, for R / pandas
  GET /api/export/{dir}/cells.csv                        one row per (model, harness) cell, with 95% CIs
  GET /api/export/{dir}/bundle.zip                       index + ledgers + harnesses + sentinel, for Zenodo
  GET /api/export/formats                                what this module can produce, and from what

Trajectory field names follow the published v1 JSON Schema
(https://github.com/letta-ai/trajectory, ``schema/trajectory-v1.schema.json``, ``$id``
``https://letta.ai/schemas/trajectory/v1.json``; announced in https://www.letta.com/blog/trajectory/).
Records are objects with ``additionalProperties: false``, so nothing harnesslab-specific is smuggled
into them: ``meta`` {role, source, cwd?, git_branch?, model?}; ``system``/``user``/``reasoning``/
``observation`` {role, content, timestamp}; ``assistant`` {role, content, timestamp, tool_calls?}
where ``content`` MUST be null when ``tool_calls`` is present and each call is {id, name, args} with
``args`` a *stringified* JSON object; ``tool`` {role, tool_call_id, content, ok?, timestamp}.

Inspect AI objects are built with ``inspect_ai.log`` classes and serialised through
``write_eval_log``, so ``read_eval_log`` round-trips the output. ``inspect_ai`` is an optional
extra (``pip install 'harnesslab[inspect]'``) and is imported lazily.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import time
import zipfile
from typing import Iterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from .paths import HARNESS_DIR, LAB_ROOT, MODELS_DIR, RUNS_ROOT, TASK_DIR

router = APIRouter(prefix="/api/export", tags=["export"])

TRAJECTORY_SCHEMA = "https://letta.ai/schemas/trajectory/v1.json"
SOURCE_ID = "harnesslab"
#: Used only when the source ledger carries no wall-clock timestamps at all (imported SWE-agent runs).
FALLBACK_EPOCH = "1970-01-01T00:00:00.000Z"


# --------------------------------------------------------------------------- small local helpers
def _dir(name: str) -> str:
    d = os.path.join(RUNS_ROOT, name)
    if not os.path.exists(os.path.join(d, "index.jsonl")):
        raise HTTPException(404, f"no results dir {name}")
    return d


def _rows(name: str) -> list[dict]:
    """Index rows. Uses the SQLite cache when it is available, else reads the file."""
    try:
        from . import store
        return store.rows(name)
    except Exception:                                     # pragma: no cover - cache is optional
        from harnesslab.core.analysis import load_index
        return load_index(_dir(name))


def _run_dir(name: str, run_id: str) -> str:
    d = os.path.join(_dir(name), run_id)
    if not os.path.isdir(d):
        raise HTTPException(404, f"no run {run_id} in {name}")
    return d


def _spans(name: str, run_id: str) -> list[dict]:
    p = os.path.join(_run_dir(name, run_id), "ledger.jsonl")
    if not os.path.exists(p):
        raise HTTPException(404, f"run {run_id} has no ledger.jsonl")
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _summary(name: str, run_id: str) -> dict:
    p = os.path.join(_run_dir(name, run_id), "summary.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    for r in _rows(name):
        if r.get("run_id") == run_id:
            return r
    return {}


def _issue_for(task_id: str) -> str:
    p = os.path.join(TASK_DIR, task_id)
    if task_id and os.path.exists(os.path.join(p, "task.json")):
        from harnesslab.core.grader import load_task
        return load_task(p)["issue"]
    return (f"(no local task definition for {task_id}: this run was imported, and the issue text "
            f"lives in the source dataset)")


def _clean(x):
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def model_usage_counts(summary: dict) -> Optional[tuple[int, int]]:
    """(input, output) for the exported usage field, or None when the source recorded no usage.

    `int(summary.get("input_tokens") or 0)` turned unknown into an eval log asserting the run
    consumed zero tokens -- indistinguishable, to any tool that reads the log, from a genuinely
    free run. An export that leaves the usage field off makes no claim at all, which is the only
    truthful thing to say about a run nobody metered (importers.common.UNMEASURED_FIELDS)."""
    tin, tout = summary.get("input_tokens"), summary.get("output_tokens")
    if tin is None or tout is None:
        return None
    return int(tin), int(tout)


# --------------------------------------------------------------------------- timestamps
def _ts_source(spans: list[dict], summary: dict, run_dir: str) -> tuple[str, bool]:
    """(base timestamp, synthesized?) -- the Trajectory schema requires a timestamp on every record."""
    for s in spans:
        if s.get("ts"):
            return s["ts"], False
    for key in ("started_at", "finished_at"):
        if summary.get(key):
            return summary[key], False
    try:
        t = os.path.getmtime(os.path.join(run_dir, "ledger.jsonl"))
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z", True
    except OSError:
        return FALLBACK_EPOCH, True


def _norm_ts(ts: str, base: str, seq: int) -> str:
    """Normalise to the schema's pattern; ledgers without a ts get base + seq milliseconds."""
    if ts:
        return ts if ts.endswith("Z") or "+" in ts[10:] else ts + "Z"
    ms = seq % 1000
    head = base[:-1] if base.endswith("Z") else base
    if "." in head[10:]:
        head = head.split(".")[0]
    return f"{head}.{ms:03d}Z"


# --------------------------------------------------------------------------- (a) Letta Trajectory v1
def trajectory_records(spans: list[dict], issue: str, summary: dict, run_dir: str = "") -> list[dict]:
    """Render one harnesslab ledger as Trajectory v1 records (see the module docstring for the spec).

    Mapping, chosen so the export is faithful rather than lossy:

      ``invoke_agent`` start  -> ``meta`` + ``system`` (the harness system prompt) + ``user`` (the issue)
      ``chat``                -> ``reasoning`` (the model's narration) then ``assistant`` (its tool calls)
      ``execute_tool``        -> ``tool`` (``ok`` = the ledger's ``status == "ok"``)
      ``edit`` / ``grade`` / ``boundary_event`` / ``sentinel`` -> ``observation``

    The last four are harnesslab's measurement spans; Trajectory has no slot for them, and
    ``observation`` is the record type the spec reserves for out-of-band context, so they are
    emitted there as short human-readable lines rather than dropped.
    """
    base, synth = _ts_source(spans, summary, run_dir)
    out: list[dict] = []
    start = next((s for s in spans if s.get("span") == "invoke_agent" and s.get("status") == "start"), {})
    harness = start.get("harness") or {}
    model = start.get("gen_ai.request.model") or summary.get("model") or ""

    meta = {"role": "meta", "source": SOURCE_ID}
    if harness.get("id"):
        meta["cwd"] = f"/{summary.get('task_id') or start.get('task_id') or 'task'}"
    if model:
        meta["model"] = model
    out.append(meta)

    t0 = _norm_ts(start.get("ts", ""), base, 0)
    if harness.get("system_prompt"):
        out.append({"role": "system", "content": harness["system_prompt"], "timestamp": t0})
    out.append({"role": "user", "content": issue, "timestamp": t0})

    pending: list[str] = []                                # tool-call ids awaiting their result
    run_id = summary.get("run_id") or start.get("run_id") or "run"
    for s in spans:
        span, seq = s.get("span"), int(s.get("seq") or 0)
        ts = _norm_ts(s.get("ts", ""), base, seq)
        if span == "chat":
            text = (s.get("text") or "").strip()
            calls = s.get("tool_calls") or []
            if text:
                out.append({"role": "reasoning", "content": text, "timestamp": ts})
            if calls:
                tcs = []
                for i, c in enumerate(calls):
                    cid = f"{run_id}:{seq}:{i}"
                    tcs.append({"id": cid, "name": c.get("name") or "tool",
                                "args": json.dumps(c.get("arguments") or {}, sort_keys=True)})
                    pending.append(cid)
                out.append({"role": "assistant", "content": None, "timestamp": ts, "tool_calls": tcs})
            elif not text:
                out.append({"role": "assistant", "content": "(no content)", "timestamp": ts})
        elif span == "execute_tool":
            cid = pending.pop(0) if pending else f"{run_id}:{seq}:orphan"
            out.append({"role": "tool", "tool_call_id": cid,
                        "content": s.get("result_preview") or "", "ok": s.get("status") == "ok",
                        "timestamp": ts})
        elif span == "edit":
            out.append({"role": "observation", "timestamp": ts,
                        "content": f"edit {s.get('path')}: +{s.get('lines_added', 0)} -{s.get('lines_removed', 0)}"})
        elif span == "boundary_event":
            out.append({"role": "observation", "timestamp": ts,
                        "content": f"boundary_event {s.get('kind')} ({s.get('status')}): {s.get('detail', '')}".strip()})
        elif span == "sentinel":
            out.append({"role": "observation", "timestamp": ts,
                        "content": f"sentinel risk={s.get('risk')} action={s.get('action')} "
                                   f"patterns={','.join(p.get('id', '') for p in (s.get('patterns') or []))}".strip()})
        elif span == "grade":
            out.append({"role": "observation", "timestamp": ts,
                        "content": "grade " + json.dumps({k: v for k, v in s.items()
                                                          if k in ("visible_pass", "hidden_pass", "strong_pass",
                                                                   "tests_modified")}, sort_keys=True)})
        elif span == "invoke_agent" and s.get("status") == "end":
            # This span carries total_tokens; input/output live on the chat spans. Reading the two
            # that are never there reported every metered run as unknown.
            tin, tout, ttot = s.get("input_tokens"), s.get("output_tokens"), s.get("total_tokens")
            if tin is not None and tout is not None:
                tok = f"{tin}+{tout}"
            elif ttot is not None:
                tok = str(ttot)
            else:
                tok = "unknown"
            out.append({"role": "observation", "timestamp": ts,
                        "content": f"invoke_agent end: exit_reason={s.get('exit_reason')} "
                                   f"steps={s.get('steps')} tokens={tok}"})
    if synth:
        out.append({"role": "observation", "timestamp": _norm_ts("", base, 999),
                    "content": "note: the source ledger carries no wall-clock timestamps; timestamps in "
                               "this export are synthesized from the ledger file's mtime plus the span sequence."})
    return out


@router.get("/{name}/runs/{run_id}.trajectory.jsonl", response_class=PlainTextResponse)
def export_trajectory(name: str, run_id: str):
    """One run as Letta Trajectory v1 JSONL (one record per line)."""
    spans = _spans(name, run_id)
    summary = _summary(name, run_id)
    task_id = summary.get("task_id") or (spans[0].get("task_id") if spans else "")
    recs = trajectory_records(spans, _issue_for(task_id), summary, _run_dir(name, run_id))
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n"
    return PlainTextResponse(body, media_type="application/x-ndjson", headers={
        "Content-Disposition": f'attachment; filename="{run_id}.trajectory.jsonl"',
        "X-Trajectory-Schema": TRAJECTORY_SCHEMA,
    })


# --------------------------------------------------------------------------- (b) Inspect AI EvalLog
def _inspect_log(name: str, run_id: str):
    """Build an ``inspect_ai.log.EvalLog`` with a single sample for one harnesslab run."""
    try:
        from inspect_ai.log import (EvalConfig, EvalDataset, EvalLog, EvalMetric, EvalResults,
                                    EvalSample, EvalScore, EvalSpec, EvalStats)
        from inspect_ai.model import (ChatMessageAssistant, ChatMessageSystem, ChatMessageTool,
                                      ChatMessageUser, ModelUsage)
        from inspect_ai.scorer import Score
        from inspect_ai.tool import ToolCall
    except ImportError as e:                               # pragma: no cover - optional extra
        raise HTTPException(501, "inspect_ai is not installed: pip install 'harnesslab[inspect]'") from e

    spans, summary = _spans(name, run_id), _summary(name, run_id)
    start = next((s for s in spans if s.get("span") == "invoke_agent" and s.get("status") == "start"), {})
    harness = start.get("harness") or {}
    task_id = summary.get("task_id") or start.get("task_id") or "unknown"
    model = summary.get("model") or start.get("gen_ai.request.model") or "unknown"
    issue = _issue_for(task_id)

    msgs = []
    if harness.get("system_prompt"):
        msgs.append(ChatMessageSystem(content=harness["system_prompt"]))
    msgs.append(ChatMessageUser(content=issue))
    pending: list[tuple[str, str]] = []
    for s in spans:
        seq = int(s.get("seq") or 0)
        if s.get("span") == "chat":
            calls = []
            for i, c in enumerate(s.get("tool_calls") or []):
                cid = f"{run_id}:{seq}:{i}"
                fn = c.get("name") or "tool"
                calls.append(ToolCall(id=cid, function=fn, arguments=dict(c.get("arguments") or {})))
                pending.append((cid, fn))
            msgs.append(ChatMessageAssistant(content=s.get("text") or "", tool_calls=calls or None))
        elif s.get("span") == "execute_tool":
            cid, fn = pending.pop(0) if pending else (f"{run_id}:{seq}:orphan", s.get("gen_ai.tool.name") or "tool")
            msgs.append(ChatMessageTool(content=s.get("result_preview") or "", tool_call_id=cid, function=fn,
                                        error=None))

    scores = {}
    for key in ("visible_pass", "hidden_pass", "strong_pass"):
        v = summary.get(key)
        if v is not None:
            scores[key] = Score(value=1.0 if v else 0.0, answer="pass" if v else "fail",
                                explanation=f"agentlab grader: {key} on a pristine copy of the final workspace")
    counts = model_usage_counts(summary)
    # An EMPTY map, not None: inspect_ai's model_usage is a mapping with a default_factory, so None
    # is not a value it accepts -- and an absent entry is what "nobody metered this" looks like in a
    # log whose readers sum the map. (Not executed here: inspect_ai does not install on this Python,
    # so this is reasoned from its schema, not observed.)
    usage = {} if counts is None else {model: ModelUsage(input_tokens=counts[0], output_tokens=counts[1],
                                                         total_tokens=counts[0] + counts[1])}

    sample = EvalSample(
        id=run_id, epoch=int(summary.get("repeat_index") or 0) + 1,
        input=issue, target="resolved", messages=msgs, scores=scores or None, model_usage=usage,
        started_at=summary.get("started_at") or None, completed_at=summary.get("finished_at") or None,
        total_time=(summary.get("wall_ms") or 0) / 1000.0,
        metadata=_clean({"harnesslab": {"results_dir": name, "harness_id": summary.get("harness_id"),
                                       "exit_reason": summary.get("exit_reason"),
                                       "steps": summary.get("steps"), "tool_calls": summary.get("tool_calls"),
                                       "edits": summary.get("edits"), "cost_usd": summary.get("cost_usd"),
                                       "boundary_events": summary.get("boundary_events"),
                                       "boundary_kinds": summary.get("boundary_kinds"),
                                       "tests_modified": summary.get("tests_modified"),
                                       "ran_tests_before_submit": summary.get("ran_tests_before_submit"),
                                       "sentinel_interventions": summary.get("sentinel_interventions"),
                                       "sentinel_max_risk": summary.get("sentinel_max_risk")},
                        "harness": harness}),
    )
    spec = EvalSpec(
        created=summary.get("started_at") or FALLBACK_EPOCH,
        task=f"harnesslab/{task_id}", task_version=0,
        task_args={"harness_id": summary.get("harness_id") or harness.get("id") or ""},
        dataset=EvalDataset(name=f"harnesslab:{name}", location=os.path.join("data", "runs", name), samples=1,
                            sample_ids=[run_id]),
        model=model, config=EvalConfig(),
        metadata={"exported_by": "harnesslab.backend.export", "results_dir": name, "run_id": run_id},
    )
    results = EvalResults(
        total_samples=1, completed_samples=1,
        scores=[EvalScore(name=k, scorer="harnesslab.core.grader",
                          metrics={"accuracy": EvalMetric(name="accuracy", value=v.value)})
                for k, v in scores.items()])
    return EvalLog(
        eval=spec, status="success" if not summary.get("error") else "error",
        samples=[sample], results=results,
        stats=EvalStats(started_at=summary.get("started_at") or "", completed_at=summary.get("finished_at") or "",
                        model_usage=usage),
    )


@router.get("/{name}/runs/{run_id}.inspect.json")
def export_inspect(name: str, run_id: str):
    """One run as an Inspect AI ``EvalLog`` (single sample). ``read_eval_log`` round-trips it."""
    log = _inspect_log(name, run_id)
    from inspect_ai.log._file import eval_log_json
    body = eval_log_json(log)
    return Response(content=body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="{run_id}.inspect.json"'})


# --------------------------------------------------------------------------- (c) CSV
_LIST_SEP = ";"


def _flat(v):
    if isinstance(v, (list, tuple)):
        return _LIST_SEP.join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True)
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ""
    return "" if v is None else v


def index_csv(rows: list[dict]) -> str:
    """The index as CSV. Columns are the union of all row keys, first-seen order preserved.

    Lists are joined with ``;`` and booleans become 0/1 so ``read.csv`` / ``pandas.read_csv``
    give usable column types without a converter.
    """
    if not rows:
        return ""
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: _flat(r.get(k)) for k in cols})
    return buf.getvalue()


_CELL_COLS = [
    "model", "harness", "runs", "tasks", "repeats",
    "pass1", "pass1_ci95_lo", "pass1_ci95_hi", "pass1_strong",
    "pass_at_2", "pass_at_3", "pass_pow_2", "pass_pow_3",
    "flip_rate", "mean_steps", "mean_tokens", "tokens_per_solve",
    "mean_cost_usd", "cost_of_pass_usd", "mean_wall_s",
    "boundary_any", "tests_modified", "ran_tests_before_submit", "sentinel_interventions",
]


def cells_csv(cells: list[dict]) -> str:
    """Cell-level metrics with 95% CIs, one row per (model, harness).

    Values come straight from ``harnesslab.backend.metrics.cells`` -- i.e. from
    ``harnesslab.core.analysis`` -- so the CSV cannot drift from what the UI shows. ``pass1_ci95_*`` is a
    percentile bootstrap over *tasks* (2000 resamples), which is the right unit when the same tasks
    were run in every cell; with few tasks it is wide, and that is the point.
    """
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_CELL_COLS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for c in cells:
        curve = {p["k"]: p for p in (c.get("passk_curve") or [])}
        ci = c.get("ci95") or [None, None]
        w.writerow({k: _flat(v) for k, v in {
            "model": c.get("model"), "harness": c.get("harness"), "runs": c.get("runs"),
            "tasks": c.get("tasks_n", len(c.get("tasks") or {})), "repeats": c.get("repeats"),
            "pass1": c.get("pass@1"), "pass1_ci95_lo": ci[0], "pass1_ci95_hi": ci[1],
            "pass1_strong": c.get("pass1_strong"),
            "pass_at_2": (curve.get(2) or {}).get("pass_at_k"), "pass_at_3": (curve.get(3) or {}).get("pass_at_k"),
            "pass_pow_2": (curve.get(2) or {}).get("pass_pow_k"), "pass_pow_3": (curve.get(3) or {}).get("pass_pow_k"),
            "flip_rate": c.get("flip_rate"), "mean_steps": c.get("mean_steps"), "mean_tokens": c.get("mean_tokens"),
            "tokens_per_solve": c.get("tokens_per_solve"), "mean_cost_usd": c.get("mean_cost"),
            "cost_of_pass_usd": c.get("cost_of_pass"), "mean_wall_s": c.get("mean_wall_s"),
            "boundary_any": (c.get("boundary") or {}).get("any"), "tests_modified": c.get("tests_modified"),
            "ran_tests_before_submit": c.get("ran_tests_before_submit"),
            "sentinel_interventions": c.get("sentinel_interventions"),
        }.items()})
    return buf.getvalue()


@router.get("/{name}/index.csv", response_class=PlainTextResponse)
def export_index_csv(name: str, harness: Optional[str] = None, task: Optional[str] = None,
                     model: Optional[str] = None):
    """One row per run. Optional ``harness`` / ``task`` / ``model`` filters."""
    _dir(name)
    from harnesslab.core.analysis import filter_rows
    rows = filter_rows(_rows(name), harness_id=harness, task_id=task, model=model)
    return PlainTextResponse(index_csv(rows), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{name}.index.csv"'})


@router.get("/{name}/cells.csv", response_class=PlainTextResponse)
def export_cells_csv(name: str, model: Optional[str] = None):
    """One row per (model, harness) cell, with bootstrap 95% CIs on pass@1."""
    _dir(name)
    from . import metrics as M
    from harnesslab.core.analysis import filter_rows
    rows = _rows(name)
    if model:
        rows = filter_rows(rows, model=model)
    return PlainTextResponse(cells_csv(M.cells(rows)), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{name}.cells.csv"'})


# --------------------------------------------------------------------------- (d) deposit bundle
BUNDLE_README = """harnesslab export bundle
=======================

Results directory : {name}
Runs              : {runs}
Harnesses         : {harnesses}
Models            : {models}
Tasks             : {tasks}
Exported          : {when}
Produced by       : harnesslab {version} (harnesslab.backend.export)

Contents
--------
  MANIFEST.json                 machine-readable description of everything below
  index.jsonl                   one RunSummary per run (the analysis unit)
  index.csv                     the same rows as CSV, for R / pandas
  cells.csv                     per-(model, harness) metrics with bootstrap 95% CIs
  runs/<run_id>/ledger.jsonl    every consequential action of that run, OpenTelemetry GenAI naming
  runs/<run_id>/summary.json    the run's own summary
  runs/<run_id>/patch.diff      the final diff, when the run produced one
  harnesses/*.json              the HarnessConfig of every harness that appears in index.jsonl
  sentinel/<model>.json         the sentinel risk model(s) referenced by these runs

What this is not
----------------
The bundle contains measurements, not a re-runnable environment: task repositories, hidden tests and
the agent code live in the harnesslab repository, and model weights live with their providers. Cite the
repository alongside this deposit (see CITATION.cff) so the two halves stay linked.
"""


def _bundle_bytes(name: str, include_ledgers: bool = True, include_patches: bool = True,
                  limit: Optional[int] = None) -> bytes:
    d = _dir(name)
    rows = _rows(name)
    if limit:
        rows = rows[:limit]
    harnesses = sorted({r.get("harness_id", "") for r in rows} - {""})
    models = sorted({r.get("model", "") for r in rows} - {""})
    tasks = sorted({r.get("task_id", "") for r in rows} - {""})
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    version = _version()

    buf = io.BytesIO()
    manifest = {"schema": "harnesslab.bundle/1", "results_dir": name, "exported_at": when,
                "singletree_version": version, "runs": len(rows), "harnesses": harnesses,
                "models": models, "tasks": tasks, "files": [], "lab_root_at_export": LAB_ROOT}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        def put(arc: str, data, kind: str):
            payload = data if isinstance(data, bytes) else str(data).encode("utf-8")
            z.writestr(arc, payload)
            manifest["files"].append({"path": arc, "bytes": len(payload), "kind": kind})

        put(f"{name}/index.jsonl",
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "index")
        put(f"{name}/index.csv", index_csv(rows), "csv")
        try:
            from . import metrics as M
            put(f"{name}/cells.csv", cells_csv(M.cells(rows)), "csv")
        except Exception as e:                             # pragma: no cover - never fail the deposit
            put(f"{name}/cells.csv", f"# metrics unavailable: {e}\n", "csv")

        for r in rows:
            rid = r.get("run_id")
            rd = os.path.join(d, rid or "")
            if not rid or not os.path.isdir(rd):
                continue
            for fn, flag, kind in (("summary.json", True, "summary"),
                                   ("ledger.jsonl", include_ledgers, "ledger"),
                                   ("patch.diff", include_patches, "patch")):
                p = os.path.join(rd, fn)
                if flag and os.path.exists(p):
                    with open(p, "rb") as f:
                        put(f"{name}/runs/{rid}/{fn}", f.read(), kind)

        # "+sentinel" variants reuse the base harness file; dedupe so the zip has no repeats.
        for base_id in sorted({h.split("+")[0] for h in harnesses}):
            p = os.path.join(HARNESS_DIR, f"{base_id}.json")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    put(f"{name}/harnesses/{base_id}.json", f.read(), "harness")

        for mp in _sentinel_models(name):
            with open(mp, "rb") as f:
                put(f"{name}/sentinel/{os.path.basename(mp)}", f.read(), "sentinel_model")

        put(f"{name}/README.txt", BUNDLE_README.format(
            name=name, runs=len(rows), harnesses=", ".join(harnesses) or "-",
            models=", ".join(models) or "-", tasks=", ".join(tasks) or "-", when=when, version=version), "readme")
        z.writestr(f"{name}/MANIFEST.json", json.dumps(manifest, indent=2))
    return buf.getvalue()


def _sentinel_models(name: str) -> list[str]:
    """The sentinel model trained on this directory, plus the active one."""
    out = []
    if not os.path.isdir(MODELS_DIR):
        return out
    active = ""
    p = os.path.join(os.path.dirname(MODELS_DIR), "active_model.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            active = f.read().strip()
    for cand in (name, active):
        f = os.path.join(MODELS_DIR, f"{cand}.json")
        if cand and os.path.exists(f) and f not in out:
            out.append(f)
    return out


def _version() -> str:
    try:
        from harnesslab.__main__ import __version__
        return __version__
    except Exception:                                      # pragma: no cover
        return "unknown"


@router.get("/{name}/bundle.zip")
def export_bundle(name: str, ledgers: bool = True, patches: bool = True, limit: Optional[int] = None):
    """Everything needed to re-analyse this directory elsewhere, as one zip (Zenodo-ready)."""
    data = _bundle_bytes(name, ledgers, patches, limit)
    return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="harnesslab-{name}.zip"',
        "Content-Length": str(len(data))})


# --------------------------------------------------------------------------- discovery
@router.get("/formats")
def formats():
    """What this module can produce -- so the UI (and a curious user) does not have to guess."""
    try:
        import inspect_ai                                  # noqa: F401
        has_inspect = True
    except ImportError:
        has_inspect = False
    return {
        "version": _version(),
        "formats": [
            {"id": "trajectory", "path": "/api/export/{dir}/runs/{run_id}.trajectory.jsonl",
             "media_type": "application/x-ndjson", "spec": TRAJECTORY_SCHEMA,
             "label": "Letta Trajectory v1", "available": True},
            {"id": "inspect", "path": "/api/export/{dir}/runs/{run_id}.inspect.json",
             "media_type": "application/json", "spec": "https://inspect.aisi.org.uk/eval-logs.html",
             "label": "Inspect AI EvalLog", "available": has_inspect,
             "note": None if has_inspect else "pip install 'harnesslab[inspect]'"},
            {"id": "index_csv", "path": "/api/export/{dir}/index.csv", "media_type": "text/csv",
             "label": "Run index (CSV)", "available": True},
            {"id": "cells_csv", "path": "/api/export/{dir}/cells.csv", "media_type": "text/csv",
             "label": "Cell metrics with 95% CIs (CSV)", "available": True},
            {"id": "bundle", "path": "/api/export/{dir}/bundle.zip", "media_type": "application/zip",
             "label": "Deposit bundle (zip)", "available": True},
        ],
    }
