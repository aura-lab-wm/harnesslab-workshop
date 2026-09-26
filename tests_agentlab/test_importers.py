"""Tests for harnesslab/backend/importers: detect + import + fingerprint + unknown outcomes.

Fixtures live in tests_agentlab/fixtures/importers/:

  claude_code_session.jsonl   hand-written, but *validated against the canonical decoder*:
                              `trajectory.normalize_transcript(source="claude-code", ...)` from the
                              `agent-trajectory` package accepts it and emits exactly the
                              `sidechain_record_dropped` diagnostic it is designed to trigger.
  codex_rollout.jsonl         likewise, emitting `injected_context_dropped`.
  trajectory_v1.json          Letta Trajectory v1, the schema's own bare-array shape.
  openhands_events.json       classic OpenHands action/observation event array.
  openhands_output.jsonl      SWE-bench output.jsonl: two rows, one resolved, one not.
  swe_agent/...traj           native SWE-agent .traj + a sibling results.json verdict.
  tiny_swe.eval               a REAL Inspect AI log, produced in-container by running a two-sample
                              task against the `mockllm/model` provider (see NOTES_importers.md).

Everything here is offline and needs no third-party package except `inspect_ai` for the one
Inspect test, which skips if it is not installed.
"""
import json
import os
import shutil
import sys
import tempfile, uuid
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import importers as I                      # noqa: E402
from harnesslab.backend.importers import common as C               # noqa: E402
from harnesslab.backend.importers import (claude_code, codex, inspect_log,   # noqa: E402
                                         openhands, swe_agent, trajectory_fmt)

FIX = os.path.join(LAB, "tests_agentlab", "fixtures", "importers")
CC = os.path.join(FIX, "claude_code_session.jsonl")
CX = os.path.join(FIX, "codex_rollout.jsonl")
TJ = os.path.join(FIX, "trajectory_v1.json")
OH = os.path.join(FIX, "openhands_events.json")
OHJ = os.path.join(FIX, "openhands_output.jsonl")
SA = os.path.join(FIX, "swe_agent")
EV = os.path.join(FIX, "tiny_swe.eval")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="harnesslab-imp-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def imp(self, path, name="d", **kw):
        return I.import_path(path, name, runs_root=self.root, **kw)

    def index(self, name="d"):
        p = os.path.join(self.root, name, "index.jsonl")
        with open(p) as f:
            return [json.loads(l) for l in f if l.strip()]

    def ledger(self, run_id, name="d"):
        with open(os.path.join(self.root, name, run_id, "ledger.jsonl")) as f:
            return [json.loads(l) for l in f if l.strip()]


