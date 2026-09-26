"""Capture writing: lock, allow-list, regeneration, archive, backfill, CLI."""
import gzip
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import importers as I                   # noqa: E402
from harnesslab.capture.lock import CaptureLock, CaptureLocked   # noqa: E402

FIXDIR = os.path.join(LAB, "tests_agentlab", "fixtures", "capture")
FIX = os.path.join(FIXDIR, "cc_session.jsonl")
IMPORT_FIX = os.path.join(LAB, "tests_agentlab", "fixtures", "importers", "claude_code_session.jsonl")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-capture-")
        self.addCleanup(shutil.rmtree, self.tmp, True)


class TestCaptureLock(Tmp):
    def test_a_lock_failure_closes_the_descriptor_and_raises_capture_locked(self):
        import fcntl as _fcntl
        before = len(os.listdir("/dev/fd"))
        with mock.patch.object(_fcntl, "flock", side_effect=PermissionError("no locks on this mount")):
            for _ in range(50):
                with self.assertRaises(CaptureLocked):
                    with CaptureLock(self.tmp):
                        pass
        self.assertLess(len(os.listdir("/dev/fd")) - before, 10)

    def test_a_second_writer_is_refused_and_the_lock_releases(self):
        with CaptureLock(self.tmp):
            with self.assertRaises(CaptureLocked):
                with CaptureLock(self.tmp):
                    pass
        with CaptureLock(self.tmp):
            pass

    def test_a_manual_import_into_captured_takes_the_lock(self):
        with CaptureLock(self.tmp):
            with self.assertRaises(CaptureLocked):
                I.import_path(IMPORT_FIX, "captured", runs_root=self.tmp)

    def test_imports_elsewhere_are_unaffected(self):
        with CaptureLock(self.tmp):
            I.import_path(IMPORT_FIX, "other", runs_root=self.tmp)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "other", "index.jsonl")))

    def test_a_spelling_variant_or_alias_of_captured_takes_the_lock(self):
        os.makedirs(os.path.join(self.tmp, "captured"), exist_ok=True)
        os.symlink(os.path.join(self.tmp, "captured"), os.path.join(self.tmp, "sneaky"))
        with CaptureLock(self.tmp):
            for name in ("Captured", "CAPTURED", "sneaky"):
                with self.subTest(name=name):
                    with self.assertRaises(CaptureLocked):
                        I.import_path(IMPORT_FIX, name, runs_root=self.tmp)

    def test_a_spelling_variant_of_captured_writes_into_the_canonical_directory(self):
        with mock.patch.object(I, "_import_path") as m:
            I.import_path(IMPORT_FIX, "Captured", runs_root=self.tmp)
        self.assertEqual(m.call_args[0][1], "captured")

    def test_a_symlink_alias_name_is_forwarded_unchanged(self):
        os.makedirs(os.path.join(self.tmp, "captured"), exist_ok=True)
        os.symlink(os.path.join(self.tmp, "captured"), os.path.join(self.tmp, "sneaky"))
        with mock.patch.object(I, "_import_path") as m:
            I.import_path(IMPORT_FIX, "sneaky", runs_root=self.tmp)
        self.assertEqual(m.call_args[0][1], "sneaky")


from harnesslab.capture import allowlist   # noqa: E402


