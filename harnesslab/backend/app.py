"""harnesslab — the web platform over harnesslab.core (the measurement engine).

  python -m harnesslab            (from the lab/ directory)   ->  http://127.0.0.1:8765

Serves the analysis API, launches live runs (mock or any OpenRouter model) with the sentinel hook, streams
every ledger span to the browser over SSE, trains and evaluates the sentinel, and serves the built UI.
"""
from __future__ import annotations
import asyncio, json, os, queue, sys, threading, time, traceback, uuid, zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
from .paths import LAB_ROOT                                     # noqa: E402  (checkout or installed wheel)
sys.path.insert(0, LAB_ROOT)

from harnesslab.core.harness import HarnessConfig, run_task              # noqa: E402
from harnesslab.core.providers import make_provider, PRICES, price_for    # noqa: E402
from harnesslab.core.grader import load_task                              # noqa: E402
from harnesslab.core.analysis import load_index                           # noqa: E402
from harnesslab.core.ledger import new_run_id                             # noqa: E402
from . import metrics as M                                         # noqa: E402
from . import sentinel as S                                        # noqa: E402
from . import real_import as RI                                    # noqa: E402
from . import judge_runner as JR                                   # noqa: E402
from .private_guard import private_results_guard                   # noqa: E402
from .guard import security_guard                                  # noqa: E402
from .repeats_api import _safe_name                                # noqa: E402  (one name rule for every route)

app = FastAPI(title="harnesslab", version="0.1")
app.middleware("http")(private_results_guard(lambda: M.RUNS_ROOT))
# Registered SECOND: FastAPI/Starlette wrap middleware so the most-recently-registered one runs
# OUTERMOST (first). This must run before private_results_guard, so a request from an untrusted
# Host or a cross-site write never reaches the private-results check, the SPA catch-all, or a route.
app.middleware("http")(security_guard())
HARNESS_DIR = os.path.join(LAB_ROOT, "harnesses")
TASK_DIR = os.path.join(LAB_ROOT, "tasks")
DIST = os.path.join(os.path.dirname(HERE), "frontend", "dist")
SETTINGS = {"openrouter_key": os.environ.get("OPENROUTER_API_KEY", ""), "base_url": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")}

# --------------------------------------------------------------------------- event bus (SSE)
class Bus:
    def __init__(self):
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.recent: list[dict] = []

    def publish(self, kind: str, **data):
        ev = {"kind": kind, "ts": time.time(), **data}
        with self.lock:
            self.recent.append(ev)
            self.recent = self.recent[-2000:]
            for q in list(self.subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=10000)
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)


BUS = Bus()


@app.get("/api/events")
async def events(request: Request):
    q = BUS.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = q.get_nowait()
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    await asyncio.sleep(0.15)
                    yield ": keepalive\n\n"
        finally:
            BUS.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- overview / static data
@app.get("/api/overview")
def overview():
    model = S.load_model()
    return {"results": M.results_dirs(), "harnesses": list_harnesses(), "tasks": list_tasks(),
            "sentinel": {"meta": model.meta, "patterns": S.PATTERNS, "default_config": S.DEFAULT_SENTINEL},
            "key_present": bool(SETTINGS["openrouter_key"]), "jobs": [job_public(j) for j in JOBS.values()]}


