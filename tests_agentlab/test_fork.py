"""Self-tests for the fork view (harnesslab/backend/fork.py).

Two halves: synthetic ledgers where the difference between the two runs is put there on purpose, so the
divergence indices have a known right answer; and the shipped mock results dirs, where we only assert
properties that must hold for *any* ledger (alignment shape, state-vector monotonicity, pair invariants).
"""
import json
import os
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import fork as F  # noqa: E402

RUNS = os.path.join(LAB, "data", "runs")


# --------------------------------------------------------------------------- synthetic ledger builder
def chat(step, text, tools, tok=(100, 20)):
    return {"span": "chat", "step": step, "text": text, "tool_calls": [{"name": t[0], "arguments": t[1]} for t in tools],
            "gen_ai.usage.input_tokens": tok[0], "gen_ai.usage.output_tokens": tok[1], "cost_usd": 0.001,
            "duration_ms": 10, "gen_ai.response.finish_reasons": ["tool_calls"]}


def tool(name, args, status="ok", tests_passed=None, result=""):
    return {"span": "execute_tool", "gen_ai.tool.name": name, "args": args, "status": status,
            "tests_passed": tests_passed, "result_preview": result}


def ledger(steps, harness=None, seed=7):
    """steps = [(text, [(tool, args, status, tests_passed)], [edit dicts], sentinel|None)]"""
    h = harness or {"id": "h", "max_steps": 20, "max_total_tokens": 1000, "tools": ["bash", "submit"], "sentinel": {}}
    out = [{"span": "invoke_agent", "status": "start", "harness": h, "seed": seed, "repeat_index": 0}]
    for i, (text, calls, edits, sen) in enumerate(steps):
        out.append(chat(i, text, [(c[0], c[1]) for c in calls]))
        if sen:
            out.append({"span": "sentinel", "step": i, **sen})
        for c in calls:
            out.append(tool(c[0], c[1], c[2] if len(c) > 2 else "ok", c[3] if len(c) > 3 else None,
                            c[4] if len(c) > 4 else ""))
        for e in edits:
            out.append({"span": "edit", **e})
    out.append({"span": "invoke_agent", "status": "end", "exit_reason": "submitted"})
    return out


def cmp_(a_steps, b_steps, ha="h", hb="h"):
    sa, sb = F.steps_of(ledger(a_steps)), F.steps_of(ledger(b_steps))
    al = F.align(sa, sb)
    v = F.classify(al, F.first_intervention(sa), F.first_intervention(sb), True, ha, hb)
    return al, v


S_OK = [("look around", [("list_files", {})], [], None),
        ("read it", [("read_file", {"path": "a.py"})], [], None),
        ("fix it", [("write_file", {"path": "a.py", "content": "x = 1\n"})], [{"path": "a.py", "lines_added": 1, "lines_removed": 0}], None),
        ("test it", [("run_tests", {}, "ok", True, "exit=0")], [], None),
        ("done", [("submit", {"summary": "fixed"})], [], None)]


# --------------------------------------------------------------------------- canonicalisation
class TestCanon(unittest.TestCase):
    def test_whitespace_is_ignored(self):
        self.assertEqual(F.canon_args({"content": "a  =\n   1"}), F.canon_args({"content": "a = 1"}))

    def test_key_order_is_ignored(self):
        self.assertEqual(F.canon_args({"a": 1, "b": 2}), F.canon_args({"b": 2, "a": 1}))

    def test_ids_are_masked(self):
        self.assertEqual(F.canon_args({"path": "/tmp/run-3f9a1b2c8d/a.py"}),
                         F.canon_args({"path": "/tmp/run-77771111ffff/a.py"}))
        self.assertEqual(F.canon_args({"log": "20260903-001456-fe6c03"}),
                         F.canon_args({"log": "20260101-235959-aaaaaa"}))

    def test_real_argument_change_still_differs(self):
        self.assertNotEqual(F.canon_args({"path": "a.py"}), F.canon_args({"path": "b.py"}))


