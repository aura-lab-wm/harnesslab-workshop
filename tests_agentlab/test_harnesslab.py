"""Self-tests for the harnesslab platform layer (sentinel evaluation, job expansion, paired metrics)."""
import json, os, random, sys, unittest
LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
from harnesslab.backend import sentinel as S


class TestPrefixFeatures(unittest.TestCase):
    def test_no_budget_means_zero_token_frac(self):
        h = {"max_steps": 75, "max_total_tokens": 0, "tools": ["bash", "submit"]}
        f = S.prefix_features([], h, tokens_used=5000, pending=[], step=3)
        self.assertEqual(f["token_frac"], 0.0)

    def test_budget_burn_silent_without_budget(self):
        h = {"max_steps": 75, "max_total_tokens": 0, "tools": ["bash", "submit"]}
        ev = [{"step": 0, "tool": "bash", "args": {"command": "ls"}, "status": "ok", "result": "", "tests_passed": None, "text": ""}]
        f = S.prefix_features(ev, h, tokens_used=900000, pending=[], step=1)
        self.assertNotIn("budget_burn", [p["id"] for p in S.detect_patterns(f)])

    def test_budget_burn_fires_with_budget(self):
        h = {"max_steps": 20, "max_total_tokens": 1000, "tools": ["bash", "submit"]}
        f = S.prefix_features([], h, tokens_used=800, pending=[], step=1)
        self.assertAlmostEqual(f["token_frac"], 0.8)
        self.assertIn("budget_burn", [p["id"] for p in S.detect_patterns(f)])


def _pairwise_auc(scores, labels):
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    return sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg) / (len(pos) * len(neg))


class TestAUC(unittest.TestCase):
    def test_rank_auc_matches_pairwise_with_ties(self):
        rng = random.Random(3)
        scores = [rng.choice([0.1, 0.2, 0.3, 0.5, 0.9]) + rng.random() * 0.01 * rng.choice([0, 1]) for _ in range(300)]
        labels = [1 if rng.random() < 0.4 else 0 for _ in range(300)]
        self.assertAlmostEqual(S._auc(scores, labels), _pairwise_auc(scores, labels), places=9)

    def test_weighted_auc_equals_duplication(self):
        scores = [0.1, 0.4, 0.4, 0.7, 0.9]
        labels = [0, 0, 1, 1, 0]
        w = [1, 2, 1, 3, 1]
        dup_s = [s for s, k in zip(scores, w) for _ in range(k)]
        dup_l = [l for l, k in zip(labels, w) for _ in range(k)]
        self.assertAlmostEqual(S._auc(scores, labels, w), _pairwise_auc(dup_s, dup_l), places=9)

    def test_auc_nan_without_both_classes(self):
        v = S._auc([0.1, 0.2], [1, 1])
        self.assertNotEqual(v, v)  # NaN != NaN


class TestTrainReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model, cls.rep = S.train([os.path.join(LAB, "data/runs/demo_mock")], epochs=30, bootstrap=25)

    def test_report_has_new_sections(self):
        r = self.rep
        for k in ("auc_baselines", "auc_by_step", "auc_run_weighted", "auc_ci", "calibration", "ece", "oracle_invisible"):
            self.assertIn(k, r)
        self.assertEqual(set(r["auc_baselines"]), {"step_only", "rules_only", "prior"})

    def test_calibration_bins_cover_all_examples(self):
        self.assertEqual(sum(b["n"] for b in self.rep["calibration"]), self.rep["n_examples"])
        self.assertEqual(len(self.rep["calibration"]), 10)
        self.assertGreaterEqual(self.rep["ece"], 0.0)

    def test_alive_rows_have_baselines(self):
        for row in self.rep["auc_by_step"]:
            for k in ("step", "n", "fail_share", "auc", "auc_rules", "auc_prior"):
                self.assertIn(k, row)
            self.assertGreaterEqual(row["n"], 10)

    def test_invisible_counts_add_up(self):
        oi = self.rep["oracle_invisible"]
        self.assertEqual(oi["invisible"] + oi["visible_fail"] + oi["unknown"], oi["n_fail"])
        self.assertAlmostEqual(self.rep["oracle_invisible_failure_share"], oi["invisible"] / oi["n_fail"])

    def test_ci_is_ordered(self):
        for k in ("any_prefix", "run_weighted"):
            lo, hi = self.rep["auc_ci"][k]
            self.assertLessEqual(lo, hi)


class TestRulesScore(unittest.TestCase):
    def test_rules_score_orders_severity(self):
        quiet = dict.fromkeys(S.FEATURE_NAMES, 0.0)
        critical = {**quiet, "destructive_attempt": 1.0}
        medium = {**quiet, "max_consecutive_repeat": 1.0}
        self.assertEqual(S.rules_score(quiet), 0.0)
        self.assertGreater(S.rules_score(critical), S.rules_score(medium))
        self.assertLessEqual(S.rules_score(critical), 1.0)


