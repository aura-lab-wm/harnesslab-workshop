"""Crash debris in captured/: what a killed writer leaves behind, and what may be done about it.

Every test builds its own lab in a temp directory. Nothing here reads a real lab.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from harnesslab.capture.lock import CaptureLock                    # noqa: E402

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.join(LAB, "tests_agentlab"))

from test_capture_backfill import FIX                      # noqa: E402
from harnesslab.capture import regen, sweep                 # noqa: E402
from harnesslab.capture.lock import CaptureLock             # noqa: E402

NOW = 2.0e9
OLD = NOW - 7 * 24 * 3600          # a week before NOW: far past any safety age


def dead_pid() -> int:
    """A pid that existed and has exited."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def age(path: str, when: float = OLD) -> None:
    """Backdate a file, or a directory and everything in it."""
    if os.path.isdir(path):
        for d, _, files in os.walk(path):
            for f in files:
                os.utime(os.path.join(d, f), (when, when))
        for d, _, _ in sorted(os.walk(path), key=lambda t: -len(t[0])):
            os.utime(d, (when, when))
    else:
        os.utime(path, (when, when))


class Lab(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-sweep-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        self.cap = os.path.join(self.runs, "captured")
        os.makedirs(self.cap)
        (r,) = regen.regenerate(FIX, self.cap, gap_s=1800)
        self.run_id = r.run_id
        self.final = os.path.join(self.cap, self.run_id)

    def scratch(self, kind: str, pid: int) -> str:
        """A copy of the run under a scratch name, as regen leaves it when killed."""
        path = f"{self.final}.{kind}-{pid}"
        shutil.copytree(self.final, path)
        age(path)
        return path

    def live_pid(self) -> int:
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p.pid

    def sweep(self, **kw):
        return sweep.sweep(self.runs, now=kw.pop("now", NOW), **kw)

    def names(self, report, key):
        return sorted(e["name"] for e in report[key])


class TestDebrisIsFoundAndReportOnlyByDefault(Lab):
    def test_the_default_removes_nothing(self):
        tmp = self.scratch("tmp", dead_pid())
        rep = self.sweep()
        self.assertFalse(rep["apply"])
        self.assertEqual(self.names(rep, "debris"), [os.path.basename(tmp)])
        self.assertTrue(os.path.isdir(tmp), "a report-only sweep removed something")
        self.assertEqual(rep["counts"]["removed"], 0)

    def test_apply_removes_a_dead_writers_tmp_and_old_dirs(self):
        tmp = self.scratch("tmp", dead_pid())
        old = self.scratch("old", dead_pid())
        rep = self.sweep(apply=True)
        self.assertFalse(os.path.exists(tmp))
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(os.path.join(self.final, "ledger.jsonl")), "the real run was touched")
        self.assertEqual(rep["counts"]["removed"], 2)

    def test_a_stale_tmp_file_is_removed_only_with_apply(self):
        path = os.path.join(self.cap, ".tmp-abc123.jsonl")
        with open(path, "w") as f:
            f.write("{}")
        age(path)
        self.assertTrue(os.path.exists(path) and self.sweep()["counts"]["debris"] == 1)
        self.assertTrue(os.path.exists(path))
        self.sweep(apply=True)
        self.assertFalse(os.path.exists(path))

    def test_names_that_are_not_scratch_are_never_touched(self):
        keep = [os.path.join(self.cap, n) for n in ("imp-x.tmp-abc", "notes.old-", "imp-y")]
        for p in keep:
            os.makedirs(p)
            age(p)
        age(self.final)
        for p in (".lock", "index.jsonl", "cursors.json"):
            with open(os.path.join(self.cap, p), "w") as f:
                f.write("")
            age(os.path.join(self.cap, p))
        before = sorted(os.listdir(self.cap))
        rep = self.sweep(apply=True)
        self.assertEqual(sorted(os.listdir(self.cap)), before)
        self.assertEqual(rep["counts"]["debris"], 0)