# --------------------------------------------------------------------------- detect
class TestDetect(Tmp):
    def test_each_fixture_detects_as_its_own_source(self):
        cases = [(CC, "claude_code"), (CX, "codex"), (TJ, "trajectory"),
                 (OH, "openhands"), (OHJ, "openhands"),
                 (os.path.join(SA, "pandas-dev__pandas-51234", "pandas-dev__pandas-51234.traj"), "swe_agent")]
        for path, want in cases:
            with self.subTest(path=os.path.basename(path)):
                self.assertEqual(I.detect(path), want)

    def test_inspect_eval_detects_by_zip_header_without_the_package(self):
        self.assertEqual(I.detect(EV), "inspect")

    def test_detect_returns_empty_for_an_unrelated_json(self):
        p = os.path.join(self.root, "nope.json")
        with open(p, "w") as f:
            json.dump({"hello": "world", "rows": [1, 2, 3]}, f)
        self.assertEqual(I.detect(p), "")

    def test_detect_returns_empty_for_a_missing_path(self):
        self.assertEqual(I.detect(os.path.join(self.root, "does-not-exist")), "")

    def test_detect_walks_a_directory(self):
        self.assertEqual(I.detect(SA), "swe_agent")

    def test_detect_detail_reports_candidates_and_session_count(self):
        d = I.detect_detail(CC)
        self.assertEqual(d["source"], "claude_code")
        self.assertEqual(d["sessions"], 1)
        self.assertTrue(any(c["source"] == "claude_code" for c in d["candidates"]))
        self.assertEqual([c for c in d["candidates"] if c["source"] == "claude_code"][0]["files"], 1)

    def test_detect_detail_scores_a_directory_per_adapter(self):
        """A session store is a directory, so the directory case cannot be the one that reports
        a winner with nothing behind it: the CLI's --detect and the import screen both show the
        numbers, and a bare name is not reviewable."""
        d = I.detect_detail(SA)
        self.assertEqual(d["source"], "swe_agent")
        self.assertTrue(d["is_dir"])
        self.assertEqual([c["source"] for c in d["candidates"]][:1], ["swe_agent"])
        c = d["candidates"][0]
        self.assertGreaterEqual(c["confidence"], 0.5)
        self.assertLessEqual(c["confidence"], 1.0)   # a mean over files, never a growing sum
        self.assertEqual(c["files"], 1)

    def test_detect_detail_ranks_a_mixed_directory_by_weight_not_by_file_count(self):
        """The fixtures directory holds one file of most formats and two openhands files; the
        winner the candidates lead with must be the one detect() actually returns."""
        d = I.detect_detail(FIX)
        self.assertTrue(d["candidates"], "a directory of known traces scored nothing")
        self.assertEqual(d["candidates"][0]["source"], d["source"])
        self.assertEqual(d["source"], I.detect(FIX))
        self.assertEqual(sum(c["files"] for c in d["candidates"]),
                         len([c for c in d["candidates"] for _ in range(c["files"])]))

    def test_detect_detail_on_a_directory_of_nothing_says_so(self):
        p = os.path.join(self.root, "empty_dir")
        os.makedirs(p)
        with open(os.path.join(p, "notes.json"), "w") as f:
            json.dump({"hello": "world"}, f)
        d = I.detect_detail(p)
        self.assertEqual(d["source"], "")
        self.assertEqual(d["candidates"], [])

    def test_runs_root_matches_the_platform(self):
        """Regression: importers/ is one directory deeper than backend/, so the LAB_ROOT walk
        needs four dirname() calls, not three. Getting this wrong writes runs into
        harnesslab/data/runs where every /api/results/<dir> endpoint 404s."""
        from harnesslab.backend import metrics as M
        self.assertEqual(I.RUNS_ROOT, M.RUNS_ROOT)
        # LAB_ROOT must be the lab checkout (the one holding tasks/, harnesses/ and data/),
        # never the harnesslab package directory. tasks/ and harnesses/ are the sentinels:
        # the engine used to sit at the root as agentlab/ and no longer does.
        for d in ("tasks", "harnesses", "data"):
            self.assertTrue(os.path.isdir(os.path.join(I.LAB_ROOT, d)), f"LAB_ROOT missing {d}/")
        # the package must sit *inside* the checkout; if LAB_ROOT ever walks up one level too
        # few it lands on the package itself and runs get written where the API cannot see them
        self.assertTrue(os.path.isdir(os.path.join(I.LAB_ROOT, "harnesslab", "backend")))

    def test_sources_registry_is_complete(self):
        names = {s["name"] for s in I.list_sources()}
        self.assertEqual(names, {"claude_code", "codex", "trajectory", "inspect", "openhands", "swe_agent",
                                 "gemini_cli", "qwen_code", "cursor"})
        for s in I.list_sources():
            self.assertTrue(s["description"] and s["patterns"])

    def test_the_import_screen_names_the_same_watched_sources_the_spine_does(self):
        """Two roads lead in -- the capture watcher follows five CLIs by itself, and import is how
        everything else arrives -- and the import screen says so in its copy. That sentence is only
        true while the two lists agree, so it is checked rather than remembered: if the spine starts
        or stops watching a source, this fails until frontend/src/method/importing.js is updated
        with it."""
        import re
        from harnesslab.capture.adapters import BY_NAME
        js = os.path.join(LAB, "harnesslab", "frontend", "src", "method", "importing.js")
        with open(js, encoding="utf-8") as f:
            src = f.read()
        block = re.search(r"export const WATCHED\s*=\s*\[(.*?)\]", src, re.S)
        self.assertIsNotNone(block, "importing.js no longer declares WATCHED")
        watched = set(re.findall(r"'([a-z_]+)'", block.group(1)))
        self.assertEqual(watched, set(BY_NAME), "the import screen and the capture spine disagree "
                                                "about which sources are watched automatically")
        every = {s["name"] for s in I.list_sources()}
        self.assertTrue(watched < every, "a watched source that no adapter can import")
        self.assertEqual(every - watched, {"inspect", "swe_agent", "trajectory", "openhands"})


# --------------------------------------------------------------------------- claude code
class TestClaudeCode(Tmp):
    def test_import_maps_tools_and_records_the_ledger(self):
        r = self.imp(CC)
        self.assertEqual((r["imported"], r["skipped"], r["errors"]), (1, 0, []))
        self.assertEqual(r["harness_ids"], ["claude-code@1.0.x"])
        row = self.index()[0]
        spans = self.ledger(row["run_id"])
        tools = [s["gen_ai.tool.name"] for s in spans if s["span"] == "execute_tool"]
        self.assertEqual(tools, ["read_file", "edit_file", "bash", "bash"])
        # Read -> read_file keeps the path; Edit -> edit_file keeps old/new
        edit = next(s for s in spans if s["span"] == "execute_tool" and s["gen_ai.tool.name"] == "edit_file")
        self.assertEqual(edit["args"]["path"], "/work/calcrepo/calc.py")
        self.assertEqual(edit["args"]["old"], "return a - b")
        self.assertEqual(edit["native_tool"], "Edit")
        self.assertTrue(any(s["span"] == "edit" for s in spans))

    def test_sidechain_rows_are_dropped(self):
        self.imp(CC)
        spans = self.ledger(self.index()[0]["run_id"])
        self.assertNotIn("Grep", [s.get("native_tool") for s in spans])

    def test_tests_passed_inferred_from_bash_output(self):
        self.imp(CC)
        spans = self.ledger(self.index()[0]["run_id"])
        pytest_span = next(s for s in spans if s["span"] == "execute_tool"
                           and s["args"].get("command", "").startswith("python -m pytest"))
        self.assertIs(pytest_span["tests_passed"], True)
        # the `rm -rf` bash call is not a test run, so it gets no verdict at all
        rm = next(s for s in spans if s["span"] == "execute_tool" and "rm -rf" in s["args"].get("command", ""))
        self.assertNotIn("tests_passed", rm)

    def test_destructive_command_becomes_a_boundary_event(self):
        self.imp(CC)
        row = self.index()[0]
        self.assertEqual(row["boundary_events"], 1)
        self.assertEqual(row["boundary_kinds"], ["rm_rf"])

    def test_outcome_is_unknown_without_a_verdict(self):
        r = self.imp(CC)
        row = self.index()[0]
        self.assertIsNone(row["hidden_pass"])
        self.assertIsNone(row["strong_pass"])
        self.assertIs(row["visible_pass"], True)          # last test run looked green (heuristic)
        self.assertEqual((r["outcomes_known"], r["outcomes_unknown"]), (0, 1))
        spans = self.ledger(row["run_id"])
        self.assertFalse([s for s in spans if s["span"] == "grade"])   # no verdict -> no grade span

    def test_a_sidecar_results_json_supplies_a_verdict(self):
        d = os.path.join(self.root, "cc")
        os.makedirs(d)
        shutil.copy(CC, os.path.join(d, "sess-abc.jsonl"))
        with open(os.path.join(d, "results.json"), "w") as f:
            json.dump({"resolved": ["sess-abc"]}, f)
        self.imp(os.path.join(d, "sess-abc.jsonl"), "withverdict")
        row = self.index("withverdict")[0]
        self.assertIs(row["hidden_pass"], True)
        spans = self.ledger(row["run_id"], "withverdict")
        grade = next(s for s in spans if s["span"] == "grade")
        self.assertIn("sidecar", grade["source"])

    def test_task_id_is_derived_and_stable(self):
        a = self.imp(CC, "a")["tasks"]
        b = self.imp(CC, "b")["tasks"]
        self.assertEqual(a, b)
        self.assertTrue(a[0].startswith("calcrepo-"))
        self.assertRegex(a[0], r"-[0-9a-f]{8}$")

    def test_import_is_idempotent(self):
        self.imp(CC)
        again = self.imp(CC)
        self.assertEqual((again["imported"], again["skipped"]), (0, 1))
        self.assertEqual(len(self.index()), 1)

    def test_model_override(self):
        self.imp(CC, model_override="openai/gpt-5")
        self.assertEqual(self.index()[0]["model"], "openai/gpt-5")

    def test_custom_task_id_fn(self):
        self.imp(CC, task_id_fn=lambda s: "t01_slugify")
        self.assertEqual(self.index()[0]["task_id"], "t01_slugify")


