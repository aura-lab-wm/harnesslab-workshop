"""live -- the run list and the per-run event stream over harnesslab.core.live.

Endpoints (prefix /api):
  GET /live?recent=86400                 every run whose ledger was written in the last `recent`
                                         seconds, newest first, with its state (running / finished /
                                         abandoned) -- core/live.scan(), which never enters captured/
  GET /runs/{dir}/{run_id}/stream        Server-Sent Events: one `data:` event per span with
                                         `id: <seq>`; `Last-Event-ID` resumes from seq + 1; a
                                         `: keepalive` comment every KEEPALIVE_S; `event: end`
                                         carrying summary.json once the end span has been sent.
                                         The file is polled in a thread pool, never on the loop.

`dir` and `run_id` are validated like repeats_api (no separator, no leading dot, no control
character) and the resolved path must stay under the runs root once symlinks are followed.
`captured` (by name, case, or symlink alias -- results_scope.is_private) is refused with 403; on
the real app the private-results middleware already answers 404 first, so the 403 is defense in
depth for any other mount. Both routes are read-only GETs: no new CSRF surface.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from harnesslab.core import live

from .paths import RUNS_ROOT
from .repeats_api import _safe_name
from .results_scope import PRIVATE_DIRS, is_private

router = APIRouter(prefix="/api")

POLL_S = 0.25            # how often a tail looks for new spans
IDLE_TIMEOUT_S = 600.0   # no span for this long -> abandoned (the same grace scan() uses)
KEEPALIVE_S = 15.0       # SSE comment cadence while nothing happens
SUMMARY_WAIT_S = 5.0     # how long the stream waits for summary.json after the end span

#: One thread per open stream, released within one poll of the client going away (tail's `stop`).
_TAILERS = ThreadPoolExecutor(max_workers=32, thread_name_prefix="live-tail")
_DONE = object()

#: Cap on streams open at once: each one holds a _TAILERS thread for as long as it stays open, so
#: an unbounded number of open tabs/reconnects could claim the whole pool. Well under _TAILERS'
#: own size so a saturated gate still leaves headroom for streams already open to keep polling.
MAX_CONCURRENT_STREAMS = 8


class _StreamGate:
    """A counting gate, not a queue: the (cap+1)th caller is refused outright (429) rather than
    waiting for a slot. `acquire` and `release` are the only mutators and both take the lock, so
    concurrent requests (this route runs on the asyncio event loop, but the gate is plain data,
    not loop-bound) never race the count."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self._lock = threading.Lock()
        self._n = 0

    def acquire(self) -> bool:
        with self._lock:
            if self._n >= self.cap:
                return False
            self._n += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._n = max(0, self._n - 1)   # tolerate a stray extra release rather than go negative


_STREAM_GATE = _StreamGate(MAX_CONCURRENT_STREAMS)


def _run_path(dirname: str, run_id: str) -> str:
    _safe_name(dirname, "results dir name")
    _safe_name(run_id, "run_id")
    if is_private(RUNS_ROOT, dirname):
        raise HTTPException(403, "captured sessions are private")
    p = os.path.join(RUNS_ROOT, dirname, run_id, "ledger.jsonl")
    real_root = os.path.realpath(RUNS_ROOT)
    if os.path.commonpath([real_root, os.path.realpath(p)]) != real_root:
        raise HTTPException(400, "bad run path")
    if not os.path.isfile(p):
        raise HTTPException(404, f"no run {dirname}/{run_id}")
    return p


@router.get("/live")
def live_runs(recent: float = 86400.0):
    return live.scan(RUNS_ROOT, recent=recent, exclude=PRIVATE_DIRS)


def sse(rec: dict) -> str:
    """One SSE frame for a span: `id: <seq>` when it has one (so Last-Event-ID can resume), then
    the JSON as `data:`. Synthetic live_status records carry no id."""
    seq = rec.get("seq")
    head = f"id: {seq}\n" if isinstance(seq, int) else ""
    return head + "data: " + json.dumps(rec, ensure_ascii=False, default=str) + "\n\n"


def _wait_summary(path: str, timeout: float):
    """summary.json as a dict once it exists and parses, or None after `timeout` seconds. The
    runner writes it right after the end span (core/harness.py), so this is normally instant."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)


@router.get("/runs/{dir}/{run_id}/stream")
async def stream(dir: str, run_id: str, request: Request):   # `dir` shadows the builtin here on purpose: the template names the route
    path = _run_path(dir, run_id)
    if not _STREAM_GATE.acquire():
        raise HTTPException(429, "too many open run streams -- try again shortly")
    try:
        # A run already silent past the grace replays and is reported abandoned at once, instead
        # of holding the page in "running" for another whole idle timeout.
        idle = 0.0 if live.state_of(path) == "abandoned" else IDLE_TIMEOUT_S
        last = (request.headers.get("last-event-id") or "").strip()
        from_seq = int(last) + 1 if last.isdigit() else 0
        stop = threading.Event()
        q: queue.Queue = queue.Queue()

        def pump():
            try:
                for rec in live.tail(path, from_seq=from_seq, poll=POLL_S, idle_timeout=idle, stop=stop.is_set):
                    q.put(rec)
            finally:
                q.put(_DONE)

        _TAILERS.submit(pump)
    except Exception:
        _STREAM_GATE.release()
        raise

    async def gen():
        last_sent = time.monotonic()
        ended = False
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    rec = q.get_nowait()
                except queue.Empty:
                    if time.monotonic() - last_sent >= KEEPALIVE_S:
                        yield ": keepalive\n\n"
                        last_sent = time.monotonic()
                    await asyncio.sleep(0.05)
                    continue
                if rec is _DONE:
                    break
                yield sse(rec)
                last_sent = time.monotonic()
                if live.is_end(rec):
                    ended = True
            if ended:
                summary = await asyncio.to_thread(_wait_summary, os.path.join(os.path.dirname(path), "summary.json"), SUMMARY_WAIT_S)
                yield "event: end\ndata: " + json.dumps(summary, ensure_ascii=False, default=str) + "\n\n"
        finally:
            stop.set()
            _STREAM_GATE.release()   # the stream ended or the client disconnected -- either way, free the slot

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
