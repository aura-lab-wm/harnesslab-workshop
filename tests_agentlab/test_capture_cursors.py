"""What the sniffer remembers between passes.

A full pass over this machine's corpus reads 19,380 files and costs minutes. A cursor says what a
source looked like when it was last captured, so an unchanged source is skipped without being
opened -- which is the whole difference between a loop that can run every minute and one that
cannot run at all.
"""
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.capture import cursors                        # noqa: E402


class TestCursors(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-cursors-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.src = os.path.join(self.root, "a.jsonl")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write("one\n")

    def test_a_source_that_has_not_moved_is_unchanged(self):
        st = os.stat(self.src)
        c = cursors.cur(size=st.st_size, mtime_ns=st.st_mtime_ns)
        self.assertTrue(cursors.unchanged(c, st))

    def test_a_source_that_grew_is_changed(self):
        st = os.stat(self.src)
        c = cursors.cur(size=st.st_size, mtime_ns=st.st_mtime_ns)
        with open(self.src, "a", encoding="utf-8") as f:
            f.write("two\n")
        self.assertFalse(cursors.unchanged(c, os.stat(self.src)))

    def test_a_rewrite_that_kept_the_size_is_still_changed(self):
        """Same length, new content: mtime is what catches it."""
        st = os.stat(self.src)
        c = cursors.cur(size=st.st_size, mtime_ns=st.st_mtime_ns)
        os.utime(self.src, ns=(st.st_atime_ns, st.st_mtime_ns + 1000))
        self.assertFalse(cursors.unchanged(c, os.stat(self.src)))

    def test_an_empty_cursor_never_claims_a_source_is_unchanged(self):
        self.assertFalse(cursors.unchanged(cursors.cur(), os.stat(self.src)))

    def test_load_of_an_absent_file_is_empty_not_an_error(self):
        self.assertEqual(cursors.load(cursors.path_for(self.root)), {})

    def test_load_of_a_torn_file_is_empty_rather_than_raising(self):
        p = cursors.path_for(self.root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(cursors.load(p), {})

    def test_a_cursor_file_from_another_schema_is_not_misread(self):
        import json
        p = cursors.path_for(self.root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"schema": cursors.SCHEMA + 1, "cursors": {self.src: cursors.cur(size=1)}}, f)
        self.assertEqual(cursors.load(p), {})

    def test_save_and_load_round_trip(self):
        p = cursors.path_for(self.root)
        cursors.save(p, {self.src: cursors.cur(size=4, mtime_ns=7, runs=["r1"])})
        back = cursors.load(p)
        self.assertEqual(back[self.src]["runs"], ["r1"])
        self.assertEqual(back[self.src]["size"], 4)

    # -------------------------------------------------------------- openness
    def test_a_source_with_an_open_run_comes_due_once_the_gap_elapses(self):
        cs = {self.src: cursors.cur(size=1, mtime_ns=1, open_until=1000.0)}
        self.assertEqual(cursors.due(cs, now=999.0, gap_s=1800), set())
        self.assertEqual(cursors.due(cs, now=1001.0, gap_s=1800), {self.src})

    def test_a_source_with_nothing_open_never_comes_due_on_its_own(self):
        cs = {self.src: cursors.cur(size=1, mtime_ns=1, open_until=0.0)}
        self.assertEqual(cursors.due(cs, now=10 ** 9, gap_s=1800), set())

    # -------------------------------------------------------------- ownership
    def test_claimed_lists_every_run_any_cursor_still_produces(self):
        cs = {"a": cursors.cur(runs=["r1", "r2"]), "b": cursors.cur(runs=["r3"])}
        self.assertEqual(cursors.claimed(cs), {"r1", "r2", "r3"})

    def test_claimed_includes_a_vanished_source_until_it_is_pruned(self):
        """Its runs are still in the index; forgetting them would orphan them silently."""
        cs = {"/gone/x.jsonl": cursors.cur(runs=["r9"])}
        self.assertIn("r9", cursors.claimed(cs))


if __name__ == "__main__":
    unittest.main()
