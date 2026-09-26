"""Orphan runs: runs that no source produces any more.

A source rewritten to hold fewer segments leaves the dropped segments' directories and index rows
behind, and a deleted source leaves all of its runs; a stale index row is counted by every metric and
cost aggregate. Orphans are found from the cursors plus a fresh read of the sources, never guessed,
and removed only with apply -- directory, index row, sidecar entries and cursor memory together.

Every test builds its own lab in a temp directory. Nothing here reads a real lab.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.join(LAB, "tests_agentlab"))

from test_capture_backfill import RESUME_TAIL, RESUMED_TAIL, copy_fixture   # noqa: E402
from harnesslab.capture import cursors, orphans, presence, sniffer, sweep   # noqa: E402
from harnesslab.capture.lock import CaptureLock                             # noqa: E402

CLOSED = 1.8e9
GAP = 1800
NOW = 2.0e9
OLD = NOW - 7 * 24 * 3600          # a week before NOW: far past the sweep's safety age


class Lab(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-orphans-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        self.cap = os.path.join(self.runs, "captured")
        self.root = os.path.realpath(os.path.join(self.tmp, "sessions"))
        self.proj = os.path.join(self.root, "proj")
        os.makedirs(self.proj)

    def tick(self):
        return sniffer.tick([self.root], self.runs, gap_s=GAP, now=CLOSED)

    def find(self, **kw):
        return orphans.prune([self.root], self.runs, gap_s=kw.pop("gap_s", GAP), now=CLOSED, **kw)

    def index_ids(self):
        with open(os.path.join(self.cap, "index.jsonl"), encoding="utf-8") as f:
            return [json.loads(line)["run_id"] for line in f if line.strip()]

    def cursor_runs(self):
        return {rid for c in cursors.load(cursors.path_for(self.runs)).values()
                for rid in (c.get("runs") or []) + (c.get("dropped") or [])}

    def rewrite(self, path, extra=()):
        """Rewrite a source with different content, and make sure its (size, mtime) moved."""
        copy_fixture(path, extra)
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


class TestCursorsRememberDroppedRuns(Lab):
    def test_a_shrunk_sources_cursor_keeps_what_it_used_to_produce(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        (c,) = cursors.load(cursors.path_for(self.runs)).values()
        base, s1 = sorted(c["runs"])
        self.rewrite(src)                                   # the second segment is gone
        self.tick()
        (c,) = cursors.load(cursors.path_for(self.runs)).values()
        self.assertEqual(c["runs"], [base])
        self.assertEqual(c["dropped"], [s1], "the cursor forgot the segment its source no longer produces")
        self.assertNotIn(s1, cursors.claimed({"x": c}), "a dropped run is not claimed")

    def test_a_dropped_run_that_comes_back_is_no_longer_dropped(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        self.rewrite(src)
        self.tick()
        self.rewrite(src, RESUMED_TAIL)
        self.tick()
        (c,) = cursors.load(cursors.path_for(self.runs)).values()
        self.assertEqual(c["dropped"], [])
        self.assertEqual(len(c["runs"]), 2)


class TestShrunkSource(Lab):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(self.src, RESUMED_TAIL)
        self.tick()
        self.base, self.s1 = sorted(self.index_ids())
        self.rewrite(self.src)
        self.tick()

    def test_the_dropped_segment_is_reported_and_report_only_removes_nothing(self):
        self.assertIn(self.s1, self.index_ids(), "precondition: the known limitation leaves the row")
        rep = self.find()
        self.assertFalse(rep["apply"])
        self.assertEqual([o["run_id"] for o in rep["orphans"]], [self.s1])
        self.assertEqual(rep["orphans"][0]["reason"], "segment_dropped")
        self.assertTrue(os.path.isdir(os.path.join(self.cap, self.s1)))
        self.assertIn(self.s1, self.index_ids())

    def test_apply_removes_the_directory_the_row_and_the_cursor_memory(self):
        before = presence.read(self.runs)["totals"]["runs_indexed"]
        rep = self.find(apply=True)
        self.assertEqual(rep["counts"]["removed"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.cap, self.s1)))
        self.assertEqual(self.index_ids(), [self.base])
        self.assertTrue(os.path.isdir(os.path.join(self.cap, self.base)), "a live run was removed")
        self.assertNotIn(self.s1, self.cursor_runs())
        self.assertEqual(presence.read(self.runs)["totals"]["runs_indexed"], before - 1,
                         "presence still counts the pruned run")
        self.assertEqual(self.find()["orphans"], [], "a second pass still finds it")

    def test_nothing_is_removed_while_a_capture_writer_holds_the_lock(self):
        with CaptureLock(self.runs):
            rep = self.find(apply=True)
        self.assertTrue(rep["locked"])
        self.assertTrue(os.path.isdir(os.path.join(self.cap, self.s1)))
        self.assertIn(self.s1, self.index_ids())

    def test_nothing_is_removed_while_a_watcher_runs(self):
        with CaptureLock(self.runs, name="sniffer"):
            rep = self.find(apply=True)
        self.assertTrue(rep["locked"])
        self.assertTrue(os.path.isdir(os.path.join(self.cap, self.s1)))

    def test_a_run_directory_that_belongs_to_another_source_is_refused(self):
        ledger = os.path.join(self.cap, self.s1, "ledger.jsonl")
        with open(ledger, encoding="utf-8") as f:
            rows = f.readlines()
        start = json.loads(rows[0])
        start["source_path"] = "somebody-else.jsonl"
        rows[0] = json.dumps(start) + "\n"
        with open(ledger, "w", encoding="utf-8") as f:
            f.writelines(rows)
        rep = self.find(apply=True)
        self.assertEqual(rep["orphans"], [])
        self.assertEqual([r["reason"] for r in rep["refused"]], ["foreign_directory"])
        self.assertTrue(os.path.isdir(os.path.join(self.cap, self.s1)))


class TestAReadThatDisagreesWithTheCaptureIsNotEvidence(Lab):
    def test_a_different_gap_is_not_evidence_of_shrinkage(self):
        """Segments depend on the gap. An unchanged source read with another gap names other run ids
        -- that says the READ differs from the capture, not that the source shrank. A run the cursor
        says the source still produces is never pruned on a read alone."""
        copy_fixture(os.path.join(self.proj, "s.jsonl"), RESUMED_TAIL)
        self.tick()
        ids = self.index_ids()
        self.assertEqual(len(ids), 2)
        rep = self.find(gap_s=10 ** 9, apply=True)
        self.assertEqual(rep["orphans"], [])
        # The cursor records the gap it was cut at, so the whole pass is refused before any source is
        # re-read; before that record existed this came back one source at a time as `inconsistent`.
        self.assertEqual([r["reason"] for r in rep["refused"]], ["gap_mismatch"])
        self.assertEqual(rep["gap_mismatch"], [float(GAP)])
        for rid in ids:
            self.assertTrue(os.path.isdir(os.path.join(self.cap, rid)))
        self.assertEqual(self.index_ids(), ids)


class TestDeletedSource(Lab):
    def setUp(self):
        super().setUp()
        self.parent = os.path.join(self.proj, "sess-cap-1.jsonl")
        self.resume = os.path.join(self.proj, "sess-cap-2.jsonl")
        copy_fixture(self.parent)
        copy_fixture(self.resume, RESUME_TAIL)
        self.tick()
        cs = cursors.load(cursors.path_for(self.runs))
        self.resume_runs = cs[os.path.realpath(self.resume)]["runs"]
        self.parent_runs = cs[os.path.realpath(self.parent)]["runs"]

    def test_a_deleted_sources_runs_are_orphans_and_apply_cleans_every_sidecar(self):
        with open(os.path.join(self.cap, "identity.json"), encoding="utf-8") as f:
            rel = json.load(f)["relations"]
        self.assertTrue(any(rid in json.dumps(rel) for rid in self.resume_runs),
                        "precondition: the resume is related to its parent")
        os.remove(self.resume)
        rep = self.find()
        self.assertEqual(sorted(o["run_id"] for o in rep["orphans"]), sorted(self.resume_runs))
        self.assertEqual({o["reason"] for o in rep["orphans"]}, {"source_deleted"})

        self.find(apply=True, deleted_sources=True)
        for rid in self.resume_runs:
            self.assertFalse(os.path.exists(os.path.join(self.cap, rid)))
            self.assertNotIn(rid, self.index_ids())
        with open(os.path.join(self.cap, "identity.json"), encoding="utf-8") as f:
            text = f.read()
        for rid in self.resume_runs:
            self.assertNotIn(rid, text, "identity.json still relates a pruned run")
        cs = cursors.load(cursors.path_for(self.runs))
        self.assertNotIn(os.path.realpath(self.resume), cs, "the deleted source's cursor survived")
        self.assertIn(os.path.realpath(self.parent), cs)
        for rid in self.parent_runs:
            self.assertTrue(os.path.isdir(os.path.join(self.cap, rid)))

    def test_a_deleted_project_directory_under_a_present_root_is_deletion(self):
        os.makedirs(os.path.join(self.root, "another-project"))   # the root still lists something
        shutil.rmtree(self.proj)
        rep = self.find()
        self.assertEqual(len(rep["orphans"]), len(self.resume_runs) + len(self.parent_runs))

    def test_a_deleted_source_is_reported_but_a_plain_apply_removes_nothing(self):
        """Claude Code deletes session files after 30 days (archive.py). A capture plus its archived
        source is then the only record of that run: the deletion is expected, and never on its own a
        reason to delete the corpus. Removal needs an explicit `deleted_sources`."""
        os.remove(self.resume)
        rep = self.find(apply=True)
        self.assertEqual(sorted(o["run_id"] for o in rep["orphans"]), sorted(self.resume_runs))
        self.assertEqual({o["reason"] for o in rep["orphans"]}, {"source_deleted"})
        self.assertEqual({o["removable"] for o in rep["orphans"]}, {False})
        self.assertEqual(rep["counts"]["removed"], 0)
        self.assertEqual(rep["counts"]["rows_removed"], 0)
        self.assertEqual(rep["counts"]["kept"], len(self.resume_runs))
        for rid in self.resume_runs:
            self.assertTrue(os.path.isdir(os.path.join(self.cap, rid)), "a deleted source cost the capture")
            self.assertIn(rid, self.index_ids())
            self.assertIn(rid, self.cursor_runs())

    def test_an_open_orphan_leaves_inflight_json_too(self):
        path = os.path.join(self.cap, "inflight.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"runs": [{"run_id": rid} for rid in self.resume_runs + self.parent_runs]}, f)
        os.remove(self.resume)
        self.find(apply=True, deleted_sources=True)
        with open(path, encoding="utf-8") as f:
            left = [r["run_id"] for r in json.load(f)["runs"]]
        self.assertEqual(left, self.parent_runs)

    def test_a_run_another_source_still_produces_is_not_an_orphan(self):
        """The same session file under two directories is one run id; losing one copy loses nothing."""
        twin_dir = os.path.join(self.root, "other")
        os.makedirs(twin_dir)
        shutil.copy(self.resume, os.path.join(twin_dir, "sess-cap-2.jsonl"))
        self.tick()
        os.remove(self.resume)
        rep = self.find(apply=True)
        self.assertEqual(rep["orphans"], [])
        for rid in self.resume_runs:
            self.assertTrue(os.path.isdir(os.path.join(self.cap, rid)))


    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads everything")
    def test_a_run_an_unreadable_source_still_names_is_not_an_orphan(self):
        """The twin may still produce it; nobody can tell while it cannot be read."""
        twin_dir = os.path.join(self.root, "other")
        os.makedirs(twin_dir)
        twin = os.path.join(twin_dir, "sess-cap-2.jsonl")
        shutil.copy(self.resume, twin)
        self.tick()
        os.remove(self.resume)
        os.chmod(twin, 0)
        self.addCleanup(os.chmod, twin, 0o644)
        rep = self.find(apply=True)
        self.assertEqual(rep["orphans"], [])
        for rid in self.resume_runs:
            self.assertTrue(os.path.isdir(os.path.join(self.cap, rid)))
            self.assertIn(rid, self.index_ids())


class TestASourceThatMovedIsNotASourceThatWentAway(Lab):
    """A run id is sha256(source|session_id|task_id|basename): the same file at another path under
    the roots produces the SAME runs. Only the cursored path is gone, and a fresh read has to cover
    every readable source under the roots, not only the ones a cursor names."""

    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(self.src, RESUMED_TAIL)
        self.tick()
        self.run_ids = self.index_ids()
        self.assertEqual(len(self.run_ids), 2)
        self.elsewhere = os.path.join(self.root, "renamed-project")
        os.makedirs(self.elsewhere)
        self.moved = os.path.join(self.elsewhere, "s.jsonl")
        with open(os.path.join(self.proj, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("not a session\n")            # the old directory still lists: the move is a deletion there

    def assertNothingPruned(self, rep):
        self.assertEqual([o for o in rep["orphans"] if o["removable"]], [])
        self.assertEqual(rep["counts"]["removed"], 0)
        self.assertEqual(rep["counts"]["rows_removed"], 0)
        for rid in self.run_ids:
            self.assertTrue(os.path.isdir(os.path.join(self.cap, rid)))
        self.assertEqual(self.index_ids(), self.run_ids)

    def test_a_source_moved_to_an_uncursored_path_still_produces_its_runs(self):
        shutil.move(self.src, self.moved)
        rep = self.find(apply=True, deleted_sources=True)
        self.assertNothingPruned(rep)
        self.assertEqual(rep["orphans"], [], "the moved source's runs were called orphans")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads everything")
    def test_a_moved_source_that_cannot_be_read_is_not_evidence_either(self):
        shutil.move(self.src, self.moved)
        os.chmod(self.moved, 0)
        self.addCleanup(os.chmod, self.moved, 0o644)
        rep = self.find(apply=True, deleted_sources=True)
        self.assertNothingPruned(rep)
        self.assertIn("unreadable", [r["reason"] for r in rep["refused"]])


class TestPruneLeavesNoScratchThatCanRestoreTheRun(Lab):
    """sweep.py restores a `<run>.old-<pid>/` whose `<run>/` is missing. A prune that removes the
    run and leaves its crash scratch behind hands the sweep a run to resurrect -- with no index row,
    no cursor memory and nothing that can ever find it again."""

    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(self.src, RESUMED_TAIL)
        self.tick()
        self.base, self.s1 = sorted(self.index_ids())
        self.old = os.path.join(self.cap, f"{self.s1}.old-999999")
        self.tmp_dir = os.path.join(self.cap, f"{self.s1}.tmp-999999")
        for path in (self.old, self.tmp_dir):
            shutil.copytree(os.path.join(self.cap, self.s1), path)
            for d, _, files in os.walk(path):
                for f in files:
                    os.utime(os.path.join(d, f), (OLD, OLD))
                os.utime(d, (OLD, OLD))
        self.rewrite(self.src)                                    # the second segment is gone
        self.tick()

    def test_apply_removes_the_runs_crash_scratch_with_it(self):
        rep = self.find(apply=True)
        self.assertEqual(rep["counts"]["removed"], 1)
        self.assertFalse(os.path.exists(self.old), "a crash copy of the pruned run survived")
        self.assertFalse(os.path.exists(self.tmp_dir))
        srep = sweep.sweep(self.runs, apply=True, now=NOW)
        self.assertEqual(srep["counts"]["restored"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.cap, self.s1)),
                         "the sweep restored a run the prune removed")
        self.assertTrue(os.path.isdir(os.path.join(self.cap, self.base)))

    def test_scratch_that_belongs_to_a_live_run_is_left_alone(self):
        base_old = os.path.join(self.cap, f"{self.base}.old-999999")
        shutil.copytree(os.path.join(self.cap, self.base), base_old)
        self.find(apply=True)
        self.assertTrue(os.path.isdir(base_old), "the prune removed a live run's scratch")


class TestUnreadableIsNotDeleted(Lab):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.proj, "sess-cap-1.jsonl")
        copy_fixture(self.src)
        self.tick()
        self.run_ids = self.index_ids()

    def assertNothingPruned(self, rep):
        self.assertEqual(rep["orphans"], [])
        for rid in self.run_ids:
            self.assertTrue(os.path.isdir(os.path.join(self.cap, rid)))
        self.assertEqual(self.index_ids(), self.run_ids)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads everything")
    def test_a_source_without_read_permission_is_not_an_orphan(self):
        os.chmod(self.src, 0)
        self.addCleanup(os.chmod, self.src, 0o644)
        rep = self.find(apply=True)
        self.assertNothingPruned(rep)
        self.assertEqual([r["reason"] for r in rep["refused"]], ["unreadable"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads everything")
    def test_a_directory_that_cannot_be_listed_is_not_proof_of_deletion(self):
        os.remove(self.src)
        os.chmod(self.proj, 0)
        self.addCleanup(os.chmod, self.proj, 0o755)
        rep = self.find(apply=True)
        self.assertNothingPruned(rep)
        self.assertEqual([r["reason"] for r in rep["refused"]], ["unreachable"])

    def test_a_root_that_is_offline_is_not_proof_of_deletion(self):
        """A network volume that is not mounted makes every path under it vanish at once."""
        shutil.move(self.root, self.root + "-unmounted")
        self.addCleanup(shutil.move, self.root + "-unmounted", self.root)
        rep = self.find(apply=True)
        self.assertNothingPruned(rep)
        self.assertEqual([r["reason"] for r in rep["refused"]], ["unreachable"])

    def test_a_dangling_symlinked_directory_is_not_proof_of_deletion(self):
        volume = os.path.join(self.tmp, "volume")
        shutil.move(self.proj, volume)
        os.symlink(volume, self.proj)
        cs = cursors.load(cursors.path_for(self.runs))
        # The cursor was recorded through the realpath before the move; point it through the link,
        # as it would be had the volume always been mounted there.
        c = cs.pop(os.path.realpath(os.path.join(volume, "sess-cap-1.jsonl")), None) or cs.pop(self.src)
        cs[self.src] = c
        cursors.save(cursors.path_for(self.runs), cs)
        os.rename(volume, volume + "-offline")
        self.addCleanup(os.rename, volume + "-offline", volume)
        rep = self.find(apply=True)
        self.assertNothingPruned(rep)
        self.assertEqual([r["reason"] for r in rep["refused"]], ["unreachable"])

    def test_an_empty_directory_where_the_source_lived_is_not_proof_of_deletion(self):
        """A volume mounted below the root and now offline leaves its mount point as an empty
        directory: the parent lists fine, the name is absent, and every source under it is 'gone'."""
        offline = self.proj + "-offline"
        shutil.move(self.proj, offline)
        os.makedirs(self.proj)
        self.addCleanup(shutil.rmtree, offline, True)
        rep = self.find(apply=True, deleted_sources=True)
        self.assertNothingPruned(rep)
        self.assertEqual([r["reason"] for r in rep["refused"]], ["unreachable"])

    def test_an_icloud_evicted_source_is_not_proof_of_deletion(self):
        """iCloud evicts a file by renaming it `.<name>.icloud`. The bytes come back on demand."""
        os.rename(self.src, os.path.join(self.proj, "." + os.path.basename(self.src) + ".icloud"))
        rep = self.find(apply=True, deleted_sources=True)
        self.assertNothingPruned(rep)
        self.assertEqual([r["reason"] for r in rep["refused"]], ["unreachable"])

    def test_a_source_outside_the_roots_given_is_not_judged(self):
        other = os.path.realpath(os.path.join(self.tmp, "elsewhere"))
        os.makedirs(other)
        rep = orphans.prune([other], self.runs, gap_s=GAP, now=CLOSED, apply=True)
        self.assertNothingPruned(rep)
        self.assertEqual([r["reason"] for r in rep["refused"]], ["out_of_scope"])



class TestReviewFixes(Lab):
    """Two sources, same basename: one shrank, the other is gone. The same run is claimed by both."""

    def two_claimants(self, first: str, second: str):
        a, b = os.path.join(self.root, first), os.path.join(self.root, second)
        os.makedirs(a), os.makedirs(b)
        src_a, src_b = os.path.join(a, "sess-cap-1.jsonl"), os.path.join(b, "sess-cap-1.jsonl")
        copy_fixture(src_a, RESUMED_TAIL)         # two segments: the second is droppable
        shutil.copy(src_a, src_b)
        with open(os.path.join(b, "notes.txt"), "w") as f:
            f.write("so the directory still lists something once the source goes\n")
        self.tick()
        self.rewrite(src_a)                       # a shrinks: its resumed run becomes `dropped`
        self.tick()
        cs = cursors.load(cursors.path_for(self.runs))
        shared = set(cs[os.path.realpath(src_a)]["dropped"]) & set(cs[os.path.realpath(src_b)]["runs"])
        self.assertTrue(shared, "precondition: one source dropped a run the other still claims")
        os.remove(src_b)                          # b is gone: it still claims every run it produced
        return sorted(shared)

    def test_the_more_protective_reason_wins_whatever_the_directories_are_called(self):
        """The reason decided which gate applied, and it was taken from whichever cursor path sorted
        first -- so a-proj/z-other deleted the run and z-proj/a-other kept it. Same evidence."""
        for first, second in (("a-proj", "z-other"), ("z-proj", "a-other")):
            with self.subTest(first=first):
                self.setUp()
                shared = self.two_claimants(first, second)
                rep = self.find()
                claimed = [o for o in rep["orphans"] if o["run_id"] in shared]
                self.assertTrue(claimed)
                for o in claimed:
                    self.assertEqual(o["reason"], "source_deleted")
                    self.assertFalse(o["removable"], "a plain apply must not remove a deleted source's run")

    def test_a_run_a_deleted_source_claims_survives_a_plain_apply(self):
        shared = self.two_claimants("a-proj", "z-other")
        self.find(apply=True)
        for rid in shared:
            self.assertTrue(os.path.isdir(os.path.join(self.cap, rid)), "deleted-source gate bypassed")
            self.assertIn(rid, self.index_ids())

    def test_a_cursored_source_that_cannot_be_read_blocks_its_name_everywhere(self):
        """unread_bases was filled in from the uncursored walk alone, so a cursored source that could
        not be read only blocked the runs its own cursor named."""
        shared = self.two_claimants("a-proj", "z-other")
        src_a = os.path.join(self.root, "a-proj", "sess-cap-1.jsonl")
        os.chmod(src_a, 0)
        self.addCleanup(os.chmod, src_a, 0o644)
        if os.access(src_a, os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        rep = self.find()
        self.assertEqual([o for o in rep["orphans"] if o["run_id"] in shared], [],
                         "a source of that name could not be read, so nothing of that name is an orphan")

    def test_a_cursored_source_that_cannot_be_reached_blocks_its_name(self):
        """A path that is gone but NOT provably gone -- an empty directory is what an unmounted volume
        leaves -- is `unreachable`: it may still hold what another, deleted source once produced. The
        walk past the cursors never sees it (there is no file to read), so only its cursor can block
        its name, and a cursor recorded before it produced anything names no runs of its own."""
        shared = self.two_claimants("a-proj", "z-other")
        quiet = os.path.join(self.root, "quiet-proj")
        os.makedirs(quiet)
        src = os.path.join(quiet, "sess-cap-1.jsonl")     # same basename, no runs recorded for it
        cs = cursors.load(cursors.path_for(self.runs))
        cs[os.path.realpath(src)] = {"size": 0, "mtime_ns": 0, "runs": [], "dropped": []}
        cursors.save(cursors.path_for(self.runs), cs)
        rep = self.find(deleted_sources=True)
        self.assertEqual([o for o in rep["orphans"] if o["run_id"] in shared], [],
                         "a source of that name could not be reached, so nothing of that name is an orphan")
        refused_ids = {rid for e in rep["refused"] for rid in e.get("run_ids", [])}
        self.assertTrue(set(shared) <= refused_ids, "kept, and said so")

    def test_refusals_name_the_runs_that_were_candidates_not_a_whole_foreign_corpus(self):
        """A cursor for a source outside the roots listed every one of its runs under `out_of_scope`,
        which read as a list of runs considered for deletion. None of them ever was."""
        outside = os.path.realpath(os.path.join(self.tmp, "elsewhere", "other.jsonl"))
        cs = cursors.load(cursors.path_for(self.runs))
        cs[outside] = {"size": 1, "mtime_ns": 1, "runs": ["run-out-1", "run-out-2"], "dropped": []}
        cursors.save(cursors.path_for(self.runs), cs)
        rep = self.find()
        listed = {rid for e in rep["refused"] for rid in e.get("run_ids", [])}
        self.assertEqual(listed & {"run-out-1", "run-out-2"}, set())

    def test_the_capture_lock_is_not_held_while_every_source_is_read(self):
        """find() re-reads the whole corpus; holding the capture lock across it blocks every manual
        import for as long as that takes. The lock is taken for the removal itself."""
        src = os.path.join(self.proj, "sess-cap-1.jsonl")
        copy_fixture(src, RESUMED_TAIL)               # two segments; the rewrite drops the second
        self.tick()
        self.rewrite(src)
        self.tick()
        held = []
        real = orphans.find
        def watched(*a, **k):
            try:
                CaptureLock(self.runs, name="").__enter__().__exit__(None, None, None)
                held.append(False)
            except Exception:
                held.append(True)
            return real(*a, **k)
        orphans.find = watched
        try:
            rep = self.find(apply=True)
        finally:
            orphans.find = real
        self.assertEqual(held, [False], "the capture lock was held while the corpus was re-read")
        self.assertGreaterEqual(rep["counts"]["removed"], 1, "and the prune still did its work")


class TestSecondReviewFixes(Lab):
    def shrunk(self):
        """A lab with exactly one orphan: a source that dropped its second segment."""
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        base, s1 = sorted(self.index_ids())
        self.rewrite(src)
        self.tick()
        return base, s1

    def test_a_directory_that_cannot_be_removed_does_not_take_the_others_index_rows_with_it(self):
        """The rows for the WHOLE batch were rewritten first and the directories deleted one by one,
        with nothing catching an OSError: one unremovable run left every other doomed run with no
        index row, no cursor update and no report -- and the process died with a traceback."""
        base, s1 = self.shrunk()
        run_dir = os.path.join(self.cap, s1)
        os.chmod(run_dir, 0o555)
        self.addCleanup(os.chmod, run_dir, 0o755)
        if os.access(run_dir, os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")
        rep = self.find(apply=True)
        self.assertEqual(rep["counts"]["removed"], 0)
        self.assertEqual(rep["counts"]["failed"], 1, rep["counts"])
        self.assertTrue(any(f["run_id"] == s1 for f in rep["failed"]))
        self.assertIn(base, self.index_ids(), "a live run lost its index row")
        self.assertIn(s1, self.index_ids(), "the row of a run still on disk was dropped anyway")
        self.assertIn(s1, self.cursor_runs(), "the cursor forgot a run that is still there")

    def test_a_run_that_could_not_be_removed_is_found_again_by_the_next_pass(self):
        base, s1 = self.shrunk()
        run_dir = os.path.join(self.cap, s1)
        os.chmod(run_dir, 0o555)
        if os.access(run_dir, os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")
        self.find(apply=True)
        os.chmod(run_dir, 0o755)
        rep = self.find(apply=True)
        self.assertEqual(rep["counts"]["removed"], 1, rep["counts"])
        self.assertNotIn(s1, self.index_ids())

    def test_a_locked_lab_does_not_report_orphans_as_removable(self):
        """Nothing was applied, but every orphan still read `removable: true` with kept 0 and
        removed 0 -- which a script reads as "these went"."""
        base, s1 = self.shrunk()
        with CaptureLock(self.runs, name="sniffer"):
            rep = self.find(apply=True)
        self.assertTrue(rep["locked"])
        self.assertFalse(rep["applied"])
        self.assertTrue(all(not o["removable"] for o in rep["orphans"]), "claimed removable while locked")
        self.assertIn(s1, self.index_ids(), "nothing was removed, which is the point")

    def test_a_refusal_that_named_no_candidate_is_not_a_refusal(self):
        """An empty refusal entry inflated counts.refused and, through it, the exit code: the CLI
        reported "a source failed" on a lab where no run was ever considered."""
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src)
        self.tick()
        outside = os.path.realpath(os.path.join(self.tmp, "elsewhere", "other.jsonl"))
        cs = cursors.load(cursors.path_for(self.runs))
        cs[outside] = {"size": 1, "mtime_ns": 1, "runs": ["run-out-1"], "dropped": []}
        cursors.save(cursors.path_for(self.runs), cs)
        rep = self.find()
        self.assertEqual([r["reason"] for r in rep["refused"]], ["out_of_scope"], "the source is recorded")
        self.assertEqual(rep["refused"][0]["run_ids"], [],
                         "but its runs were never candidates, and listing them says they were")


class TestThirdReviewFixes(Lab):
    def shrunk(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        base, s1 = sorted(self.index_ids())
        self.rewrite(src)
        self.tick()
        return base, s1

    def test_scratch_that_will_not_delete_does_not_abort_the_prune(self):
        """The run's own crash scratch was removed with an UNGUARDED rmtree, after the index had
        already been rewritten: one EACCES there and the cursors were never saved, so the next pass
        no longer knew the run had been pruned."""
        base, s1 = self.shrunk()
        scratch = os.path.join(self.cap, f"{s1}.old-999999")
        os.makedirs(os.path.join(scratch, "held"))
        with open(os.path.join(scratch, "held", "f"), "w") as f:
            f.write("x")
        os.chmod(os.path.join(scratch, "held"), 0o555)
        self.addCleanup(os.chmod, os.path.join(scratch, "held"), 0o755)
        if os.access(os.path.join(scratch, "held"), os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")
        rep = self.find(apply=True)                       # must not raise
        self.assertEqual(rep["counts"]["removed"], 1)
        self.assertNotIn(s1, self.index_ids(), "the run itself went")
        self.assertNotIn(s1, self.cursor_runs(), "and the cursor was saved")
        self.assertTrue(any("scratch" in f.get("what", "") for f in rep["failed"]), rep["failed"])

    def test_a_lab_that_cannot_be_locked_is_reported_not_raised(self):
        self.shrunk()
        os.chmod(self.cap, 0o555)
        self.addCleanup(os.chmod, self.cap, 0o755)
        if os.access(self.cap, os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")
        rep = self.find(apply=True)                       # must not raise
        self.assertTrue(rep["locked"])
        self.assertIn("lock_error", rep)


class TestFourthReviewFixes(Lab):
    def shrunk(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        base, s1 = sorted(self.index_ids())
        self.rewrite(src)
        self.tick()
        return base, s1

    def test_a_scratch_left_behind_by_a_failed_prune_is_never_restored_into_a_run(self):
        """sweep --apply restores a `<run>.old-<pid>/` whose run is missing -- it has no idea a prune
        put it there. That recreated a run with no index row and no cursor: invisible to every later
        prune, and to every aggregate, forever."""
        from harnesslab.capture import sweep as sw
        base, s1 = self.shrunk()
        scratch = os.path.join(self.cap, f"{s1}.old-999999")
        shutil.copytree(os.path.join(self.cap, s1), scratch)
        old = os.stat(scratch).st_mtime - 7 * 24 * 3600
        for d, _, files in os.walk(scratch):
            for f in files:
                os.utime(os.path.join(d, f), (old, old))
        os.utime(scratch, (old, old))
        # The scratch itself is read-only, so the prune cannot remove anything inside it and the
        # copy survives WHOLE -- ledger included, which is what makes a restore possible at all.
        os.chmod(scratch, 0o555)
        self.addCleanup(os.chmod, scratch, 0o755)
        if os.access(scratch, os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")
        self.find(apply=True)
        self.assertTrue(os.path.isfile(os.path.join(scratch, "ledger.jsonl")),
                        "precondition: a whole copy outlived the prune")
        self.assertFalse(os.path.exists(os.path.join(self.cap, s1)), "the run went")
        rep = sw.sweep(self.runs, apply=True, now=CLOSED + 30 * 24 * 3600)
        self.assertEqual(rep["counts"]["restored"], 0, "a pruned run was resurrected")
        self.assertFalse(os.path.exists(os.path.join(self.cap, s1)), "and it is still gone")

    def test_a_sidecar_that_cannot_be_written_is_reported_not_raised(self):
        """The index, inflight and identity writes come AFTER the directories are gone and were
        unguarded: an ENOSPC or EIO there lost the cursor save and the report with it."""
        import harnesslab.capture.orphans as orph
        base, s1 = self.shrunk()
        real = orph._atomic_write
        def failing(path, text):
            if path.endswith("index.jsonl"):
                raise OSError(28, "No space left on device")
            return real(path, text)
        orph._atomic_write = failing
        try:
            rep = self.find(apply=True)              # must not raise
        finally:
            orph._atomic_write = real
        self.assertTrue(rep["failed"], "nothing said the index could not be rewritten")
        self.assertNotIn(s1, self.cursor_runs(), "the cursor save still happened")


class TestAnUnreadableDirectoryHasAWayForward(Lab):
    def shrunk_with_a_locked_folder(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        base, s1 = sorted(self.index_ids())
        self.rewrite(src)
        self.tick()
        shut = os.path.join(self.root, "locked-away")
        os.makedirs(shut)
        copy_fixture(os.path.join(shut, "other.jsonl"))
        os.chmod(shut, 0o000)
        self.addCleanup(os.chmod, shut, 0o755)
        if os.access(shut, os.R_OK):
            self.skipTest("running as a user that ignores directory permissions")
        return base, s1

    def test_the_operator_can_say_they_accept_it(self):
        """One permanently unreadable folder -- ~/Library under a ~ root is the usual one -- would
        otherwise make the prune inert for good."""
        base, s1 = self.shrunk_with_a_locked_folder()
        rep = orphans.prune([self.root], self.runs, gap_s=GAP, now=CLOSED, apply=True,
                            ignore_unreadable=True)
        self.assertEqual(rep["counts"]["removed"], 1)
        self.assertNotIn(s1, self.index_ids())

    def test_the_report_names_the_directory_without_printing_where_it_lives(self):
        self.shrunk_with_a_locked_folder()
        rep = self.find()
        self.assertEqual(rep["unreadable_dirs"], ["locked-away"])
        self.assertNotIn(self.root, json.dumps(rep), "an absolute path reached the report")

    def test_a_run_whose_own_source_is_in_there_is_refused_even_then(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        base, s1 = sorted(self.index_ids())
        self.rewrite(src)
        self.tick()
        os.chmod(self.proj, 0o000)                      # the claiming source's own directory
        self.addCleanup(os.chmod, self.proj, 0o755)
        if os.access(self.proj, os.R_OK):
            self.skipTest("running as a user that ignores directory permissions")
        rep = orphans.prune([self.root], self.runs, gap_s=GAP, now=CLOSED, apply=True,
                            ignore_unreadable=True)
        self.assertEqual(rep["counts"]["removed"], 0)
        self.assertIn(s1, self.index_ids())


class TestASourceThatProducesNothingIsDamagedNotShrunk(Lab):
    def test_a_source_that_now_parses_to_no_runs_at_all_is_never_evidence(self):
        """A source still on disk, still claimed by an adapter, whose content is damaged so it reads
        as zero runs: every run it ever produced moves into `dropped` and a plain --apply deletes
        them. The capture is then the only copy of that work, and it goes with no flag asked for.
        The `inconsistent` guard cannot fire, because by then the cursor's `runs` is empty."""
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        before = self.index_ids()
        self.assertEqual(len(before), 2)
        # The shape, without depending on a particular way of damaging a file: the source is still
        # there and still claimed, and reading it now yields nothing. The capture that recorded the
        # emptiness has already moved its runs into `dropped`, which is what makes this deletable.
        cs = cursors.load(cursors.path_for(self.runs))
        real = os.path.realpath(src)
        cs[real] = dict(cs[real], runs=[], dropped=sorted(before))
        cursors.save(cursors.path_for(self.runs), cs)
        empty = lambda *a, **k: []
        orig = orphans._fresh_run_ids
        orphans._fresh_run_ids = empty
        try:
            rep = self.find(apply=True)
        finally:
            orphans._fresh_run_ids = orig
        self.assertEqual(rep["counts"]["removed"], 0, f"a damaged source deleted its own captures: {rep}")
        self.assertEqual(sorted(self.index_ids()), sorted(before))
        self.assertIn("empty_read", [r["reason"] for r in rep["refused"]], rep["refused"])

    def test_a_source_that_really_shrank_is_still_pruned(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        self.rewrite(src)                                 # one segment left, not zero
        self.tick()
        rep = self.find(apply=True)
        self.assertEqual(rep["counts"]["removed"], 1)


class TestTheGapThatCutTheRuns(Lab):
    def test_a_prune_at_another_gap_is_refused_before_it_reads_anything(self):
        """Proven to delete a live run: the watcher captures at one gap, the prune re-reads at
        another, the ids disagree and the run reads as one nobody produces any more."""
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        sniffer.tick([self.root], self.runs, gap_s=60, now=CLOSED)
        before = self.index_ids()
        rep = orphans.prune([self.root], self.runs, gap_s=1800, now=CLOSED, apply=True,
                            deleted_sources=True)
        self.assertEqual(rep["gap_mismatch"], [60.0])
        self.assertEqual(rep["orphans"], [])
        self.assertEqual(self.index_ids(), before, "a live run was pruned at the wrong gap")

    def test_the_same_gap_prunes_as_before(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        sniffer.tick([self.root], self.runs, gap_s=60, now=CLOSED)
        self.rewrite(src)
        sniffer.tick([self.root], self.runs, gap_s=60, now=CLOSED)
        rep = orphans.prune([self.root], self.runs, gap_s=60, now=CLOSED, apply=True)
        self.assertNotIn("gap_mismatch", rep)
        self.assertEqual(rep["counts"]["removed"], 1)

    def test_a_lab_whose_cursors_predate_the_record_still_prunes(self):
        """The field is new: an older cursor records 0, which is not a disagreement with anything."""
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        self.rewrite(src)
        self.tick()
        cs = cursors.load(cursors.path_for(self.runs))
        for c in cs.values():
            c.pop("gap_s", None)
        cursors.save(cursors.path_for(self.runs), cs)
        rep = self.find(apply=True)
        self.assertNotIn("gap_mismatch", rep)
        self.assertEqual(rep["counts"]["removed"], 1)


class TestFifthReviewFixes(Lab):
    def shrunk(self):
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        self.tick()
        base, s1 = sorted(self.index_ids())
        self.rewrite(src)
        self.tick()
        return base, s1

    def test_a_directory_that_cannot_be_entered_blocks_the_whole_pass(self):
        """The invariant is "a fresh read of EVERY readable source under the roots". The walk
        swallowed an unreadable directory and reported no error, so the runs under it never reached
        the fresh set -- and a live run whose source sits in that subtree reads as an orphan.

        A run id is a session id and a BASENAME, so a copy of any source could be in there: by
        default the whole pass stops. --prune-ignore-unreadable is the operator accepting that."""
        base, s1 = self.shrunk()
        shut = os.path.join(self.root, "locked-away")
        os.makedirs(shut)
        copy_fixture(os.path.join(shut, "other.jsonl"))
        os.chmod(shut, 0o000)
        self.addCleanup(os.chmod, shut, 0o755)
        if os.access(shut, os.R_OK):
            self.skipTest("running as a user that ignores directory permissions")
        rep = self.find(apply=True)
        self.assertEqual(rep["counts"]["removed"], 0, "pruned while part of the corpus was unreadable")
        self.assertIn(s1, self.index_ids())
        self.assertTrue(any(e["reason"] in ("unreachable", "unreadable") for e in rep["refused"]),
                        rep["refused"])

    def test_a_source_is_read_by_the_adapter_whose_glob_claims_it(self):
        """_source_state asked discover() about a bare FILE, which lets every descriptor answer --
        the explicit-request branch. Under the roots the same file is claimed by glob scoping, so the
        two passes could read one source with two different adapters."""
        from harnesslab.capture import adapters
        qwen = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "qwen_chat.jsonl")
        if not os.path.exists(qwen):
            self.skipTest("needs the qwen fixture")
        planted = os.path.join(self.proj, "sess.jsonl")
        shutil.copy(qwen, planted)
        by_root, _ = adapters.discover([self.root])
        self.assertEqual([a for p, a in by_root if p == os.path.realpath(planted)], [],
                         "precondition: no glob claims it under this root")
        state, fresh = orphans._source_state(os.path.realpath(planted), [self.root], GAP)
        self.assertEqual(state, "unclaimed", "read by an adapter whose glob does not claim it here")

    def test_a_clean_apply_says_it_applied(self):
        """`applied` is how a caller tells "ran, nothing to do" from "refused, another writer has the
        lab"; it stayed false whenever there was nothing removable."""
        src = os.path.join(self.proj, "s.jsonl")
        copy_fixture(src)
        self.tick()
        rep = self.find(apply=True)
        self.assertEqual(rep["orphans"], [])
        self.assertTrue(rep["applied"])
        self.assertFalse(rep["locked"])


if __name__ == "__main__":
    unittest.main()
