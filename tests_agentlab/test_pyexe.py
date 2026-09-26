"""The interpreter must be resolved by execution, not by name.

A bare `python` does not exist on a stock macOS, and under pyenv it is a shim that
resolves per directory and can exit 127 while `which python` prints a path. Either way
the grader and the agent's own run_tests both fail, every run grades as a failure, and
the sweep reads 0% with a transcript that looks correct. These tests are the tripwire.
"""
import os, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harnesslab.core import pyexe


class TestInterpreterResolution(unittest.TestCase):
    def test_resolved_interpreter_actually_runs(self):
        exe = pyexe.interpreter()
        r = subprocess.run([exe, "-c", "print(1 + 1)"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, f"{exe} does not run: {r.stderr[:200]}")
        self.assertEqual(r.stdout.strip(), "2")

    def test_probe_rejects_an_interpreter_that_does_not_start(self):
        d = tempfile.mkdtemp()
        broken = os.path.join(d, "python")
        with open(broken, "w") as f:
            f.write("#!/bin/sh\nexit 127\n")     # what a pyenv shim does with `pyenv global system`
        os.chmod(broken, 0o755)
        self.assertFalse(pyexe._works(broken), "a shim that exits 127 must not be accepted")

    def test_probe_rejects_something_that_is_not_python(self):
        self.assertFalse(pyexe._works("/bin/echo"))

    def test_shim_puts_a_working_python_on_path(self):
        """`python -m ...` has to work even when PATH holds no python at all."""
        env = {"PATH": pyexe.shim_dir() + os.pathsep + "/nonexistent"}
        r = subprocess.run("python -c \"print('ok')\"", shell=True, capture_output=True, text=True,
                           timeout=30, env=env, cwd=tempfile.gettempdir())
        self.assertEqual(r.returncode, 0, f"shimmed python failed: {(r.stdout + r.stderr)[:300]}")
        self.assertEqual(r.stdout.strip(), "ok")


class TestGradingWithoutPythonOnPath(unittest.TestCase):
    """The end-to-end version: a sweep must grade correctly with an empty PATH."""

    def test_sweep_grades_with_no_python_on_path(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = tempfile.mkdtemp(prefix="agentlab_sweep_")
        r = subprocess.run([sys.executable, "-m", "harnesslab.core.runner", "--provider", "mock",
                            "--tasks", "t01_slugify", "--repeats", "2", "--out", out],
                           cwd=root, capture_output=True, text=True, timeout=300,
                           env={"PATH": "/nonexistent", "HOME": os.path.expanduser("~")})
        self.assertEqual(r.returncode, 0, f"the sweep died: {(r.stdout + r.stderr)[-600:]}")
        index = os.path.join(out, "index.jsonl")
        self.assertTrue(os.path.exists(index), "no index.jsonl was written")
        import json
        rows = [json.loads(l) for l in open(index) if l.strip()]
        self.assertEqual(len(rows), 2)
        # the point is not that they pass, it is that they were graded at all
        for row in rows:
            self.assertIsNotNone(row["hidden_pass"], "graded None: the grader could not run the tests")
        self.assertTrue(any(row["hidden_pass"] for row in rows),
                        "every run failed with an empty PATH, which is the 0% sweep this guards against")


if __name__ == "__main__":
    unittest.main()