# --------------------------------------------------------------------------- codex
class TestCodex(Tmp):
    def test_import_and_schema_variant_tolerance(self):
        r = self.imp(CX)
        self.assertEqual((r["imported"], r["errors"]), (1, []))
        self.assertEqual(r["harness_ids"], ["codex@0.4.x"])
        spans = self.ledger(self.index()[0]["run_id"])
        tools = [s["gen_ai.tool.name"] for s in spans if s["span"] == "execute_tool"]
        self.assertEqual(tools, ["bash", "edit_file", "bash"])   # shell, apply_patch, shell
        # function_call_output may be a dict with metadata.exit_code; the text must survive
        first = next(s for s in spans if s["span"] == "execute_tool")
        self.assertIn("def add", first["result_preview"])

    def test_injected_context_user_turn_is_dropped(self):
        self.imp(CX)
        with open(os.path.join(self.root, "d", self.index()[0]["run_id"], "messages.json")) as f:
            msgs = json.load(f)
        users = [m["content"] for m in msgs if m["role"] == "user"]
        self.assertEqual(len(users), 1)
        self.assertNotIn("<environment_context>", users[0])

    def test_system_instructions_are_hashed_not_stored(self):
        self.imp(CX)
        spans = self.ledger(self.index()[0]["run_id"])
        h = spans[0]["harness"]
        self.assertEqual(h["system_prompt"], "")
        self.assertEqual(h["fingerprint"]["system_prompt_chars"], 48)
        self.assertRegex(h["fingerprint"]["system_prompt_sha256"], r"^[0-9a-f]{64}$")
        with open(os.path.join(self.root, "d", self.index()[0]["run_id"], "messages.json")) as f:
            blob = f.read()
        self.assertNotIn("You are Codex", blob)

    def test_arguments_may_be_a_dict_instead_of_a_json_string(self):
        p = os.path.join(self.root, "variant.jsonl")
        rows = [
            {"type": "session_meta", "payload": {"id": "s1", "cwd": "/x", "cli_version": "0.9.0"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "hello"}]}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
                                                  "arguments": {"command": "pytest -q"}}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
                                                  "output": "3 passed"}},
        ]
        with open(p, "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows))
        self.assertEqual(I.detect(p), "codex")
        self.imp(p, "v")
        spans = self.ledger(self.index("v")[0]["run_id"], "v")
        t = next(s for s in spans if s["span"] == "execute_tool")
        self.assertEqual(t["args"]["command"], "pytest -q")
        self.assertIs(t["tests_passed"], True)


# --------------------------------------------------------------------------- trajectory v1
class TestTrajectoryFormat(Tmp):
    def test_bare_array_shape(self):
        r = self.imp(TJ)
        self.assertEqual((r["imported"], r["errors"]), (1, []))
        self.assertEqual(r["harness_ids"], ["letta-code"])         # meta.source becomes the agent
        row = self.index()[0]
        self.assertEqual(row["model"], "gpt-5")
        spans = self.ledger(row["run_id"])
        self.assertEqual([s["gen_ai.tool.name"] for s in spans if s["span"] == "execute_tool"],
                         ["read_file", "edit_file", "bash"])

    def test_stringified_args_are_decoded(self):
        self.imp(TJ)
        spans = self.ledger(self.index()[0]["run_id"])
        e = next(s for s in spans if s["span"] == "execute_tool" and s["gen_ai.tool.name"] == "edit_file")
        self.assertEqual(e["args"]["path"], "widget.py")
        self.assertEqual(e["args"]["new"], "return self.title")

    def test_ok_false_marks_the_tool_call_as_an_error_and_a_failing_test(self):
        self.imp(TJ)
        spans = self.ledger(self.index()[0]["run_id"])
        b = next(s for s in spans if s["span"] == "execute_tool" and s["gen_ai.tool.name"] == "bash")
        self.assertEqual(b["status"], "error")
        self.assertIs(b["tests_passed"], False)
        self.assertIs(self.index()[0]["visible_pass"], False)

    def test_records_envelope_and_jsonl_are_both_accepted(self):
        with open(TJ) as f:
            recs = json.load(f)
        env = os.path.join(self.root, "env.json")
        with open(env, "w") as f:
            json.dump({"records": recs, "diagnostics": []}, f)
        lines = os.path.join(self.root, "lines.jsonl")
        with open(lines, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs))
        for p in (env, lines):
            with self.subTest(p=os.path.basename(p)):
                self.assertEqual(I.detect(p), "trajectory")
                self.assertEqual(trajectory_fmt.parse_file(p).model, "gpt-5")

    def test_reference_runtime_bridge_reports_a_clear_error_when_unavailable(self):
        with self.assertRaises(RuntimeError):
            trajectory_fmt.normalize_via_package(CC, "not-a-source")


