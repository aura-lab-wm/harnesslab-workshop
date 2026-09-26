"""A fact the source does not record is UNKNOWN in the ledger and the summary -- null, never zero.

A Cursor run has tool requests and no tool results, no usage and no clock. Written the way every
other source is written, it came out as `tool_calls: 0`-shaped facts on the one hand and fabricated
ones on the other: an execute_tool span with status "ok" for a call whose outcome nobody saw,
estimated output tokens, `cost_usd: 0.0`. Aggregated, that run is indistinguishable from one that
chose not to call tools and cost nothing. Both writers must make the gap legible instead.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend.importers import claude_code, common as C, cursor   # noqa: E402
from harnesslab.capture import harness_config, writer                         # noqa: E402

FIXDIR = os.path.join(LAB, "tests_agentlab", "fixtures", "capture")
MISSING = frozenset({"tool_spans", "usage", "timestamps"})
TOOL_FACTS = ("tool_calls", "edits", "lines_added", "lines_removed", "files_touched", "boundary_events",
              "boundary_kinds", "tests_run_by_agent", "ran_tests_before_submit", "tests_modified",
              "patch_bytes")


def session(module, fixture):
    return next(iter(module.sessions(os.path.join(FIXDIR, fixture))))


def read(run_dir):
    with open(os.path.join(run_dir, "ledger.jsonl"), encoding="utf-8") as f:
        spans = [json.loads(line) for line in f]
    with open(os.path.join(run_dir, "summary.json"), encoding="utf-8") as f:
        return spans, json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-unmeasured-")
        self.addCleanup(shutil.rmtree, self.tmp, True)


class TestCaptureWriter(Base):
    def write(self, module, fixture, missing):
        sess = session(module, fixture)
        out = os.path.join(self.tmp, "run")
        writer.write_run(sess, out, "run-1", "task-1", harness_config.capture_harness(sess), missing=missing)
        return read(out)

    def test_no_execution_span_is_written_for_a_call_whose_execution_was_not_recorded(self):
        spans, _ = self.write(cursor, "cursor_transcript.jsonl", MISSING)
        kinds = {s["span"] for s in spans}
        self.assertFalse(kinds & {"execute_tool", "edit", "boundary_event"}, kinds)

    def test_the_requests_themselves_stay_on_the_chat_spans(self):
        spans, summary = self.write(cursor, "cursor_transcript.jsonl", MISSING)
        chats = [s for s in spans if s["span"] == "chat"]
        requested = [c["name"] for s in chats for c in s["tool_calls"]]
        self.assertEqual(requested, ["read_file", "bash", "list_files", "edit_file", "bash", "write_file"])
        self.assertEqual(summary["steps"], len(chats))
        self.assertGreater(summary["steps"], 0)

    def test_tool_facts_usage_and_cost_are_null_in_the_summary(self):
        _, summary = self.write(cursor, "cursor_transcript.jsonl", MISSING)
        for k in TOOL_FACTS + ("input_tokens", "output_tokens", "cost_usd"):
            with self.subTest(field=k):
                self.assertIsNone(summary[k])
        self.assertIsNone(summary["visible_pass"])
        self.assertEqual(summary["started_at"], "")

    def test_chat_spans_carry_no_invented_tokens_or_cost(self):
        spans, _ = self.write(cursor, "cursor_transcript.jsonl", MISSING)
        for s in spans:
            if s["span"] == "chat":
                self.assertIsNone(s["gen_ai.usage.input_tokens"])
                self.assertIsNone(s["gen_ai.usage.output_tokens"])
                self.assertIsNone(s["cost_usd"])
                self.assertEqual(s["ts"], "")

    def test_the_end_span_names_what_was_not_measured(self):
        spans, _ = self.write(cursor, "cursor_transcript.jsonl", MISSING)
        end = spans[-1]
        self.assertEqual(end["unmeasured"], C.unmeasured_fields(MISSING))
        self.assertIn("tool_calls", end["unmeasured"])
        self.assertIn("cost_usd", end["unmeasured"])
        self.assertIsNone(end["total_tokens"])
        self.assertIsNone(end["cost_usd"])

    def test_a_fully_measured_source_is_unchanged_and_says_so(self):
        spans, summary = self.write(claude_code, "cc_session.jsonl", frozenset())
        self.assertEqual(spans[-1]["unmeasured"], [])
        self.assertIsInstance(summary["tool_calls"], int)
        self.assertGreater(summary["tool_calls"], 0)
        self.assertTrue(any(s["span"] == "execute_tool" for s in spans))


class TestImporterConvert(Base):
    def test_convert_honours_the_same_declaration(self):
        sess = session(cursor, "cursor_transcript.jsonl")
        harness = C.harness_from_fingerprint("cursor", C.fingerprint(sess.events, {"source": "cursor"}))
        summary = C.convert(sess, self.tmp, "task-1", harness, missing=MISSING)
        spans, row = read(os.path.join(self.tmp, summary.run_id))
        self.assertFalse(any(s["span"] == "execute_tool" for s in spans))
        for k in TOOL_FACTS + ("input_tokens", "output_tokens", "cost_usd"):
            with self.subTest(field=k):
                self.assertIsNone(row[k])
        self.assertEqual(spans[-1]["unmeasured"], C.unmeasured_fields(MISSING))


class TestExportSaysUnknownOnlyWhenItIs(unittest.TestCase):
    def spans_and_summary(self):
        import glob
        p = sorted(glob.glob(os.path.join(LAB, "data", "runs", "demo_mock", "*", "ledger.jsonl")))
        if not p:
            self.skipTest("needs a demo_mock run")
        spans = [json.loads(l) for l in open(p[0], encoding="utf-8") if l.strip()]
        with open(os.path.join(os.path.dirname(p[0]), "summary.json"), encoding="utf-8") as f:
            return spans, json.load(f)

    def test_a_metered_run_exports_its_token_count(self):
        """The end span carries total_tokens, never input_tokens/output_tokens, so reading those two
        made every fully measured run export `tokens=unknown`."""
        from harnesslab.backend import export
        spans, summary = self.spans_and_summary()
        recs = export.trajectory_records(spans, "issue", summary)
        line = [r for r in recs if "invoke_agent end" in str(r.get("content"))]
        self.assertTrue(line)
        self.assertNotIn("tokens=unknown", line[0]["content"])
        self.assertRegex(line[0]["content"], r"tokens=\d")

    def test_a_run_with_no_usage_at_all_still_says_unknown(self):
        from harnesslab.backend import export
        spans, summary = self.spans_and_summary()
        stripped = [dict(s) for s in spans]
        for s in stripped:
            if s.get("span") == "invoke_agent" and s.get("status") == "end":
                for k in ("total_tokens", "input_tokens", "output_tokens"):
                    s.pop(k, None)
        recs = export.trajectory_records(stripped, "issue", summary)
        line = [r for r in recs if "invoke_agent end" in str(r.get("content"))]
        self.assertIn("tokens=unknown", line[0]["content"])


class TestCursorDeclinesWhatIsNotCursor(unittest.TestCase):
    def test_a_file_that_is_mostly_something_else_scores_below_the_threshold(self):
        """The fallback returned exactly the 0.5 that resolve() accepts with >=, so the declining
        branch never declined."""
        from harnesslab.backend.importers import cursor
        d = tempfile.mkdtemp(prefix="hl-sniff-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "mostly-other.jsonl")
        rows = [{"role": "user", "message": {"role": "user", "content": []}}]
        rows += [{"nothing": "this parser knows"} for _ in range(9)]
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertLess(cursor.sniff(p), 0.5, "a file that is mostly foreign must not be claimed")


if __name__ == "__main__":
    unittest.main()
