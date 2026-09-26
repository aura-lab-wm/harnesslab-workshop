"""Unit tests for the pure study-aggregate functions in harnesslab/core/analysis.py:
cohens_kappa, factor_summary, comparisons_for. Fixture-free -- every fixture here is built
in-line, no results directory required. Run from lab/:
  python -m unittest discover -s tests_agentlab -v
or  pytest tests_agentlab -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harnesslab.core.analysis import (  # noqa: E402
    cohens_kappa, factor_summary, comparisons_for, paired_bootstrap,
)


def _row(model, harness_id, task_id, hidden_pass):
    return {"model": model, "harness_id": harness_id, "task_id": task_id, "hidden_pass": hidden_pass}


class TestCohensKappa(unittest.TestCase):
    def test_degenerate_marginal_returns_none_with_reason(self):
        # both raters say True on every eligible pair -> chance agreement pe == 1 -> 0/0 form.
        pairs = [(True, True), (True, True), (True, True)]
        r = cohens_kappa(pairs)
        self.assertIsNone(r["kappa"])
        self.assertEqual(r["kappa_undefined_reason"], "degenerate_marginal")
        self.assertEqual(r["n_eligible"], 3)
        self.assertEqual(r["n_excluded"], 0)
        self.assertEqual(r["cells"], {"both_true": 3, "both_false": 0, "only_a_true": 0, "only_b_true": 0})
        self.assertEqual(r["rate_a"], {"rate": 1.0, "n": 3})
        self.assertEqual(r["rate_b"], {"rate": 1.0, "n": 3})

        # symmetric case: both raters say False on every eligible pair.
        r2 = cohens_kappa([(False, False), (False, False)])
        self.assertIsNone(r2["kappa"])
        self.assertEqual(r2["kappa_undefined_reason"], "degenerate_marginal")

    def test_one_sided_disagreement_is_defined_and_zero(self):
        # complete, deterministic disagreement (a always True, b always False): po = 0 but so is
        # pe (the marginals are constant and complementary), so kappa is the defined value 0.0,
        # not "undefined" and not an invented nonzero effect.
        pairs = [(True, False)] * 4
        r = cohens_kappa(pairs)
        self.assertIsNone(r["kappa_undefined_reason"])
        self.assertAlmostEqual(r["kappa"], 0.0)
        self.assertEqual(r["cells"], {"both_true": 0, "both_false": 0, "only_a_true": 4, "only_b_true": 0})
        self.assertEqual(r["rate_a"], {"rate": 1.0, "n": 4})
        self.assertEqual(r["rate_b"], {"rate": 0.0, "n": 4})

    def test_empty_input(self):
        r = cohens_kappa([])
        self.assertIsNone(r["kappa"])
        self.assertEqual(r["kappa_undefined_reason"], "no_eligible_pairs")
        self.assertEqual(r["n_eligible"], 0)
        self.assertEqual(r["n_excluded"], 0)
        self.assertEqual(r["cells"], {"both_true": 0, "both_false": 0, "only_a_true": 0, "only_b_true": 0})
        self.assertIsNone(r["rate_a"]["rate"])
        self.assertIsNone(r["rate_b"]["rate"])

    def test_missing_side_is_excluded_not_coerced_to_false(self):
        # a judge that never reached an item (None) must be excluded and counted, never read as False.
        pairs = [
            (True, True), (True, True), (True, False), (False, False), (False, False),
            (None, True), (True, None),
        ]
        r = cohens_kappa(pairs)
        self.assertEqual(r["n_eligible"], 5)
        self.assertEqual(r["n_excluded"], 2)
        self.assertEqual(r["cells"], {"both_true": 2, "both_false": 2, "only_a_true": 1, "only_b_true": 0})
        self.assertAlmostEqual(r["rate_a"]["rate"], 0.6)
        self.assertAlmostEqual(r["rate_b"]["rate"], 0.4)
        # po=0.8, pe=0.6*0.4+0.4*0.6=0.48 -> kappa=(0.8-0.48)/(1-0.48)
        self.assertAlmostEqual(r["kappa"], (0.8 - 0.48) / (1 - 0.48))
        self.assertIsNone(r["kappa_undefined_reason"])


class TestFactorSummary(unittest.TestCase):
    ANOVA = {
        "table": [
            {"term": "model (A)", "SS": 10.0, "df": 1, "MS": 10.0, "F": 5.0, "share": 0.5},
            {"term": "harness (B)", "SS": 4.0, "df": 1, "MS": 4.0, "F": 2.0, "share": 0.2},
            {"term": "model x harness (AB)", "SS": 1.0, "df": 1, "MS": 1.0, "F": 0.5, "share": 0.05},
            {"term": "task (block)", "SS": 3.0, "df": 2, "MS": 1.5, "F": float("nan"), "share": 0.15},
            {"term": "cell x task", "SS": 1.5, "df": 2, "MS": 0.75, "F": float("nan"), "share": 0.075},
            {"term": "residual (repeats)", "SS": 0.5, "df": 4, "MS": 0.125, "F": float("nan"), "share": 0.025},
        ],
        "a_means": {"gpt": 0.8, "qwen": 0.3},
        "b_means": {"baseline": 0.6, "no_test_tool": 0.5},
    }

    def test_leading_factor_share_and_range(self):
        r = factor_summary(self.ANOVA)
        self.assertEqual(r["leading_factor"], "model")
        self.assertAlmostEqual(r["model"]["share"], 0.5)
        self.assertAlmostEqual(r["model"]["range"], 0.5)     # 0.8 - 0.3
        self.assertAlmostEqual(r["harness"]["share"], 0.2)
        self.assertAlmostEqual(r["harness"]["range"], 0.1)   # 0.6 - 0.5
        self.assertAlmostEqual(r["interaction_share"], 0.05)

    def test_harness_can_lead(self):
        anova = {**self.ANOVA, "table": [
            {**t, "SS": 4.0} if t["term"] == "model (A)" else
            {**t, "SS": 10.0} if t["term"] == "harness (B)" else t
            for t in self.ANOVA["table"]
        ]}
        self.assertEqual(factor_summary(anova)["leading_factor"], "harness")

    def test_missing_term_raises(self):
        broken = {**self.ANOVA, "table": [t for t in self.ANOVA["table"] if t["term"] != "harness (B)"]}
        with self.assertRaises(KeyError):
            factor_summary(broken)

    def test_does_not_refit_real_factorial_result(self):
        """Sanity-checks factor_summary against the real factorial() output shape, without
        duplicating its arithmetic here."""
        from harnesslab.core.analysis import factorial
        rows = []
        for model, bump in (("strong", 0.9), ("weak", 0.1)):
            for harness in ("h1", "h2"):
                for task in ("t1", "t2", "t3"):
                    rows.append(_row(model, harness, task, bump > 0.5))
        anova = factorial(rows)
        s = factor_summary(anova)
        by_term = {t["term"]: t["share"] for t in anova["table"]}
        self.assertAlmostEqual(s["model"]["share"], by_term["model (A)"])
        self.assertAlmostEqual(s["harness"]["share"], by_term["harness (B)"])
        self.assertAlmostEqual(s["interaction_share"], by_term["model x harness (AB)"])
        self.assertEqual(s["leading_factor"], "model")   # model perfectly separates outcomes here


class TestComparisonsFor(unittest.TestCase):
    def _rows(self):
        rows = []
        outcomes = {
            ("baseline", "t1"): True, ("baseline", "t2"): False, ("baseline", "t3"): True,
            ("m1", "t1"): True, ("m1", "t2"): True, ("m1", "t3"): False,
            ("m2", "t1"): False, ("m2", "t2"): False, ("m2", "t3"): False,
        }
        for (model, task), outcome in outcomes.items():
            for _rep in range(2):    # a couple of repeats per task, like real runs
                rows.append(_row(model, "h1", task, outcome))
        return rows

    def test_matches_calling_paired_bootstrap_directly_per_model(self):
        rows = self._rows()
        out = comparisons_for(rows, baseline="baseline", B=200, seed=0)
        for model in ("m1", "m2"):
            direct = paired_bootstrap(
                [r for r in rows if r["model"] == "baseline"],
                [r for r in rows if r["model"] == model],
                B=200, seed=0,
            )
            got = out["comparisons"][model]
            self.assertEqual(got["status"], "ok")
            self.assertEqual(got["n_tasks"], direct["n_tasks"])
            self.assertAlmostEqual(got["mean_diff"], direct["mean_diff"])
            self.assertEqual(got["ci95"], direct["ci95"])
            self.assertEqual(got["per_task"], direct["per_task"])

    def test_model_with_no_overlapping_baseline_cell_is_insufficient_data(self):
        rows = [
            _row("baseline", "h1", "t1", True),
            _row("baseline", "h1", "t1", False),
            _row("only_elsewhere", "h1", "t9", True),   # no task overlap with baseline at all
        ]
        out = comparisons_for(rows, baseline="baseline", B=50)
        got = out["comparisons"]["only_elsewhere"]
        self.assertEqual(got["status"], "insufficient_data")
        self.assertEqual(got["reason"], "no_overlapping_tasks")
        self.assertEqual(got["n_tasks"], 0)
        self.assertNotIn("mean_diff", got)   # no invented zero effect

    def test_baseline_entirely_absent_marks_every_model_insufficient_data(self):
        rows = [_row("m1", "h1", "t1", True), _row("m2", "h1", "t1", False)]
        out = comparisons_for(rows, baseline="nonexistent_baseline", B=50)
        for model in ("m1", "m2"):
            got = out["comparisons"][model]
            self.assertEqual(got["status"], "insufficient_data")
            self.assertEqual(got["reason"], "no_baseline_rows")

    def test_single_grouping_pass_not_reparsed_per_model(self):
        """comparisons_for must group `rows` once, not re-walk it per model: a `rows` argument
        that raises on a second full iteration should still work (list is only walked once by
        the grouping loop; paired_bootstrap then works off the grouped lists, not the input)."""
        class OneShotList(list):
            def __init__(self, *a):
                super().__init__(*a)
                self._iterated = False

            def __iter__(self):
                if self._iterated:
                    raise AssertionError("rows iterated more than once")
                self._iterated = True
                return super().__iter__()

        rows = OneShotList(self._rows())
        out = comparisons_for(rows, baseline="baseline", B=50)
        self.assertEqual(set(out["comparisons"]), {"m1", "m2"})


if __name__ == "__main__":
    unittest.main()
