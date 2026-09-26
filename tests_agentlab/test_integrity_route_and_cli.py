"""/api/results/<d>/integrity and `harnesslab integrity <d>` are one read, not two.

Both call `metrics.integrity`. A sentinel patched at that lookup site must reach BOTH surfaces --
agreement on an ordinary fixture would not show that, since two copies of the same assembly agree
until one of them is edited. The per-task `pass1_strong` the judge step's bars draw is checked
against an independent count over the raw index rows, not against the function that produced it.
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.testclient import TestClient          # noqa: E402
from harnesslab.backend.app import app, _STUDY_CACHE  # noqa: E402
from harnesslab.backend import metrics as M           # noqa: E402
from harnesslab import __main__ as cli                # noqa: E402

DIR = "families_mock"
SENTINEL = {"leakage": [{"task": "__sentinel__", "pass1": 42.0}], "weak_tests": {}, "self_report": [],
            "ochiai": [], "ochiai_harness": [], "harness": "__sentinel_harness__"}


def _reset():
    with _STUDY_CACHE._guard:
        _STUDY_CACHE._entries.clear()
        _STUDY_CACHE._locks.clear()


class IntegrityOneRead(unittest.TestCase):
    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(["integrity", DIR, *args])
        return rc, json.loads(out.getvalue())

    def test_route_and_cli_return_the_same_json(self):
        r = TestClient(app).get(f"/api/results/{DIR}/integrity?harness=baseline")
        self.assertEqual(r.status_code, 200)
        rc, body = self._cli("--harness", "baseline")
        self.assertEqual(rc, 0)
        self.assertEqual(r.json(), body)

    def test_a_sentinel_at_metrics_integrity_reaches_both_surfaces(self):
        with patch("harnesslab.backend.metrics.integrity", return_value=SENTINEL):
            r = TestClient(app).get(f"/api/results/{DIR}/integrity?harness=baseline")
            rc, body = self._cli("--harness", "baseline")
        self.assertEqual(r.json()["harness"], "__sentinel_harness__")
        self.assertEqual(body["harness"], "__sentinel_harness__")
        self.assertEqual(rc, 0)

    def test_pass1_strong_matches_an_independent_count(self):
        rows = M.rows_for(DIR)
        by_task = {}
        for r in rows:
            if r["harness_id"] == "baseline" and r.get("strong_pass") is not None:
                by_task.setdefault(r["task_id"], []).append(bool(r["strong_pass"]))
        self.assertTrue(by_task, "fixture has no strengthened verdicts to check against")
        leak = {l["task"]: l for l in M.integrity(DIR, rows, "baseline")["leakage"]}
        for task, v in by_task.items():
            self.assertAlmostEqual(leak[task]["pass1_strong"], sum(v) / len(v), msg=task)

    def test_unknown_dir_exits_1(self):
        err = io.StringIO()
        with patch("sys.stderr", err):
            rc = cli.main(["integrity", "__no_such_dir__"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
