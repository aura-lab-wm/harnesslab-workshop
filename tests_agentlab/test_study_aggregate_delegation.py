"""The delegation test (docs/superpowers/specs/2026-09-21-study-aggregates-design.md #5):
"Numerical parity is NOT sufficient -- two implementations can agree on ordinary fixtures."

Instead of comparing numbers, this monkeypatches the canonical statistic AT ITS REAL LOOKUP SITE
so it returns a distinctive, physically-impossible SENTINEL value, then asserts the sentinel
surfaces through every surface that claims to delegate to it. An independent second
implementation -- one that recomputes the statistic instead of calling the canonical function --
cannot see a patch of the canonical function's name, so its output stays the real (non-sentinel)
value and the assertion catches it immediately, no matter how numerically close its answer is.

Two real lookup sites, not one, because of ordinary Python import semantics: `paired_bootstrap`
is imported by name into BOTH harnesslab.backend.metrics (metrics.harness_comparison, the
/metrics endpoint's comparison) and used inside harnesslab.core.analysis (comparisons_for, which
/comparisons AND the `comparisons` CLI subcommand both call). `from X import name` binds a
SEPARATE reference in each importing module's own namespace, so patching one does not patch the
other -- both must be proven independently for "delegates" to mean anything.
"""
import copy
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from starlette.testclient import TestClient          # noqa: E402
from harnesslab.backend.app import app, _STUDY_CACHE  # noqa: E402
from harnesslab import __main__ as cli                # noqa: E402

# A results dir checked into the repo as a stable fixture (tests_agentlab/test_harness_tools.py
# already depends on it): two models ("mock", "mock-b") that ran the exact same five tasks under
# a "baseline" harness among others -- real structure for both a MODEL comparison (/comparisons)
# and a HARNESS comparison (/metrics) to take the "ok" branch instead of insufficient_data, so the
# sentinel has somewhere real to surface.
DIR = "families_mock"

# Values a real task-paired bootstrap over a 5-task, boolean-outcome study could never produce:
# n_tasks tops out at 5, and mean_diff/ci95 are bounded in [-1, 1]. Anything of this magnitude
# reaching a response can only have come from the patched function, never the real one.
SENTINEL_PB = {
    "n_tasks": 424242,
    "mean_diff": 123456.789,
    "ci95": (-987654.321, 555555.555),
    "per_task": {"__sentinel_task__": 123456.789},
}

SENTINEL_KAPPA = {
    "n_eligible": 909090,
    "n_excluded": 17,
    "cells": {"both_true": 1, "both_false": 2, "only_a_true": 3, "only_b_true": 4},
    "rate_a": {"rate": 0.111111, "n": 909090},
    "rate_b": {"rate": 0.222222, "n": 909090},
    "kappa": 7.918273645,          # real kappa is bounded in [-1, 1]; this value cannot be real
    "kappa_undefined_reason": None,
}


def _fake_pb(*_args, **_kwargs):
    return copy.deepcopy(SENTINEL_PB)


def _fake_kappa(*_args, **_kwargs):
    return copy.deepcopy(SENTINEL_KAPPA)


def _reset_study_cache():
    """The HTTP routes coalesce identical (name, baseline/...) requests through a process-wide
    fingerprint-keyed cache (app._StudyCache, docs/superpowers/specs/2026-09-21-study-aggregates-
    design.md #4). A prior test's REAL result already sitting in that cache under the same key
    would make this test's monkeypatch never actually run -- get_or_compute would return the old
    cached value without calling compute() again, and the assertions below would fail for the
    wrong reason (a stale cache, not a broken delegation). Cleared before AND after every test
    here so it neither reads nor leaves behind a stale (and in the positive tests, sentinel-
    poisoned) entry for any other test in the suite."""
    _STUDY_CACHE._entries.clear()
    _STUDY_CACHE._locks.clear()


