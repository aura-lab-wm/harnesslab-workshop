"""The watcher's own activity log: what the corpus held, tick by tick.

Nothing in this spine remembers what it looked like a minute ago. The menu-bar app kept a ring in
memory, which is gone on every restart and cannot answer "how much today" or "what happened while I
was not looking" -- and a panel that offers a 6h range with two minutes of data would be drawing a
shape it does not have.

So the WATCHER keeps it. It ticks anyway, it is the one writer, and a file it owns survives the app.
Counts only: no path, no task id, no message ever reaches this file, exactly as presence.json.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.capture import activity                       # noqa: E402


class TestTheLog(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-activity-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_an_unwritten_log_reads_as_no_history_not_as_zero(self):
        self.assertEqual(activity.read(self.root), [])

    def test_a_sample_round_trips_with_only_counts_in_it(self):
        activity.record(self.root, at=1.8e9, runs=20538, open_runs=11, files=19576,
                        cost_usd=30655.63)
        rows = activity.read(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["runs"], 20538)
        self.assertEqual(rows[0]["t"], 1.8e9)
        blob = json.dumps(rows)
        for forbidden in ("/", "task", "path", "Users"):
            self.assertNotIn(forbidden, blob, f"{forbidden!r} reached the activity log")

    def test_it_is_bounded_and_drops_the_oldest(self):
        for i in range(activity.MAX_ROWS + 50):
            activity.record(self.root, at=1.8e9 + i, runs=i, open_runs=0, files=0, cost_usd=0.0)
        rows = activity.read(self.root)
        self.assertLessEqual(len(rows), activity.MAX_ROWS)
        self.assertEqual(rows[-1]["runs"], activity.MAX_ROWS + 49, "the newest sample was dropped")
        self.assertGreater(rows[0]["runs"], 0, "the oldest samples should have gone, not the newest")

    def test_a_torn_line_is_skipped_rather_than_losing_the_file(self):
        activity.record(self.root, at=1.0, runs=1, open_runs=0, files=0, cost_usd=0.0)
        with open(activity.path_for(self.root), "a", encoding="utf-8") as f:
            f.write("{not json\n")
        activity.record(self.root, at=2.0, runs=2, open_runs=0, files=0, cost_usd=0.0)
        rows = activity.read(self.root)
        self.assertEqual([r["runs"] for r in rows], [1, 2])

    def test_a_tick_records_one_sample(self):
        from harnesslab.capture import sniffer
        src = os.path.join(self.root, "src")
        os.makedirs(src)
        from test_capture_backfill import copy_fixture
        copy_fixture(os.path.join(src, "s.jsonl"))
        runs = os.path.join(self.root, "runs")
        sniffer.tick([src], runs, gap_s=1800.0, now=1.8e9)
        rows = activity.read(runs)
        self.assertEqual(len(rows), 1, "a completed scan left no trace of what the corpus held")
        self.assertEqual(rows[0]["t"], 1.8e9)
        self.assertGreaterEqual(rows[0]["runs"], 1)


if __name__ == "__main__":
    unittest.main()
