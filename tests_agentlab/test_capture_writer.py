"""Capture writer, its harness config, and the cost model it depends on."""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.core import providers as P   # noqa: E402

FIX = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "cc_session.jsonl")


class TestCacheAwareCost(unittest.TestCase):
    def test_existing_signature_is_unchanged(self):
        self.assertAlmostEqual(P.cost_usd("claude-opus-5", 1_000_000, 1_000_000), 30.0)

    def test_cache_reads_and_writes_are_priced_off_the_input_rate(self):
        read_mult, write_mult = P.cache_multipliers("claude-opus-5")
        self.assertAlmostEqual(P.cost_usd("claude-opus-5", 0, 0, cache_read_tokens=1_000_000), 5.0 * read_mult)
        self.assertAlmostEqual(P.cost_usd("claude-opus-5", 0, 0, cache_write_tokens=1_000_000), 5.0 * write_mult)

    def test_anthropic_multipliers(self):
        self.assertEqual(P.cache_multipliers("anthropic/claude-sonnet-5"), (0.10, 1.25))

    def test_unknown_providers_bill_cached_tokens_as_plain_input(self):
        self.assertEqual(P.cache_multipliers("some-new-model"), (1.0, 1.0))

    def test_fable_reads_cache_cheaper_than_other_claude_models(self):
        self.assertEqual(P.cache_multipliers("claude-fable-5-1"), (0.025, 1.25))
        self.assertEqual(P.cache_multipliers("anthropic/claude-fable-5-1"), (0.025, 1.25))
        self.assertEqual(P.cache_multipliers("claude-opus-5"), (0.10, 1.25))

    def test_fable_5_1_has_its_own_published_price(self):
        self.assertTrue(P.is_priced("claude-fable-5-1"))
        self.assertEqual(P.price_for("claude-fable-5-1"), (10.0, 50.0))
        self.assertAlmostEqual(P.cost_usd("claude-fable-5-1", 0, 0, cache_read_tokens=1_000_000), 10.0 * 0.025)

    def test_is_priced_distinguishes_a_real_price_from_the_fallback(self):
        self.assertTrue(P.is_priced("claude-opus-5"))
        self.assertFalse(P.is_priced("some-new-model"))


from harnesslab.backend.importers.common import Event as E, Session   # noqa: E402
from harnesslab.capture.harness_config import capture_harness          # noqa: E402


class TestCaptureHarness(unittest.TestCase):
    @staticmethod
    def sess(modes, n_tools):
        ev = [E("user", text="go")] + [E("tool_call", name="Bash", call_id=f"c{i}", args={"command": "ls"})
                                       for i in range(n_tools)]
        return Session(source="claude_code", session_id="s", path="/x/s.jsonl", agent="claude-code",
                       agent_version="2.1.3", events=ev,
                       extra={"permission_modes": modes, "entrypoint": "cli"})

    def test_hash_ignores_observed_behaviour(self):
        self.assertEqual(capture_harness(self.sess(["default"], 1))["hash"],
                         capture_harness(self.sess(["default"], 40))["hash"])

    def test_policy_changes_the_cell(self):
        a, b = capture_harness(self.sess(["bypassPermissions"], 1)), capture_harness(self.sess(["default"], 1))
        self.assertEqual((a["policy"], b["policy"]), ("permissive", "strict"))
        self.assertNotEqual(a["hash"], b["hash"])
        self.assertEqual(capture_harness(self.sess([], 1))["policy"], "unknown")

    def test_no_fingerprint_in_the_config_and_the_hash_matches_the_lab(self):
        from harnesslab.core.harness import content_hash
        h = capture_harness(self.sess(["default"], 3))
        self.assertNotIn("fingerprint", h)
        self.assertEqual(h["hash"], content_hash(h))
        self.assertEqual(h["id"], "claude-code@2.1.x")
        self.assertEqual(h["observed_config"], {"permission_modes": ["default"], "entrypoint": "cli",
                                                "agent_version": "2.1.3"})


from harnesslab.capture.writer import model_calls   # noqa: E402


