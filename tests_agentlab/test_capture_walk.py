"""One walk per root, matched against every descriptor, must find exactly what glob found.

Each descriptor used to glob the tree itself -- five patterns, two passes when a tick re-reads related
sources -- and on this machine that was 401,000 scandir calls and 35 of a 43-second tick, to find
five changed files. The replacement is only worth having if it is indistinguishable from glob, so
that is what this tests: set equality against glob.glob on a tree built to hit its edge cases.
"""
import glob
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.capture import adapters                       # noqa: E402

PATTERNS = ["**/*.jsonl", "**/rollout-*.jsonl", "**/chats/**/*.jsonl", "*.jsonl", "a/*/c.jsonl"]


def touch(root, rel):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("{}\n")


class TestWalkMatchesGlob(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-walk-")
        self.addCleanup(shutil.rmtree, self.root, True)
        for rel in ["top.jsonl", "notes.txt", "rollout-1.jsonl", "a/b/c.jsonl", "a/x/c.jsonl",
                    "p/chats/one.jsonl", "p/chats/deep/two.jsonl", "p/sub/chats/three.jsonl",
                    "p/subagents/uuid/four.jsonl", "chats/root-chat.jsonl",
                    ".hidden/inside.jsonl", "p/.cache/chats/five.jsonl", "p/chats/.dot.jsonl",
                    "sessions/2026/09/rollout-2026-09-09.jsonl", "deep/er/rollout-x.jsonl"]:
            touch(self.root, rel)
        real_dir = os.path.join(self.root, "p", "chats", "deep")
        try:
            os.symlink(real_dir, os.path.join(self.root, "linked"))
        except OSError:
            pass

    def test_every_pattern_finds_exactly_what_glob_finds(self):
        index, _blocked = adapters._walk(self.root)
        for pat in PATTERNS:
            with self.subTest(pattern=pat):
                expected = set(glob.glob(os.path.join(self.root, pat), recursive=True))
                got = {os.path.join(self.root, rel) for rel in index if adapters._matches(rel, pat)}
                self.assertEqual(got, expected)

    def test_the_compiled_matcher_agrees_with_the_reference_on_every_path(self):
        index, _blocked = adapters._walk(self.root)
        extra = ["a.jsonl", ".a.jsonl", "x/.y/z.jsonl", "chats/a/b/c.jsonl", "q/chats", "rollout-.jsonl",
                 "p/q/r/s/t/rollout-9.jsonl", "p/.chats/x.jsonl"]
        for pat in PATTERNS + ["**/.cache/*.jsonl", "p/?/x.jsonl"]:
            fast = adapters._compile(pat)
            for rel in index + extra:
                with self.subTest(pattern=pat, rel=rel):
                    self.assertEqual(fast(rel), adapters._matches(rel, pat))

    def test_a_symlink_cycle_does_not_hang_the_walk(self):
        os.symlink(self.root, os.path.join(self.root, "p", "loop"))
        files, _blocked = adapters._walk(self.root)      # returns rather than recursing forever
        self.assertTrue(any(f.endswith("top.jsonl") for f in files))

    def test_the_tree_is_walked_once_however_many_patterns_there_are(self):
        from unittest import mock
        with mock.patch.object(os, "scandir", wraps=os.scandir) as sd:
            adapters.discover([self.root])
            walked = sd.call_count
        with mock.patch.object(os, "scandir", wraps=os.scandir) as sd:
            adapters._walk(self.root)
            once = sd.call_count
        self.assertLessEqual(walked, once + 1, "discover walked the tree more than once")


if __name__ == "__main__":
    unittest.main()