# --------------------------------------------------------------------------- openhands
class TestOpenHands(Tmp):
    def test_classic_event_array(self):
        r = self.imp(OH)
        self.assertEqual((r["imported"], r["errors"]), (1, []))
        spans = self.ledger(self.index()[0]["run_id"])
        self.assertEqual([s["gen_ai.tool.name"] for s in spans if s["span"] == "execute_tool"],
                         ["bash", "edit_file", "bash", "submit"])
        self.assertEqual(self.index()[0]["exit_reason"], "submitted")
        self.assertIsNone(self.index()[0]["hidden_pass"])       # a bare trajectory has no verdict

    def test_observations_link_to_their_action_by_cause(self):
        self.imp(OH)
        spans = self.ledger(self.index()[0]["run_id"])
        grep = next(s for s in spans if s["span"] == "execute_tool" and "grep" in s["args"].get("command", ""))
        self.assertIn("concat.py:120", grep["result_preview"])

    def test_swebench_output_jsonl_keeps_instance_ids_and_report_verdicts(self):
        r = self.imp(OHJ)
        self.assertEqual(r["imported"], 2)
        self.assertEqual((r["outcomes_known"], r["outcomes_unknown"]), (2, 0))
        rows = {x["task_id"]: x for x in self.index()}
        self.assertEqual(set(rows), {"django__django-12345", "astropy__astropy-7746"})
        self.assertIs(rows["django__django-12345"]["hidden_pass"], True)
        self.assertIs(rows["astropy__astropy-7746"]["hidden_pass"], False)
        self.assertEqual(rows["django__django-12345"]["harness_id"], "openhands@0.30.x")
        self.assertEqual(rows["django__django-12345"]["model"], "anthropic/claude-sonnet-4-5")
        self.assertGreater(rows["django__django-12345"]["patch_bytes"], 0)
        grade = next(s for s in self.ledger(rows["django__django-12345"]["run_id"]) if s["span"] == "grade")
        self.assertIn("resolved", grade["source"])

    def test_v1_event_export_shape(self):
        p = os.path.join(self.root, "v1.json")
        events = [
            {"id": "e1", "kind": "MessageEvent", "source": "user", "timestamp": "2026-08-01T00:00:00",
             "llm_message": {"role": "user", "content": [{"type": "text", "text": "fix the bug"}]}},
            {"id": "e2", "kind": "ActionEvent", "source": "agent", "timestamp": "2026-08-01T00:00:01",
             "thought": [{"type": "text", "text": "run the suite"}], "tool_name": "execute_bash",
             "tool_call_id": "tc1", "action": {"kind": "ExecuteBashAction", "command": "pytest -q"}},
            {"id": "e3", "kind": "ObservationEvent", "source": "environment", "timestamp": "2026-08-01T00:00:05",
             "tool_call_id": "tc1", "observation": {"kind": "ExecuteBashObservation",
                                                    "content": "1 failed, 1 passed", "is_error": True}},
        ]
        with open(p, "w") as f:
            json.dump(events, f)
        self.assertEqual(I.detect(p), "openhands")
        self.imp(p, "v1")
        spans = self.ledger(self.index("v1")[0]["run_id"], "v1")
        t = next(s for s in spans if s["span"] == "execute_tool")
        self.assertEqual(t["gen_ai.tool.name"], "bash")
        self.assertEqual(t["status"], "error")
        self.assertIs(t["tests_passed"], False)