class TestAllowlist(Tmp):
    def setUp(self):
        super().setUp()
        self.allowed = os.path.join(self.tmp, "allowed")
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.allowed)
        os.makedirs(self.outside)
        self.cfg = os.path.join(self.tmp, "capture.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"paths": [self.allowed, "", 7]}, f)

    def test_load_resolves_and_skips_junk(self):
        self.assertEqual(allowlist.load_paths(self.cfg), [os.path.realpath(self.allowed)])

    def test_a_missing_config_allows_nothing(self):
        self.assertEqual(allowlist.load_paths(os.path.join(self.tmp, "nope.json")), [])

    def test_a_symlink_cannot_escape(self):
        link = os.path.join(self.allowed, "sneaky")
        os.symlink(self.outside, link)
        roots = allowlist.load_paths(self.cfg)
        self.assertTrue(allowlist.is_allowed(os.path.join(self.allowed, "a.jsonl"), roots))
        self.assertFalse(allowlist.is_allowed(os.path.join(link, "b.jsonl"), roots))

    def test_a_prefix_sibling_is_not_inside(self):
        sibling = self.allowed + "-2"
        os.makedirs(sibling)
        self.assertFalse(allowlist.is_allowed(os.path.join(sibling, "c.jsonl"), allowlist.load_paths(self.cfg)))

    def test_a_non_list_paths_value_allows_nothing(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"paths": self.allowed}, f)
        self.assertEqual(allowlist.load_paths(self.cfg), [])

    def test_a_corrupt_config_allows_nothing(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write('{"paths": [')
        self.assertEqual(allowlist.load_paths(self.cfg), [])

    def test_a_root_given_with_a_trailing_slash_contains_itself(self):
        root = os.path.realpath(self.allowed)
        self.assertTrue(allowlist.is_allowed(root, [root + os.sep]))


from harnesslab.backend.importers import claude_code, common as C   # noqa: E402
from harnesslab.capture import regen                                # noqa: E402

RESUMED_TAIL = [
    {"type": "user", "uuid": "u-0003", "parentUuid": "a-0008", "isSidechain": False, "sessionId": "sess-cap-1",
     "cwd": "/work/demo", "version": "2.1.3", "gitBranch": "main", "entrypoint": "cli",
     "timestamp": "2026-09-12T09:00:00.000Z", "message": {"role": "user", "content": "Back again: add type hints"}},
    {"type": "assistant", "uuid": "a-0009", "parentUuid": "u-0003", "isSidechain": False, "sessionId": "sess-cap-1",
     "cwd": "/work/demo", "version": "2.1.3", "gitBranch": "main", "entrypoint": "cli", "requestId": "req_7",
     "timestamp": "2026-09-12T09:00:04.000Z",
     "message": {"id": "msg_7", "role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                 "usage": {"input_tokens": 2, "output_tokens": 15, "cache_read_input_tokens": 0,
                           "cache_creation_input_tokens": 900},
                 "content": [{"type": "text", "text": "Added type hints."}]}},
]


def copy_fixture(dest, extra_rows=()):
    with open(FIX, encoding="utf-8") as src, open(dest, "w", encoding="utf-8") as out:
        out.write(src.read())
        for r in extra_rows:
            out.write(json.dumps(r) + "\n")
    return dest


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class TestRegenerate(Tmp):
    def test_one_run_whole_and_deterministic(self):
        a = regen.regenerate(FIX, os.path.join(self.tmp, "a"), gap_s=1800)
        b = regen.regenerate(FIX, os.path.join(self.tmp, "b"), gap_s=1800)
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0].run_id, b[0].run_id)
        self.assertEqual(read_bytes(os.path.join(self.tmp, "a", a[0].run_id, "ledger.jsonl")),
                         read_bytes(os.path.join(self.tmp, "b", b[0].run_id, "ledger.jsonl")))

    def test_regenerating_in_place_leaves_no_temp_or_old_dirs(self):
        out = os.path.join(self.tmp, "out")
        (first,) = regen.regenerate(FIX, out, gap_s=1800)
        before = read_bytes(os.path.join(out, first.run_id, "ledger.jsonl"))
        regen.regenerate(FIX, out, gap_s=1800)
        self.assertEqual(os.listdir(out), [first.run_id])
        self.assertEqual(read_bytes(os.path.join(out, first.run_id, "ledger.jsonl")), before)

    def test_segment_zero_has_the_manual_importers_run_id(self):
        (r,) = regen.regenerate(FIX, os.path.join(self.tmp, "out"), gap_s=1800)
        sess = claude_code.parse_file(FIX)
        self.assertEqual(r.run_id, C.run_id_for(sess, C.derive_task_id(sess)))
        self.assertEqual(r.uuids[0], "u-0001")

    def test_a_resumed_segment_gets_a_suffixed_id_and_resumed_from(self):
        p = copy_fixture(os.path.join(self.tmp, "s.jsonl"), RESUMED_TAIL)
        out = os.path.join(self.tmp, "out")
        runs = regen.regenerate(p, out, gap_s=1800)
        sess = claude_code.parse_file(p)
        base = C.run_id_for(sess, C.derive_task_id(sess))
        self.assertEqual([r.run_id for r in runs], [base, base + "-s1"])
        with open(os.path.join(out, base + "-s1", "ledger.jsonl"), encoding="utf-8") as f:
            start = json.loads(f.readline())
        self.assertEqual((start["segment"], start["resumed_from"], start["root_uuid"]), (1, base, "u-0003"))
        self.assertNotEqual(runs[1].summary.task_id, runs[0].summary.task_id)


