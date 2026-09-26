"""Aggregates over runs whose facts are UNKNOWN (null) average what was measured -- not zeros.

A captured Cursor run writes `tool_calls`, tokens and `cost_usd` as null (see
test_capture_unmeasured). Every consumer of index rows used to meet that as either a crash
(`sum(r["cost_usd"])`) or a silent zero (`r.get("tool_calls") or 0`), and a zero drags a mean
toward "calls no tools, costs nothing". Unknown rows are left out of the mean; a cell with no
measured row reads null, which the UI renders as a dash.
"""
import json
import math
import os
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import harness_tools, metrics           # noqa: E402
from harnesslab.core import analysis, console, reportcard       # noqa: E402
from harnesslab.core.patterns import sequences                 # noqa: E402
from harnesslab.core.trajtest import Trajectory                # noqa: E402

TOOL_FACTS = ("tool_calls", "edits", "lines_added", "lines_removed", "files_touched", "boundary_events",
              "boundary_kinds", "tests_run_by_agent", "ran_tests_before_submit", "tests_modified",
              "patch_bytes")


def row(i, **kw):
    r = {"run_id": f"r{i}", "task_id": f"t{i}", "harness_id": "h", "model": "m", "provider": "capture:x",
         "repeat_index": 0, "started_at": "", "finished_at": "", "exit_reason": "no_action",
         "sentinel_interventions": 0, "sentinel_max_risk": 0.0, "sentinel_cost_usd": 0.0, "steps": 4,
         "tool_calls": 10, "edits": 2, "lines_added": 3, "lines_removed": 1, "files_touched": ["a"],
         "boundary_events": 1, "boundary_kinds": ["sudo"], "tests_run_by_agent": 1,
         "ran_tests_before_submit": True, "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.5,
         "wall_ms": 0, "visible_pass": None, "hidden_pass": True, "strong_pass": None,
         "tests_modified": False, "patch_bytes": 10, "error": "", "harness_hash": ""}
    r.update(kw)
    return r


def unmeasured(i):
    return row(i, hidden_pass=None, input_tokens=None, output_tokens=None, cost_usd=None,
               **{k: None for k in TOOL_FACTS})


MEASURED = [row(1), row(2, tool_calls=20, cost_usd=1.5, input_tokens=300, output_tokens=150,
                        boundary_events=0, boundary_kinds=[])]
MIXED = MEASURED + [unmeasured(3)]
NONE_KNOWN = [unmeasured(1), unmeasured(2)]


