"""Qwen Code writes subagent work to sibling files the chats glob never sees.

Measured on the operator's corpus: 24 chats under <project>/chats/, and 160 files under
<project>/subagents/<parent-session-id>/ carrying 4,143 tool calls -- more tool-call volume than
the chats themselves. The parser already reads them correctly; only the sniffer does not recognise
them, because it spends 0.6 of its score on `provenance`, a key only the chats variant carries.
"""
import json
import os
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend.importers import qwen_code           # noqa: E402
from harnesslab.capture import adapters                      # noqa: E402

PARENT = "11111111-2222-3333-4444-555555555555"

# The subagent shape, written from the key sets observed on disk: no `provenance`, but agentId,
# agentName and isSidechain, and the same message.parts body the chats variant uses.
ROWS = [
    {"uuid": "u-1", "parentUuid": None, "sessionId": PARENT, "timestamp": "2026-09-10T10:00:00.000Z",
     "type": "user", "cwd": "/w", "version": "0.4.1", "agentId": "a-1", "agentName": "reviewer",
     "isSidechain": True, "gitBranch": "main",
     "message": {"role": "user", "parts": [{"text": "review this"}]}},
    {"uuid": "u-2", "parentUuid": "u-1", "sessionId": PARENT, "timestamp": "2026-09-10T10:00:04.000Z",
     "type": "assistant", "cwd": "/w", "version": "0.4.1", "agentId": "a-1", "agentName": "reviewer",
     "isSidechain": True, "gitBranch": "main",
     "message": {"role": "model", "parts": [{"functionCall": {"id": "c-1", "name": "read_file",
                                                              "args": {"path": "a.py"}}}]},
     "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4}},
    {"uuid": "u-3", "parentUuid": "u-2", "sessionId": PARENT, "timestamp": "2026-09-10T10:00:06.000Z",
     "type": "user", "cwd": "/w", "version": "0.4.1", "agentId": "a-1", "agentName": "reviewer",
     "isSidechain": True, "gitBranch": "main",
     "message": {"role": "user", "parts": [{"functionResponse": {"id": "c-1", "name": "read_file",
                                                                 "response": {"output": "ok"}}}]}},
]


class TestQwenSubagentSessions(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="hl-qwen-sub-")
        self.dir = os.path.join(self.d, "projects", "slug", "subagents", PARENT)
        os.makedirs(self.dir)
        self.path = os.path.join(self.dir, "66666666-7777-8888-9999-000000000000.jsonl")
        with open(self.path, "w", encoding="utf-8") as f:
            for r in ROWS:
                f.write(json.dumps(r) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, True)

    def test_a_subagent_session_is_recognised_as_qwen(self):
        """Without this the sniffer scores 0.40 -- under the floor -- on a file it parses perfectly."""
        self.assertGreaterEqual(qwen_code.sniff(self.path), adapters.MIN_CONFIDENCE)

    def test_the_registry_claims_it_for_qwen_and_not_for_claude_code(self):
        """claude_code sniffs these at 0.95 and then parses none of them, so losing this race matters."""
        name, score = adapters.resolve(self.path)
        self.assertEqual(name, "qwen_code")
        self.assertGreaterEqual(score, adapters.MIN_CONFIDENCE)

    def test_the_parent_session_is_recorded_so_the_run_is_never_counted_as_a_peer(self):
        """160 subagent files belong to 7 parents. Counting them as independent runs would inflate
        Qwen's run count sevenfold against its real chats, so the link has to survive the import."""
        sess = qwen_code.parse_file(self.path)
        self.assertIsNotNone(sess)
        self.assertTrue(sess.extra.get("is_subagent"))
        self.assertEqual(sess.extra.get("parent_session_id"), PARENT)

    def test_an_ordinary_chat_is_not_marked_as_a_subagent(self):
        chat = os.path.join(self.d, "projects", "slug", "chats")
        os.makedirs(chat)
        p = os.path.join(chat, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl")
        rows = [dict(r) for r in ROWS]
        for r in rows:                       # the chats variant: provenance present, no agentId
            r["provenance"] = "real_user" if r["type"] == "user" else "assistant_output"
            r.pop("agentId", None); r.pop("agentName", None); r.pop("isSidechain", None)
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        sess = qwen_code.parse_file(p)
        self.assertIsNotNone(sess)
        self.assertFalse(sess.extra.get("is_subagent"))
        self.assertEqual(sess.extra.get("parent_session_id"), "")


if __name__ == "__main__":
    unittest.main()