class TestRefusals(Lab):
    def test_young_debris_is_left_alone(self):
        tmp = f"{self.final}.tmp-{dead_pid()}"
        shutil.copytree(self.final, tmp)                # written just now
        rep = self.sweep(apply=True, now=None)
        self.assertTrue(os.path.isdir(tmp))
        self.assertEqual([e["reason"] for e in rep["refused"]], ["young"])

    def test_age_is_the_newest_thing_inside_not_the_directory_itself(self):
        tmp = self.scratch("tmp", dead_pid())
        with open(os.path.join(tmp, "ledger.jsonl"), "a") as f:      # still being written into
            f.write("\n")
        os.utime(tmp, (OLD, OLD))
        self.sweep(apply=True, now=None)
        self.assertTrue(os.path.isdir(tmp), "a directory with a fresh file inside was called old")

    def test_a_live_writers_scratch_is_never_touched(self):
        """pid running AND the capture lock taken: somebody may be mid-swap right now."""
        tmp = self.scratch("tmp", self.live_pid())
        with CaptureLock(self.runs):
            rep = self.sweep(apply=True)
        self.assertTrue(os.path.isdir(tmp))
        self.assertEqual([e["reason"] for e in rep["refused"]], ["writer_alive"])

    def test_nothing_is_removed_while_another_writer_holds_the_lock(self):
        tmp = self.scratch("tmp", dead_pid())
        path = os.path.join(self.cap, ".tmp-zzz.json")
        with open(path, "w") as f:
            f.write("{}")
        age(path)
        with CaptureLock(self.runs):
            rep = self.sweep(apply=True)
        self.assertTrue(os.path.isdir(tmp), "removed while not holding the capture lock")
        self.assertTrue(os.path.exists(path))
        self.assertTrue(rep["locked"])
        self.assertEqual(rep["counts"]["removed"], 0)

    def test_a_live_pid_with_the_lock_free_is_debris(self):
        """Every writer into captured/ swaps under the capture lock, so a free lock proves nobody is
        mid-swap -- a recycled pid that happens to be running is not a writer."""
        tmp = self.scratch("tmp", self.live_pid())
        self.sweep(apply=True)
        self.assertFalse(os.path.exists(tmp))


class TestTheOnlyCopyIsRestored(Lab):
    def test_an_old_copy_whose_run_is_missing_is_restored_not_deleted(self):
        """Killed between `replace(final, old)` and `replace(tmp, final)`: the old copy is the run."""
        with open(os.path.join(self.final, "ledger.jsonl"), "rb") as f:
            ledger = f.read()
        old = f"{self.final}.old-{dead_pid()}"
        os.replace(self.final, old)
        age(old)
        rep = self.sweep()
        self.assertEqual([e["action"] for e in rep["debris"]], ["restore"])
        self.assertTrue(os.path.isdir(old) and not os.path.exists(self.final), "report-only moved it")

        rep = self.sweep(apply=True)
        self.assertFalse(os.path.exists(old))
        with open(os.path.join(self.final, "ledger.jsonl"), "rb") as f:
            self.assertEqual(f.read(), ledger, "the restored run is not the one that was parked")
        self.assertEqual(rep["counts"]["restored"], 1)

    def test_with_two_old_copies_the_newest_is_restored_and_the_other_removed(self):
        pid_a, pid_b = dead_pid(), dead_pid()
        older = f"{self.final}.old-{pid_a}"
        shutil.copytree(self.final, older)
        age(older, OLD - 3600)
        newer = f"{self.final}.old-{pid_b}"
        os.replace(self.final, newer)
        with open(os.path.join(newer, "marker"), "w") as f:
            f.write("newest")
        age(newer)
        self.sweep(apply=True)
        self.assertTrue(os.path.isfile(os.path.join(self.final, "marker")))
        self.assertFalse(os.path.exists(older))
        self.assertFalse(os.path.exists(newer))

    def test_an_old_copy_that_is_not_a_whole_run_is_kept(self):
        old = f"{self.final}.old-{dead_pid()}"
        os.replace(self.final, old)
        os.remove(os.path.join(old, "ledger.jsonl"))
        age(old)
        rep = self.sweep(apply=True)
        self.assertTrue(os.path.isdir(old), "the only copy of a run was deleted")
        self.assertEqual([e["reason"] for e in rep["refused"]], ["unrestorable"])