def nan_or_none(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


class TestAnalysis(unittest.TestCase):
    def test_means_leave_unknown_rows_out(self):
        s = analysis.summarize(MIXED)
        self.assertAlmostEqual(s["mean_cost"], 1.0)
        self.assertAlmostEqual(s["mean_tokens"], 300.0)
        self.assertEqual(s["boundary_any"], 0.5, "the unknown run is neither with nor without a boundary event")
        self.assertEqual(s["ran_tests_before_submit"], 1.0)
        self.assertEqual(s["runs"], 3, "the run itself still counts as a run")

    def test_a_cell_with_nothing_measured_is_unknown_not_zero(self):
        s = analysis.summarize(NONE_KNOWN)
        for k in ("mean_cost", "mean_tokens", "boundary_any", "tests_modified", "ran_tests_before_submit"):
            with self.subTest(metric=k):
                self.assertTrue(nan_or_none(s[k]), f"{k}={s[k]!r}")
        self.assertIn("$/run=n/a", analysis.fmt_summary(s))

    def test_cost_and_tokens_per_solve_use_only_rows_whose_spend_is_known(self):
        self.assertAlmostEqual(analysis.cost_of_pass(MIXED), 1.0)
        self.assertAlmostEqual(analysis.tokens_per_solve(MIXED), 300.0)
        self.assertTrue(nan_or_none(analysis.cost_of_pass(NONE_KNOWN)))
        self.assertTrue(nan_or_none(analysis.tokens_per_solve(NONE_KNOWN)))

    def test_boundary_kinds_are_rated_over_rows_that_know_them(self):
        self.assertEqual(analysis.boundary_rate(MIXED)["by_kind"], {"sudo": 0.5})


class TestPlatformMetrics(unittest.TestCase):
    def test_cells_average_tool_calls_and_tokens_over_measured_runs(self):
        (c,) = metrics.cells(MIXED)
        self.assertAlmostEqual(c["tool_calls_per_run"], 15.0)
        self.assertAlmostEqual(c["in_out_ratio"], 2.0)
        (c,) = metrics.cells(NONE_KNOWN)
        self.assertIsNone(c["tool_calls_per_run"])
        self.assertIsNone(c["mean_cost"])
        self.assertIsNone(c["in_out_ratio"])

    def test_report_card_conduct_and_cost_are_unknown_when_unmeasured(self):
        card = reportcard.build_card(NONE_KNOWN, [])
        self.assertTrue(nan_or_none(card["conduct"]["tool_calls"]))
        self.assertTrue(nan_or_none(card["conduct"]["edits"]))
        self.assertTrue(nan_or_none(card["cost"]["cost_per_run"]))
        self.assertTrue(nan_or_none(card["conduct"]["tests_modified"]))
        card = reportcard.build_card(MIXED, [])
        self.assertAlmostEqual(card["conduct"]["tool_calls"], 15.0)

    def test_harness_diff_extractors_say_unknown_instead_of_zero(self):
        by_key = {k: fn for k, _, fn, _, _ in harness_tools.METRICS if fn}
        u = unmeasured(1)
        for k in ("verified", "boundary_rate", "tests_modified", "cost_per_run", "tokens"):
            with self.subTest(metric=k):
                self.assertIsNone(by_key[k](u))
                self.assertIsNotNone(by_key[k](row(1)))

    def test_sequences_average_cost_over_known_rows(self):
        t = Trajectory("r1", "t1", "h", "m", {}, [])
        (g,) = sequences([(t, MEASURED[0]), (t, unmeasured(3))])
        self.assertAlmostEqual(g["mean_cost"], 0.5)
        (g,) = sequences([(t, unmeasured(3))])
        self.assertIsNone(g["mean_cost"])

    def test_run_features_do_not_crash_on_an_unmeasured_summary(self):
        t = Trajectory("r1", "t1", "h", "m", unmeasured(1), [])
        f = console.run_features(t)
        for k in ("files_edited", "lines_edited", "boundary_events", "tokens", "cost"):
            with self.subTest(feature=k):
                self.assertIsNone(f[k])
        self.assertIsNone(f["tests_modified"])
        # execution features would otherwise read "no edit at all, never tested" off an empty ledger
        for k in ("steps", "n_edit", "n_test", "first_edit_step", "ran_after_last_edit", "read_before_write"):
            with self.subTest(feature=k):
                self.assertIsNone(f[k])
        self.assertEqual(console.run_features(Trajectory("r1", "t1", "h", "m", row(1), []))["n_edit"], 0)

    def test_fork_outcome_reports_unknown_tokens_as_unknown(self):
        from harnesslab.backend import fork
        self.assertIsNone(fork.outcome_of("", "r1", [], unmeasured(1))["tokens"])
        self.assertEqual(fork.outcome_of("", "r1", [], row(1))["tokens"], 150)

    def test_judge_runner_skips_a_run_with_no_known_patch_instead_of_crashing(self):
        import json as _json
        import shutil as _shutil
        import tempfile as _tempfile
        from harnesslab.backend import judge_runner
        d = _tempfile.mkdtemp(prefix="hl-judge-")
        self.addCleanup(_shutil.rmtree, d, True)
        with open(os.path.join(d, "index.jsonl"), "w", encoding="utf-8") as f:
            f.write(_json.dumps(dict(unmeasured(1), hidden_pass=True)) + "\n")
        with self.assertRaisesRegex(ValueError, "no graded runs with a non-empty patch"):
            judge_runner.run(d, n=1, repeats=1)

    def test_attribution_features_leave_out_runs_that_could_not_see_their_tools(self):
        """Every attribution feature is a fact about tool EXECUTION ("no_test_run", "no_edit_at_all").
        A run whose execution was never recorded would light up as all of them."""
        import json as _json
        import shutil as _shutil
        import tempfile as _tempfile
        from harnesslab.core.real_traj import ledger_rows_as_features
        d = _tempfile.mkdtemp(prefix="hl-feat-")
        self.addCleanup(_shutil.rmtree, d, True)
        for r in (row(1, hidden_pass=True), unmeasured(2)):
            os.makedirs(os.path.join(d, r["run_id"]))
            with open(os.path.join(d, r["run_id"], "summary.json"), "w", encoding="utf-8") as f:
                _json.dump(r, f)
            with open(os.path.join(d, r["run_id"], "ledger.jsonl"), "w", encoding="utf-8") as f:
                f.write(_json.dumps({"span": "invoke_agent", "status": "start", "run_id": r["run_id"],
                                     "task_id": r["task_id"], "harness_id": "h",
                                     "gen_ai.request.model": "m", "harness": {}}) + "\n")
            with open(os.path.join(d, "index.jsonl"), "a", encoding="utf-8") as f:
                f.write(_json.dumps(r) + "\n")
        feats = ledger_rows_as_features(d)
        self.assertEqual([f["instance_id"] for f in feats], ["t1"])


class TestFeatureRowsStayWithTheirRuns(unittest.TestCase):
    """`ledger_rows_as_features` skips a run it cannot describe; the filter used to zip what is left
    against the FULL run list, so from the first skip onwards every feature row belonged to another
    run -- and Ochiai attributed failures to the wrong ones."""

    def setUp(self):
        import glob, shutil, tempfile
        mock = os.path.join(LAB, "data", "runs", "demo_mock")
        src = sorted(glob.glob(os.path.join(mock, "*", "summary.json")))[:3]
        if len(src) < 3:
            self.skipTest("needs three runs in demo_mock")
        self.d = tempfile.mkdtemp(prefix="hl-feat-")
        self.addCleanup(shutil.rmtree, self.d, True)
        ids = {os.path.basename(os.path.dirname(p)) for p in src}
        for p in src:
            shutil.copytree(os.path.dirname(p), os.path.join(self.d, os.path.basename(os.path.dirname(p))))
        with open(os.path.join(mock, "index.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip() and json.loads(l)["run_id"] in ids]
        # A run whose tool calls were never measured, FIRST in load order: every row after a skip is
        # the one that shifts. load_runs reads the index, which is where a capture writes its nulls.
        rows[0]["tool_calls"] = None
        self.blank = rows[0]["run_id"]
        self.keep = {r["run_id"] for r in rows[1:]}
        with open(os.path.join(self.d, "index.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_the_filter_keeps_the_rows_of_the_runs_it_was_asked_for(self):
        from harnesslab.core.real_traj import ledger_rows_as_features_with_ids
        rows = metrics.ledger_rows_as_features_filtered(self.d, self.keep)
        want = [f for rid, f in ledger_rows_as_features_with_ids(self.d) if rid in self.keep]
        self.assertEqual(len(rows), 2, "both measured runs are described")
        self.assertEqual([r["instance_id"] for r in rows], [f["instance_id"] for f in want])
        self.assertNotIn(self.blank, {rid for rid, _ in ledger_rows_as_features_with_ids(self.d)})


class TestTimeTravelStateIsUnknownAware(unittest.TestCase):
    """fork.state_vectors drew hard zeros for edits, files and tests on a run whose tool execution
    was never recorded -- the same claim the tokens and cost in the same row stopped making."""

    def steps(self):
        return [{"step": 0, "in_tokens": None, "out_tokens": None, "cost": None, "text": "",
                 "tools": [], "edits": [], "boundary": [], "sentinel": None}]

    def test_a_run_with_no_tool_record_reports_unknown_not_zero(self):
        from harnesslab.backend import fork
        (v,) = fork.state_vectors(self.steps(), unmeasured=["tool_calls", "edits", "files_touched"])
        for k in ("edits", "lines_added", "lines_removed", "files_touched", "tests_run", "last_test",
                  "boundary_events", "boundary_kinds"):
            self.assertIsNone(v[k], f"{k} was drawn as a measurement")

    def test_a_measured_run_still_reports_its_counts(self):
        from harnesslab.backend import fork
        (v,) = fork.state_vectors(self.steps(), unmeasured=[])
        self.assertEqual((v["edits"], v["files_touched"], v["boundary_events"]), (0, [], 0))


class TestTheReplayedSentinelSaysNothingAboutAnUnwatchedRun(unittest.TestCase):
    """The replay reads tool calls and edits. A source that records neither scored 0.995 with the
    patterns "no_action" and "idle_no_edit" -- a confident reading of a run nobody watched, which the
    Explorer draws as the run's risk."""

    def spans(self, unmeasured):
        return [{"span": "invoke_agent", "status": "start", "harness": {}},
                {"span": "chat", "step": 0, "seq": 0},
                {"span": "invoke_agent", "status": "end", "exit_reason": "no_action",
                 "unmeasured": unmeasured}]

    def state(self):
        return [{"step": 0}]

    def test_a_run_with_no_tool_record_gets_no_replayed_score(self):
        from harnesslab.backend import fork
        (sv,) = fork.with_replay(self.state(), self.spans(["tool_calls", "edits"]))
        self.assertIsNone(sv["risk_replay"])
        self.assertEqual(sv["replay_patterns"], [])

    def test_a_measured_run_is_still_replayed(self):
        from harnesslab.backend import fork
        (sv,) = fork.with_replay(self.state(), self.spans([]))
        self.assertIn("risk_replay", sv)


class TestNothingDescribableIsNotZeroSuspicion(unittest.TestCase):
    """Every run in a directory can now be one the feature schema cannot express, so the row list can
    be empty for a non-empty directory -- which used to be unreachable."""

    def test_attribution_over_no_rows_is_empty_not_a_crash(self):
        self.assertEqual(analysis.ochiai_attribution([]), [])


if __name__ == "__main__":
    unittest.main()
