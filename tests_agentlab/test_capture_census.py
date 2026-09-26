"""The discovery instruments must not leak what they are pointed at."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET = "MY-API-KEY-sk-abc123"
CLIENT = "ClientNameLtd"

CUR = os.path.join(LAB, "tools", "census_cursor.py")


@unittest.skipUnless(os.path.isfile(CUR), f"requires the full checkout: {CUR}")
class TestCursorCensusScript(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="hl-cur-census-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.t = os.path.join(self.d, "proj", "agent-transcripts", "abc")
        os.makedirs(self.t)

    def write(self, rows):
        with open(os.path.join(self.t, "abc.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def run_cur(self):
        return subprocess.run([sys.executable, CUR, self.d],
                              capture_output=True, text=True, timeout=180)

    def test_no_timestamp_anywhere_blocks_the_adapter(self):
        self.write([{"role": "assistant",
                     "message": {"content": [{"type": "tool_use", "name": "Read",
                                              "input": {"path": "a.py"}}]}},
                    {"type": "turn_ended", "status": "success"}])
        out = self.run_cur()
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("BLOCKED", out.stdout)
        self.assertIn("timestamp", out.stdout.lower())

    def test_a_timestamp_anywhere_clears_the_gate(self):
        self.write([{"role": "assistant", "createdAt": "2026-09-09T12:00:00.000Z",
                     "message": {"content": [{"type": "tool_use", "name": "Read",
                                              "input": {"path": "a.py"}}]}},
                    {"type": "turn_ended", "status": "success"}])
        out = self.run_cur()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("CLEARED", out.stdout)

    def test_a_timestamp_nested_below_the_content_block_also_clears(self):
        """The real corpus nests four levels deep inside input, so the scan must recurse."""
        self.write([{"role": "assistant",
                     "message": {"content": [{"type": "tool_use", "name": "CallDynamicTool",
                                              "input": {"arguments": {
                                                  "startedAt": "2026-09-09T12:00:00.000Z"}}}]}}])
        out = self.run_cur()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("CLEARED", out.stdout)

    def test_a_status_field_is_not_mistaken_for_a_timestamp(self):
        """`status` contains the substring `at`. A substring heuristic clears the gate on the
        real corpus, which is the exact silent failure this gate exists to prevent."""
        self.write([{"type": "turn_ended", "status": "success"},
                    {"type": "turn_ended", "status": "error", "error": "boom"}])
        out = self.run_cur()
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("BLOCKED", out.stdout)

    def test_a_timestamp_shaped_key_holding_a_non_timestamp_does_not_clear(self):
        """A key named `createdAt` whose value is not a time is not a timestamp."""
        self.write([{"role": "user", "createdAt": "yes",
                     "message": {"content": [{"type": "text", "text": "hi"}]}}])
        out = self.run_cur()
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("BLOCKED", out.stdout)

    def test_it_reports_whether_tool_use_blocks_carry_ids(self):
        self.write([{"role": "assistant", "createdAt": "2026-09-09T12:00:00.000Z",
                     "message": {"content": [{"type": "tool_use", "name": "Read",
                                              "input": {"path": "a.py"}}]}}])
        self.assertIn("has_call_ids=False", self.run_cur().stdout)

    def test_an_empty_corpus_blocks_rather_than_passing_vacuously(self):
        shutil.rmtree(self.t, True)
        out = self.run_cur()
        self.assertEqual(out.returncode, 1)
        self.assertIn("no files", out.stdout.lower() + out.stderr.lower())

    def test_the_gate_output_carries_no_values(self):
        self.write([{"role": "user", "createdAt": "2026-09-09T12:00:00.000Z",
                     "message": {"content": [{"type": "text", "text": SECRET}]}},
                    {"role": "assistant",
                     "message": {"content": [{"type": "tool_use", "name": "Shell",
                                              "input": {"command": SECRET,
                                                        "working_directory":
                                                            "/Users/someone/" + CLIENT}}]}}])
        out = self.run_cur()
        self.assertNotIn(SECRET, out.stdout)
        self.assertNotIn(CLIENT, out.stdout)


GEM = os.path.join(LAB, "tools", "census_gemini.py")


@unittest.skipUnless(os.path.isfile(GEM), f"requires the full checkout: {GEM}")
class TestGeminiCensusScript(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="hl-gem-census-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.chats = os.path.join(self.d, "proj", "chats")
        os.makedirs(self.chats)

    def write(self, name, rows):
        with open(os.path.join(self.chats, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def good_rows(self):
        return [
            {"kind": "main", "sessionId": "gs-1", "projectHash": "abc",
             "startTime": "2026-09-09T12:00:00.000Z", "lastUpdated": "2026-09-09T12:00:10.000Z"},
            {"type": "user", "id": "gm-1", "timestamp": "2026-09-09T12:00:01.000Z",
             "content": [{"text": "go"}]},
            {"type": "gemini", "id": "gm-2", "timestamp": "2026-09-09T12:00:04.000Z",
             "model": "gemini-3.8-flash", "content": "ok",
             "tokens": {"input": 10, "output": 2, "cached": 4, "total": 12},
             "toolCalls": [{"id": "gc-1", "name": "replace", "args": {}, "result": "done",
                            "status": "success", "timestamp": "2026-09-09T12:00:05.000Z"}]},
            {"$set": {"lastUpdated": "2026-09-09T12:00:10.000Z", "summary": "done"}},
        ]

    def run_gem(self):
        return subprocess.run([sys.executable, GEM, self.d],
                              capture_output=True, text=True, timeout=180)

    def test_a_metadata_only_set_envelope_passes(self):
        self.write("a.jsonl", self.good_rows())
        out = self.run_gem()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("Q6", out.stdout)
        self.assertIn("PASS", out.stdout)

    def test_a_set_envelope_that_replays_a_message_id_fails_the_gate(self):
        rows = self.good_rows()
        rows[-1]["$set"]["messages"] = [{"id": "gm-2", "type": "gemini",
                                         "timestamp": "2026-09-09T12:00:04.000Z",
                                         "content": "ok"}]
        self.write("a.jsonl", rows)
        out = self.run_gem()
        self.assertEqual(out.returncode, 1)
        self.assertIn("Q6", out.stdout)
        self.assertIn("FAIL", out.stdout)

    def test_an_unexpected_tool_status_fails_the_gate(self):
        rows = self.good_rows()
        rows[2]["toolCalls"][0]["status"] = "cancelled"
        self.write("a.jsonl", rows)
        out = self.run_gem()
        self.assertEqual(out.returncode, 1)
        self.assertIn("Q9", out.stdout)

    def test_a_missing_version_is_a_recorded_finding_not_a_failure(self):
        self.write("a.jsonl", self.good_rows())
        out = self.run_gem()
        self.assertEqual(out.returncode, 0)
        self.assertIn("NOTE Q7", out.stdout)

    def test_an_empty_corpus_fails_loudly(self):
        out = self.run_gem()
        self.assertEqual(out.returncode, 1)
        self.assertIn("no files", out.stdout.lower() + out.stderr.lower())

    def test_the_gate_output_carries_no_values(self):
        rows = self.good_rows()
        rows[1]["content"] = [{"text": SECRET}]
        self.write("a.jsonl", rows)
        self.assertNotIn(SECRET, self.run_gem().stdout)



# --------------------------------------------------------------- the generic census instrument
CENSUS = os.path.join(LAB, "tools", "capture_census.py")
QWEN = os.path.join(LAB, "tools", "census_qwen.py")


@unittest.skipUnless(os.path.isfile(CENSUS), f"requires the full checkout: {CENSUS}")
class TestCensusLeaksNothing(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="hl-census-")
        self.addCleanup(shutil.rmtree, self.d, True)
        with open(os.path.join(self.d, "secret.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"text": SECRET},
                                "cwd": f"/Users/someone/Projects/{CLIENT}"}) + "\n")

    def run_census(self, *args):
        out = subprocess.run([sys.executable, CENSUS, self.d, *args],
                             capture_output=True, text=True, timeout=120)
        return out

    def test_no_value_reaches_the_output(self):
        out = self.run_census()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn(SECRET, out.stdout)
        self.assertNotIn(CLIENT, out.stdout)
        self.assertNotIn("sk-abc", out.stdout)

    def test_a_path_used_as_a_dict_key_does_not_reach_the_output(self):
        """Codex keys its `changes` map by absolute path, so there the KEY is the data.

        Every other test in this class puts the secret in a value, which is exactly why this leak
        survived them: the walker printed the key verbatim as a dotted path component.
        """
        with open(os.path.join(self.d, "keyed.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "item_completed", "item": {
                "type": "FileChange",
                "changes": {f"/Users/someone/Projects/{CLIENT}/{SECRET}.py": {"add": 3}}}}) + "\n")
        out = self.run_census()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn(SECRET, out.stdout)
        self.assertNotIn(CLIENT, out.stdout)
        self.assertNotIn("/Users/", out.stdout)
        self.assertIn("item.changes.<key>.add", out.stdout)

    def test_key_paths_and_types_are_reported(self):
        out = self.run_census().stdout
        self.assertIn("message.text", out)
        self.assertIn("str", out)
        self.assertIn("record types", out)

    def test_a_missing_root_is_an_empty_census_not_a_crash(self):
        out = subprocess.run([sys.executable, CENSUS, os.path.join(self.d, "nope")],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("files=0", out.stdout)


@unittest.skipUnless(os.path.isfile(QWEN), f"requires the full checkout: {QWEN}")
class TestQwenCensusScript(unittest.TestCase):
    """The Qwen census is a gate, so its own pass/fail behaviour is tested offline against
    synthetic corpora rather than trusted against the operator's real one."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="hl-qwen-census-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.chats = os.path.join(self.d, "proj", "chats")
        os.makedirs(self.chats)

    def write(self, name, rows):
        with open(os.path.join(self.chats, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def good_rows(self):
        """The shape measured on the real corpus: a top-level `subtype`, a BOOLEAN `thought` flag
        on a text part, and `filePath` inside `resultDisplay`."""
        return [
            {"type": "system", "uuid": "s0", "sessionId": "s", "version": "0.23.3",
             "provenance": "system", "timestamp": "2026-09-09T11:59:59.000Z",
             "subtype": "ui_telemetry", "systemPayload": {"uiEvent": {}}},
            {"type": "user", "uuid": "u1", "sessionId": "s", "version": "0.23.3",
             "provenance": "real_user", "timestamp": "2026-09-09T12:00:00.000Z",
             "message": {"role": "user", "parts": [{"text": "go"}]}},
            {"type": "assistant", "uuid": "a1", "sessionId": "s", "version": "0.23.3",
             "provenance": "assistant_output", "timestamp": "2026-09-09T12:00:02.000Z",
             "model": "qwen3.8-max",
             "message": {"role": "model",
                         "parts": [{"text": "think", "thought": True},
                                   {"functionCall": {"id": "c1", "name": "edit", "args": {}}}]},
             "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2,
                               "cachedContentTokenCount": 4, "totalTokenCount": 12}},
            {"type": "tool_result", "uuid": "r1", "sessionId": "s", "version": "0.23.3",
             "provenance": "tool_result", "timestamp": "2026-09-09T12:00:03.000Z",
             "toolCallResult": {"callId": "c1", "executionStatus": "success",
                                "resultDisplay": {"filePath": "/w/a.py",
                                                  "diffStat": {"model_added_lines": 1,
                                                               "model_removed_lines": 1}}}},
        ]

    def run_qwen(self):
        return subprocess.run([sys.executable, QWEN, self.d],
                              capture_output=True, text=True, timeout=180)

    def test_a_conforming_corpus_exits_zero(self):
        self.write("a.jsonl", self.good_rows())
        out = self.run_qwen()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("Q1", out.stdout)
        self.assertIn("PASS", out.stdout)

    def test_a_system_row_whose_subtype_moved_into_the_payload_fails_the_gate(self):
        """The miss this gate exists to catch: an adapter reading systemPayload.subtype drops
        ~70% of every real chat file into unknown_record_types, silently, and every other question
        here still passes."""
        rows = self.good_rows()
        rows.append({"type": "system", "uuid": "s1", "sessionId": "s", "version": "0.23.3",
                     "provenance": "system", "timestamp": "2026-09-09T12:00:04.000Z",
                     "systemPayload": {"subtype": "ui_telemetry", "uiEvent": {}}})
        self.write("a.jsonl", rows)
        out = self.run_qwen()
        self.assertEqual(out.returncode, 1)
        self.assertIn("Q5", out.stdout)

    def test_missing_tool_call_ids_fail_the_gate(self):
        rows = self.good_rows()
        rows[2]["message"]["parts"][1]["functionCall"].pop("id")
        self.write("a.jsonl", rows)
        out = self.run_qwen()
        self.assertEqual(out.returncode, 1)
        self.assertIn("FAIL", out.stdout)
        self.assertIn("Q1", out.stdout)

    def test_missing_usage_fails_the_gate(self):
        rows = self.good_rows()
        rows[2].pop("usageMetadata")
        self.write("a.jsonl", rows)
        self.assertEqual(self.run_qwen().returncode, 1)

    def test_a_string_result_display_does_not_crash_the_gate(self):
        """`resultDisplay` is a STRING on 2,217 of 3,098 real results and a dict on 880. The
        first draft of this gate assumed dict and died with AttributeError on the real corpus."""
        rows = self.good_rows()
        rows[3]["toolCallResult"]["resultDisplay"] = "rendered output"
        rows.append(self.good_rows()[3])
        self.write("a.jsonl", rows)
        out = self.run_qwen()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_an_empty_corpus_fails_loudly_rather_than_passing_vacuously(self):
        out = self.run_qwen()
        self.assertEqual(out.returncode, 1)
        self.assertIn("no files", out.stdout.lower() + out.stderr.lower())

    def test_the_gate_output_carries_no_values(self):
        rows = self.good_rows()
        rows[1]["message"]["parts"][0]["text"] = SECRET
        self.write("a.jsonl", rows)
        out = self.run_qwen()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)   # it ran, and still leaked nothing
        self.assertNotIn(SECRET, out.stdout)


if __name__ == "__main__":
    unittest.main()
