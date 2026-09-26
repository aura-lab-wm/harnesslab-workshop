"""The document the sniffer writes and everything else reads.

Two files, two directions. presence.json is the sniffer talking to the world: counts, states and
timestamps and NOTHING else -- a captured task id embeds a slug of the first prompt, and this
document is read by a menubar process and rendered into a menu title. control.json is the world
talking back: pause, and capture-now.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.capture import presence                      # noqa: E402
from test_capture_backfill import copy_fixture               # noqa: E402


class TestPresenceDocument(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-presence-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "captured"), exist_ok=True)

    def test_an_unwritten_presence_reads_as_never_started(self):
        d = presence.read(self.root)
        self.assertEqual(d["state"], "never_started")
        self.assertEqual(d["schema"], presence.SCHEMA)
        self.assertEqual(d["totals"]["runs_indexed"], 0)

    def test_read_returns_a_document_not_none(self):
        """Every caller does presence.read(root)["state"]; None would be a crash in a menu."""
        self.assertIsInstance(presence.read(self.root), dict)

    def test_a_write_round_trips_and_keeps_the_schema(self):
        presence.write(self.root, state="idle", totals={"runs_indexed": 7, "open_runs": 2})
        d = presence.read(self.root)
        self.assertEqual(d["state"], "idle")
        self.assertEqual(d["totals"]["runs_indexed"], 7)
        self.assertEqual(d["schema"], presence.SCHEMA)

    def test_an_unknown_state_is_clamped_rather_than_passed_through(self):
        """The value becomes a menu title in another process; it may only be one of the known words."""
        presence.write(self.root, state="on fire")
        self.assertIn(presence.read(self.root)["state"], presence.STATES)

    def test_an_error_is_flattened_to_one_line(self):
        presence.write(self.root, state="error", error="boom\nwith a traceback\nand more")
        err = presence.read(self.root)["error"]
        self.assertNotIn("\n", err)
        self.assertLessEqual(len(err), 200)

    def test_stopped_is_a_state_a_clean_shutdown_can_write(self):
        self.assertIn("stopped", presence.STATES)
        presence.write(self.root, state="stopped")
        self.assertEqual(presence.read(self.root)["state"], "stopped")

    # ------------------------------------------------------------------ privacy
    def test_a_capture_record_carries_counts_and_never_content(self):
        report = {"files": 3, "runs": 4, "closed_new": 2, "closed_updated": 1, "open": 1,
                  "errors": [{"path": "/Users/someone/.claude/projects/secret-proj/x.jsonl",
                              "error": "OSError: boom"}],
                  "input_tokens": 10, "output_tokens": 5, "cost_usd": 1.5,
                  "models": {"claude-opus-5": 4}}
        presence.write(self.root, totals={"runs_indexed": 900, "open_runs": 7})
        presence.record_capture(self.root, report, at=1789000000.0)
        blob = json.dumps(presence.read(self.root))
        self.assertNotIn("secret-proj", blob, "a source path reached the presence document")
        self.assertNotIn("someone", blob, "a username reached the presence document")
        self.assertNotIn("task_id", blob)
        d = presence.read(self.root)
        self.assertEqual(d["errors"], 1)                      # a COUNT, not the messages
        self.assertEqual(d["last_capture_at"], 1789000000.0)
        # This used to assert runs_indexed == 3 -- closed_new + closed_updated, the PASS's own
        # count, in the field whose name and whose every reader mean the corpus. That assertion
        # was the defect written down as the contract. A pass reports what it did; it does not
        # get to restate the size of the lab.
        self.assertEqual(d["totals"]["runs_indexed"], 900)
        self.assertEqual(d["totals"]["open_runs"], 7)
        # and when the caller DOES know the corpus, it travels in the same write
        presence.record_capture(self.root, report, at=1789000001.0,
                                totals={"runs_indexed": 901, "open_runs": 6})
        self.assertEqual(presence.read(self.root)["totals"]["runs_indexed"], 901)

    # ------------------------------------------------------------------ control
    def test_control_defaults_to_running_and_unnudged(self):
        c = presence.read_control(self.root)
        self.assertEqual(c["paused"], False)
        self.assertEqual(c["nudge_at"], 0)

    def test_pause_and_nudge_round_trip(self):
        presence.set_paused(self.root, True)
        self.assertTrue(presence.read_control(self.root)["paused"])
        presence.nudge(self.root, 1789000123.0)
        c = presence.read_control(self.root)
        self.assertEqual(c["nudge_at"], 1789000123.0)
        self.assertTrue(c["paused"], "a nudge must not silently unpause")

    def test_a_torn_control_file_reads_as_defaults_rather_than_raising(self):
        with open(presence.control_path(self.root), "w", encoding="utf-8") as f:
            f.write('{"paused": tr')
        self.assertEqual(presence.read_control(self.root)["paused"], False)

    def test_a_torn_presence_file_reads_as_never_started(self):
        with open(presence.path_for(self.root), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(presence.read(self.root)["state"], "never_started")


class TestTotalsNeverHoldThePassInsteadOfTheCorpus(unittest.TestCase):
    """`totals.runs_indexed` must mean the corpus, in every document a reader can observe.

    tick() used to write it twice, three lines apart: record_capture() put the PASS's closed count
    there, and the next write "corrected" it to the corpus total. Between the two, presence.json on
    disk said a corpus of 20,538 runs held 27 -- and the readers are a menu-bar app polling every
    five seconds and /api/capture/status. It was caught in the wild: the menu's plot differences
    consecutive readings, and it reported a scan that captured 20,511 runs.

    Every write during a tick is checked, not just the last one, because the defect was entirely in
    the intermediate state.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-totals-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        copy_fixture(os.path.join(self.src, "sess-cap-1.jsonl"))

    def _index_rows(self):
        idx = os.path.join(self.runs, "captured", "index.jsonl")
        if not os.path.exists(idx):
            return 0
        with open(idx, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def test_recording_a_pass_does_not_overwrite_the_corpus_count_with_it(self):
        """The defect, directly. `summarise` put the PASS's closed count into
        `totals.runs_indexed`, whose name and every reader mean the corpus."""
        presence.write(self.runs, totals={"runs_indexed": 20538, "open_runs": 18,
                                          "files_seen": 19573})
        presence.record_capture(self.runs, {"closed_new": 27, "closed_updated": 0, "open": 18,
                                            "files": 19573, "errors": []}, at=1.8e9)
        t = presence.read(self.runs)["totals"]
        self.assertEqual(t["runs_indexed"], 20538,
                         "a pass of 27 runs overwrote a corpus of 20,538")
        # what the pass legitimately reports still lands
        self.assertEqual(presence.read(self.runs)["last_capture_at"], 1.8e9)
        self.assertEqual(presence.read(self.runs)["errors"], 0)

    def test_no_document_written_during_a_tick_says_the_pass_count(self):
        from harnesslab.capture import sniffer

        # An ESTABLISHED corpus first -- which is the situation the defect needs. A reader looking
        # at a fresh lab mid-first-pass sees zeros, and zero really is what it holds.
        sniffer.tick([self.src], self.runs, gap_s=1800.0, now=1.8e9)
        established = self._index_rows()
        self.assertGreaterEqual(established, 1, "the fixture captured nothing")

        # A second source, so the next pass genuinely closes something and its pass count is a
        # small number next to the corpus -- exactly the shape that put "20511 runs" on screen.
        copy_fixture(os.path.join(self.src, "sess-cap-2.jsonl"))

        seen = []
        real = presence._atomic

        def spy(path, data):
            real(path, data)
            if path.endswith("presence.json") and isinstance(data, dict):
                seen.append(dict(data.get("totals") or {}))

        presence._atomic = spy
        self.addCleanup(setattr, presence, "_atomic", real)

        sniffer.tick([self.src], self.runs, gap_s=1800.0, now=1.8e9 + 60)
        rows = self._index_rows()
        self.assertTrue(seen, "no presence document was written at all")

        for t in seen:
            n = t.get("runs_indexed")
            self.assertIn(n, (established, rows),
                          f"a reader could see totals.runs_indexed={n} for a corpus of {rows}")


class TestTheDocumentHasNoFieldsNobodyWrites(unittest.TestCase):
    """A field the document declares, a reader renders, and no writer ever sets is a lie with a
    schema. Two of them shipped:

    `last_tick_at` -- the menu's "Last scan" row -- was written by nothing anywhere in the repo, so
    a watcher scanning every sixty seconds said "not yet" forever, in every screenshot of it.

    `progress` was only ever written as {0, 0}, so Health.capturing's "Capturing -- 812 of 19,576
    files" could not happen: the headline was always the bare word. That was the feedback missing
    during a first full sweep, which takes twenty-five minutes on a real corpus.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-fields-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        copy_fixture(os.path.join(self.src, "sess-cap-1.jsonl"))

    def test_a_completed_scan_records_when_it_happened(self):
        from harnesslab.capture import sniffer
        sniffer.tick([self.src], self.runs, gap_s=1800.0, now=1.8e9)
        d = presence.read(self.runs)
        self.assertEqual(d["last_tick_at"], 1.8e9,
                         "a scan ran and the document still says it never did")

    def test_the_capture_pass_reports_progress_against_a_real_total(self):
        from harnesslab.capture import backfill
        copy_fixture(os.path.join(self.src, "sess-cap-2.jsonl"))
        seen = []
        backfill.run([self.src], self.runs, 1800.0, now=1.8e9,
                     progress=lambda done, of: seen.append((done, of)))
        self.assertTrue(seen, "the pass reported no progress at all")
        totals = {of for _, of in seen}
        self.assertEqual(len(totals), 1, f"the total moved during the pass: {totals}")
        self.assertEqual(totals.pop(), 2, "the total is not the number of sources")
        self.assertEqual([d for d, _ in seen], [1, 2], "done did not count up")

    def test_a_tick_publishes_that_progress_where_the_menu_reads_it(self):
        from harnesslab.capture import sniffer
        copy_fixture(os.path.join(self.src, "sess-cap-2.jsonl"))
        seen = []
        real = presence._atomic

        def spy(path, data):
            real(path, data)
            if path.endswith("presence.json") and isinstance(data, dict):
                seen.append(dict(data.get("progress") or {}))

        presence._atomic = spy
        self.addCleanup(setattr, presence, "_atomic", real)
        sniffer.tick([self.src], self.runs, gap_s=1800.0, now=1.8e9)

        live = [p for p in seen if (p.get("of") or 0) > 0]
        self.assertTrue(live, "no document ever carried a real progress total")
        for p in live:
            self.assertLessEqual(p["done"], p["of"], "done ran past the total")
        # and it is cleared when the pass ends, so a finished scan does not look stuck mid-way
        self.assertEqual(presence.read(self.runs)["progress"], {"done": 0, "of": 0})


class TestTheErrorLineCarriesNoPath(unittest.TestCase):
    """The docstring above, and docs/capture.md, both say this document carries counts, states and
    timestamps and nothing that could carry prompt text. The sniffer wrote
    `f"{type(e).__name__}: {e}"` into it, and an exception message routinely names the file it
    failed on -- which here is a session transcript under ~/.claude/projects, whose directory
    component is a slug of the working directory it was recorded in. A menubar process renders
    this document into a menu title.

    The class name says what went wrong and carries nothing; the message goes to stderr, which is
    the watcher's log under launchd (~/Library/Logs/harnesslab-capture.log).
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-err-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "captured"), exist_ok=True)

    def test_a_tick_that_raises_records_the_kind_and_not_the_message(self):
        import io
        import contextlib
        from harnesslab.capture import sniffer

        secret = "/Users/someone/.claude/projects/-Users-someone-Projects-acme/abc.jsonl"

        def boom(*a, **k):
            raise FileNotFoundError(f"[Errno 2] No such file or directory: {secret!r}")

        real = sniffer._capture
        sniffer._capture = boom
        self.addCleanup(setattr, sniffer, "_capture", real)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(FileNotFoundError):
                sniffer.tick([self.root], self.root, 1800.0)

        d = presence.read(self.root)
        self.assertEqual(d["state"], "error")
        self.assertEqual(d["error"], "FileNotFoundError")
        self.assertNotIn("someone", json.dumps(d))
        self.assertNotIn(".claude", json.dumps(d))
        # and it is not simply thrown away: the watcher log still gets the whole thing
        self.assertIn(secret, err.getvalue())


