"""A LaunchAgent for the watcher, printed and never installed.

A watcher started from a terminal dies with the terminal. launchd keeps it running across logouts and
relaunches it when it crashes. Two properties matter more than the rest:

  * KeepAlive restarts only on a NON-ZERO exit. `true` would relaunch a watcher the operator had just
    stopped -- a clean stop exits 0 (see __main__: SIGTERM, which is how `launchctl bootout` stops a
    job, goes through the loop's clean path).
  * Nothing is written. Installing a login item is the operator's decision; the command to do it is
    printed beside the plist.

Measured on macOS with a throwaway agent: SIGTERM to a running watcher -> last exit code 0, not
relaunched; kill -9 -> "last terminating signal: Killed: 9", relaunched; SIGTERM to that relaunch ->
exit 0, not relaunched. One window remains: a SIGTERM that lands during interpreter startup, before
any Python runs, still kills by signal and is relaunched. __main__ installs its handler first thing
to keep that window as small as a process start.

Headless by construction: a LaunchAgent with no UI, driven entirely by launchctl.
"""
from __future__ import annotations

import os
import plistlib
import sys

LABEL = "com.harnesslab.capture.sniffer"

#: Capture settings read from the environment. launchd starts the watcher with none of the printing
#: shell's environment, so each one set there has to travel in the plist -- or the watcher silently
#: reads a different allow-list and archives to a different volume. The lab itself is pinned by
#: --lab. A test scans harnesslab/capture for every HARNESSLAB_CAPTURE_* it reads and fails if one is
#: missing here.
CARRIED = ("HARNESSLAB_CAPTURE_CONFIG", "HARNESSLAB_CAPTURE_ARCHIVE")


def carried_env(environ=None) -> dict:
    """The CARRIED settings that are set, as absolute paths -- launchd has no cwd or ~ to resolve them."""
    environ = os.environ if environ is None else environ
    return {k: os.path.abspath(os.path.expanduser(environ[k])) for k in CARRIED if environ.get(k)}

#: The directory that CONTAINS the harnesslab package, so `python -m harnesslab.capture` imports.
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _working_directory(lab: str) -> str:
    """Where `python -m harnesslab.capture` can import the package, and nothing else shadows it.

    In a checkout the package is importable only from the checkout, so that is where it starts.
    From an installed wheel the package is already on sys.path, and CODE_ROOT is site-packages --
    which python -m would put FIRST on sys.path, letting any stray module there with a standard-library
    name shadow the real one. The lab is a neutral place to start instead.
    """
    from harnesslab.backend.labroot import _is_checkout
    return CODE_ROOT if _is_checkout(CODE_ROOT) else os.path.abspath(os.path.expanduser(lab))


def render(lab: str, paths: list, interval_s: float, gap_minutes: float, archive: bool,
           python: str = sys.executable, env: dict = None) -> dict:
    argv = [python, "-m", "harnesslab.capture", "--watch", "--lab", os.path.abspath(os.path.expanduser(lab)),
            # repr, not %g: %g keeps six significant digits, so the watcher ran on a different
            # interval than the one typed (90.1234567 -> 90.1235, 1234567 -> 1.23457e+06).
            "--interval", repr(float(interval_s)), "--gap-minutes", repr(float(gap_minutes))]
    if not archive:
        argv.append("--no-archive")
    for p in paths or []:
        argv += ["--path", p]
    logs = os.path.join(os.path.expanduser("~"), "Library", "Logs", "harnesslab-capture.log")
    return {
        "Label": LABEL,
        "ProgramArguments": argv,
        "WorkingDirectory": _working_directory(lab),
        "RunAtLoad": True,
        # Restart a crash, never a requested stop.
        "KeepAlive": {"SuccessfulExit": False},
        # A watcher that exits immediately -- another one holds the lab, say -- must not spin.
        "ThrottleInterval": 60,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", **(carried_env() if env is None else env)},
        "StandardOutPath": logs,
        "StandardErrorPath": logs,
    }


def to_xml(doc: dict) -> bytes:
    return plistlib.dumps(doc, fmt=plistlib.FMT_XML, sort_keys=True)


def install_hint() -> str:
    target = os.path.join("~", "Library", "LaunchAgents", f"{LABEL}.plist")
    # Never `> {target}`: the shell truncates it before python runs, so one failed print leaves an
    # empty plist where a working agent was. Print to a temp file and move it only on success.
    return (f"Nothing was installed. To run the watcher under launchd:\n"
            f"  t=\"$(mktemp -t hl-sniffer-agent)\" && python3 -m harnesslab.capture --print-launchd ... > \"$t\" \\\n"
            f"    && mkdir -p ~/Library/LaunchAgents && mv \"$t\" {target} \\\n"
            f"    && launchctl bootstrap gui/$(id -u) {target}\n"
            f"To stop it and remove it:\n"
            f"  launchctl bootout gui/$(id -u)/{LABEL} && rm {target}")


#: The menubar app's agent. Versioned with the bundle, so v2 is a new job and never a silent
#: replacement of v1's.
MENUBAR_LABEL = "com.harnesslab.capture.menubar.v1"


def render_menubar(app: str, lab: str) -> dict:
    """A LaunchAgent that starts the status-bar app at login.

    Limited to the Aqua session: a menu-bar presence has nothing to show without a logged-in desktop.
    KeepAlive restarts it only on a crash, because Quit exits 0 and must stay quit. Like the watcher's
    agent it is printed and never loaded -- whether its status item is visible can only be seen in a
    logged-in session, which is why that is the operator's step.
    """
    app = os.path.abspath(os.path.expanduser(app))
    return {
        "Label": MENUBAR_LABEL,
        "ProgramArguments": [os.path.join(app, "Contents", "MacOS", "CaptureMenubar"),
                             "--lab", os.path.abspath(os.path.expanduser(lab))],
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 60,
        "ProcessType": "Interactive",
    }

