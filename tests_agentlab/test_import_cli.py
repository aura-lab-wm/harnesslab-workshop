"""`harnesslab import` -- the command line over the nine import adapters.

The adapters and the confidence-scored detector have been reachable only over HTTP since they
were written, which means the one way to exercise them was to start a server and drive a page.
This suite drives the CLI instead, as a subprocess, exactly as a person or a script would: the
detector's verdict has to be printable, the import has to land real files under a lab root the
flag chose, and a forced adapter has to beat the sniffer.

Every test writes into a temporary lab root (`--lab`), so nothing here can touch `data/runs/`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.__main__ import suggest_results_dir            # noqa: E402

FIX = os.path.join(LAB, "tests_agentlab", "fixtures", "importers")
CC = os.path.join(FIX, "claude_code_session.jsonl")
SA = os.path.join(FIX, "swe_agent")
TJ = os.path.join(FIX, "trajectory_v1.json")


class CLI(unittest.TestCase):
    """Runs the real command, in a subprocess, against a throwaway lab root."""

    def setUp(self):
        self.lab = tempfile.mkdtemp(prefix="harnesslab-cli-")
        self.addCleanup(shutil.rmtree, self.lab, True)

    def run_cli(self, *args, lab=True):
        env = {**os.environ, "NO_COLOR": "1"}
        if lab:
            env["HARNESSLAB_LAB"] = self.lab
        else:
            env.pop("HARNESSLAB_LAB", None)
        return subprocess.run([sys.executable, "-B", "-m", "harnesslab", "import", *args],
                              cwd=LAB, env=env, capture_output=True, text=True, timeout=180)

    def runs_dir(self, name):
        return os.path.join(self.lab, "data", "runs", name)

    def index(self, name):
        with open(os.path.join(self.runs_dir(name), "index.jsonl"), encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]


class TestDetectMode(CLI):
    def test_detect_names_the_source_and_prints_per_adapter_confidence(self):
        r = self.run_cli("--detect", CC)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("claude_code", r.stdout)
        # the point of the mode: not just the winner, but the numbers behind it
        self.assertIn("1.00", r.stdout)
        self.assertIn("qwen_code", r.stdout)   # the runner-up the sniffer also scored
        self.assertIn("0.60", r.stdout)

    def test_detect_scores_a_directory_too(self):
        """A directory is the normal shape of a session store, so it cannot be the case that
        only a single file gets per-adapter numbers."""
        r = self.run_cli("--detect", SA)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("swe_agent", r.stdout)
        self.assertIn("1 session", r.stdout)
        self.assertRegex(r.stdout, r"swe_agent\s+\d\.\d\d")

    def test_detect_imports_nothing(self):
        """--detect is a question. It resolves the lab root like every other subcommand, so a
        fresh --lab gets seeded (harnesslab.backend.paths does that at import time); what it must
        never do is write a run."""
        r = self.run_cli("--detect", CC)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        runs = os.path.join(self.lab, "data", "runs")
        self.assertEqual([] if not os.path.isdir(runs) else os.listdir(runs), [])

    def test_detect_on_an_unknown_format_fails_and_lists_the_adapters(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "notes.json"), "w") as f:
            f.write('{"hello": "world"}')
        r = self.run_cli("--detect", d)
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        self.assertIn("swe_agent", out)          # the list to pick --source from
        self.assertIn("inspect", out)

    def test_detect_on_a_missing_path_fails(self):
        r = self.run_cli("--detect", os.path.join(self.lab, "nope"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such file or directory", (r.stdout + r.stderr).lower())


class TestImportMode(CLI):
    def test_imports_into_the_results_dir_under_the_lab_the_flag_chose(self):
        r = self.run_cli(CC, "--results-dir", "cli_cc")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.index("cli_cc")
        self.assertEqual(len(rows), 1)
        self.assertIn("claude_code", r.stdout)
        self.assertIn("cli_cc", r.stdout)
        # a ledger under the run, the same artefact the runner writes
        self.assertTrue(os.path.exists(os.path.join(self.runs_dir("cli_cc"), rows[0]["run_id"], "ledger.jsonl")))

    def test_says_how_many_runs_carry_a_real_verdict(self):
        """The honesty rule of the whole subsystem: an unknown outcome is not a failure, and the
        CLI has to say which it got, or a directory of unknowns reads as pass@1 = 0 with no warning."""
        r = self.run_cli(SA, "--results-dir", "cli_sa")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("verdict", r.stdout.lower())

    def test_a_second_import_of_the_same_path_adds_nothing(self):
        self.run_cli(CC, "--results-dir", "cli_cc")
        r = self.run_cli(CC, "--results-dir", "cli_cc")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.index("cli_cc")), 1, "the importer is idempotent; the CLI must not double-write")
        self.assertIn("skipped", r.stdout.lower())

    def test_source_forces_an_adapter_over_what_the_sniffer_picked(self):
        """The fixtures directory sniffs as openhands on totals; --source has to override that."""
        r = self.run_cli(FIX, "--source", "claude_code", "--results-dir", "cli_forced")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.index("cli_forced")
        self.assertEqual(len(rows), 1)
        self.assertIn("claude_code", r.stdout)
        self.assertNotIn("openhands", r.stdout)

    def test_an_unknown_source_name_is_refused_before_anything_is_written(self):
        r = self.run_cli(CC, "--source", "not_an_adapter", "--results-dir", "cli_bad")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.runs_dir("cli_bad")))

    def test_a_results_dir_that_would_escape_the_runs_root_is_refused(self):
        for bad in ("a/b", "../out", ".hidden"):
            r = self.run_cli(CC, "--results-dir", bad)
            self.assertNotEqual(r.returncode, 0, f"{bad!r} was accepted")
        self.assertFalse(os.path.exists(os.path.join(self.lab, "data", "runs", "a")))

    def test_without_a_results_dir_it_names_one_from_the_source_and_says_which(self):
        r = self.run_cli(TJ, )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        want = suggest_results_dir("trajectory", TJ)
        self.assertIn(want, r.stdout)
        self.assertEqual(len(self.index(want)), 1)

    def test_a_missing_path_fails_without_a_traceback(self):
        r = self.run_cli(os.path.join(self.lab, "nope"), "--results-dir", "cli_x")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)


class TestSuggestedName(unittest.TestCase):
    """The name the CLI falls back to, and the one the UI offers -- one function, so the two
    surfaces cannot drift into naming the same import two different things."""

    def test_is_the_source_and_a_slug_of_the_path(self):
        self.assertEqual(suggest_results_dir("claude_code", "/home/me/.claude/projects/my-repo"),
                         "imported_claude_code_my_repo")

    def test_ignores_a_trailing_slash(self):
        self.assertEqual(suggest_results_dir("codex", "/a/b/sessions/"), "imported_codex_sessions")

    def test_is_a_legal_results_dir_name(self):
        for path in ("/a/b/../weird name!.jsonl", "/", "relative/path", ""):
            name = suggest_results_dir("inspect", path)
            self.assertNotIn("/", name)
            self.assertFalse(name.startswith("."))
            self.assertRegex(name, r"^[A-Za-z0-9_-]+$")


if __name__ == "__main__":
    unittest.main()


class TestWhenSomethingElseHoldsTheLock(CLI):
    """`data/runs/captured/` has one writer at a time (capture spine spec 5.7), and the watcher
    normally is it. An import into that directory takes the same lock and fails fast with
    CaptureLocked -- a RuntimeError, which the command did not catch, so what a person saw when
    the watcher happened to be running was a Python traceback with a flock path in it."""

    def test_a_held_lock_is_a_sentence_and_an_exit_code_not_a_traceback(self):
        from harnesslab.capture.lock import CaptureLock
        with CaptureLock(os.path.join(self.lab, "data", "runs")):
            r = self.run_cli(CC, "--results-dir", "captured")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("CaptureLocked", r.stderr)
        said = (r.stdout + r.stderr).lower()
        self.assertIn("another", said)
        self.assertIn("captur", said)

    def test_the_same_import_goes_through_once_the_lock_is_free(self):
        r = self.run_cli(CC, "--results-dir", "captured")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.index("captured")), 1)
