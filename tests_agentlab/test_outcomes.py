"""Self-tests for harnesslab/backend/outcomes_api.py: the failure-mode classifier and its route.

The classifier is pure, so most cases are synthetic rows where the right answer is put there on
purpose. The route is exercised against the shipped llma4se_live directory, whose counts were
checked by hand against the ledgers when the endpoint was written (133 cut-off runs out of 227
hidden-suite failures).
"""
from __future__ import annotations

import os
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import outcomes_api as O                       # noqa: E402


def row(**kw):
    base = {"run_id": "r", "hidden_pass": False, "patch_bytes": 0, "exit_reason": "no_action", "error": ""}
    base.update(kw)
    return base


class TestClassify(unittest.TestCase):
    def test_pass_wins_over_everything(self):
        self.assertEqual(O.classify(row(hidden_pass=True, patch_bytes=0), [["length"]])["mode"], "passed")

    def test_missing_grade_is_ungraded_not_failure(self):
        self.assertEqual(O.classify(row(hidden_pass=None), [["length"]])["mode"], "ungraded")

    def test_error_is_reported_before_the_agent_is_blamed(self):
        self.assertEqual(O.classify(row(error="boom", patch_bytes=10), None)["mode"], "harness_error")

    def test_a_patch_that_fails_is_a_wrong_patch(self):
        self.assertEqual(O.classify(row(patch_bytes=384, exit_reason="submitted"), [["tool_calls"]])["mode"], "wrong_patch")

    def test_last_reply_cut_off_with_no_patch(self):
        c = O.classify(row(), [["tool_calls"], ["length"]])
        self.assertEqual(c["mode"], "cutoff_no_patch")
        self.assertEqual(c["cutoff_calls"], 1)
        self.assertEqual(c["last_finish"], "length")

    def test_an_earlier_cutoff_does_not_explain_a_later_exit(self):
        # cut off once, recovered, then ran out of steps: the step limit is what ended it
        c = O.classify(row(exit_reason="max_steps"), [["length"], ["tool_calls"]])
        self.assertEqual(c["mode"], "step_limit_no_patch")
        self.assertEqual(c["cutoff_calls"], 1)

    def test_without_a_ledger_cutoff_is_never_claimed(self):
        c = O.classify(row(), None)
        self.assertEqual(c["mode"], "no_patch")
        self.assertFalse(c["ledger"])


class TestRoute(unittest.TestCase):
    NAME = "llma4se_live"

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(os.path.join(O.M.RUNS_ROOT, cls.NAME, "index.jsonl")):
            raise unittest.SkipTest(f"{cls.NAME} not present")
        cls.out = O.study(cls.NAME)

    def test_every_run_is_classified_once(self):
        self.assertEqual(sum(self.out["counts"].values()), len(self.out["runs"]))
        self.assertEqual(len(self.out["runs"]), 1632)

    def test_failures_split_as_checked_by_hand(self):
        c = self.out["counts"]
        failures = sum(v for k, v in c.items() if k not in ("passed", "ungraded"))
        self.assertEqual(failures, 227)
        self.assertEqual(c["cutoff_no_patch"], 133)

    def test_the_three_failed_t03_deepseek_runs_are_cutoffs(self):
        for rid in ("20260909-034555-f0125f", "20260909-034622-5c8690", "20260909-034622-f74e28"):
            self.assertEqual(self.out["runs"][rid]["mode"], "cutoff_no_patch", rid)

    def test_bad_names_are_refused(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            O.study("../etc")


if __name__ == "__main__":
    unittest.main()
