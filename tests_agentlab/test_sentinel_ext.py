"""Self-tests for the sentinel platform layer: the safe rule evaluator, plugin loading, portable models
and the EW-AUC@k leaderboard."""
import json, os, shutil, sys, tempfile, time, unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
from harnesslab.backend import sentinel as S            # noqa: E402
from harnesslab.backend import sentinel_ext as X        # noqa: E402

ZERO = dict.fromkeys(S.FEATURE_NAMES, 0.0)


def feats(**kw):
    return {**ZERO, **kw}


class TestSafeEval(unittest.TestCase):
    def env(self, **kw):
        return S.rule_env(feats(**kw))

    def test_accepts_the_intended_grammar(self):
        env = self.env(n_edits=3.0, step_frac=0.75, n_tests=1.0, files_touched=4.0, edits_since_test=2.0)
        env["harness_policy"] = "permissive"
        for expr, want in [
            ("edits_since_test >= 2 and steps_frac > 0.5", True),
            ("n_edits >= 2 and not (n_tests > 5)", True),
            ("n_edits == 3 or files_touched == 99", True),
            ("0.5 < step_frac <= 1.0", True),                       # chained comparison
            ("n_edits / 2 + 1 > 2", True),
            ("max(n_edits, files_touched) == 4", True),
            ("min(1.0, step_frac * 2) == 1.0", True),
            ("abs(n_edits - 5) == 2", True),
            ("round(step_frac, 1) == 0.8", True),
            ("harness_policy in ('permissive', 'lenient')", True),
            ("n_edits > 100", False),
            ("n_edits >= 2 and steps_frac > 0.9", False),
        ]:
            with self.subTest(expr=expr):
                self.assertEqual(bool(S.safe_eval(expr, env)), want)

    def test_rejects_dunder_and_attribute_access(self):
        env = self.env(n_edits=1.0)
        for expr in [
            "__import__('os').system('echo pwned')",
            "__import__",
            "().__class__.__bases__[0].__subclasses__()",
            "n_edits.__class__",
            "n_edits.real",
            "open('/etc/passwd').read()",
            "eval('1+1')",
            "exec('x=1')",
            "globals()",
            "[x for x in (1, 2)]",
            "(lambda: 1)()",
            "n_edits if n_edits else 0",          # IfExp is not in the whitelist
            "features['n_edits']",
            "f'{n_edits}'",
            "n_edits := 3",
            "print(1)",
            "unknown_feature > 1",
            "n_edits ** 99",
        ]:
            with self.subTest(expr=expr):
                with self.assertRaises(S.RuleError):
                    S.safe_eval(expr, env)

    def test_rejects_oversize_and_empty(self):
        with self.assertRaises(S.RuleError):
            S.safe_eval("", self.env())
        with self.assertRaises(S.RuleError):
            S.safe_eval("n_edits > 0 and " * 100 + "n_edits > 0", self.env())

    def test_no_builtins_leak_through_env(self):
        # a name resolves only from the env or the tiny function whitelist; nothing else is reachable
        for name in ("sum", "open", "type", "vars", "getattr", "dict", "__builtins__"):
            with self.subTest(name=name), self.assertRaises(S.RuleError):
                S.safe_eval(f"{name}((1, 2))", {})
        self.assertEqual(S.safe_eval("len((1, 2, 3))", {}), 3)

    def test_division_by_zero_is_a_rule_error_not_a_crash(self):
        with self.assertRaises(S.RuleError):
            S.safe_eval("1 / max_steps", S.rule_env(feats(), harness={"max_steps": 0}))