class TestJobs(unittest.TestCase):
    def test_seed_pairs_across_sentinel_suffix(self):
        from harnesslab.backend import app as A
        a = A._seed_for(0, "t01_slugify", "baseline", "mock", 2)
        b = A._seed_for(0, "t01_slugify", "baseline+sentinel", "mock", 2)
        c = A._seed_for(0, "t01_slugify", "no_test_tool", "mock", 2)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_expand_sentinel_only(self):
        from harnesslab.backend import app as A
        hs = A._expand_harnesses({"harnesses": ["baseline"], "sentinel_only": True, "sentinel_ab": False, "sentinel": None, "temperature": None})
        self.assertEqual([h.id for h in hs], ["baseline+sentinel"])
        self.assertTrue(hs[0].sentinel["enabled"])

    def test_expand_ab(self):
        from harnesslab.backend import app as A
        hs = A._expand_harnesses({"harnesses": ["baseline"], "sentinel_only": False, "sentinel_ab": True, "sentinel": None, "temperature": None})
        self.assertEqual([h.id for h in hs], ["baseline", "baseline+sentinel"])
        self.assertFalse(hs[0].sentinel.get("enabled", False))


class TestSentinelPairs(unittest.TestCase):
    def test_pairs_on_demo_mock(self):
        from harnesslab.backend import metrics as M
        rows = M.rows_for("demo_mock")
        pairs = M.sentinel_pairs(rows)
        self.assertEqual(len(pairs), 1)
        p = pairs[0]
        self.assertEqual((p["model"], p["harness"]), ("mock", "permissive"))
        for k in ("hidden_pass", "verified", "boundary"):
            self.assertIn("ci95", p[k])
            self.assertEqual(p[k]["n_tasks"], 3)
        self.assertNotIn("boundary_any", rows[0])   # input rows are not mutated


