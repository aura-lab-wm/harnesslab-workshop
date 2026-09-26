"""Self-tests for the harness-as-an-object layer: content hashing, harness diff,
ablation planning, Kendall tau ranking stability, and version/drift detection."""
import json, os, shutil, sys, tempfile, unittest
from dataclasses import asdict

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.core.harness import HarnessConfig, content_hash          # noqa: E402
from harnesslab.core.ledger import RunSummary                            # noqa: E402
from harnesslab.backend import harness_tools as HT                 # noqa: E402


# --------------------------------------------------------------------------- content hash
class TestContentHash(unittest.TestCase):
    def test_key_order_does_not_change_the_hash(self):
        a = asdict(HarnessConfig(id="x"))
        b = {k: a[k] for k in reversed(list(a))}
        self.assertEqual(content_hash(a), content_hash(b))

    def test_id_and_notes_do_not_change_the_hash(self):
        base = HarnessConfig(id="baseline", notes="full surface")
        renamed = HarnessConfig(id="something_else", notes="a completely different annotation")
        self.assertEqual(content_hash(base), content_hash(renamed))

    def test_tool_order_does_not_change_the_hash(self):
        a = HarnessConfig(tools=["read_file", "bash", "submit"])
        b = HarnessConfig(tools=["submit", "read_file", "bash"])
        self.assertEqual(content_hash(a), content_hash(b))

    def test_removing_a_tool_changes_the_hash(self):
        a = HarnessConfig(tools=["read_file", "run_tests", "submit"])
        b = HarnessConfig(tools=["read_file", "submit"])
        self.assertNotEqual(content_hash(a), content_hash(b))

    def test_every_behavioural_knob_changes_the_hash(self):
        base = HarnessConfig()
        h0 = content_hash(base)
        for field, value in [("policy", "permissive"), ("max_steps", 21), ("max_total_tokens", 1),
                             ("context_window", 5), ("observation_chars", 1500), ("temperature", 0.7),
                             ("max_tokens_per_call", 99), ("include_file_listing", False),
                             ("system_prompt", "Fix it."), ("sentinel", {"enabled": True})]:
            with self.subTest(field=field):
                self.assertNotEqual(h0, content_hash(HarnessConfig(**{**asdict(base), field: value})))

    def test_hash_is_12_hex_and_deterministic(self):
        h = content_hash(HarnessConfig())
        self.assertEqual(len(h), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))
        self.assertEqual(h, content_hash(HarnessConfig()))
        self.assertEqual(h, HarnessConfig().content_hash())

    def test_accepts_a_recorded_dict_with_missing_and_extra_keys(self):
        """Ledgers recorded before a field existed still hash like the defaulted config."""
        d = asdict(HarnessConfig())
        d.pop("sentinel")
        d["some_future_field"] = 123
        self.assertEqual(content_hash(d), content_hash(HarnessConfig()))

    def test_run_summary_defaults_the_hash_for_old_rows(self):
        s = RunSummary(run_id="r", task_id="t", harness_id="h", model="m", provider="p",
                       repeat_index=0, started_at="")
        self.assertEqual(s.harness_hash, "")
        self.assertIn("harness_hash", json.loads(s.to_json()))


# --------------------------------------------------------------------------- diff
DIR = "prerecorded_mock"
HAVE_DIR = os.path.exists(os.path.join(LAB, "data", "runs", DIR, "index.jsonl"))


