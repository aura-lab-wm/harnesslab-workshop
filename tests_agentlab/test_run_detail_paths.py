"""GET /api/results/{name}/runs/{run_id} used to accept `..` as a run id: `%2E%2E` came back 200
with an empty ledger (it read the results directory's parent), and `..%2F..` crashed with a
JSONDecodeError. Both must be refused, on the real app, before any file is opened."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient            # noqa: E402

import harnesslab.backend.metrics as M               # noqa: E402
from harnesslab.backend.app import app               # noqa: E402
from live_fixtures import chat, end, ledger, start, tool   # noqa: E402


class TestRunDetailPaths(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-detail-root-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self._orig = M.RUNS_ROOT
        M.RUNS_ROOT = self.root                       # app._dir reads M.RUNS_ROOT at call time
        self.addCleanup(setattr, M, "RUNS_ROOT", self._orig)
        p = ledger(self.root, "demo_mock", "r1", [start(0), chat(1, 0), tool(2), end(3)])
        d = os.path.dirname(p)
        with open(os.path.join(d, "summary.json"), "w") as f:
            json.dump({"run_id": "r1", "task_id": "t01_slugify", "harness_id": "baseline", "model": "mock",
                       "exit_reason": "submitted", "hidden_pass": True}, f)
        with open(os.path.join(self.root, "demo_mock", "index.jsonl"), "w") as f:
            f.write(json.dumps({"run_id": "r1", "task_id": "t01_slugify", "harness_id": "baseline", "model": "mock",
                                "hidden_pass": True}) + "\n")
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_a_real_run_is_still_served(self):
        r = self.client.get("/api/results/demo_mock/runs/r1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["spans"]), 4)
        self.assertEqual(r.json()["summary"]["exit_reason"], "submitted")

    def test_encoded_dotdot_run_id_is_refused(self):
        r = self.client.get("/api/results/demo_mock/runs/%2E%2E")
        self.assertEqual(r.status_code, 400)

    def test_encoded_traversal_run_id_is_refused_not_a_500(self):
        # `..%2F..` decodes to a literal "/" before Starlette's router matches: the request no
        # longer has the shape `/api/results/{name}/runs/{run_id}` (run_id's default converter
        # never matches an embedded "/"), so it falls through past every /api/* route to the SPA
        # catch-all (`app.py`'s `spa()`, registered last) and gets index.html at 200 -- inert, no
        # run data, no traversal read -- never reaching `run_detail` at all. That is the same shape
        # Task 7 flagged and tests_agentlab/test_repeats.py's own encoded-slash precedent works
        # around by asserting `!= 200` against a BARE router with no catch-all mounted; this app's
        # real, full `TestClient(app)` has one, so the reachable, meaningful assertion here is what
        # the regression was actually about: the old `JSONDecodeError` 500 is gone.
        self.assertNotEqual(self.client.get("/api/results/demo_mock/runs/..%2F..").status_code, 500)
        self.assertNotEqual(
            self.client.get("/api/results/demo_mock/runs/..%2F..%2Fetc%2Fpasswd").status_code, 500)

    def test_traversal_run_id_is_rejected_by_the_guard_directly(self):
        """Defense in depth, mirroring test_repeats.py's `_guard_directly` tests: even though the
        real app's routing can never deliver a decoded "../.." as a single `run_id` path parameter
        (see the test above), the guard `run_detail` calls must still refuse that value outright if
        anything ever reaches it with one -- e.g. a future route without the SPA catch-all, or a
        direct call."""
        import harnesslab.backend.app as APP
        with self.assertRaises(Exception) as ctx:
            APP._safe_name("../..", "run_id")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

    def test_dot_prefixed_and_null_byte_run_ids_are_refused(self):
        self.assertEqual(self.client.get("/api/results/demo_mock/runs/.hidden").status_code, 400)
        self.assertEqual(self.client.get("/api/results/demo_mock/runs/r1%00").status_code, 400)

    def test_dotdot_results_dir_is_refused(self):
        self.assertEqual(self.client.get("/api/results/%2E%2E/runs").status_code, 400)
        self.assertEqual(self.client.get("/api/results/%2E%2E/runs/r1").status_code, 400)

    def test_unknown_run_is_404(self):
        self.assertEqual(self.client.get("/api/results/demo_mock/runs/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
