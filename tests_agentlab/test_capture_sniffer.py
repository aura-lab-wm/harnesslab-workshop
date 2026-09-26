"""The loop that makes capture ambient instead of a chore.

One tick captures what changed and skips what did not; the loop runs ticks on a cadence, obeys the
control file, and publishes what it is doing on either side of every pass.
"""
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.join(LAB, "tests_agentlab"))

from test_capture_backfill import RESUME_TAIL, copy_fixture  # noqa: E402
from harnesslab.capture import cursors, presence, sniffer    # noqa: E402
from harnesslab.capture.lock import CaptureLock              # noqa: E402

CLOSED = 1.8e9          # far past the fixture's last activity, so its run is finished


class TestTick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-sniffer-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        self.src = os.path.join(self.tmp, "sessions")
        os.makedirs(self.src)
        copy_fixture(os.path.join(self.src, "sess-cap-1.jsonl"))

    def tick(self, **kw):
        return sniffer.tick([self.src], self.runs, gap_s=1800, now=kw.pop("now", CLOSED), **kw)

    def test_a_first_tick_captures_and_remembers_the_source(self):
        rep = self.tick()
        self.assertEqual(rep["files"], 1)
        cs = cursors.load(cursors.path_for(self.runs))
        self.assertEqual(len(cs), 1)
        self.assertTrue(next(iter(cs.values()))["runs"], "the cursor forgot what the source produced")

    def test_a_second_tick_opens_nothing_because_nothing_changed(self):
        self.tick()
        rep = self.tick()
        self.assertEqual(rep["files"], 0, "an unchanged source was read again")

    def test_a_grown_source_is_captured_again(self):
        self.tick()
        with open(os.path.join(self.src, "sess-cap-1.jsonl"), "a", encoding="utf-8") as f:
            f.write("\n")
        rep = self.tick()
        self.assertEqual(rep["files"], 1, "a changed source was skipped")

    def test_content_appended_while_the_pass_runs_is_not_marked_captured(self):
        """The cursor must describe the file as it was READ, not as it is afterwards.

        A pass over the real corpus takes minutes, and a session being written is exactly the kind
        that grows during one. Stat-after-read records those bytes as captured without ever reading
        them -- and if what WAS read looked finished, no recheck is scheduled and the source is
        skipped forever.
        """
        src_file = os.path.join(self.src, "sess-cap-1.jsonl")
        real = sniffer._capture

        def capture_then_append(*a, **k):
            rep = real(*a, **k)
            with open(src_file, "a", encoding="utf-8") as f:   # arrives after the read
                f.write("\n")
            return rep

        sniffer._capture = capture_then_append
        try:
            self.tick()
        finally:
            sniffer._capture = real

        rep = self.tick()
        self.assertEqual(rep["files"], 1,
                         "bytes written during the pass were recorded as captured and never read")

    def test_a_resume_written_after_the_parent_went_quiet_is_still_linked(self):
        """Relations are pairwise, and a pass only relates the runs it READ.

        The parent is captured and goes quiet, so its cursor matches and every later tick skips it.
        A resume then arrives as a new file carrying the parent's uuids. Reading only the new file,
        the pass has nothing to relate it to -- so in watch mode resumes and forks would never be
        linked, for as long as the watcher ran, where --backfill links them at once.
        """
        self.tick()                                                   # parent alone, then quiet
        copy_fixture(os.path.join(self.src, "sess-cap-2.jsonl"), RESUME_TAIL)
        self.tick()                                                   # only the resume has changed
        import json
        with open(os.path.join(self.runs, "captured", "identity.json"), encoding="utf-8") as f:
            rel = json.load(f)["relations"]
        superseded = [rid for rid, v in rel.items() if v.get("superseded_by")]
        self.assertEqual(len(superseded), 1, "the resume was never linked to the parent it continues")

    def test_a_tick_publishes_what_it_did(self):
        self.tick()
        d = presence.read(self.runs)
        self.assertEqual(d["state"], "idle")
        self.assertGreater(d["last_capture_at"], 0)
        self.assertGreaterEqual(d["totals"]["runs_indexed"], 1)

    def test_a_quiet_tick_does_not_blank_the_corpus_totals(self):
        """Totals are what is CAPTURED, not what the last pass happened to do.

        Reporting the pass would mean the first quiet tick -- the normal case, since usually nothing
        changed -- resets the page to "0 runs, 0 open" while 20,090 runs sit in the index.
        """
        self.tick()
        first = presence.read(self.runs)["totals"]
        self.assertGreaterEqual(first["runs_indexed"], 1)
        self.assertGreater(first["cost_usd"], 0, "fixture should have priced tokens")
        self.tick()                                   # nothing changed this time
        second = presence.read(self.runs)["totals"]
        # EVERY total, not just the one an earlier fix happened to cover: a menubar reading
        # totals.cost_usd would otherwise show $0.00 for good after the first quiet pass.
        blanked = [k for k, v in first.items() if v and not second.get(k)]
        self.assertEqual(blanked, [], f"a quiet pass blanked {blanked}")

    def test_corpus_totals_count_the_runs_whose_spend_is_unknown(self):
        import json as _json
        cap = os.path.join(self.runs, "captured")
        os.makedirs(cap, exist_ok=True)
        with open(os.path.join(cap, "index.jsonl"), "w", encoding="utf-8") as f:
            f.write(_json.dumps({"run_id": "a", "cost_usd": 1.5, "input_tokens": 10, "output_tokens": 5}) + "\n")
            f.write(_json.dumps({"run_id": "b", "cost_usd": None, "input_tokens": None, "output_tokens": None}) + "\n")
        t = sniffer._corpus_totals(self.runs, {})
        self.assertEqual((t["runs_indexed"], t["cost_usd"], t["input_tokens"]), (2, 1.5, 10))
        self.assertEqual(t["unmeasured_runs"], 1)

    def test_a_tick_that_cannot_take_the_lock_says_so_instead_of_failing(self):
        with CaptureLock(self.runs):
            rep = self.tick()
        self.assertEqual(rep.get("state"), "locked")
        self.assertEqual(presence.read(self.runs)["state"], "locked")

    def test_an_open_run_is_rechecked_once_its_quiet_time_elapses(self):
        """Nothing about the file changes when a session goes quiet -- only the clock does."""
        open_now = 1757499000.0          # inside the fixture's activity window
        sniffer.tick([self.src], self.runs, gap_s=1800, now=open_now)
        cs = cursors.load(cursors.path_for(self.runs))
        self.assertTrue(any(c["open_until"] for c in cs.values()), "no recheck was scheduled")
        self.assertTrue(cursors.due(cs, now=CLOSED, gap_s=1800), "the recheck never came due")