class TestPythonShim(unittest.TestCase):
    # -- helpers ----------------------------------------------------------
    def _plant(self, body):
        """Put an executable `python` of our own first on PATH and reset the module's caches.

        Returns the env subprocess_env() hands out under that PATH. Restores PATH, the caches, and
        removes anything the shim path created, on cleanup.
        """
        import shutil, tempfile
        from harnesslab.core import tools
        d = tempfile.mkdtemp(prefix="hl-fakepy-")
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "python"), "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(os.path.join(d, "python"), 0o755)

        saved_path = os.environ.get("PATH", "")
        saved = {k: getattr(tools, k) for k in ("_PYTHON_OK", "_SHIM_DIR")}

        def restore():
            os.environ["PATH"] = saved_path
            made = tools._SHIM_DIR
            if made and made != saved["_SHIM_DIR"]:
                shutil.rmtree(made, ignore_errors=True)   # the probe builds one per planted world
            for k, v in saved.items():
                setattr(tools, k, v)
        self.addCleanup(restore)

        os.environ["PATH"] = d + os.pathsep + saved_path
        tools._PYTHON_OK = tools._SHIM_DIR = None         # the probe caches; this plants a new world
        return d, tools.subprocess_env()

    def _major_version_from(self, env, cwd=None):
        import subprocess
        return subprocess.run(["python", "-c", "import sys; print(sys.version_info[0])"],
                              cwd=cwd, env=env, capture_output=True, text=True)

    # -- the three ways a `python` can be on PATH and still not be usable --
    def test_a_python_that_exists_but_cannot_run_is_not_accepted(self):
        """`shutil.which` finds a FILE; a grader needs one that EXECUTES.

        A pyenv shim for a version providing no `python` is a real, executable file that exits 127
        with "pyenv: python: command not found" -- the default state on macOS, which dropped
        /usr/bin/python in 12.3. Probing for the file instead of the effect is how the grader came
        to run every oracle against a dead interpreter and report correct patches as failures.

        This plants such a shim rather than depending on the host, so it fails on any machine.
        """
        import shutil
        _, env = self._plant("#!/bin/sh\necho 'pyenv: python: command not found' >&2\nexit 127\n")
        self.assertIsNotNone(shutil.which("python", path=env["PATH"]))
        out = self._major_version_from(env)
        self.assertEqual(out.returncode, 0, f"a python that cannot run was accepted: {out.stderr!r}")
        self.assertEqual(out.stdout.strip(), "3")

    def test_a_python_2_is_not_accepted(self):
        """A host `python` that is Python 2 runs, exits 0, and then fails every oracle on syntax.

        Same empty-output, all-tiers-red signature as the dead shim, so the guard has to ask which
        Python it got, not merely whether the process started.
        """
        _, env = self._plant("#!/bin/sh\necho 2\nexit 0\n")
        out = self._major_version_from(env)
        self.assertEqual(out.stdout.strip(), "3", "a Python 2 was accepted for the grader")

    def test_a_python_that_runs_here_but_not_where_the_grader_runs_is_not_accepted(self):
        """pyenv resolves its version from the cwd, and the grader's cwd is not the launch cwd.

        `grader._run_unittest` runs in a fresh tempfile.mkdtemp("grade_"); `Sandbox.run` in a
        scratch workspace. A pyenv `python` that resolves in the checkout (a `.python-version`
        there) and 127s in a temp directory therefore passes a probe taken in the launch directory
        and then fails every oracle -- the identical silent false negative, one directory over.
        """
        import tempfile
        marker = "#!/bin/sh\n[ -f ./.python-version ] && exec %s \"$@\"\n" \
                 "echo 'pyenv: python: command not found' >&2\nexit 127\n" % sys.executable
        here = tempfile.mkdtemp(prefix="hl-pinned-")
        self.addCleanup(__import__("shutil").rmtree, here, True)
        open(os.path.join(here, ".python-version"), "w").close()

        cwd0 = os.getcwd()
        self.addCleanup(os.chdir, cwd0)
        os.chdir(here)
        _, env = self._plant(marker)

        elsewhere = tempfile.mkdtemp(prefix="grade_")
        self.addCleanup(__import__("shutil").rmtree, elsewhere, True)
        out = self._major_version_from(env, cwd=elsewhere)
        self.assertEqual(out.returncode, 0,
                         f"a python that only runs in the launch directory was accepted: {out.stderr!r}")
        self.assertEqual(out.stdout.strip(), "3")

    def test_the_shim_dir_is_published_only_once_it_contains_a_python(self):
        """backend/app.py grades up to eight cells in parallel, every one calling subprocess_env().

        Publishing the temp dir before the symlink lands hands a concurrent worker a PATH entry
        with no `python` in it -- so `python` falls through to the broken host one and the cell
        grades red for a reason nothing in its log explains.
        """
        import threading, time, unittest.mock
        from harnesslab.core import tools

        saved = {k: getattr(tools, k) for k in ("_PYTHON_OK", "_SHIM_DIR")}

        def restore():
            made = tools._SHIM_DIR
            if made and made != saved["_SHIM_DIR"]:
                __import__("shutil").rmtree(made, ignore_errors=True)
            for k, v in saved.items():
                setattr(tools, k, v)
        self.addCleanup(restore)

        tools._SHIM_DIR = None
        tools._PYTHON_OK = False                      # force the shim branch, skip the probe
        real_symlink = os.symlink
        building, release, saw = threading.Event(), threading.Event(), []

        def held_symlink(src, dst, **kw):
            building.set()            # the directory exists; the python inside it does not, yet
            release.wait(10)
            return real_symlink(src, dst, **kw)

        def reader():                 # a second cell asking for its env mid-build
            first = tools.subprocess_env()["PATH"].split(os.pathsep)[0]
            saw.append(os.path.exists(os.path.join(first, "python")))

        with unittest.mock.patch("os.symlink", held_symlink):
            builder = threading.Thread(target=tools.subprocess_env)
            builder.start()
            self.assertTrue(building.wait(10), "the shim was never built")
            t = threading.Thread(target=reader)
            t.start()
            time.sleep(0.3)           # long enough to read an early-published dir; the fix blocks
            release.set()
            t.join(10)
            builder.join(10)
        self.assertEqual(saw, [True], "a worker got a shim directory with no python in it")

    def test_python_resolves_in_subprocess_env(self):
        import shutil, subprocess
        from harnesslab.core.tools import subprocess_env
        env = subprocess_env()
        self.assertIsNotNone(shutil.which("python", path=env["PATH"]))
        out = subprocess.run("python -c 'import sys; print(sys.version_info[0])'", shell=True, env=env, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "3")


class TestLLMLayer(unittest.TestCase):
    def test_compact_trajectory_accepts_toolcall_objects_with_empty_args(self):
        from harnesslab.core.providers import ToolCall
        txt = S.compact_trajectory([], [ToolCall("c1", "list_files", {}), {"name": "submit", "arguments": {"summary": "done"}}], issue="fix it")
        self.assertIn("list_files({})", txt)
        self.assertIn("submit(", txt)

    def test_parse_verdict_fenced_embedded_truncated(self):
        v, st = S.parse_verdict('```json\n{"risk": 0.4, "pattern": "none", "rationale": "fine", "nudge": ""}\n```')
        self.assertEqual((v["risk"], st), (0.4, "ok"))
        v, st = S.parse_verdict('Sure. {"risk": 0.9, "pattern": "repeat_loop", "rationale": "x", "nudge": "stop"} done')
        self.assertEqual((v["pattern"], st), ("repeat_loop", "ok"))
        v, st = S.parse_verdict('{"risk": 0.7, "pattern": "submit_without_verify", "rationale": "the agent edited and')
        self.assertEqual((v["risk"], v["pattern"], st), (0.7, "submit_without_verify", "partial"))
        v, st = S.parse_verdict("I cannot say.")
        self.assertEqual((v, st), ({}, "unparsed"))


if __name__ == "__main__":
    unittest.main()