# --------------------------------------------------------------------------- swe-agent
class TestSweAgent(Tmp):
    def test_traj_import_reuses_the_command_language(self):
        r = self.imp(SA)
        self.assertEqual((r["imported"], r["errors"]), (1, []))
        row = self.index()[0]
        self.assertEqual(row["task_id"], "pandas-dev__pandas-51234")     # instance id kept verbatim
        self.assertEqual(row["harness_id"], "swe-agent@1.0.x")
        spans = self.ledger(row["run_id"])
        self.assertEqual([s["gen_ai.tool.name"] for s in spans if s["span"] == "execute_tool"],
                         ["read_file", "list_files", "edit_file", "bash", "submit"])

    def test_open_file_state_gives_the_edited_path(self):
        self.imp(SA)
        spans = self.ledger(self.index()[0]["run_id"])
        e = next(s for s in spans if s["span"] == "execute_tool" and s["gen_ai.tool.name"] == "edit_file")
        self.assertEqual(e["args"]["path"], "pandas/core/frame.py")

    def test_exit_status_is_not_a_verdict_but_the_sidecar_is(self):
        self.imp(SA)
        row = self.index()[0]
        self.assertEqual(row["exit_reason"], "submitted")
        self.assertIs(row["hidden_pass"], True)                          # from the sibling results.json
        self.assertGreater(row["patch_bytes"], 0)

    def test_a_traj_without_any_verdict_stays_unknown(self):
        d = os.path.join(self.root, "bare")
        os.makedirs(d)
        src = os.path.join(SA, "pandas-dev__pandas-51234", "pandas-dev__pandas-51234.traj")
        shutil.copy(src, os.path.join(d, "pandas-dev__pandas-51234.traj"))
        r = self.imp(os.path.join(d, "pandas-dev__pandas-51234.traj"), "bare")
        self.assertEqual((r["outcomes_known"], r["outcomes_unknown"]), (0, 1))
        self.assertIsNone(self.index("bare")[0]["hidden_pass"])


# --------------------------------------------------------------------------- inspect
class TestInspectLog(Tmp):
    def setUp(self):
        super().setUp()
        try:
            import inspect_ai  # noqa: F401
        except ImportError:
            self.skipTest("inspect_ai not installed (pip install --break-system-packages inspect_ai)")

    def test_real_eval_log_becomes_one_run_per_sample_with_score_verdicts(self):
        r = self.imp(EV)
        self.assertEqual((r["imported"], r["errors"]), (2, []))
        self.assertEqual(r["harness_ids"], ["inspect:use_tools+generate"])
        self.assertEqual((r["outcomes_known"], r["outcomes_unknown"]), (2, 0))
        rows = {x["task_id"]: x for x in self.index()}
        self.assertEqual(set(rows), {"astropy__astropy-12907", "django__django-11099"})
        self.assertIs(rows["astropy__astropy-12907"]["hidden_pass"], True)     # score "C"
        self.assertIs(rows["django__django-11099"]["hidden_pass"], False)      # score "I"
        for row in rows.values():
            self.assertEqual(row["model"], "mockllm/model")
            grade = next(s for s in self.ledger(row["run_id"]) if s["span"] == "grade")
            self.assertIn("includes", grade["source"])

    def test_tool_calls_and_results_survive(self):
        self.imp(EV)
        row = next(x for x in self.index() if x["task_id"] == "astropy__astropy-12907")
        spans = self.ledger(row["run_id"])
        tools = [s for s in spans if s["span"] == "execute_tool"]
        self.assertEqual([t["gen_ai.tool.name"] for t in tools], ["bash", "bash"])
        self.assertEqual(tools[0]["args"]["command"], "python -m pytest -q")
        self.assertIs(tools[0]["tests_passed"], True)
        self.assertIs(row["visible_pass"], True)

    def test_declared_tools_and_message_limit_come_from_the_plan(self):
        self.imp(EV)
        h = self.ledger(self.index()[0]["run_id"])[0]["harness"]
        self.assertEqual(h["fingerprint"]["declared_tools"], ["bash"])
        self.assertEqual(h["max_steps"], 8)                     # config.message_limit

    def test_score_value_mapping(self):
        f = inspect_log._score_to_bool
        self.assertIs(f("C"), True)
        self.assertIs(f("I"), False)
        self.assertIs(f("P"), False)
        self.assertIs(f(True), True)
        self.assertIs(f(1.0), True)
        self.assertIs(f(0.2), False)
        self.assertIsNone(f("???"))
        self.assertIsNone(f(None))


