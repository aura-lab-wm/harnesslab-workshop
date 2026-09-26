"""The ledger has to add up to the summary.

The bill of materials in the console reconciles a run's chat spans against the total the
run recorded. That is only an honest check if the writer does not round: rounding each
span's cost to six decimals made the spans miss summary.json by up to 5e-7 a call, which
reads as an accounting discrepancy in a document meant to be evidence.
"""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class TestLedgerReconciles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="agentlab_recon_")
        # mock-weak's prices (0.25 / 1.25 per M) put the per-call cost well past six decimals,
        # which is where the old rounding actually bit; at mock's prices it happened to be exact,
        # so a sweep with the default model would pass against the bug it is meant to catch.
        r = subprocess.run([sys.executable, "-m", "harnesslab.core.runner", "--provider", "mock", "--model", "mock-weak",
                            "--tasks", "t01_slugify,t02_intervals", "--repeats", "2", "--out", cls.out],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stderr[-2000:]
        cls.runs = [json.loads(l) for l in open(os.path.join(cls.out, "index.jsonl")) if l.strip()]

    def test_the_sweep_produced_runs(self):
        self.assertEqual(len(self.runs), 4)

    def test_the_costs_have_enough_decimals_to_catch_rounding(self):
        """If every cost were already 6-decimal exact this file would pass against the bug."""
        self.assertTrue(any(round(s["cost_usd"], 6) != s["cost_usd"] for s in self.runs),
                        "no run has a cost past six decimals: this suite cannot see the rounding")

    def test_chat_spans_sum_to_the_recorded_cost_and_tokens(self):
        for s in self.runs:
            d = os.path.join(self.out, s["run_id"])
            spans = [json.loads(l) for l in open(os.path.join(d, "ledger.jsonl")) if l.strip()]
            chat = [x for x in spans if x["span"] == "chat"]
            self.assertTrue(chat, f"{s['run_id']} has no chat span")
            cost = sum(x.get("cost_usd", 0.0) for x in chat)
            # The invariant is that the writer does not ROUND, not that two float
            # accumulations are bit-identical: summing the spans and accumulating the
            # summary differ in the last bit (~1e-19). 12 places still catches the bug
            # this file exists for -- 6dp rounding misses by up to 5e-7, five orders larger.
            self.assertAlmostEqual(cost, s["cost_usd"], places=12,
                                   msg=f"{s['run_id']}: chat spans sum to {cost!r}, "
                                       f"summary says {s['cost_usd']!r}")
            self.assertEqual(sum(x["gen_ai.usage.input_tokens"] for x in chat), s["input_tokens"])
            self.assertEqual(sum(x["gen_ai.usage.output_tokens"] for x in chat), s["output_tokens"])

    def test_the_end_span_carries_the_same_total(self):
        for s in self.runs:
            d = os.path.join(self.out, s["run_id"])
            spans = [json.loads(l) for l in open(os.path.join(d, "ledger.jsonl")) if l.strip()]
            end = [x for x in spans if x["span"] == "invoke_agent" and x.get("status") == "end"]
            self.assertEqual(len(end), 1, f"{s['run_id']} should end exactly once")
            self.assertEqual(end[0]["cost_usd"], s["cost_usd"])
            self.assertEqual(end[0]["total_tokens"], s["input_tokens"] + s["output_tokens"])

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