class TestSeed(unittest.TestCase):
    """Adopting a corpus captured before cursors existed, without reading it again.

    This lab already holds 20,090 runs from --backfill and no cursors. Without seeding, the first
    --watch reads every source again -- minutes, and a pass that --backfill already did.
    """

    def setUp(self):
        from harnesslab.capture import backfill
        self.tmp = tempfile.mkdtemp(prefix="hl-seed-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        self.src = os.path.join(self.tmp, "sessions")
        os.makedirs(self.src)
        copy_fixture(os.path.join(self.src, "sess-cap-1.jsonl"))
        quiet = os.stat(os.path.join(self.src, "sess-cap-1.jsonl")).st_mtime_ns - 3600 * 10 ** 9
        os.utime(os.path.join(self.src, "sess-cap-1.jsonl"), ns=(quiet, quiet))   # untouched for an hour
        backfill.run([self.src], self.runs, gap_s=1800, now=CLOSED)   # the pre-cursor world

    def test_seeding_adopts_captured_sources_and_the_next_tick_reads_nothing(self):
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 1)
        cs = cursors.load(cursors.path_for(self.runs))
        (c,) = cs.values()
        self.assertTrue(c["seeded"])
        self.assertTrue(c["runs"], "the seeded cursor does not know what the source produced")
        tick = sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(tick["files"], 0, "a seeded, unchanged source was read again")

    def test_seeding_opens_no_source_file(self):
        """Seeding is stat-only. Reading the sources would be the pass it exists to avoid."""
        import builtins
        opened = []
        real_open = builtins.open
        def spy(file, *a, **k):
            if str(file).startswith(self.src):
                opened.append(file)
            return real_open(file, *a, **k)
        builtins.open = spy
        try:
            sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        finally:
            builtins.open = real_open
        self.assertEqual(opened, [], "seeding read a source file")

    def test_a_source_changed_since_it_was_captured_is_not_adopted(self):
        """Adopting it would record the new bytes as captured, and they were never read."""
        import glob as _glob
        path = os.path.join(self.src, "sess-cap-1.jsonl")
        (summary,) = _glob.glob(os.path.join(self.runs, "captured", "*", "summary.json"))
        later = os.stat(summary).st_mtime_ns + 10 ** 12       # modified long after the capture
        os.utime(path, ns=(later, later))
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 0)
        self.assertEqual(rep["stale"], 1)
        self.assertEqual(sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)["files"], 1)

    def test_a_source_that_was_never_captured_is_left_for_the_watcher(self):
        copy_fixture(os.path.join(self.src, "sess-new.jsonl"))
        other = os.path.join(self.src, "sess-new.jsonl")
        with open(other, "a", encoding="utf-8") as f:
            f.write("\n")
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["uncaptured"], 1)

    def test_seeding_never_overwrites_a_cursor_the_watcher_already_has(self):
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        before = cursors.load(cursors.path_for(self.runs))
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 0)
        self.assertEqual(cursors.load(cursors.path_for(self.runs)), before)

    def test_seeding_refuses_while_a_watcher_is_running(self):
        """Seeding loads cursors, walks for twenty seconds, then saves the whole file. A tick in
        between loses on one side or the other -- silently, with the seed still reporting success."""
        from harnesslab.capture.lock import CaptureLock, CaptureLocked
        with CaptureLock(self.runs, name="sniffer"):
            with self.assertRaises(CaptureLocked):
                sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(cursors.load(cursors.path_for(self.runs)), {})

    def test_a_basename_two_sources_share_is_not_adopted_for_either(self):
        """The start span records only a basename. If two sources share one, the lab cannot say which
        was captured, so neither is adopted -- the watcher reads both, which only costs a read."""
        other = os.path.join(self.tmp, "sessions2")
        os.makedirs(other)
        twin = os.path.join(other, "sess-cap-1.jsonl")      # never captured, same basename
        with open(twin, "w", encoding="utf-8") as f:
            f.write("{}\n")
        old = os.stat(os.path.join(self.src, "sess-cap-1.jsonl")).st_mtime_ns - 10 ** 12
        os.utime(twin, ns=(old, old))                        # older, so a naive check would adopt it
        rep = sniffer.seed([self.src, other], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 0)
        self.assertEqual(rep["ambiguous"], 2)

    def test_a_change_just_before_the_capture_finished_is_not_trusted(self):
        """summary.json is written AFTER the read; a change a moment before it may be unread bytes."""
        import glob as _glob
        path = os.path.join(self.src, "sess-cap-1.jsonl")
        (summary,) = _glob.glob(os.path.join(self.runs, "captured", "*", "summary.json"))
        just_before = os.stat(summary).st_mtime_ns - 500_000_000     # half a second earlier
        os.utime(path, ns=(just_before, just_before))
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 0, "a change inside the capture window was adopted as read")
        self.assertEqual(rep["stale"], 1)

    def test_an_abandoned_temp_segment_directory_is_ignored(self):
        """regen writes <run>.tmp-<pid>/ before renaming. A leftover one is not a captured run, and
        counting it pushes the capture time forward and lists the run twice."""
        import glob as _glob
        (rdir,) = [d for d in _glob.glob(os.path.join(self.runs, "captured", "*")) if os.path.isdir(d)]
        leftover = rdir + ".tmp-99999"
        shutil.copytree(rdir, leftover)
        later = os.stat(os.path.join(leftover, "summary.json")).st_mtime_ns + 10 ** 12
        os.utime(os.path.join(leftover, "summary.json"), ns=(later, later))
        path = os.path.join(self.src, "sess-cap-1.jsonl")
        between = os.stat(os.path.join(rdir, "summary.json")).st_mtime_ns + 10 ** 11
        os.utime(path, ns=(between, between))               # changed after the REAL capture
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 0, "a leftover temp dir made a stale source look fresh")

    def test_the_first_watch_after_seeding_does_not_read_the_corpus_again(self):
        """Asked of loop(), not tick(): the full-sweep clock started at zero, so the first pass of
        --watch ignored every seeded cursor and read everything -- the pass seeding exists to avoid."""
        sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        fulls = []
        real = sniffer.tick
        sniffer.tick = lambda *a, **k: (fulls.append(bool(k.get("full"))), real(*a, **k))[1]
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0,
                         stop=lambda: len(fulls) >= 1, now=lambda: CLOSED + 60, sleep=lambda s: None)
        finally:
            sniffer.tick = real
        self.assertEqual(fulls, [False], "the first watch after seeding was a full read")

    def test_a_basename_the_lab_holds_for_two_different_sessions_is_not_adopted(self):
        """The discovered side can look unique while the LAB holds runs of two different sessions
        under that basename -- from a root not being seeded, or a source since moved."""
        import json as _json
        from harnesslab.capture import backfill
        other = os.path.join(self.tmp, "elsewhere")
        os.makedirs(other)
        twin = os.path.join(other, "sess-cap-1.jsonl")
        with open(os.path.join(self.src, "sess-cap-1.jsonl"), encoding="utf-8") as f:
            rows = [_json.loads(l) for l in f if l.strip()]
        with open(twin, "w", encoding="utf-8") as f:
            for r in rows:
                if "sessionId" in r:
                    r["sessionId"] = "a-different-session"
                f.write(_json.dumps(r) + "\n")
        quiet = os.stat(twin).st_mtime_ns - 3600 * 10 ** 9
        os.utime(twin, ns=(quiet, quiet))
        backfill.run([other], self.runs, gap_s=1800, now=CLOSED)        # lab now holds both sessions
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)   # seeding only one root
        self.assertEqual(rep["seeded"], 0)
        self.assertEqual(rep["ambiguous"], 1)

    def test_a_session_that_changed_directory_is_still_adopted(self):
        """`project` is the basename of each segment's cwd, so one session that moved directory has
        two projects. It is still one session; keying identity on project marked it ambiguous and it
        never got the speed-up."""
        import glob as _glob, json as _json
        for ledger in _glob.glob(os.path.join(self.runs, "captured", "*", "ledger.jsonl")):
            with open(ledger, encoding="utf-8") as f:
                lines = f.readlines()
            start = _json.loads(lines[0])
            clone_dir = os.path.dirname(ledger) + "-s9"
            shutil.copytree(os.path.dirname(ledger), clone_dir)
            start["project"] = "somewhere-else"               # same session, different cwd
            start["run_id"] = start.get("run_id", "") + "-s9"
            with open(os.path.join(clone_dir, "ledger.jsonl"), "w", encoding="utf-8") as f:
                f.write(_json.dumps(start) + "\n" + "".join(lines[1:]))
            # recorded, as a real capture records every segment it writes: the row is the summary
            with open(os.path.join(clone_dir, "summary.json"), encoding="utf-8") as f:
                row = dict(_json.load(f), run_id=start["run_id"])
            with open(os.path.join(self.runs, "captured", "index.jsonl"), "a", encoding="utf-8") as f:
                f.write(_json.dumps(row) + "\n")
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["ambiguous"], 0, "one session in two directories was called ambiguous")
        self.assertEqual(rep["seeded"], 1)

    def test_runs_written_by_a_backfill_that_never_finished_are_not_trusted(self):
        """A backfill writes index.jsonl and inflight.json at the END of its pass. Ctrl-c part-way
        leaves run folders the lab never recorded; adopting their source meant the watcher skipped it
        for good and those runs were never indexed. Reproduced before this test existed: 1 folder,
        0 index rows, seeded=1."""
        from harnesslab.capture import backfill
        shutil.rmtree(self.runs, True)
        second = copy_fixture(os.path.join(self.src, "sess-cap-2.jsonl"))
        q = os.stat(second).st_mtime_ns - 3600 * 10 ** 9
        os.utime(second, ns=(q, q))
        real, n = backfill.regenerate, {"i": 0}
        def ctrl_c(*a, **k):
            n["i"] += 1
            if n["i"] == 2:
                raise KeyboardInterrupt
            return real(*a, **k)
        backfill.regenerate = ctrl_c
        try:
            with self.assertRaises(KeyboardInterrupt):
                backfill.run([self.src], self.runs, gap_s=1800, now=CLOSED)
        finally:
            backfill.regenerate = real
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 0, "a source whose runs the lab never recorded was adopted")

    def test_a_run_folder_the_index_does_not_list_is_not_trusted(self):
        """Also what a partly-written source leaves: regen swaps segments in one at a time, and a
        failure after the first leaves a folder nothing recorded."""
        os.remove(os.path.join(self.runs, "captured", "index.jsonl"))
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 0)
        self.assertEqual(rep["unrecorded"], 1)

    def test_a_recorded_run_whose_index_row_is_stale_is_not_trusted(self):
        """Listed is not the same as current. A second backfill that re-captured a grown source and was
        killed before rewriting index.jsonl leaves a fresh folder under an old row; adopting the source
        froze the stale row, since the watcher would never read it again. The row is built from the
        folder's own summary, so the two disagreeing is the tell."""
        import glob as _glob, json as _json
        (summary,) = _glob.glob(os.path.join(self.runs, "captured", "*", "summary.json"))
        with open(summary, encoding="utf-8") as f:
            doc = _json.load(f)
        doc["steps"] = int(doc.get("steps") or 0) + 7            # the folder was re-captured, grown
        doc["output_tokens"] = int(doc.get("output_tokens") or 0) + 500
        with open(summary, "w", encoding="utf-8") as f:
            _json.dump(doc, f)
        rep = sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(rep["seeded"], 0, "a run whose index row no longer matches its folder was adopted")
        self.assertEqual(rep["unrecorded"], 1)

    def test_seeding_waits_for_no_capture_it_would_be_racing(self):
        from harnesslab.capture.lock import CaptureLock, CaptureLocked
        with CaptureLock(self.runs):                        # a backfill is mid-pass
            with self.assertRaises(CaptureLocked):
                sniffer.seed([self.src], self.runs, gap_s=1800, now=CLOSED)

    def test_a_run_still_open_at_capture_is_scheduled_for_a_recheck(self):
        from harnesslab.capture import backfill
        shutil.rmtree(self.runs, True)
        open_now = 1757499000.0
        backfill.run([self.src], self.runs, gap_s=1800, now=open_now)   # captured while still open
        sniffer.seed([self.src], self.runs, gap_s=1800, now=open_now)
        cs = cursors.load(cursors.path_for(self.runs))
        self.assertTrue(cursors.due(cs, now=CLOSED, gap_s=1800),
                        "an open run adopted by seeding was never rechecked")


class TestLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-loop-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        self.src = os.path.join(self.tmp, "sessions")
        os.makedirs(self.src)
        copy_fixture(os.path.join(self.src, "sess-cap-1.jsonl"))

    def test_the_loop_stops_when_asked_and_leaves_a_stopped_presence(self):
        n = {"i": 0}
        def stop():
            n["i"] += 1
            return n["i"] > 2
        sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0, stop=stop, now=lambda: CLOSED)
        self.assertEqual(presence.read(self.runs)["state"], "stopped")

    def test_a_paused_loop_does_not_capture(self):
        presence.set_paused(self.runs, True)
        ticks = {"n": 0}
        real = sniffer.tick
        def counting(*a, **k):
            ticks["n"] += 1
            return real(*a, **k)
        sniffer.tick = counting
        seen = []
        n = {"i": 0}
        def stop():
            # Sampled from INSIDE the loop: on exit the state is always "stopped", by design.
            seen.append(presence.read(self.runs)["state"])
            n["i"] += 1
            return n["i"] > 2
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0, stop=stop,
                         now=lambda: CLOSED)
        finally:
            sniffer.tick = real
        self.assertEqual(ticks["n"], 0, "a paused loop captured anyway")
        self.assertIn("paused", seen, "the loop never published that it was paused")
        self.assertEqual(presence.read(self.runs)["state"], "stopped")

    def _counting_stop(self, limit):
        n = {"i": 0}
        def stop():
            n["i"] += 1
            return n["i"] > limit
        return stop

    def test_a_nudge_cuts_the_wait_short(self):
        """Capture-now has to actually mean now. A nudge computed and then ignored left the caller
        waiting out the full interval."""
        ticks, slept = [], []
        real = sniffer.tick
        sniffer.tick = lambda *a, **k: (ticks.append(1), real(*a, **k))[1]
        def sleep(sec):
            slept.append(sec)
            if len(slept) == 1:
                presence.nudge(self.runs, 1.0)          # somebody presses "capture now"
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=1000,
                         stop=lambda: len(ticks) >= 2, now=lambda: CLOSED, sleep=sleep)
        finally:
            sniffer.tick = real
        self.assertEqual(len(ticks), 2)
        self.assertLess(sum(slept), 1000, "a nudge did not shorten the wait")

    def test_one_failed_tick_does_not_kill_the_watcher(self):
        calls = {"n": 0}
        real = sniffer.tick
        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(28, "No space left on device")
            return real(*a, **k)
        sniffer.tick = flaky
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0,
                         stop=lambda: calls["n"] >= 2, now=lambda: CLOSED, sleep=lambda s: None)
        finally:
            sniffer.tick = real
        self.assertEqual(calls["n"], 2, "the watcher died on the first failure")

    def test_a_crash_is_not_reported_as_a_clean_stop(self):
        real = presence.read_control
        presence.read_control = lambda root: (_ for _ in ()).throw(RuntimeError("control unreadable"))
        try:
            with self.assertRaises(RuntimeError):
                sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0,
                             stop=lambda: False, now=lambda: CLOSED, sleep=lambda s: None)
        finally:
            presence.read_control = real
        self.assertEqual(presence.read(self.runs)["state"], "error",
                         "a watcher that died was shown as stopped cleanly")

    def test_a_quiet_tick_does_not_rewrite_the_cursor_file(self):
        """At corpus size the cursor file is megabytes; fsyncing it every minute to record that
        nothing changed is pure cost."""
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        path = cursors.path_for(self.runs)
        before = os.stat(path).st_mtime_ns
        os.utime(path, ns=(before - 10 ** 9, before - 10 ** 9))   # make any rewrite visible
        marked = os.stat(path).st_mtime_ns
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(os.stat(path).st_mtime_ns, marked, "a quiet tick rewrote the cursor file")

    def test_a_nudge_while_paused_does_not_spin_the_loop(self):
        """A paused watcher must idle for its interval. An unconsumed nudge made every wait return
        after its first short step, rewriting presence.json every two seconds until resumed.

        The nudge arrives mid-run: issued before the loop starts it is already "seen", and a test
        written that way passes whether the fix is there or not.
        """
        presence.set_paused(self.runs, True)
        slept = []
        def sleep(sec):
            slept.append(sec)
            if len(slept) == 1:
                presence.nudge(self.runs, 1.0)          # "capture now", pressed while paused
        paused_writes = {"n": 0}
        real_write = presence.write
        def counting_write(root, **fields):
            if fields.get("state") == "paused":
                paused_writes["n"] += 1
            return real_write(root, **fields)
        presence.write = counting_write
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=10,
                         stop=lambda: sum(slept) >= 60, now=lambda: CLOSED, sleep=sleep)
        finally:
            presence.write = real_write
        # 60s idled at a 10s interval is about six iterations; spinning on the nudge is thirty.
        self.assertLessEqual(paused_writes["n"], 8,
                             f"{paused_writes['n']} paused iterations in 60s: the nudge kept cutting waits short")

    def test_a_stop_request_is_honoured_between_files_of_a_pass(self):
        """The first pass reads the whole corpus -- minutes. A stop must not have to wait it out."""
        from harnesslab.capture import backfill
        copy_fixture(os.path.join(self.src, "sess-cap-9.jsonl"))
        seen = {"n": 0}
        def stop():
            seen["n"] += 1
            return seen["n"] > 1                       # allow the first file, then stop
        rep = backfill.run([self.src], self.runs, gap_s=1800, now=CLOSED, stop=stop)
        self.assertTrue(rep.get("interrupted"))
        self.assertEqual(rep["files"], 1)

    def test_the_full_sweep_runs_on_its_cadence(self):
        """What a (size, mtime) cursor cannot see, only a full read finds -- so it has to happen."""
        fulls = []
        real = sniffer.tick
        clock = {"t": CLOSED}
        def spy(*a, **k):
            fulls.append(bool(k.get("full")))
            try:
                return real(*a, **k)
            finally:
                clock["t"] += 100            # time moves per PASS; the loop reads the clock many times
        sniffer.tick = spy
        def now():
            return clock["t"]
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0, full_sweep_s=250,
                         stop=lambda: len(fulls) >= 6, now=now, sleep=lambda s: None)
        finally:
            sniffer.tick = real
        self.assertTrue(any(fulls), "no full sweep ever ran")
        self.assertFalse(all(fulls), "every pass was a full sweep")
        self.assertGreater(presence.read(self.runs)["last_full_sweep_at"], 0)

    def test_an_interrupted_full_sweep_is_not_recorded_as_done(self):
        """A stop between files ends a full sweep early. Recording it as complete skipped the next
        real one for a day -- and this happened on the operator's lab, from a watcher stopped seconds in."""
        real = sniffer.tick
        calls = {"n": 0}
        def interrupted(*a, **k):
            calls["n"] += 1
            return {"state": "idle", "files": 1, "errors": [], "interrupted": True}
        sniffer.tick = interrupted
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0, full_sweep_s=100,
                         stop=lambda: calls["n"] >= 1, now=lambda: CLOSED, sleep=lambda s: None)
        finally:
            sniffer.tick = real
        self.assertEqual(presence.read(self.runs)["last_full_sweep_at"], 0.0,
                         "a sweep that was stopped part-way was recorded as done")

    def test_a_full_sweep_locked_out_by_another_writer_is_not_recorded_as_done(self):
        """tick() catches CaptureLocked and RETURNS, so the loop's own except never fires. A sweep that
        read nothing because a --backfill held the lock was recorded as done, skipping a day."""
        from harnesslab.capture.lock import CaptureLock
        calls = {"n": 0}
        real = sniffer.tick
        def counted(*a, **k):
            calls["n"] += 1
            return real(*a, **k)
        sniffer.tick = counted
        try:
            with CaptureLock(self.runs):                    # a manual import holds the capture lock
                sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0, full_sweep_s=100,
                             stop=lambda: calls["n"] >= 1, now=lambda: CLOSED, sleep=lambda s: None)
        finally:
            sniffer.tick = real
        self.assertEqual(presence.read(self.runs)["last_full_sweep_at"], 0.0,
                         "a sweep that could not take the lock was recorded as done")

    def test_a_pause_takes_effect_without_waiting_out_the_interval(self):
        """The wait woke for a nudge or a stop but not for a pause, so the menubar's pause sat unapplied
        for a whole interval while its label and the watcher's report disagreed."""
        ticks, slept = [], []
        real = sniffer.tick
        sniffer.tick = lambda *a, **k: (ticks.append(1), real(*a, **k))[1]
        def sleep(sec):
            slept.append(sec)
            if len(slept) == 1:
                presence.set_paused(self.runs, True)          # the menubar's click, mid-wait
        states = []
        def stop():
            states.append(presence.read(self.runs)["state"])
            return len(slept) > 3
        try:
            sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=1000, stop=stop,
                         now=lambda: CLOSED, sleep=sleep)
        finally:
            sniffer.tick = real
        self.assertIn("paused", states, "the pause was not applied before the interval ran out")
        self.assertLess(slept[0], 1000)

    def test_a_second_sniffer_refuses_to_start(self):
        from harnesslab.capture.lock import CaptureLocked
        with CaptureLock(self.runs, name="sniffer"):
            with self.assertRaises(CaptureLocked):
                sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0,
                             stop=lambda: True, now=lambda: CLOSED)


if __name__ == "__main__":
    unittest.main()
