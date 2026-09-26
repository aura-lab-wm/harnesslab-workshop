"""Import real external coding-agent traces into the lab's ledger format.

The platform's claim is that the *harness* is a controlled experimental variable. That
only means something if the same measurement can be taken of harnesses the lab did not
write. These adapters take native logs from five real harnesses plus one interchange
format and produce exactly the artefacts `harnesslab.core.runner` produces:

    data/runs/<dir>/index.jsonl                 one RunSummary per imported run
    data/runs/<dir>/<run_id>/ledger.jsonl       invoke_agent / chat / execute_tool / edit /
                                                boundary_event / grade spans
    data/runs/<dir>/<run_id>/summary.json
    data/runs/<dir>/<run_id>/messages.json
    data/runs/<dir>/<run_id>/patch.diff

so every page of the platform — outcome, harness lab, trajectories, sentinel, integrity —
works on an imported Claude Code session exactly as it does on a lab run.

Adapters
--------
  claude_code    Claude Code session JSONL       (~/.claude/projects/<proj>/<session>.jsonl)
  codex          OpenAI Codex CLI rollout JSONL  (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)
  cursor         Cursor agent transcripts        (~/.cursor/projects/<proj>/agent-transcripts/**; partial)
  trajectory     Letta Trajectory v1 records     (the cross-harness interchange format)
  inspect        Inspect AI eval log             (.eval / .json; scores are real verdicts)
  openhands      OpenHands events / SWE-bench output.jsonl
  swe_agent      Native SWE-agent .traj

Public API
----------
  detect(path)                                  -> source name, or "" if nothing matches
  import_path(path, results_dir_name, ...)      -> {imported, skipped, errors, harness_ids, ...}
  fingerprint(events, meta)                     -> the observed harness fingerprint
  harness_from_fingerprint(id, fp, notes)       -> a HarnessConfig-shaped dict + hash
  SOURCES                                       -> adapter registry for the UI

Honesty rules that hold across every adapter
--------------------------------------------
* `hidden_pass` is set **only** when the trace carries a real verdict (an Inspect score, an
  OpenHands/SWE-bench `resolved` flag, or a sidecar `results.json` / `report.json` found
  next to the file). Otherwise it stays `None` — an unknown outcome, not a failure.
  Downstream, `harnesslab.core.analysis.task_table` folds `None` into "not passed" via
  `bool(r.get(outcome))`, so a directory of unknown-outcome runs reads as pass@1 = 0. That
  is a *display* caveat, not a crash; the UI card says so, and `metrics.py` /
  `analysis.py` are left untouched.
* An external harness's system prompt is never stored: only `sha256` and length, so two
  runs can be shown to share a prompt without the prompt leaving the machine.
* `policy` is `"permissive"` only when a destructive command was actually observed to run;
  otherwise `"unknown"`. We never claim to know a policy we did not see enforced.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Callable, Iterable, Optional

from . import (claude_code, codex, cursor, gemini_cli, inspect_log, openhands, qwen_code, swe_agent,
               trajectory_fmt)
from .common import (Event, Session, derive_task_id, fingerprint, harness_from_fingerprint,
                     harness_id_for, map_tool, run_id_for, sha12, convert, LAB_TOOLS)

__all__ = ["SOURCES", "detect", "import_path", "fingerprint", "harness_from_fingerprint",
           "harness_id_for", "derive_task_id", "Session", "Event", "map_tool", "sha12"]

try:                                          # another work-stream added the canonical resolver
    from ..paths import LAB_ROOT, RUNS_ROOT    # (handles a checkout and an installed wheel)
except Exception:                             # standalone fallback: .../importers -> backend -> harnesslab -> lab
    LAB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    RUNS_ROOT = os.path.join(LAB_ROOT, "data", "runs")

#: adapter registry, most specific sniffers first (order only breaks ties)
SOURCES = [inspect_log, swe_agent, qwen_code, gemini_cli, claude_code, codex, cursor, trajectory_fmt, openhands]

_ADAPTER = {m.NAME: m for m in SOURCES}

_WRITE_LOCK = threading.Lock()


def list_sources() -> list[dict]:
    return [{"name": m.NAME, "description": m.DESCRIPTION, "patterns": list(m.PATTERNS)} for m in SOURCES]


# --------------------------------------------------------------------- detection
#: files sniffed before a directory walk gives up and answers from what it has
_WALK_CAP = 400


def _file_scores(path: str) -> dict[str, float]:
    """Every adapter's confidence in one file. An adapter that raises scores zero."""
    out = {}
    for m in SOURCES:
        try:
            s = float(m.sniff(path))
        except Exception:
            s = 0.0
        if s > 0:
            out[m.NAME] = s
    return out


