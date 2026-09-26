"""Self-test for the lab infrastructure. Run from lab/:  python -m unittest discover -s tests_agentlab -v"""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harnesslab.core.tools import check_command, check_write, Sandbox
from harnesslab.core.analysis import pass_at_k, pass_pow_k, cohen_kappa, bootstrap_ci
from harnesslab.core.harness import HarnessConfig, run_task
from harnesslab.core.grader import load_task, grade
from harnesslab.core.providers import make_provider

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPolicy(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(check_command("rm -rf cache").kind, "destructive_shell")
        self.assertEqual(check_command("git reset --hard").kind, "destructive_shell")
        self.assertEqual(check_command("curl http://x").kind, "network")
        self.assertEqual(check_command("sudo ls").kind, "privilege")
        self.assertEqual(check_command("cat ~/.ssh/id_rsa").kind, "secret_access")
        self.assertEqual(check_command("cat ../../etc/hosts").kind, "path_escape")
        self.assertTrue(check_command("python -m unittest discover -s tests").allowed)
        self.assertTrue(check_command("ls -la && grep -rn slug textkit").allowed)

    def test_writes(self):
        self.assertEqual(check_write("tests/test_x.py").kind, "test_tampering")
        self.assertEqual(check_write("../x.py").kind, "path_escape")
        self.assertTrue(check_write("pkg/mod.py").allowed)


class TestEstimators(unittest.TestCase):
    def test_pass_k(self):
        self.assertAlmostEqual(pass_at_k(5, 10, 1), 0.5)
        self.assertAlmostEqual(pass_pow_k(5, 10, 1), 0.5)
        self.assertAlmostEqual(pass_at_k(10, 10, 3), 1.0)
        self.assertAlmostEqual(pass_pow_k(10, 10, 3), 1.0)
        self.assertAlmostEqual(pass_pow_k(2, 10, 3), 0.0)
        self.assertGreater(pass_at_k(5, 10, 3), pass_pow_k(5, 10, 3))

    def test_kappa(self):
        self.assertAlmostEqual(cohen_kappa([True, False, True, False], [True, False, True, False]), 1.0)
        self.assertAlmostEqual(cohen_kappa([True] * 9 + [False], [True] * 10), 0.0)

    def test_bootstrap(self):
        lo, hi = bootstrap_ci([0, 1, 1, 1, 0, 1, 1, 0, 1, 1], B=500)
        self.assertLessEqual(lo, 0.7); self.assertGreaterEqual(hi, 0.7)


@unittest.skipUnless(os.path.isdir(os.path.join(LAB, "solutions", "reference")),
                      "requires the full checkout: solutions/reference/ "
                      "(instructor solutions are not part of the school replication package)")
class TestSandbox(unittest.TestCase):
    def test_patch_and_grade(self):
        task = load_task(os.path.join(LAB, "tasks", "t01_slugify"))
        sb = Sandbox(task["dir"])
        try:
            self.assertEqual(sb.patch(), "")
            g0 = grade(task["dir"], sb.workdir)
            self.assertFalse(g0["hidden"])
            with open(os.path.join(LAB, "solutions", "reference", "t01_slugify", "textkit", "slug.py")) as f:
                sb.write_file("textkit/slug.py", f.read())
            self.assertIn("+++ b/textkit/slug.py", sb.patch())
            g1 = grade(task["dir"], sb.workdir)
            self.assertTrue(g1["visible"] and g1["hidden"] and g1["strong"])
            self.assertFalse(sb.tests_modified())
        finally:
            sb.cleanup()

    def test_reference_solutions_pass_all_tiers(self):
        tasks_root = os.path.join(LAB, "tasks")
        # only real task directories: macOS drops .DS_Store into any browsed folder
        for tid in sorted(t for t in os.listdir(tasks_root)
                          if os.path.isfile(os.path.join(tasks_root, t, "task.json"))):
            task = load_task(os.path.join(tasks_root, tid))
            sb = Sandbox(task["dir"])
            try:
                ref = os.path.join(LAB, "solutions", "reference", tid)
                for dp, _, fn in os.walk(ref):
                    for f in fn:
                        rel = os.path.relpath(os.path.join(dp, f), ref)
                        with open(os.path.join(dp, f)) as fh:
                            sb.write_file(rel, fh.read())
                g = grade(task["dir"], sb.workdir)
                self.assertTrue(g["hidden"] and g["strong"], f"{tid}: {g['logs']}")
            finally:
                sb.cleanup()


class TestMockRun(unittest.TestCase):
    def test_end_to_end(self):
        task = load_task(os.path.join(LAB, "tasks", "t02_intervals"))
        out = tempfile.mkdtemp()
        s = run_task(task, HarnessConfig(), make_provider("mock", "mock", seed=1), out, seed=1)
        self.assertIn(s.exit_reason, ("submitted", "no_action", "max_steps"))
        self.assertTrue(os.path.exists(os.path.join(out, s.run_id, "ledger.jsonl")))
        self.assertGreater(s.input_tokens, 0)
        self.assertIsNotNone(s.hidden_pass)

    def test_seed_reproducible(self):
        task = load_task(os.path.join(LAB, "tasks", "t03_ratelimit"))
        outs = []
        for _ in range(2):
            out = tempfile.mkdtemp()
            s = run_task(task, HarnessConfig(), make_provider("mock", "mock"), out, seed=42)
            outs.append((s.exit_reason, s.steps, s.hidden_pass, s.tool_calls))
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
