"""/api/results/<d>/{patterns,query} and `harnesslab patterns <d>` are one read, not two.

Both call metrics.patterns / metrics.run_query, so a sentinel patched at that lookup site must reach
BOTH surfaces. The counts the Trajectory features view shows are checked against an independent
pass over the raw ledgers, not against the function that produced them.
"""
import io
import os
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.testclient import TestClient          # noqa: E402
from harnesslab.backend.app import app, _STUDY_CACHE  # noqa: E402
from harnesslab.backend import metrics as M           # noqa: E402
from harnesslab.backend import paths                  # noqa: E402
from harnesslab.core.trajtest import load_runs        # noqa: E402
from harnesslab import __main__ as cli                # noqa: E402

DIR, H, PATTERN = "prerecorded_mock", "baseline", r"[WE](?!.*T)"


def _reset():
    with _STUDY_CACHE._guard:
        _STUDY_CACHE._entries.clear()
        _STUDY_CACHE._locks.clear()


def _cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["patterns", *argv])
    return code, out.getvalue(), err.getvalue()


class PatternsOneRead(unittest.TestCase):
    def setUp(self):
        _reset()
        self.client = TestClient(app)

    def tearDown(self):
        _reset()

    def test_a_sentinel_at_the_lookup_reaches_both_surfaces(self):
        fake = {"n_runs": 4242, "sequences": [], "divergence": []}
        with patch.object(M, "patterns", return_value=fake):
            self.assertEqual(self.client.get(f"/api/results/{DIR}/patterns", params={"harness": H}).json()["n_runs"], 4242)
            code, out, _ = _cli(DIR, "--harness", H)
        self.assertEqual(code, 0)
        self.assertIn('"n_runs": 4242', out)

    def test_the_query_agrees_with_an_independent_count_over_the_ledgers(self):
        runs = [t for t in load_runs(os.path.join(paths.RUNS_ROOT, DIR)) if t.summary.get("harness_id") == H
                and t.summary.get("hidden_pass") is not None]
        hits = [t for t in runs if re.search(PATTERN, t.action_string)]
        api = self.client.get(f"/api/results/{DIR}/query", params={"pattern": PATTERN, "harness": H}).json()
        self.assertEqual(api["matched"], len(hits))
        self.assertEqual(api["matched"] + api["unmatched"], len(runs))
        self.assertAlmostEqual(api["pass_in"], sum(1 for t in hits if t.summary["hidden_pass"]) / len(hits))
        code, out, _ = _cli(DIR, "--harness", H, "--query", PATTERN)
        self.assertEqual(code, 0)
        self.assertIn(f'"matched": {len(hits)}', out)

    def test_unittest_export_is_a_runnable_test_and_bad_input_exits_1(self):
        code, out, _ = _cli(DIR, "--harness", H, "--query", PATTERN, "--unittest")
        self.assertEqual(code, 0)
        compile(out, "generated_test.py", "exec")
        self.assertIn(f'PATTERN = r"{PATTERN}"', out)
        self.assertEqual(_cli(DIR, "--query", "[")[0], 1)                       # bad regex
        self.assertEqual(_cli("no_such_dir_here")[0], 1)                         # no such directory


if __name__ == "__main__":
    unittest.main()