def _file_winner(scores: dict[str, float]) -> tuple[str, float]:
    if not scores:
        return "", 0.0
    # ties go to the adapter registered first: SOURCES is ordered most-specific-first
    order = {m.NAME: i for i, m in enumerate(SOURCES)}
    name = min(scores, key=lambda n: (-scores[n], order[n]))
    return name, scores[name]


def _dir_scores(path: str) -> dict[str, dict]:
    """One walk of a directory: per adapter, the total confidence of the files it won and
    how many files those were. The total is what picks the winner (so two half-hearted
    matches lose to one certain one); the mean is what can honestly be called a confidence."""
    totals: dict[str, dict] = {}
    seen = 0
    for dp, dn, fn in os.walk(path):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in sorted(fn):
            if not f.endswith((".json", ".jsonl", ".eval", ".traj")):
                continue
            best, score = _file_winner(_file_scores(os.path.join(dp, f)))
            if best and score >= 0.5:
                t = totals.setdefault(best, {"total": 0.0, "files": 0})
                t["total"] += score
                t["files"] += 1
            seen += 1
            if seen >= _WALK_CAP:
                break
        if seen >= _WALK_CAP:
            break
    return totals


def detect(path: str) -> str:
    """Sniff a file or directory and return the adapter name (or "").

    For a directory we sniff up to 400 candidate files and return the source with the
    highest total confidence, so a `~/.claude/projects/<proj>` folder resolves to
    `claude_code` even if it also holds a stray json.
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return ""
    if os.path.isfile(path):
        best, score = _file_winner(_file_scores(path))
        return best if score >= 0.5 else ""
    totals = _dir_scores(path)
    if not totals:
        return ""
    return max(totals.items(), key=lambda kv: kv[1]["total"])[0]


def detect_detail(path: str) -> dict:
    """What `GET /api/import/detect` returns: the winner plus per-adapter confidence.

    `candidates` is ranked best-first and every entry carries `confidence` (0..1) and `files`
    (how many files scored it). For a single file that is one adapter's own sniff score, and
    every adapter that scored anything at all is listed, below the 0.5 bar included. For a
    directory it is the mean over the files that adapter *won*, so it stays a confidence rather
    than growing with the size of the directory -- while the ranking is by total weight, which
    is what `detect()` picks the winner by.
    """
    path = os.path.expanduser(path)
    out = {"path": path, "exists": os.path.exists(path), "is_dir": os.path.isdir(path),
           "source": "", "candidates": [], "sessions": None, "error": ""}
    if not out["exists"]:
        out["error"] = "no such file or directory"
        return out
    if os.path.isfile(path):
        for m in SOURCES:
            try:
                s = float(m.sniff(path))
            except Exception as e:
                s = 0.0
                out["error"] = out["error"] or f"{m.NAME}: {type(e).__name__}: {e}"
            if s > 0:
                out["candidates"].append({"source": m.NAME, "confidence": round(s, 2), "files": 1})
        out["candidates"].sort(key=lambda c: -c["confidence"])
    else:
        totals = _dir_scores(path)
        out["candidates"] = [{"source": n, "confidence": round(t["total"] / t["files"], 2),
                              "files": t["files"]}
                             for n, t in sorted(totals.items(), key=lambda kv: -kv[1]["total"])]
    out["source"] = detect(path)
    if out["source"]:
        try:
            out["sessions"] = sum(1 for _ in _sessions_for(out["source"], path))
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
    return out


def _sessions_for(source: str, path: str) -> Iterable[Session]:
    m = _ADAPTER.get(source)
    if m is None:
        raise ValueError(f"unknown source {source!r}; known: {', '.join(sorted(_ADAPTER))}")
    return m.sessions(path)


# --------------------------------------------------------------------- import
def _existing_run_ids(out_root: str) -> set[str]:
    idx = os.path.join(out_root, "index.jsonl")
    if not os.path.exists(idx):
        return set()
    out = set()
    with open(idx, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.add(json.loads(line)["run_id"])
            except Exception:
                continue
    return out


def session_harness(sess: Session) -> dict:
    """The harness dict written into every `invoke_agent` start span of this session."""
    meta = {
        "source": sess.source, "agent": sess.agent or sess.source,
        "agent_version": sess.agent_version, "model": sess.model,
        "temperature": sess.temperature, "declared_tools": sess.declared_tools,
        "system_prompt": sess.system_prompt, "max_steps_declared": sess.max_steps_declared,
        "tool_overrides": sess.extra.get("tool_overrides"),
    }
    fp = fingerprint(sess.events, meta)
    hid = harness_id_for(sess)
    bits = [f"imported from {sess.source}"]
    if sess.extra.get("policy_note"):
        bits.append(sess.extra["policy_note"])
    if sess.extra.get("solver"):
        bits.append(f"solver={sess.extra['solver']}")
    if sess.extra.get("trajectory_source"):
        bits.append(f"trajectory source={sess.extra['trajectory_source']}")
    if sess.extra.get("agent_class"):
        bits.append(f"agent_class={sess.extra['agent_class']}")
    if fp["system_prompt_chars"]:
        bits.append(f"system prompt withheld (sha256 {fp['system_prompt_sha256'][:12]}…, {fp['system_prompt_chars']} chars)")
    bits.append("unknown fields are 0/\"\"/false, never guessed")
    return harness_from_fingerprint(hid, fp, notes="; ".join(bits))


def import_path(path: str, results_dir_name: str, source: Optional[str] = None,
                model_override: Optional[str] = None,
                task_id_fn: Optional[Callable[[Session], str]] = None,
                progress: Optional[Callable[[int, int, str], None]] = None,
                runs_root: Optional[str] = None) -> dict:
    """Import every run found under `path` into `data/runs/<results_dir_name>/` (see _import_path).

    An import into a private results directory takes the capture lock, so it can never interleave
    with a capture backfill writing the same runs (capture spine spec §5.7). A private directory is
    recognised by what the name refers to (case, symlink), not its spelling -- and when the name is
    a case/whitespace/`./` spelling variant of a private directory (not a symlink alias, which
    already points at the real one), the *canonical* spelling is what actually gets written, so a
    case-sensitive filesystem never creates a second sibling directory next to it.
    """
    from ..results_scope import is_private, PRIVATE_DIRS
    args = (path, results_dir_name, source, model_override, task_id_fn, progress, runs_root)
    root = runs_root or RUNS_ROOT
    private_dest = is_private(root, results_dir_name)

    # A private session store may only land in a private results directory. The boundary around
    # captured sessions is drawn around the DIRECTORY NAME -- results_scope names it, private_guard
    # 404s it, the static export never reaches it -- so importing ~/.claude/projects into
    # `demo_mock` would carry the same transcripts straight out through a shareable export and an
    # open /api/results route. Refused at this one choke point, which is where the CLI and
    # POST /api/import both arrive.
    if not private_dest:
        from harnesslab.capture.adapters import is_private_source
        store = is_private_source(path)
        if store:
            raise ValueError(
                f"{path} is inside {store}, a live session store, and {results_dir_name!r} is a "
                f"public results directory -- its runs are served by /api/results and frozen into "
                f"any --export. Import it into "
                f"{' or '.join(sorted(PRIVATE_DIRS))} instead, which the API and the export both "
                f"hold back, or point this at a copy that is not a session store.")

    if private_dest:
        cleaned = os.path.normpath((results_dir_name or "").strip()).casefold()
        canonical = next((p for p in sorted(PRIVATE_DIRS) if p.casefold() == cleaned), results_dir_name)
        args = (path, canonical, source, model_override, task_id_fn, progress, runs_root)
        from harnesslab.capture.lock import CaptureLock
        with CaptureLock(root):
            return _import_path(*args)
    return _import_path(*args)


def _import_path(path: str, results_dir_name: str, source: Optional[str] = None,
                model_override: Optional[str] = None,
                task_id_fn: Optional[Callable[[Session], str]] = None,
                progress: Optional[Callable[[int, int, str], None]] = None,
                runs_root: Optional[str] = None) -> dict:
    """Import every run found under `path` into `data/runs/<results_dir_name>/`.

    Idempotent: a run whose deterministic `run_id` is already in `index.jsonl` is skipped,
    so re-pointing the importer at a growing session directory only adds what is new.

    `task_id_fn(session) -> str` overrides the default task id derivation
    (SWE-bench instance id verbatim, otherwise `<cwd-slug>-<first-message-slug>-<hash8>`).
    `progress(done, total, label)` is called as sessions land. `total` is None: the adapters hand
    back a generator, so how many sessions are under a path is not known until the last one has
    been read. It used to be reported as the running count of sessions seen, which equalled `done`
    on every clean iteration and made the import screen's bar read 100% from the first session --
    a measured-looking number for a quantity nobody had measured. Unknown is reported as unknown.
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if not results_dir_name or "/" in results_dir_name or results_dir_name.startswith("."):
        raise ValueError("bad results dir name")
    src = source or detect(path)
    if not src:
        raise ValueError(f"could not detect a known trace format at {path}; "
                         f"pass source= one of {', '.join(sorted(_ADAPTER))}")
    if src not in _ADAPTER:
        raise ValueError(f"unknown source {src!r}")

    out_root = os.path.join(runs_root or RUNS_ROOT, results_dir_name)
    os.makedirs(out_root, exist_ok=True)
    known = _existing_run_ids(out_root)
    # The capture descriptor is the one declaration of what a source cannot record, and a manual
    # import must honour it too: otherwise the Import page writes "ok" execute_tool spans and zero
    # cost for a Cursor transcript that the Capture page correctly calls unknown.
    from harnesslab.capture.adapters import BY_NAME as _CAPTURE, missing as _missing
    gaps = _missing(_CAPTURE[src]) if src in _CAPTURE else frozenset()

    imported, skipped, errors = 0, 0, []
    harnesses: dict[str, dict] = {}
    task_ids: set[str] = set()
    outcomes = {"known": 0, "unknown": 0}
    rows: list[str] = []

    sess_iter = _sessions_for(src, path)
    for sess in sess_iter:
        try:
            task_id = (task_id_fn(sess) if task_id_fn else None) or derive_task_id(sess)
            harness = session_harness(sess)
            run_id = run_id_for(sess, task_id)
            if run_id in known:
                skipped += 1
                if progress:
                    progress(imported + skipped, None, f"skip {run_id}")
                continue
            summary = convert(sess, out_root, task_id, harness, model_override=model_override or "",
                              missing=gaps)
            rows.append(json.dumps(summary.__dict__, ensure_ascii=False, default=str))
            known.add(run_id)
            imported += 1
            task_ids.add(task_id)
            h = harnesses.setdefault(harness["id"], {"harness_id": harness["id"], "hash": harness["hash"],
                                                     "runs": 0, "fingerprint": harness["fingerprint"]})
            h["runs"] += 1
            outcomes["known" if summary.hidden_pass is not None else "unknown"] += 1
            if progress:
                progress(imported + skipped, None, run_id)
        except Exception as e:
            errors.append(f"{os.path.basename(getattr(sess, 'path', '?'))}: {type(e).__name__}: {e}")

    if rows:
        with _WRITE_LOCK:
            with open(os.path.join(out_root, "index.jsonl"), "a", encoding="utf-8") as f:
                f.write("\n".join(rows) + "\n")

    return {"source": src, "results_dir": results_dir_name, "path": path,
            "imported": imported, "skipped": skipped, "errors": errors,
            "harness_ids": sorted(harnesses),
            "harnesses": sorted(harnesses.values(), key=lambda h: h["harness_id"]),
            "tasks": sorted(task_ids)[:200], "n_tasks": len(task_ids),
            "outcomes_known": outcomes["known"], "outcomes_unknown": outcomes["unknown"]}