# --------------------------------------------------------------------------- fingerprint
class TestFingerprint(Tmp):
    def fp(self, path):
        sess = next(iter(I._sessions_for(I.detect(path), path)))
        return I.session_harness(sess)

    def test_claude_code_fingerprint(self):
        h = self.fp(CC)
        fp = h["fingerprint"]
        self.assertEqual(fp["agent"], "claude-code")
        self.assertEqual(fp["agent_version"], "1.0.60")
        self.assertEqual(fp["observed_tools"], ["Bash", "Edit", "Read"])
        self.assertEqual(fp["lab_tools"], ["read_file", "edit_file", "bash"])
        self.assertEqual(fp["observed_max_steps"], 4)
        self.assertTrue(fp["reasoning_exposed"])                       # a `thinking` block was present
        self.assertEqual(fp["destructive_executed"], ["rm_rf"])
        self.assertEqual(fp["policy_hint"], "permissive")              # because we *saw* rm -rf run
        self.assertEqual(h["id"], "claude-code@1.0.x")
        self.assertRegex(h["hash"], r"^[0-9a-f]{12}$")

    def test_no_destructive_command_means_policy_unknown_not_strict(self):
        self.assertEqual(self.fp(CX)["fingerprint"]["policy_hint"], "unknown")
        self.assertEqual(self.fp(CX)["policy"], "unknown")

    def test_system_prompt_is_hashed_and_measured_never_stored(self):
        h = self.fp(CX)
        with open(CX) as f:
            prompt = json.loads(f.readline())["payload"]["instructions"]
        self.assertEqual(h["fingerprint"]["system_prompt_sha256"], C.sha256_hex(prompt))
        self.assertEqual(h["fingerprint"]["system_prompt_chars"], len(prompt))
        self.assertNotIn(prompt, json.dumps(h))

    def test_hash_is_a_function_of_the_fingerprint_only(self):
        h = self.fp(CC)
        self.assertEqual(h["hash"], C.sha12(h["fingerprint"]))
        self.assertNotEqual(self.fp(CC)["hash"], self.fp(CX)["hash"])

    def test_hash_is_stable_across_reimports(self):
        self.assertEqual(self.fp(CC)["hash"], self.fp(CC)["hash"])

    def test_harness_dict_has_the_HarnessConfig_shape_with_honest_unknowns(self):
        from harnesslab.core.harness import HarnessConfig
        from dataclasses import fields
        h = self.fp(CC)
        known = {f.name for f in fields(HarnessConfig)}
        self.assertTrue(known <= set(h), f"missing: {known - set(h)}")
        self.assertEqual(h["max_total_tokens"], 0)          # unknown -> 0, not invented
        self.assertEqual(h["context_window"], 0)
        self.assertEqual(h["max_tokens_per_call"], 0)
        self.assertIs(h["include_file_listing"], False)
        self.assertEqual(h["system_prompt"], "")
        self.assertEqual(h["sentinel"], {})
        self.assertIn("imported from claude_code", h["notes"])
        # extras beyond the dataclass are additive and must not break HarnessConfig(**...)
        HarnessConfig(**{k: v for k, v in h.items() if k in known})

    def test_harness_id_shapes(self):
        S = I.Session
        self.assertEqual(I.harness_id_for(S("claude_code", "s", agent="claude-code", agent_version="1.0.60")),
                         "claude-code@1.0.x")
        self.assertEqual(I.harness_id_for(S("codex", "s", agent="codex", agent_version="0.4.12")), "codex@0.4.x")
        self.assertEqual(I.harness_id_for(S("openhands", "s", agent="openhands", agent_version="")), "openhands")
        self.assertEqual(I.harness_id_for(S("swe_agent", "s", agent="swe-agent", agent_version="")), "swe-agent")
        self.assertEqual(I.harness_id_for(S("inspect", "s", agent="inspect", extra={"solver": "react"})),
                         "inspect:react")

    def test_context_truncation_hints_are_observed(self):
        ev = [I.Event("tool_call", name="Bash", args={"command": "ls"}),
              I.Event("tool_result", text="a\n<response clipped>\nb")]
        fp = I.fingerprint(ev, {"source": "claude_code", "agent": "claude-code"})
        self.assertEqual(fp["context_hints"], ["output_truncated"])

    def test_every_invoke_agent_start_span_carries_the_full_harness_dict(self):
        for path in (CC, CX, TJ, OH, OHJ, SA):
            with self.subTest(path=os.path.basename(path)):
                name = "h" + str(abs(hash(path)) % 10000)
                self.imp(path, name)
                for row in self.index(name):
                    span = self.ledger(row["run_id"], name)[0]
                    self.assertEqual(span["span"], "invoke_agent")
                    self.assertEqual(span["status"], "start")
                    self.assertEqual(span["harness"]["id"], row["harness_id"])
                    self.assertIn("fingerprint", span["harness"])
                    self.assertIn("hash", span["harness"])


# --------------------------------------------------------------------------- unit-level helpers
class TestHelpers(unittest.TestCase):
    def test_tool_mapping_covers_every_named_harness(self):
        cases = [
            ("Bash", {"command": "ls"}, "bash"), ("Read", {"file_path": "a.py"}, "read_file"),
            ("Edit", {"file_path": "a.py"}, "edit_file"), ("MultiEdit", {"file_path": "a.py"}, "edit_file"),
            ("Write", {"file_path": "a.py", "content": "x"}, "write_file"),
            ("Grep", {"pattern": "x"}, "list_files"), ("Glob", {"pattern": "*.py"}, "list_files"),
            ("LS", {"path": "."}, "list_files"), ("Task", {"prompt": "go"}, "bash"),
            ("shell", {"command": "ls"}, "bash"), ("apply_patch", {"path": "a.py"}, "edit_file"),
            ("execute_bash", {"command": "ls"}, "bash"), ("finish", {}, "submit"),
            ("str_replace_editor", {"path": "a.py"}, "edit_file"), ("submit", {}, "submit"),
            ("open", {"path": "a.py"}, "read_file"), ("search_dir", {"pattern": "x"}, "list_files"),
        ]
        for name, args, want in cases:
            with self.subTest(tool=name):
                self.assertEqual(C.map_tool(name, args)[0], want)

    def test_unknown_tools_degrade_to_bash_and_keep_their_native_name(self):
        tool, args = C.map_tool("mcp__linear__create_issue", {"title": "x"})
        self.assertEqual(tool, "bash")
        self.assertIn("mcp__linear__create_issue", args["command"])

    def test_tests_passed_only_fires_on_a_test_command(self):
        self.assertIsNone(C.tests_passed_for("bash", {"command": "ls -la"}, "2 passed"))
        self.assertIs(C.tests_passed_for("bash", {"command": "pytest -q"}, "2 passed in 0.1s"), True)
        self.assertIs(C.tests_passed_for("bash", {"command": "pytest -q"}, "1 failed, 2 passed"), False)
        self.assertIsNone(C.tests_passed_for("read_file", {"path": "x"}, "2 passed"))
        self.assertIs(C.tests_passed_for("bash", {"command": "pytest -q"}, "", ok=True), True)

    def test_destructive_detection(self):
        self.assertEqual(C.destructive_kinds("rm -rf /tmp/x"), ["rm_rf"])
        self.assertIn("git_destructive", C.destructive_kinds("git reset --hard HEAD~3"))
        self.assertEqual(C.destructive_kinds("ls -la"), [])

    def test_swebench_ids_are_kept_verbatim(self):
        s = I.Session("swe_agent", "x", task_id_hint="django__django-12345")
        self.assertEqual(C.derive_task_id(s), "django__django-12345")

    def test_derived_task_ids_are_deterministic_and_prompt_sensitive(self):
        a = I.Session("claude_code", "x", cwd="/repo/foo",
                      events=[I.Event("user", text="fix the parser")])
        b = I.Session("claude_code", "y", cwd="/repo/foo",
                      events=[I.Event("user", text="fix the parser")])
        c = I.Session("claude_code", "z", cwd="/repo/foo",
                      events=[I.Event("user", text="fix the printer")])
        self.assertEqual(C.derive_task_id(a), C.derive_task_id(b))
        self.assertNotEqual(C.derive_task_id(a), C.derive_task_id(c))