@unittest.skipUnless(HAVE_DIR, f"no data/runs/{DIR}")
class TestDiff(unittest.TestCase):
    def test_baseline_vs_no_test_tool(self):
        d = HT.harness_diff(a="baseline", b="no_test_tool", dir=DIR)
        self.assertEqual(d["changed_fields"], ["tools"])
        self.assertFalse(d["same_hash"])
        self.assertTrue(d["paired"])
        self.assertEqual(d["runs"]["a"], 80)
        self.assertEqual(d["runs"]["b"], 80)
        got = {m["key"]: m for m in d["metrics"]}
        self.assertEqual(len(got), len(HT.METRICS))
        # dropping run_tests cannot leave the "ran tests before submit" rate alone
        self.assertTrue(got["verified"]["moved"])
        self.assertLess(got["verified"]["mean_diff"], -0.5)
        self.assertEqual(got["verified"]["ci95"][1] < 0, True)
        # ...but it does not move pass@1 out of the noise: the headline number is the stable one
        self.assertFalse(got["pass1_hidden"]["moved"])
        self.assertLessEqual(got["pass1_hidden"]["ci95"][0], got["pass1_hidden"]["mean_diff"])
        self.assertGreaterEqual(got["pass1_hidden"]["ci95"][1], got["pass1_hidden"]["mean_diff"])
        # cost and steps do move
        self.assertTrue(got["steps"]["moved"])

    def test_every_field_carries_a_knob_explanation(self):
        d = HT.harness_diff(a="baseline", b="permissive", dir=DIR)
        for f in d["fields"]:
            self.assertIn("knob", f)
            self.assertTrue(f["knob"], f"no explanation for {f['field']}")
        self.assertEqual([f["field"] for f in d["fields"] if f["changed"] and not f["cosmetic"]], ["policy"])

    def test_same_harness_has_the_same_hash_and_no_changed_fields(self):
        d = HT.harness_diff(a="baseline", b="baseline", dir=DIR)
        self.assertTrue(d["same_hash"])
        self.assertEqual(d["changed_fields"], [])

    def test_without_a_dir_it_is_a_field_diff_only(self):
        d = HT.harness_diff(a="baseline", b="tight_budget")
        self.assertEqual(d["metrics"], [])
        self.assertIn("note", d)
        self.assertIn("max_steps", d["changed_fields"])

    def test_notes_only_difference_is_reported_as_cosmetic(self):
        cfg = asdict(HarnessConfig(id="baseline"))
        fields = HT.harness_diff(a="baseline", b="baseline")["fields"]
        self.assertTrue(all(f["cosmetic"] for f in fields if f["field"] in ("id", "notes")))
        self.assertEqual(content_hash(cfg), content_hash({**cfg, "notes": "x"}))


@unittest.skipUnless(HAVE_DIR, f"no data/runs/{DIR}")
class TestUnpairedFallback(unittest.TestCase):
    def test_unpaired_is_used_and_flagged_when_task_sets_differ(self):
        a = {"t1": 0.2, "t2": 0.4, "t3": 0.6}
        b = {"t4": 0.9, "t5": 0.8}
        r = HT._unpaired_diff(a, b, B=500)
        self.assertFalse(r["paired"])
        self.assertAlmostEqual(r["mean_diff"], 0.85 - 0.4, places=6)
        p = HT._paired_diff({"t1": 0.0, "t2": 0.0}, {"t1": 1.0, "t2": 1.0}, B=500)
        self.assertTrue(p["paired"])
        self.assertAlmostEqual(p["mean_diff"], 1.0)
        self.assertTrue(p["moved"])


