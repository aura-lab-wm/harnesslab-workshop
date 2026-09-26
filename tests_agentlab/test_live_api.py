"""harnesslab.backend.live_api -- the run list and the SSE stream over harnesslab.core.live.

The router is mounted on a bare FastAPI app with RUNS_ROOT pointed at a temp directory (the same
pattern test_repeats.py uses), so every check here is about this router's own behaviour. On the
real app the private-results middleware answers 404 for `captured` before any router runs
(private_guard.py); this router's own 403 is what a bare mount shows.

Starlette's TestClient buffers a streaming response until the generator finishes (measured: two
frames yielded 0.4 s apart both arrive together), so the follow tests append spans from a thread
and assert on the ORDER of the whole body -- spans written after the request started must be in
it, after the ones that were there before, with `event: end` last."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI                     # noqa: E402
from fastapi.testclient import TestClient       # noqa: E402

import harnesslab.backend.live_api as api_mod   # noqa: E402
from live_fixtures import append, append_raw, chat, end, ledger, start, tool   # noqa: E402


def frames(text: str) -> list:
    """Parse an SSE body into [{id, event, data}], dropping comment-only frames."""
    out = []
    for block in text.split("\n\n"):
        fr = {"id": None, "event": "message", "data": None, "comment": None}
        for line in block.splitlines():
            if line.startswith(":"):
                fr["comment"] = line
            elif line.startswith("id: "):
                fr["id"] = line[4:]
            elif line.startswith("event: "):
                fr["event"] = line[7:]
            elif line.startswith("data: "):
                fr["data"] = line[6:]
        if fr["data"] is not None:
            out.append(fr)
    return out


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-live-api-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self._orig = (api_mod.RUNS_ROOT, api_mod.POLL_S, api_mod.IDLE_TIMEOUT_S, api_mod.KEEPALIVE_S)
        api_mod.RUNS_ROOT, api_mod.POLL_S = self.root, 0.02
        self.addCleanup(self._restore)
        app = FastAPI()
        app.include_router(api_mod.router)
        self.client = TestClient(app)

    def _restore(self):
        api_mod.RUNS_ROOT, api_mod.POLL_S, api_mod.IDLE_TIMEOUT_S, api_mod.KEEPALIVE_S = self._orig


class TestLiveList(_Base):
    def test_lists_recent_runs_newest_first_and_never_captured(self):
        old = ledger(self.root, "a", "r-old", [start(0), end(1)])
        t = time.time() - 30
        os.utime(old, (t, t))
        ledger(self.root, "a", "r-run", [start(0), chat(1, 0)])
        ledger(self.root, "captured", "r-secret", [start(0), chat(1, 0)])
        os.symlink(os.path.join(self.root, "captured"), os.path.join(self.root, "alias"))
        r = self.client.get("/api/live")
        self.assertEqual(r.status_code, 200)
        rows = r.json()
        self.assertEqual([(x["dir"], x["run_id"], x["state"]) for x in rows],
                         [("a", "r-run", "running"), ("a", "r-old", "finished")])
        self.assertEqual(set(rows[0]), {"dir", "run_id", "task_id", "harness_id", "model", "repeat_index",
                                        "started_ts", "last_ts", "last_seq", "state"})

    def test_recent_query_bounds_the_listing(self):
        p = ledger(self.root, "a", "r1", [start(0), end(1)])
        t = time.time() - 100
        os.utime(p, (t, t))
        self.assertEqual(self.client.get("/api/live?recent=50").json(), [])
        self.assertEqual(len(self.client.get("/api/live?recent=500").json()), 1)

    def test_empty_root_is_an_empty_list(self):
        self.assertEqual(self.client.get("/api/live").json(), [])


def _summary(path_of_ledger, **extra):
    d = os.path.dirname(path_of_ledger)
    with open(os.path.join(d, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"run_id": "r1", "task_id": "t01_slugify", "harness_id": "baseline", "model": "mock",
                   "exit_reason": "submitted", "hidden_pass": True, "wall_ms": 5, "steps": 2, **extra}, f)


class TestStream(_Base):
    def test_replays_then_follows_a_writer_thread_and_ends_with_the_summary(self):
        p = ledger(self.root, "a", "r1", [start(0), chat(1, 0)])

        def writer():
            time.sleep(0.15)
            append(p, [tool(2), chat(3, 1)])
            time.sleep(0.1)
            append(p, [end(4)])
            _summary(p)

        threading.Thread(target=writer, daemon=True).start()
        r = self.client.get("/api/runs/a/r1/stream")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/event-stream", r.headers["content-type"])
        fr = frames(r.text)
        self.assertEqual([f["id"] for f in fr if f["id"] is not None], ["0", "1", "2", "3", "4"])
        self.assertEqual([json.loads(f["data"])["seq"] for f in fr[:5]], [0, 1, 2, 3, 4])
        self.assertEqual(fr[-1]["event"], "end")
        self.assertEqual(json.loads(fr[-1]["data"])["exit_reason"], "submitted")

    def test_a_finished_run_replays_everything_and_ends_at_once(self):
        p = ledger(self.root, "a", "r1", [start(0), chat(1, 0), tool(2), end(3)])
        _summary(p)
        t0 = time.monotonic()
        fr = frames(self.client.get("/api/runs/a/r1/stream").text)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual([f["id"] for f in fr[:-1]], ["0", "1", "2", "3"])
        self.assertEqual(fr[-1]["event"], "end")

    def test_last_event_id_resumes_after_the_given_seq(self):
        p = ledger(self.root, "a", "r1", [start(0), chat(1, 0), tool(2), chat(3, 1), end(4)])
        _summary(p)
        fr = frames(self.client.get("/api/runs/a/r1/stream", headers={"Last-Event-ID": "2"}).text)
        self.assertEqual([f["id"] for f in fr if f["id"] is not None], ["3", "4"])

    def test_keepalive_comment_while_idle(self):
        api_mod.KEEPALIVE_S = 0.05
        p = ledger(self.root, "a", "r1", [start(0)])

        def writer():
            time.sleep(0.3)
            append(p, [end(1)])
            _summary(p)

        threading.Thread(target=writer, daemon=True).start()
        self.assertIn(": keepalive", self.client.get("/api/runs/a/r1/stream").text)

    def test_abandoned_after_the_idle_timeout_and_no_end_event(self):
        api_mod.IDLE_TIMEOUT_S = 0.2
        ledger(self.root, "a", "r1", [start(0), chat(1, 0)])
        fr = frames(self.client.get("/api/runs/a/r1/stream").text)
        self.assertEqual(json.loads(fr[-1]["data"]), {"span": "live_status", "status": "abandoned"})
        self.assertNotIn("end", [f["event"] for f in fr])

    def test_a_run_already_silent_past_the_grace_replays_then_reports_abandoned_at_once(self):
        p = ledger(self.root, "a", "r1", [start(0), chat(1, 0)])
        t = time.time() - 700
        os.utime(p, (t, t))
        t0 = time.monotonic()
        fr = frames(self.client.get("/api/runs/a/r1/stream").text)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual([f["id"] for f in fr[:2]], ["0", "1"])
        self.assertEqual(json.loads(fr[-1]["data"])["status"], "abandoned")

    def test_end_without_a_summary_file_still_ends_with_null(self):
        api_mod.SUMMARY_WAIT_S = 0.1
        self.addCleanup(setattr, api_mod, "SUMMARY_WAIT_S", 5.0)
        ledger(self.root, "a", "r1", [start(0), end(1)])
        fr = frames(self.client.get("/api/runs/a/r1/stream").text)
        self.assertEqual(fr[-1]["event"], "end")
        self.assertIsNone(json.loads(fr[-1]["data"]))

    def test_unreadable_line_is_reported_without_an_id_and_the_stream_goes_on(self):
        p = ledger(self.root, "a", "r1", [start(0)])
        append_raw(p, "{oops\n")
        append(p, [end(1)])
        _summary(p)
        fr = frames(self.client.get("/api/runs/a/r1/stream").text)
        bad = [f for f in fr if f["id"] is None and f["event"] == "message"]
        self.assertEqual(json.loads(bad[0]["data"]), {"span": "live_status", "status": "unreadable_line", "line": 2})
        self.assertEqual(fr[-1]["event"], "end")

    def test_missing_run_is_404_and_missing_dir_is_404(self):
        self.assertEqual(self.client.get("/api/runs/a/nope/stream").status_code, 404)
        self.assertEqual(self.client.get("/api/runs/nope/r1/stream").status_code, 404)

    def test_captured_is_refused_with_403_even_by_case_or_alias(self):
        ledger(self.root, "captured", "r1", [start(0), chat(1, 0)])
        os.symlink(os.path.join(self.root, "captured"), os.path.join(self.root, "alias"))
        for d in ("captured", "Captured", "alias"):
            with self.subTest(dir=d):
                self.assertEqual(self.client.get(f"/api/runs/{d}/r1/stream").status_code, 403)

    def test_traversal_and_dot_names_are_400(self):
        ledger(self.root, "a", "r1", [start(0), end(1)])
        for url in ("/api/runs/a/%2E%2E/stream", "/api/runs/%2E%2E/r1/stream",
                    "/api/runs/a/.hidden/stream", "/api/runs/a/r1%00/stream"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 400)

    def test_embedded_encoded_slash_traversal_does_not_reach_the_run(self):
        """`..%2F..%2Fetc` decodes at the ASGI layer (scope["path"]) BEFORE routing ever sees it
        (uvicorn and Starlette's TestClient both decode percent-escapes into scope["path"], per the
        ASGI spec), so the extra `/` segments make the URL simply not match this route's shape --
        a 404 from Starlette's router, never reaching `_run_path`'s own guard. Same characteristic
        already documented for `/api/repeats/...` in test_repeats.py
        (test_dir_with_embedded_traversal_segment_is_rejected / ..._over_http): the guard is not
        reachable for this exact payload shape, so the codebase's existing precedent is "not 200",
        not a guaranteed 400. This is defense in depth on top of that 404, not a gap: no handler
        ever runs and nothing is ever read outside RUNS_ROOT."""
        ledger(self.root, "a", "r1", [start(0), end(1)])
        r = self.client.get("/api/runs/a/..%2F..%2Fetc/stream")
        self.assertNotEqual(r.status_code, 200)

    def test_a_symlink_escaping_the_runs_root_is_refused(self):
        outside = tempfile.mkdtemp(prefix="hl-live-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        ledger(outside, "x", "r1", [start(0), end(1)])
        os.symlink(os.path.join(outside, "x"), os.path.join(self.root, "escape"))
        self.assertEqual(self.client.get("/api/runs/escape/r1/stream").status_code, 400)


class TestStreamCap(_Base):
    """Each open /stream holds one thread from the fixed 32-worker _TAILERS pool for as long as
    the connection stays open (risk 1 in 00-context.md). Without a cap, enough open tabs could
    claim the whole pool. api_mod._STREAM_GATE is exercised directly (acquire/release) rather than
    by holding real concurrent connections open -- TestClient buffers a streaming response until
    its generator finishes (see the module docstring), so it cannot hold N connections open at
    once; this is the same module-knob pattern the tests above already use (IDLE_TIMEOUT_S,
    KEEPALIVE_S, SUMMARY_WAIT_S)."""
    def setUp(self):
        super().setUp()
        api_mod.SUMMARY_WAIT_S = 0.05   # no summary.json in this test's ledgers: fail fast, not after the real 5s default
        self.addCleanup(setattr, api_mod, "SUMMARY_WAIT_S", 5.0)
        self.addCleanup(setattr, api_mod._STREAM_GATE, "_n", 0)

    def test_the_cap_plus_one_stream_is_429_until_a_slot_is_released(self):
        ledger(self.root, "a", "r1", [start(0), end(1)])
        for _ in range(api_mod.MAX_CONCURRENT_STREAMS):
            self.assertTrue(api_mod._STREAM_GATE.acquire())
        r = self.client.get("/api/runs/a/r1/stream")
        self.assertEqual(r.status_code, 429)
        self.assertIn("detail", r.json())
        api_mod._STREAM_GATE.release()
        r2 = self.client.get("/api/runs/a/r1/stream")
        self.assertEqual(r2.status_code, 200)

    def test_a_real_stream_releases_its_slot_when_it_ends(self):
        ledger(self.root, "a", "r1", [start(0), end(1)])
        for _ in range(api_mod.MAX_CONCURRENT_STREAMS - 1):
            self.assertTrue(api_mod._STREAM_GATE.acquire())
        r = self.client.get("/api/runs/a/r1/stream")   # the cap-th stream: still within the cap
        self.assertEqual(r.status_code, 200)
        # A finished run's stream ends on its own (the `finally` in gen() releases the slot), so
        # the gate is back to its pre-request count once the (buffered) response has returned.
        self.assertTrue(api_mod._STREAM_GATE.acquire())
        self.assertFalse(api_mod._STREAM_GATE.acquire())


if __name__ == "__main__":
    unittest.main()
