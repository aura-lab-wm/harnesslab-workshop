"""`/api/assist/context` joins a caller-supplied name onto RUNS_ROOT with no guard.

field.py:60 already has the right one -- refuse anything where basename(name) != name -- and this
route did not use it, so a relative name reached any index.jsonl on the filesystem.
"""
import json
import os
import shutil
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)


class TestAssistContextStaysInsideRunsRoot(unittest.TestCase):
    def setUp(self):
        from harnesslab.backend import metrics as M
        self.outside = os.path.join(os.path.dirname(os.path.dirname(M.RUNS_ROOT)), "outside_probe")
        os.makedirs(self.outside, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.outside, True)
        with open(os.path.join(self.outside, "index.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": "r1", "task_id": "t", "harness_id": "baseline",
                                "model": "m", "hidden_pass": True}) + "\n")

    def test_a_relative_name_cannot_reach_an_index_outside_runs_root(self):
        from fastapi.testclient import TestClient
        from harnesslab.backend.app import app
        r = TestClient(app).get("/api/assist/context?results=../../outside_probe")
        self.assertNotEqual(r.status_code, 200,
                            "a traversal read an index.jsonl outside the runs root")
        self.assertEqual(r.status_code, 400)

    def test_a_bare_name_still_works(self):
        from fastapi.testclient import TestClient
        from harnesslab.backend.app import app
        r = TestClient(app).get("/api/assist/context?results=demo_mock")
        self.assertEqual(r.status_code, 200)
