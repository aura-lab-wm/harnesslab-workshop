"""Self-tests for the repeated-commands chart's backend (harnesslab/core/repeats.py), its API
router (harnesslab/backend/repeats_api.py), and its CLI.

Three layers, matching the module's own claim of "one definition, shared everywhere":
  1. the pure functions (`key`, `series`, `crossing`, `loop_onset`, `study`) against synthetic
     ledgers where the right answer is put there on purpose;
  2. that `trajtest.Trajectory.repeated_tool_calls` and `repeats.series(...)["cumulative"][-1]`
     agree on the SAME calls (key parity — there is only one place the key is computed);
  3. the API router (shapes, the harness filter, path-traversal rejection) and the CLI (its
     human-readable report and its --json/--csv output), each exercised against a real results
     directory written to disk exactly the way `real_import.py`/the lab writer would.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.core import repeats as R                              # noqa: E402
from harnesslab.core.trajtest import Trajectory, load_runs            # noqa: E402


def tool_call(name, args):
    return {"span": "execute_tool", "gen_ai.tool.name": name, "args": args}


# --------------------------------------------------------------------------- pure functions
class TestKey(unittest.TestCase):
    def test_same_name_and_args_is_the_same_key(self):
        a = tool_call("bash", {"command": "ls"})
        b = tool_call("bash", {"command": "ls"})
        self.assertEqual(R.key(a), R.key(b))

    def test_different_args_is_a_different_key(self):
        a = tool_call("bash", {"command": "ls"})
        b = tool_call("bash", {"command": "ls -la"})
        self.assertNotEqual(R.key(a), R.key(b))

    def test_key_is_order_insensitive_over_arg_fields(self):
        """json.dumps(..., sort_keys=True): field order in `args` must not matter."""
        a = tool_call("edit_file", {"path": "a.py", "old": "x", "new": "y"})
        b = tool_call("edit_file", {"new": "y", "path": "a.py", "old": "x"})
        self.assertEqual(R.key(a), R.key(b))

    def test_malformed_call_degrades_instead_of_raising(self):
        self.assertEqual(R.key({}), R.key({}))


class TestSeries(unittest.TestCase):
    def test_empty_run(self):
        s = R.series([])
        self.assertEqual(s, {"cumulative": [], "repeat_of": [], "n": 0})

    def test_no_repeats(self):
        calls = [tool_call("bash", {"command": c}) for c in ("a", "b", "c")]
        s = R.series(calls)
        self.assertEqual(s["cumulative"], [0, 0, 0])
        self.assertEqual(s["repeat_of"], [-1, -1, -1])

    def test_all_repeats(self):
        calls = [tool_call("bash", {"command": "same"}) for _ in range(5)]
        s = R.series(calls)
        self.assertEqual(s["cumulative"], [0, 1, 2, 3, 4])
        self.assertEqual(s["repeat_of"], [-1, 0, 0, 0, 0])
        self.assertEqual(s["cumulative"][-1], 4)

    def test_repeat_of_points_at_the_first_occurrence(self):
        calls = [tool_call("bash", {"command": v}) for v in ("a", "b", "a", "a")]
        s = R.series(calls)
        self.assertEqual(s["repeat_of"], [-1, -1, 0, 0])

    def test_final_cumulative_matches_a_plain_counter(self):
        """The docstring's cross-check: sum(count-1 for count>1) over a Counter of the same keys."""
        from collections import Counter
        vals = ["a", "b", "a", "c", "a", "b", "d"]
        calls = [tool_call("bash", {"command": v}) for v in vals]
        s = R.series(calls)
        c = Counter(vals)
        self.assertEqual(s["cumulative"][-1], sum(v - 1 for v in c.values() if v > 1))


class TestCrossing(unittest.TestCase):
    def test_never_crosses(self):
        self.assertIsNone(R.crossing([0, 1, 2, 3], threshold=3))

    def test_first_index_strictly_over_threshold(self):
        self.assertEqual(R.crossing([0, 1, 2, 3, 4, 4], threshold=3), 4)

    def test_empty(self):
        self.assertIsNone(R.crossing([], threshold=3))


