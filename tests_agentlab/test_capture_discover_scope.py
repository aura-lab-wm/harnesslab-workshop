"""A descriptor's glob has to be able to EXCLUDE, not only to widen the walk.

`discover()` built its candidate set as the union of every descriptor's globs and then let
`resolve()` pick a winner on contents alone. Because claude_code and codex both glob `**/*.jsonl`,
any narrower glob was void -- qwen_code's deliberate `**/chats/**/*.jsonl` included. The capture
spine relies on that narrowing: Qwen writes subagent sessions to a sibling directory, and they are
child work, not runs. Captured as peers they inflate every aggregate that reads index.jsonl.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.capture import adapters                       # noqa: E402

PARENT = "parent-session-id"
BASE = [
    {"uuid": "u-1", "parentUuid": None, "sessionId": PARENT, "timestamp": "2026-09-10T10:00:00.000Z",
     "type": "user", "cwd": "/w", "version": "0.4.1", "gitBranch": "main",
     "message": {"role": "user", "parts": [{"text": "go"}]}},
    {"uuid": "u-2", "parentUuid": "u-1", "sessionId": PARENT, "timestamp": "2026-09-10T10:00:04.000Z",
     "type": "assistant", "cwd": "/w", "version": "0.4.1", "gitBranch": "main",
     "message": {"role": "model", "parts": [{"functionCall": {"id": "c1", "name": "read_file",
                                                              "args": {"path": "a.py"}}}]},
     "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4}},
]


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class TestDiscoverHonoursEachDescriptorsGlob(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-disc-")
        self.addCleanup(shutil.rmtree, self.root, True)
        chat = [dict(r) for r in BASE]
        for r in chat:
            r["provenance"] = "real_user" if r["type"] == "user" else "assistant_output"
        self.chat = os.path.join(self.root, "slug", "chats", "aaaa.jsonl")
        write(self.chat, chat)
        sub = [dict(r, agentId="a-1", agentName="rev", isSidechain=True) for r in BASE]
        self.sub = os.path.join(self.root, "slug", "subagents", PARENT, "bbbb.jsonl")
        write(self.sub, sub)

    def found(self):
        rows, _ = adapters.discover([self.root])
        return {p: name for p, name in rows}

    def test_a_chat_is_discovered_as_qwen(self):
        self.assertEqual(self.found().get(os.path.realpath(self.chat)), "qwen_code")

    def test_a_subagent_file_outside_the_qwen_glob_is_not_claimed_by_qwen(self):
        self.assertNotEqual(self.found().get(os.path.realpath(self.sub)), "qwen_code",
                            "the descriptor's glob excluded this path and was ignored")

    def test_resolve_alone_still_answers_on_contents(self):
        """resolve() is the content question and keeps answering it; only discover() applies scope."""
        self.assertEqual(adapters.resolve(self.sub)[0], "qwen_code")
