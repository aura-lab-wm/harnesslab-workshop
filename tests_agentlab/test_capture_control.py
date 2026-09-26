"""Pause, resume and capture-now, from a terminal and from the console.

control.json is how anything asks the watcher to stop, start again, or capture now. Until now its
only writer was the macOS menubar app, so a watcher paused from the menu -- or by an app that was
since deleted -- could be seen on the Capture page and not restarted from it. Two writers are added
here, the CLI and one route, and both write the SAME two fields the sniffer already reads.

What the route may do is deliberately small (see capture_api's module docstring): control.json and
nothing else. It never starts a capture, and it never removes anything.
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

from harnesslab.backend.private_guard import OPT_IN_HEADER          # noqa: E402

OPT_IN = {OPT_IN_HEADER: "1"}


class TestControlCli(unittest.TestCase):
    """`python -m harnesslab.capture --pause | --resume | --nudge`.

    Run as a subprocess: RUNS_ROOT is resolved at import, so a lab passed with --lab is only really
    honoured by a process that had not already imported paths.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="hl-ctlhome-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.lab = os.path.join(self.home, "lab")
        os.makedirs(os.path.join(self.lab, "data", "runs"))

    def control_path(self):
        return os.path.join(self.lab, "data", "runs", "captured", "control.json")

    def control(self):
        with open(self.control_path(), encoding="utf-8") as f:
            return json.load(f)

    def run_cli(self, *argv):
        env = dict(os.environ, HOME=self.home,
                   # no allow-list: a control write reads no source, so it must not need one
                   HARNESSLAB_CAPTURE_CONFIG=os.path.join(self.home, "nothing.json"))
        env.pop("HARNESSLAB_LAB", None)
        return subprocess.run([sys.executable, "-B", "-m", "harnesslab.capture", "--lab", self.lab, *argv],
                              cwd=LAB, env=env, capture_output=True, text=True, timeout=120)

    def test_a_nudge_while_paused_says_the_watcher_will_throw_it_away(self):
        """The watcher consumes a nudge whether or not it acts on it (sniffer.loop: last_nudge is
        advanced, and a paused pass captures nothing). Recording one against a paused lab and saying
        nothing leaves the operator waiting for a pass that will never come."""
        self.run_cli("--pause")
        r = self.run_cli("--nudge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("paused", r.stderr.lower())
        self.assertIn("--resume", r.stderr)

    def test_a_nudge_on_a_running_lab_says_no_such_thing(self):
        r = self.run_cli("--nudge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("--resume", r.stderr)

    def test_pause_writes_the_pause_the_watcher_reads(self):
        r = self.run_cli("--pause")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.control()["paused"])

    def test_resume_clears_it_again(self):
        self.run_cli("--pause")
        r = self.run_cli("--resume")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.control()["paused"])

    def test_capture_now_does_not_quietly_resume_a_paused_watcher(self):
        """Asking for a pass is not asking to resume -- presence.nudge is built that way, and the
        CLI must not undo it by writing a fresh document."""
        self.run_cli("--pause")
        r = self.run_cli("--nudge")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = self.control()
        self.assertTrue(doc["paused"], "capture-now resumed a paused watcher")
        self.assertGreater(doc["nudge_at"], 0)

    def test_resume_keeps_the_nudge_it_did_not_ask_about(self):
        self.run_cli("--nudge")
        at = self.control()["nudge_at"]
        self.run_cli("--resume")
        self.assertEqual(self.control()["nudge_at"], at)

    def test_it_prints_what_the_control_file_now_says(self):
        r = self.run_cli("--pause")
        self.assertEqual(json.loads(r.stdout)["paused"], True)

    def test_a_control_flag_needs_no_allow_list_and_reads_no_source(self):
        """--backfill and --watch refuse without sources. A pause reads none, so refusing would
        leave a paused watcher unreachable on exactly the machine that has no allow-list yet."""
        r = self.run_cli("--resume")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("nothing to capture", r.stderr)

    def test_two_control_flags_at_once_are_refused(self):
        r = self.run_cli("--pause", "--resume")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.control_path()))

    def test_a_control_flag_does_not_combine_with_a_capture_mode(self):
        for other in ("--backfill", "--watch", "--seed-cursors", "--print-launchd"):
            with self.subTest(other=other):
                r = self.run_cli("--pause", other)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertFalse(os.path.exists(self.control_path()))

    def test_with_no_mode_at_all_it_still_prints_help(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 2)

    def test_the_parser_accepts_the_three_flags_by_their_full_spelling_only(self):
        from harnesslab.capture.__main__ import build_parser
        args = build_parser().parse_args(["--pause"])
        self.assertTrue(args.pause)
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--paus"])