class TestFmtPct(unittest.TestCase):
    """F8: Python's builtin round() is round-half-to-EVEN (banker's rounding); the JS port
    (repeats.js::fmtPct) uses Math.round(), which always rounds x.5 UP. The two must agree at
    every x.5 boundary -- the codebase's own invariant is that the CLI and the chart are always
    the same numbers."""

    def test_none_is_an_em_dash(self):
        self.assertEqual(R._fmt_pct(None), "—")

    def test_agrees_with_js_math_round_at_the_half_boundary(self):
        # round(0.125*100) in plain Python banker's rounding gives 12 (rounds to the nearest EVEN
        # integer); the fix must give 13, matching JS's Math.round(12.5) === 13. Verified by direct
        # interpreter comparison (not inference): python3 -c "print(round(12.5))" -> 12;
        # node -e "console.log(Math.round(12.5))" -> 13.
        self.assertEqual(R._fmt_pct(0.125), "13%")
        # A second x.5 boundary, confirmed empirically to diverge the same way (16 is even, so
        # banker's rounding keeps round(16.5) at 16; Math.round(16.5) === 17).
        self.assertEqual(R._fmt_pct(0.165), "17%")

    def test_non_boundary_values_are_unaffected(self):
        self.assertEqual(R._fmt_pct(0.18), "18%")
        self.assertEqual(R._fmt_pct(0.4), "40%")
        self.assertEqual(R._fmt_pct(0.0), "0%")


class TestLoopOnset(unittest.TestCase):
    def test_no_streak(self):
        self.assertIsNone(R.loop_onset([-1, -1, 0, -1, -1], min_len=5))

    def test_streak_too_short(self):
        self.assertIsNone(R.loop_onset([-1, 0, 0, 0, -1], min_len=5))

    def test_exact_streak(self):
        repeat_of = [-1, 0, 0, 0, 0, 0]  # indices 1..5, length 5
        self.assertEqual(R.loop_onset(repeat_of, min_len=5), (1, 5))

    def test_streak_touching_the_end(self):
        """The sentinel-flush case: a streak that never gets a trailing -1 to close it."""
        repeat_of = [-1, -1, 0, 0, 0, 0, 0]
        self.assertEqual(R.loop_onset(repeat_of, min_len=5), (2, 6))

    def test_longest_streak_wins_and_ties_keep_the_leftmost(self):
        repeat_of = [-1, 0, 0, 0, 0, 0, -1, -1, 1, 1, 1, 1, 1, -1]  # two streaks of length 5
        self.assertEqual(R.loop_onset(repeat_of, min_len=5), (1, 5))

    def test_all_repeats(self):
        repeat_of = [-1, 0, 0, 0, 0, 0]
        self.assertEqual(R.loop_onset(repeat_of, min_len=3), (1, 5))


# --------------------------------------------------------------------------- key parity with trajtest
class TestKeyParity(unittest.TestCase):
    """`Trajectory.repeated_tool_calls()` and `repeats.series(...)` must be computing the SAME
    thing over the SAME calls -- there is exactly one definition of "repeat" in this codebase."""

    def test_trajectory_delegates_to_repeats_key(self):
        calls = [tool_call("bash", {"command": v}) for v in ("a", "b", "a", "a", "c", "b")]
        t = Trajectory("r", "t", "h", "m", {}, calls)
        self.assertEqual(t.repeated_tool_calls(), R.series(calls)["cumulative"][-1])

    def test_agrees_on_zero_repeats(self):
        calls = [tool_call("read_file", {"path": p}) for p in ("a.py", "b.py", "c.py")]
        t = Trajectory("r", "t", "h", "m", {}, calls)
        self.assertEqual(t.repeated_tool_calls(), 0)
        self.assertEqual(R.series(calls)["cumulative"][-1], 0)


