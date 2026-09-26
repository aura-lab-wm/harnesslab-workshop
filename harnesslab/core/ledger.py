"""The measurement ledger.

Every consequential thing that happens during a run is appended as one JSON
record (a *span*) to `ledger.jsonl`. Field names deliberately mirror the
OpenTelemetry GenAI semantic conventions (gen_ai.*) so that students can map
what they see here onto production telemetry. There is no cost attribute in
the OTel conventions; we derive `cost_usd` from tokens x price, as observability
vendors do.

Span kinds (the `span` field):
  invoke_agent    one per run, opened first and closed last
  chat            one model call (gen_ai.operation.name = chat)
  execute_tool    one tool execution (gen_ai.operation.name = execute_tool)
  edit            a file changed on disk (derived from write_file / edit_file)
  boundary_event  the policy flagged an action (blocked or allowed, see status)
  grade           hidden tests executed against the final workspace
  sentinel        an early-warning module scored the partial trajectory (risk, patterns, action)
"""
from __future__ import annotations
import json, os, time, uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((time.time() % 1) * 1000):03d}Z"


class Ledger:
    """Append-only JSONL writer with a monotonically increasing `seq`."""

    def __init__(self, path: str, run_id: str, task_id: str, harness_id: str, model: str, listener=None):
        self.path = path
        self.listener = listener          # optional callable(rec) invoked after every append (live streaming)
        self.run_id, self.task_id, self.harness_id, self.model = run_id, task_id, harness_id, model
        self.seq = 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")

    def record(self, span: str, **fields: Any) -> dict:
        rec = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "harness_id": self.harness_id,
            "gen_ai.request.model": self.model,
            "seq": self.seq,
            "ts": now_iso(),
            "span": span,
        }
        rec.update(fields)
        self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._f.flush()
        self.seq += 1
        if self.listener is not None:
            try:
                self.listener(rec)
            except Exception:
                pass  # a broken listener must never affect the run
        return rec

    def close(self):
        self._f.close()


@dataclass
class RunSummary:
    """One row per run. This is what analysis.py loads."""
    run_id: str
    task_id: str
    harness_id: str
    model: str
    provider: str
    repeat_index: int
    started_at: str
    finished_at: str = ""
    exit_reason: str = ""            # submitted | max_steps | budget_exceeded | error | no_action | sentinel_abort
    sentinel_interventions: int = 0  # nudges / blocks / aborts issued by an early-warning hook
    sentinel_max_risk: float = 0.0   # highest risk score the hook reported during the run
    sentinel_cost_usd: float = 0.0   # spend of the LLM layer of the hook, if any (not included in cost_usd)
    steps: int = 0                   # number of model calls
    # Every field blank_unmeasured() nulls is Optional here. They were not, and the dataclass is the
    # contract every consumer of a captured run is read against: declaring a field non-optional while
    # it routinely holds None understates what a reader has to handle.
    tool_calls: Optional[int] = 0   # None: the source records no tool execution (see importers.common.UNMEASURED_FIELDS)
    edits: Optional[int] = 0
    lines_added: Optional[int] = 0
    lines_removed: Optional[int] = 0
    files_touched: Optional[list] = field(default_factory=list)
    boundary_events: Optional[int] = 0
    boundary_kinds: Optional[list] = field(default_factory=list)
    tests_run_by_agent: Optional[int] = 0
    ran_tests_before_submit: Optional[bool] = False
    input_tokens: Optional[int] = 0  # None: the source records no usage
    output_tokens: Optional[int] = 0
    cost_usd: Optional[float] = 0.0  # None: unpriceable because usage is unknown, not free
    wall_ms: int = 0
    visible_pass: Optional[bool] = None
    hidden_pass: Optional[bool] = None
    strong_pass: Optional[bool] = None
    tests_modified: Optional[bool] = False
    patch_bytes: Optional[int] = 0
    error: str = ""
    harness_hash: str = ""           # harnesslab.core.harness.content_hash of the exact config this run used (older index rows lack it)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