class TestModelCalls(unittest.TestCase):
    def test_a_discarded_call_does_not_consume_its_requests_usage(self):
        u = {"input_tokens": 3, "output_tokens": 9}
        ev = [E("user", text="go"),
              E("assistant", text="   ", request_id="r1", usage=u),
              E("user", text="again"),
              E("tool_call", name="Bash", call_id="c1", request_id="r1", usage=u)]
        (call,) = model_calls(ev)
        self.assertEqual(call["usage"]["output_tokens"], 9)

    def test_content_blocks_of_one_request_form_one_call(self):
        ev = [E("user", text="go"),
              E("assistant", text="plan", request_id="r1", usage={"input_tokens": 1, "output_tokens": 9}),
              E("tool_call", name="Read", call_id="c1", request_id="r1", usage={"input_tokens": 1, "output_tokens": 9}),
              E("tool_call", name="Bash", call_id="c2", request_id="r1", usage={"input_tokens": 1, "output_tokens": 9}),
              E("tool_result", call_id="c1", text="x"),
              E("tool_result", call_id="c2", text="y"),
              E("tool_call", name="Edit", call_id="c3", request_id="r2", usage={"output_tokens": 4})]
        calls = model_calls(ev)
        self.assertEqual([len(c["tools"]) for c in calls], [2, 1])
        self.assertEqual(calls[0]["texts"], ["plan"])
        self.assertEqual(calls[0]["usage"]["output_tokens"], 9)

    def test_user_closes_prose_as_its_own_call_and_starts_a_turn(self):
        ev = [E("user", text="one"),
              E("tool_call", name="Bash", call_id="c1", request_id="r1"),
              E("tool_result", call_id="c1", text="ok"),
              E("assistant", text="done", request_id="r2"),
              E("user", text="two"),
              E("tool_call", name="Bash", call_id="c2", request_id="r3")]
        self.assertEqual([(c["turn"], bool(c["tools"]), c["texts"]) for c in model_calls(ev)],
                         [(0, True, []), (0, False, ["done"]), (1, True, [])])

    def test_consecutive_user_messages_do_not_skip_turns(self):
        ev = [E("user", text="a"), E("user", text="[image]"), E("assistant", text="hi", request_id="r1")]
        self.assertEqual([c["turn"] for c in model_calls(ev)], [0])

    def test_events_without_request_ids_keep_the_importer_rule(self):
        ev = [E("user", text="go"), E("assistant", text="thinking"),
              E("tool_call", name="Bash", call_id="c1"),
              E("tool_result", call_id="c1", text="ok"),
              E("assistant", text="next"), E("tool_call", name="Read", call_id="c2")]
        self.assertEqual([(c["texts"], len(c["tools"])) for c in model_calls(ev)],
                         [(["thinking"], 1), (["next"], 1)])

    def test_an_interleaved_request_is_counted_once(self):
        u1 = {"input_tokens": 3, "output_tokens": 9}
        ev = [E("user", text="go"),
              E("assistant", text="a1", request_id="r1", usage=u1),
              E("assistant", text="b", request_id="r2", usage={"input_tokens": 2, "output_tokens": 4}),
              E("tool_call", name="Bash", call_id="c1", request_id="r1", usage=u1)]
        calls = model_calls(ev)
        self.assertEqual(sum(int(c["usage"].get("output_tokens") or 0) for c in calls), 13)
        self.assertEqual(calls[-1]["usage"]["output_tokens"], 0)


from harnesslab.backend.importers import claude_code, common as C   # noqa: E402
from harnesslab.capture import identity                             # noqa: E402
from harnesslab.capture.writer import write_run                     # noqa: E402


def build_fixture_run(out_dir):
    sess = claude_code.parse_file(FIX)
    sess.events = identity.dedup_events(sess.events)
    harness = capture_harness(sess)
    task_id = C.derive_task_id(sess)
    run_id = C.run_id_for(sess, task_id)
    summary, extras = write_run(sess, out_dir, run_id, task_id, harness)
    with open(os.path.join(out_dir, "ledger.jsonl"), encoding="utf-8") as f:
        spans = [json.loads(line) for line in f if line.strip()]
    return summary, extras, spans


class TestWriteRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hl-writer-")
        cls.summary, cls.extras, cls.spans = build_fixture_run(os.path.join(cls.tmp, "run"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def of(self, kind):
        return [s for s in self.spans if s["span"] == kind]

    def test_one_chat_span_per_request_with_usage_counted_once(self):
        chats = self.of("chat")
        self.assertEqual([c["request_id"] for c in chats], ["req_1", "req_2", "req_3", "req_4", "req_5", "req_6"])
        self.assertEqual([c["gen_ai.usage.output_tokens"] for c in chats], [120, 80, 40, 30, 20, 10])
        self.assertEqual([t["name"] for t in chats[0]["tool_calls"]], ["read_file", "bash"])
        self.assertEqual((self.summary.input_tokens, self.summary.output_tokens), (13, 300))
        self.assertEqual((self.extras["cache_read_input_tokens"], self.extras["cache_creation_input_tokens"]),
                         (130000, 1500))

    def test_a_turns_answer_is_its_own_stop_call(self):
        c = self.of("chat")[3]
        self.assertEqual((c["text"], c["gen_ai.response.finish_reasons"], c["turn"], c["step"]),
                         ("Fixed: slugify now lowercases.", ["stop"], 0, 3))
        self.assertEqual(self.of("chat")[4]["turn"], 1)

    def test_every_tool_span_carries_step_and_turn(self):
        self.assertEqual([(t["gen_ai.tool.name"], t["step"], t["turn"]) for t in self.of("execute_tool")],
                         [("read_file", 0, 0), ("bash", 0, 0), ("edit_file", 1, 0), ("bash", 2, 0), ("bash", 4, 1)])

    def test_tool_outcomes(self):
        tools = self.of("execute_tool")
        self.assertEqual([t.get("tests_passed") for t in tools], [None, False, None, True, None])
        self.assertEqual((tools[4]["status"], tools[4]["timed_out_ms"]), ("error", 120000))
        self.assertTrue(self.summary.ran_tests_before_submit)
        self.assertTrue(self.summary.visible_pass)

    def test_edit_line_counts_and_patch_come_from_structured_patch(self):
        (edit,) = self.of("edit")
        self.assertEqual((edit["path"], edit["lines_added"], edit["lines_removed"], edit["line_counts_known"]),
                         ("/work/demo/slug.py", 1, 1, True))
        with open(os.path.join(self.tmp, "run", "patch.diff"), encoding="utf-8") as f:
            patch = f.read()
        self.assertEqual(patch, "--- a/work/demo/slug.py\n+++ b/work/demo/slug.py\n@@ -1,2 +1,2 @@\n"
                                " def slugify(s):\n-    return s\n+    return s.lower()\n")
        self.assertEqual((self.summary.lines_added, self.summary.lines_removed), (1, 1))
        self.assertEqual(self.summary.patch_bytes, len(patch.encode()))

    def test_cost_includes_cache_tokens(self):
        expected = sum(P.cost_usd("claude-opus-5", i, o, cache_read_tokens=r, cache_write_tokens=w)
                       for i, o, r, w in [(3, 120, 20000, 1500), (2, 80, 21000, 0), (2, 40, 21500, 0),
                                          (2, 30, 22000, 0), (2, 20, 22500, 0), (2, 10, 23000, 0)])
        self.assertAlmostEqual(self.summary.cost_usd, expected, places=9)
        self.assertGreater(self.summary.cost_usd, P.cost_usd("claude-opus-5", 13, 300))

    def test_start_span_is_configuration_and_end_span_carries_the_fingerprint(self):
        start, end = self.spans[0], self.spans[-1]
        self.assertEqual((start["span"], start["status"]), ("invoke_agent", "start"))
        self.assertNotIn("fingerprint", start["harness"])
        self.assertEqual(start["harness"]["policy"], "permissive")
        self.assertEqual((end["span"], end["status"]), ("invoke_agent", "end"))
        self.assertEqual(end["observed_fingerprint"]["observed_max_steps"], 5)
        self.assertEqual(end["unknown_record_types"], {})
        self.assertEqual(self.summary.harness_hash, start["harness"]["hash"])
        self.assertIs(end["cost_priced"], True)

    def test_only_three_files_are_written(self):
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "run"))),
                         ["ledger.jsonl", "patch.diff", "summary.json"])

    def test_span_count_and_exit_reason(self):
        self.assertEqual(len(self.spans), 14)
        self.assertEqual((self.summary.steps, self.summary.tool_calls, self.summary.edits), (6, 5, 1))
        self.assertEqual(self.summary.exit_reason, "no_action")
        self.assertEqual(self.summary.provider, "capture:claude_code")


class TestUnpricedModelIsFlagged(unittest.TestCase):
    def test_a_cost_from_the_fallback_price_is_marked_as_an_estimate(self):
        tmp = tempfile.mkdtemp(prefix="hl-writer-")
        self.addCleanup(shutil.rmtree, tmp, True)
        sess = claude_code.parse_file(FIX)
        sess.events = identity.dedup_events(sess.events)
        sess.model = "some-unpriced-model-x"
        harness = capture_harness(sess)
        task_id = C.derive_task_id(sess)
        summary, extras = write_run(sess, os.path.join(tmp, "run"), C.run_id_for(sess, task_id), task_id, harness)
        with open(os.path.join(tmp, "run", "ledger.jsonl"), encoding="utf-8") as f:
            end = [json.loads(line) for line in f if line.strip()][-1]
        self.assertIs(end["cost_priced"], False)
        self.assertGreater(summary.cost_usd, 0)


if __name__ == "__main__":
    unittest.main()
