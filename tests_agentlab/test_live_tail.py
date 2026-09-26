"""harnesslab.core.live -- tailing a ledger while it is written, classifying runs on disk, and
the two-lane terminal rendering `watch` prints. Everything runs against temp files this file
writes itself; no dataset is read."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harnesslab.core import live                                    # noqa: E402
from live_fixtures import (append, append_raw, boundary, chat, edit, end, grade, ledger,  # noqa: E402
                           span, start, tool, write)

FAST = dict(poll=0.01)


class TestTail(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-live-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = os.path.join(self.root, "ledger.jsonl")

    def test_replays_complete_lines_then_follows_appends_in_seq_order(self):
        write(self.path, [start(0), chat(1, 0)])

        def writer():
            time.sleep(0.05)
            append(self.path, [tool(2), chat(3, 1)])
            time.sleep(0.05)
            append(self.path, [end(4)])

        threading.Thread(target=writer, daemon=True).start()
        got = list(live.tail(self.path, idle_timeout=5, **FAST))
        self.assertEqual([r["seq"] for r in got], [0, 1, 2, 3, 4])
        self.assertTrue(live.is_end(got[-1]))

    def test_holds_a_partial_line_until_its_newline_arrives(self):
        write(self.path, [start(0), chat(1, 0)], trailing_newline=False)   # line 2 has no "\n" yet
        got = []

        def reader():
            for rec in live.tail(self.path, idle_timeout=5, **FAST):
                got.append(rec)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.08)
        self.assertEqual([r["seq"] for r in got], [0], "the unterminated line must not be yielded")
        append_raw(self.path, "\n")                       # the writer finishes the line
        append(self.path, [end(2)])
        t.join(timeout=3)
        self.assertEqual([r["seq"] for r in got], [0, 1, 2])

    def test_stops_after_the_end_span_without_reading_further(self):
        write(self.path, [start(0), end(1), span(2, "chat")])
        got = list(live.tail(self.path, idle_timeout=5, **FAST))
        self.assertEqual([r["seq"] for r in got], [0, 1])

    def test_from_seq_skips_earlier_spans(self):
        write(self.path, [start(0), chat(1, 0), tool(2), end(3)])
        got = list(live.tail(self.path, from_seq=2, idle_timeout=5, **FAST))
        self.assertEqual([r["seq"] for r in got], [2, 3])

    def test_from_seq_past_the_end_yields_nothing_and_returns(self):
        write(self.path, [start(0), end(1)])
        self.assertEqual(list(live.tail(self.path, from_seq=5, idle_timeout=5, **FAST)), [])

    def test_idle_timeout_yields_abandoned_then_stops(self):
        write(self.path, [start(0), chat(1, 0)])
        t0 = time.monotonic()
        got = list(live.tail(self.path, idle_timeout=0.15, **FAST))
        self.assertEqual([r.get("seq") for r in got[:2]], [0, 1])
        self.assertEqual(got[-1], {"span": "live_status", "status": "abandoned"})
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_stop_callable_ends_the_follow_quietly(self):
        write(self.path, [start(0)])
        flag = threading.Event()
        got = []

        def reader():
            for rec in live.tail(self.path, idle_timeout=30, stop=flag.is_set, **FAST):
                got.append(rec)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.05)
        flag.set()
        t.join(timeout=2)
        self.assertFalse(t.is_alive(), "stop() must end the generator within a poll")
        self.assertEqual([r["seq"] for r in got], [0])

    def test_unreadable_line_is_reported_once_with_its_line_number_and_skipped(self):
        write(self.path, [start(0)])
        append_raw(self.path, "{not json\n")
        append(self.path, [chat(1, 0), end(2)])
        got = list(live.tail(self.path, idle_timeout=5, **FAST))
        self.assertEqual(got[1], {"span": "live_status", "status": "unreadable_line", "line": 2})
        self.assertEqual([r.get("seq") for r in got if "seq" in r], [0, 1, 2])

    def test_a_non_object_json_line_is_unreadable_too(self):
        write(self.path, [start(0)])
        append_raw(self.path, "[1, 2]\n")
        append(self.path, [end(1)])
        got = list(live.tail(self.path, idle_timeout=5, **FAST))
        self.assertEqual(got[1]["status"], "unreadable_line")

    def test_from_seq_only_reports_an_unreadable_line_after_the_resume_point(self):
        # line 1 start(seq0), line 2 bad (before seq2 resumes), line 3 chat(seq1, also before),
        # line 4 tool(seq2 -- the resume point), line 5 bad (after), line 6 end(seq3).
        write(self.path, [start(0)])
        append_raw(self.path, "{bad early\n")
        append(self.path, [chat(1, 0), tool(2)])
        append_raw(self.path, "{bad late\n")
        append(self.path, [end(3)])
        got = list(live.tail(self.path, from_seq=2, idle_timeout=5, **FAST))
        self.assertEqual([r.get("seq") for r in got if "seq" in r], [2, 3])
        bad = [r for r in got if r.get("status") == "unreadable_line"]
        self.assertEqual(len(bad), 1, "the bad line before the resume point must not be re-reported")
        self.assertEqual(bad[0]["line"], 5)


class TestDescribeAndState(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-live-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = os.path.join(self.root, "ledger.jsonl")

    def test_finished_when_the_last_line_is_the_end_span(self):
        write(self.path, [start(0), chat(1, 0), end(2)])
        d = live.describe(self.path)
        self.assertEqual(d["state"], "finished")
        self.assertEqual((d["task_id"], d["harness_id"], d["model"], d["repeat_index"]),
                         ("t01_slugify", "baseline", "mock", 0))
        self.assertEqual(d["started_ts"], "2026-09-19T00:00:00.000Z")
        self.assertEqual(d["last_ts"], "2026-09-19T00:00:02.000Z")
        self.assertEqual(d["last_seq"], 2)

    def test_running_when_no_end_and_written_within_the_grace(self):
        write(self.path, [start(0), chat(1, 0)])
        self.assertEqual(live.state_of(self.path), "running")

    def test_abandoned_when_no_end_and_silent_past_the_grace(self):
        write(self.path, [start(0), chat(1, 0)])
        old = time.time() - live.RUNNING_GRACE_S - 5
        os.utime(self.path, (old, old))
        self.assertEqual(live.state_of(self.path), "abandoned")
        self.assertEqual(live.state_of(self.path, grace=10 ** 6), "running")   # the grace is a parameter

    def test_a_partial_last_line_is_ignored_for_last_seq_and_state(self):
        write(self.path, [start(0), chat(1, 0), end(2)], trailing_newline=False)   # end span not yet terminated
        d = live.describe(self.path)
        self.assertEqual(d["last_seq"], 1)
        self.assertEqual(d["state"], "running")

    def test_an_empty_fresh_ledger_is_running_with_no_identity(self):
        write(self.path, [])
        d = live.describe(self.path)
        self.assertEqual(d["state"], "running")
        self.assertIsNone(d["task_id"])
        self.assertIsNone(d["last_seq"])


class TestScan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-live-root-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _age(self, path, seconds):
        t = time.time() - seconds
        os.utime(path, (t, t))

    def test_lists_recent_runs_newest_first_with_dir_run_id_and_state(self):
        old = ledger(self.root, "a", "r-old", [start(0), end(1)])
        self._age(old, 30)
        ledger(self.root, "a", "r-run", [start(0), chat(1, 0)])
        stale = ledger(self.root, "b", "r-gone", [start(0), chat(1, 0)])
        self._age(stale, live.RUNNING_GRACE_S + 5)
        rows = live.scan(self.root)
        self.assertEqual([(r["dir"], r["run_id"], r["state"]) for r in rows],
                         [("a", "r-run", "running"), ("a", "r-old", "finished"), ("b", "r-gone", "abandoned")])
        for k in ("task_id", "harness_id", "model", "repeat_index", "started_ts", "last_ts", "last_seq"):
            self.assertIn(k, rows[0])
        self.assertNotIn("mtime", rows[0])

    def test_runs_older_than_recent_are_left_out(self):
        p = ledger(self.root, "a", "r1", [start(0), end(1)])
        self._age(p, 100)
        self.assertEqual(live.scan(self.root, recent=50), [])
        self.assertEqual(len(live.scan(self.root, recent=500)), 1)

    def test_captured_is_never_entered_and_neither_is_a_symlink_alias_of_it(self):
        ledger(self.root, "captured", "r-secret", [start(0), chat(1, 0)])
        ledger(self.root, "demo", "r1", [start(0), chat(1, 0)])
        os.symlink(os.path.join(self.root, "captured"), os.path.join(self.root, "alias"))
        rows = live.scan(self.root, exclude=("captured",))
        self.assertEqual([(r["dir"], r["run_id"]) for r in rows], [("demo", "r1")])

    def test_exclude_is_case_insensitive_like_results_scope(self):
        ledger(self.root, "Captured", "r-secret", [start(0), chat(1, 0)])
        self.assertEqual(live.scan(self.root, exclude=("captured",)), [])

    def test_dot_directories_and_dirs_without_a_ledger_are_skipped(self):
        ledger(self.root, ".hidden", "r1", [start(0), chat(1, 0)])
        os.makedirs(os.path.join(self.root, "demo", "no-ledger"))
        self.assertEqual(live.scan(self.root), [])

    def test_missing_root_is_empty(self):
        self.assertEqual(live.scan(os.path.join(self.root, "nope")), [])


class TestRender(unittest.TestCase):
    def test_chat_goes_to_the_agent_lane_with_its_text_and_call(self):
        a, h = live.lanes(chat(1, 0, text="Reading the file.", calls=(("read_file", {"path": "a.py"}),)))
        self.assertIn("Reading the file.", a)
        self.assertIn('read_file(path="a.py")', a)
        self.assertEqual(h, "")

    def test_tool_result_goes_to_the_harness_lane(self):
        a, h = live.lanes(tool(2, name="run_tests", status="ok", preview="exit=0 ok", tests_passed=True))
        self.assertEqual(a, "")
        self.assertIn("run_tests: ok", h)
        self.assertIn("tests pass", h)
        self.assertIn("3 ms", h)
        self.assertIn("exit=0 ok", h)

    def test_edit_boundary_grade_sentinel(self):
        self.assertEqual(live.lanes(edit(3))[1], "edit textkit/slug.py +2 −1")
        self.assertEqual(live.lanes(boundary(4))[1], "blocked: path_escape (write_file)")
        self.assertEqual(live.lanes(boundary(4, status="allowed"))[1], "flagged: path_escape (write_file)")
        self.assertEqual(live.lanes(grade(5, strong=False))[1], "grade visible=pass hidden=pass strong=fail")
        self.assertEqual(live.lanes(span(6, "sentinel", action="nudge", risk=0.4))[1], "sentinel: nudge risk=0.4")

    def test_start_is_a_header_and_end_is_a_footer(self):
        a, _ = live.lanes(start(0))
        self.assertIn("run r1", a)
        self.assertIn("t01_slugify", a)
        self.assertIn("baseline", a)
        _, h = live.lanes(end(9))
        self.assertIn("end: submitted", h)
        self.assertIn("hidden pass", h)
        self.assertIn("$0.0123", h)

    def test_live_status_and_unknown_kinds_do_not_raise(self):
        self.assertEqual(live.lanes({"span": "live_status", "status": "abandoned"})[1], "[abandoned]")
        self.assertEqual(live.lanes({"span": "live_status", "status": "unreadable_line", "line": 7})[1], "[unreadable_line] line 7")
        self.assertEqual(live.lanes({"span": "weird"})[1], "weird")

    def test_render_line_has_seq_then_the_two_lanes_separated_by_a_bar(self):
        line = live.render_line(tool(2), width=20)
        self.assertTrue(line.startswith("   2  "))
        self.assertIn(" │ ", line)
        agent, harness = line[6:].split(" │ ", 1)
        self.assertEqual(agent, " " * 20)
        self.assertTrue(harness.startswith("read_file: ok"))

    def test_render_line_truncates_each_lane_with_an_ellipsis(self):
        line = live.render_line(chat(1, 0, text="x" * 200), width=12)
        agent = line[6:].split(" │ ", 1)[0]
        self.assertEqual(len(agent), 12)
        self.assertTrue(agent.endswith("…"))


if __name__ == "__main__":
    unittest.main()