# --------------------------------------------------------------------------- a real results dir
def _write_run(root, run_id, task_id, harness_id, hidden_pass, calls, exit_reason="", max_steps=None):
    d = os.path.join(root, run_id)
    os.makedirs(d)
    start = {"span": "invoke_agent", "status": "start"}
    if max_steps is not None:
        start["harness"] = {"max_steps": max_steps}
    spans = [start, *calls, {"span": "invoke_agent", "status": "end", "hidden_pass": hidden_pass}]
    with open(os.path.join(d, "ledger.jsonl"), "w", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s) + "\n")
    row = {"run_id": run_id, "task_id": task_id, "harness_id": harness_id, "model": "mock",
           "hidden_pass": hidden_pass, "exit_reason": exit_reason}
    with open(os.path.join(d, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(row, f)
    return row


def make_results_dir(parent=None):
    """Three runs: a resolved run with no repeats, an unresolved run that loops (crosses the
    threshold and has a loop-onset streak), and an unknown-outcome run -- enough to exercise every
    branch of `study()` (bands only cover resolved/unresolved; summary covers all three).

    `parent`, when given, is where the results directory itself is created -- so a caller that
    needs a RUNS_ROOT distinct from the bare system tempdir (the path-traversal tests: an "outside"
    directory must be outside RUNS_ROOT, which is only true if RUNS_ROOT is its OWN tempdir and not
    just `dirname(mkdtemp())`, i.e. the system tempdir itself) can nest this under one."""
    root = tempfile.mkdtemp(prefix="hl-repeats-", dir=parent)
    rows = []
    ok_calls = [tool_call("bash", {"command": c}) for c in ("ls", "cat a.py", "run tests")]
    rows.append(_write_run(root, "run-ok", "task-a", "mock", True, ok_calls))

    loop_cmd = tool_call("bash", {"command": "flaky test"})
    bad_calls = [tool_call("bash", {"command": "ls"})] + [loop_cmd] * 6
    rows.append(_write_run(root, "run-loop", "task-a", "mock", False, bad_calls))

    unk_calls = [tool_call("bash", {"command": c}) for c in ("ls", "ls")]
    rows.append(_write_run(root, "run-unknown", "task-b", "mock", None, unk_calls))

    with open(os.path.join(root, "index.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return root


class TestStudy(unittest.TestCase):
    def setUp(self):
        self.root = make_results_dir()
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.data = R.study(self.root)

    def test_top_level_shape(self):
        for k in ("definition", "threshold", "runs", "bands", "summary", "total", "truncated"):
            self.assertIn(k, self.data)
        self.assertEqual(self.data["threshold"], R.THRESHOLD)

    def test_no_limit_is_not_truncated_and_total_matches_the_full_set(self):
        """The default (`limit=None`) is unbounded -- every existing caller of `study()` that
        never passed `limit` (the CLI, every test above this one) must keep seeing every run."""
        self.assertEqual(self.data["total"], 3)
        self.assertFalse(self.data["truncated"])
        self.assertEqual(len(self.data["runs"]), 3)

    def test_every_run_is_present_with_its_outcome(self):
        by_id = {r["run_id"]: r for r in self.data["runs"]}
        self.assertEqual(by_id["run-ok"]["outcome"], "resolved")
        self.assertEqual(by_id["run-loop"]["outcome"], "unresolved")
        self.assertEqual(by_id["run-unknown"]["outcome"], "unknown")

    def test_the_looping_run_crosses_and_has_a_loop_onset(self):
        row = next(r for r in self.data["runs"] if r["run_id"] == "run-loop")
        self.assertIsNotNone(row["crossing"])
        self.assertIsNotNone(row["loop"])
        self.assertGreaterEqual(row["loop"]["end"] - row["loop"]["start"], 4)

    def test_the_resolved_run_never_crosses(self):
        row = next(r for r in self.data["runs"] if r["run_id"] == "run-ok")
        self.assertIsNone(row["crossing"])

    def test_bands_cover_only_resolved_and_unresolved(self):
        self.assertEqual(set(self.data["bands"].keys()), {"resolved", "unresolved"})
        self.assertTrue(len(self.data["bands"]["resolved"]) > 0)
        self.assertTrue(len(self.data["bands"]["unresolved"]) > 0)

    def test_summary_covers_all_three_outcomes(self):
        self.assertEqual(self.data["summary"]["resolved"]["n"], 1)
        self.assertEqual(self.data["summary"]["unresolved"]["n"], 1)
        self.assertEqual(self.data["summary"]["unknown"]["n"], 1)

    def test_harness_filter(self):
        d2 = R.study(self.root, harness="nope")
        self.assertEqual(d2["runs"], [])

    def test_finding_sentence_uses_the_same_summary(self):
        s = R.finding_sentence(self.data)
        self.assertIn("unresolved", s)
        self.assertIn("resolved", s)
        self.assertTrue(s.endswith("."))

    def test_run_detail_shape_and_preview(self):
        rows = R.run_detail(self.root, "run-loop")
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["repeat_of"], -1)
        self.assertTrue(all(r["repeat_of"] in (-1, 1) for r in rows[1:]))
        self.assertTrue(any("flaky test" in r["command_preview"] for r in rows))

    def test_run_detail_on_a_missing_run_is_empty_not_an_error(self):
        self.assertEqual(R.run_detail(self.root, "does-not-exist"), [])

    def test_ordinary_runs_are_not_flagged_truncated(self):
        """None of the three fixture runs record a harness.max_steps on their start span."""
        for r in self.data["runs"]:
            self.assertFalse(r["truncated"], r["run_id"])


# --------------------------------------------------------------------------- study()'s `limit`
# (a results directory's `runs` used to come back with no cap at all -- tens of MB of per-run
# cumulative/repeat_of arrays on a 20k-run corpus, and RepeatsChart.jsx spread one Math.max
# argument per run over the whole set. `limit` caps how many runs get a full array built and
# serialized; `total`/`truncated` say the true count and whether the cap actually cut anything.)
def _make_many_runs(root, n, resolved_every=2):
    """`n` minimal two-call runs, alternating resolved/unresolved so bands/summary have both
    outcomes to work with; `index.jsonl` order is the same order `study()` sees (no shuffling),
    so "the first `limit` runs" is a deterministic, checkable set."""
    rows = []
    for i in range(n):
        calls = [tool_call("bash", {"command": f"cmd-{i}-0"}), tool_call("bash", {"command": f"cmd-{i}-1"})]
        hidden = (i % resolved_every == 0)
        rows.append(_write_run(root, f"run-{i:03d}", f"task-{i % 3}", "mock", hidden, calls))
    with open(os.path.join(root, "index.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return rows


class TestStudyLimit(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-repeats-limit-")
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.rows = _make_many_runs(self.root, 10)

    def test_limit_caps_the_runs_list_and_reports_the_true_total(self):
        data = R.study(self.root, limit=4)
        self.assertEqual(len(data["runs"]), 4)
        self.assertEqual(data["total"], 10)
        self.assertTrue(data["truncated"])

    def test_the_capped_runs_are_the_first_N_in_index_order(self):
        """Not an arbitrary subset -- the same order `load_runs` reads `index.jsonl` in, so which
        runs get shown is deterministic and reproducible from the directory alone."""
        data = R.study(self.root, limit=4)
        self.assertEqual([r["run_id"] for r in data["runs"]], ["run-000", "run-001", "run-002", "run-003"])

    def test_limit_at_the_exact_total_is_not_truncated(self):
        data = R.study(self.root, limit=10)
        self.assertEqual(len(data["runs"]), 10)
        self.assertFalse(data["truncated"])

    def test_limit_above_the_total_is_not_truncated(self):
        data = R.study(self.root, limit=500)
        self.assertEqual(len(data["runs"]), 10)
        self.assertEqual(data["total"], 10)
        self.assertFalse(data["truncated"])

    def test_no_limit_returns_everything_unbounded(self):
        data = R.study(self.root, limit=None)
        self.assertEqual(len(data["runs"]), 10)
        self.assertFalse(data["truncated"])

    def test_limit_zero_returns_no_runs_but_still_reports_the_true_total(self):
        data = R.study(self.root, limit=0)
        self.assertEqual(data["runs"], [])
        self.assertEqual(data["total"], 10)
        self.assertTrue(data["truncated"])

    def test_bands_and_summary_are_computed_over_the_capped_set_only(self):
        """run-000..run-003 (limit=4) are resolved, unresolved, resolved, unresolved (i % 2 == 0
        is resolved) -- 2 of each, not the full 10-run directory's 5-and-5. A summary/bands
        computed over the full corpus instead of the capped one would disagree with this."""
        data = R.study(self.root, limit=4)
        self.assertEqual(data["summary"]["resolved"]["n"], 2)
        self.assertEqual(data["summary"]["unresolved"]["n"], 2)
        # every band index has exactly 2 runs alive per outcome (2 calls each, none truncated
        # early), never 5 -- proof the bands never fell back to the uncapped set.
        for outcome in ("resolved", "unresolved"):
            for row in data["bands"][outcome]:
                self.assertEqual(row["n_alive"], 2, (outcome, row))

    def test_a_negative_limit_is_treated_as_no_cap(self):
        data = R.study(self.root, limit=-1)
        self.assertEqual(len(data["runs"]), 10)
        self.assertFalse(data["truncated"])


# --------------------------------------------------------------------------- the "truncated" flag
# (F4: a run whose OWN ledger hit the harness's configured call budget is a truncated PREFIX of a
# longer real run -- the crossing/loop/final-count study() reports for it must be reproducible
# from that flag, not quietly indistinguishable from a run that actually finished.)
class TestUnit_Truncated(unittest.TestCase):
    """Direct unit coverage of `_truncated`, isolating it from `study()`/`load_runs` -- see
    TestTruncatedFlag below for the same thing exercised through the full `study()` pipeline."""

    def test_hits_the_configured_cap(self):
        spans = [{"span": "invoke_agent", "status": "start", "harness": {"max_steps": 5}}]
        self.assertTrue(R._truncated(spans, n_calls=5))

    def test_under_the_cap_is_not_truncated(self):
        spans = [{"span": "invoke_agent", "status": "start", "harness": {"max_steps": 75}}]
        self.assertFalse(R._truncated(spans, n_calls=40))

    def test_no_harness_info_at_all_is_not_truncated(self):
        self.assertFalse(R._truncated([{"span": "invoke_agent", "status": "start"}], n_calls=999))

    def test_a_harness_with_no_configured_cap_is_not_truncated(self):
        """real_traj.py's to_results_dir() writes harness={"max_steps": None, ...} explicitly (it
        never slices the trajectory) -- an explicit None must not be treated as "hit the cap"."""
        spans = [{"span": "invoke_agent", "status": "start", "harness": {"max_steps": None}}]
        self.assertFalse(R._truncated(spans, n_calls=999))

    def test_exit_reason_alone_is_not_the_signal(self):
        """A run whose SOURCE exited due to its own budget, but whose ledger never reached the
        harness's configured cap, is NOT a truncated ledger -- exit_reason is not the signal
        (see _truncated's docstring: this was the F4 bug's first, over-triggering attempt)."""
        spans = [{"span": "invoke_agent", "status": "start", "harness": {"max_steps": 75}}]
        self.assertFalse(R._truncated(spans, n_calls=40))


class TestTruncatedFlag(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hl-repeats-trunc-")
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        loop_cmd = tool_call("bash", {"command": "flaky test"})
        capped_calls = [tool_call("bash", {"command": "ls"})] + [loop_cmd] * 6   # 7 calls
        rows = [
            # Hits its own harness's cap of 7: this run's ledger IS a truncated prefix.
            _write_run(self.root, "run-capped", "task-a", "mock", False, capped_calls,
                       exit_reason="budget_exceeded", max_steps=7),
            # Same exit_reason, but its harness allowed far more than 7 calls -- the run genuinely
            # finished in 7, nothing was cut off. Proves the fix no longer keys off exit_reason alone.
            _write_run(self.root, "run-genuinely-short", "task-a", "mock", True, capped_calls,
                       exit_reason="budget_exceeded", max_steps=75),
        ]
        with open(os.path.join(self.root, "index.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.data = R.study(self.root)

    def test_a_run_that_hits_its_harness_cap_is_flagged(self):
        row = next(r for r in self.data["runs"] if r["run_id"] == "run-capped")
        self.assertTrue(row["truncated"])

    def test_a_run_under_its_own_harness_cap_is_not_flagged_even_with_the_same_exit_reason(self):
        row = next(r for r in self.data["runs"] if r["run_id"] == "run-genuinely-short")
        self.assertFalse(row["truncated"])

    def test_cli_human_readable_report_notes_the_truncation(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = R.main(["--results", self.root, "--run", "run-capped"])
        self.assertEqual(rc, 0)
        self.assertIn("NOTE: this run's ledger hit its own harness's call budget", buf.getvalue())

    def test_cli_json_carries_the_flag(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            R.main(["--results", self.root, "--run", "run-capped", "--json"])
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["run"]["truncated"])


# --------------------------------------------------------------------------- the API router
class TestRepeatsApi(unittest.TestCase):
    def setUp(self):
        # A RUNS_ROOT of its own (not just dirname(mkdtemp()), which IS the bare system tempdir) --
        # otherwise the symlink-escape test's "outside" directory, also made with mkdtemp(), would
        # land as a SIBLING *inside* that same system tempdir and never actually be outside RUNS_ROOT.
        self.runs_root = tempfile.mkdtemp(prefix="hl-repeats-root-")
        self.addCleanup(__import__("shutil").rmtree, self.runs_root, True)
        self.root = make_results_dir(parent=self.runs_root)
        self.dirname = os.path.basename(self.root)

        import harnesslab.backend.repeats_api as api_mod
        self._api_mod = api_mod
        self._orig_root = api_mod.RUNS_ROOT
        api_mod.RUNS_ROOT = self.runs_root
        self.addCleanup(setattr, api_mod, "RUNS_ROOT", self._orig_root)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(api_mod.router)
        self.client = TestClient(app)

    def test_study_endpoint_shape(self):
        r = self.client.get(f"/api/repeats/{self.dirname}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["runs"]), 3)
        self.assertIn("bands", body)
        self.assertIn("summary", body)

    def test_study_endpoint_defaults_to_the_documented_500_limit(self):
        r = self.client.get(f"/api/repeats/{self.dirname}")
        body = r.json()
        # This fixture has only 3 runs -- well under the default -- so the point of this test is
        # that the endpoint's DEFAULT (not one the caller had to ask for) is exactly the module's
        # documented constant, not that it truncates here.
        self.assertEqual(self._api_mod.DEFAULT_LIMIT, 500)
        self.assertFalse(body["truncated"])
        self.assertEqual(body["total"], 3)

    def test_limit_query_param_caps_the_response(self):
        big_root = tempfile.mkdtemp(prefix="hl-repeats-root-big-", dir=self.runs_root)
        self.addCleanup(__import__("shutil").rmtree, big_root, True)
        _make_many_runs(big_root, 12)
        name = os.path.basename(big_root)

        r = self.client.get(f"/api/repeats/{name}?limit=5")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["runs"]), 5)
        self.assertEqual(body["total"], 12)
        self.assertTrue(body["truncated"])

    def test_run_endpoint_shape(self):
        r = self.client.get(f"/api/repeats/{self.dirname}/run/run-loop")
        self.assertEqual(r.status_code, 200)
        rows = r.json()
        self.assertEqual(len(rows), 7)
        self.assertIn("command_preview", rows[0])

    def test_unknown_dir_is_404(self):
        r = self.client.get("/api/repeats/does-not-exist")
        self.assertEqual(r.status_code, 404)

    def test_unknown_run_is_404(self):
        r = self.client.get(f"/api/repeats/{self.dirname}/run/does-not-exist")
        self.assertEqual(r.status_code, 404)

    # ------------------------------------------------------------- path traversal (adversarial pass)
    #
    # A literal ".." *segment* in a URL is normalised away by the HTTP client itself (RFC 3986 dot-
    # segment removal -- httpx does this before the request is ever sent), so
    # `GET /api/repeats/{dirname}/run/..` never actually reaches the server as "..": the client
    # collapses it to `GET /api/repeats/{dirname}` first. That is a real, useful defense (every
    # browser and most proxies do the same normalisation) but it means the HTTP round trip cannot
    # prove the SERVER'S OWN guard works -- only a direct call proves the guard rejects the raw
    # string. The two encoded-slash tests below (`..%2F...`) are NOT normalised client-side (percent-
    # encoding survives), so they exercise the real HTTP path with an actual traversal payload.
    def test_dotdot_dir_name_is_rejected_by_the_guard_directly(self):
        with self.assertRaises(Exception) as ctx:
            self._api_mod._dir("..")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

    def test_dotdot_run_id_is_rejected_by_the_guard_directly(self):
        with self.assertRaises(Exception) as ctx:
            self._api_mod._safe_name("..", "run_id")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

    def test_a_dot_prefixed_dir_name_is_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self._api_mod._dir(".hidden")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

    def test_dir_with_embedded_traversal_segment_is_rejected(self):
        """A results dir name that is itself literally "../etc" (no slash reaches FastAPI's path
        converter as one segment, but a name that STARTS with a dot is refused outright, and a
        realpath check on top of that catches anything that would resolve outside RUNS_ROOT)."""
        r = self.client.get("/api/repeats/..%2Fetc")
        self.assertIn(r.status_code, (400, 404))
        self.assertNotEqual(r.status_code, 200)

    def test_run_id_with_encoded_traversal_is_rejected_over_http(self):
        """The real end-to-end probe: an encoded ".." survives client normalisation, reaches the
        server as one path SEGMENT containing "..%2F..", and the guard must still refuse it."""
        r = self.client.get(f"/api/repeats/{self.dirname}/run/..%2F..%2F..%2Fetc%2Fpasswd")
        self.assertNotEqual(r.status_code, 200)

    def test_a_symlink_escaping_the_runs_root_is_rejected(self):
        """Defense in depth: even a name that passes the segment check must not resolve, via a
        symlink, to somewhere outside RUNS_ROOT once realpath follows it."""
        outside = tempfile.mkdtemp(prefix="hl-repeats-outside-")
        self.addCleanup(__import__("shutil").rmtree, outside, True)
        with open(os.path.join(outside, "index.jsonl"), "w") as f:
            f.write("")
        link = os.path.join(self._api_mod.RUNS_ROOT, "escape-link")
        os.symlink(outside, link)
        self.addCleanup(os.remove, link)
        r = self.client.get("/api/repeats/escape-link")
        self.assertEqual(r.status_code, 400)

    # ------------------------------------------------------------- embedded null byte (F7)
    #
    # os.path.realpath() raises an uncaught ValueError on an embedded "\x00", which used to
    # surface as an unhandled 500 instead of the clean 400 every other malformed name gets --
    # `_safe_name` must reject it before any os.path call ever sees it.
    def test_embedded_null_byte_in_dir_name_is_rejected_directly(self):
        with self.assertRaises(Exception) as ctx:
            self._api_mod._dir("real_swe_agent_500\x00.txt")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

    def test_embedded_null_byte_in_run_id_is_rejected_directly(self):
        with self.assertRaises(Exception) as ctx:
            self._api_mod._safe_name("foo\x00bar", "run_id")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

    def test_embedded_null_byte_over_http_is_a_clean_400_not_a_500(self):
        r = self.client.get(f"/api/repeats/{self.dirname}%00.txt")
        self.assertEqual(r.status_code, 400)

    def test_embedded_null_byte_in_run_id_over_http_is_a_clean_400_not_a_500(self):
        r = self.client.get(f"/api/repeats/{self.dirname}/run/foo%00bar")
        self.assertEqual(r.status_code, 400)


# --------------------------------------------------------------------------- the CLI
class TestCli(unittest.TestCase):
    def setUp(self):
        self.root = make_results_dir()
        self.addCleanup(__import__("shutil").rmtree, self.root, True)

    def test_human_readable_report(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = R.main(["--results", self.root])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("definition:", out)
        self.assertIn("threshold: repeated_commands > 3", out)
        self.assertIn("resolved", out)
        self.assertIn("unresolved", out)

    def test_run_flag_reports_crossing_and_loop(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = R.main(["--results", self.root, "--run", "run-loop"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("run run-loop", out)
        self.assertIn("crossing: call", out)
        self.assertIn("loop onset: calls", out)

    def test_unknown_run_is_an_error(self):
        buf = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stdout(buf):
            rc = R.main(["--results", self.root, "--run", "nope"])
        self.assertEqual(rc, 1)

    def test_json_output_matches_the_finding_sentence_numbers(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            R.main(["--results", self.root, "--json"])
        payload = json.loads(buf.getvalue())
        self.assertIn("summary", payload)
        data = R.study(self.root)
        self.assertEqual(payload["summary"], data["summary"])

    def test_csv_output(self):
        with tempfile.TemporaryDirectory() as td:
            csv_path = os.path.join(td, "out.csv")
            buf = io.StringIO()
            with redirect_stdout(buf):
                R.main(["--results", self.root, "--csv", csv_path])
            self.assertTrue(os.path.exists(csv_path))
            with open(csv_path) as f:
                header = f.readline().strip()
            self.assertEqual(header, "run_id,outcome,i,cumulative,is_repeat")

    def test_module_entrypoint_runs_as_a_subprocess(self):
        """`python -m harnesslab.core.repeats` -- the exact invocation the spec and the docs use."""
        p = subprocess.run([sys.executable, "-m", "harnesslab.core.repeats", "--results", self.root],
                           cwd=LAB, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("definition:", p.stdout)


if __name__ == "__main__":
    unittest.main()
