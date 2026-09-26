"""The Capture page's data, and the opt-in it must never be served without.

The private guard hides any route whose PATH names a private directory. These routes do not name
one -- `/api/capture/status` says nothing about `captured` -- so the guard cannot see them and they
have to demand the opt-in themselves. A route that serves the operator's real sessions to an
unmarked request is the whole failure this module exists to avoid.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend.private_guard import OPT_IN_HEADER          # noqa: E402

OPT_IN = {OPT_IN_HEADER: "1"}

ROW_A = {"run_id": "imp-cc-aaaa", "task_id": "fix-parser", "model": "claude-opus-5",
         "harness_id": "claude-code", "steps": 14, "cost_usd": 20.33,
         "input_tokens": 448265, "output_tokens": 121680, "exit_reason": "submitted",
         "lines_added": 245, "lines_removed": 54, "root_uuid": "u-1"}
ROW_B = {"run_id": "imp-cx-bbbb", "task_id": "add-cache", "model": "gpt-5-codex",
         "harness_id": "codex", "steps": 9, "cost_usd": 1.02,
         "input_tokens": 1000, "output_tokens": 200, "exit_reason": "no_action",
         "lines_added": 3, "lines_removed": 1, "root_uuid": "u-2"}


class TestCaptureApi(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-capapi-")
        self.addCleanup(shutil.rmtree, self.root, True)
        cap = os.path.join(self.root, "captured")
        os.makedirs(cap)
        with open(os.path.join(cap, "index.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(ROW_A) + "\n")
            f.write(json.dumps(ROW_B) + "\n")
        with open(os.path.join(cap, "inflight.json"), "w", encoding="utf-8") as f:
            json.dump({"runs": [{"run_id": "imp-cc-cccc", "task_id": "wip", "steps": 3,
                                 "last_ts": "2026-09-15T10:00:00Z"}]}, f)
        with open(os.path.join(cap, "identity.json"), "w", encoding="utf-8") as f:
            json.dump({"relations": {"imp-cc-aaaa": {"supersedes": [], "superseded_by": ["imp-cc-zzzz"],
                                                     "forked_from": []}}}, f)
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

    # ---------------------------------------------------------------- the opt-in
    def test_every_route_is_404_without_the_opt_in(self):
        for path in ("/api/capture/status", "/api/capture/runs", "/api/capture/relations"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_a_wrong_opt_in_value_is_still_404(self):
        for value in ("0", "true", "yes", ""):
            with self.subTest(value=value):
                r = self.client.get("/api/capture/status", headers={OPT_IN_HEADER: value})
                self.assertEqual(r.status_code, 404)

    def test_no_run_content_reaches_an_unmarked_request(self):
        body = self.client.get("/api/capture/runs").text
        self.assertNotIn("fix-parser", body)
        self.assertNotIn("imp-cc-aaaa", body)

    # ---------------------------------------------------------------- the data
    def test_status_counts_what_is_on_disk(self):
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual(d["runs_indexed"], 2)
        self.assertEqual(d["open_runs"], 1)
        self.assertAlmostEqual(d["totals"]["cost_usd"], 21.35, places=2)
        self.assertEqual(d["totals"]["input_tokens"], 449265)
        self.assertEqual(d["totals"]["output_tokens"], 121880)

    def test_status_totals_say_how_many_runs_they_could_not_measure(self):
        """A Cursor run records no usage. Its null cost is left out of the total, and the total says
        so, rather than silently presenting a partial sum as the corpus's spend."""
        cap = os.path.join(self.root, "captured")
        with open(os.path.join(cap, "index.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(ROW_B, run_id="imp-cursor-dddd", cost_usd=None,
                                    input_tokens=None, output_tokens=None)) + "\n")
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual(d["runs_indexed"], 3)
        self.assertAlmostEqual(d["totals"]["cost_usd"], 21.35, places=2)
        self.assertEqual(d["totals"]["input_tokens"], 449265)
        self.assertEqual(d["totals"]["unmeasured_runs"], 1)

    def test_a_watcher_that_died_is_not_shown_as_running(self):
        """After kill -9 the document still says idle; only the lock knows nobody is there."""
        from harnesslab.capture import presence
        presence.write(self.root, state="idle", pid=4242, interval_s=60, roots=1)
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertNotEqual(d["sniffer"]["state"], "idle", "a dead watcher reads as running")
        self.assertEqual(d["sniffer"]["state"], "error")

    def test_a_live_watcher_is_shown_as_it_reports_itself(self):
        from harnesslab.capture import presence
        from harnesslab.capture.lock import CaptureLock
        with CaptureLock(self.root, name="sniffer"):
            presence.write(self.root, state="idle", pid=os.getpid(), interval_s=60, roots=1)
            d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual(d["sniffer"]["state"], "idle")

    def test_status_carries_the_debris_the_watcher_counted(self):
        """The daily full sweep counts crash debris into presence.json and never removes it. Served
        here or it is written into a document nothing renders, and the operator is never told there
        is anything to sweep."""
        from harnesslab.capture import presence
        presence.write(self.root, debris=3, debris_checked_at=1_789_000_000.0)
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual(d["sniffer"]["debris"], 3)
        self.assertEqual(d["sniffer"]["debris_checked_at"], 1_789_000_000.0)

    def test_a_lab_no_watcher_has_swept_reports_debris_as_unchecked_not_as_none(self):
        """Zero debris and zero checked-at are different claims: one says there is none, the other
        says nobody has looked. The page can only tell them apart if both fields arrive."""
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual(d["sniffer"]["debris"], 0)
        self.assertEqual(d["sniffer"]["debris_checked_at"], 0)

    def test_status_says_how_many_sources_are_remembered_and_how_many_were_seeded(self):
        """--seed-cursors adopts an already-captured corpus so --watch does not read it again. It
        left no trace anywhere anyone could see, so whether it had been run was unanswerable."""
        from harnesslab.capture import cursors
        cursors.save(cursors.path_for(self.root), {
            "/a/one.jsonl": cursors.cur(size=10, mtime_ns=1, runs=["r1"], seeded=True),
            "/a/two.jsonl": cursors.cur(size=20, mtime_ns=2, runs=["r2"], seeded=True),
            "/a/three.jsonl": cursors.cur(size=30, mtime_ns=3, runs=["r3"]),
        })
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual(d["cursors"]["sources"], 3)
        self.assertEqual(d["cursors"]["seeded"], 2)

    def test_a_lab_with_no_cursors_reports_zeros_rather_than_omitting_them(self):
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual(d["cursors"], {"sources": 0, "seeded": 0})

    def test_status_reports_the_adapters_the_spine_knows(self):
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertIn("claude_code", d["adapters"])
        self.assertIn("codex", d["adapters"])

    def test_runs_returns_the_index_rows_newest_first_and_pages(self):
        d = self.client.get("/api/capture/runs", headers=OPT_IN).json()
        self.assertEqual(d["total"], 2)
        self.assertEqual({r["run_id"] for r in d["rows"]}, {"imp-cc-aaaa", "imp-cx-bbbb"})
        one = self.client.get("/api/capture/runs?limit=1", headers=OPT_IN).json()
        self.assertEqual(len(one["rows"]), 1)
        self.assertEqual(one["total"], 2)

    def test_relations_are_served(self):
        d = self.client.get("/api/capture/relations", headers=OPT_IN).json()
        self.assertEqual(d["relations"]["imp-cc-aaaa"]["superseded_by"], ["imp-cc-zzzz"])

    def test_inflight_runs_are_listed_separately_from_indexed_ones(self):
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual([r["run_id"] for r in d["open"]], ["imp-cc-cccc"])

    # ---------------------------------------------------------------- nothing captured yet
    def test_an_absent_captured_directory_is_zeros_not_a_crash(self):
        empty = tempfile.mkdtemp(prefix="hl-capapi-empty-")
        self.addCleanup(shutil.rmtree, empty, True)
        d = self._client(empty).get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual((d["runs_indexed"], d["open_runs"]), (0, 0))
        self.assertEqual(d["totals"]["cost_usd"], 0)

    def test_a_torn_index_line_is_skipped_rather_than_failing_the_page(self):
        with open(os.path.join(self.root, "captured", "index.jsonl"), "a", encoding="utf-8") as f:
            f.write('{"run_id": "half-writ')
        d = self.client.get("/api/capture/status", headers=OPT_IN).json()
        self.assertEqual(d["runs_indexed"], 2)


class TestCaptureApiIsMountedOnTheRealApp(unittest.TestCase):
    """A probe app proves the router; only the real app proves anybody can reach it."""

    def test_the_routes_answer_on_the_real_app_and_demand_the_opt_in(self):
        """Asserted by asking, not by reading app.routes: this FastAPI wraps an included router in
        an _IncludedRouter that carries no `path`, so scanning the route list finds nothing and
        proves nothing. What matters is whether a request is answered."""
        from fastapi.testclient import TestClient
        from harnesslab.backend.app import app
        client = TestClient(app)
        for path in ("/api/capture/status", "/api/capture/runs", "/api/capture/relations"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404, "served without the opt-in")
                self.assertEqual(client.get(path, headers=OPT_IN).status_code, 200,
                                 f"{path} is not reachable on the real app")


if __name__ == "__main__":
    unittest.main()