class TestSecondReviewFixes(Lab):
    def test_a_report_only_sweep_does_not_hold_the_lock_every_capture_waits_on(self):
        """A pure report took the EXCLUSIVE capture lock while it walked the debris, so every manual
        import waited on a read -- and if a writer held it, the report refused to judge anything and
        still said success."""
        self.scratch("tmp", dead_pid())
        seen = {}
        real = sweep._classify
        def watched(*a, **k):
            try:
                CaptureLock(self.runs, name="").__enter__().__exit__(None, None, None)
                seen["held"] = False
            except Exception:
                seen["held"] = True
            return real(*a, **k)
        sweep._classify = watched
        try:
            rep = self.sweep()
        finally:
            sweep._classify = real
        self.assertFalse(seen["held"], "the capture lock was held while a report-only sweep looked")
        self.assertEqual(rep["counts"]["debris"], 1, "and it still saw the debris")

    def test_debris_that_could_not_be_removed_is_reported_as_not_done(self):
        d = self.scratch("tmp", dead_pid())
        inner = os.path.join(d, "held")
        os.makedirs(inner)
        with open(os.path.join(inner, "f"), "w") as f:
            f.write("x")
        age(d)
        os.chmod(inner, 0o555)
        self.addCleanup(os.chmod, inner, 0o755)
        if os.access(inner, os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")
        rep = self.sweep(apply=True)
        self.assertEqual(rep["counts"]["removed"], 0)
        self.assertTrue(any(e.get("done") is False for e in rep["debris"]), rep["debris"])
        self.assertTrue(os.path.isdir(d))

    def test_a_lab_that_cannot_be_locked_reports_instead_of_crashing(self):
        self.scratch("tmp", dead_pid())
        os.chmod(self.cap, 0o555)
        self.addCleanup(os.chmod, self.cap, 0o755)
        if os.access(self.cap, os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")
        rep = self.sweep(apply=True)             # must not raise
        self.assertTrue(rep["locked"])
        self.assertIn("lock_error", rep)

    def test_a_run_waiting_to_be_restored_is_not_published_as_junk(self):
        """counts.debris counted restores too, so a run awaiting recovery was shown on the Capture
        page as debris sitting in the lab."""
        old = f"{self.final}.old-{dead_pid()}"
        os.replace(self.final, old)
        age(old, NOW - 60)
        rep = self.sweep()
        self.assertEqual(rep["counts"]["restore"], 1)
        self.assertEqual(rep["counts"]["debris"], 0, "a restore is not debris")


class TestAnUnaskableLock(Lab):
    def test_a_lock_that_cannot_be_asked_about_is_not_a_clean_lab(self):
        """_capture_lock_free returns None on a mount with no flock, or an unreadable lock file. That
        refused every entry -- but left `locked` false, so the CLI said clean and the watcher
        published debris 0 over the last true count."""
        from harnesslab.capture import sniffer, sweep as sw
        from harnesslab.capture import presence
        self.scratch("tmp", dead_pid())
        real = sw._capture_lock_free
        sw._capture_lock_free = lambda *_a, **_k: None
        try:
            rep = self.sweep()
            self.assertTrue(rep["locked"], "a lock nobody could ask about is not a free lab")
            presence.write(self.runs, state="idle", debris=7, debris_checked_at=1.0)
            sniffer._count_debris(self.runs, lambda: NOW)
            p = presence.read(self.runs)
            self.assertEqual((p["debris"], p["debris_checked_at"]), (7, 1.0),
                             "an unaskable lab overwrote the last real count")
        finally:
            sw._capture_lock_free = real


class TestAPrunedRunsLeftovers(Lab):
    def pruned_scratch(self):
        old = f"{self.final}.old-{dead_pid()}"
        shutil.copytree(self.final, old)
        age(old)
        shutil.rmtree(self.final)                      # the prune removed the run itself
        with open(os.path.join(self.cap, sweep.PRUNED), "w") as f:
            json.dump({"run_ids": [self.run_id]}, f)
        return old

    def test_the_leftovers_of_a_pruned_run_are_removed_not_kept_forever(self):
        """Refusing to restore was right; refusing to DELETE left the scratch in the lab for good,
        reported as refused on every sweep with no remedy but rm."""
        old = self.pruned_scratch()
        rep = self.sweep(apply=True)
        self.assertEqual(rep["counts"]["restored"], 0, "a pruned run must not come back")
        self.assertFalse(os.path.exists(old), "and its leftovers must not stay forever")
        self.assertFalse(os.path.exists(self.final))

    def test_the_record_is_dropped_once_nothing_of_that_run_is_left(self):
        """Otherwise a run id captured again later -- ids repeat, they hash the session and the name
        -- could never be restored from a genuinely killed swap."""
        self.pruned_scratch()
        self.sweep(apply=True)
        self.assertEqual(sweep.pruned_runs(self.cap), set(), "the record outlived what it was about")


class TestTheRecordOfAPrunedRun(Lab):
    def test_it_is_dropped_once_the_run_is_in_the_lab_again(self):
        """Run ids repeat -- they hash the session and the source's name -- so a record kept after a
        later capture would refuse to restore that run from a genuinely killed swap."""
        with open(os.path.join(self.cap, sweep.PRUNED), "w") as f:
            json.dump({"run_ids": [self.run_id]}, f)     # the run itself is present again
        self.sweep(apply=True)
        self.assertEqual(sweep.pruned_runs(self.cap), set())

    def test_a_killed_swap_of_that_run_is_restored_again_afterwards(self):
        with open(os.path.join(self.cap, sweep.PRUNED), "w") as f:
            json.dump({"run_ids": [self.run_id]}, f)
        self.sweep(apply=True)                            # forgets it: the run is back
        old = f"{self.final}.old-{dead_pid()}"
        os.replace(self.final, old)
        age(old, NOW - 60)
        rep = self.sweep(apply=True)
        self.assertEqual(rep["counts"]["restored"], 1)
        self.assertTrue(os.path.isdir(self.final))


class TestASweepPublishesWhatItJustChanged(Lab):
    def test_the_count_on_the_page_is_not_a_day_out_of_date(self):
        """Only the watcher's daily hook wrote the debris count, so after a removal by hand the
        Capture page kept publishing debris that was no longer in the lab."""
        from harnesslab.capture import presence
        presence.write(self.runs, state="idle", debris=3, debris_checked_at=1.0)
        self.scratch("tmp", dead_pid())
        rep = self.sweep(apply=True)
        self.assertEqual(rep["counts"]["removed"], 1)
        p = presence.read(self.runs)
        self.assertEqual(p["debris"], 0, "the page still shows debris that was just removed")
        self.assertEqual(p["debris_checked_at"], NOW)

    def test_a_report_only_sweep_publishes_nothing(self):
        from harnesslab.capture import presence
        presence.write(self.runs, state="idle", debris=3, debris_checked_at=1.0)
        self.scratch("tmp", dead_pid())
        self.sweep()
        p = presence.read(self.runs)
        self.assertEqual((p["debris"], p["debris_checked_at"]), (3, 1.0), "a read published a count")


class TestCli(unittest.TestCase):
    """python -m harnesslab.capture --sweep | --prune-orphans, against a lab built here."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-sweep-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lab = os.path.join(self.tmp, "lab")
        for d in ("harnesses", "tasks", os.path.join("data", "runs")):
            os.makedirs(os.path.join(self.lab, d), exist_ok=True)
        self.runs = os.path.join(self.lab, "data", "runs")
        self.cap = os.path.join(self.runs, "captured")
        self.src = os.path.realpath(os.path.join(self.tmp, "sessions"))
        os.makedirs(self.src)

    def cli(self, *args):
        env = dict(os.environ, HARNESSLAB_CAPTURE_CONFIG=os.path.join(self.tmp, "no-allowlist.json"))
        return subprocess.run([sys.executable, "-B", "-m", "harnesslab.capture", *args, "--lab", self.lab],
                              cwd=LAB, env=env, capture_output=True, text=True, timeout=120)

    def test_a_prune_that_could_not_read_a_source_does_not_exit_zero(self):
        """`1 if any source failed`, as --backfill and --seed-cursors already report: a caller cannot
        otherwise tell a clean "no orphans" from a pass that established nothing."""
        import json
        from harnesslab.capture import sniffer
        src = os.path.join(self.src, "sess-cap-1.jsonl")
        shutil.copy(FIX, src)
        sniffer.tick([self.src], self.runs, gap_s=1800, now=1.8e9)
        os.chmod(src, 0)
        self.addCleanup(os.chmod, src, 0o644)
        if os.access(src, os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        p = self.cli("--prune-orphans", "--path", self.src)
        self.assertEqual(p.returncode, 1, p.stdout[:400] + p.stderr[:400])
        self.assertEqual(json.loads(p.stdout)["counts"]["orphans"], 0, "and it claims no orphans")

    def test_a_prune_that_could_not_remove_a_directory_does_not_exit_zero(self):
        """--sweep already reports a failed removal with exit 1; the two modes must agree."""
        import json
        from harnesslab.capture import cursors, sniffer
        from test_capture_backfill import RESUMED_TAIL, copy_fixture
        src = os.path.join(self.src, "s.jsonl")
        copy_fixture(src, RESUMED_TAIL)
        sniffer.tick([self.src], self.runs, gap_s=1800, now=1.8e9)
        ids = sorted(os.path.basename(d) for d in glob.glob(os.path.join(self.cap, "imp-*")))
        copy_fixture(src)                                  # shrink it: the second segment is dropped
        st = os.stat(src)
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        sniffer.tick([self.src], self.runs, gap_s=1800, now=1.8e9)
        doomed = os.path.join(self.cap, ids[-1])
        os.chmod(doomed, 0o555)
        self.addCleanup(os.chmod, doomed, 0o755)
        if os.access(doomed, os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")
        p = self.cli("--prune-orphans", "--apply", "--path", self.src)
        self.assertEqual(p.returncode, 1, p.stdout[:400] + p.stderr[:300])
        self.assertEqual(json.loads(p.stdout)["counts"]["failed"], 1)

    def test_two_modes_at_once_are_refused_rather_than_one_being_swallowed(self):
        """--print-launchd printed a plist and exited 0 while the sweep it was asked for never ran."""
        # --path so the allow-list check cannot be the thing that refuses: the collision must be.
        for pair in (("--print-launchd", "--sweep"), ("--print-launchd", "--prune-orphans")):
            p = self.cli(*pair, "--apply", "--path", self.src)
            self.assertEqual(p.returncode, 2, f"{pair} -> {p.stdout[:200]}{p.stderr[:200]}")
            self.assertNotIn("<plist", p.stdout, f"{pair} printed a plist anyway")
            self.assertIn("another mode", p.stderr, f"{pair} -> {p.stderr[:200]}")

    def test_apply_without_a_mode_that_applies_is_refused(self):
        p = self.cli("--apply")
        self.assertEqual(p.returncode, 2, p.stdout[:200])

    def test_sweep_reports_by_default_and_removes_only_with_apply(self):
        import json
        os.makedirs(self.cap)
        (r,) = regen.regenerate(FIX, self.cap, gap_s=1800)
        tmp = os.path.join(self.cap, f"{r.run_id}.tmp-{dead_pid()}")
        shutil.copytree(os.path.join(self.cap, r.run_id), tmp)
        age(tmp, when=1_000_000_000)
        p = self.cli("--sweep")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["counts"]["debris"], 1)
        self.assertTrue(os.path.isdir(tmp))
        p = self.cli("--sweep", "--apply")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["counts"]["removed"], 1)
        self.assertFalse(os.path.exists(tmp))

    def test_prune_orphans_reports_by_default_and_prunes_only_with_apply(self):
        import json
        from harnesslab.capture import sniffer
        src = os.path.join(self.src, "sess-cap-1.jsonl")
        shutil.copy(FIX, src)
        with open(os.path.join(self.src, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("not a session\n")           # the directory still lists: the removal is provable
        sniffer.tick([self.src], self.runs, gap_s=1800, now=1.8e9)
        (run_id,) = [n for n in os.listdir(self.cap) if n.startswith("imp-")]
        os.remove(src)
        p = self.cli("--prune-orphans", "--path", self.src)
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(p.stdout)
        self.assertEqual([o["run_id"] for o in rep["orphans"]], [run_id])
        self.assertNotIn(self.src, p.stdout, "the report printed session paths")
        self.assertTrue(os.path.isdir(os.path.join(self.cap, run_id)))

        p = self.cli("--prune-orphans", "--path", self.src, "--apply")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["counts"]["kept"], 1)
        self.assertTrue(os.path.isdir(os.path.join(self.cap, run_id)),
                        "--apply alone removed a run whose source was merely deleted")

        p = self.cli("--prune-orphans", "--path", self.src, "--apply", "--prune-deleted-sources")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.cap, run_id)))

    def test_prune_deleted_sources_is_refused_without_prune_orphans(self):
        p = self.cli("--sweep", "--apply", "--prune-deleted-sources")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--prune-deleted-sources", p.stderr)

    def test_apply_while_locked_exits_2_and_removes_nothing(self):
        os.makedirs(self.cap)
        with CaptureLock(self.runs):
            p = self.cli("--sweep", "--apply")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        import json
        self.assertTrue(json.loads(p.stdout)["locked"], "exit 2 for a reason other than the lock")

    def test_apply_without_a_mode_is_refused(self):
        p = self.cli("--apply")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--apply only goes with", p.stderr)

    def test_sweep_does_not_combine_with_another_mode(self):
        p = self.cli("--sweep", "--backfill", "--path", self.src)
        self.assertEqual(p.returncode, 2)

    def test_prune_orphans_with_nothing_to_look_at_exits_2(self):
        p = self.cli("--prune-orphans")
        self.assertEqual(p.returncode, 2)
        self.assertIn("nothing to capture", p.stderr)


class TestTheWatchersDailySweepOnlyCounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-sweep-loop-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        self.cap = os.path.join(self.runs, "captured")
        self.src = os.path.join(self.tmp, "sessions")
        os.makedirs(self.src)
        shutil.copy(FIX, os.path.join(self.src, "sess-cap-1.jsonl"))
        os.makedirs(self.cap)
        self.debris = os.path.join(self.cap, f"imp-gone.tmp-{dead_pid()}")
        os.makedirs(self.debris)
        with open(os.path.join(self.debris, "ledger.jsonl"), "w") as f:
            f.write("{}\n")
        age(self.debris, when=1_000_000_000)

    def loop(self, full_sweep_s):
        from harnesslab.capture import sniffer
        calls = {"n": 0}
        real = sniffer.tick
        def counted(*a, **k):
            try:
                return real(*a, **k)
            finally:
                calls["n"] += 1          # after the pass: a stop seen mid-pass interrupts the sweep
        sniffer.tick = counted
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0, full_sweep_s=full_sweep_s,
                         stop=lambda: calls["n"] >= 1, now=lambda: 1.8e9, sleep=lambda s: None)
        finally:
            sniffer.tick = real

    def test_a_full_sweep_publishes_the_debris_count_and_deletes_nothing(self):
        from harnesslab.capture import presence
        self.loop(full_sweep_s=100)
        doc = presence.read(self.runs)
        self.assertEqual(doc["debris"], 1, "the daily sweep did not count the debris")
        self.assertGreater(doc["debris_checked_at"], 0)
        self.assertTrue(os.path.isdir(self.debris), "the watcher removed debris; it may only count it")

    def test_an_ordinary_tick_does_not_sweep(self):
        from harnesslab.capture import presence
        self.loop(full_sweep_s=0)
        self.assertEqual(presence.read(self.runs)["debris_checked_at"], 0.0)

    def test_a_failing_sweep_does_not_kill_the_watcher(self):
        from harnesslab.capture import presence
        real = sweep.sweep
        sweep.sweep = lambda *a, **k: (_ for _ in ()).throw(OSError(5, "I/O error"))
        try:
            self.loop(full_sweep_s=100)
        finally:
            sweep.sweep = real
        doc = presence.read(self.runs)
        self.assertEqual(doc["state"], "stopped")
        self.assertGreater(doc["last_full_sweep_at"], 0, "precondition: the full sweep ran")


class TestReviewFixes(Lab):
    def test_the_only_copy_of_a_run_is_restored_at_once_not_in_an_hour(self):
        """The age guard exists to avoid racing a LIVE writer, and a restore already proves the writer
        is gone. Waiting an hour hides the run from the index, the page and every aggregate."""
        old = f"{self.final}.old-{dead_pid()}"
        os.replace(self.final, old)
        age(old, NOW - 60)                                # killed mid-swap a minute ago
        rep = self.sweep(apply=True)
        self.assertEqual(rep["counts"]["restored"], 1, f"refused: {rep['refused']}")
        self.assertTrue(os.path.isdir(self.final), "the run is still missing")
        self.assertFalse(os.path.exists(old))

    def test_a_young_copy_beside_a_present_run_is_still_left_alone(self):
        """Deleting is the other half: that one waits, because the run itself is not missing."""
        old = f"{self.final}.old-{dead_pid()}"
        shutil.copytree(self.final, old)
        age(old, NOW - 60)
        rep = self.sweep(apply=True)
        self.assertEqual(rep["counts"]["removed"], 0)
        self.assertEqual([e["reason"] for e in rep["refused"]], ["young"])
        self.assertTrue(os.path.isdir(old))

    def test_a_live_writer_still_holds_off_a_restore(self):
        """Alive AND the capture lock held: a swap really is in flight, so nothing is put back."""
        from harnesslab.capture.lock import CaptureLock
        old = f"{self.final}.old-{self.live_pid()}"
        os.replace(self.final, old)
        age(old, NOW - 60)
        with CaptureLock(self.runs, name=""):
            rep = self.sweep(apply=True)
        self.assertEqual(rep["counts"]["restored"], 0)
        self.assertEqual([e["reason"] for e in rep["refused"]], ["writer_alive"])

    def test_a_count_taken_while_the_lab_was_locked_is_not_published_as_the_count(self):
        """sweep() refuses what it cannot judge while the capture lock is held, so its count is a
        floor, not a reading. Stamping it as just-checked overwrote the true count with a smaller one."""
        from harnesslab.capture import presence, sniffer
        from harnesslab.capture.lock import CaptureLock
        presence.write(self.runs, state="idle", debris=7, debris_checked_at=1.0)
        self.scratch("tmp", dead_pid())
        with CaptureLock(self.runs, name=""):
            sniffer._count_debris(self.runs, lambda: NOW)
        p = presence.read(self.runs)
        self.assertEqual((p["debris"], p["debris_checked_at"]), (7, 1.0),
                         "a locked sweep overwrote the last real count")
        sniffer._count_debris(self.runs, lambda: NOW)
        p = presence.read(self.runs)
        self.assertEqual((p["debris"], p["debris_checked_at"]), (1, NOW), "an unlocked sweep does publish")


if __name__ == "__main__":
    unittest.main()
