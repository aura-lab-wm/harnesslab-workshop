"""An incremental SQLite index over ``data/runs/*/index.jsonl``.

Why: every analysis endpoint currently re-reads and re-parses the whole ``index.jsonl`` of a
results directory on every request, and ``results_dirs()`` re-reads *every* directory. That is
fine for the 24-run demo and wasteful at 500 runs; at the thousands-of-runs scale the platform is
meant to reach it is the dominant cost of a page load.

This module keeps a cache keyed by ``(dir, run_id)`` in ``data/.harnesslab_index.sqlite``. A
directory is re-ingested only when the mtime or size of its ``index.jsonl`` changes, so the steady
state is a single indexed SQL query. The cache is *derived data*: deleting the file (or corrupting
it) costs one rebuild and nothing else.

Parity is the design constraint. ``rows(dir, **filters)`` returns exactly what
``harnesslab.core.analysis.filter_rows(load_index(dir), **filters)`` returns today -- the same dicts, in
the same order -- because the verbatim JSON line is stored alongside the indexed columns and handed
back through ``json.loads``. ``tests_agentlab/test_packaging.py`` asserts that equality on
``demo_mock`` and ``prerecorded_mock``.

Public API::

    rows(dir, harness_id=None, task_id=None, model=None)   -> list[dict]   (== filter_rows(load_index(dir), ...))
    run_ids(dir)                                           -> list[str]
    overview()                                             -> list[dict]   (== metrics.results_dirs())
    refresh(dir=None)                                      -> dict         (rows ingested per directory)
    stats()                                                -> dict         (cache diagnostics)

Concurrency: SQLite in WAL mode with a short busy timeout. Live runs append to ``index.jsonl``
while the UI reads; a stale read at worst misses the newest row until the next mtime bump.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
from typing import Iterable, Optional

from .paths import INDEX_DB, RUNS_ROOT
from .results_scope import is_private, list_results_dirs

SCHEMA_VERSION = 3

# Columns promoted out of the JSON blob so they can be filtered/aggregated in SQL.
_DDL = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS sources (
    dir     TEXT PRIMARY KEY,
    mtime   REAL NOT NULL,
    size    INTEGER NOT NULL,
    n_rows  INTEGER NOT NULL,
    scanned REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    dir          TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    ord          INTEGER NOT NULL,     -- position in index.jsonl: preserves load_index() order
    task_id      TEXT,
    harness_id   TEXT,
    model        TEXT,
    hidden_pass  INTEGER,
    strong_pass  INTEGER,
    exit_reason  TEXT,
    steps        INTEGER,
    tokens       INTEGER,
    cost_usd     REAL,
    -- a few per-run facts pulled from <run_id>/summary.json and the run directory
    has_run_dir  INTEGER,
    has_ledger   INTEGER,
    ledger_bytes INTEGER,
    has_patch    INTEGER,
    has_messages INTEGER,
    sum_error    TEXT,
    sum_files    INTEGER,
    row_json     TEXT NOT NULL,
    PRIMARY KEY (dir, run_id)
);

CREATE INDEX IF NOT EXISTS runs_dir_ord ON runs(dir, ord);
CREATE INDEX IF NOT EXISTS runs_filter  ON runs(dir, harness_id, task_id, model);
"""

_LOCK = threading.Lock()
_CONN: dict[str, sqlite3.Connection] = {}


# --------------------------------------------------------------------------- connection
def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    cx = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=NORMAL")
    cx.execute("PRAGMA busy_timeout=5000")
    cx.executescript(_DDL)
    v = cx.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
    if v is None:
        cx.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        cx.commit()
    elif int(v["v"]) != SCHEMA_VERSION:
        cx.executescript("DROP TABLE IF EXISTS runs; DROP TABLE IF EXISTS sources;")
        cx.executescript(_DDL)
        cx.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        cx.commit()
    return cx


