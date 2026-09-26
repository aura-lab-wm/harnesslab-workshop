"""The menubar app and the sniffer, held to one document by running both.

Fixtures are not typed: a real sniffer writes presence.json and the Swift binary reads it, and the
Swift binary writes control.json and Python reads it. A hand-fed fixture would prove only that a
fixture and a reader agree, while the document the sniffer really writes drifted away from both.
Nothing here starts the status-bar app; every check goes through its argv router.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.join(LAB, "tests_agentlab"))

from test_capture_backfill import copy_fixture                   # noqa: E402
from harnesslab.capture import presence, sniffer                  # noqa: E402
from harnesslab.capture.lock import CaptureLock                   # noqa: E402

PKG = os.path.join(LAB, "macos", "CaptureMenubar")
CLOSED = 1.8e9


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "needs macOS and a Swift toolchain")
@unittest.skipUnless(os.path.isdir(PKG), f"requires the full checkout: {PKG}")
class TestMenubarReadsWhatTheSnifferWrites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run(["swift", "build"], cwd=PKG, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise AssertionError("swift build failed:\n" + r.stdout[-2000:] + r.stderr[-2000:])
        cls.binary = os.path.join(PKG, ".build", "debug", "CaptureMenubar")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-menubar-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lab = os.path.join(self.tmp, "lab")
        self.runs = os.path.join(self.lab, "data", "runs")
        self.src = os.path.join(self.tmp, "sessions")
        os.makedirs(self.src)
        copy_fixture(os.path.join(self.src, "sess-cap-1.jsonl"))

    def app(self, *args):
        r = subprocess.run([self.binary, "--lab", self.lab, *args], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_a_sniffer_that_has_ticked_and_stopped_reads_as_not_running(self):
        n = {"i": 0}
        sniffer.loop([self.src], self.runs, gap_s=1800, interval_s=0,
                     stop=lambda: (n.__setitem__("i", n["i"] + 1), n["i"] > 1)[1],
                     now=lambda: CLOSED, sleep=lambda s: None)
        self.assertEqual(presence.read(self.runs)["state"], "stopped")
        menu = self.app("--menu-json")
        self.assertEqual(menu["rows"][0]["title"], "Capture service is not running")

    def test_while_the_sniffer_holds_its_lock_the_menu_shows_its_real_counts(self):
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)   # the sniffer writes the document
        indexed = presence.read(self.runs)["totals"]["runs_indexed"]
        self.assertGreaterEqual(indexed, 1)
        with CaptureLock(self.runs, name="sniffer"):                  # ... and a watcher is alive
            status = self.app("--status-json")
            menu = self.app("--menu-json")
        self.assertEqual(status["alive"], True)
        self.assertEqual(status["runs_indexed"], indexed)
        titles = [r["title"] for r in menu["rows"]]
        self.assertTrue(titles[0].startswith("Watching"), titles[0])
        # The corpus section is a name/value pair split on a tab; --menu-json breaks it out so this
        # assertion is about the fact and not about the column width.
        # The headline number rides under the state line as a nameless row, not as a column in the
        # table below it: in a row of four identical greys the one figure this app exists to show
        # looked exactly as important as the metered dollars.
        # The page is a ledger now: two name/value pairs per line, so the figures stand in columns.
        cells = {c["name"]: c["value"] for r in menu["rows"] for c in r.get("cells", [])}
        self.assertEqual(cells.get("runs"), f"{indexed:,}")
        self.assertEqual(cells.get("open"), "0")
        self.assertIsNone(menu["glyph"]["tint"], "a healthy watcher is a template glyph, not a colour")

    def test_the_menu_is_a_tree_and_every_branch_is_printable(self):
        """The app's whole testability rests on --menu-json: nothing run from a terminal can open a
        menu, so a row whose contents live in a submenu is only checkable if the JSON carries it."""
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        with CaptureLock(self.runs, name="sniffer"):
            menu = self.app("--menu-json")
        by_title = {r["title"]: r for r in menu["rows"]}
        # Details is gone: a ledger page is read at once, so the timings and the health are LINES on
        # it. Settings stays behind a door -- it is what the record is kept with, not the record.
        self.assertNotIn("Details", by_title)
        self.assertIn("Settings", by_title, [r["title"] for r in menu["rows"]])
        self.assertTrue(by_title["Settings"]["submenu"], "Settings opens an empty submenu")
        cells = {c["name"]: c["value"] for r in menu["rows"] for c in r.get("cells", [])}
        for name in ("runs", "open", "captured", "scanned", "sweep", "debris"):
            self.assertIn(name, cells, f"the ledger has no {name} line")
        # never counted is not "none" -- the sniffer has not run a full sweep in this fixture
        self.assertEqual(cells["debris"], "not checked yet")
        settings = by_title["Settings"]["submenu"]
        names = {r.get("name") for r in settings}
        self.assertLessEqual({"Scan interval", "Watching", "Lab root", "Allow-list", "Watcher log"}, names)
        self.assertTrue(any(r["title"] == "Start at login" for r in settings))

    def test_the_menu_draws_and_every_row_fits_the_pane_it_is_in(self):
        """The look was written blind until --render-menu existed, and it showed: a fixed tab stop
        put every value in a different place per pane and ran long names into their own values. The
        rows draw themselves now, so the check is that each one ASKS for a width that fits inside
        the pane the widest row sets -- which is exactly the invariant that was broken."""
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        png = os.path.join(self.tmp, "menu.png")
        with CaptureLock(self.runs, name="sniffer"):
            out = self.app("--render-menu", png, "--samples", "10,12,12,30")
        self.assertTrue(os.path.exists(png), out)
        self.assertGreater(os.path.getsize(png), 5000, "the render is suspiciously empty")
        self.assertGreater(out["width"], 200)
        self.assertGreater(out["height"], 100)

    def test_a_clickable_row_is_the_only_kind_that_carries_an_action(self):
        """A custom view gets none of AppKit's highlighting, so a row that does something has to
        light up by itself. The rule this asserts is the one the shim keys off: a detail row with a
        real action is clickable, and an informational one never is."""
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        with CaptureLock(self.runs, name="sniffer"):
            menu = self.app("--menu-json")
        settings = next(r for r in menu["rows"] if r["title"] == "Settings")["submenu"]
        acting = [r for r in settings if "name" in r and r["action"] != "none"]
        self.assertTrue(acting, "no clickable fact rows at all")
        for r in acting:
            self.assertTrue(r["action"].startswith("copyPath:"), r["action"])
            self.assertTrue(r["enabled"], f"{r['name']} acts but is drawn disabled")
        for r in settings:
            if "name" in r and r["action"] == "none":
                self.assertFalse(r["enabled"], f"{r['name']} is lit but does nothing")

    def test_no_row_anywhere_in_the_tree_carries_a_task_id(self):
        """A captured task id embeds a slug of the first prompt. presence.json carries none, and this
        asserts the menu cannot have acquired one from anywhere else either -- at any depth."""
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        with CaptureLock(self.runs, name="sniffer"):
            menu = self.app("--menu-json")
        rows = []
        def walk(rs):
            for r in rs:
                rows.append(r["title"])
                walk(r.get("submenu") or [])
        walk(menu["rows"])
        self.assertGreater(len(rows), 12, "the tree flattened to fewer rows than the menu has")
        with open(os.path.join(self.runs, "captured", "index.jsonl"), encoding="utf-8") as f:
            ids = [json.loads(l)["task_id"] for l in f if l.strip()]
        self.assertTrue(ids)
        blob = "\n".join(rows)
        for tid in ids:
            self.assertNotIn(tid, blob, "a task id reached the menu")

    def test_a_watcher_killed_hard_is_not_shown_as_running(self):
        """kill -9 leaves the document saying idle; the Swift probe asks the lock, like the page does."""
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        self.assertEqual(presence.read(self.runs)["state"], "idle")
        self.assertEqual(self.app("--status-json")["health"], "not_running")

    def test_what_swift_writes_python_reads(self):
        self.app("--pause")
        self.assertEqual(presence.read_control(self.runs)["paused"], True)
        before = presence.read_control(self.runs)["nudge_at"]
        self.app("--nudge")
        c = presence.read_control(self.runs)
        self.assertGreater(c["nudge_at"], before)
        self.assertTrue(c["paused"], "capture-now must not silently resume")
        self.app("--resume")
        self.assertEqual(presence.read_control(self.runs)["paused"], False)

    def test_what_python_writes_swift_reads(self):
        presence.set_paused(self.runs, True)
        sniffer.tick([self.src], self.runs, gap_s=1800, now=CLOSED)
        presence.write(self.runs, state="paused")
        with CaptureLock(self.runs, name="sniffer"):
            menu = self.app("--menu-json")
        self.assertEqual(menu["rows"][0]["title"], "Capture is paused")
        self.assertIn("Resume capture", [r["title"] for r in menu["rows"]])


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "needs macOS and a Swift toolchain")
@unittest.skipUnless(os.path.isdir(PKG), f"requires the full checkout: {PKG}")
class TestMenubarReviewFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run(["swift", "build"], cwd=PKG, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-2000:])
        cls.binary = os.path.join(PKG, ".build", "debug", "CaptureMenubar")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-mbfix-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lab = os.path.join(self.tmp, "lab")
        self.runs = os.path.join(self.lab, "data", "runs")

    def run_app(self, *args):
        r = subprocess.run([self.binary, "--lab", self.lab, *args], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_coloured_glyphs_really_have_colour_when_drawn(self):
        """Rendered offscreen through the same function the shim uses, and the pixels read back."""
        for state, want in (("failed", (0xe5, 0x48, 0x4d)), ("not_running", (0xff, 0x7a, 0x1a))):
            with self.subTest(state=state):
                d = self.run_app("--render-glyph", f"{state}:{os.path.join(self.tmp, state + '.png')}")
                got = tuple(int(d["mean_color"][i:i + 2], 16) for i in (1, 3, 5))
                self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(got, want)), f"{state} drew {d['mean_color']}")

    def test_the_pause_row_follows_the_request_while_the_watcher_catches_up(self):
        from harnesslab.capture import presence
        from harnesslab.capture.lock import CaptureLock
        os.makedirs(os.path.join(self.runs, "captured"), exist_ok=True)
        presence.write(self.runs, state="idle", interval_s=60, roots=1, heartbeat_at=CLOSED)
        self.run_app("--pause")                          # asked, not yet applied by the watcher
        with CaptureLock(self.runs, name="sniffer"):
            rows = self.run_app("--menu-json")["rows"]
        self.assertEqual(rows[0]["title"], "Pausing…")
        self.assertEqual([r["action"] for r in rows if r["title"] in ("Pause capture", "Resume capture")], ["resume"])

    def test_a_coloured_glyph_still_has_its_mark_knocked_out(self):
        """A palette colour painted EVERY layer solid, so exclamationmark.circle.fill lost its mark and
        drew as a plain disc. The mean colour is identical either way, so the layers are counted: a
        symbol rendered hierarchically keeps a half-lit body behind its full-strength mark."""
        for state in ("failed", "not_running"):
            d = self.run_app("--render-glyph", f"{state}:{os.path.join(self.tmp, state + '.png')}")
            self.assertGreater(d["half_lit"], 100, f"{state} drew one flat shape, with no mark on it")

    def test_two_commands_at_once_are_refused(self):
        """Each is a write or a read; running one and dropping the other exits 0 for work not done."""
        for pair in (("--pause", "--nudge"), ("--status-json", "--nudge"), ("--menu-json", "--status-json")):
            r = subprocess.run([self.binary, "--lab", self.lab, *pair], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, f"{pair} -> {r.stdout[:200]}")

    def test_an_uninstalled_service_offers_the_setup_command(self):
        if subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.harnesslab.capture.sniffer"],
                          capture_output=True).returncode == 0:
            self.skipTest("the real watcher agent is loaded on this machine")
        rows = self.run_app("--menu-json")["rows"]
        titles = [r["title"] for r in rows]
        self.assertIn("Copy the command that installs the capture service", titles)
        self.assertNotIn("Start capture service", titles)
        self.assertEqual(self.run_app("--status-json")["agent_loaded"], False)

    def test_there_is_one_agent_label_and_no_flag_to_split_it(self):
        """--agent-label changed which job was probed and restarted but not which plist the setup command
        installed, so a custom label offered setup forever. The label is fixed; the flag is gone."""
        r = subprocess.run([self.binary, "--lab", self.lab, "--agent-label", "x.y", "--menu-json"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout)
        src = ""
        for root, _, files in os.walk(os.path.join(PKG, "Sources")):
            for f in files:                          # .swift only: a .DS_Store here is not UTF-8
                if f.endswith(".swift"):
                    with open(os.path.join(root, f), encoding="utf-8") as fh:
                        src += fh.read()
        self.assertEqual(src.count('"com.harnesslab.capture.sniffer"'), 1, "one definition, in Setup.swift")


if __name__ == "__main__":
    unittest.main()