# --------------------------------------------------------------------------- ablation
class TestAblationPlan(unittest.TestCase):
    def test_plan_shape(self):
        p = HT.ablate_plan(base="baseline")
        self.assertEqual(p["base"], "baseline")
        self.assertEqual(p["base_hash"], content_hash(HarnessConfig.load(os.path.join(LAB, "harnesses", "baseline.json"))))
        self.assertGreaterEqual(p["n_variants"], 10)
        self.assertEqual(p["n_variants"], len(p["variants"]))
        for v in p["variants"]:
            self.assertTrue(v["id"].startswith("baseline~"), v["id"])
            self.assertIn(v["factor"], HT.FACTORS)
            self.assertEqual(len(v["hash"]), 12)
            self.assertTrue(v["change"])
            # every variant is a valid HarnessConfig differing from the base in exactly one field
            cfg = HarnessConfig(**v["config"])
            base = HarnessConfig.load(os.path.join(LAB, "harnesses", "baseline.json"))
            diffs = [k for k in asdict(base) if k not in ("id", "notes")
                     and json.dumps(asdict(cfg)[k], sort_keys=True) != json.dumps(asdict(base)[k], sort_keys=True)]
            self.assertEqual(diffs, [v["factor"]], f"{v['id']} changed {diffs}")

    def test_variant_hashes_are_distinct_and_the_grid_rediscovers_permissive(self):
        p = HT.ablate_plan(base="baseline")
        hashes = [v["hash"] for v in p["variants"]]
        self.assertEqual(len(hashes), len(set(hashes)))
        dup = [v for v in p["variants"] if v["id"] == "baseline~policy=permissive"]
        self.assertEqual(dup[0]["duplicate_of"], "permissive")
        self.assertIn("permissive", p["job"]["harnesses"])
        self.assertNotIn("baseline~policy=permissive", p["job"]["harnesses"])

    def test_job_body_matches_JobIn(self):
        from harnesslab.backend.app import JobIn
        p = HT.ablate_plan(base="baseline", models="mock", repeats=2)
        job = JobIn(**p["job"])                        # raises if a field is missing or mistyped
        self.assertEqual(job.repeats, 2)
        self.assertEqual(job.models, ["mock"])
        self.assertEqual(job.harnesses[0], "baseline")
        self.assertEqual(p["n_runs"], len(job.models) * len(p["job"]["harnesses"]) * len(job.tasks) * job.repeats)

    def test_factor_subset(self):
        p = HT.ablate_plan(base="baseline", factors="policy,temperature")
        self.assertEqual(sorted({v["factor"] for v in p["variants"]}), ["policy", "temperature"])

    def test_unknown_factor_is_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            HT.ablate_plan(base="baseline", factors="nonsense")

    def test_plan_is_a_dry_run(self):
        before = set(os.listdir(os.path.join(LAB, "harnesses")))
        HT.ablate_plan(base="baseline")
        self.assertEqual(before, set(os.listdir(os.path.join(LAB, "harnesses"))))

    def test_save_writes_new_files_and_never_overwrites(self):
        tmp = tempfile.mkdtemp()
        old = HT.HARNESS_DIR
        try:
            shutil.copy(os.path.join(LAB, "harnesses", "baseline.json"), os.path.join(tmp, "baseline.json"))
            HT.HARNESS_DIR = tmp
            from harnesslab.backend.harness_tools import AblateIn
            r1 = HT.ablate(AblateIn(base="baseline", factors=["temperature"], save=True))
            self.assertEqual(r1["saved"], ["baseline~temperature=0.7"])
            written = os.path.join(tmp, "baseline~temperature=0.7.json")
            self.assertTrue(os.path.exists(written))
            self.assertAlmostEqual(HarnessConfig.load(written).temperature, 0.7)
            with open(written) as f:
                stamp = f.read()
            r2 = HT.ablate(AblateIn(base="baseline", factors=["temperature"], save=True))
            self.assertEqual(r2["saved"], [])
            self.assertEqual(r2["skipped_existing"], ["baseline~temperature=0.7"])
            with open(written) as f:
                self.assertEqual(f.read(), stamp)
        finally:
            HT.HARNESS_DIR = old
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- Kendall tau
class TestKendallTau(unittest.TestCase):
    def test_identical_rankings(self):
        self.assertAlmostEqual(HT.kendall_tau([3, 2, 1], [0.9, 0.5, 0.1]), 1.0)

    def test_reversed_rankings(self):
        self.assertAlmostEqual(HT.kendall_tau([3, 2, 1], [0.1, 0.5, 0.9]), -1.0)

    def test_one_swap_of_four(self):
        # 6 pairs, one discordant -> (5 - 1) / 6
        self.assertAlmostEqual(HT.kendall_tau([4, 3, 2, 1], [4, 3, 1, 2]), 4 / 6)

    def test_ties_use_tau_b(self):
        # one side fully tied carries no ranking information at all: tau-b is undefined, not 1.0
        self.assertIsNone(HT.kendall_tau([1, 1, 1], [3, 2, 1]))
        # one tie on the a side: 2 concordant pairs, 1 tie -> 2 / sqrt(3 * 2)
        self.assertAlmostEqual(HT.kendall_tau([2, 2, 1], [3, 2, 1]), 2 / (6 ** 0.5), places=9)

    def test_too_few_items(self):
        self.assertIsNone(HT.kendall_tau([1], [1]))

    def test_ranks_are_competition_ranks(self):
        self.assertEqual(HT._ranks({"a": 0.9, "b": 0.5, "c": 0.1}), {"a": 1, "b": 2, "c": 3})
        self.assertEqual(HT._ranks({"a": 0.5, "b": 0.5, "c": 0.1}), {"a": 1, "b": 1, "c": 3})


