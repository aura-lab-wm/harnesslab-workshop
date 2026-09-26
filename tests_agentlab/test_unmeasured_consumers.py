"""The consumers of an unmeasured fact: null must not crash, and must not read as zero.

The Cursor adapter writes `null` for what Cursor does not record — usage, cost, tool execution,
the clock. Two reviews of that work found the sweep stopped at the index-row readers. Everything
downstream of a ledger was still written as if `.get(key, 0)` could not return None:

  * `dict.get(k, 0)` returns **None**, not 0, when the key is present with value None. So every
    `s.get("gen_ai.usage.input_tokens", 0) + s.get("gen_ai.usage.output_tokens", 0)` over a chat
    span raises TypeError on a Cursor ledger — the sentinel replay, the risk curve, and the lab's
    own trajectory-test DSL.
  * `x or 0` does not raise, and that is worse: it asserts the run was free. The Inspect AI export,
    the Trajectory v1 note, the fork/twin step timeline and every aggregate in the browser console
    all turned "nobody measured this" into "this cost nothing".
  * And a Cursor file with one row the parser has never seen stopped being detected as Cursor at
    all, falling through to a generic reader with no capability declaration — which writes the
    zeros the whole design exists to prevent.

Every test here fails on the code as the reviews found it, for the reason named in its docstring.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import export, fork, sentinel as S                     # noqa: E402
from harnesslab.backend import importers                                        # noqa: E402
from harnesslab.backend.importers import cursor, trajectory_fmt                 # noqa: E402
from harnesslab.capture import adapters                                         # noqa: E402
from harnesslab.core import reportcard                                          # noqa: E402
from harnesslab.core.trajtest import Trajectory, load_runs                      # noqa: E402

FIXDIR = os.path.join(LAB, "tests_agentlab", "fixtures", "capture")
CURSOR_FIXTURE = os.path.join(FIXDIR, "cursor_transcript.jsonl")
WEB_SRC = os.path.join(LAB, "web", "src")
NODE = shutil.which("node")


def golden_spans(name: str) -> list[dict]:
    with open(os.path.join(FIXDIR, adapters.BY_NAME[name].golden), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class CursorRun(unittest.TestCase):
    """One imported Cursor run, in a temp results dir, exactly as the Import page would write it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hl-unmeasured-consumers-")
        importers.import_path(CURSOR_FIXTURE, "probe", source="cursor", runs_root=cls.tmp)
        cls.dir = os.path.join(cls.tmp, "probe")
        with open(os.path.join(cls.dir, "index.jsonl"), encoding="utf-8") as f:
            cls.rows = [json.loads(line) for line in f if line.strip()]
        cls.summary = cls.rows[0]
        with open(os.path.join(cls.dir, cls.summary["run_id"], "ledger.jsonl"), encoding="utf-8") as f:
            cls.spans = [json.loads(line) for line in f if line.strip()]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_the_fixture_run_really_does_carry_nulls(self):
        """The premise of every other test in this file."""
        self.assertIsNone(self.summary["input_tokens"])
        self.assertIsNone(self.summary["cost_usd"])
        self.assertIsNone(self.summary["tool_calls"])
        chats = [s for s in self.spans if s["span"] == "chat"]
        self.assertTrue(chats)
        for s in chats:
            self.assertIsNone(s["gen_ai.usage.input_tokens"])
            self.assertIsNone(s["cost_usd"])


# ------------------------------------------------------------------ 1. sentinel replay
class TestSentinelReplay(CursorRun):
    def test_replay_scores_a_ledger_whose_usage_is_unknown(self):
        """sentinel.replay summed `.get(k, 0)` over chat spans: TypeError on every Cursor ledger,
        which 500s the run-detail endpoint and — because replay_all has no try/except — the risk
        curve for every other run in the same results directory."""
        S.replay(self.spans, S.load_model())

    def test_replay_runs_over_every_registered_adapters_golden_ledger(self):
        """The guard the fix needs: a new partial source cannot reintroduce this without a red test."""
        model = S.load_model()
        for a in adapters.REGISTRY:
            with self.subTest(adapter=a.name):
                S.replay(golden_spans(a.name), model)


