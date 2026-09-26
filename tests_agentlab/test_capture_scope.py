"""The enumeration guard: captured runs never appear in a default listing (spec §9.2)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import results_scope as RS   # noqa: E402


def make_dir(root, name, task_id, model="m"):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    row = {"run_id": f"{name}-r1", "task_id": task_id, "harness_id": "h", "model": model,
           "provider": "p", "repeat_index": 0, "started_at": "", "hidden_pass": None}
    with open(os.path.join(d, "index.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


class TestListResultsDirs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-scope-")
        self.addCleanup(shutil.rmtree, self.root, True)
        make_dir(self.root, "demo_mock", "t01_slugify")
        make_dir(self.root, "captured", "myproj-secret-prompt-text-deadbeef")
        os.makedirs(os.path.join(self.root, "no_index"))

    def test_captured_is_excluded_by_default(self):
        self.assertEqual(RS.list_results_dirs(self.root), ["demo_mock"])

    def test_captured_is_listed_only_when_asked_for(self):
        self.assertEqual(RS.list_results_dirs(self.root, include_private=True), ["captured", "demo_mock"])

    def test_missing_root_is_empty(self):
        self.assertEqual(RS.list_results_dirs(os.path.join(self.root, "nope")), [])

    def test_count_runs(self):
        self.assertEqual(RS.count_runs(self.root, "captured"), 1)
        self.assertEqual(RS.count_runs(self.root, "absent"), 0)

    def test_a_symlink_alias_of_captured_is_not_listed(self):
        os.symlink(os.path.join(self.root, "captured"), os.path.join(self.root, "alias"))
        self.assertEqual(RS.list_results_dirs(self.root), ["demo_mock"])


class TestConfirmPrivateExport(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-scope-")
        self.addCleanup(shutil.rmtree, self.root, True)
        make_dir(self.root, "captured", "x")

    @staticmethod
    def never(prompt):
        raise AssertionError("must not prompt")

    def test_no_private_dir_needs_no_confirmation(self):
        self.assertTrue(RS.confirm_private_export(["demo_mock"], self.root, False, False, self.never))

    def test_non_interactive_without_yes_refuses(self):
        self.assertFalse(RS.confirm_private_export(["captured"], self.root, False, False, self.never))

    def test_yes_flag_allows(self):
        self.assertTrue(RS.confirm_private_export(["captured"], self.root, True, False, self.never))

    def test_interactive_prompt_names_the_count_and_needs_a_literal_yes(self):
        seen = []

        def ask(prompt):
            seen.append(prompt)
            return "yes"

        self.assertTrue(RS.confirm_private_export(["captured"], self.root, False, True, ask))
        self.assertIn("1 captured", seen[0])
        self.assertFalse(RS.confirm_private_export(["captured"], self.root, False, True, lambda p: "y"))

    def test_spelling_variants_of_a_private_dir_still_need_a_yes(self):
        for name in ("Captured", "CAPTURED", "captured/", " captured ", "./captured"):
            with self.subTest(name=name):
                self.assertTrue(RS.is_private(self.root, name))
                self.assertFalse(RS.confirm_private_export([name], self.root, False, False, self.never))

    def test_a_symlink_alias_of_a_private_dir_is_private(self):
        os.symlink(os.path.join(self.root, "captured"), os.path.join(self.root, "alias"))
        self.assertTrue(RS.is_private(self.root, "alias"))
        self.assertFalse(RS.confirm_private_export(["alias"], self.root, False, False, self.never))


class TestNoUnguardedEnumeration(unittest.TestCase):
    """Any module that lists data/runs/ must do it through results_scope."""

    def test_no_listdir_next_to_index_jsonl_outside_results_scope(self):
        enumerators = ("os.listdir(", "os.scandir(", "glob.glob(", "glob.iglob(", ".iterdir(", "os.walk(")
        runs_markers = ("index.jsonl", "RUNS_ROOT", '"data", "runs"', "'data', 'runs'")
        offenders = []
        for sub in ("backend", "core", "capture"):
            base = os.path.join(LAB, "harnesslab", sub)
            for dp, dn, fn in os.walk(base):
                dn[:] = [d for d in dn if d != "__pycache__"]
                for f in fn:
                    if not f.endswith(".py") or f == "results_scope.py":
                        continue
                    p = os.path.join(dp, f)
                    with open(p, encoding="utf-8") as fh:
                        lines = fh.read().splitlines()
                    for i, line in enumerate(lines):
                        if not any(e in line for e in enumerators):
                            continue
                        if "results_scope: not a results listing" in line:
                            continue
                        window = " ".join(lines[max(0, i - 3):i + 4])
                        if any(m in window for m in runs_markers):
                            offenders.append(f"{os.path.relpath(p, LAB)}:{i + 1}: {line.strip()}")
        self.assertEqual(offenders, [], "list results directories through harnesslab.backend.results_scope")


class TestEnumerationSitesHideCaptured(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-scope-")
        self.addCleanup(shutil.rmtree, self.root, True)
        make_dir(self.root, "demo_mock", "t01_slugify", model="m")
        make_dir(self.root, "captured", "myproj-secret-prompt-text-deadbeef", model="secret-model")

    def test_metrics_results_dirs(self):
        from harnesslab.backend import metrics as M
        with mock.patch.object(M, "RUNS_ROOT", self.root):
            self.assertEqual([d["name"] for d in M.results_dirs()], ["demo_mock"])

    def test_store_dirs_on_disk(self):
        from harnesslab.backend import store
        self.assertEqual(store._dirs_on_disk(self.root), ["demo_mock"])

    def test_harness_tools_observed_cost(self):
        from harnesslab.backend import harness_tools as H
        with mock.patch.object(H, "RUNS_ROOT", self.root):
            self.assertEqual(sorted(H._observed_cost_per_run()), ["m"])

    def test_static_export_default_dirs(self):
        from harnesslab.backend import static_export as SE
        with mock.patch.object(SE, "RUNS_ROOT", self.root):
            self.assertEqual(SE.default_dirs(), ["demo_mock"])

    def test_http_routes_do_not_leak(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi TestClient not available")
        from harnesslab.backend import metrics as M
        from harnesslab.backend.app import app
        with mock.patch.object(M, "RUNS_ROOT", self.root):
            c = TestClient(app)
            results = c.get("/api/results")
            overview = c.get("/api/overview")
            settings = c.get("/api/settings")
        self.assertEqual([d["name"] for d in results.json()], ["demo_mock"])
        self.assertEqual([d["name"] for d in overview.json()["results"]], ["demo_mock"])
        # /api/settings arrives with the console's settings drawer; until then the SPA catch-all answers
        # it with HTML. Wherever it exists it lists results through the same guarded metrics.results_dirs.
        if settings.headers.get("content-type", "").startswith("application/json"):
            self.assertEqual(settings.json()["results"], ["demo_mock"])
        for r in (results, overview, settings):
            self.assertNotIn("secret-prompt-text", r.text)
            self.assertNotIn("secret-model", r.text)

    def test_legacy_console_default_results_dirs(self):
        from harnesslab.core import console
        lab = tempfile.mkdtemp(prefix="hl-scope-lab-")
        self.addCleanup(shutil.rmtree, lab, True)
        runs = os.path.join(lab, "data", "runs")
        make_dir(runs, "demo_mock", "t01_slugify")
        make_dir(runs, "captured", "myproj-secret-prompt-text-deadbeef")
        os.makedirs(os.path.join(runs, "stray_without_index"))
        self.assertEqual(console.default_results_dirs(lab_root=lab), [os.path.join(runs, "demo_mock")])


class TestExportCliRefusesCapturedWithoutYes(unittest.TestCase):
    def run_cli(self, *args):
        import subprocess
        return subprocess.run([sys.executable, "-m", "harnesslab", *args], cwd=LAB,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def test_export_of_captured_is_refused_non_interactively(self):
        out = os.path.join(tempfile.mkdtemp(prefix="hl-scope-"), "x.html")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        p = self.run_cli("--export", out, "--export-results", "captured")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("--yes", p.stderr)
        self.assertFalse(os.path.exists(out))

    def test_export_field_of_captured_is_refused_non_interactively(self):
        out = os.path.join(tempfile.mkdtemp(prefix="hl-scope-"), "f.html")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        p = self.run_cli("--export-field", out, "--export-results", "captured")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("--yes", p.stderr)
        self.assertFalse(os.path.exists(out))


class TestPrivateGuardMiddleware(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-guard-")
        self.addCleanup(shutil.rmtree, self.root, True)
        make_dir(self.root, "demo_mock", "t01")
        make_dir(self.root, "captured", "secret-prompt-text")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from harnesslab.backend.private_guard import private_results_guard
        probe = FastAPI()
        probe.middleware("http")(private_results_guard(lambda: self.root))

        @probe.get("/{path:path}")
        def echo(path: str):
            return {"served": path}

        self.client = TestClient(probe)

    def test_an_unrelated_query_value_is_not_treated_as_a_dir_name(self):
        for url in ("/api/results/demo_mock/query?pattern=captured", "/api/x?outcome=captured", "/api/x?harness=captured"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_parameters_that_name_a_results_dir_are_still_checked(self):
        for url in ("/api/x?dir=captured", "/api/x?results=captured", "/api/x?name=captured", "/api/x?also=captured"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_private_name_in_the_path_is_hidden_without_opt_in(self):
        for url in ("/api/export/captured/bundle.zip", "/api/fork/Captured/pairs", "/api/results/captured/runs"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_private_name_in_the_query_is_hidden_without_opt_in(self):
        for url in ("/api/bundle?results=captured", "/api/field/html?results=CAPTURED", "/field?results=captured"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_public_names_are_untouched(self):
        self.assertEqual(self.client.get("/api/bundle?results=demo_mock").status_code, 200)
        self.assertEqual(self.client.get("/api/results/demo_mock/runs").status_code, 200)

    def test_the_opt_in_header_serves_private_names(self):
        r = self.client.get("/api/export/captured/bundle.zip", headers={"X-Harnesslab-Private": "1"})
        self.assertEqual(r.status_code, 200)

    def test_the_real_app_has_the_guard_installed(self):
        from harnesslab.backend import metrics as M
        from harnesslab.backend.app import app
        from fastapi.testclient import TestClient
        with mock.patch.object(M, "RUNS_ROOT", self.root):
            c = TestClient(app)
            hidden = c.get("/api/results/captured/runs")
            served = c.get("/api/results/captured/runs", headers={"X-Harnesslab-Private": "1"})
        self.assertEqual(hidden.status_code, 404)
        self.assertNotIn("secret-prompt-text", hidden.text)
        self.assertEqual(served.status_code, 200, served.text)


class TestStoreStatsHidesPrivateDirs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-scope-")
        self.addCleanup(shutil.rmtree, self.root, True)
        make_dir(self.root, "demo_mock", "t01_slugify")
        make_dir(self.root, "captured", "myproj-secret-prompt-text-deadbeef")
        db_dir = tempfile.mkdtemp(prefix="hl-scope-db-")
        self.addCleanup(shutil.rmtree, db_dir, True)
        self.db_path = os.path.join(db_dir, "index.sqlite")

    def test_a_private_dir_already_in_the_cache_is_not_listed_in_stats(self):
        from harnesslab.backend import store
        self.addCleanup(store.close_all)
        # ingest both dirs into the cache the way a caller who already knows the private name would
        store.rows("demo_mock", runs_root=self.root, db_path=self.db_path)
        store.rows("captured", runs_root=self.root, db_path=self.db_path)
        dirs = [d["dir"] for d in store.stats(runs_root=self.root, db_path=self.db_path)["dirs"]]
        self.assertIn("demo_mock", dirs)
        self.assertNotIn("captured", dirs)


class TestOverviewIsAListingToo(unittest.TestCase):
    """`overview()` is documented as a drop-in for metrics.results_dirs(), so it is a listing, and a
    listing may never name a private directory.

    Its sibling `stats()` filters; this one did not. The gap only opens once something ingests the
    dir by name -- export._rows(name) -> store.rows(name) -> refresh(name) -- because a full refresh
    deliberately no longer evicts private dirs. Then it stays in `sources` and gets reported, `tasks`
    and all, and a captured task id embeds a slug of the first prompt.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-ov-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.db = os.path.join(self.root, "index.db")
        make_dir(self.root, "demo_mock", "t01", model="m")
        make_dir(self.root, "captured", "myproj-secret-prompt-text-deadbeef", model="secret-model")

    def test_overview_never_names_a_private_dir_even_once_it_is_cached(self):
        from harnesslab.backend import store
        store.refresh("captured", runs_root=self.root, db_path=self.db)
        names = [d["name"] for d in store.overview(runs_root=self.root, db_path=self.db)]
        self.assertIn("demo_mock", names)
        self.assertNotIn("captured", names)

    def test_overview_agrees_with_stats_about_what_may_be_listed(self):
        from harnesslab.backend import store
        store.refresh("captured", runs_root=self.root, db_path=self.db)
        ov = {d["name"] for d in store.overview(runs_root=self.root, db_path=self.db)}
        st = {d["dir"] for d in store.stats(runs_root=self.root, db_path=self.db)["dirs"]}
        self.assertEqual(ov, st, "the two listings disagree about the privacy boundary")


class TestRefreshKeepsPrivateDirsItWasAskedFor(unittest.TestCase):
    """A private dir is hidden from listings, not absent from disk: a full refresh must not evict it."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-scope-")
        self.addCleanup(shutil.rmtree, self.root, True)
        make_dir(self.root, "demo_mock", "t01")
        make_dir(self.root, "captured", "secret-prompt-text")
        self.db = os.path.join(self.root, "idx.sqlite")

    def test_a_full_refresh_keeps_an_explicitly_cached_private_dir(self):
        from harnesslab.backend import store
        store.refresh("captured", runs_root=self.root, db_path=self.db)
        store.refresh(None, runs_root=self.root, db_path=self.db)
        again = store.refresh("captured", runs_root=self.root, db_path=self.db)
        self.assertEqual(again.get("captured", 0), 0)
        self.assertEqual(store._dirs_on_disk(self.root), ["demo_mock"])


if __name__ == "__main__":
    unittest.main()