# --------------------------------------------------------------------------- divergence semantics
class TestDivergence(unittest.TestCase):
    def test_identical_runs_have_no_fork(self):
        al, v = cmp_(S_OK, S_OK)
        self.assertIsNone(al["tool_divergence"])
        self.assertIsNone(al["effect_divergence"])
        self.assertIsNone(al["text_divergence"])
        self.assertEqual(v["cause"], "identical")
        self.assertIsNone(v["fork_step"])
        self.assertTrue(all(r["same"] for r in al["steps"]))

    def test_divergence_index_is_the_first_differing_step(self):
        b = list(S_OK)
        b[3] = ("test it", [("read_file", {"path": "b.py"})], [], None)
        al, v = cmp_(S_OK, b)
        self.assertEqual(al["tool_divergence"], 3)
        self.assertEqual(v["fork_step"], 3)
        self.assertEqual([r["same"] for r in al["steps"]], [True, True, True, False, True])

    def test_cosmetic_argument_difference_is_not_a_fork(self):
        b = list(S_OK)
        b[2] = ("fix it", [("write_file", {"content": "x  =  1\n", "path": "a.py"})],
                [{"path": "a.py", "lines_added": 1, "lines_removed": 0}], None)
        al, v = cmp_(S_OK, b)
        self.assertIsNone(al["tool_divergence"])
        self.assertEqual(v["cause"], "identical")

    def test_blocked_call_forks_the_effect_before_the_tool_sequence(self):
        b = [(t, [(c[0], c[1], "sentinel_blocked") for c in calls], [], sen) if i == 2 else (t, calls, e, sen)
             for i, (t, calls, e, sen) in enumerate(S_OK)]
        b[2] = (b[2][0], b[2][1], [], {"risk": 0.9, "action": "block", "reason": "Destructive shell command",
                                       "patterns": ["destructive_shell"]})
        b.append(("retry", [("submit", {"summary": "fixed"})], [], None))
        al, v = cmp_(S_OK, b)
        self.assertEqual(al["effect_divergence"], 2)          # the block changed what happened at step 2
        self.assertTrue(all(r["same"] for r in al["steps"][:5]))   # ...but the model asked for the same tools
        self.assertEqual(al["tool_divergence"], 5)            # only the extra retry step differs by tool
        self.assertEqual(v["fork_step"], 2)                   # the fork is the effect, not the tool sequence
        self.assertEqual(v["cause"], "intervention")
        self.assertEqual(v["intervention_step"], 2)

    def test_text_divergence_before_the_action_is_called_sampling(self):
        b = list(S_OK)
        b[1] = ("let me grep instead", [("read_file", {"path": "a.py"})], [], None)
        b[3] = ("test it", [("bash", {"command": "pytest"})], [], None)
        al, v = cmp_(S_OK, b)
        self.assertEqual(al["text_divergence"], 1)
        self.assertEqual(al["tool_divergence"], 3)
        self.assertEqual(v["cause"], "sampling")
        self.assertEqual(v["fork_step"], 3)

    def test_intervention_after_the_fork_does_not_claim_causation(self):
        b = list(S_OK)
        b[1] = ("different plan", [("bash", {"command": "ls"})], [], None)
        b[3] = ("test it", [("run_tests", {}, "ok", True, "exit=0")], [],
                {"risk": 0.9, "action": "nudge", "reason": "late", "patterns": []})
        al, v = cmp_(S_OK, b)
        self.assertEqual(v["fork_step"], 1)
        self.assertNotEqual(v["cause"], "intervention")

    def test_different_base_harness_is_reported_as_a_harness_fork(self):
        b = list(S_OK)
        b[3] = ("test it", [("bash", {"command": "pytest"})], [], None)
        al, v = cmp_(S_OK, b, ha="baseline", hb="no_test_tool")
        self.assertEqual(v["cause"], "harness")

    def test_alignment_covers_the_longer_run(self):
        al, _ = cmp_(S_OK, S_OK[:3])
        self.assertEqual(al["n"], len(S_OK))
        self.assertEqual(len(al["steps"]), len(S_OK))
        self.assertIsNone(al["steps"][4]["b"])
        self.assertFalse(al["steps"][3]["same"])
        self.assertEqual(al["tool_divergence"], 3)


# --------------------------------------------------------------------------- state vectors
def assert_monotone(case, state, keys=("edits", "lines_added", "lines_removed", "tests_run", "tokens",
                                       "boundary_events", "blocked_calls", "interventions")):
    for k in keys:
        vals = [s[k] for s in state]
        case.assertEqual(vals, sorted(vals), f"{k} is not non-decreasing: {vals}")
    files = [len(s["files_touched"]) for s in state]
    case.assertEqual(files, sorted(files), "files_touched shrank")
    costs = [s["cost"] for s in state]
    case.assertEqual(costs, sorted(costs), "cost decreased")
    subs = [1 if s["submitted"] else 0 for s in state]
    case.assertEqual(subs, sorted(subs), "submitted flipped back to false")
    for s in state:
        case.assertIn(s["last_test"], ("pass", "fail", "unknown"))
        case.assertEqual(s["files_touched"], list(dict.fromkeys(s["files_touched"])), "duplicate file")


