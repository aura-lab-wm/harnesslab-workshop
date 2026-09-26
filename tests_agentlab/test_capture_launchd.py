"""--print-launchd: the watcher, supervised, without this tool ever installing anything.

A watcher started from a terminal dies with the terminal. launchd keeps it running across logouts
and restarts it when it crashes -- but a stop the operator asked for must stay a stop, so the agent
restarts only on a non-zero exit. The plist is printed, never written: installing a login item is
the operator's decision, and the command to do it is printed beside it.
"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.capture import launchd                        # noqa: E402


class TestLaunchdPlist(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="hl-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.lab = os.path.join(self.home, "lab")
        os.makedirs(os.path.join(self.lab, "data", "runs"))

    def render(self, **kw):
        base = dict(lab=self.lab, paths=["/Users/x/.claude/projects"], interval_s=60.0,
                    gap_minutes=30.0, archive=True, python="/usr/bin/python3")
        base.update(kw)
        return launchd.render(**base)

    def test_it_is_a_valid_plist(self):
        doc = plistlib.loads(launchd.to_xml(self.render()))
        self.assertEqual(doc["Label"], launchd.LABEL)

    def test_a_clean_stop_is_not_restarted_but_a_crash_is(self):
        """KeepAlive true would relaunch a watcher the operator just stopped."""
        doc = self.render()
        self.assertEqual(doc["KeepAlive"], {"SuccessfulExit": False})
        self.assertTrue(doc["RunAtLoad"])
        self.assertGreaterEqual(doc["ThrottleInterval"], 30, "a crash loop would spin")

    def test_its_command_line_is_one_the_cli_accepts_as_watch(self):
        """The ProgramArguments are parsed by the real parser, so a renamed flag fails here."""
        from harnesslab.capture.__main__ import build_parser
        doc = self.render(archive=False)
        argv = doc["ProgramArguments"]
        self.assertEqual(argv[:3], ["/usr/bin/python3", "-m", "harnesslab.capture"])
        args = build_parser().parse_args(argv[3:])
        self.assertTrue(args.watch)
        self.assertEqual(args.lab, self.lab)
        self.assertEqual(args.interval, 60.0)
        self.assertEqual(args.path, ["/Users/x/.claude/projects"])
        self.assertTrue(args.no_archive)

    def test_the_interval_and_gap_survive_the_round_trip_exactly(self):
        """%g keeps six significant digits: 90.1234567 became 90.1235, 1234567 became 1.23457e+06."""
        from harnesslab.capture.__main__ import build_parser
        for interval, gap in ((90.1234567, 30.0), (1234567.0, 12.345678901)):
            with self.subTest(interval=interval, gap=gap):
                argv = self.render(interval_s=interval, gap_minutes=gap)["ProgramArguments"]
                args = build_parser().parse_args(argv[3:])
                self.assertEqual(args.interval, interval)
                self.assertEqual(args.gap_minutes, gap)

    def test_outside_a_checkout_the_watcher_does_not_start_in_site_packages(self):
        """python -m puts the working directory first on sys.path. In site-packages, a stray module
        with a standard-library name would shadow the real one and the watcher would crash-loop."""
        from unittest import mock
        with mock.patch("harnesslab.backend.labroot._is_checkout", return_value=False):
            doc = self.render()
        self.assertEqual(doc["WorkingDirectory"], os.path.abspath(self.lab))

    def test_the_working_directory_is_where_the_package_can_be_imported(self):
        doc = self.render()
        self.assertTrue(os.path.isdir(os.path.join(doc["WorkingDirectory"], "harnesslab", "capture")))

    def test_plutil_accepts_it(self):
        if not shutil.which("plutil"):
            self.skipTest("plutil is macOS-only")
        p = os.path.join(self.home, "agent.plist")
        with open(p, "wb") as f:
            f.write(launchd.to_xml(self.render()))
        r = subprocess.run(["plutil", "-lint", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_cli_prints_and_installs_nothing(self):
        env = dict(os.environ, HOME=self.home)
        r = subprocess.run([sys.executable, "-m", "harnesslab.capture", "--print-launchd",
                            "--lab", self.lab, "--path", "/Users/x/.claude/projects"],
                           cwd=LAB, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(plistlib.loads(r.stdout.encode())["Label"], launchd.LABEL)
        self.assertIn("launchctl bootstrap", r.stderr, "the install command should be printed, not run")
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library", "LaunchAgents")),
                         "printing a plist must not install one")


class TestPrintLaunchdCli(unittest.TestCase):
    """What the CLI puts in the plist has to be the watcher it means."""

    def run_main(self, argv):
        import io
        from unittest import mock
        from harnesslab.capture.__main__ import main
        out = io.BytesIO()
        fake = mock.Mock()
        fake.buffer = out
        err = io.StringIO()
        with mock.patch("sys.stdout", fake), mock.patch("sys.stderr", err):
            try:
                code = main(argv)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_an_abbreviated_flag_is_refused_rather_than_half_honoured(self):
        """argparse accepted --wat as --watch while the early SIGTERM handler looked for the literal
        '--watch', so watch mode started without it and crashed. Reproduced before this test."""
        code, _, _ = self.run_main(["--wat", "--path", "/nonexistent", "--interval", "0.1"])
        self.assertEqual(code, 2)

    def test_without_lab_the_plist_names_the_lab_the_app_resolves(self):
        """Not the package directory: from an installed wheel that is site-packages, and the watcher
        would write ledgers there instead of where the Capture page reads."""
        from unittest import mock
        with mock.patch("harnesslab.backend.labroot.resolve_lab_root", return_value="/resolved/lab") as r:
            code, out, _ = self.run_main(["--print-launchd", "--path", "/x"])
        self.assertEqual(code, 0)
        argv = plistlib.loads(out)["ProgramArguments"]
        self.assertEqual(argv[argv.index("--lab") + 1], "/resolved/lab")
        self.assertFalse(r.call_args.kwargs.get("seed", True), "printing a plist must not create a workspace")

    def test_a_tilde_in_lab_is_expanded(self):
        doc = launchd.render(lab="~/somelab", paths=[], interval_s=60, gap_minutes=30, archive=True)
        argv = doc["ProgramArguments"]
        self.assertEqual(argv[argv.index("--lab") + 1], os.path.join(os.path.expanduser("~"), "somelab"))

    def test_a_plist_whose_watcher_could_never_start_is_not_printed(self):
        """--interval 0 exits 2 on every start, and KeepAlive would relaunch it every minute forever."""
        code, out, _ = self.run_main(["--print-launchd", "--path", "/x", "--interval", "0"])
        self.assertEqual(code, 2)
        self.assertEqual(out, b"")

    def test_print_launchd_does_not_silently_drop_another_mode(self):
        code, out, _ = self.run_main(["--print-launchd", "--backfill", "--path", "/x"])
        self.assertEqual(code, 2)
        self.assertEqual(out, b"")


class TestPrintLaunchdTouchesNothing(unittest.TestCase):
    """Run in a subprocess: an in-process test cannot see an import-time side effect, because the
    import has already happened -- the earlier test's own mock.patch imported paths.py, which is how
    a created workspace went unnoticed."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="hl-pl-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def cli(self, *args, extra_env=None):
        env = {k: v for k, v in os.environ.items() if not k.endswith("_LAB")}
        env.update({"HOME": self.home}, **(extra_env or {}))
        return subprocess.run([sys.executable, "-B", "-m", "harnesslab.capture", *args], cwd=LAB,
                              env=env, capture_output=True, text=True, timeout=120)

    def test_printing_a_plist_creates_no_workspace(self):
        """Reproduced before the fix: this left newlab/{data,harnesses,tasks} on disk."""
        newlab = os.path.join(self.home, "newlab")
        r = self.cli("--print-launchd", "--path", "/x", extra_env={"HARNESSLAB_LAB": newlab})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(newlab), "printing a plist created a lab workspace")

    def test_the_pure_resolver_can_be_imported_without_side_effects(self):
        newlab = os.path.join(self.home, "elsewhere")
        env = {k: v for k, v in os.environ.items() if not k.endswith("_LAB")}
        env.update({"HOME": self.home, "HARNESSLAB_LAB": newlab})
        r = subprocess.run([sys.executable, "-B", "-c",
                            "from harnesslab.backend.labroot import resolve_lab_root as r; print(r(seed=False))"],
                           cwd=LAB, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), newlab)
        self.assertFalse(os.path.exists(newlab))

    def test_a_plist_with_nothing_to_capture_is_not_printed(self):
        """No --path and no allow-list: its watcher exits 2 "nothing to capture" on every start, and
        KeepAlive would relaunch it every minute forever."""
        r = self.cli("--print-launchd", "--lab", os.path.join(self.home, "lab"))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("nothing to capture", r.stderr)