class TestPluginLoading(unittest.TestCase):
    """Loading is exercised against a temporary plugin dir so the shipped examples are never touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="singletree_plugins_")
        self.orig = S.PLUGINS_DIR
        S.PLUGINS_DIR = self.tmp

    def tearDown(self):
        S.PLUGINS_DIR = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)
        S.load_plugins(force=True)

    def write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_rule_plugin_fires_on_a_crafted_feature_dict(self):
        self.write("t.rule.json", json.dumps({
            "id": "unverified_streak_t", "severity": "high", "when": "edits_since_test >= 2 and steps_frac > 0.5",
            "label": "L", "reason": "R", "nudge": "N"}))
        S.load_plugins(force=True)
        hot = S.run_plugins(feats(edits_since_test=2.0, step_frac=0.75))
        self.assertEqual([p["id"] for p in hot], ["unverified_streak_t"])
        self.assertEqual(hot[0]["severity"], "high")
        self.assertEqual(hot[0]["source"], "rule")
        self.assertEqual(hot[0]["nudge"], "N")
        self.assertEqual(S.run_plugins(feats(edits_since_test=2.0, step_frac=0.2)), [])

    def test_bad_rules_are_reported_and_do_not_stop_the_good_ones(self):
        self.write("good.rule.json", json.dumps({"id": "good", "severity": "low", "when": "n_edits >= 1"}))
        self.write("evil.rule.json", json.dumps({"id": "evil", "severity": "low", "when": "__import__('os')"}))
        self.write("nosev.rule.json", json.dumps({"id": "nosev", "severity": "URGENT", "when": "n_edits >= 1"}))
        self.write("broken.rule.json", "{not json")
        st = S.load_plugins(force=True)
        self.assertEqual([d["id"] for d in st["detectors"]], ["good"])
        self.assertEqual({e["file"] for e in st["errors"]}, {"evil.rule.json", "nosev.rule.json", "broken.rule.json"})
        self.assertEqual([p["id"] for p in S.run_plugins(feats(n_edits=1.0))], ["good"])

    def test_python_plugin_loads_and_fires(self):
        self.write("mine.py", "PATTERNS = {'my_pat': {'label': 'L', 'severity': 'critical', 'why': 'W', 'nudge': 'N'}}\n"
                              "def detect(features, events, harness):\n"
                              "    if len(events) >= 2:\n"
                              "        return [{'id': 'my_pat', 'label': 'L', 'severity': 'critical', 'why': 'W', 'nudge': 'N'}]\n"
                              "    return []\n")
        S.load_plugins(force=True)
        cat = S.plugin_catalogue()
        self.assertEqual([c["id"] for c in cat], ["my_pat"])
        self.assertEqual(cat[0]["source"], "python")
        self.assertNotIn("fn", cat[0])                       # the callable is not serialised to the UI
        self.assertEqual(S.run_plugins(feats(), events=[{}, {}]), [
            {"id": "my_pat", "label": "L", "severity": "critical", "why": "W", "nudge": "N",
             "source": "python", "plugin": "mine.py"}])
        self.assertEqual(S.run_plugins(feats(), events=[{}]), [])

    def test_python_plugin_exception_is_contained(self):
        self.write("boom.py", "def detect(features, events, harness):\n    raise RuntimeError('boom')\n")
        S.load_plugins(force=True)
        self.assertEqual(S.run_plugins(feats()), [])          # no exception escapes
        self.assertTrue(any("boom" in e["error"] for e in S.load_plugins()["errors"]))

    def test_hot_reload_on_mtime_change(self):
        p = self.write("hr.rule.json", json.dumps({"id": "hr", "severity": "low", "when": "n_edits >= 5"}))
        S.load_plugins(force=True)
        self.assertEqual(S.run_plugins(feats(n_edits=1.0)), [])
        with open(p, "w") as f:
            json.dump({"id": "hr", "severity": "low", "when": "n_edits >= 1"}, f)
        os.utime(p, (time.time() + 2, time.time() + 2))       # a newer mtime, no sleep needed
        S._PLUGIN_CACHE["checked_at"] = 0.0                   # skip the once-a-second throttle
        self.assertEqual([x["id"] for x in S.run_plugins(feats(n_edits=1.0))], ["hr"])

    def test_missing_plugin_dir_is_not_an_error(self):
        S.PLUGINS_DIR = os.path.join(self.tmp, "does_not_exist")
        st = S.load_plugins(force=True)
        self.assertEqual(st["detectors"], [])
        self.assertEqual(st["errors"], [])


class TestShippedPlugins(unittest.TestCase):
    def setUp(self):
        S.load_plugins(force=True)

    def test_the_three_examples_load_cleanly(self):
        st = S.load_plugins(force=True)
        self.assertEqual(st["errors"], [])
        by_id = {d["id"]: d for d in st["detectors"]}
        self.assertEqual(set(by_id), {"unverified_edit_streak", "blind_flailing", "edit_storm"})
        self.assertEqual(by_id["edit_storm"]["source"], "python")
        self.assertEqual(by_id["unverified_edit_streak"]["source"], "rule")

    def test_edit_storm_needs_four_untested_edits_to_one_file(self):
        def ev(step, tool, path=None, tests=None):
            return {"step": step, "tool": tool, "args": {"path": path} if path else {}, "status": "ok",
                    "result": "", "tests_passed": tests, "text": ""}
        storm = [ev(i, "write_file", "pkg/a.py") for i in range(4)]
        self.assertEqual([p["id"] for p in S.run_plugins(feats(), storm)], ["edit_storm"])
        self.assertEqual(S.run_plugins(feats(), storm[:3]), [])                       # three is not a storm
        spread = [ev(0, "write_file", "a.py"), ev(1, "write_file", "b.py"), ev(2, "write_file", "a.py"),
                  ev(3, "write_file", "b.py")]
        self.assertEqual(S.run_plugins(feats(), spread), [])                          # four edits, two files
        tested = storm[:2] + [ev(2, "run_tests", tests=False)] + storm[2:]
        self.assertEqual(S.run_plugins(feats(), tested), [])                          # a test run breaks the streak

    def test_plugins_reach_the_live_hook_and_replay_through_score(self):
        """score() is what make_hook and replay both call, so this is the whole participation path."""
        harness = {"max_steps": 8, "tools": ["write_file", "run_tests", "submit"], "policy": "standard"}
        events = [{"step": i, "tool": "write_file", "args": {"path": "pkg/a.py"}, "status": "ok",
                   "result": "", "tests_passed": None, "text": ""} for i in range(5)]
        out = S.score(events, harness, 0, [], S.RiskModel.default(), step=5)
        ids = [p["id"] for p in out["patterns"]]
        self.assertIn("edit_storm", ids)                      # python plugin
        self.assertIn("unverified_edit_streak", ids)          # rule plugin: 5 untested edits, step_frac 0.75
        self.assertTrue(any(p.get("source") == "builtin" for p in out["patterns"]))
        self.assertTrue(any(p.get("source") in ("rule", "python") for p in out["patterns"]))

    def test_builtin_detector_list_is_unchanged_by_plugins(self):
        # detect_patterns stays plugin-free so training baselines remain reproducible
        f = S.prefix_features([{"step": i, "tool": "write_file", "args": {"path": "a.py"}, "status": "ok",
                               "result": "", "tests_passed": None, "text": ""} for i in range(6)],
                              {"max_steps": 20, "tools": ["write_file", "run_tests"]}, 0, pending=[], step=6)
        self.assertTrue(set(p["id"] for p in S.detect_patterns(f)) <= set(S.PATTERNS))
        self.assertLessEqual(S.rules_score(f), S.rules_score_all(f, [], {}) + 1e-9)


class TestExportImport(unittest.TestCase):
    NAME = "roundtrip_tmp"

    def setUp(self):
        # Imported models land in a temp MODELS_DIR, never in harnesslab/data/models/. Removing
        # them in tearDown only works when the unlink succeeds; redirecting the directory means
        # a failed removal cannot leave a stray model in the shipped registry either way.
        # list_models()/load_model() read the module global at call time, so the source model
        # is copied in first and stays readable.
        self._tmp = tempfile.mkdtemp(prefix="harnesslab-models-test-")
        self._real = S.MODELS_DIR
        for fn in os.listdir(self._real):
            if fn.endswith(".json"):
                shutil.copy2(os.path.join(self._real, fn), os.path.join(self._tmp, fn))
        S.MODELS_DIR = self._tmp

    def tearDown(self):
        S.MODELS_DIR = self._real
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_round_trip_preserves_predictions(self):
        src = S.list_models()[0]["name"]
        bundle = X._model_bundle(src)
        self.assertEqual(bundle["format"], X.BUNDLE_FORMAT)
        for k in ("training_dirs", "run_counts", "features", "cv_metrics", "created_at", "singletree_version"):
            self.assertIn(k, bundle["provenance"])
        self.assertEqual(bundle["provenance"]["features"], list(S.FEATURE_NAMES))
        self.assertEqual(len(bundle["sha256"]), 64)

        res = X.import_model(X.ImportIn(bundle=bundle, name=self.NAME, activate=False))
        self.assertEqual(res["name"], self.NAME)
        self.assertNotEqual(S.active_model_name(), self.NAME)          # activate=False must not switch
        a, b = S.load_model(src), S.load_model(self.NAME)
        for f in ({}, {"n_edits": 3.0, "step_frac": 0.8}, {"submit_without_verify": 1.0, "n_errors": 2.0}):
            ff = {**ZERO, **f}
            self.assertAlmostEqual(a.predict(ff), b.predict(ff), places=12)
        self.assertTrue(b.meta.get("imported"))
        self.assertEqual(b.meta["imported_from"]["training_dirs"], bundle["provenance"]["training_dirs"])

    def test_export_of_unknown_model_is_404(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            X._model_bundle("no_such_model_here")
        self.assertEqual(cm.exception.status_code, 404)

    def test_import_refuses_a_different_feature_list(self):
        from fastapi import HTTPException
        bundle = json.loads(json.dumps(X._model_bundle(S.list_models()[0]["name"])))
        bundle["model"]["names"] = bundle["model"]["names"][:-1] + ["a_feature_from_the_future"]
        with self.assertRaises(HTTPException) as cm:
            X.import_model(X.ImportIn(bundle=bundle, name=self.NAME + "_2", ignore_checksum=True))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("a_feature_from_the_future", cm.exception.detail)
        self.assertIn(S.FEATURE_NAMES[-1], cm.exception.detail)
        self.assertFalse(os.path.exists(os.path.join(S.MODELS_DIR, self.NAME + "_2.json")))

    def test_import_refuses_a_tampered_bundle(self):
        from fastapi import HTTPException
        bundle = json.loads(json.dumps(X._model_bundle(S.list_models()[0]["name"])))
        bundle["model"]["w"][0] += 1.0
        with self.assertRaises(HTTPException) as cm:
            X.import_model(X.ImportIn(bundle=bundle, name=self.NAME))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("checksum", cm.exception.detail)
        # ...unless the caller says so explicitly
        self.assertEqual(X.import_model(X.ImportIn(bundle=bundle, name=self.NAME, ignore_checksum=True))["name"], self.NAME)

    def test_import_rejects_junk(self):
        from fastapi import HTTPException
        for junk in ({}, {"model": {"w": [1]}}, {"names": S.FEATURE_NAMES}):
            with self.assertRaises(HTTPException):
                X.import_model(X.ImportIn(bundle=junk, name=self.NAME))


class TestLeaderboard(unittest.TestCase):
    """Shape and invariants on the shipped mock directory. Short k list, small bootstrap: seconds."""
    KS = (2, 3)

    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.mkdtemp(prefix="singletree_lb_cache_")
        cls.orig_cache, X.CACHE_DIR = X.CACHE_DIR, cls.cache
        cls.lb = X.leaderboard("prerecorded_mock", cls.KS, threshold=0.6, bootstrap=40, use_cache=False)

    @classmethod
    def tearDownClass(cls):
        X.CACHE_DIR = cls.orig_cache
        shutil.rmtree(cls.cache, ignore_errors=True)

    def test_top_level_shape(self):
        lb = self.lb
        self.assertEqual(lb["dir"], "prerecorded_mock")
        self.assertEqual(lb["k"], list(self.KS))
        self.assertEqual(lb["n_runs"], 480)
        self.assertGreater(lb["n_prefixes"], lb["n_runs"])
        self.assertIn("EW-AUC@k", lb["metric"])

    @unittest.skipUnless(os.path.isfile(os.path.join(LAB, "harnesslab", "data", "models", "real_swe_agent_500.json")),
                          "requires the full checkout: harnesslab/data/models/real_swe_agent_500.json "
                          "(the school package ships only the mock_prerecorded sentinel)")
    def test_every_entrant_is_present(self):
        names = [r["model"] for r in self.lb["rows"]]
        for shipped in ("mock_prerecorded", "real_swe_agent_500", "prior (untrained)", "rules only"):
            self.assertIn(shipped, names)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sum(1 for r in self.lb["rows"] if r["active"]), 1)
        row = next(r for r in self.lb["rows"] if r["model"] == "mock_prerecorded")
        self.assertTrue(row["in_sample"])                      # trained on this very directory
        self.assertFalse(next(r for r in self.lb["rows"] if r["model"] == "rules only")["in_sample"])

    def test_per_k_rows_are_well_formed(self):
        for r in self.lb["rows"]:
            self.assertEqual([p["k"] for p in r["per_k"]], list(self.KS))
            for p in r["per_k"]:
                self.assertGreater(p["n_alive"], 0)
                self.assertTrue(0.0 <= p["ew_auc"] <= 1.0, p)
                lo, hi = p["ew_auc_ci"]
                self.assertLessEqual(lo, p["ew_auc"] + 1e-9)
                self.assertGreaterEqual(hi, p["ew_auc"] - 1e-9)
                for key in ("recall", "false_alarm"):
                    if p[key] is not None:
                        self.assertTrue(0.0 <= p[key] <= 1.0)
                if p["median_lead"] is not None:
                    self.assertGreaterEqual(p["median_lead"], 0)

    def test_alive_cohort_shrinks_with_k(self):
        per_k = self.lb["rows"][0]["per_k"]
        self.assertGreaterEqual(per_k[0]["n_alive"], per_k[-1]["n_alive"])
        self.assertLessEqual(per_k[0]["n_alive"], self.lb["n_runs"])

    def test_overall_operating_point(self):
        for r in self.lb["rows"]:
            o = r["overall"]
            self.assertEqual(o["n_runs"], self.lb["n_runs"])
            self.assertEqual(o["n_fail"], self.lb["n_fail"])
            for key in ("recall", "false_alarm"):
                self.assertTrue(o[key] is None or 0.0 <= o[key] <= 1.0)

    def test_the_untrained_prior_is_no_better_than_the_trained_model_on_its_own_data(self):
        auc = {r["model"]: r["per_k"][-1]["ew_auc"] for r in self.lb["rows"]}
        self.assertGreaterEqual(auc["mock_prerecorded"], auc["prior (untrained)"] - 0.2)

    def test_markdown_is_a_table(self):
        md = X.leaderboard_markdown(self.lb)
        lines = [l for l in md.splitlines() if l.startswith("|")]
        self.assertEqual(len(lines), len(self.lb["rows"]) + 2)          # header + separator + one per model
        self.assertIn(f"EW-AUC@{self.KS[0]}", lines[0])
        self.assertIn("in-sample", md)                                   # the caveat is carried into the paper table
        for r in self.lb["rows"]:
            self.assertIn(r["model"], md)

    def test_cache_hit_on_the_second_call(self):
        first = X.leaderboard("demo_mock", (2,), bootstrap=25, use_cache=True)
        self.assertFalse(first["cached"])
        second = X.leaderboard("demo_mock", (2,), bootstrap=25, use_cache=True)
        self.assertTrue(second["cached"])
        self.assertEqual(first["rows"][0]["per_k"], second["rows"][0]["per_k"])

    def test_unknown_dir_is_404(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            X.leaderboard("no_such_dir", (2,), bootstrap=0)
        self.assertEqual(cm.exception.status_code, 404)

    def test_k_parsing(self):
        from fastapi import HTTPException
        self.assertEqual(X._parse_ks("10,15,20"), (10, 15, 20))
        self.assertEqual(X._parse_ks(" 20, 10 ,10"), (10, 20))
        for bad in ("", "a,b", "1,2,3,4,5,6,7,8,9", "-1"):
            with self.assertRaises(HTTPException):
                X._parse_ks(bad)


class TestRoutesDoNotCollide(unittest.TestCase):
    def test_new_paths_are_distinct_from_app_paths(self):
        from harnesslab.backend import app as A
        existing = {getattr(r, "path", None) for r in A.app.router.routes if getattr(r, "path", None)}
        mine = {r.path for r in X.router.routes}
        self.assertTrue(mine, "sentinel_ext registers no routes")
        self.assertEqual(mine & existing, set(), f"path collision with app.py: {mine & existing}")
        for p in mine:
            self.assertTrue(p.startswith("/api/sentinel/"), p)


if __name__ == "__main__":
    unittest.main()