def list_harnesses():
    out = []
    for fn in sorted(os.listdir(HARNESS_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(HARNESS_DIR, fn)) as f:
                d = json.load(f)
            out.append({"file": fn, **asdict(HarnessConfig(**d))})
    return out


def list_tasks():
    out = []
    for tid in sorted(os.listdir(TASK_DIR)):
        p = os.path.join(TASK_DIR, tid)
        if os.path.isdir(p) and not tid.startswith("_") and os.path.exists(os.path.join(p, "task.json")):
            t = load_task(p)
            out.append({"id": tid, "title": t.get("title"), "probe": t.get("probe"), "issue": t.get("issue"), "src_files": t.get("src_files")})
    return out


@app.get("/api/harnesses")
def harnesses():
    return list_harnesses()


@app.get("/api/tasks")
def tasks():
    return list_tasks()


# --------------------------------------------------------------------------- results & metrics
def _dir(name):
    _safe_name(name, "results dir name")
    d = os.path.join(M.RUNS_ROOT, name)
    real_root = os.path.realpath(M.RUNS_ROOT)
    if os.path.commonpath([real_root, os.path.realpath(d)]) != real_root:
        raise HTTPException(400, "bad results dir name")
    if not os.path.exists(os.path.join(d, "index.jsonl")):
        raise HTTPException(404, f"no results dir {name}")
    return d


# --------------------------------------------------------------------------- study aggregates cache
# (/comparisons, /oracle, /experiment's `fit`; docs/superpowers/specs/2026-09-21-study-aggregates-design.md #4)
#
# _COST_CACHE above is wall-clock TTL only, which is fine for "mean cost per model over every
# results dir": a slow-changing, low-stakes number where a stale minute costs nothing. A
# correctness-sensitive statistic over ONE study needs a stronger invalidation signal than "60
# seconds have passed" -- index.jsonl's mtime resolution and an in-place rewrite can otherwise
# leave a stale value looking current for the whole TTL window. So this cache keys on a study
# FINGERPRINT instead of (or in addition to) time.
#
# BEST-EFFORT, NOT a correctness guarantee: the fingerprint is (real path, st_dev, st_ino,
# st_size, st_mtime_ns, st_ctime_ns) of index.jsonl, taken before AND after the read, with a few
# retries on mismatch. That narrows the race window against a writer appending or rewriting the
# file concurrently; it does NOT close it. There is no lock the writer also respects here, so a
# torn read landing between the two stat() calls -- or an in-place rewrite that happens to land at
# the same size/mtime_ns/ctime_ns -- can in principle still be observed and cached as if it were
# consistent. Document this rather than calling it correct invalidation (see the design doc's
# "Replace 'mtime cache' with a snapshot contract" section for the transactional alternative this
# is a pragmatic stand-in for).
_STUDY_CACHE_VERSION = 1     # bump to invalidate every entry if a statistic's definition changes
_STUDY_CACHE_TTL = 300.0     # seconds; belt-and-suspenders -- a matching fingerprint is the real check
_STUDY_CACHE_MAX = 256       # bound memory: evict the least-recently-written entry past this many keys


class _StudyCache:
    """Fingerprint-keyed cache with a per-key lock, so several screens opening the same study (or
    the same comparison/oracle/fit) at once coalesce into ONE bootstrap computation instead of a
    stampede of N -- while a DIFFERENT study or a different baseline/outcome still computes
    concurrently, since each key gets its own lock."""

    def __init__(self):
        self._entries: dict[tuple, tuple] = {}          # key -> (fingerprint, written_at, value)
        self._locks: dict[tuple, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: tuple) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    def _fresh(self, key: tuple, fingerprint: tuple):
        with self._guard:
            entry = self._entries.get(key)
        if entry and entry[0] == fingerprint and time.time() - entry[1] < _STUDY_CACHE_TTL:
            return entry[2]
        return None

    def get_or_compute(self, key: tuple, fingerprint: tuple, compute):
        key = (_STUDY_CACHE_VERSION,) + key
        hit = self._fresh(key, fingerprint)
        if hit is not None:
            return hit
        with self._lock_for(key):                       # one computation per key at a time
            hit = self._fresh(key, fingerprint)          # someone else may have finished while we waited
            if hit is not None:
                return hit
            value = compute()
            with self._guard:
                self._entries[key] = (fingerprint, time.time(), value)
                if len(self._entries) > _STUDY_CACHE_MAX:
                    oldest = min(self._entries, key=lambda k: self._entries[k][1])
                    del self._entries[oldest]
                    self._locks.pop(oldest, None)
            return value


_STUDY_CACHE = _StudyCache()


def _study_fingerprint(name: str) -> tuple:
    """A results directory's index.jsonl identity right now. BEST-EFFORT -- see _StudyCache above."""
    p = os.path.join(M.RUNS_ROOT, name, "index.jsonl")
    st = os.stat(p)
    return (os.path.realpath(p), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _study_rows_stable(name: str, retries: int = 3) -> tuple[list, tuple]:
    """`load_index(name)`'s rows, plus the fingerprint they were read under: stat, read, stat
    again, retrying on mismatch. Exhausting `retries` still returns the last read rather than
    raising -- this is the documented best-effort fallback, not transactional consistency."""
    fp = _study_fingerprint(name)
    for _ in range(retries):
        rows = load_index(os.path.join(M.RUNS_ROOT, name))
        fp2 = _study_fingerprint(name)
        if fp2 == fp:
            return rows, fp2
        fp = fp2
    return rows, fp2


@app.get("/api/results")
def results():
    return M.results_dirs()


@app.get("/api/results/{name}/runs")
def runs(name: str, harness: Optional[str] = None, task: Optional[str] = None, model: Optional[str] = None):
    _dir(name)
    return M.rows_for(name, harness_id=harness, task_id=task, model=model)


@app.get("/api/results/{name}/runs/{run_id}")
def run_detail(name: str, run_id: str):
    d = _dir(name)
    _safe_name(run_id, "run_id")
    rd = os.path.join(d, run_id)
    real_d = os.path.realpath(d)
    if os.path.commonpath([real_d, os.path.realpath(rd)]) != real_d or not os.path.isdir(rd):
        raise HTTPException(404)
    def rd_(fn, js=False):
        p = os.path.join(rd, fn)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f) if js else f.read()
    spans = [json.loads(l) for l in (rd_("ledger.jsonl") or "").splitlines() if l.strip()]
    model = S.load_model()
    return {"summary": rd_("summary.json", True), "spans": spans, "patch": rd_("patch.diff"),
            "messages": rd_("messages.json", True), "replay": S.replay(spans, model),
            "issue": _issue_for(spans[0]["task_id"]) if spans else ""}


def _issue_for(tid: str) -> str:
    p = os.path.join(TASK_DIR, tid)
    if os.path.isdir(p) and os.path.exists(os.path.join(p, "task.json")):
        return load_task(p)["issue"]
    return f"(no local task for {tid}: this run was imported; the issue text lives in the source dataset)"


@app.get("/api/results/{name}/metrics")
def metrics(name: str, model: Optional[str] = None, baseline: str = "baseline"):
    _dir(name)
    rows = M.rows_for(name, model=model)
    return {"cells": M.cells(rows), "comparison": M.harness_comparison(rows, baseline, model), "sentinel_pairs": M.sentinel_pairs(rows)}


@app.get("/api/results/{name}/comparisons")
def comparisons(name: str, baseline: Optional[str] = None):
    """Every model's per-harness paired comparison against the `baseline` HARNESS, in ONE response
    -- the attribution matrix's replacement for a round trip per model. Delegates to
    `M.comparisons`, which calls `harness_comparison`, the SAME function /metrics uses.

    `baseline` is a HARNESS id. It defaults to (and falls back to, if named but absent) "baseline"
    when the study has one, else the alphabetically first harness -- the same "pick a legal default
    rather than 400" convention /integrity uses for its harness parameter."""
    _dir(name)
    rows, fp = _study_rows_stable(name)
    b = M.resolve_baseline_harness(rows, baseline)
    return _STUDY_CACHE.get_or_compute(("comparisons", name, b), fp, lambda: M.comparisons(rows, b))



@app.get("/api/results/{name}/oracle")
def oracle(name: str):
    """The hidden-test vs. strong-suite agreement summary (Cohen's kappa, chance-corrected) over
    every run -- what judge and report used to compute by pulling the full 1.2 MB /runs payload for
    two booleans per row. Delegates to `M.oracle` (core.cohens_kappa): eligible-pair count,
    excluded count, the four cell counts, each side's rate with its denominator, and kappa --
    `null` with a `kappa_undefined_reason`, never NaN, when it is undefined or there is nothing
    eligible."""
    _dir(name)
    rows, fp = _study_rows_stable(name)
    return _STUDY_CACHE.get_or_compute(("oracle", name), fp, lambda: M.oracle(rows))


@app.get("/api/results/{name}/patterns")
def patterns(name: str, harness: Optional[str] = None, task: Optional[str] = None,
             model: Optional[str] = None, outcome: str = "hidden_pass", clusters: int = 4):
    """Sets of trajectories over the action alphabet: exercise 3, one level up from a single run.
    Cached like /integrity: it is deterministic and reads only indexed runs (~0.55s per call on
    llma4se_live, the attribute step's long pole once /runs was filtered)."""
    _dir(name)
    _, fp = _study_rows_stable(name)
    return _STUDY_CACHE.get_or_compute(("patterns", name, harness, task, model, outcome, clusters), fp,
                                       lambda: M.patterns(name, harness, task, model, outcome, clusters))


@app.get("/api/results/{name}/query")
def run_query(name: str, pattern: str = "", harness: Optional[str] = None, model: Optional[str] = None,
              outcome: str = "hidden_pass", steps_min: Optional[int] = None,
              steps_max: Optional[int] = None, exit: Optional[str] = None):
    """Runs whose action string matches a regex, versus the rest, with the association measures
    and the generated unittest that turns the finding into a regression test."""
    _dir(name)
    return M.run_query(name, pattern, harness, model, outcome, steps_min, steps_max, exit)


@app.get("/api/results/{name}/experiment")
def experiment(name: str, a: str = "model", b: str = "harness_id", outcome: str = "hidden_pass",
               a_cell: Optional[str] = None, b_cell: Optional[str] = None, also: Optional[str] = None):
    """Exercise 8 for whatever is in the filter, now also carrying `fit` -- `factor_summary(anova)`,
    the pure summary of the SAME ANOVA table this response already returned, so the Experiment view
    stops deriving it client-side (docs/superpowers/specs/2026-09-21-study-aggregates-design.md).
    Everything this response returned before is unchanged. `also` adds comma-separated results dirs
    as extra levels of the model factor, which is how a live model is compared against the
    prerecorded cells; every named dir's fingerprint feeds the cache key so a change to any of them
    invalidates it."""
    _dir(name)
    extras = sorted(x.strip() for x in (also or "").split(",") if x.strip())
    for extra in extras:
        _dir(extra)
    rows, fp = _study_rows_stable(name)
    fps = [fp]
    for extra in extras:
        extra_rows, extra_fp = _study_rows_stable(extra)
        rows = rows + extra_rows
        fps.append(extra_fp)
    key = ("experiment", name, a, b, outcome, a_cell, b_cell, tuple(extras))
    return _STUDY_CACHE.get_or_compute(key, tuple(fps), lambda: M.experiment(rows, a, b, outcome, a_cell, b_cell))


@app.get("/api/results/{name}/integrity")
def integrity(name: str, harness: str = "baseline"):
    """Behind _STUDY_CACHE like /comparisons: every part walks index.jsonl (self_report and ochiai
    read each INDEXED run's ledger, and a run is indexed only once it has finished), so the index
    fingerprint is the same best-effort invalidation the other aggregates rely on. Uncached this
    parsed every ledger three times and took ~1s on llma4se_live -- the judge step's slowest call."""
    _dir(name)
    rows, fp = _study_rows_stable(name)
    hs = {r["harness_id"] for r in rows}
    if harness not in hs and hs:
        harness = sorted(hs)[0]                          # resolve BEFORE keying the cache
    return _STUDY_CACHE.get_or_compute(("integrity", name, harness), fp,
                                       lambda: M.integrity(name, rows, harness))


@app.get("/api/results/{name}/report")
def report(name: str, harness: str = "baseline"):
    _dir(name)
    return M.report_card(name, M.rows_for(name), harness)


@app.get("/api/results/{name}/report.md", response_class=PlainTextResponse)
def report_md(name: str, harness: str = "baseline"):
    _dir(name)
    return M.report_card(name, M.rows_for(name), harness).get("markdown", "")


# --------------------------------------------------------------------------- models (OpenRouter)
CURATED = [
    {"id": "openai/gpt-5.6-luna", "family": "OpenAI", "tier": "small"},
    {"id": "google/gemini-2.5-flash-lite", "family": "Google", "tier": "small"},
    {"id": "google/gemini-3.8-flash", "family": "Google", "tier": "mid"},
    {"id": "deepseek/deepseek-v4-flash", "family": "DeepSeek", "tier": "small"},
    {"id": "qwen/qwen3.8-27b", "family": "Qwen (open weights)", "tier": "mid"},
    {"id": "z-ai/glm-5.3-flash", "family": "Zhipu (open weights)", "tier": "small"},
    {"id": "anthropic/claude-haiku-4.5", "family": "Anthropic", "tier": "small"},
    {"id": "openai/gpt-5-mini", "family": "OpenAI", "tier": "small"},
    {"id": "mock", "family": "Mock (offline)", "tier": "free"},
]
_MODEL_CACHE = {"ts": 0, "data": None}
_COST_CACHE = {"ts": 0.0, "data": {}}


def observed_costs() -> dict:
    """Mean recorded cost per run by model id over every results directory (60 s cache)."""
    if time.time() - _COST_CACHE["ts"] < 60:
        return _COST_CACHE["data"]
    agg: dict[str, list] = {}
    for d in M.results_dirs():
        for r in load_index(os.path.join(M.RUNS_ROOT, d["name"])):
            if "cost_usd" in r and r["cost_usd"] is None:
                continue                          # unpriceable, not free: never averaged in as $0
            a = agg.setdefault(r["model"], [0.0, 0])
            a[0] += float(r.get("cost_usd") or 0.0)
            a[1] += 1
    _COST_CACHE.update(ts=time.time(), data={m: {"cost_per_run": s / n, "runs": n} for m, (s, n) in agg.items() if n})
    return _COST_CACHE["data"]


@app.get("/api/models")
def models(refresh: bool = False):
    """Curated list, enriched with OpenRouter's live catalogue (prices, context) when a key is present."""
    live = {}
    if SETTINGS["openrouter_key"] and (refresh or time.time() - _MODEL_CACHE["ts"] > 3600 or not _MODEL_CACHE["data"]):
        try:
            import urllib.request
            req = urllib.request.Request(SETTINGS["base_url"].rstrip("/") + "/models",
                                         headers={"Authorization": f"Bearer {SETTINGS['openrouter_key']}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
            _MODEL_CACHE.update(ts=time.time(), data={m["id"]: m for m in data.get("data", [])})
        except Exception as e:
            _MODEL_CACHE["error"] = str(e)
    live = _MODEL_CACHE.get("data") or {}
    out = []
    for c in CURATED:
        m = live.get(c["id"], {})
        pr = m.get("pricing", {})
        pi = float(pr.get("prompt", 0) or 0) * 1e6 if pr else None
        po = float(pr.get("completion", 0) or 0) * 1e6 if pr else None
        if pi is None and c["id"] != "mock":
            pi, po = price_for(c["id"])
        out.append({**c, "available": bool(m) or c["id"] == "mock" or not live, "price_in": pi, "price_out": po,
                    "context": m.get("context_length"), "name": m.get("name", c["id"]), "supports_tools": "tools" in (m.get("supported_parameters") or ["tools"])})
    return {"models": out, "observed": observed_costs(), "catalogue_size": len(live), "error": _MODEL_CACHE.get("error")}


class KeyIn(BaseModel):
    key: str
    base_url: Optional[str] = None


@app.post("/api/settings/key")
def set_key(k: KeyIn):
    SETTINGS["openrouter_key"] = k.key.strip()
    if k.base_url:
        SETTINGS["base_url"] = k.base_url
    _MODEL_CACHE["ts"] = 0
    return {"key_present": bool(SETTINGS["openrouter_key"])}


# --------------------------------------------------------------------------- jobs
class JobIn(BaseModel):
    out: str                                  # results dir name under data/runs
    models: list[str]                         # openrouter ids, or "mock"
    harnesses: list[str]                      # harness ids (files under harnesses/)
    tasks: list[str]
    repeats: int = 3
    parallel: int = 3
    seed: int = 0
    temperature: Optional[float] = None
    sentinel: Optional[dict] = None           # overrides HarnessConfig.sentinel for every harness in this job
    sentinel_ab: bool = False                 # also run each harness with the sentinel enabled as "<id>+sentinel"
    sentinel_only: bool = False               # run each harness ONLY as "<id>+sentinel" (pair with plain cells already in `out`)
    price: Optional[list[float]] = None
    max_cost_usd: Optional[float] = None      # stop submitting work once the job's ledger cost reaches this


JOBS: dict[str, dict] = {}
_INDEX_LOCK = threading.Lock()
_EXEC = ThreadPoolExecutor(max_workers=8)


def job_public(j: dict) -> dict:
    d = {k: v for k, v in j.items() if k not in ("_future",)}
    d["spent"] = round(sum((r.get("cost") or 0.0) for r in j.get("results", [])), 6)
    return d


def _harness_by_id(hid: str) -> HarnessConfig:
    for fn in os.listdir(HARNESS_DIR):
        if fn.endswith(".json"):
            h = HarnessConfig.load(os.path.join(HARNESS_DIR, fn))
            if h.id == hid:
                return h
    raise HTTPException(404, f"no harness {hid}")


def _seed_for(job_seed: int, task_id: str, harness_id: str, model: str, repeat: int) -> int:
    """X and X+sentinel share seeds: the pair differs only by the hook."""
    base = harness_id[: -len("+sentinel")] if harness_id.endswith("+sentinel") else harness_id
    return job_seed * 1_000_000 + zlib.crc32(f"{task_id}|{base}|{model}".encode()) % 10_000 * 100 + repeat


def _expand_harnesses(job: dict) -> list[HarnessConfig]:
    """The harness list a job actually runs: plain, plain + "+sentinel" twin (A/B), or the twin only."""
    out = []
    for hid in job["harnesses"]:
        h = _harness_by_id(hid)
        if job.get("temperature") is not None:
            h.temperature = job["temperature"]
        if job.get("sentinel") is not None:
            h.sentinel = S.merged_config(job["sentinel"])
        withs = HarnessConfig(**{**asdict(h), "id": h.id + "+sentinel", "sentinel": {**S.merged_config(h.sentinel), "enabled": True}})
        plain = HarnessConfig(**{**asdict(h), "sentinel": {**h.sentinel, "enabled": False}})
        if job.get("sentinel_only"):
            out.append(withs)
        elif job.get("sentinel_ab"):
            out += [plain, withs]
        else:
            out.append(h)
    return out


def _run_one(job: dict, cell: dict, task: dict, harness: HarnessConfig, repeat: int, rmodel: S.RiskModel):
    if job.get("cancel"):
        return                                    # every cell is submitted up front; a cancel or a spend cap stops the rest here
    out_dir = os.path.join(M.RUNS_ROOT, job["out"])
    run_id = new_run_id()
    seed = _seed_for(job["seed"], task["id"], harness.id, cell["model"], repeat)
    kw = {}
    if cell["model"].startswith("mock"):
        # "mock", "mock-b", ...: the offline scripted agent, labelled as given (lets you rehearse multi-model views offline)
        provider = make_provider("mock", cell["model"], temperature=harness.temperature, seed=seed)
    else:
        provider = make_provider("openai", cell["model"], temperature=harness.temperature, price=job.get("price"),
                                 api_key=SETTINGS["openrouter_key"], base_url=SETTINGS["base_url"])
    BUS.publish("run_start", job=job["id"], run_id=run_id, task=task["id"], harness=harness.id, model=cell["model"], repeat=repeat)
    job["running"].append(run_id)

    def listener(rec):
        BUS.publish("span", job=job["id"], run_id=run_id, span=rec)

    hook = S.make_hook(harness.sentinel, rmodel, task_issue=task["issue"], api_key=SETTINGS["openrouter_key"],
                       base_url=SETTINGS["base_url"]) if harness.sentinel.get("enabled") else None
    try:
        s = run_task(task, harness, provider, out_dir, repeat_index=repeat, seed=seed, span_listener=listener, step_hook=hook, run_id=run_id)
        with _INDEX_LOCK:
            with open(os.path.join(out_dir, "index.jsonl"), "a") as f:
                f.write(json.dumps(asdict(s)) + "\n")
        job["done"] += 1
        job["running"].remove(run_id)
        job["results"].append({"run_id": run_id, "task": task["id"], "harness": harness.id, "model": cell["model"], "repeat": repeat,
                               "hidden_pass": s.hidden_pass, "steps": s.steps, "exit": s.exit_reason, "cost": s.cost_usd,
                               "interventions": s.sentinel_interventions, "max_risk": s.sentinel_max_risk, "boundary": s.boundary_events})
        cap = job.get("max_cost_usd")
        if cap is not None and not job.get("cancel") and job_public(job)["spent"] >= cap:
            job["cancel"] = True
            job["stop_reason"] = f"spend cap ${cap:.2f} reached"
        BUS.publish("run_end", job=job["id"], run_id=run_id, summary=asdict(s))
    except Exception as e:
        job["done"] += 1
        if run_id in job["running"]:
            job["running"].remove(run_id)
        job["errors"].append(f"{task['id']}/{harness.id}/{cell['model']}: {e}")
        BUS.publish("run_error", job=job["id"], run_id=run_id, error=str(e), trace=traceback.format_exc()[-800:])


def _run_job(job: dict):
    job["status"] = "running"
    BUS.publish("job", job=job_public(job))
    rmodel = S.load_model()
    try:
        harnesses = _expand_harnesses(job)
        tasks_ = [load_task(os.path.join(TASK_DIR, t)) for t in job["tasks"]]
        os.makedirs(os.path.join(M.RUNS_ROOT, job["out"]), exist_ok=True)
        work = [(cell, t, h, r) for cell in [{"model": m} for m in job["models"]] for h in harnesses for t in tasks_ for r in range(job["repeats"])]
        job["total"] = len(work)
        with ThreadPoolExecutor(max_workers=max(1, min(8, job["parallel"]))) as ex:
            futs = [ex.submit(_run_one, job, c, t, h, r, rmodel) for c, t, h, r in work]
            for f in futs:
                if job.get("cancel"):
                    f.cancel()
            for f in futs:
                try:
                    f.result()
                except Exception:
                    pass
        job["status"] = "stopped" if job.get("stop_reason") else ("cancelled" if job.get("cancel") else "finished")
    except Exception as e:
        job["status"] = "error"
        job["errors"].append(str(e))
    job["finished_at"] = time.time()
    BUS.publish("job", job=job_public(job))


@app.post("/api/jobs")
def create_job(j: JobIn):
    if any(not m.startswith("mock") for m in j.models) and not SETTINGS["openrouter_key"]:
        raise HTTPException(400, "OpenRouter key not set (export OPENROUTER_API_KEY or paste it in Settings)")
    if not j.out or "/" in j.out or j.out.startswith("."):
        raise HTTPException(400, "bad results dir name")
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "status": "queued", "created_at": time.time(), "done": 0, "total": 0, "running": [], "results": [], "errors": [],
           **j.model_dump()}
    JOBS[jid] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job_public(job)


@app.get("/api/jobs")
def jobs():
    return [job_public(j) for j in JOBS.values()]


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    """Stop a running job.

    Kept when the retired page set went: the Field document still renders a cancel button for every
    running job (backend/field.html), and it is served from this app. Without this route that button
    POSTs into the SPA catch-all, gets a 200 with index.html, and logs "cancelled job <id>" about a
    job that keeps running -- a worse outcome than having no button. The spend cap is not a
    substitute: it ends a job when the money runs out, not when the operator asks.
    """
    if jid not in JOBS:
        raise HTTPException(404)
    JOBS[jid]["cancel"] = True
    return job_public(JOBS[jid])


# --------------------------------------------------------------------------- sentinel
@app.get("/api/sentinel")
def sentinel():
    model = S.load_model()
    return {"model": model.to_json(), "patterns": S.PATTERNS, "default_config": S.DEFAULT_SENTINEL,
            "features": S.FEATURE_NAMES, "models": S.list_models(), "active": S.active_model_name()}


@app.get("/api/sentinel/replay_all/{name}")
def sentinel_replay_all(name: str, harness: Optional[str] = None):
    """Risk curves for every run in a results dir: the early-detection picture over a whole cell."""
    d = _dir(name)
    model = S.load_model()
    out = []
    for r in M.rows_for(name, harness_id=harness):
        p = os.path.join(d, r["run_id"], "ledger.jsonl")
        if not os.path.exists(p):
            continue
        spans = [json.loads(l) for l in open(p) if l.strip()]
        rp = S.replay(spans, model)
        out.append({"run_id": r["run_id"], "task": r["task_id"], "harness": r["harness_id"], "hidden_pass": r["hidden_pass"],
                    "visible_pass": r.get("visible_pass"), "risk": [x["risk"] for x in rp], "patterns": [x["patterns"] for x in rp],
                    "max_risk": max((x["risk"] for x in rp), default=0)})
    return out


# --------------------------------------------------------------------------- real trajectories (exercise 7)
class RealImportIn(BaseModel):
    n: int = 500
    seed: int = 0
    model: Optional[str] = None
    out: Optional[str] = None


REAL_STATE = {"status": "idle", "progress": None, "error": None, "result": None}


@app.post("/api/real/import")
def real_import(r: RealImportIn):
    if REAL_STATE["status"] == "running":
        raise HTTPException(409, "import already running")
    out = r.out or f"real_swe_agent_{r.n}" + (f"_{r.model.split('/')[-1]}" if r.model else "") + (f"_s{r.seed}" if r.seed else "")
    if "/" in out or out.startswith("."):
        raise HTTPException(400, "bad results dir name")

    def work():
        REAL_STATE.update(status="running", progress=[0, r.n], error=None, result=None)
        BUS.publish("real_import", status="running", out=out)
        try:
            def prog(i, n):
                REAL_STATE["progress"] = [i, n]
                BUS.publish("real_import", status="running", out=out, progress=[i, n])
            res = RI.import_runs(r.n, os.path.join(M.RUNS_ROOT, out), seed=r.seed, model=r.model, progress=prog)
            REAL_STATE.update(status="done", result={**res, "out": out})
        except Exception as e:
            REAL_STATE.update(status="error", error=f"{type(e).__name__}: {e}")
        BUS.publish("real_import", status=REAL_STATE["status"], out=out)

    threading.Thread(target=work, daemon=True).start()
    return {"status": "running", "out": out}


@app.get("/api/real/status")
def real_status():
    return REAL_STATE


@app.get("/api/real/{name}/analysis")
def real_analysis(name: str):
    _dir(name)
    return M._clean(RI.analysis(os.path.join(M.RUNS_ROOT, name)))


# --------------------------------------------------------------------------- judge (exercise 4)
class JudgeIn(BaseModel):
    results: str
    judge: str = "mock"           # mock | openrouter
    model: str = "mock"
    n: int = 24
    repeats: int = 2
    seed: int = 0


JUDGE_STATE = {"status": "idle", "stage": None, "error": None, "report": None, "history": []}


@app.post("/api/judge/run")
def judge_run(j: JudgeIn):
    if JUDGE_STATE["status"] == "running":
        raise HTTPException(409, "a judge run is already in progress")
    d = _dir(j.results)
    if j.judge != "mock" and not SETTINGS["openrouter_key"]:
        raise HTTPException(400, "OpenRouter key not set")

    def work():
        JUDGE_STATE.update(status="running", stage="sampling", error=None)
        BUS.publish("judge", status="running")
        try:
            def prog(stage):
                JUDGE_STATE["stage"] = stage
                BUS.publish("judge", status="running", stage=stage)
            rep = JR.run(d, judge=j.judge, model=j.model if j.judge != "mock" else "mock", n=j.n, repeats=j.repeats, seed=j.seed,
                         api_key=SETTINGS["openrouter_key"], base_url=SETTINGS["base_url"], progress=prog)
            JUDGE_STATE.update(status="done", report=M._clean(rep))
            JUDGE_STATE["history"] = ([{"judge": rep["judge"], "model": rep["model"], "results": rep["results"], "n": rep["n"],
                                        "agreement": rep["single"]["agreement"], "kappa": rep["single"]["kappa"], "test_retest": rep["single"]["test_retest"],
                                        "position_consistent": (rep["pairwise"] or {}).get("position_consistent"), "cost": rep["spend"]["cost_usd"]}] + JUDGE_STATE["history"])[:10]
        except Exception as e:
            JUDGE_STATE.update(status="error", error=f"{type(e).__name__}: {e}")
        BUS.publish("judge", status=JUDGE_STATE["status"])

    threading.Thread(target=work, daemon=True).start()
    return {"status": "running"}


@app.get("/api/judge")
def judge_state():
    return JUDGE_STATE


def main():
    import uvicorn
    port = int(os.environ.get("HARNESSLAB_PORT", "8765"))
    print(f"harnesslab -> http://127.0.0.1:{port}   (lab root: {LAB_ROOT}; OpenRouter key: {'set' if SETTINGS['openrouter_key'] else 'NOT set'})")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()


# --- fork routes ---
from harnesslab.backend.fork import router as fork_router, hoist_api_routes as _fork_hoist
app.include_router(fork_router)
_fork_hoist(app)   # keep /api/* ahead of the SPA catch-all registered above (see fork.py)

# --- repeated-commands chart routes ---
from harnesslab.backend.repeats_api import router as repeats_router
app.include_router(repeats_router)
_fork_hoist(app)   # same catch-all hoist fork.py uses; safe to call again (stable sort)

# --- failure modes: how a run failed, not only whether it did ---
from harnesslab.backend.outcomes_api import router as outcomes_router
app.include_router(outcomes_router)
_fork_hoist(app)

# --- live run list + per-run SSE stream ---
from harnesslab.backend.live_api import router as live_router
app.include_router(live_router)
_fork_hoist(app)   # keep /api/live and /api/runs/*/stream ahead of the SPA catch-all

# --- harness routes ---
from harnesslab.backend.harness_tools import router as harness_router
app.include_router(harness_router)
# The SPA catch-all above ("/{path:path}", registered only when frontend/dist exists) would
# otherwise shadow every GET route added after it. Routes are matched in order and list.sort is
# stable, so this only moves the catch-all to the end and is safe to repeat.
app.router.routes.sort(key=lambda r: 1 if getattr(r, "path", "") == "/{path:path}" else 0)


# --- sentinel routes ---
from harnesslab.backend.sentinel_ext import router as sentinel_router
app.include_router(sentinel_router)
# Same reason as above: keep the SPA catch-all last so /api/sentinel/* stays reachable (stable sort).
app.router.routes.sort(key=lambda r: 1 if getattr(r, "path", "") == "/{path:path}" else 0)

# --- packaging routes ---
from harnesslab.backend.export import router as packaging_router
app.include_router(packaging_router)
# Keep the SPA catch-all last so /api/export/* stays reachable once frontend/dist exists (stable sort).
app.router.routes.sort(key=lambda r: 1 if getattr(r, "path", "") == "/{path:path}" else 0)

# --- importers routes ---
from harnesslab.backend.importers.routes import router as importers_router, hoist_api_routes as _importers_hoist
app.include_router(importers_router)
_importers_hoist(app)   # keep /api/import/* ahead of the SPA catch-all registered above (see importers/routes.py)


# --- the Field (projector cold-open, ported from agent-lab) ---
from harnesslab.backend.field import router as field_router
app.include_router(field_router)

from .capture_api import capture_router                      # noqa: E402  (mounted with its siblings)
app.include_router(capture_router(lambda: M.RUNS_ROOT))
# Same reason as the routers above: keep the SPA catch-all last so /api/bundle stays reachable.
app.router.routes.sort(key=lambda r: 1 if getattr(r, "path", "") == "/{path:path}" else 0)


# --------------------------------------------------------------------------- frontend
if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    # The page shell and the build stamp are never cached: after a rebuild, the next load of an
    # open tab must get the new index.html (the hashed assets under /assets can be cached forever).
    _NO_CACHE = {"Cache-Control": "no-cache"}

    @app.get("/{path:path}")
    def spa(path: str):
        p = os.path.join(DIST, path)
        if path and os.path.isfile(p):
            return FileResponse(p, headers=_NO_CACHE if path in ("index.html", "build-source.json") else None)
        return FileResponse(os.path.join(DIST, "index.html"), headers=_NO_CACHE)
else:
    @app.get("/")
    def no_ui():
        return PlainTextResponse("harnesslab API is running. Build the UI: cd harnesslab/frontend && npm install && npm run build")


# --- assistant routes (the lab assistant: deterministic findings + a grounded model answer) ---
from harnesslab.backend.assist import router as assist_router


@app.get("/api/settings")
def get_settings():
    """Everything the settings drawer needs in one read. The key itself is never returned."""
    k = SETTINGS["openrouter_key"]
    return {"key_present": bool(k), "key_hint": (k[:7] + "…" + k[-4:]) if len(k) > 14 else "",
            "base_url": SETTINGS["base_url"], "lab_root": LAB_ROOT,
            "results_root": M.RUNS_ROOT, "harness_dir": HARNESS_DIR, "task_dir": TASK_DIR,
            "results": [d["name"] for d in M.results_dirs()],
            "harnesses": [h["id"] for h in list_harnesses()],
            "sentinel": S.load_model().meta}


app.include_router(assist_router)
# Same reason as every router above: keep the SPA catch-all last (stable sort).
app.router.routes.sort(key=lambda r: 1 if getattr(r, "path", "") == "/{path:path}" else 0)
