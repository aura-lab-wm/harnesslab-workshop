"""``python -m harnesslab`` / the ``harnesslab`` console script.

Flags are parsed *before* the backend is imported, because
``harnesslab.backend.paths`` resolves the lab root at import time from the environment.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

__version__ = "0.3.0"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harnesslab",
        description="harnesslab -- the web platform over harnesslab.core: run coding-agent cells, watch the "
                    "ledger live, compare harnesses, and train the sentinel.",
        epilog="subcommands: `harnesslab run ...` sweeps the lab's own harnesses; "
               "`harnesslab watch [<dir>[/<run_id>]]` follows a run's ledger in the terminal as it "
               "is written; `harnesslab import <path>` reads another harness's trace into the "
               "ledger (`--detect` first, to see what it is); `harnesslab oracle <dir>`, "
               "`harnesslab comparisons <dir> [--baseline B]` and `harnesslab fit <dir>` print the "
               "same canonical study-aggregate statistics as GET /api/results/<dir>/{oracle,"
               "comparisons,experiment} as JSON; `harnesslab serve` is this, the default.",
    )
    p.add_argument("--host", default=os.environ.get("HARNESSLAB_HOST", "127.0.0.1"),
                   help="interface to bind (default 127.0.0.1; use 0.0.0.0 in a container)")
    p.add_argument("--port", type=int, default=int(os.environ.get("HARNESSLAB_PORT", "8765")),
                   help="port to bind (default 8765)")
    p.add_argument("--lab", default=None, metavar="DIR",
                   help="lab root holding harnesses/, tasks/ and data/runs/. Defaults to the "
                        "checkout when running from one, else ~/.harnesslab (seeded on first run).")
    p.add_argument("--no-browser", action="store_true",
                   help="do not open a browser window on startup")
    p.add_argument("--log-level", default="warning", choices=["critical", "error", "warning", "info", "debug"])
    p.add_argument("--export", metavar="FILE.html", default=None,
                   help="write one self-contained HTML file with the data embedded, and exit. "
                        "Opens from disk with no server; live actions are absent by construction.")
    p.add_argument("--export-results", metavar="A,B", default=None,
                   help="comma-separated results directories to embed (default: all of them)")
    p.add_argument("--export-field", metavar="FILE.html", default=None,
                   help="write one self-contained Field (the projector cold-open) with its corpus "
                        "embedded, and exit. Opens from disk with no server.")
    p.add_argument("--export-runs", type=int, default=None, metavar="N",
                   help="how many per-run detail payloads to embed for the Trajectories page "
                        "(default 40; 0 embeds none and keeps the file small)")
    p.add_argument("--yes", action="store_true",
                   help="confirm exporting private results (captured sessions) without a prompt")
    p.add_argument("--paths", action="store_true", help="print the resolved paths as JSON and exit")
    p.add_argument("--version", action="version", version=f"harnesslab {__version__}")
    return p


def suggest_results_dir(source: str, path: str) -> str:
    """The results directory an import falls back to when nobody named one.

    The import screen offers the same name from the same rule (frontend/src/method/importing.js),
    so running the import from the command line and running it from the UI cannot quietly file the
    same trace under two different studies. The name is always a legal results dir: one path
    segment, no leading dot.
    """
    base = os.path.basename(os.path.normpath(os.path.expanduser(path or "")))
    slug = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")[:24].strip("_")
    src = re.sub(r"[^a-z0-9]+", "_", (source or "").lower()).strip("_") or "trace"
    return f"imported_{src}" + (f"_{slug}" if slug else "")


def build_import_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="harnesslab import",
        description="Read a coding-agent trace recorded by some other harness into this lab's "
                    "ledger format, so every page measures it the way it measures a lab run.",
        epilog="Capture (`python -m harnesslab.capture --watch`) already follows the five "
               "interactive CLIs on this machine by itself. This command is the other road in: "
               "batch traces -- SWE-agent, Inspect, OpenHands, Trajectory v1 -- that no watcher "
               "will ever see, plus any session store sitting somewhere capture does not look.",
    )


def _add_import_args(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("path", help="a trace file, or a directory of them")
    p.add_argument("--detect", action="store_true",
                   help="say what the detector makes of this path -- the adapter it would pick and "
                        "every adapter's confidence -- and import nothing")
    p.add_argument("--source", default=None, metavar="ADAPTER",
                   help="force an adapter instead of sniffing (see --detect for the list)")
    p.add_argument("--results-dir", dest="results_dir", default=None, metavar="NAME",
                   help="the results directory to write into, under data/runs/ "
                        "(default: imported_<source>_<path slug>)")
    p.add_argument("--lab", default=None, metavar="DIR",
                   help="lab root to import into (default: the same one `harnesslab serve` uses)")
    return p


#: `data/runs/captured/` has one writer at a time (capture spine spec 5.7): the watcher's batch,
#: or an import, never both. Contention is normal operation, not a failure of this command.
LOCKED_SENTENCE = ("another capture writer holds the captured directory -- the watcher mid-batch, "
                   "or another import. Wait for it, or pause the watcher first:\n"
                   "  python -m harnesslab.capture --pause")


class _NoLock(Exception):
    """Stands in for CaptureLocked where harnesslab.capture cannot be imported at all."""


try:                                             # capture is part of this package; be safe anyway
    from harnesslab.capture.lock import CaptureLocked as CAPTURE_LOCKED
except Exception:                                # pragma: no cover - only on a broken install
    CAPTURE_LOCKED = _NoLock


def _fmt_sessions(n) -> str:
    return "unknown sessions" if n is None else f"{n} session{'' if n == 1 else 's'}"


def _print_adapters(adapters: list[dict]) -> None:
    print("\nthe adapters, any of which you can force with --source:")
    for a in adapters:
        print(f"  {a['name']:<14}{a['description']}")


def import_cli(argv: list[str]) -> int:
    """`harnesslab import <path>` -- the command line over the import adapters.

    Exit codes: 0 did it, 1 the answer was no (nothing there, nothing recognised, nothing
    imported), 2 the invocation was wrong (unknown adapter, illegal results dir).
    """
    args = _add_import_args(build_import_parser()).parse_args(argv)
    if args.lab:
        os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))

    from harnesslab.backend import paths                      # noqa: E402  (after HARNESSLAB_LAB)
    from harnesslab.backend import importers as I             # noqa: E402

    adapters = I.list_sources()
    known = [a["name"] for a in adapters]
    path = os.path.abspath(os.path.expanduser(args.path))

    if args.source and args.source not in known:
        print(f"unknown adapter {args.source!r}", file=sys.stderr)
        _print_adapters(adapters)
        return 2
    if not os.path.exists(path):
        print(f"no such file or directory: {path}", file=sys.stderr)
        return 1

    if args.detect:
        d = I.detect_detail(path)
        kind = "directory" if d["is_dir"] else "file"
        print(f"path       {d['path']}")
        print(f"kind       {kind}" + (f" · {_fmt_sessions(d['sessions'])}" if d["source"] else ""))
        if not d["source"]:
            print("detected   nothing -- no adapter scored this path at 0.50 or better")
        else:
            desc = next((a["description"] for a in adapters if a["name"] == d["source"]), "")
            print(f"detected   {d['source']}")
            print(f"           {desc}")
        if d["candidates"]:
            print("\nper-adapter confidence" + ("  (mean over the files each one won)" if d["is_dir"] else ""))
            for c in d["candidates"]:
                over = f"  over {c['files']} file{'' if c['files'] == 1 else 's'}" if d["is_dir"] else ""
                print(f"  {c['source']:<14}{c['confidence']:.2f}{over}")
        if d["error"]:
            print(f"\n{d['error']}", file=sys.stderr)
        if not d["source"]:
            _print_adapters(adapters)
            return 1
        out = args.results_dir or suggest_results_dir(d["source"], path)
        print(f"\nimport it with:\n  harnesslab import {path} --results-dir {out}")
        return 0

    src = args.source or I.detect(path)
    if not src:
        print(f"no adapter recognises {path}", file=sys.stderr)
        print("run it through --detect to see the scores, or name one with --source", file=sys.stderr)
        _print_adapters(adapters)
        return 1

    out = args.results_dir or suggest_results_dir(src, path)
    if not out or "/" in out or os.sep in out or out.startswith("."):
        print(f"bad results dir name {out!r}: it is one directory under data/runs/, "
              f"not a path", file=sys.stderr)
        return 2

    print(f"source     {src}  ({'forced' if args.source else 'detected'})")
    print(f"path       {path}")
    print(f"into       {os.path.join(paths.RUNS_ROOT, out)}")

    def progress(done, total, label):
        if sys.stderr.isatty():
            print(f"\r  {done} read", end="", file=sys.stderr, flush=True)

    try:
        r = I.import_path(path, out, source=src, progress=progress, runs_root=paths.RUNS_ROOT)
    except (FileNotFoundError, ValueError) as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    except CAPTURE_LOCKED as e:
        # CaptureLocked is a RuntimeError, so it used to leave here as a traceback with a flock
        # path in it -- which is what a person got whenever the watcher happened to be mid-batch.
        # The lock is expected contention, not a bug: say so, and say what to do about it.
        print(f"\n{LOCKED_SENTENCE}", file=sys.stderr)
        print(f"  ({e})", file=sys.stderr)
        return 1
    finally:
        if sys.stderr.isatty():
            print("\r          \r", end="", file=sys.stderr, flush=True)

    known_v, unknown_v = r["outcomes_known"], r["outcomes_unknown"]
    print(f"imported   {r['imported']} run{'' if r['imported'] == 1 else 's'}")
    print(f"skipped    {r['skipped']} already in this results directory")
    print(f"tasks      {r['n_tasks']}")
    # The honesty rule the adapters are built on, said out loud: a run whose trace carries no
    # verdict imports with hidden_pass = None, and every outcome page reads that as "not passed".
    print(f"verdicts   {known_v} of {known_v + unknown_v} runs carry a real one"
          + (f"; the other {unknown_v} import with an unknown outcome, which the outcome pages "
             f"read as not passed -- treat pass@1 here as a lower bound" if unknown_v else ""))
    for h in r["harnesses"]:
        f = h.get("fingerprint") or {}
        print(f"harness    {h['harness_id']}  {h['runs']} run{'' if h['runs'] == 1 else 's'}  "
              f"hash {h['hash']}  tools {' '.join(f.get('lab_tools') or []) or '--'}")
    if r["errors"]:
        print(f"\n{len(r['errors'])} session(s) failed:", file=sys.stderr)
        for e in r["errors"][:10]:
            print(f"  {e}", file=sys.stderr)
        return 1
    if not r["imported"] and not r["skipped"]:
        print("\nnothing to import: the adapter found no sessions under that path", file=sys.stderr)
        return 1
    return 0


def build_watch_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harnesslab watch",
        description="Follow a run's ledger in the terminal as it is written -- two lanes, what the "
                    "agent asked for │ what the harness returned -- and exit when the run ends.",
        epilog="`harnesslab watch` alone lists the runs written in the last 24 hours and their state "
               "(running / finished / abandoned); `harnesslab watch <dir>` follows the newest run in "
               "progress in that results directory; `harnesslab watch <dir>/<run_id>` (or a run "
               "directory path) follows that run. Exit 0 when the run finished, 2 when it was "
               "abandoned (no span for --idle-timeout seconds), 1 when there is nothing to follow.",
    )
    p.add_argument("target", nargs="?", default=None, help="<dir>, <dir>/<run_id>, or a run directory path")
    p.add_argument("--from-seq", type=int, default=0, help="skip spans below this seq (default 0: replay all)")
    p.add_argument("--idle-timeout", type=float, default=None, metavar="SECONDS",
                   help="give up after this long without a new span (default 600, the grace the run list uses)")
    p.add_argument("--poll", type=float, default=0.25, metavar="SECONDS", help="how often to look for new spans")
    p.add_argument("--recent", type=float, default=86400.0, metavar="SECONDS",
                   help="for the listing: how far back to look (default 86400)")
    p.add_argument("--lab", default=None, metavar="DIR", help="lab root (default: the one `harnesslab serve` uses)")
    return p


def _watch_target(target: str, runs_root: str, is_private, live):
    """(ledger path, error message). A run directory path (or its ledger.jsonl) is taken as-is;
    otherwise `<dir>` or `<dir>/<run_id>` under the runs root, never a private directory."""
    t = os.path.expanduser(target)
    if os.path.isdir(t) and os.path.isfile(os.path.join(t, "ledger.jsonl")):
        return os.path.join(t, "ledger.jsonl"), ""
    if os.path.isfile(t) and os.path.basename(t) == "ledger.jsonl":
        return t, ""
    parts = [p for p in target.strip("/").split("/") if p]
    if not 1 <= len(parts) <= 2 or any(p.startswith(".") for p in parts):
        return None, f"not a run: {target!r} (expected <dir>, <dir>/<run_id>, or a run directory path)"
    if is_private(runs_root, parts[0]):
        return None, f"{parts[0]} holds captured sessions, which watch never lists; give the run directory path explicitly"
    if len(parts) == 1:
        rows = [r for r in live.scan(runs_root, recent=live.RUNNING_GRACE_S, exclude=())
                if r["dir"] == parts[0] and r["state"] == "running"]
        if not rows:
            return None, f"no run in progress in {parts[0]}"
        return os.path.join(runs_root, parts[0], rows[0]["run_id"], "ledger.jsonl"), ""
    p = os.path.join(runs_root, parts[0], parts[1], "ledger.jsonl")
    real_root = os.path.realpath(runs_root)
    if os.path.commonpath([real_root, os.path.realpath(p)]) != real_root or not os.path.isfile(p):
        return None, f"no such run: {parts[0]}/{parts[1]}"
    return p, ""


def watch_cli(argv: list[str], runs_root: str | None = None) -> int:
    """`harnesslab watch` -- see build_watch_parser. `runs_root` is for tests; the command line
    resolves it through harnesslab.backend.paths after --lab is applied."""
    args = build_watch_parser().parse_args(argv)
    if args.lab:
        os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))
    from harnesslab.backend.results_scope import PRIVATE_DIRS, is_private   # noqa: E402
    from harnesslab.core import live                                         # noqa: E402
    if runs_root is None:
        from harnesslab.backend import paths                                 # noqa: E402  (after HARNESSLAB_LAB)
        runs_root = paths.RUNS_ROOT

    if not args.target:
        rows = live.scan(runs_root, recent=args.recent, exclude=PRIVATE_DIRS)
        if not rows:
            print(f"no runs written in the last {int(args.recent)} s under {runs_root}")
            return 0
        print(f"{'state':<10} {'dir/run_id':<44} {'task':<22} {'harness':<16} {'model':<24} {'seq':>4}  last write")
        for r in rows:
            seq = "-" if r["last_seq"] is None else str(r["last_seq"])
            print(f"{r['state']:<10} {(r['dir'] + '/' + r['run_id'])[:44]:<44} {str(r['task_id'] or '?')[:22]:<22} "
                  f"{str(r['harness_id'] or '?')[:16]:<16} {str(r['model'] or '?')[:24]:<24} {seq:>4}  {r['last_ts'] or ''}")
        return 0

    path, err = _watch_target(args.target, runs_root, is_private, live)
    if path is None:
        print(err, file=sys.stderr)
        return 1
    rc = 0
    idle = args.idle_timeout if args.idle_timeout is not None else live.RUNNING_GRACE_S
    if args.idle_timeout is None and live.state_of(path) == "abandoned":
        idle = 0.0                                   # already silent past the grace: replay, say so, exit 2
    for rec in live.tail(path, from_seq=args.from_seq, poll=args.poll, idle_timeout=idle):
        print(live.render_line(rec), flush=True)
        if rec.get("span") == "live_status" and rec.get("status") == "abandoned":
            rc = 2
    return rc


def _study_rows_or_die(dir_name: str, runs_root: str):
    """`(rows, "")` for a results directory's index.jsonl, or `(None, message)` when there is no
    such study -- the one loader every study-aggregate subcommand below shares, so `oracle`,
    `comparisons` and `fit` fail the same legible way `_dir()` does for the HTTP routes."""
    d = os.path.join(runs_root, dir_name)
    if not os.path.exists(os.path.join(d, "index.jsonl")):
        return None, f"no results dir {dir_name!r} under {runs_root}"
    from harnesslab.core.analysis import load_index          # noqa: E402
    return load_index(d), ""


def build_oracle_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="harnesslab oracle",
        description="Agreement between the hidden-test verdict and the strong-suite verdict over "
                    "every run in a results directory (Cohen's kappa, chance-corrected; null with a "
                    "reason when undefined, never NaN) -- the SAME canonical harnesslab.core.analysis."
                    "cohens_kappa GET /api/results/<dir>/oracle serves, printed as JSON.",
    )


def _add_dir_and_lab_args(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("dir", help="results directory name under data/runs/")
    p.add_argument("--lab", default=None, metavar="DIR", help="lab root (default: the one `harnesslab serve` uses)")
    return p


def oracle_cli(argv: list[str]) -> int:
    """`harnesslab oracle <dir>` -- see build_oracle_parser. Exit 0 printed the JSON, 1 no such dir."""
    args = _add_dir_and_lab_args(build_oracle_parser()).parse_args(argv)
    if args.lab:
        os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))
    from harnesslab.backend import paths                      # noqa: E402  (after HARNESSLAB_LAB)
    from harnesslab.backend import metrics as M                # noqa: E402
    import json                                                # noqa: E402
    rows, err = _study_rows_or_die(args.dir, paths.RUNS_ROOT)
    if rows is None:
        print(err, file=sys.stderr)
        return 1
    print(json.dumps(M.oracle(rows), indent=2))
    return 0


def build_comparisons_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harnesslab comparisons",
        description="Every model's task-paired bootstrap comparison of each HARNESS against a "
                    "baseline HARNESS, as {model: {harness: comparison}} -- the SAME "
                    "harness_comparison -> paired_bootstrap path GET /api/results/<dir>/comparisons "
                    "and /metrics's own comparison run, printed as JSON.",
    )
    p.add_argument("--baseline", default=None, metavar="HARNESS",
                   help="baseline harness id (default: 'baseline' when the study has one, else the "
                        "alphabetically first harness -- same fallback as the HTTP route)")
    return p


def comparisons_cli(argv: list[str]) -> int:
    """`harnesslab comparisons <dir> [--baseline B]` -- see build_comparisons_parser."""
    args = _add_dir_and_lab_args(build_comparisons_parser()).parse_args(argv)
    if args.lab:
        os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))
    from harnesslab.backend import paths                      # noqa: E402  (after HARNESSLAB_LAB)
    from harnesslab.backend import metrics as M                # noqa: E402
    import json                                                # noqa: E402
    rows, err = _study_rows_or_die(args.dir, paths.RUNS_ROOT)
    if rows is None:
        print(err, file=sys.stderr)
        return 1
    baseline = M.resolve_baseline_harness(rows, args.baseline)
    print(json.dumps(M.comparisons(rows, baseline), indent=2))
    return 0


def build_integrity_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harnesslab integrity",
        description="The judge step's integrity read over a results directory -- leakage probes with "
                    "each task's hidden AND strengthened pass@1, weak tests, the agent's self-report "
                    "against the suite, and the Ochiai ranking -- the SAME metrics.integrity "
                    "GET /api/results/<dir>/integrity serves, printed as JSON.",
    )
    p.add_argument("--harness", default="baseline", metavar="HARNESS",
                   help="harness the per-task rows are read under (default 'baseline'; falls back to "
                        "the alphabetically first harness, same as the HTTP route)")
    return p


def integrity_cli(argv: list[str]) -> int:
    """`harnesslab integrity <dir> [--harness H]` -- see build_integrity_parser."""
    args = _add_dir_and_lab_args(build_integrity_parser()).parse_args(argv)
    if args.lab:
        os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))
    from harnesslab.backend import paths                      # noqa: E402  (after HARNESSLAB_LAB)
    from harnesslab.backend import metrics as M                # noqa: E402
    import json                                                # noqa: E402
    rows, err = _study_rows_or_die(args.dir, paths.RUNS_ROOT)
    if rows is None:
        print(err, file=sys.stderr)
        return 1
    print(json.dumps(M.integrity(args.dir, rows, args.harness), indent=2))
    return 0


def build_fit_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harnesslab fit",
        description="The factorial fit summary (leading factor, each factor's SS share and level "
                    "range, interaction share) over a results directory's model x harness design -- "
                    "the SAME factor_summary(anova) the `fit` key of GET /api/results/<dir>/experiment "
                    "carries, printed as JSON. It only summarizes the existing ANOVA table; it never "
                    "refits or adds a second significance test.",
    )
    p.add_argument("--a", dest="a_key", default="model", metavar="KEY", help="factor A column (default: model)")
    p.add_argument("--b", dest="b_key", default="harness_id", metavar="KEY", help="factor B column (default: harness_id)")
    p.add_argument("--outcome", default="hidden_pass", metavar="KEY", help="outcome column (default: hidden_pass)")
    return p


def fit_cli(argv: list[str]) -> int:
    """`harnesslab fit <dir>` -- see build_fit_parser. Exit 1 when there is no fit (no graded runs,
    or fewer than 2 balanced levels of either factor) -- the same insufficient_data the HTTP route's
    `fit` key carries goes to stderr instead of stdout, rather than printed as if it were a result."""
    args = _add_dir_and_lab_args(build_fit_parser()).parse_args(argv)
    if args.lab:
        os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))
    from harnesslab.backend import paths                      # noqa: E402  (after HARNESSLAB_LAB)
    from harnesslab.backend import metrics as M                # noqa: E402
    import json                                                # noqa: E402
    rows, err = _study_rows_or_die(args.dir, paths.RUNS_ROOT)
    if rows is None:
        print(err, file=sys.stderr)
        return 1
    result = M.experiment(rows, args.a_key, args.b_key, args.outcome)
    fit = result.get("fit")
    if fit is None:
        # no `fit` key at all means experiment() found nothing to summarize in the first place
        # (e.g. no graded runs under this outcome) -- not a result, so it goes to stderr, not stdout.
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(fit, indent=2))
    return 0



def _port_is_explicit(argv: list[str] | None) -> bool:
    """True when the user named a port (flag or HARNESSLAB_PORT): then we never move it."""
    args = sys.argv[1:] if argv is None else argv
    return "HARNESSLAB_PORT" in os.environ or any(a == "--port" or a.startswith("--port=") for a in args)


def _port_free(host: str, port: int) -> bool:
    import socket
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(fam, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pick_port(host: str, port: int, explicit: bool) -> int | None:
    """The port to serve on. A busy default port used to fail inside uvicorn *after* the browser
    had been told to open it, so the tab showed whatever else was listening there (often an older
    harnesslab). Now: a busy default moves to the next free port and says so; a busy port the user
    asked for by name is an error, stated before anything opens."""
    if _port_free(host, port):
        return port
    if explicit:
        print(f"harnesslab: port {port} is already in use. Stop the other server, or pick another "
              f"port with --port.", file=sys.stderr)
        return None
    for alt in range(port + 1, port + 21):
        if _port_free(host, alt):
            print(f"  port {port} is busy (another server, perhaps an older harnesslab); using {alt}.",
                  file=sys.stderr)
            return alt
    print(f"harnesslab: ports {port}-{port + 20} are all in use; pick one with --port.", file=sys.stderr)
    return None

def main(argv: list[str] | None = None) -> int:
    # The project was called holdstill until 2026-09-03. Honour the old environment variables so
    # existing scripts and shells keep working; the new names win when both are set.
    for new, olds in (("HARNESSLAB_LAB", ("SINGLETREE_LAB", "ARGUS_LAB", "HOLDSTILL_LAB")),
                      ("HARNESSLAB_PORT", ("SINGLETREE_PORT", "ARGUS_PORT", "HOLDSTILL_PORT")),
                      ("HARNESSLAB_HOST", ("SINGLETREE_HOST", "ARGUS_HOST", "HOLDSTILL_HOST")),
                      ("HARNESSLAB_NO_BROWSER", ("SINGLETREE_NO_BROWSER", "ARGUS_NO_BROWSER", "HOLDSTILL_NO_BROWSER"))):
        for old in olds:
            if old in os.environ and new not in os.environ:
                os.environ[new] = os.environ[old]

    # One name, one command. `harnesslab run ...` forwards to the runner so nobody has to type
    # `python -m harnesslab.core.runner`; `harnesslab import <path>` forwards to the import
    # adapters, which until now could only be reached over HTTP; `harnesslab serve` is the
    # explicit form of the default. A bare invocation still serves, because the handout and the
    # lab deck both say `python3 -m harnesslab --no-browser` and those must keep working.
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "run":
        from harnesslab.core import runner  # noqa: E402
        sys.argv = ["harnesslab run"] + argv[1:]
        return runner.main() or 0
    if argv and argv[0] == "watch":
        return watch_cli(argv[1:])
    if argv and argv[0] == "import":
        return import_cli(argv[1:])
    if argv and argv[0] == "oracle":
        return oracle_cli(argv[1:])
    if argv and argv[0] == "comparisons":
        return comparisons_cli(argv[1:])
    if argv and argv[0] == "fit":
        return fit_cli(argv[1:])
    if argv and argv[0] == "integrity":
        return integrity_cli(argv[1:])
    if argv and argv[0] == "serve":
        argv = argv[1:]

    args = build_parser().parse_args(argv)
    if args.lab:
        os.environ["HARNESSLAB_LAB"] = os.path.abspath(os.path.expanduser(args.lab))

    from harnesslab.backend import paths  # noqa: E402  (after HARNESSLAB_LAB is set)

    if args.paths:
        import json
        print(json.dumps(paths.describe(), indent=2))
        return 0

    if args.export_field:
        from harnesslab.backend.field import export_field   # noqa: E402
        picked = [x.strip() for x in args.export_results.split(",")] if args.export_results else ["prerecorded_mock"]
        from harnesslab.backend.results_scope import confirm_private_export  # noqa: E402
        if not confirm_private_export(picked, paths.RUNS_ROOT, args.yes, sys.stdin.isatty(), input):
            print("export cancelled: captured runs need an explicit yes (pass --yes in a script)", file=sys.stderr)
            return 1
        info = export_field(picked[0], args.export_field)
        print(f"wrote {info['path']}  ({info['bytes'] / 1e6:.1f} MB, {info['runs']} runs, "
              f"{info['harnesses']} harnesses, {info['tasks']} tasks, results: {info['results']})")
        print("  open it directly in a browser; no server needed.")
        return 0

    if args.export:
        from harnesslab.backend.static_export import build  # noqa: E402
        picked = [x.strip() for x in args.export_results.split(",")] if args.export_results else None
        if picked:
            from harnesslab.backend.results_scope import confirm_private_export  # noqa: E402
            if not confirm_private_export(picked, paths.RUNS_ROOT, args.yes, sys.stdin.isatty(), input):
                print("export cancelled: captured runs need an explicit yes (pass --yes in a script)", file=sys.stderr)
                return 1
        from harnesslab.backend.static_export import DEFAULT_RUNS
        info = build(args.export, picked, DEFAULT_RUNS if args.export_runs is None else args.export_runs)
        print(f"wrote {info['path']}  ({info['bytes']/1_000_000:.1f} MB, "
              f"{info['endpoints']} endpoints, {info['runs_embedded']} run details, "
              f"results: {', '.join(info['results'])})")
        if info["failed"]:
            print(f"  {len(info['failed'])} endpoint(s) not captured:", file=sys.stderr)
            for u, why in info["failed"][:8]:
                print(f"    {u} -> {why}", file=sys.stderr)
        print("  open it directly in a browser; no server needed.")
        return 0

    from harnesslab.backend.app import app  # noqa: E402
    from harnesslab.backend.guard import set_bound_host  # noqa: E402
    set_bound_host(args.host)

    port = _pick_port(args.host, args.port, explicit=_port_is_explicit(argv))
    if port is None:
        return 1
    args.port = port
    from harnesslab.backend.guard import set_bound_port, codespaces_host  # noqa: E402
    set_bound_port(port)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    cs = codespaces_host()
    if cs:
        print(f"  GitHub Codespaces: open https://{cs} (the Ports tab also links it)")
    key = "set" if os.environ.get("OPENROUTER_API_KEY") else "NOT set"
    print(f"harnesslab {__version__} -> {url}   (lab root: {paths.LAB_ROOT}; OpenRouter key: {key})")
    if not paths.describe()["dist_built"]:
        print("  note: the UI is not built; the API is still served. "
              "Build it with: cd harnesslab/frontend && npm install && npm run build", file=sys.stderr)

    if not args.no_browser and os.environ.get("HARNESSLAB_NO_BROWSER") != "1":
        try:
            import threading
            import webbrowser
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:  # pragma: no cover - headless hosts
            pass

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