# --------------------------------------------------------------------------- ranking endpoint
class TestRanking(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(os.path.join(LAB, "data", "runs", "families_mock", "index.jsonl")), "no families_mock")
    def test_families_mock(self):
        r = HT.ranking(dir="families_mock", B=60)
        self.assertIsNone(r["insufficient"])
        self.assertEqual(len(r["models"]), 2)
        self.assertGreaterEqual(len(r["harnesses"]), 2)
        self.assertIsNotNone(r["mean_tau"])
        self.assertGreaterEqual(r["mean_tau"], -1.0)
        self.assertLessEqual(r["mean_tau"], 1.0)
        self.assertEqual(len(r["mean_tau_ci95"]), 2)
        self.assertTrue(r["finding"])
        for p in r["per_model"]:
            self.assertGreaterEqual(p["rank_range"], 0)
            self.assertEqual(p["rank_range"], p["worst_rank"] - p["best_rank"])
            if p["sensitivity"] is not None:
                self.assertGreaterEqual(p["sensitivity"], 0)

    @unittest.skipUnless(HAVE_DIR, f"no data/runs/{DIR}")
    def test_single_model_dir_is_insufficient_not_an_error(self):
        r = HT.ranking(dir=DIR)
        self.assertIsInstance(r["insufficient"], str)
        self.assertIn("1 model", r["insufficient"])
        self.assertNotIn("per_model", r)

    @unittest.skipUnless(os.path.exists(os.path.join(LAB, "data", "runs", "real_swe_agent_500", "index.jsonl")), "no real dir")
    def test_single_harness_dir_is_insufficient(self):
        r = HT.ranking(dir="real_swe_agent_500")
        self.assertIsInstance(r["insufficient"], str)
        self.assertIn("1 harness", r["insufficient"])

    def test_missing_dir_404s(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            HT.ranking(dir="definitely_not_a_results_dir")


# --------------------------------------------------------------------------- versions & drift
@unittest.skipUnless(HAVE_DIR, f"no data/runs/{DIR}")
class TestVersions(unittest.TestCase):
    def test_every_prerecorded_harness_still_matches_its_file(self):
        v = HT.versions(dir=DIR)
        self.assertEqual(v["runs"], 480)
        self.assertEqual(len(v["versions"]), 6)
        self.assertEqual(v["drift"], [])
        self.assertEqual(v["multi_version"], [])
        self.assertEqual(v["no_hash_runs"], 0)
        for entry in v["versions"]:
            self.assertEqual(entry["runs"], 80)
            self.assertTrue(entry["matches_current"], entry["harness_id"])
            self.assertEqual(entry["hash"], entry["current_hash"])
            self.assertEqual(len(entry["hash"]), 12)

    @unittest.skipUnless(os.path.exists(os.path.join(LAB, "data", "runs", "real_swe_agent_500", "index.jsonl")), "no real dir")
    def test_imported_runs_collapse_to_one_version_despite_per_model_notes(self):
        v = HT.versions(dir="real_swe_agent_500")
        self.assertEqual(len(v["versions"]), 1)
        e = v["versions"][0]
        self.assertEqual(e["runs"], 500)
        self.assertEqual(len(e["models"]), 2)          # two models, one harness identity
        self.assertFalse(e["has_file"])
        self.assertIsNone(e["matches_current"])

    def test_drift_is_detected_when_the_file_changes(self):
        tmp = tempfile.mkdtemp()
        old = HT.HARNESS_DIR
        try:
            for fn in os.listdir(os.path.join(LAB, "harnesses")):
                if fn.endswith(".json"):
                    shutil.copy(os.path.join(LAB, "harnesses", fn), os.path.join(tmp, fn))
            p = os.path.join(tmp, "baseline.json")
            with open(p) as f:
                cfg = json.load(f)
            cfg["max_steps"] = 999
            with open(p, "w") as f:
                json.dump(cfg, f)
            HT.HARNESS_DIR = tmp
            v = HT.versions(dir=DIR)
            self.assertEqual(len(v["drift"]), 1)
            d = v["drift"][0]
            self.assertEqual(d["harness_id"], "baseline")
            self.assertEqual(d["runs"], 80)
            self.assertEqual(d["changed_fields"], ["max_steps"])
            self.assertIn("80 run(s)", d["message"])
            self.assertIn("older `baseline`", d["message"])
        finally:
            HT.HARNESS_DIR = old
            shutil.rmtree(tmp, ignore_errors=True)

    def test_changed_fields_ignores_cosmetics_and_tool_order(self):
        a = asdict(HarnessConfig(id="a", notes="one", tools=["submit", "bash"]))
        b = asdict(HarnessConfig(id="b", notes="two", tools=["bash", "submit"]))
        self.assertEqual(HT._changed_fields(a, b), [])
        b["policy"] = "permissive"
        self.assertEqual(HT._changed_fields(a, b), ["policy"])


# --------------------------------------------------------------------------- routing
class TestRoutes(unittest.TestCase):
    def test_endpoints_are_reachable_through_the_app(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:                                   # httpx not installed
            self.skipTest("fastapi.testclient unavailable")
        from harnesslab.backend.app import app
        c = TestClient(app)
        for url in ["/api/harness/knobs", "/api/harness/list",
                    "/api/harness/ablate/plan?base=baseline"] + ([
                        f"/api/harness/versions?dir={DIR}",
                        f"/api/harness/diff?a=baseline&b=permissive&dir={DIR}"] if HAVE_DIR else []):
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 200)


if __name__ == "__main__":
    unittest.main()
