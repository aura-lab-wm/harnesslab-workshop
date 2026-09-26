"""`harnesslab watch` -- the terminal view over harnesslab.core.live.tail: one run, the newest run
in progress in a directory, or the listing. Every form is exercised in-process with an explicit
runs root, plus one subprocess run of the real entrypoint with --lab."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harnesslab.__main__ import watch_cli                 # noqa: E402
from live_fixtures import append, chat, end, ledger, start, tool   # noqa: E402


class TestWatchCli(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.mkdtemp(prefix="hl-watch-lab-")
        self.addCleanup(shutil.rmtree, self.lab, True)
        self.root = os.path.join(self.lab, "data", "runs")
        os.makedirs(self.root)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = watch_cli(argv, runs_root=self.root)
        return rc, out.getvalue(), err.getvalue()

    def _finish_later(self, path, delay=0.1):
        def w():
            time.sleep(delay)
            append(path, [tool(2, name="run_tests", tests_passed=True), end(3)])
        threading.Thread(target=w, daemon=True).start()

    def test_follows_one_run_and_exits_zero_at_the_end(self):
        p = ledger(self.root, "d", "r1", [start(0), chat(1, 0)])
        self._finish_later(p)
        rc, out, _ = self._run(["d/r1", "--poll", "0.01"])
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertIn("run r1", lines[0])
        self.assertIn("→ read_file", lines[1])
        self.assertIn("run_tests: ok", lines[2])
        self.assertIn("end: submitted", lines[3])
        self.assertTrue(all(" │ " in l for l in lines))

    def test_an_absolute_run_directory_path_works_too(self):
        p = ledger(self.root, "d", "r1", [start(0), end(1)])
        rc, out, _ = self._run([os.path.dirname(p)])
        self.assertEqual(rc, 0)
        self.assertIn("end: submitted", out)

    def test_dir_form_follows_the_newest_run_in_progress(self):
        ledger(self.root, "d", "r-old", [start(0), end(1)])
        p = ledger(self.root, "d", "r-new", [start(0), chat(1, 0)])
        self._finish_later(p)
        rc, out, _ = self._run(["d", "--poll", "0.01"])
        self.assertEqual(rc, 0)
        self.assertIn("run r1", out)          # the fixture's run_id field; the file is r-new's
        self.assertIn("end: submitted", out)

    def test_dir_form_with_nothing_in_progress_says_so(self):
        ledger(self.root, "d", "r-old", [start(0), end(1)])
        rc, _, err = self._run(["d"])
        self.assertEqual(rc, 1)
        self.assertIn("no run in progress in d", err)

    def test_missing_run_and_bad_shapes_are_errors(self):
        self.assertEqual(self._run(["d/nope"])[0], 1)
        self.assertEqual(self._run(["a/b/c"])[0], 1)
        self.assertEqual(self._run(["../etc"])[0], 1)

    def test_captured_is_refused_by_name(self):
        ledger(self.root, "captured", "r1", [start(0), chat(1, 0)])
        rc, _, err = self._run(["captured/r1"])
        self.assertEqual(rc, 1)
        self.assertIn("captured", err)

    def test_no_argument_lists_recent_runs_with_state(self):
        ledger(self.root, "d", "r1", [start(0), end(1)])
        ledger(self.root, "d", "r2", [start(0), chat(1, 0)])
        ledger(self.root, "captured", "r3", [start(0), chat(1, 0)])
        rc, out, _ = self._run([])
        self.assertEqual(rc, 0)
        self.assertIn("finished", out)
        self.assertIn("running", out)
        self.assertIn("d/r1", out)
        self.assertNotIn("captured", out)

    def test_abandoned_run_prints_the_status_and_exits_2(self):
        ledger(self.root, "d", "r1", [start(0), chat(1, 0)])
        rc, out, _ = self._run(["d/r1", "--idle-timeout", "0.2", "--poll", "0.01"])
        self.assertEqual(rc, 2)
        self.assertIn("[abandoned]", out)

    def test_a_run_already_abandoned_replays_then_exits_2_at_once(self):
        p = ledger(self.root, "d", "r1", [start(0), chat(1, 0)])
        t = time.time() - 700
        os.utime(p, (t, t))
        t0 = time.time()
        rc, out, _ = self._run(["d/r1", "--poll", "0.01"])
        self.assertEqual(rc, 2)
        self.assertLess(time.time() - t0, 2.0)
        self.assertIn("[abandoned]", out)

    def test_module_entrypoint_runs_as_a_subprocess_with_lab(self):
        ledger(self.root, "d", "r1", [start(0), end(1)])
        p = subprocess.run([sys.executable, "-m", "harnesslab", "watch", "--lab", self.lab],
                           cwd=LAB, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("d/r1", p.stdout)
        self.assertIn("finished", p.stdout)


if __name__ == "__main__":
    unittest.main()