from harnesslab.capture import archive   # noqa: E402


class TestArchive(Tmp):
    def test_archives_once_then_again_after_the_source_grows(self):
        src = copy_fixture(os.path.join(self.tmp, "s.jsonl"))
        root = os.path.join(self.tmp, "archive")
        self.assertTrue(archive.archive_source(src, root))
        self.assertFalse(archive.archive_source(src, root))
        with open(src, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "bridge-session"}) + "\n")
        self.assertTrue(archive.archive_source(src, root))
        (gz,) = [f for f in os.listdir(root) if f.endswith(".gz")]
        with gzip.open(os.path.join(root, gz), "rb") as g:
            self.assertEqual(g.read(), read_bytes(src))

    def test_the_archive_directory_is_private(self):
        root = os.path.join(self.tmp, "archive")
        archive.archive_source(copy_fixture(os.path.join(self.tmp, "s.jsonl")), root)
        self.assertEqual(stat.S_IMODE(os.stat(root).st_mode) & 0o077, 0)

    def test_the_meta_describes_the_bytes_actually_archived(self):
        src = copy_fixture(os.path.join(self.tmp, "g.jsonl"))
        root = os.path.join(self.tmp, "archive2")
        real_copy = shutil.copyfileobj

        def growing_copy(fsrc, fdst, length=0):
            with open(src, "a", encoding="utf-8") as f:       # the session grows while being archived
                f.write(json.dumps({"type": "bridge-session"}) + "\n")
            return real_copy(fsrc, fdst, length)

        with mock.patch.object(archive.shutil, "copyfileobj", growing_copy):
            archive.archive_source(src, root)
        (gz,) = [f for f in os.listdir(root) if f.endswith(".gz")]
        with gzip.open(os.path.join(root, gz), "rb") as g:
            archived = len(g.read())
        with open(os.path.join(root, gz + ".meta.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["size"], archived)


from harnesslab.capture import backfill, identity   # noqa: E402

RESUME_TAIL = [
    {"type": "user", "uuid": "u-0004", "parentUuid": "a-0008", "isSidechain": False, "sessionId": "sess-cap-2",
     "cwd": "/work/demo", "version": "2.1.3", "gitBranch": "main", "entrypoint": "cli",
     "timestamp": "2026-09-10T10:08:00.000Z", "message": {"role": "user", "content": "One more thing: rename it."}},
    {"type": "assistant", "uuid": "a-0010", "parentUuid": "u-0004", "isSidechain": False, "sessionId": "sess-cap-2",
     "cwd": "/work/demo", "version": "2.1.3", "gitBranch": "main", "entrypoint": "cli", "requestId": "req_8",
     "timestamp": "2026-09-10T10:08:03.000Z",
     "message": {"id": "msg_8", "role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                 "usage": {"input_tokens": 2, "output_tokens": 5, "cache_read_input_tokens": 23500,
                           "cache_creation_input_tokens": 0},
                 "content": [{"type": "text", "text": "Renamed."}]}},
]


class TestNamedLocks(Tmp):
    """The sniffer holds a lock for its whole life so another sniffer cannot start, but it must not
    hold the capture lock, which would block every manual import for as long as it runs."""

    def test_two_different_names_do_not_block_each_other(self):
        with CaptureLock(self.tmp, name="sniffer"):
            with CaptureLock(self.tmp):          # the capture lock is still free
                pass

    def test_the_same_name_still_refuses_a_second_holder(self):
        with CaptureLock(self.tmp, name="sniffer"):
            with self.assertRaises(CaptureLocked):
                with CaptureLock(self.tmp, name="sniffer"):
                    pass

    def test_the_default_name_is_the_capture_lock(self):
        with CaptureLock(self.tmp):
            with self.assertRaises(CaptureLocked):
                with CaptureLock(self.tmp):
                    pass


class TestSwapInSurvivesAStaleLeftover(Tmp):
    def test_a_leftover_old_dir_from_a_dead_process_does_not_poison_the_run(self):
        """`_swap_in` clears <run>.old-<pid> after the swap but never before.

        If a process dies between the two replaces, that directory survives. A later process with a
        recycled pid then fails `os.replace(final, old)` with "Directory not empty" -- and because
        the pid is in the name, it fails on EVERY subsequent backfill, so the run can never be
        regenerated until someone deletes the stray directory by hand.
        """
        from harnesslab.capture.regen import _swap_in
        final = os.path.join(self.tmp, "run-1")
        tmp_dir = os.path.join(self.tmp, "run-1.tmp")
        os.makedirs(final); os.makedirs(tmp_dir)
        with open(os.path.join(final, "old.txt"), "w") as f:
            f.write("previous")
        with open(os.path.join(tmp_dir, "new.txt"), "w") as f:
            f.write("fresh")
        stale = f"{final}.old-{os.getpid()}"                 # what a dead predecessor left behind
        os.makedirs(stale)
        with open(os.path.join(stale, "junk.txt"), "w") as f:
            f.write("not empty")

        _swap_in(tmp_dir, final)

        self.assertTrue(os.path.exists(os.path.join(final, "new.txt")), "the swap did not happen")
        self.assertFalse(os.path.isdir(stale), "the stale directory was left to poison the next pass")


class TestAScopedPassLeavesOtherSourcesAlone(Tmp):
    """`--path` is a documented, repeatable flag, so a pass can legitimately cover part of the corpus.

    index.jsonl is merged across passes. inflight.json and identity.json were rewritten wholesale
    from whatever the pass happened to see, so a scoped run silently discarded the open runs and the
    resume lineage of every source it did not rescan -- and the module docstring's own reason for
    keeping identity.json is that a resume found later changes a run already indexed.
    """

    def setUp(self):
        super().setUp()
        self.runs = os.path.join(self.tmp, "runs")
        self.a = os.path.join(self.tmp, "sessions", "a")
        self.b = os.path.join(self.tmp, "sessions", "b")
        os.makedirs(self.a); os.makedirs(self.b)
        copy_fixture(os.path.join(self.a, "sess-cap-1.jsonl"))
        copy_fixture(os.path.join(self.b, "sess-cap-2.jsonl"))
        self.open_now = identity.parse_ts("2026-09-10T10:10:00Z")
        self.captured = os.path.join(self.runs, "captured")

    def load(self, name):
        with open(os.path.join(self.captured, name), encoding="utf-8") as f:
            return json.load(f)

    def test_a_scoped_pass_keeps_the_inflight_runs_of_sources_it_did_not_scan(self):
        full = backfill.run([self.a, self.b], self.runs, gap_s=1800, now=self.open_now)
        self.assertEqual(full["open"], 2)
        before = {r["run_id"] for r in self.load("inflight.json")["runs"]}
        self.assertEqual(len(before), 2)

        backfill.run([self.a], self.runs, gap_s=1800, now=self.open_now)   # --path a, only
        after = {r["run_id"] for r in self.load("inflight.json")["runs"]}
        self.assertEqual(after, before, "a scoped pass dropped an open run it never looked at")

    def test_a_scoped_pass_keeps_the_relations_of_sources_it_did_not_scan(self):
        copy_fixture(os.path.join(self.b, "sess-cap-2-resumed.jsonl"), RESUME_TAIL)
        closed = identity.parse_ts("2026-09-11T00:00:00Z")
        backfill.run([self.a, self.b], self.runs, gap_s=1800, now=closed)
        before = self.load("identity.json")["relations"]
        self.assertTrue(before, "the fixture should produce at least one relation")

        backfill.run([self.a], self.runs, gap_s=1800, now=closed)          # --path a, only
        after = self.load("identity.json")["relations"]
        for run_id, rel in before.items():
            self.assertIn(run_id, after, f"a scoped pass erased the lineage of {run_id}")
            self.assertEqual(after[run_id], rel)


class TestBackfill(Tmp):
    def setUp(self):
        super().setUp()
        self.runs = os.path.join(self.tmp, "runs")
        self.src = os.path.join(self.tmp, "sessions", "proj")
        os.makedirs(self.src)
        copy_fixture(os.path.join(self.src, "sess-cap-1.jsonl"))
        self.closed = identity.parse_ts("2026-09-11T00:00:00Z")
        self.still_open = identity.parse_ts("2026-09-10T10:10:00Z")
        self.captured = os.path.join(self.runs, "captured")

    def index_rows(self):
        p = os.path.join(self.captured, "index.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def load(self, name):
        with open(os.path.join(self.captured, name), encoding="utf-8") as f:
            return json.load(f)

    def test_a_failing_archive_is_an_error_not_fatal(self):
        from harnesslab.capture import archive as archive_mod
        with mock.patch.object(archive_mod, "archive_source", side_effect=FileNotFoundError("gone")):
            rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed,
                               archive_root=os.path.join(self.tmp, "arch"))
        self.assertEqual(rep["runs"], 1)
        self.assertEqual(len(rep["errors"]), 1)
        self.assertIn("FileNotFoundError", rep["errors"][0]["error"])
        self.assertEqual(len(self.index_rows()), 1)

    def test_a_closed_runs_index_row_is_refreshed_when_its_content_grows(self):
        backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        (before,) = self.index_rows()
        with open(os.path.join(self.src, "sess-cap-1.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "uuid": "r-0006", "parentUuid": "a-0008", "isSidechain": False,
                                "sessionId": "sess-cap-1", "cwd": "/work/demo", "version": "2.1.3",
                                "timestamp": "2026-09-10T10:07:20.000Z",
                                "message": {"role": "user", "content": [{"type": "tool_result",
                                            "tool_use_id": "toolu_9", "content": "ok", "is_error": False}]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "uuid": "a-0011", "parentUuid": "r-0006", "isSidechain": False,
                                "sessionId": "sess-cap-1", "cwd": "/work/demo", "version": "2.1.3", "requestId": "req_9",
                                "timestamp": "2026-09-10T10:07:25.000Z",
                                "message": {"id": "msg_9", "role": "assistant", "model": "claude-opus-5",
                                            "stop_reason": "end_turn",
                                            "usage": {"input_tokens": 2, "output_tokens": 50,
                                                      "cache_read_input_tokens": 100,
                                                      "cache_creation_input_tokens": 0},
                                            "content": [{"type": "text", "text": "More work."}]}}) + "\n")
        rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        rows = self.index_rows()
        self.assertEqual(len(rows), 1)
        with open(os.path.join(self.captured, rows[0]["run_id"], "summary.json"), encoding="utf-8") as f:
            summary = json.load(f)
        self.assertEqual(rows[0]["steps"], summary["steps"])
        self.assertGreater(rows[0]["steps"], before["steps"])
        self.assertEqual(rep["closed_updated"], 1)

    def test_the_same_session_found_under_two_roots_is_indexed_once(self):
        """run_id hashes source|session_id|task_id|basename, not the directory, so the same session
        file living under two project dirs is one run. Seen live: 14 such pairs in a real backfill."""
        other = os.path.join(self.tmp, "sessions", "proj-copy")
        os.makedirs(other)
        copy_fixture(os.path.join(other, "sess-cap-1.jsonl"))
        rep = backfill.run([self.src, other], self.runs, gap_s=1800, now=self.closed)
        rows = self.index_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len({r["run_id"] for r in rows}), 1)
        self.assertEqual((rep["closed_new"], rep["closed_updated"]), (1, 1))

    def test_closed_runs_are_indexed_exactly_once(self):
        first = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        second = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual((first["closed_new"], second["closed_new"]), (1, 0))
        self.assertEqual(len(self.index_rows()), 1)
        self.assertEqual(self.index_rows()[0]["root_uuid"], "u-0001")
        self.assertEqual(self.load("inflight.json"), {"runs": []})

    def test_open_runs_go_to_inflight_not_the_index(self):
        rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.still_open)
        self.assertEqual((rep["open"], rep["closed_new"]), (1, 0))
        self.assertEqual(self.index_rows(), [])
        self.assertEqual(len(self.load("inflight.json")["runs"]), 1)

    def test_a_resumed_copy_supersedes_its_parent(self):
        copy_fixture(os.path.join(self.src, "sess-cap-2.jsonl"), RESUME_TAIL)
        rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual((rep["runs"], rep["superseded"], rep["forks"]), (2, 1, 0))
        rows = {os.path.basename(r["run_id"]): r for r in self.index_rows()}
        self.assertEqual(len(rows), 2)
        rel = self.load("identity.json")["relations"]
        (parent,) = [rid for rid, v in rel.items() if v["superseded_by"]]
        (child,) = rel[parent]["superseded_by"]
        self.assertEqual(rel[child]["supersedes"], [parent])

    def test_the_report_carries_real_totals(self):
        rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual((rep["input_tokens"], rep["output_tokens"]), (13, 300))
        self.assertEqual((rep["cache_read_input_tokens"], rep["cache_creation_input_tokens"]), (130000, 1500))
        self.assertEqual((rep["lines_added"], rep["lines_removed"]), (1, 1))
        self.assertEqual(rep["models"], {"claude-opus-5": 1})
        self.assertEqual(rep["unpriced_models"], [])
        self.assertGreater(rep["cost_usd"], 0)

    def test_one_bad_file_does_not_stop_the_others(self):
        copy_fixture(os.path.join(self.src, "other.jsonl"))
        real = regen.regenerate

        def flaky(path, out_root, gap_s, adapter="claude_code"):
            if path.endswith("other.jsonl"):
                raise ValueError("boom")
            return real(path, out_root, gap_s, adapter=adapter)

        with mock.patch.object(backfill, "regenerate", side_effect=flaky):
            rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual(len(rep["errors"]), 1)
        self.assertIn("boom", rep["errors"][0]["error"])
        self.assertEqual(rep["runs"], 1)

    def test_it_takes_the_capture_lock(self):
        with CaptureLock(self.runs):
            with self.assertRaises(CaptureLocked):
                backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)

    def test_a_symlink_out_of_the_allow_list_is_ignored(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        copy_fixture(os.path.join(outside, "leak.jsonl"))
        os.symlink(os.path.join(outside, "leak.jsonl"), os.path.join(self.src, "leak.jsonl"))
        rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual(rep["files"], 1)

    def test_archives_sources_when_asked(self):
        archive_root = os.path.join(self.tmp, "archive")
        rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed, archive_root=archive_root)
        self.assertEqual(rep["archived"], 1)

    def test_an_unreadable_file_is_an_error_not_a_crash(self):
        bad = copy_fixture(os.path.join(self.src, "unreadable.jsonl"))
        os.chmod(bad, 0)
        self.addCleanup(os.chmod, bad, 0o600)
        rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual(rep["runs"], 1)
        self.assertEqual([e["path"] for e in rep["errors"]], [os.path.realpath(bad)])

    def test_a_torn_index_line_is_repaired_not_duplicated(self):
        backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        p = os.path.join(self.captured, "index.jsonl")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        with open(p, "w", encoding="utf-8") as f:
            f.write(text[: len(text) // 2])          # an interrupted write: half a row, no newline
        backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual(len(self.index_rows()), 1)

    def test_source_files_tags_each_file_with_its_adapter(self):
        root = os.path.join(self.tmp, "roots")
        os.makedirs(root)
        shutil.copy(FIX, os.path.join(root, "s1.jsonl"))
        files, errors = backfill.source_files([root])
        self.assertEqual(errors, [])
        self.assertEqual([(os.path.basename(p), a) for p, a in files],
                         [("s1.jsonl", "claude_code")])

    def test_the_report_counts_files_per_source(self):
        root = os.path.join(self.tmp, "roots2")
        os.makedirs(root)
        shutil.copy(FIX, os.path.join(root, "s1.jsonl"))
        rep = backfill.run([root], os.path.join(self.tmp, "lab2", "runs"),
                           gap_s=1800, now=4102444800.0)
        self.assertEqual(rep["by_source"], {"claude_code": 1})
        self.assertEqual(rep["files"], 1)
        self.assertEqual(rep["errors"], [])

    def test_a_run_with_no_parseable_timestamp_stays_open(self):
        with mock.patch.object(identity, "parse_ts", return_value=None):
            rep = backfill.run([self.src], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual((rep["open"], rep["closed_new"]), (1, 0))


class TestCli(Tmp):
    def cli(self, *args, env_extra=None):
        env = dict(os.environ, **(env_extra or {}))
        return subprocess.run([sys.executable, "-m", "harnesslab.capture", *args], cwd=LAB, env=env,
                              capture_output=True, text=True, timeout=120)

    def test_backfill_writes_into_the_given_lab(self):
        lab = os.path.join(self.tmp, "lab")
        for d in ("harnesses", "tasks", os.path.join("data", "runs")):
            os.makedirs(os.path.join(lab, d), exist_ok=True)
        src = os.path.join(self.tmp, "sessions")
        os.makedirs(src)
        copy_fixture(os.path.join(src, "sess-cap-1.jsonl"))
        p = self.cli("--backfill", "--path", src, "--lab", lab, "--no-archive")
        self.assertEqual(p.returncode, 0, p.stderr)
        report = json.loads(p.stdout)
        self.assertEqual(report["runs"], 1)
        self.assertEqual(report["lab_runs_root"], os.path.realpath(os.path.join(lab, "data", "runs")))
        self.assertTrue(os.path.exists(os.path.join(lab, "data", "runs", "captured", "index.jsonl")))

    def test_the_printed_report_does_not_list_source_paths(self):
        """report["sources"] exists for the sniffer. Printed, it is ~19,380 absolute session paths and
        their run ids on this machine -- the content presence.py goes out of its way to keep out."""
        lab = os.path.join(self.tmp, "lab")
        for d in ("harnesses", "tasks", os.path.join("data", "runs")):
            os.makedirs(os.path.join(lab, d), exist_ok=True)
        src = os.path.join(self.tmp, "sessions")
        os.makedirs(src)
        copy_fixture(os.path.join(src, "sess-cap-1.jsonl"))
        p = self.cli("--backfill", "--path", src, "--lab", lab, "--no-archive")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("sources", json.loads(p.stdout))
        self.assertNotIn("sess-cap-1.jsonl", p.stdout)

    def test_sigterm_stops_the_watcher_cleanly(self):
        """SIGTERM is how launchd, systemd and a plain `kill` ask a job to stop. With no handler,
        Python dies without running the loop's cleanup, and the page reports a crash."""
        import signal, time as _t
        from harnesslab.capture import presence
        lab = os.path.join(self.tmp, "lab")
        for d in ("harnesses", "tasks", os.path.join("data", "runs")):
            os.makedirs(os.path.join(lab, d), exist_ok=True)
        src = os.path.join(self.tmp, "sessions")
        os.makedirs(src)
        copy_fixture(os.path.join(src, "sess-cap-1.jsonl"))
        runs = os.path.join(lab, "data", "runs")
        proc = subprocess.Popen([sys.executable, "-m", "harnesslab.capture", "--watch", "--interval", "1",
                                 "--lab", lab, "--path", src, "--no-archive"],
                                cwd=LAB, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = _t.time() + 60
            while _t.time() < deadline and presence.read(runs)["state"] != "idle":
                _t.sleep(0.2)
            self.assertEqual(presence.read(runs)["state"], "idle", "the watcher never came up")
            proc.send_signal(signal.SIGTERM)
            code = proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.assertEqual(code, 0)
        self.assertEqual(presence.read(runs)["state"], "stopped",
                         "a requested stop was left looking like a crash")

    def test_seed_cursors_then_watch_reads_nothing_already_captured(self):
        lab = os.path.join(self.tmp, "lab")
        for d in ("harnesses", "tasks", os.path.join("data", "runs")):
            os.makedirs(os.path.join(lab, d), exist_ok=True)
        src = os.path.join(self.tmp, "sessions")
        os.makedirs(src)
        f = copy_fixture(os.path.join(src, "sess-cap-1.jsonl"))
        quiet = os.stat(f).st_mtime_ns - 3600 * 10 ** 9      # a session nobody has touched for an hour
        os.utime(f, ns=(quiet, quiet))
        self.assertEqual(self.cli("--backfill", "--path", src, "--lab", lab, "--no-archive").returncode, 0)
        p = self.cli("--seed-cursors", "--path", src, "--lab", lab)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["seeded"], 1)

    def test_nothing_to_capture_exits_2(self):
        p = self.cli("--backfill", env_extra={"HARNESSLAB_CAPTURE_CONFIG": os.path.join(self.tmp, "none.json")})
        self.assertEqual(p.returncode, 2)
        self.assertIn("nothing to capture", p.stderr)

    def test_without_backfill_prints_help_and_exits_2(self):
        p = self.cli()
        self.assertEqual(p.returncode, 2)
        self.assertIn("--backfill", p.stdout)


class TestALockedFolderIsNotACaptureError(TestBackfill):
    """A capture is about the files it CAN read. One locked folder under a home directory must not
    put the watcher at exit 1 on every pass, or leave presence.errors nonzero on the Capture page --
    while the orphan prune, where an unreadable directory is exactly what must stop a deletion, still
    sees it (orphans.find asks adapters.discover directly)."""

    def locked_dir(self):
        shut = os.path.join(os.path.dirname(self.src), "locked-away")
        os.makedirs(shut)
        copy_fixture(os.path.join(shut, "other.jsonl"))
        os.chmod(shut, 0o000)
        self.addCleanup(os.chmod, shut, 0o755)
        if os.access(shut, os.R_OK):
            self.skipTest("running as a user that ignores directory permissions")
        return shut

    def test_the_backfill_reports_no_error_for_it(self):
        self.locked_dir()
        rep = backfill.run([os.path.dirname(self.src)], self.runs, gap_s=1800, now=self.closed)
        self.assertEqual(rep["errors"], [])
        self.assertGreaterEqual(rep["runs"], 1, "and it still captured what it could read")

    def test_the_prune_still_sees_it(self):
        from harnesslab.capture import adapters
        self.locked_dir()
        _found, errors = adapters.discover([os.path.dirname(self.src)])
        self.assertTrue(any(e.get("kind") == "directory" for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
