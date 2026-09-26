"""Cursor registered as a partial capture source: what discover() claims, and how a run with no clock
closes.

Layout measured on the operator's machine (structure only): ~/.cursor/projects/<slug>/ holds
agent-transcripts/, agent-tools/, canvases/, mcps/ and terminals/. Transcripts sit at
agent-transcripts/<id>/<id>.jsonl (4 files), with child work at agent-transcripts/<id>/subagents/
<child>.jsonl (3 files). Only the first shape is a run: as with qwen_code, a subagent's parent link
is a start-span field an index row cannot carry, so claiming children would inflate every aggregate.

Every file here is synthetic.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import importers as I                    # noqa: E402
from harnesslab.capture import adapters, backfill, cursors, regen, sniffer   # noqa: E402

FIXDIR = os.path.join(LAB, "tests_agentlab", "fixtures", "capture")
CURSOR_FIX = os.path.join(FIXDIR, "cursor_transcript.jsonl")
CC_FIX = os.path.join(FIXDIR, "cc_session.jsonl")
GAP = 1800


def place(src, dst, mtime=None):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    if mtime is not None:
        os.utime(dst, (mtime, mtime))
    return os.path.realpath(dst)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-cursor-reg-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "cursor", "projects")


class TestDescriptor(unittest.TestCase):
    def test_cursor_is_registered_for_exactly_what_it_records(self):
        ad = adapters.BY_NAME["cursor"]
        self.assertEqual((ad.has_call_ids, ad.has_tool_spans, ad.has_usage, ad.has_timestamps),
                         (False, False, False, False))
        self.assertEqual(adapters.missing(ad), frozenset({"tool_spans", "usage", "timestamps"}))
        self.assertEqual(ad.default_roots, ("~/.cursor/projects",))
        self.assertEqual(ad.policy, "none")

    def test_the_capture_page_lists_it_with_its_gaps(self):
        row = next(r for r in adapters.describe() if r["name"] == "cursor")
        self.assertFalse(row["has_tool_spans"] or row["has_usage"] or row["has_timestamps"])


class TestDiscover(Tmp):
    def test_it_claims_top_level_transcripts_and_nothing_else(self):
        proj = os.path.join(self.root, "proj-slug")
        top = place(CURSOR_FIX, os.path.join(proj, "agent-transcripts", "s-1", "s-1.jsonl"))
        # Cursor-shaped content in every place that is NOT a run transcript
        place(CURSOR_FIX, os.path.join(proj, "agent-transcripts", "s-1", "subagents", "c-1.jsonl"))
        place(CURSOR_FIX, os.path.join(proj, "canvases", "x.jsonl"))
        place(CURSOR_FIX, os.path.join(proj, "agent-tools", "t.jsonl"))
        place(CURSOR_FIX, os.path.join(proj, "agent-transcripts", "loose.jsonl"))
        place(CURSOR_FIX, os.path.join(proj, "terminals", "a", "b.jsonl"))
        with open(os.path.join(proj, "mcps.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"server": "<name>"}\n')
        # another source's session that happens to live where a transcript would
        cc = place(CC_FIX, os.path.join(proj, "agent-transcripts", "s-2", "s-2.jsonl"))
        for root in (self.root, os.path.dirname(self.root)):          # ~/.cursor/projects and ~/.cursor
            with self.subTest(root=root):
                found, errors = adapters.discover([root])
                self.assertEqual(errors, [])
                self.assertEqual(dict(found), {top: "cursor", cc: "claude_code"})

    def test_no_other_source_claims_a_transcript(self):
        self.assertEqual(adapters.resolve(CURSOR_FIX)[0], "cursor")
        for ad in adapters.REGISTRY:
            if ad.name != "cursor":
                with self.subTest(adapter=ad.name):
                    self.assertEqual(adapters.resolve(CURSOR_FIX, only={ad.name}), ("", 0.0))


class TestRegenAndImport(Tmp):
    def test_a_captured_cursor_run_reads_unknown_not_zero(self):
        (r,) = regen.regenerate(CURSOR_FIX, os.path.join(self.tmp, "out"), GAP, adapter="cursor")
        s = r.summary
        self.assertIsNone(s.tool_calls)
        self.assertIsNone(s.cost_usd)
        self.assertIsNone(s.input_tokens)
        self.assertGreater(s.steps, 0)
        self.assertEqual(s.provider, "capture:cursor")
        self.assertEqual(s.exit_reason, "error", "the fixture's last turn ended in error")

    def test_a_manual_import_is_just_as_honest(self):
        out = I.import_path(CURSOR_FIX, "imported", source="cursor", runs_root=self.tmp)
        self.assertEqual(out["imported"], 1, out)
        with open(os.path.join(self.tmp, "imported", "index.jsonl"), encoding="utf-8") as f:
            (row,) = [json.loads(line) for line in f]
        self.assertIsNone(row["tool_calls"])
        self.assertIsNone(row["cost_usd"])
        with open(os.path.join(self.tmp, "imported", row["run_id"], "ledger.jsonl"), encoding="utf-8") as f:
            spans = [json.loads(line) for line in f]
        self.assertFalse(any(s["span"] == "execute_tool" for s in spans))


class TestAClocklessRunCloses(Tmp):
    """No timestamp exists in the format, and backfill decided openness from last_ts alone -- so
    every Cursor run would have parked in inflight.json forever and looked like it worked. The
    source file's mtime is when the session last wrote; it decides openness, and only openness.
    It is never written into a span: the ledger stays clock-free."""

    def setUp(self):
        super().setUp()
        self.now = 1.8e9
        self.src = os.path.join(self.root, "p", "agent-transcripts", "s-1", "s-1.jsonl")
        self.runs = os.path.join(self.tmp, "runs")

    def cap(self):
        return os.path.join(self.runs, "captured")

    def index(self):
        p = os.path.join(self.cap(), "index.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def inflight(self):
        with open(os.path.join(self.cap(), "inflight.json"), encoding="utf-8") as f:
            return json.load(f)["runs"]

    def test_a_quiet_transcript_is_closed_and_indexed_with_its_gaps(self):
        place(CURSOR_FIX, self.src, mtime=self.now - 2 * GAP)
        rep = backfill.run([self.root], self.runs, GAP, now=self.now)
        self.assertEqual((rep["closed_new"], rep["open"]), (1, 0), rep["errors"])
        (row,) = self.index()
        self.assertIsNone(row["tool_calls"])
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(rep["unmeasured_runs"], 1)
        with open(os.path.join(self.cap(), row["run_id"], "ledger.jsonl"), encoding="utf-8") as f:
            self.assertTrue(all(json.loads(line)["ts"] == "" for line in f), "a clock leaked into the ledger")

    def test_a_transcript_still_being_written_is_open_and_says_what_its_clock_is(self):
        place(CURSOR_FIX, self.src, mtime=self.now - 60)
        rep = backfill.run([self.root], self.runs, GAP, now=self.now)
        self.assertEqual((rep["closed_new"], rep["open"]), (0, 1))
        (r,) = self.inflight()
        self.assertEqual(r["last_ts"], "")
        self.assertEqual(r["clock"], "source_mtime")
        self.assertTrue(r["last_activity"].startswith("2027-"))

    def test_the_watcher_rechecks_an_open_clockless_run_and_closes_it_on_time(self):
        place(CURSOR_FIX, self.src, mtime=self.now - 60)
        sniffer.tick([self.root], self.runs, GAP, now=self.now)
        (c,) = cursors.load(cursors.path_for(self.runs)).values()
        self.assertGreater(c["open_until"], self.now, "no recheck scheduled: the run would stay open forever")
        self.assertEqual(self.index(), [])
        rep = sniffer.tick([self.root], self.runs, GAP, now=self.now + 2 * GAP)   # file untouched
        self.assertEqual(rep["files"], 1, "the due recheck did not re-read the source")
        self.assertEqual(len(self.index()), 1)
        self.assertEqual(self.inflight(), [])


if __name__ == "__main__":
    unittest.main()