class TestLiveness(unittest.TestCase):
    """Whether a watcher is actually running, asked of the lock rather than of a document it wrote."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-alive-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "captured"), exist_ok=True)

    def test_nobody_ever_started_is_not_alive(self):
        self.assertIs(presence.sniffer_alive(self.root), False)

    def test_a_held_sniffer_lock_is_alive(self):
        from harnesslab.capture.lock import CaptureLock
        with CaptureLock(self.root, name="sniffer"):
            self.assertIs(presence.sniffer_alive(self.root), True)

    def test_a_released_lock_is_not_alive_whatever_the_document_says(self):
        """kill -9 releases the flock but leaves presence.json saying idle."""
        from harnesslab.capture.lock import CaptureLock
        with CaptureLock(self.root, name="sniffer"):
            presence.write(self.root, state="idle", pid=4242)
        self.assertIs(presence.sniffer_alive(self.root), False)
        self.assertIsNone(presence.sniffer_pid(self.root))

    def test_a_mount_without_locking_is_unknown_not_alive(self):
        """ENOLCK is not contention. Reporting it as a live watcher read a dead pid out of a file."""
        import errno, fcntl
        from unittest import mock
        from harnesslab.capture.lock import CaptureLock
        with CaptureLock(self.root, name="sniffer"):
            pass                                  # create the lock file
        with mock.patch.object(fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks")):
            self.assertIsNone(presence.sniffer_alive(self.root))
            self.assertIsNone(presence.sniffer_pid(self.root))

    def test_probing_does_not_create_a_lock_file(self):
        presence.sniffer_alive(self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, "captured", ".sniffer.lock")))


if __name__ == "__main__":
    unittest.main()
