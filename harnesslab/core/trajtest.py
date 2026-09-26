"""Load ledgers and write assertions over trajectories.

A *trajectory test* is an ordinary unittest that iterates over runs and checks
properties of what the agent did, not only of what it produced:

    from harnesslab.core.trajtest import load_runs
    class TestConduct(unittest.TestCase):
        def test_no_destructive_shell(self):
            for t in load_runs("data/runs/demo"):
                with self.subTest(run=t.run_id):
                    self.assertEqual(t.boundary_kinds.count("destructive_shell"), 0)

Run with:  python -m unittest discover -s trajectory_tests -v
Set the results directory with the HARNESSLAB_RESULTS environment variable.
"""
from __future__ import annotations
import json, os
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional


def _unmeasured(span: dict, key: str) -> bool:
    """True when the span says "nobody recorded this": the key is PRESENT and null.

    A key that is simply absent is an older ledger that predates the attribute, and keeps its
    historical default of zero. Only an explicit null is a declaration of ignorance -- that is what
    importers.common.UNMEASURED_FIELDS writes, and what console.run_features already reads."""
    return key in span and span[key] is None


@dataclass
class Trajectory:
    run_id: str
    task_id: str
    harness_id: str
    model: str
    summary: dict
    spans: list

    # -- derived views ------------------------------------------------------
    @property
    def tool_calls(self) -> list[dict]:
        return [s for s in self.spans if s["span"] == "execute_tool"]

    @property
    def tool_names(self) -> list[str]:
        return [s["gen_ai.tool.name"] for s in self.tool_calls]

    @property
    def action_string(self) -> str:
        """The run as one string over the action alphabet used by the console and by trajectory queries:
        L list_files, R read_file, W write_file, E edit_file, T run_tests, B bash, S submit, F search (imported runs)."""
        code = {"list_files": "L", "read_file": "R", "write_file": "W", "edit_file": "E", "run_tests": "T", "bash": "B", "submit": "S"}
        kind = {"view": "R", "search": "F", "edit": "E", "run": "T", "submit": "S", "other": "B"}
        out = []
        for s in self.tool_calls:
            out.append(kind[s["kind"]] if s.get("kind") in kind else code.get(s["gen_ai.tool.name"], "?"))
        return "".join(out)

    @property
    def edits(self) -> list[dict]:
        return [s for s in self.spans if s["span"] == "edit"]

    @property
    def boundary_events(self) -> list[dict]:
        return [s for s in self.spans if s["span"] == "boundary_event"]

    @property
    def boundary_kinds(self) -> list[str]:
        return [b["kind"] for b in self.boundary_events]

    @property
    def commands(self) -> list[str]:
        return [s["args"].get("command", "") for s in self.tool_calls if s["gen_ai.tool.name"] == "bash"]

    @property
    def chat_spans(self) -> list[dict]:
        return [s for s in self.spans if s["span"] == "chat"]

    @property
    def total_tokens(self) -> Optional[int]:
        """Tokens over the whole run, or None when any model call's usage was never recorded.

        A source can write the key present and null (importers.common.UNMEASURED_FIELDS), which
        `.get(k, 0)` returns as None rather than the default -- this used to raise TypeError on
        every Cursor ledger. Coercing the null to 0 instead would be worse: a partial sum presented
        as a total is the dishonesty the null exists to prevent. Unknown is the honest answer."""
        spans = self.chat_spans
        if any(_unmeasured(s, k) for s in spans
               for k in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")):
            return None
        return sum((s.get("gen_ai.usage.input_tokens") or 0) + (s.get("gen_ai.usage.output_tokens") or 0)
                   for s in spans)

    @property
    def cost_usd(self) -> Optional[float]:
        """Cost over the whole run, or None when any model call's cost was never recorded."""
        spans = self.chat_spans
        if any(_unmeasured(s, "cost_usd") for s in spans):
            return None
        return sum(s.get("cost_usd") or 0.0 for s in spans)

    @property
    def files_read(self) -> list[str]:
        return [s["args"].get("path", "") for s in self.tool_calls if s["gen_ai.tool.name"] == "read_file"]

    @property
    def files_written(self) -> list[str]:
        return [e["path"] for e in self.edits]

    @staticmethod
    def is_edit(span: dict) -> bool:
        return span["gen_ai.tool.name"] in ("write_file", "edit_file") or span.get("kind") == "edit"

    @staticmethod
    def is_test(span: dict) -> bool:
        if span["gen_ai.tool.name"] == "run_tests" or span.get("kind") == "run":
            return True
        cmd = span.get("args", {}).get("command", "") or ""
        return span["gen_ai.tool.name"] == "bash" and ("unittest" in cmd or "pytest" in cmd)

    @property
    def passed(self) -> bool:
        return bool(self.summary.get("hidden_pass"))

    def index_of_last(self, tool_name: str) -> int:
        idx = [i for i, n in enumerate(self.tool_names) if n == tool_name]
        return idx[-1] if idx else -1

    def ran_tests_after_last_edit(self) -> bool:
        calls = self.tool_calls
        last_edit = max([i for i, s in enumerate(calls) if self.is_edit(s)] or [-1])
        return any(self.is_test(s) for i, s in enumerate(calls) if i > last_edit)

    def commands_at(self, i: int) -> str:
        return self.tool_calls[i]["args"].get("command", "")

    def repeated_tool_calls(self) -> int:
        """Number of exactly repeated (name, args) tool calls: a loop smell.

        The (name, args) key is `repeats.key` -- the ONE definition, also used by the repeated-
        commands chart's backend (`core/repeats.py`) and the CLI over it. Imported locally to
        avoid a load-time cycle: `repeats.study()` calls `load_runs` from this module."""
        from .repeats import key as _repeat_key
        c = Counter(_repeat_key(s) for s in self.tool_calls)
        return sum(v - 1 for v in c.values() if v > 1)

    def read_before_write(self) -> bool:
        """Did the agent read every file it later wrote?"""
        seen = set()
        for s in self.tool_calls:
            n = s["gen_ai.tool.name"]
            if n == "read_file":
                seen.add(s["args"].get("path"))
            elif n in ("write_file", "edit_file") and s["args"].get("path") not in seen:
                return False
        return True


def load_runs(results_dir: str = None, **filters) -> list[Trajectory]:
    results_dir = results_dir or os.environ.get("HARNESSLAB_RESULTS", os.environ.get("AGENTLAB_RESULTS", "data/runs/demo"))
    out = []
    with open(os.path.join(results_dir, "index.jsonl")) as f:
        for line in f:
            if not line.strip():
                continue
            summ = json.loads(line)
            if any(summ.get(k) != v for k, v in filters.items()):
                continue
            with open(os.path.join(results_dir, summ["run_id"], "ledger.jsonl")) as g:
                spans = [json.loads(l) for l in g if l.strip()]
            out.append(Trajectory(summ["run_id"], summ["task_id"], summ["harness_id"], summ["model"], summ, spans))
    return out