# ------------------------------------------------------------------ 2. the trajectory-test DSL
class TestTrajectoryTotals(CursorRun):
    def traj(self) -> Trajectory:
        return load_runs(self.dir)[0]

    def test_total_tokens_is_unknown_rather_than_a_crash(self):
        """`sum(s.get(in, 0) + s.get(out, 0))` raised TypeError. An unknown total is the honest
        answer, and it is what UNMEASURED_FIELDS already says everywhere else."""
        self.assertIsNone(self.traj().total_tokens)

    def test_cost_usd_is_unknown_rather_than_a_crash(self):
        """`sum(s.get("cost_usd", 0.0))` raised TypeError: int + None."""
        self.assertIsNone(self.traj().cost_usd)

    def test_a_measured_ledger_still_totals(self):
        t = Trajectory("r", "t", "h", "m", {}, [
            {"span": "chat", "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5, "cost_usd": 0.25},
            {"span": "chat", "gen_ai.usage.input_tokens": 20, "gen_ai.usage.output_tokens": 1, "cost_usd": 0.75},
        ])
        self.assertEqual(t.total_tokens, 36)
        self.assertAlmostEqual(t.cost_usd, 1.0)

    def test_one_unknown_span_makes_the_whole_total_unknown(self):
        """A partial sum presented as a total is the failure mode, not a lesser version of it."""
        t = Trajectory("r", "t", "h", "m", {}, [
            {"span": "chat", "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5, "cost_usd": 0.25},
            {"span": "chat", "gen_ai.usage.input_tokens": None, "gen_ai.usage.output_tokens": None, "cost_usd": None},
        ])
        self.assertIsNone(t.total_tokens)
        self.assertIsNone(t.cost_usd)


# ------------------------------------------------------------------ 3. outward artifacts
class TestExportAndFork(CursorRun):
    def test_the_inspect_export_claims_no_usage_rather_than_zero_usage(self):
        """export.py did `int(summary.get("input_tokens") or 0)` and emitted
        ModelUsage(0, 0, 0) — an eval log, read by other tooling, asserting the run was free."""
        self.assertIsNone(export.model_usage_counts(self.summary))

    def test_a_measured_run_still_exports_its_usage(self):
        self.assertEqual(export.model_usage_counts({"input_tokens": 300, "output_tokens": 40}), (300, 40))

    def test_the_trajectory_v1_note_says_unknown_rather_than_zero_plus_zero(self):
        """`tokens={s.get('input_tokens', 0)}+{s.get('output_tokens', 0)}` rendered `tokens=0+0`."""
        note = next(r["content"] for r in export.trajectory_records(self.spans, "<issue>", self.summary)
                    if "invoke_agent end" in str(r.get("content")))
        self.assertNotIn("tokens=0+0", note)
        self.assertIn("tokens=unknown", note)

    def test_the_fork_step_timeline_holds_unknown_spend_open(self):
        """fork.steps_of collapsed a null usage/cost to 0, so the twin view reported a $0.00 run."""
        steps = fork.steps_of(self.spans)
        self.assertTrue(steps)
        for st in steps:
            self.assertIsNone(st["in_tokens"], st)
            self.assertIsNone(st["out_tokens"], st)
            self.assertIsNone(st["cost"], st)

    def test_the_forks_cumulative_state_does_not_report_a_partial_sum_as_a_total(self):
        vec = fork.state_vectors(fork.steps_of(self.spans), budget_tokens=1000, max_steps=20)
        self.assertTrue(vec)
        for v in vec:
            self.assertIsNone(v["tokens"], v)
            self.assertIsNone(v["cost"], v)
            self.assertIsNone(v["token_frac"], v)


# ------------------------------------------------------------------ 4. the report card
class TestReportCard(CursorRun):
    def card(self) -> dict:
        return reportcard.build_card(self.rows, load_runs(self.dir), source=self.dir)

    def test_conduct_claims_nothing_about_a_run_whose_tools_were_never_observed(self):
        """An empty execute_tool list made ran_tests_after_last_edit() False and read_before_write()
        vacuously True, so the headline artifact asserted "0% verified after their last edit" and
        "100% read before write" about a run in which no read, write or test was ever recorded."""
        conduct = self.card()["conduct"]
        for k in ("verified_after_last_edit", "read_before_write"):
            v = conduct[k]
            self.assertTrue(v is None or v != v, f"{k} = {v!r}, but no tool execution was recorded")

    def test_the_markdown_renders_those_two_rates_as_not_available(self):
        md = reportcard.card_markdown(self.card())
        for label in ("verified after their last edit", "read before writing"):
            line = next(l for l in md.splitlines() if label in l.lower())
            self.assertIn("n/a", line, line)

    def test_a_run_with_observed_tools_still_contributes(self):
        rows = list(self.rows) + [dict(self.rows[0], run_id="measured", tool_calls=2)]
        t = Trajectory("measured", rows[0]["task_id"], "h", "m", {"tool_calls": 2}, [
            {"span": "execute_tool", "gen_ai.tool.name": "write_file", "args": {"path": "a"}, "kind": "edit"},
            {"span": "execute_tool", "gen_ai.tool.name": "run_tests", "args": {}, "kind": "run"},
        ])
        conduct = reportcard.build_card(rows, load_runs(self.dir) + [t])["conduct"]
        self.assertEqual(conduct["verified_after_last_edit"], 1.0)
        self.assertEqual(conduct["read_before_write"], 0.0)


# ------------------------------------------------------------------ 5. detection
class TestCursorStaysCursor(unittest.TestCase):
    """A row the parser has never seen is what `unknown_record_types` is for, not a reason to hand
    the file to a generic reader that has no capability declaration and writes zeros."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-detect-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def mixed(self, extra: dict) -> str:
        with open(CURSOR_FIXTURE, encoding="utf-8") as f:
            rows = [line for line in f if line.strip()]
        p = os.path.join(self.tmp, "probe.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(rows[0])
            f.write(json.dumps(extra) + "\n")
            f.writelines(rows[1:])
        return p

    def test_a_clean_cursor_file_is_cursor(self):
        self.assertEqual(importers.detect(CURSOR_FIXTURE), "cursor")

    def test_one_unrecognised_row_does_not_hand_the_file_to_a_generic_reader(self):
        """cursor.sniff dropped to 0.5 while trajectory_fmt scored 0.758 on the very same rows, so
        detect() returned "trajectory" — a source absent from the capture registry, which imports
        with gaps=() and writes steps/tool_calls/tokens/cost all 0."""
        p = self.mixed({"role": "system", "content": "<x>"})
        self.assertGreater(cursor.sniff(p), trajectory_fmt.sniff(p))
        self.assertEqual(importers.detect(p), "cursor")

    def test_the_import_of_that_file_still_declares_what_cursor_cannot_record(self):
        p = self.mixed({"role": "system", "content": "<x>"})
        importers.import_path(p, "probe", runs_root=self.tmp)
        with open(os.path.join(self.tmp, "probe", "index.jsonl"), encoding="utf-8") as f:
            row = json.loads(f.readline())
        self.assertIsNone(row["tool_calls"])
        self.assertIsNone(row["input_tokens"])
        self.assertIsNone(row["cost_usd"])

    def test_a_genuinely_mixed_file_does_not_claim_to_be_cursor(self):
        rows = [{"role": "user", "message": {"content": [{"type": "text", "text": "<p>"}]}}]
        rows += [{"role": "assistant", "content": "<c>"} for _ in range(9)]
        p = os.path.join(self.tmp, "mixed.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        self.assertLess(cursor.sniff(p), 0.78)

    def test_a_real_trajectory_v1_file_is_untouched(self):
        recs = [{"role": "meta", "source": "x"},
                {"role": "user", "content": "<p>", "timestamp": ""},
                {"role": "assistant", "content": "<a>", "timestamp": ""}]
        p = os.path.join(self.tmp, "traj.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in recs)
        self.assertEqual(cursor.sniff(p), 0.0)
        self.assertEqual(importers.detect(p), "trajectory")


# ------------------------------------------------------------------ 6. the browser console
JS_DRIVER = r"""
const fs = require("fs");
const parts = ["core.js", "charts.js", "views_a.js", "views_b.js", "views_c.js"];
const src = parts.map(p => fs.readFileSync(process.argv[2] + "/" + p, "utf8")).join("\n");
const api = new Function(src + `
  return { tokOf, costOf, mean, sum, known, tokensPerSolve, costOfPass, boundaryAny, unmeasured };
`)();
const R = [
  { input_tokens: 1000, output_tokens: 500, cost_usd: 0.50, hidden_pass: true, boundary_events: 0 },
  { input_tokens: null, output_tokens: null, cost_usd: null, hidden_pass: true, boundary_events: null },
];
console.log(JSON.stringify({
  tok_null: api.tokOf(R[1]),
  cost_null: api.costOf(R[1]),
  mean_tokens: api.mean(R.map(api.tokOf)),
  spend: api.sum(R.map(api.costOf)),
  mean_cost: api.mean(R.map(api.costOf)),
  tokens_per_solve: api.tokensPerSolve(R, "hidden_pass"),
  cost_of_pass: api.costOfPass(R, "hidden_pass"),
  boundary_any: api.boundaryAny(R),
  unmeasured_cost: api.unmeasured(R, "cost_usd"),
  unmeasured_tokens: api.unmeasured(R, "input_tokens"),
}));
"""


@unittest.skipUnless(NODE, "node is not installed; the console's own functions cannot be exercised")
@unittest.skipUnless(os.path.isdir(WEB_SRC), f"requires the full checkout: {os.path.relpath(WEB_SRC, LAB)}")
class TestConsoleAggregates(unittest.TestCase):
    """The console recomputes every token and cost aggregate in the browser with `|| 0`, and it is
    the surface those numbers are actually read on. One imported Cursor run in the filter halved
    the reported spend of a whole harness with nothing on screen saying a run was unmeasured."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix="hl-console-")
        driver = os.path.join(tmp, "driver.js")
        with open(driver, "w", encoding="utf-8") as f:
            f.write(JS_DRIVER)
        proc = subprocess.run([NODE, driver, WEB_SRC], capture_output=True, text=True, timeout=60)
        shutil.rmtree(tmp, True)
        if proc.returncode != 0:
            raise AssertionError(f"node exited {proc.returncode}\n{proc.stderr}")
        cls.out = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_a_run_with_no_recorded_usage_has_unknown_tokens_and_cost(self):
        self.assertIsNone(self.out["tok_null"])
        self.assertIsNone(self.out["cost_null"])

    def test_the_mean_is_over_what_was_measured(self):
        """was 750 tokens and $0.25 over two runs; the one measured run spent 1500 and $0.50."""
        self.assertEqual(self.out["mean_tokens"], 1500)
        self.assertAlmostEqual(self.out["mean_cost"], 0.50)

    def test_total_spend_does_not_read_a_null_as_free(self):
        self.assertAlmostEqual(self.out["spend"], 0.50)

    def test_tokens_per_solve_and_cost_of_pass_drop_the_unmeasured_run_from_both_sides(self):
        """was 750 and $0.25: the unmeasured run's solve counted, its spend did not."""
        self.assertEqual(self.out["tokens_per_solve"], 1500)
        self.assertAlmostEqual(self.out["cost_of_pass"], 0.50)

    def test_a_null_boundary_count_is_not_an_assertion_that_none_happened(self):
        """`r.boundary_events > 0` is false for null, so the run silently voted "clean"."""
        self.assertEqual(self.out["boundary_any"], 0)
        self.assertEqual(self.out["unmeasured_cost"], 1)
        self.assertEqual(self.out["unmeasured_tokens"], 1)


class TestNoZeroCoercionLeftInAnyBrowserSurface(unittest.TestCase):
    """A grep guard over both front ends, so the next view added cannot quietly reintroduce it.

    Two shapes of the same bug: `x || 0` says an unknown cost was zero, and `a + b` over the two
    token fields says the same thing more quietly still — `null + null` is 0 in JavaScript."""

    FRONTEND = os.path.join(LAB, "harnesslab", "frontend", "src")

    def sources(self):
        # web/ is the legacy stdlib console: a real developer-repo target, but not part of the
        # school replication package (school-package.json never lists it). Skip candidates that
        # are not on disk here instead of skipping the whole guard, so harnesslab/frontend/src --
        # which DOES ship, and is exactly what participants run -- is still checked for real in
        # the extracted package, not only in a full checkout.
        candidates = [os.path.join(WEB_SRC, name) for name in
                      ("core.js", "charts.js", "views_a.js", "views_b.js", "views_c.js", "app.js")]
        # The BUILT bundle too. web/index.html is what serve.py hands the browser and what the static
        # export embeds, and it is assembled by web/build.py: fixing a source without rebuilding left
        # every one of them absent from the file people actually open.
        candidates.append(os.path.join(LAB, "web", "index.html"))
        for path in candidates:
            if os.path.isfile(path):
                yield path
        for dirpath, dirnames, filenames in os.walk(self.FRONTEND):
            dirnames[:] = [d for d in dirnames if d != "node_modules"]
            for name in sorted(filenames):
                if name.endswith((".js", ".jsx")):
                    yield os.path.join(dirpath, name)

    def test_no_view_coerces_an_unknown_token_or_cost_to_zero(self):
        """Matched against the EXPRESSION, not the line. These files have single lines hundreds of
        characters long holding a dozen expressions, so exempting a whole line because something on
        it checks for null let the next expression along coerce a null to zero unseen."""
        bad = []
        for path in self.sources():
            rel = os.path.relpath(path, LAB)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for i, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith(("*", "//", "#")):
                    continue                      # a comment, often quoting the pattern to warn about it
                for m in re.finditer(r"(cost_usd|input_tokens|output_tokens)\s*\|\|\s*0", line):
                    if not self.guarded(line, m):
                        bad.append(f"{rel}:{i}: {m.group(0)}")
                for m in re.finditer(r"input_tokens\s*\+\s*[A-Za-z_.\[\]\"\']*output_tokens", line):
                    if not self.guarded(line, m):
                        bad.append(f"{rel}:{i}: input_tokens + output_tokens (null + null is 0)")
                # `x ? 1 : 0` is the shape a mean() is built from: a null votes "no", which is the
                # same claim as a zero.
                for m in re.finditer(r"\.(tests_modified|ran_tests_before_submit|boundary_events|"
                                     r"tool_calls|edits|patch_bytes)\s*\?\s*1\s*:\s*0", line):
                    if not self.guarded(line, m):
                        bad.append(f"{rel}:{i}: {m.group(0).strip()} (null votes 0)")
                # Math.min / Math.max coerce a null to 0, inventing a floor nobody measured.
                for m in re.finditer(r"Math\.(?:min|max)\([^)]*\btokOf\b[^)]*\)", line):
                    if "known(" not in m.group(0):
                        bad.append(f"{rel}:{i}: {m.group(0)[:60]} (null becomes 0)")
        self.assertEqual(bad, [])

    @staticmethod
    def guarded(line, m):
        """Whether THIS expression sits inside a null check, rather than merely sharing a line."""
        window = line[max(0, m.start() - 90):m.end() + 40]
        # Number.isFinite(x) is false for null and undefined, so a sum gated on it over the same
        # token fields is guarded exactly as `!= null` would guard it.
        finite = re.search(r"Number\.isFinite\([^)]*(?:input_tokens|output_tokens)", window)
        return "== null" in window or "!= null" in window or bool(finite)


@unittest.skipUnless(os.path.isfile(os.path.join(LAB, "web", "index.html")),
                      "requires the full checkout: web/index.html")
class TestTheShippedBundleIsTheSourcesBuilt(unittest.TestCase):
    """web/index.html is a build artifact served straight to the browser, so a source fixed without a
    rebuild ships nothing. Rebuild it here into a temp file and compare."""

    def test_index_html_is_up_to_date_with_web_src(self):
        import subprocess, tempfile, shutil
        built = os.path.join(LAB, "web", "index.html")
        with open(built, encoding="utf-8") as f:
            before = f.read()
        backup = tempfile.mkdtemp(prefix="hl-bundle-")
        self.addCleanup(shutil.rmtree, backup, True)
        shutil.copy(built, os.path.join(backup, "index.html"))
        try:
            r = subprocess.run([sys.executable, "-B", os.path.join(LAB, "web", "build.py")],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(built, encoding="utf-8") as f:
                after = f.read()
        finally:
            shutil.copy(os.path.join(backup, "index.html"), built)
        self.assertEqual(before, after, "web/index.html is stale: run python3 web/build.py and commit it")


class TestTheConsoleBundleCarriesWhatWasNotMeasured(unittest.TestCase):
    """web/src/core.js decides whether a run's conduct was observed by reading the end span's
    `unmeasured` list. The static bundle is built by console.compact_span, which kept four fields of
    that span -- so in the console every unwatched run looked watched and the abstention never fired."""

    def test_the_end_span_keeps_its_unmeasured_list(self):
        from harnesslab.core import console
        end = {"seq": 9, "span": "invoke_agent", "status": "end", "exit_reason": "no_action",
               "total_tokens": None, "cost_usd": None, "unmeasured": ["tool_calls", "edits"]}
        self.assertEqual(console.compact_span(end)["unmeasured"], ["tool_calls", "edits"])

    def test_a_measured_run_carries_no_such_list(self):
        from harnesslab.core import console
        end = {"seq": 9, "span": "invoke_agent", "status": "end", "exit_reason": "submitted",
               "total_tokens": 10, "cost_usd": 0.1}
        self.assertNotIn("unmeasured", console.compact_span(end))


if __name__ == "__main__":
    unittest.main()