def _sidecar_path(path: str) -> str:
    """Where the index goes when it cannot live beside the data.

    SQLite needs POSIX locking, which network and userspace filesystems (OneDrive, SMB, a FUSE
    mount) do not always provide -- there it fails with "disk I/O error" no matter how healthy the
    file is. The index is a derived cache, so in that case it moves to the local temp directory,
    keyed by the lab root so two checkouts never share one.
    """
    key = hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), f"harnesslab-index-{key}.sqlite")


def _db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Cached connection.

    A corrupt or unreadable database is deleted and rebuilt once; a filesystem that cannot host a
    SQLite file at all falls back to a local sidecar copy. If even that fails the error propagates,
    and the query helpers fall back to reading index.jsonl directly.
    """
    path = os.path.abspath(db_path or INDEX_DB)
    with _LOCK:
        cx = _CONN.get(path)
        if cx is not None:
            return cx
        try:
            cx = _connect(path)
        except sqlite3.DatabaseError:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass
            try:
                cx = _connect(path)
            except (sqlite3.Error, OSError):
                cx = _connect(_sidecar_path(path))
        except (sqlite3.Error, OSError):
            # OperationalError("disk I/O error"/"unable to open database file"), or an OSError from
            # the directory itself: the filesystem, not the file. Nothing to delete; go straight
            # to the sidecar.
            cx = _connect(_sidecar_path(path))
        _CONN[path] = cx
        return cx


def close_all() -> None:
    """Drop cached connections (tests, and after moving the lab root)."""
    with _LOCK:
        for cx in _CONN.values():
            try:
                cx.close()
            except Exception:
                pass
        _CONN.clear()


# --------------------------------------------------------------------------- ingest
def _runs_root(runs_root: Optional[str]) -> str:
    return os.path.abspath(runs_root or RUNS_ROOT)


def _index_path(name: str, runs_root: Optional[str] = None) -> str:
    return os.path.join(_runs_root(runs_root), name, "index.jsonl")


def _facts(run_dir: str) -> tuple:
    """(has_run_dir, has_ledger, ledger_bytes, has_patch, has_messages, error, n_files_touched).

    ``error`` and ``n_files_touched`` come from ``summary.json``; the rest is one stat per file.
    Only read during ingest, i.e. once per change of ``index.jsonl``.
    """
    if not os.path.isdir(run_dir):
        return (0, 0, 0, 0, 0, None, None)
    def size(fn):
        try:
            return os.path.getsize(os.path.join(run_dir, fn))
        except OSError:
            return -1
    led, pat, msg = size("ledger.jsonl"), size("patch.diff"), size("messages.json")
    err, nfiles = None, None
    sp = os.path.join(run_dir, "summary.json")
    if os.path.exists(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                s = json.load(f)
            err = s.get("error") or None
            ft = s.get("files_touched")
            nfiles = len(ft) if isinstance(ft, list) else None
        except (OSError, ValueError):
            pass
    return (1, int(led >= 0), max(led, 0), int(pat >= 0), int(msg >= 0), err, nfiles)


def _ingest(cx: sqlite3.Connection, name: str, runs_root: Optional[str] = None) -> int:
    idx = _index_path(name, runs_root)
    d = os.path.dirname(idx)
    st = os.stat(idx)
    payload = []
    with open(idx, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue                                  # a half-written append during a live run
            f_ = _facts(os.path.join(d, r.get("run_id", "")))
            payload.append((
                name, r.get("run_id"), i, r.get("task_id"), r.get("harness_id"), r.get("model"),
                _b(r.get("hidden_pass")), _b(r.get("strong_pass")), r.get("exit_reason"),
                r.get("steps"),
                # NULL when the source recorded no usage (importers.common.UNMEASURED_FIELDS), never 0
                None if ("input_tokens" in r and r["input_tokens"] is None)
                else (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0),
                r.get("cost_usd"), *f_, line,
            ))
    with cx:
        cx.execute("DELETE FROM runs WHERE dir = ?", (name,))
        cx.executemany(
            "INSERT OR REPLACE INTO runs (dir, run_id, ord, task_id, harness_id, model, hidden_pass,"
            " strong_pass, exit_reason, steps, tokens, cost_usd, has_run_dir, has_ledger, ledger_bytes,"
            " has_patch, has_messages, sum_error, sum_files, row_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
        cx.execute("INSERT OR REPLACE INTO sources (dir, mtime, size, n_rows, scanned) VALUES (?,?,?,?,?)",
                   (name, st.st_mtime, st.st_size, len(payload), time.time()))
    return len(payload)


def _b(x):
    return None if x is None else int(bool(x))


def _dirs_on_disk(runs_root: Optional[str] = None, include_private: bool = False) -> list[str]:
    return list_results_dirs(_runs_root(runs_root), include_private=include_private)


def refresh(name: Optional[str] = None, runs_root: Optional[str] = None,
            db_path: Optional[str] = None) -> dict:
    """Bring the cache in line with disk. Returns ``{dir: rows_ingested}`` for what was re-read.

    A directory is re-ingested when its ``index.jsonl`` mtime or size differs from what was
    recorded; directories that vanished are dropped.
    """
    cx = _db(db_path)
    names = [name] if name else _dirs_on_disk(runs_root)
    known = {r["dir"]: r for r in cx.execute("SELECT * FROM sources").fetchall()}
    done = {}
    for n in names:
        idx = _index_path(n, runs_root)
        try:
            st = os.stat(idx)
        except OSError:
            with cx:
                cx.execute("DELETE FROM runs WHERE dir = ?", (n,))
                cx.execute("DELETE FROM sources WHERE dir = ?", (n,))
            continue
        prev = known.get(n)
        if prev and abs(prev["mtime"] - st.st_mtime) < 1e-6 and prev["size"] == st.st_size:
            continue
        done[n] = _ingest(cx, n, runs_root)
    if name is None:                                       # drop directories that disappeared
        # Hidden from listings is not absent from disk. A private dir that was ingested on an
        # explicit request is still there, so evicting it here would drop it on every refresh.
        gone = set(known) - set(_dirs_on_disk(runs_root, include_private=True))
        if gone:
            with cx:
                cx.executemany("DELETE FROM runs WHERE dir = ?", [(g,) for g in gone])
                cx.executemany("DELETE FROM sources WHERE dir = ?", [(g,) for g in gone])
    return done


# --------------------------------------------------------------------------- JSONL fallback
def _plain(name: str, runs_root: Optional[str] = None) -> list[dict]:
    """The uncached path: parse index.jsonl the way analysis.load_index does."""
    from harnesslab.core.analysis import load_index
    root = runs_root or RUNS_ROOT
    return load_index(os.path.join(root, name))


# --------------------------------------------------------------------------- queries
def rows(name: str, harness_id: Optional[str] = None, task_id: Optional[str] = None,
         model: Optional[str] = None, runs_root: Optional[str] = None,
         db_path: Optional[str] = None) -> list[dict]:
    """Drop-in replacement for ``filter_rows(load_index(<runs>/name), ...)``.

    Same dicts, same order. Falsy filters are ignored, matching ``metrics.rows_for``.
    """
    try:
        refresh(name, runs_root, db_path)
    except (sqlite3.Error, OSError):
        from harnesslab.core.analysis import filter_rows
        return filter_rows(_plain(name, runs_root), harness_id or None, task_id or None, model or None)
    q = "SELECT row_json FROM runs WHERE dir = ?"
    args: list = [name]
    for col, val in (("harness_id", harness_id), ("task_id", task_id), ("model", model)):
        if val:
            q += f" AND {col} = ?"
            args.append(val)
    q += " ORDER BY ord"
    cur = _db(db_path).cursor()
    cur.row_factory = None                                 # plain tuples: ~25% faster on a full scan
    return [json.loads(r[0]) for r in cur.execute(q, args)]


def run_ids(name: str, runs_root: Optional[str] = None, db_path: Optional[str] = None) -> list[str]:
    """Run ids of a directory, in index.jsonl order -- without decoding any JSON."""
    try:
        refresh(name, runs_root, db_path)
    except (sqlite3.Error, OSError):
        return [r["run_id"] for r in _plain(name, runs_root)]
    cur = _db(db_path).cursor()
    cur.row_factory = None
    return [r[0] for r in cur.execute("SELECT run_id FROM runs WHERE dir = ? ORDER BY ord", (name,))]


def has(name: str, runs_root: Optional[str] = None, db_path: Optional[str] = None) -> bool:
    return os.path.exists(_index_path(name, runs_root))


def overview(runs_root: Optional[str] = None, db_path: Optional[str] = None) -> list[dict]:
    """Drop-in replacement for ``metrics.results_dirs()``: one card per results directory.

    Identical keys and values; computed with SQL aggregates instead of parsing every index.

    Never names a private results directory, for the same reason ``stats()`` does not: this is a
    listing, and the cache is a superset of what any listing may show. The dir only reaches the
    cache by being asked for explicitly (export -> rows -> refresh(name)), and a full refresh
    deliberately no longer evicts it, so filtering has to happen here rather than upstream.
    """
    try:
        refresh(None, runs_root, db_path)
        cx = _db(db_path)
    except (sqlite3.Error, OSError):
        from . import metrics as _M
        return _M.results_dirs()
    out = []
    root = _runs_root(runs_root)
    for src in cx.execute("SELECT * FROM sources ORDER BY dir"):
        name = src["dir"]
        if is_private(root, name):
            continue
        agg = cx.execute(
            "SELECT COUNT(*) n, AVG(CASE WHEN hidden_pass THEN 1.0 ELSE 0.0 END) p FROM runs WHERE dir = ?",
            (name,)).fetchone()
        def distinct(col):
            return [r[0] for r in cx.execute(
                f"SELECT DISTINCT {col} FROM runs WHERE dir = ? ORDER BY {col}", (name,)) if r[0] is not None]
        out.append({
            "name": name, "runs": agg["n"],
            "harnesses": distinct("harness_id"), "models": distinct("model"), "tasks": distinct("task_id"),
            "pass_rate": (agg["p"] if agg["n"] else None),
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(src["mtime"])),
        })
    return _clean(out)


def facts(name: str, run_id: str, runs_root: Optional[str] = None, db_path: Optional[str] = None) -> dict:
    """The per-run facts gathered at ingest (artefact presence, ledger size, summary error)."""
    refresh(name, runs_root, db_path)
    r = _db(db_path).execute(
        "SELECT has_run_dir, has_ledger, ledger_bytes, has_patch, has_messages, sum_error, sum_files"
        " FROM runs WHERE dir = ? AND run_id = ?", (name, run_id)).fetchone()
    return dict(r) if r else {}


def stats(runs_root: Optional[str] = None, db_path: Optional[str] = None) -> dict:
    """Cache diagnostics for the notes / a debug endpoint.

    Never lists a private results directory (see results_scope), even one already ingested into
    the cache from an earlier query that named it explicitly -- the cache is a superset of what
    any listing may show, and this is a listing.
    """
    cx = _db(db_path)
    path = os.path.abspath(db_path or INDEX_DB)
    root = _runs_root(runs_root)
    srcs = [dict(r) for r in cx.execute("SELECT * FROM sources ORDER BY dir") if not is_private(root, r["dir"])]
    return {"db": path, "db_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "schema": SCHEMA_VERSION, "dirs": srcs,
            "runs": cx.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]}


def _clean(x):
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


# --------------------------------------------------------------------------- CLI
def _main(argv: Optional[Iterable[str]] = None) -> int:  # pragma: no cover - convenience
    import argparse
    import pprint
    p = argparse.ArgumentParser(prog="python -m harnesslab.backend.store",
                                description="Rebuild / inspect the run index cache.")
    p.add_argument("--rebuild", action="store_true", help="delete the database and re-ingest everything")
    p.add_argument("--dir", default=None, help="only this results directory")
    a = p.parse_args(list(argv) if argv is not None else None)
    if a.rebuild:
        close_all()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(INDEX_DB + suffix)
            except OSError:
                pass
    t0 = time.perf_counter()
    done = refresh(a.dir)
    pprint.pprint({"ingested": done, "seconds": round(time.perf_counter() - t0, 3), **stats()})
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