class TestThePlistCarriesTheEnvironmentItWasPrintedWith(unittest.TestCase):
    """launchd starts the watcher with none of the printing shell's environment. Every setting the
    shell changed has to travel in the plist, or the watcher silently captures a different allow-list
    and archives somewhere else."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="hl-plenv-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def test_every_capture_setting_read_from_the_environment_is_carried(self):
        """A guard, not an example: scan the capture code for the variables it reads, so one added
        later without being carried fails here rather than in a review."""
        import re
        read = set()
        for root, _, files in os.walk(os.path.join(LAB, "harnesslab", "capture")):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(root, name), encoding="utf-8") as f:
                        read |= set(re.findall(r"HARNESSLAB_CAPTURE_[A-Z_]+", f.read()))
        self.assertTrue(read, "the scan found nothing -- it is no longer looking in the right place")
        self.assertEqual(read - set(launchd.CARRIED), set(), "read from the environment but not carried")

    def test_a_config_and_archive_set_in_the_shell_reach_the_plist_as_absolute_paths(self):
        cfg = os.path.join(self.home, "capture.json")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('{"paths": ["%s"]}' % self.home)
        env = {k: v for k, v in os.environ.items() if not k.endswith("_LAB")}
        env.update(HOME=self.home, HARNESSLAB_CAPTURE_CONFIG=cfg,
                   HARNESSLAB_CAPTURE_ARCHIVE=os.path.join(self.home, "big-archive"))
        r = subprocess.run([sys.executable, "-B", "-m", "harnesslab.capture", "--print-launchd",
                            "--lab", os.path.join(self.home, "lab")],
                           cwd=LAB, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        carried = plistlib.loads(r.stdout.encode())["EnvironmentVariables"]
        self.assertEqual(carried.get("HARNESSLAB_CAPTURE_CONFIG"), cfg)
        self.assertEqual(carried.get("HARNESSLAB_CAPTURE_ARCHIVE"), os.path.join(self.home, "big-archive"))

    def test_unset_settings_are_not_invented(self):
        doc = launchd.render(lab="/l", paths=["/p"], interval_s=60, gap_minutes=30, archive=True, env={})
        self.assertEqual(set(doc["EnvironmentVariables"]), {"PYTHONUNBUFFERED"})


class TestMenubarAgent(unittest.TestCase):
    """The menubar app at login: a GUI job, printed like the watcher's and never loaded here."""

    APP = "/Applications/HarnessLab Capture v1.app"

    def render(self):
        return launchd.render_menubar(app=self.APP, lab="/some/lab")

    def test_it_is_a_separate_job_from_the_watcher(self):
        doc = self.render()
        self.assertNotEqual(doc["Label"], launchd.LABEL)
        self.assertTrue(doc["Label"].endswith(".v1"), "the agent follows the bundle's version")

    def test_it_runs_the_bundle_binary_on_the_lab(self):
        doc = self.render()
        self.assertEqual(doc["ProgramArguments"],
                         [self.APP + "/Contents/MacOS/CaptureMenubar", "--lab", "/some/lab"])

    def test_it_is_limited_to_a_login_session_and_restarts_only_on_a_crash(self):
        """A status-bar app has nothing to show without Aqua, and Quit exits 0: that must stay quit."""
        doc = self.render()
        self.assertEqual(doc["LimitLoadToSessionType"], "Aqua")
        self.assertEqual(doc["KeepAlive"], {"SuccessfulExit": False})

    def cli(self, home, app):
        env = {k: v for k, v in os.environ.items() if not k.endswith("_LAB")}
        env["HOME"] = home
        return subprocess.run([sys.executable, "-B", "-m", "harnesslab.capture", "--print-menubar-agent",
                               "--app", app, "--lab", os.path.join(home, "lab")],
                              cwd=LAB, env=env, capture_output=True, text=True, timeout=120)

    def fake_bundle(self, home):
        app = os.path.join(home, "Applications", "HarnessLab Capture v1.app")
        os.makedirs(os.path.join(app, "Contents", "MacOS"))
        exe = os.path.join(app, "Contents", "MacOS", "CaptureMenubar")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(exe, 0o755)
        return app

    def test_the_cli_prints_it_and_installs_nothing(self):
        home = tempfile.mkdtemp(prefix="hl-mbagent-")
        self.addCleanup(shutil.rmtree, home, True)
        r = self.cli(home, self.fake_bundle(home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(plistlib.loads(r.stdout.encode())["ProgramArguments"][1:],
                         ["--lab", os.path.join(home, "lab")])
        self.assertFalse(os.path.exists(os.path.join(home, "Library", "LaunchAgents")))

    def test_the_printed_hints_never_redirect_straight_onto_a_plist(self):
        """> truncates the target before python runs: one failure and a working agent becomes an empty
        file. Both hints write a temp file first and move it, as the watcher's setup command does."""
        home = tempfile.mkdtemp(prefix="hl-mbagent-")
        self.addCleanup(shutil.rmtree, home, True)
        hints = [self.cli(home, self.fake_bundle(home)).stderr, launchd.install_hint()]
        for hint in hints:
            self.assertIn("mktemp", hint)
            self.assertIn("mv ", hint)
            self.assertIn("mkdir -p", hint)          # a fresh account has no LaunchAgents directory
            for line in hint.splitlines():
                if "mv " in line:
                    self.assertTrue(line.rstrip().endswith("\\") or "&&" in line.split("mv ")[1],
                                    f"bootstrap runs even when the move failed: {line.strip()}")
            for line in hint.splitlines():
                if ">" in line and "LaunchAgents" in line:
                    self.fail(f"redirects onto the plist: {line.strip()}")

    def test_it_refuses_to_print_an_agent_for_a_bundle_that_is_not_there(self):
        """KeepAlive relaunches a failed start every ThrottleInterval: a missing app would spin forever."""
        home = tempfile.mkdtemp(prefix="hl-mbagent-")
        self.addCleanup(shutil.rmtree, home, True)
        for app in (os.path.join(home, "nowhere.app"), os.path.join(home, "Applications")):
            r = self.cli(home, app)
            self.assertEqual(r.returncode, 2, app)
            self.assertEqual(r.stdout, "", "nothing to redirect into a plist")
            self.assertIn("CaptureMenubar", r.stderr)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "needs the Swift toolchain")
    @unittest.skipUnless(os.path.isdir(os.path.join(LAB, "macos", "CaptureMenubar")),
                          f"requires the full checkout: {os.path.join('macos', 'CaptureMenubar')}")
    def test_its_arguments_are_ones_the_binary_accepts(self):
        """Fed to the real binary with a read-only command appended, so a renamed flag fails here."""
        pkg = os.path.join(LAB, "macos", "CaptureMenubar")
        b = subprocess.run(["swift", "build"], cwd=pkg, capture_output=True, text=True, timeout=900)
        self.assertEqual(b.returncode, 0, b.stderr[-1500:])
        lab = tempfile.mkdtemp(prefix="hl-mblab-")
        self.addCleanup(shutil.rmtree, lab, True)
        argv = launchd.render_menubar(app=self.APP, lab=lab)["ProgramArguments"][1:]
        r = subprocess.run([os.path.join(pkg, ".build", "debug", "CaptureMenubar"), *argv, "--status-json"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["runs_root"], os.path.join(lab, "data", "runs"))


if __name__ == "__main__":
    unittest.main()