class TestControlRoute(unittest.TestCase):
    """POST /api/capture/control -- the console's half of the same two fields."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-ctlapi-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "captured"))
        self.client = self._client(self.root)

    @staticmethod
    def _client(root):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from harnesslab.backend.private_guard import private_results_guard
        from harnesslab.backend.capture_api import capture_router
        app = FastAPI()
        app.middleware("http")(private_results_guard(lambda: root))
        app.include_router(capture_router(lambda: root))
        return TestClient(app)

    def control(self):
        from harnesslab.capture import presence
        return presence.read_control(self.root)

    def test_it_is_404_without_the_opt_in(self):
        r = self.client.post("/api/capture/control", json={"action": "pause"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(os.path.exists(os.path.join(self.root, "captured", "control.json")),
                         "an unmarked request wrote to the operator's lab")

    def test_pause_and_resume_move_the_one_flag_the_sniffer_reads(self):
        self.assertEqual(self.client.post("/api/capture/control", json={"action": "pause"},
                                          headers=OPT_IN).status_code, 200)
        self.assertTrue(self.control()["paused"])
        r = self.client.post("/api/capture/control", json={"action": "resume"}, headers=OPT_IN)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.control()["paused"])
        self.assertFalse(r.json()["paused"], "the response has to say what it just wrote")

    def test_capture_now_leaves_the_pause_alone(self):
        self.client.post("/api/capture/control", json={"action": "pause"}, headers=OPT_IN)
        r = self.client.post("/api/capture/control", json={"action": "nudge"}, headers=OPT_IN)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.control()["paused"], "capture-now resumed a paused watcher")
        self.assertGreater(self.control()["nudge_at"], 0)

    def test_an_action_it_does_not_know_is_refused_and_writes_nothing(self):
        for action in ("start", "backfill", "prune", "delete", "", None):
            with self.subTest(action=action):
                r = self.client.post("/api/capture/control", json={"action": action}, headers=OPT_IN)
                self.assertEqual(r.status_code, 422)
        self.assertFalse(os.path.exists(os.path.join(self.root, "captured", "control.json")))

    def test_it_writes_control_json_and_nothing_else(self):
        """The one file this router is allowed to touch. A route that started a capture would take
        the capture lock for minutes and rewrite inflight.json and identity.json wholesale."""
        cap = os.path.join(self.root, "captured")
        for action in ("pause", "nudge", "resume"):
            self.client.post("/api/capture/control", json={"action": action}, headers=OPT_IN)
        self.assertEqual([n for n in os.listdir(cap) if not n.startswith(".")], ["control.json"])

    def test_status_reports_the_control_the_page_is_about_to_change(self):
        """A watcher stopped while paused leaves paused standing in control.json, and presence says
        `stopped`, not `paused`. Without the control document the page cannot tell the operator that
        the next watcher starts paused."""
        self.client.post("/api/capture/control", json={"action": "pause"}, headers=OPT_IN)
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertTrue(d["control"]["paused"])

    def test_the_route_answers_on_the_real_app_and_demands_the_opt_in(self):
        from fastapi.testclient import TestClient
        from harnesslab.backend.app import app
        client = TestClient(app)
        self.assertEqual(client.post("/api/capture/control", json={"action": "nudge"}).status_code, 404,
                         "served without the opt-in")


if __name__ == "__main__":
    unittest.main()