class TestPairedBootstrapDelegation(unittest.TestCase):
    def setUp(self):
        _reset_study_cache()

    def tearDown(self):
        _reset_study_cache()

    def test_metrics_endpoint_surfaces_the_sentinel(self):
        """/api/results/<d>/metrics?model=M: metrics.harness_comparison calls paired_bootstrap
        directly, once per non-baseline harness -- patching metrics.py's own imported name must
        make every harness's comparison come back as the sentinel."""
        client = TestClient(app)
        with patch("harnesslab.backend.metrics.paired_bootstrap", side_effect=_fake_pb):
            r = client.get(f"/api/results/{DIR}/metrics", params={"model": "mock", "baseline": "baseline"})
        self.assertEqual(r.status_code, 200)
        comparisons = r.json()["comparison"]
        self.assertTrue(comparisons, "fixture has no non-baseline harness to compare")
        for harness, pb in comparisons.items():
            self.assertEqual(pb["n_tasks"], SENTINEL_PB["n_tasks"], harness)
            self.assertAlmostEqual(pb["mean_diff"], SENTINEL_PB["mean_diff"], msg=harness)
            self.assertEqual(pb["ci95"], list(SENTINEL_PB["ci95"]), harness)
            self.assertEqual(pb["per_task"], SENTINEL_PB["per_task"], harness)

    def _assert_grid_carries_sentinel(self, body):
        """Every (model, harness) cell of a {model: {harness: comparison}} grid is the sentinel."""
        self.assertEqual(body["baseline"], "baseline")
        grid = body["comparisons"]
        self.assertTrue(grid, "fixture produced no models")
        seen = 0
        for model, row in grid.items():
            for harness, pb in row.items():
                seen += 1
                self.assertEqual(pb["n_tasks"], SENTINEL_PB["n_tasks"], (model, harness))
                self.assertAlmostEqual(pb["mean_diff"], SENTINEL_PB["mean_diff"])
                self.assertEqual(pb["ci95"], list(SENTINEL_PB["ci95"]))
                self.assertEqual(pb["per_task"], SENTINEL_PB["per_task"])
        self.assertGreater(seen, 0, "no non-baseline harness cell to compare")

    def test_comparisons_endpoint_surfaces_the_sentinel(self):
        """/api/results/<d>/comparisons returns {model: {harness: comparison}} by calling
        `harness_comparison` -- the SAME function /metrics calls -- once per model. So it resolves
        `paired_bootstrap` through metrics.py's own binding, exactly as /metrics does, and the
        spy sits there. (An earlier version went through core.analysis.comparisons_for and compared
        MODELS against a baseline MODEL, pooling harnesses; that dropped the harness axis.)"""
        client = TestClient(app)
        with patch("harnesslab.backend.metrics.paired_bootstrap", side_effect=_fake_pb):
            r = client.get(f"/api/results/{DIR}/comparisons", params={"baseline": "baseline"})
        self.assertEqual(r.status_code, 200)
        self._assert_grid_carries_sentinel(r.json())

    def test_cli_comparisons_subcommand_surfaces_the_sentinel(self):
        """`harnesslab comparisons <dir> --baseline B`: the CLI calls M.comparisons ->
        harness_comparison, the identical path the endpoint uses -- no HTTP layer, no cache, and
        no second implementation the CLI could have grown on its own."""
        out = io.StringIO()
        with patch("harnesslab.backend.metrics.paired_bootstrap", side_effect=_fake_pb):
            with redirect_stdout(out):
                rc = cli.main(["comparisons", DIR, "--baseline", "baseline"])
        self.assertEqual(rc, 0)
        self._assert_grid_carries_sentinel(json.loads(out.getvalue()))

    def test_comparisons_no_longer_routes_through_the_model_vs_model_path(self):
        """The mirror proof for /comparisons: patching ONLY core.analysis.paired_bootstrap -- the
        binding the old comparisons_for path used -- must NOT reach /comparisons any more. If
        someone re-pointed /comparisons at comparisons_for (and so back to comparing models
        against each other), this is the test that notices."""
        client = TestClient(app)
        with patch("harnesslab.core.analysis.paired_bootstrap", side_effect=_fake_pb):
            r = client.get(f"/api/results/{DIR}/comparisons", params={"baseline": "baseline"})
        self.assertEqual(r.status_code, 200)
        for model, row in r.json()["comparisons"].items():
            for harness, pb in row.items():
                self.assertNotEqual(pb.get("n_tasks"), SENTINEL_PB["n_tasks"], (model, harness))

    def test_an_independent_second_implementation_would_be_caught(self):
        """The mirror-image proof that the positive tests above are actually discriminating,
        not vacuous: reproduce, deliberately, the exact failure they exist to catch. If /metrics's
        own comparison did NOT delegate to `paired_bootstrap` (say it inlined its own interval
        instead), then patching ONLY core.analysis.paired_bootstrap -- never
        metrics.paired_bootstrap -- would never reach it, and /metrics would keep answering with
        real, non-sentinel numbers while /comparisons (patched the same way) answers with the
        sentinel. That is exactly what this test observes: it patches only the analysis-module
        name and confirms /metrics does NOT pick up the sentinel through it, proving
        test_metrics_endpoint_surfaces_the_sentinel is patching a binding /metrics actually needs,
        not one it happens to ignore."""
        client = TestClient(app)
        with patch("harnesslab.core.analysis.paired_bootstrap", side_effect=_fake_pb):
            r = client.get(f"/api/results/{DIR}/metrics", params={"model": "mock", "baseline": "baseline"})
        comparisons = r.json()["comparison"]
        self.assertTrue(comparisons)
        for harness, pb in comparisons.items():
            self.assertNotEqual(pb.get("n_tasks"), SENTINEL_PB["n_tasks"], harness)


class TestKappaDelegation(unittest.TestCase):
    def setUp(self):
        _reset_study_cache()

    def tearDown(self):
        _reset_study_cache()

    def test_oracle_endpoint_surfaces_the_sentinel(self):
        """/api/results/<d>/oracle: M.oracle calls cohens_kappa directly -- metrics.py's own
        imported name is the real lookup site both the HTTP route and the CLI go through."""
        client = TestClient(app)
        with patch("harnesslab.backend.metrics.cohens_kappa", side_effect=_fake_kappa):
            r = client.get(f"/api/results/{DIR}/oracle")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["n_eligible"], SENTINEL_KAPPA["n_eligible"])
        self.assertEqual(body["n_excluded"], SENTINEL_KAPPA["n_excluded"])
        self.assertEqual(body["cells"], SENTINEL_KAPPA["cells"])
        self.assertAlmostEqual(body["kappa"], SENTINEL_KAPPA["kappa"])
        self.assertIsNone(body["kappa_undefined_reason"])

    def test_cli_oracle_subcommand_surfaces_the_sentinel(self):
        out = io.StringIO()
        with patch("harnesslab.backend.metrics.cohens_kappa", side_effect=_fake_kappa):
            with redirect_stdout(out):
                rc = cli.main(["oracle", DIR])
        self.assertEqual(rc, 0)
        body = json.loads(out.getvalue())
        self.assertEqual(body["n_eligible"], SENTINEL_KAPPA["n_eligible"])
        self.assertAlmostEqual(body["kappa"], SENTINEL_KAPPA["kappa"])


if __name__ == "__main__":
    unittest.main()