class TestStateVectors(unittest.TestCase):
    def test_synthetic_state_is_monotone_and_correct(self):
        st = F.state_vectors(F.steps_of(ledger(S_OK)), budget_tokens=1000, max_steps=20)
        assert_monotone(self, st)
        self.assertEqual(len(st), 5)
        self.assertEqual(st[-1]["edits"], 1)
        self.assertEqual(st[-1]["files_touched"], ["a.py"])
        self.assertEqual(st[-1]["tests_run"], 1)
        self.assertEqual(st[-1]["last_test"], "pass")
        self.assertTrue(st[-1]["submitted"])
        self.assertFalse(st[0]["submitted"])
        self.assertEqual(st[2]["step_frac"], 3 / 20)

    def test_blocked_tools_do_not_count_as_work(self):
        b = [("go", [("run_tests", {}, "sentinel_blocked")], [], None),
             ("go", [("submit", {}, "sentinel_blocked")], [], None)]
        st = F.state_vectors(F.steps_of(ledger(b)))
        self.assertEqual(st[-1]["tests_run"], 0)
        self.assertEqual(st[-1]["blocked_calls"], 2)
        self.assertFalse(st[-1]["submitted"])

    def test_last_test_tracks_the_most_recent_result(self):
        b = [("t", [("run_tests", {}, "ok", False, "exit=1")], [], None),
             ("t", [("run_tests", {}, "ok", True, "exit=0")], [], None)]
        st = F.state_vectors(F.steps_of(ledger(b)))
        self.assertEqual([s["last_test"] for s in st], ["fail", "pass"])

    def test_bash_test_commands_count_as_tests(self):
        b = [("t", [("bash", {"command": "python -m pytest -q"}, "ok", None, "exit=0")], [], None)]
        st = F.state_vectors(F.steps_of(ledger(b)))
        self.assertEqual(st[-1]["tests_run"], 1)
        self.assertEqual(st[-1]["last_test"], "pass")


# --------------------------------------------------------------------------- shipped results dirs
def have(dirname):
    return os.path.exists(os.path.join(RUNS, dirname, "index.jsonl"))


class TestOnShippedRuns(unittest.TestCase):
    def _dirs(self):
        return [d for d in ("demo_mock", "prerecorded_mock") if have(d)]

    def test_state_endpoint_is_monotone_on_real_ledgers(self):
        for name in self._dirs():
            d = os.path.join(RUNS, name)
            rows = F.load_index(d)[:12]
            for r in rows:
                out = F.state(name, r["run_id"])
                assert_monotone(self, out["state"])
                self.assertEqual(len(out["state"]), len(out["steps"]))
                self.assertEqual([s["step"] for s in out["state"]], sorted(s["step"] for s in out["state"]))
                if r.get("edits") is not None:
                    self.assertEqual(out["state"][-1]["edits"], r["edits"])
                if r.get("files_touched"):
                    self.assertEqual(sorted(out["state"][-1]["files_touched"]), sorted(r["files_touched"]))

    def test_two_runs_of_the_same_task_align(self):
        name = self._dirs()[0]
        rows = F.load_index(os.path.join(RUNS, name))
        by_task = {}
        for r in rows:
            by_task.setdefault(r["task_id"], []).append(r)
        pair = next(v for v in by_task.values() if len(v) >= 2)
        out = F.compare(name, pair[0]["run_id"], pair[1]["run_id"])
        self.assertEqual(out["n_steps"], len(out["steps"]))
        self.assertEqual(out["n_steps"], max(len(out["state"]["a"]), len(out["state"]["b"])))
        self.assertEqual([s["step"] for s in out["steps"]], list(range(out["n_steps"])))
        for s in out["steps"]:
            self.assertTrue(s["a"] is not None or s["b"] is not None)
            if s["a"] is None or s["b"] is None:
                self.assertFalse(s["same"])
        d = out["divergence"]
        # the tool-level fork can never be earlier than the effect-level one
        if d["tool"] is not None and d["effect"] is not None:
            self.assertLessEqual(d["effect"], d["tool"])
        if d["tool"] is None:
            self.assertTrue(all(s["same"] for s in out["steps"]))
        else:
            self.assertTrue(all(s["same"] for s in out["steps"][: d["tool"]]))
            self.assertFalse(out["steps"][d["tool"]]["same"])

    @unittest.skipUnless(have("demo_mock"), "demo_mock not present")
    def test_demo_mock_twins_are_seed_paired(self):
        p = F.pairs("demo_mock")
        self.assertGreater(p["counts"]["twin"], 0)
        for t in p["pairs"]["twin"]:
            self.assertTrue(t["seed_matched"], t)
            self.assertEqual(t["seed_a"], t["seed_b"])
            self.assertEqual(F.base_harness(t["harness_a"]), F.base_harness(t["harness_b"]))
            self.assertNotEqual(t["harness_a"].endswith("+sentinel"), t["harness_b"].endswith("+sentinel"))
            self.assertEqual(t["kind"], "twin")

    @unittest.skipUnless(have("demo_mock"), "demo_mock not present")
    def test_a_blocked_twin_forks_at_the_intervention(self):
        """The one demo_mock pair where the hook actually fired: same seed, same tools requested, but the
        destructive `rm -rf` is blocked — the effect forks at the intervention step, the tool sequence later."""
        p = F.pairs("demo_mock")
        blocked = [t for t in p["pairs"]["twin"] if (t["interventions_a"] or t["interventions_b"])]
        self.assertTrue(blocked, "expected at least one twin pair with an intervention")
        for t in blocked:
            out = F.compare("demo_mock", t["a"], t["b"])
            self.assertEqual(out["verdict"]["cause"], "intervention")
            iv = out["verdict"]["intervention_step"]
            self.assertLessEqual(iv, out["verdict"]["fork_step"])
            self.assertEqual(out["divergence"]["effect"], iv)
            self.assertTrue(out["pair"]["twin"])
            self.assertTrue(out["pair"]["seed_matched"])
            self.assertTrue(out["pair"]["deterministic_provider"])

    @unittest.skipUnless(have("demo_mock"), "demo_mock not present")
    def test_pairs_kinds_are_what_they_claim(self):
        p = F.pairs("demo_mock")
        for r in p["pairs"]["repeat"]:
            self.assertEqual(r["harness_a"], r["harness_b"])
            self.assertEqual(r["model"], r["model"])
        for r in p["pairs"]["harness"]:
            self.assertNotEqual(F.base_harness(r["harness_a"]), F.base_harness(r["harness_b"]))
        for k in ("twin", "repeat", "harness"):
            for r in p["pairs"][k]:
                self.assertNotEqual(r["a"], r["b"])

    def test_compare_rejects_a_run_against_itself(self):
        name = self._dirs()[0]
        rid = F.load_index(os.path.join(RUNS, name))[0]["run_id"]
        with self.assertRaises(Exception):
            F.compare(name, rid, rid)


