"""python -m harnesslab.capture --backfill | --watch | --sweep | --prune-orphans [--apply]
                             [--path DIR ...] [--gap-minutes 30] [--lab DIR]

  --backfill        capture every allow-listed source once, print a JSON report, exit.
  --watch           stay up and capture what changed, every --interval seconds, until interrupted.
  --pause           ask a watcher to stop capturing; --resume undoes it, --nudge asks for a pass now.
  --sweep           report crash debris in captured/ (scratch left by a killed writer). Removes or
                    restores it only with --apply.
  --prune-orphans   report runs no source produces any more. Removes them only with --apply, and a
                    run whose source file is merely gone -- Claude Code deletes sessions after 30
                    days -- only with --prune-deleted-sources on top of it.

`--backfill` reads every source every time. `--watch` reads only what moved since its last pass --
on a corpus of 19,380 files that is the difference between minutes and nothing at all -- and
publishes what it is doing to captured/presence.json, which the Capture page reads.

Exit 0 on success, 1 if any source failed, 2 on bad usage or an --apply refused because another writer
holds the lab, 130 on interrupt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    # 3.14's argparse colorizes --help by default. Deciding whether to do so touches
    # os.isatty(sys.stdout.fileno()) the moment the first argument (its own -h/--help) is added --
    # so constructing ANY ArgumentParser now crashes wherever sys.stdout is not a real file: a test
    # double with a Mock-typed fileno(), a frozen/embedded launcher, a supervisor that redirects
    # stdout to something without one (reproduced here: TestPrintLaunchdCli patches sys.stdout with
    # unittest.mock.Mock(), which happily returns another Mock from .fileno(), and os.isatty()
    # cannot use that as a file descriptor). Passing color=False to ArgumentParser does NOT fix
    # this: _get_formatter() builds the HelpFormatter with just prog=..., so HelpFormatter.__init__
    # runs its OWN default (color=True) and probes stdout before the parser's color value is ever
    # applied. The only switch _colorize.can_colorize() honours before it touches the file object
    # is the PYTHON_COLORS env var, so that is what actually has to be set, for the whole time this
    # parser might format help (construction now, and any later --help/-h). This is a headless CLI
    # with no benefit from ANSI colour; setdefault() still lets an operator force PYTHON_COLORS=1
    # explicitly. Harmless on Python < 3.14: the variable is simply not read there.
    os.environ.setdefault("PYTHON_COLORS", "0")
    p = argparse.ArgumentParser(
        # No prefix matching. `--wat` used to be accepted as --watch while main()'s early SIGTERM
        # handler looked for the literal flag, so watch mode started without its handler and
        # crashed. A flag has one spelling.
        allow_abbrev=False,
        prog="python -m harnesslab.capture",
        description="Capture coding-agent sessions into private harnesslab ledgers (data/runs/captured/).")
    p.add_argument("--backfill", action="store_true", help="capture every allow-listed session once, then exit")
    p.add_argument("--watch", action="store_true",
                   help="stay up and capture what changed, every --interval seconds")
    p.add_argument("--seed-cursors", action="store_true",
                   help="adopt a corpus already captured by --backfill so --watch does not read it again; "
                        "stats sources, reads none")
    p.add_argument("--pause", action="store_true",
                   help="ask the watcher to stop capturing (writes captured/control.json)")
    p.add_argument("--resume", action="store_true", help="undo --pause")
    p.add_argument("--nudge", action="store_true",
                   help="ask the watcher to capture now, without resuming a pause")
    p.add_argument("--sweep", action="store_true",
                   help="report crash debris (<run>.tmp-<pid>/, <run>.old-<pid>/, .tmp-* files) in captured/; "
                        "report-only unless --apply")
    p.add_argument("--prune-orphans", action="store_true",
                   help="report captured runs that no source produces any more; report-only unless --apply")
    p.add_argument("--apply", action="store_true",
                   help="with --sweep or --prune-orphans: actually remove (and restore) what the report lists")
    p.add_argument("--prune-deleted-sources", action="store_true",
                   help="with --prune-orphans --apply: ALSO remove runs whose source file is gone. Claude Code "
                        "deletes session files after 30 days, so these are usually captures of real work whose "
                        "archived source is the only copy left; without this flag they are reported and kept")
    p.add_argument("--prune-ignore-unreadable", action="store_true",
                   help="with --prune-orphans: continue even though a directory under the roots could not "
                        "be read. By default that stops the pass, because a source hidden in there may "
                        "still produce the runs about to be deleted; runs whose own source lies in the "
                        "unreadable subtree are refused either way")
    p.add_argument("--interval", type=float, default=60.0, metavar="S",
                   help="seconds between passes when watching (default 60)")
    p.add_argument("--path", action="append", default=[],
                   help="a session directory or file to capture (repeatable); defaults to the allow-list "
                        "in ~/.harnesslab/capture.json")
    p.add_argument("--gap-minutes", type=float, default=30.0,
                   help="quiet time that closes a segment (default 30)")
    p.add_argument("--no-archive", action="store_true",
                   help="do not keep gzip copies of the captured sources")
    p.add_argument("--lab", default=None, metavar="DIR", help="lab root holding data/runs/ (default: resolved as usual)")
    p.add_argument("--print-menubar-agent", action="store_true",
                   help="print a LaunchAgent that starts the menubar app at login; installs nothing")
    p.add_argument("--app", default="~/Applications/HarnessLab Capture v1.app", metavar="PATH",
                   help="the menubar app bundle, for --print-menubar-agent")
    p.add_argument("--print-launchd", action="store_true",
                   help="print a LaunchAgent plist that runs --watch under launchd; installs nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    stopping = None
    if "--watch" in (sys.argv[1:] if argv is None else argv):
        # FIRST, before parsing or importing the lab. SIGTERM arriving before a handler exists kills
        # the process by signal, which launchd reads as a crash and relaunches -- measured: a stop
        # sent while a relaunched watcher was still starting up came straight back. Watch mode only;
        # a one-shot --backfill must still die when told to.
        import signal
        import threading
        stopping = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    p = build_parser()
    args = p.parse_args(argv)

    if args.watch and stopping is None:          # belt and braces: never run the loop without it
        import signal
        import threading
        stopping = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())

    # What this run asks of a watcher, rather than does itself. Counted here so every mode below can
    # refuse to silently drop one: `--print-launchd --pause` printed a plist and paused nothing.
    control = [f for f in ("pause", "resume", "nudge") if getattr(args, f)]

    if args.print_menubar_agent:
        from . import launchd
        if args.backfill or args.watch or args.seed_cursors or args.print_launchd or control:
            print("--print-menubar-agent does not combine with another mode", file=sys.stderr)
            return 2
        if args.lab:
            lab = os.path.abspath(os.path.expanduser(args.lab))
        else:
            from harnesslab.backend.labroot import resolve_lab_root
            lab = resolve_lab_root(seed=False)
        doc = launchd.render_menubar(app=args.app, lab=lab)
        exe = doc["ProgramArguments"][0]
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            # launchd would fail every start and KeepAlive relaunch it every ThrottleInterval, forever.
            print(f"no app binary at {exe}: build the bundle and copy it there, or pass --app", file=sys.stderr)
            return 2
        sys.stdout.buffer.write(launchd.to_xml(doc))
        target = os.path.join("~", "Library", "LaunchAgents", f"{launchd.MENUBAR_LABEL}.plist")
        print(f"Nothing was installed. To start the menubar app at login:\n"
              f'  t="$(mktemp -t hl-menubar-agent)" '
              f'&& python3 -m harnesslab.capture --print-menubar-agent ... > "$t" \\\n'
              f'    && mkdir -p ~/Library/LaunchAgents && mv "$t" {target} \\\n'
              f"    && launchctl bootstrap gui/$(id -u) {target}", file=sys.stderr)
        return 0

    if args.print_launchd:
        from . import launchd
        if (args.backfill or args.watch or args.seed_cursors or control
                or args.sweep or args.prune_orphans):
            print("--print-launchd prints a watcher for launchd; it does not combine with another mode",
                  file=sys.stderr)
            return 2
        if args.interval <= 0:
            # Its watcher would exit 2 on every start, and KeepAlive would relaunch it every minute.
            print("--interval must be positive", file=sys.stderr)
            return 2
        if args.lab:
            lab = os.path.abspath(os.path.expanduser(args.lab))
        else:
            # The lab the app itself resolves -- a checkout, HARNESSLAB_LAB, or ~/.harnesslab -- and
            # never the package directory, which from an installed wheel is site-packages. From
            # labroot, not paths: importing paths resolves LAB_ROOT with seeding on, which created a
            # workspace before seed=False here could matter.
            from harnesslab.backend.labroot import resolve_lab_root
            lab = resolve_lab_root(seed=False)
        from . import allowlist
        paths = [os.path.realpath(os.path.expanduser(x)) for x in args.path]
        if not paths and not allowlist.load_paths():
            # Its watcher would print "nothing to capture" and exit 2 on every start, and KeepAlive
            # would relaunch it every minute forever.
            print("nothing to capture: pass --path, or add paths to the capture allow-list", file=sys.stderr)
            return 2
        doc = launchd.render(lab=lab, paths=paths,
                             interval_s=args.interval, gap_minutes=args.gap_minutes,
                             archive=not args.no_archive)
        sys.stdout.buffer.write(launchd.to_xml(doc))
        print(launchd.install_hint(), file=sys.stderr)
        return 0

    if control:
        # Before the mode check below, and before any allow-list is read: writing control.json reads
        # no source, so refusing it without one would leave a paused watcher unreachable on exactly
        # the machine that has nothing allow-listed yet -- which is where a pause is hardest to undo.
        if len(control) > 1 or args.backfill or args.watch or args.seed_cursors:
            print("choose one of --pause, --resume, --nudge, on its own", file=sys.stderr)
            return 2
        import time
        if args.lab:
            os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))
        from harnesslab.backend.paths import RUNS_ROOT   # after HARNESSLAB_LAB, as everywhere here
        from . import presence
        if control[0] == "nudge":
            presence.nudge(RUNS_ROOT, time.time())       # never touches `paused`: see presence.nudge
        else:
            presence.set_paused(RUNS_ROOT, control[0] == "pause")
        state = presence.read_control(RUNS_ROOT)
        if control[0] == "nudge" and state.get("paused"):
            # The watcher CONSUMES a nudge whether or not it acts on it (sniffer.loop advances
            # last_nudge before it checks `paused`), so a request recorded against a paused lab is
            # not queued -- it is thrown away by the next pass. Saying nothing leaves the operator
            # waiting for a capture that will never happen.
            print("this lab is paused: the watcher will consume this request without capturing. "
                  "Run --resume first", file=sys.stderr)
        if presence.sniffer_alive(RUNS_ROOT) is not True:
            # Said out loud, because the file alone cannot: control.json is read by a watcher between
            # passes, so with none running the request is recorded and acted on by the next one. A
            # resume that appears to have worked, on a machine where nothing is watching, is the
            # same trap in the other direction.
            print("no watcher is running on this lab — the request stands for the next one",
                  file=sys.stderr)
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0

    modes = [m for m in ("backfill", "watch", "seed_cursors", "sweep", "prune_orphans") if getattr(args, m)]
    if args.apply and not (args.sweep or args.prune_orphans):
        print("--apply only goes with --sweep or --prune-orphans", file=sys.stderr)
        return 2
    if args.prune_deleted_sources and not args.prune_orphans:
        print("--prune-deleted-sources only goes with --prune-orphans", file=sys.stderr)
        return 2
    if not modes:
        p.print_help()
        return 2
    if len(modes) > 1:
        print("choose one of --backfill, --watch, --seed-cursors, --sweep, --prune-orphans", file=sys.stderr)
        return 2
    if args.watch and args.interval <= 0:
        print("--interval must be positive", file=sys.stderr)
        return 2
    if args.lab:
        os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))

    from harnesslab.backend.paths import RUNS_ROOT   # after HARNESSLAB_LAB: paths resolves at import
    from . import allowlist, archive, backfill

    if args.sweep:
        # Reads no source, so it needs no allow-list: debris is a property of the lab alone.
        from . import sweep
        report = sweep.sweep(RUNS_ROOT, apply=args.apply)
        print(json.dumps(report, indent=2, sort_keys=True))
        if args.apply and report["locked"]:
            return 2
        # 1 when something could not be done: a removal that failed, or a report that could not
        # judge because a writer held the lab. Exit 0 said "clean" for both.
        if any(e.get("done") is False for e in report["debris"]) and args.apply:
            return 1
        return 1 if report["locked"] else 0

    roots = [os.path.realpath(os.path.expanduser(x)) for x in args.path] or allowlist.load_paths()
    if not roots:
        print("nothing to capture: pass --path, or add paths to the capture allow-list", file=sys.stderr)
        return 2
    gap_s = args.gap_minutes * 60
    archive_root = None if args.no_archive else archive.default_root()

    if args.prune_orphans:
        from . import orphans
        report = orphans.prune(roots, RUNS_ROOT, gap_s=gap_s, apply=args.apply,
                               deleted_sources=args.prune_deleted_sources,
                               ignore_unreadable=args.prune_ignore_unreadable)
        print(json.dumps(report, indent=2, sort_keys=True))
        if report.get("gap_mismatch"):
            gaps = ", ".join(f"{g / 60:g}" for g in report["gap_mismatch"])
            print(f"this lab was captured with --gap-minutes {gaps}; run the prune with the same "
                  f"value, because a different gap cuts different runs", file=sys.stderr)
            return 2
        if args.apply and report["locked"]:
            return 2
        # 1 when a source could not be established, as --backfill and --seed-cursors report: a caller
        # cannot otherwise tell "no orphans" from "read nothing".
        blind = {"unreadable", "unreachable", "inconsistent"}
        # A directory that would not go is a failure in both modes; --sweep already says so.
        if report.get("failed"):
            return 1
        return 1 if any(e.get("reason") in blind for e in report["refused"]) else 0

    if args.seed_cursors:
        from . import sniffer
        from .lock import CaptureLocked
        try:
            report = sniffer.seed(roots, RUNS_ROOT, gap_s=gap_s)
        except CaptureLocked as e:
            print(f"cannot seed while a watcher or a capture is running on this lab ({e})", file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["errors"] else 0

    if args.watch:
        from . import sniffer
        from .lock import CaptureLocked
        # SIGTERM is how launchd, systemd and a plain `kill` ask a job to stop. With no handler Python
        # dies on the spot, the loop's cleanup never runs, and the page reports a crash for a stop
        # somebody requested. The handler installed at the top of main() sets a flag instead; the
        # loop checks it between short waits and exits through the same clean path as ctrl-c.
        print(f"watching {len(roots)} root(s) every {args.interval:g}s — ctrl-c to stop", file=sys.stderr)
        try:
            sniffer.loop(roots, RUNS_ROOT, gap_s=gap_s, interval_s=args.interval,
                         archive_root=archive_root, stop=stopping.is_set)
        except CaptureLocked as e:
            # The lock's own words: "another capture writer holds ..." for contention, "cannot
            # lock ..." for a mount without locking. Always claiming a second watcher misled.
            print(str(e), file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("stopped", file=sys.stderr)
            return 130
        return 0

    report = backfill.run(roots, RUNS_ROOT, gap_s=gap_s, archive_root=archive_root)
    # `sources` is per-file detail kept for the sniffer. Printed, it is every session path on the
    # machine with its run ids -- the content the presence document is built to keep out.
    report.pop("sources", None)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
