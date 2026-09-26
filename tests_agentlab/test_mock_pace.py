"""MockProvider pacing: off by default, a real pause per model call when asked for, never a change
to what the ledger records."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.core.grader import load_task                   # noqa: E402
from harnesslab.core.harness import HarnessConfig, run_task     # noqa: E402
from harnesslab.core.mock import MockProvider                   # noqa: E402
from harnesslab.core.providers import make_provider             # noqa: E402

MESSAGES = [{"role": "system", "content": "You are an engineer."},
            {"role": "user", "content": "Task id: t01_slugify\n\nFix it."}]
TOOLS = [{"name": n} for n in ("list_files", "read_file", "write_file", "edit_file", "run_tests", "bash", "submit")]


class TestMockPace(unittest.TestCase):
    def test_default_is_no_pause(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HARNESSLAB_MOCK_PACE_MS", None)
            p = MockProvider(seed=1)
            self.assertEqual(p.pace_s, 0.0)
            t0 = time.monotonic()
            p.chat(MESSAGES, TOOLS)
            self.assertLess(time.monotonic() - t0, 0.05)

    def test_argument_pauses_each_call(self):
        p = MockProvider(seed=1, pace_ms=60)
        t0 = time.monotonic()
        p.chat(MESSAGES, TOOLS)
        self.assertGreaterEqual(time.monotonic() - t0, 0.06)

    def test_environment_variable_is_read_by_make_provider(self):
        with mock.patch.dict(os.environ, {"HARNESSLAB_MOCK_PACE_MS": "40"}):
            p = make_provider("mock", "mock", seed=1)
        self.assertAlmostEqual(p.pace_s, 0.04)

    def test_a_paced_run_takes_at_least_steps_times_pace_and_records_the_same_ledger(self):
        task = load_task(os.path.join(LAB, "tasks", "t01_slugify"))
        out_a, out_b = tempfile.mkdtemp(), tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"HARNESSLAB_MOCK_PACE_MS": "0"}):
            a = run_task(task, HarnessConfig(), make_provider("mock", "mock", seed=3), out_a, seed=3)
        with mock.patch.dict(os.environ, {"HARNESSLAB_MOCK_PACE_MS": "80"}):
            b = run_task(task, HarnessConfig(), make_provider("mock", "mock", seed=3), out_b, seed=3)
        self.assertEqual((a.steps, a.exit_reason, a.hidden_pass, a.tool_calls), (b.steps, b.exit_reason, b.hidden_pass, b.tool_calls))
        self.assertGreaterEqual(b.wall_ms, b.steps * 80)
        self.assertLess(a.wall_ms, b.wall_ms)


if __name__ == "__main__":
    unittest.main()