# --------------------------------------------------------------------------- downstream tolerance
class TestDownstreamToleratesUnknownOutcomes(Tmp):
    """Imported runs frequently have hidden_pass = None. Nothing downstream may crash."""

    def setUp(self):
        super().setUp()
        for p in (CC, CX, TJ, OH, OHJ, SA):
            self.imp(p, "mix")
        self.rows = self.index("mix")

    def test_a_mixed_directory_imports_cleanly(self):
        self.assertEqual(len(self.rows), 7)
        self.assertTrue(any(r["hidden_pass"] is None for r in self.rows))
        self.assertTrue(any(r["hidden_pass"] is not None for r in self.rows))

    def test_analysis_functions_do_not_crash_on_none(self):
        from harnesslab.core.analysis import (aggregate, task_table, summarize, flip_rate,
                                       exit_reasons, boundary_rate, tokens_per_solve)
        self.assertIn("pass@1", aggregate(self.rows))
        self.assertTrue(task_table(self.rows))
        self.assertTrue(summarize(self.rows, "mixed"))
        flip_rate(self.rows)
        self.assertTrue(exit_reasons(self.rows))
        self.assertIn("any", boundary_rate(self.rows))
        tokens_per_solve(self.rows)

    def test_none_is_folded_into_not_passed_by_analysis(self):
        """Documented caveat rather than a bug: harnesslab.core.analysis uses bool(r.get(outcome)),
        so an unknown outcome reads as a failure in pass@1. The UI says so; we do not edit
        analysis.py or metrics.py from this stream."""
        from harnesslab.core.analysis import aggregate
        unknown = [r for r in self.rows if r["hidden_pass"] is None]
        self.assertEqual(aggregate(unknown)["pass@1"], 0.0)

    def test_metrics_endpoints_logic_does_not_crash(self):
        from harnesslab.backend import metrics as M
        old = M.RUNS_ROOT
        M.RUNS_ROOT = self.root
        try:
            cells = M.cells(self.rows)
            self.assertTrue(cells)
            self.assertTrue(M.harness_comparison(self.rows, self.rows[0]["harness_id"]) is not None)
            self.assertEqual(M.sentinel_pairs(self.rows), [])
            self.assertTrue(M.weak_tests(self.rows))
            self.assertTrue(M.self_report("mix", self.rows) is not None)
            self.assertTrue(M.leakage("mix", self.rows, self.rows[0]["harness_id"]) is not None)
            card = M.report_card("mix", self.rows, self.rows[0]["harness_id"])
            self.assertIn("markdown", card)
            self.assertTrue(M.results_dirs() is not None)
        finally:
            M.RUNS_ROOT = old

    def test_trajectory_tests_and_the_sentinel_can_read_imported_ledgers(self):
        from harnesslab.core.trajtest import load_runs
        from harnesslab.backend import sentinel as S
        traj = load_runs(os.path.join(self.root, "mix"))
        self.assertEqual(len(traj), 7)
        for t in traj:
            self.assertTrue(t.tool_names)
            t.ran_tests_after_last_edit()
            t.read_before_write()
            t.repeated_tool_calls()
        spans = self.ledger(self.rows[0]["run_id"], "mix")
        replay = S.replay(spans, S.load_model())
        self.assertTrue(replay)
        self.assertTrue(all(0.0 <= x["risk"] <= 1.0 for x in replay))