# --------------------------------------------------------------------------- wiring
class TestRouterWiring(unittest.TestCase):
    """app.py registers the SPA catch-all `GET /{path:path}` before anything appended at the end of the
    file, and Starlette matches in registration order — so an appended router is dead once the frontend
    has been built unless the catch-all is pushed back behind it."""

    def _client(self, app):
        try:
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover - httpx/fastapi are platform requirements
            self.skipTest("fastapi TestClient not available")
        return TestClient(app)

    def _app_with_catch_all(self):
        try:
            from fastapi import FastAPI
        except ImportError:  # pragma: no cover
            self.skipTest("fastapi not installed")
        a = FastAPI()

        @a.get("/{path:path}")
        def spa(path: str):
            return {"spa": path}

        a.include_router(F.router)
        return a

    def test_catch_all_shadows_an_appended_router(self):
        c = self._client(self._app_with_catch_all())
        self.assertIn("spa", c.get("/api/fork/demo_mock/pairs").json())

    def test_hoist_makes_the_appended_router_reachable(self):
        a = self._app_with_catch_all()
        F.hoist_api_routes(a)
        c = self._client(a)
        body = c.get("/api/fork/demo_mock/pairs").json()
        self.assertNotIn("spa", body)
        self.assertEqual(body["dir"], "demo_mock")
        self.assertIn("counts", body)
        self.assertEqual(c.get("/anything/else").json(), {"spa": "anything/else"})   # the SPA still works

    def test_hoist_is_idempotent_and_safe_without_a_catch_all(self):
        try:
            from fastapi import FastAPI
        except ImportError:  # pragma: no cover
            self.skipTest("fastapi not installed")
        a = FastAPI()
        a.include_router(F.router)
        before = [id(r) for r in a.router.routes]
        F.hoist_api_routes(a)
        F.hoist_api_routes(a)
        self.assertEqual(before, [id(r) for r in a.router.routes])
        self.assertEqual(self._client(a).get("/api/fork/demo_mock/pairs").json()["dir"], "demo_mock")

    def test_the_platform_app_serves_the_fork_routes(self):
        try:
            from harnesslab.backend.app import app
        except ImportError:  # pragma: no cover
            self.skipTest("platform app not importable")
        c = self._client(app)
        r = c.get("/api/fork/demo_mock/pairs")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["dir"], "demo_mock")
        self.assertEqual(c.get("/api/fork/demo_mock/state/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
