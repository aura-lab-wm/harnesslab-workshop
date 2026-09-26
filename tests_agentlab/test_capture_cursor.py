"""Cursor agent transcripts: an HONESTLY PARTIAL source.

Measured with tools/census_cursor.py on the operator's corpus (structure and counts only): 7 files,
679 rows, three top-level shapes -- {message, role} x643, {status, type} x12, {error, status, type}
x24. Content blocks are text x239 and tool_use x1137; every tool_use carries exactly {input, name,
type}: NO id. There is no tool_result block anywhere, no usage, no model, and no timestamp at any
depth. So the model's tool REQUESTS are observable, their execution is not.

Every row here is synthetic: typed placeholders, never real content.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend.importers import common as C, cursor   # noqa: E402

FIXDIR = os.path.join(LAB, "tests_agentlab", "fixtures", "capture")
FIXTURE = os.path.join(FIXDIR, "cursor_transcript.jsonl")


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def user(text="<prompt>"):
    return {"role": "user", "message": {"content": [{"type": "text", "text": text}]}}


def assistant(*blocks):
    return {"role": "assistant", "message": {"content": list(blocks)}}


def text(t="<prose>"):
    return {"type": "text", "text": t}


def tool(name="Read", **inp):
    return {"type": "tool_use", "name": name, "input": inp or {"path": "<path>"}}


class TestCursorParse(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="hl-cursor-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.path = os.path.join(self.d, "proj", "agent-transcripts", "sess-1", "sess-1.jsonl")

    def parse(self, rows):
        write(self.path, rows)
        return cursor.parse_file(self.path)

    def test_messages_and_tool_requests_become_events_in_block_order(self):
        s = self.parse([user("<p1>"), assistant(text("<a1>"), tool("Read"), tool("Shell", command="<c>")),
                        {"type": "turn_ended", "status": "success"}])
        self.assertEqual([e.kind for e in s.events], ["user", "assistant", "tool_call", "tool_call"])
        self.assertEqual(s.events[0].text, "<p1>")
        self.assertEqual(s.events[1].text, "<a1>")
        self.assertEqual([e.name for e in s.events[2:]], ["Read", "Shell"])
        self.assertEqual(s.events[3].args, {"command": "<c>"})

    def test_nothing_is_invented_the_format_does_not_carry(self):
        s = self.parse([user(), assistant(text(), tool())])
        for e in s.events:
            self.assertEqual(e.ts, "", "no clock exists in the format; none may be synthesised")
            self.assertEqual(e.call_id, "", "tool_use blocks carry no id; none may be synthesised")
            self.assertEqual(e.usage, {}, "the format records no usage")
            self.assertEqual(e.record_id, "")
        self.assertFalse(any(e.kind in ("tool_result", "observation") for e in s.events))
        self.assertEqual(s.source, "cursor")
        self.assertEqual(s.session_id, "sess-1")

    def test_turn_ended_is_understood_and_a_final_error_is_the_exit_status(self):
        s = self.parse([user(), assistant(text()), {"type": "turn_ended", "status": "success"},
                        user(), assistant(text()), {"type": "turn_ended", "status": "error", "error": "<e>"}])
        self.assertEqual(s.extra["unknown_record_types"], {})
        self.assertEqual(s.exit_status, "error")
        # the error TEXT is content; it never becomes the exit status
        self.assertNotIn("<e>", json.dumps(s.extra))

    def test_a_turn_that_ended_cleanly_is_not_a_submission(self):
        s = self.parse([user(), assistant(text()), {"type": "turn_ended", "status": "success"}])
        self.assertEqual(s.exit_status, "")

    def test_shapes_it_does_not_understand_are_counted_not_dropped(self):
        s = self.parse([user(), assistant(text(), {"type": "image"}),
                        {"type": "checkpoint"}, {"role": "system", "message": {"content": []}}])
        self.assertEqual(s.extra["unknown_record_types"],
                         {"block:image": 1, "type:checkpoint": 1, "role:system": 1})

    def test_a_subagent_transcript_names_its_parent(self):
        path = os.path.join(self.d, "proj", "agent-transcripts", "parent-1", "subagents", "child-1.jsonl")
        write(path, [user(), assistant(text())])
        s = cursor.parse_file(path)
        self.assertEqual(s.extra["parent_session_id"], "parent-1")
        self.assertEqual(self.parse([user(), assistant(text())]).extra["parent_session_id"], "")

    def test_every_native_tool_in_the_census_is_mapped(self):
        census = ["AskQuestion", "AwaitShell", "CallDynamicTool", "GetDynamicTools", "Glob", "Grep",
                  "Read", "ReadLints", "SearchConversations", "SetActiveBranch", "Shell", "StrReplace",
                  "SwitchMode", "Task", "TodoWrite", "UpdateCurrentStep", "Write"]
        from harnesslab.backend.importers import common as C
        for name in census:
            with self.subTest(tool=name):
                self.assertTrue(name.lower() in cursor.TOOL_OVERRIDES or name.lower() in C.TOOL_MAP)
        self.assertEqual(C.map_tool("StrReplace", {"path": "<p>"}, cursor.TOOL_OVERRIDES)[0], "edit_file")


class TestCursorSniff(unittest.TestCase):
    def test_the_fixture_is_cursor(self):
        self.assertGreaterEqual(cursor.sniff(FIXTURE), 0.5)

    def test_every_other_capture_fixture_is_not_cursor(self):
        for fx in ("cc_session.jsonl", "cx_rollout.jsonl", "qwen_chat.jsonl", "gemini_session.jsonl"):
            with self.subTest(fixture=fx):
                self.assertEqual(cursor.sniff(os.path.join(FIXDIR, fx)), 0.0)

    def test_a_row_carrying_another_formats_envelope_disqualifies_the_file(self):
        d = tempfile.mkdtemp(prefix="hl-cursor-sniff-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "x.jsonl")
        write(p, [dict(user(), uuid="u-1", timestamp="2026-09-10T10:00:00Z", type="user")])
        self.assertEqual(cursor.sniff(p), 0.0)
        write(p, [{"type": "turn_ended", "status": "success"}])
        self.assertEqual(cursor.sniff(p), 0.0, "no message row: nothing says this is a transcript")


class TestCursorArgumentsSurvive(unittest.TestCase):
    """The tool NAMES were mapped and the arguments were not, so a Write became an empty file and a
    Glob became a listing of the working directory."""

    def one_call(self, name, args):
        d = tempfile.mkdtemp(prefix="hl-cursor-args-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "t.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"role": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": name, "input": args}]}}) + "\n")
        sess = cursor.parse_file(p)
        call = [e for e in sess.events if e.kind == "tool_call"][0]
        return C.map_tool(call.name, call.args, cursor.TOOL_OVERRIDES)

    def test_a_write_keeps_what_it_was_writing(self):
        tool, args = self.one_call("Write", {"path": "a.py", "contents": "print(1)\n"})
        self.assertEqual(tool, "write_file")
        self.assertEqual(args["content"], "print(1)\n")

    def test_a_glob_keeps_its_pattern_and_directory(self):
        tool, args = self.one_call("Glob", {"glob_pattern": "**/*.py", "target_directory": "src"})
        self.assertEqual(tool, "list_files")
        self.assertEqual((args["path"], args["pattern"]), ("src", "**/*.py"))

    def test_an_edit_keeps_both_sides(self):
        tool, args = self.one_call("StrReplace", {"path": "a.py", "old_string": "x", "new_string": "y"})
        self.assertEqual((tool, args["old"], args["new"]), ("edit_file", "x", "y"))

    def test_a_key_cursor_did_not_send_is_not_invented(self):
        self.assertEqual(cursor.rename_args({"path": "p"}), {"path": "p"})
        self.assertEqual(cursor.rename_args({"content": "a", "contents": "b"})["content"], "a")


class TestTheTwoEntryPointsAgree(unittest.TestCase):
    """The capture glob claims **/agent-transcripts/*/*.jsonl and leaves subagents/ out, because a
    child's parent link is a start-span field an index row cannot carry. A directory IMPORT walked
    them in as peers, so the same tree produced different runs depending on which door it came
    through."""

    def tree(self):
        d = tempfile.mkdtemp(prefix="hl-cursor-two-")
        self.addCleanup(shutil.rmtree, d, True)
        top = os.path.join(d, "projects", "p", "agent-transcripts", "abc")
        os.makedirs(os.path.join(top, "subagents"))
        fix = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "cursor_transcript.jsonl")
        shutil.copy(fix, os.path.join(top, "abc.jsonl"))
        shutil.copy(fix, os.path.join(top, "subagents", "child.jsonl"))
        return d, top

    def test_a_directory_import_skips_subagent_children(self):
        d, top = self.tree()
        got = sorted(os.path.basename(s.path) for s in cursor.sessions(d))
        self.assertEqual(got, ["abc.jsonl"], "a child was imported as a peer run")

    def test_a_directory_import_claims_exactly_what_the_capture_glob_claims(self):
        """Not just "everything but subagents": canvases/, terminals/ and agent-tools/ sit beside the
        transcripts, and importing them made the same tree produce four times the runs."""
        from harnesslab.capture import adapters
        d, top = self.tree()
        fix = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "cursor_transcript.jsonl")
        for sibling in ("canvases", "terminals", "agent-tools"):
            os.makedirs(os.path.join(d, "projects", "p", sibling), exist_ok=True)
            shutil.copy(fix, os.path.join(d, "projects", "p", sibling, "x.jsonl"))
        captured = sorted(os.path.realpath(p) for p, _a in adapters.discover([d])[0])
        imported = sorted(os.path.realpath(s.path) for s in cursor.sessions(d))
        self.assertEqual(imported, captured)

    def test_a_child_named_explicitly_is_still_read(self):
        d, top = self.tree()
        child = os.path.join(top, "subagents", "child.jsonl")
        self.assertEqual([os.path.basename(s.path) for s in cursor.sessions(child)], ["child.jsonl"])

    def test_a_clock_key_does_not_stop_the_adapter_claiming_its_own_file(self):
        """The census is kept red against the day Cursor grows a clock. On that day a foreign-key
        test would have stopped it claiming its transcripts at all."""
        d = tempfile.mkdtemp(prefix="hl-cursor-clock-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "t.jsonl")
        rows = [{"role": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                 "timestamp": "2026-09-16T10:00:00Z"}]
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertGreaterEqual(cursor.sniff(p), 0.5, "a clock made it foreign to itself")


if __name__ == "__main__":
    unittest.main()