# --------------------------------------------------------------------------- API
class TestRoutes(Tmp):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from fastapi.responses import PlainTextResponse
        from harnesslab.backend.importers import routes
        self.routes = routes
        app = FastAPI()

        @app.get("/{path:path}")
        def spa(path: str):                       # mimic app.py's SPA catch-all
            return PlainTextResponse("spa")

        app.include_router(routes.router)
        routes.hoist_api_routes(app)
        self.client = TestClient(app)
        routes.STATE.update(status="idle", result=None, error=None, history=[])

    def test_hoist_keeps_api_routes_ahead_of_the_spa_catch_all(self):
        self.assertEqual(self.client.get("/api/import/status").json()["status"], "idle")
        self.assertEqual(self.client.get("/whatever").text, "spa")

    def test_sources_endpoint(self):
        d = self.client.get("/api/import/sources").json()
        # the endpoint must expose exactly the registry, not a number that rots every time an
        # adapter lands (it was hardcoded to 6 and broke on the seventh and eighth)
        self.assertEqual([s["name"] for s in d["sources"]], [s["name"] for s in I.list_sources()])
        self.assertTrue(all(s["description"] for s in d["sources"]))

    def test_detect_endpoint(self):
        d = self.client.get("/api/import/detect", params={"path": CC}).json()
        self.assertEqual(d["source"], "claude_code")
        self.assertEqual(d["sessions"], 1)

    def test_detect_endpoint_on_a_missing_path(self):
        d = self.client.get("/api/import/detect", params={"path": "/no/such/thing"}).json()
        self.assertFalse(d["exists"])
        self.assertEqual(d["source"], "")

    def test_post_rejects_bad_input(self):
        self.assertEqual(self.client.post("/api/import", json={"path": "/nope", "results_dir": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/import", json={"path": CC, "results_dir": "a/b"}).status_code, 400)

    def test_post_runs_an_import_and_status_reports_it(self):
        import time
        # Write into a temp root, never into the shipped data/runs/. addCleanup(rmtree, ...,
        # ignore_errors=True) is not a cleanup guarantee: on any filesystem that refuses the
        # unlink it fails silently and the directory accumulates. Redirecting the root means
        # there is nothing to leave behind even when removal fails.
        tmp_root = tempfile.mkdtemp(prefix="harnesslab-import-test-")
        self.addCleanup(shutil.rmtree, tmp_root, True)
        real_root, I.RUNS_ROOT = I.RUNS_ROOT, tmp_root
        self.addCleanup(setattr, I, "RUNS_ROOT", real_root)
        out = "imported_%s" % uuid.uuid4().hex[:8]
        target = os.path.join(tmp_root, out)
        r = self.client.post("/api/import", json={"path": CC, "results_dir": out})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["source"], "claude_code")
        for _ in range(100):
            st = self.client.get("/api/import/status").json()
            if st["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual(st["status"], "done", st.get("error"))
        self.assertEqual(st["result"]["imported"], 1)
        self.assertTrue(os.path.exists(os.path.join(target, "index.jsonl")))

    def test_progress_never_reports_a_total_it_has_not_counted(self):
        """The old `total` was incremented inside the same loop that consumed the sessions, so it
        was a running count of what had been seen -- never a total. `imported + skipped` equalled
        it on every clean iteration, which made the bar on the import screen read 100% from the
        first session to the last. An unknown quantity is reported as unknown here, the way the
        ledger reports one: null, never a number that looks measured."""
        tmp_root = tempfile.mkdtemp(prefix="harnesslab-progress-")
        self.addCleanup(shutil.rmtree, tmp_root, True)
        seen = []
        r = I.import_path(SA, "p_%s" % uuid.uuid4().hex[:8], progress=lambda d, t, l: seen.append((d, t)),
                          runs_root=tmp_root)
        self.assertGreaterEqual(r["imported"], 1, r)
        self.assertTrue(seen)
        for done, total in seen:
            self.assertIsNone(total, f"progress claimed a total of {total} it had not counted")
        self.assertEqual([d for d, _ in seen], sorted(d for d, _ in seen), "done went backwards")
        self.assertEqual(seen[-1][0], r["imported"] + r["skipped"])

    def test_a_held_capture_lock_reaches_the_page_as_a_sentence_without_a_path(self):
        """The lock's own message names the flock file -- an absolute path under the operator's
        home -- and this payload is frozen verbatim into `--export`. Contention is normal
        operation anyway, so the page gets a sentence about it instead of an exception class."""
        import time
        from harnesslab.capture.lock import CaptureLock
        tmp_root = tempfile.mkdtemp(prefix="harnesslab-locked-")
        self.addCleanup(shutil.rmtree, tmp_root, True)
        real_root, I.RUNS_ROOT = I.RUNS_ROOT, tmp_root
        self.addCleanup(setattr, I, "RUNS_ROOT", real_root)
        real_rr, self.routes.RUNS_ROOT = self.routes.RUNS_ROOT, tmp_root
        self.addCleanup(setattr, self.routes, "RUNS_ROOT", real_rr)
        with CaptureLock(tmp_root):
            r = self.client.post("/api/import", json={"path": CC, "results_dir": "captured"})
            self.assertEqual(r.status_code, 200)
            for _ in range(100):
                st = self.client.get("/api/import/status").json()
                if st["status"] in ("done", "error"):
                    break
                time.sleep(0.05)
        self.assertEqual(st["status"], "error", st)
        self.assertNotIn("CaptureLocked", st["error"])
        self.assertNotIn(tmp_root, st["error"])
        self.assertNotIn("/", st["error"].replace("--", ""))
        self.assertIn("another capture writer", st["error"])

    def test_publish_callback_is_used_when_set(self):
        seen = []
        self.routes.PUBLISH = lambda kind, **d: seen.append((kind, d.get("status")))
        self.addCleanup(setattr, self.routes, "PUBLISH", None)
        self.routes._publish("import", status="running")
        self.assertEqual(seen, [("import", "running")])


if __name__ == "__main__":
    unittest.main()
