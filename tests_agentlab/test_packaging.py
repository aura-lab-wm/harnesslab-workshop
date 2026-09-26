"""Self-tests for the packaging stream: lab-root resolution, the SQLite run index, and the exports.

Nothing here needs a network or a live server. `inspect_ai` is optional: the round-trip test skips
itself when the extra is not installed.
"""
import csv
import sqlite3
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.core.analysis import filter_rows, load_index          # noqa: E402
from harnesslab.backend import export as E                      # noqa: E402
from harnesslab.backend import metrics as M                     # noqa: E402
from harnesslab.backend import paths as P                       # noqa: E402
from harnesslab.backend import store as ST                      # noqa: E402

DEMO, PRE = "demo_mock", "prerecorded_mock"


def _has(name):
    return os.path.exists(os.path.join(M.RUNS_ROOT, name, "index.jsonl"))


# --------------------------------------------------------------------------- 1. paths
class TestPaths(unittest.TestCase):
    def test_checkout_is_detected(self):
        self.assertEqual(os.path.realpath(P.LAB_ROOT), os.path.realpath(LAB))
        self.assertTrue(os.path.isdir(P.HARNESS_DIR))
        self.assertTrue(os.path.isdir(P.TASK_DIR))

    def test_env_override_wins_over_checkout(self):
        with tempfile.TemporaryDirectory() as t:
            root = P.resolve_lab_root(env={"HARNESSLAB_LAB": t}, start=LAB)
            self.assertEqual(os.path.realpath(root), os.path.realpath(t))
            # a non-checkout override is seeded so live runs have somewhere to land
            self.assertTrue(os.path.isdir(os.path.join(t, "data", "runs")))

    def test_env_override_expands_user_and_relative(self):
        with tempfile.TemporaryDirectory() as t:
            sub = os.path.join(t, "nested", "lab")
            root = P.resolve_lab_root(env={"HARNESSLAB_LAB": sub}, start=LAB)
            self.assertTrue(os.path.isabs(root))
            self.assertTrue(os.path.isdir(root))

    def test_falls_back_to_workspace_when_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as t:
            notlab = os.path.join(t, "site-packages")
            ws = os.path.join(t, "workspace")
            os.makedirs(notlab)
            root = P.resolve_lab_root(env={}, start=notlab, workspace=ws)
            self.assertEqual(os.path.realpath(root), os.path.realpath(ws))
            self.assertTrue(os.path.isdir(os.path.join(ws, "data", "runs")))

    def test_dry_resolution_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            ws = os.path.join(t, "workspace")
            P.resolve_lab_root(env={}, start=os.path.join(t, "nope"), workspace=ws, seed=False)
            self.assertFalse(os.path.exists(ws))

    def test_ensure_workspace_is_idempotent_and_never_clobbers(self):
        with tempfile.TemporaryDirectory() as t:
            P.ensure_workspace(t)
            marker = os.path.join(t, "harnesses", "mine.json")
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w") as f:
                f.write("{}")
            P.ensure_workspace(t)
            self.assertTrue(os.path.exists(marker))

    def test_describe_reports_the_source(self):
        d = P.describe()
        self.assertIn(d["source"], {"env HARNESSLAB_LAB", "checkout", "workspace"})
        for k in ("lab_root", "runs_root", "harnesses", "tasks", "dist", "index_db"):
            self.assertTrue(d[k], k)


class TestCli(unittest.TestCase):
    """`harnesslab --help` / `--paths` must work without starting a server."""

    def _run(self, args, env=None):
        e = dict(os.environ, PYTHONPATH=LAB)
        e.update(env or {})
        return subprocess.run([sys.executable, "-m", "harnesslab", *args], capture_output=True,
                              text=True, cwd=LAB, env=e, timeout=120)

    def test_help(self):
        r = self._run(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        for flag in ("--host", "--port", "--lab", "--no-browser"):
            self.assertIn(flag, r.stdout)

    def test_paths_json_follows_lab_flag(self):
        with tempfile.TemporaryDirectory() as t:
            r = self._run(["--paths", "--lab", t])
            self.assertEqual(r.returncode, 0, r.stderr)
            d = json.loads(r.stdout)
            self.assertEqual(os.path.realpath(d["lab_root"]), os.path.realpath(t))
            self.assertEqual(d["source"], "env HARNESSLAB_LAB")


# --------------------------------------------------------------------------- 2. store parity
class TestStoreParity(unittest.TestCase):
    """The SQLite index must be indistinguishable from load_index + filter_rows."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "idx.sqlite")

    def tearDown(self):
        ST.close_all()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _parity(self, name):
        d = os.path.join(M.RUNS_ROOT, name)
        base = load_index(d)
        self.assertEqual(ST.rows(name, db_path=self.db), filter_rows(base))
        harnesses = sorted({r["harness_id"] for r in base})
        tasks = sorted({r["task_id"] for r in base})
        models = sorted({r["model"] for r in base})
        for h in harnesses:
            self.assertEqual(ST.rows(name, harness_id=h, db_path=self.db),
                             filter_rows(base, harness_id=h), f"{name} harness={h}")
        for t in tasks[:3]:
            self.assertEqual(ST.rows(name, task_id=t, db_path=self.db),
                             filter_rows(base, task_id=t), f"{name} task={t}")
        self.assertEqual(ST.rows(name, harness_id=harnesses[0], task_id=tasks[0], model=models[0], db_path=self.db),
                         filter_rows(base, harness_id=harnesses[0], task_id=tasks[0], model=models[0]))
        self.assertEqual(ST.run_ids(name, db_path=self.db), [r["run_id"] for r in base])

    def test_parity_demo_mock(self):
        self._parity(DEMO)

    @unittest.skipUnless(_has(PRE), "prerecorded_mock not present")
    def test_parity_prerecorded_mock(self):
        self._parity(PRE)

    def test_unknown_filter_value_returns_nothing(self):
        self.assertEqual(ST.rows(DEMO, harness_id="no_such_harness", db_path=self.db), [])

    def test_overview_matches_results_dirs(self):
        self.assertEqual(ST.overview(db_path=self.db), M.results_dirs())

    def test_per_run_facts_are_recorded(self):
        rid = ST.run_ids(DEMO, db_path=self.db)[0]
        f = ST.facts(DEMO, rid, db_path=self.db)
        self.assertEqual(f["has_run_dir"], 1)
        self.assertEqual(f["has_ledger"], 1)
        self.assertGreater(f["ledger_bytes"], 0)
        self.assertIsInstance(f["sum_files"], int)


class TestStoreIncremental(unittest.TestCase):
    """Re-ingest on mtime change; survive deletion and corruption of the cache."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.runs = os.path.join(self.tmp, "runs")
        os.makedirs(os.path.join(self.runs, "d1"))
        self.idx = os.path.join(self.runs, "d1", "index.jsonl")
        self.db = os.path.join(self.tmp, "idx.sqlite")
        self._write(2)

    def tearDown(self):
        ST.close_all()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, n, harness="baseline"):
        with open(self.idx, "w") as f:
            for i in range(n):
                f.write(json.dumps({
                    "run_id": f"r{i}", "task_id": f"t{i % 2}", "harness_id": harness, "model": "mock",
                    "steps": i, "input_tokens": 10, "output_tokens": 1, "cost_usd": 0.001,
                    "exit_reason": "submitted", "hidden_pass": i % 2 == 0, "strong_pass": False}) + "\n")
        # make the mtime unambiguously different from the previous write
        st = os.stat(self.idx)
        os.utime(self.idx, (st.st_atime, st.st_mtime + 10 + n))

    def test_picks_up_appended_rows(self):
        self.assertEqual(len(ST.rows("d1", runs_root=self.runs, db_path=self.db)), 2)
        self._write(5)
        self.assertEqual(len(ST.rows("d1", runs_root=self.runs, db_path=self.db)), 5)

    def test_second_read_does_not_reingest(self):
        ST.rows("d1", runs_root=self.runs, db_path=self.db)
        self.assertEqual(ST.refresh("d1", runs_root=self.runs, db_path=self.db), {})

    def test_rewritten_rows_replace_not_duplicate(self):
        ST.rows("d1", runs_root=self.runs, db_path=self.db)
        self._write(2, harness="permissive")
        rows = ST.rows("d1", runs_root=self.runs, db_path=self.db)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["harness_id"] for r in rows}, {"permissive"})
        self.assertEqual(ST.rows("d1", harness_id="baseline", runs_root=self.runs, db_path=self.db), [])

    def test_vanished_directory_is_dropped(self):
        ST.overview(runs_root=self.runs, db_path=self.db)
        shutil.rmtree(os.path.join(self.runs, "d1"))
        self.assertEqual(ST.overview(runs_root=self.runs, db_path=self.db), [])

    def test_deleting_the_database_is_safe(self):
        ST.rows("d1", runs_root=self.runs, db_path=self.db)
        ST.close_all()
        for s in ("", "-wal", "-shm"):
            if os.path.exists(self.db + s):
                os.remove(self.db + s)
        self.assertEqual(len(ST.rows("d1", runs_root=self.runs, db_path=self.db)), 2)

    def test_corrupt_database_is_rebuilt(self):
        ST.rows("d1", runs_root=self.runs, db_path=self.db)
        ST.close_all()
        with open(self.db, "wb") as f:
            f.write(b"this is not a database, it is a picture of one")
        self.assertEqual(len(ST.rows("d1", runs_root=self.runs, db_path=self.db)), 2)

    def test_half_written_line_is_skipped(self):
        with open(self.idx, "a") as f:
            f.write('{"run_id": "partial", "task')            # a live run mid-append
        st = os.stat(self.idx)
        os.utime(self.idx, (st.st_atime, st.st_mtime + 100))
        self.assertEqual(len(ST.rows("d1", runs_root=self.runs, db_path=self.db)), 2)


# --------------------------------------------------------------------------- 3. Trajectory export
#: The record grammar of Trajectory v1, transcribed from schema/trajectory-v1.schema.json
#: ($id https://letta.ai/schemas/trajectory/v1.json) in github.com/letta-ai/trajectory.
#: Every record type has additionalProperties: false, hence the exact key sets below.
TRAJ_REQUIRED = {
    "meta": {"role", "source"},
    "system": {"role", "content", "timestamp"},
    "user": {"role", "content", "timestamp"},
    "reasoning": {"role", "content", "timestamp"},
    "observation": {"role", "content", "timestamp"},
    "assistant": {"role", "content", "timestamp"},
    "tool": {"role", "tool_call_id", "content", "timestamp"},
}
TRAJ_ALLOWED = {
    "meta": {"role", "source", "cwd", "git_branch", "model"},
    "system": {"role", "content", "timestamp"},
    "user": {"role", "content", "timestamp"},
    "reasoning": {"role", "content", "timestamp"},
    "observation": {"role", "content", "timestamp"},
    "assistant": {"role", "content", "timestamp", "tool_calls"},
    "tool": {"role", "tool_call_id", "content", "ok", "timestamp"},
}
TS_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$"


class TestTrajectoryExport(unittest.TestCase):
    def _records(self, name, run_id):
        spans = E._spans(name, run_id)
        summary = E._summary(name, run_id)
        return E.trajectory_records(spans, E._issue_for(summary.get("task_id", "")), summary,
                                    E._run_dir(name, run_id))

    def _validate(self, recs):
        import re
        self.assertTrue(recs)
        self.assertEqual(recs[0]["role"], "meta", "the meta record must lead")
        self.assertEqual(recs[0]["source"], "harnesslab")
        seen_meta = 0
        for i, r in enumerate(recs):
            role = r.get("role")
            self.assertIn(role, TRAJ_REQUIRED, f"record {i}: unknown role {role!r}")
            self.assertTrue(TRAJ_REQUIRED[role] <= set(r), f"record {i} ({role}): missing keys")
            self.assertTrue(set(r) <= TRAJ_ALLOWED[role],
                            f"record {i} ({role}): extra keys {set(r) - TRAJ_ALLOWED[role]}")
            if role == "meta":
                seen_meta += 1
                continue
            self.assertRegex(r["timestamp"], TS_RE, f"record {i} ({role}): bad timestamp")
            if role == "assistant":
                if "tool_calls" in r:
                    self.assertIsNone(r["content"], "assistant with tool_calls must have null content")
                    self.assertTrue(r["tool_calls"], "tool_calls must be non-empty when present")
                    for c in r["tool_calls"]:
                        self.assertEqual(set(c), {"id", "name", "args"})
                        self.assertIsInstance(c["args"], str, "args must be stringified JSON")
                        json.loads(c["args"])
                        self.assertTrue(c["id"] and c["name"])
                else:
                    self.assertIsInstance(r["content"], str)
                    self.assertTrue(len(r["content"]) >= 1)
            elif role == "tool":
                self.assertIsInstance(r["content"], str)
                if "ok" in r:
                    self.assertIsInstance(r["ok"], bool)
            else:
                self.assertIsInstance(r["content"], str)
        self.assertEqual(seen_meta, 1, "exactly one meta record")

    def test_demo_mock_run_is_schema_shaped(self):
        rid = ST.run_ids(DEMO)[0]
        self._validate(self._records(DEMO, rid))

    @unittest.skipUnless(_has(PRE), "prerecorded_mock not present")
    def test_prerecorded_runs_are_schema_shaped(self):
        for rid in ST.run_ids(PRE)[:5]:
            self._validate(self._records(PRE, rid))

    @unittest.skipUnless(_has("real_swe_agent_500"), "real trajectories not present")
    def test_imported_runs_without_timestamps_are_schema_shaped(self):
        rid = ST.run_ids("real_swe_agent_500")[0]
        recs = self._records("real_swe_agent_500", rid)
        self._validate(recs)
        self.assertTrue(any("synthesized" in r.get("content", "") for r in recs if r["role"] == "observation"),
                        "a run with no wall-clock timestamps must say so")

    def test_tool_results_link_back_to_their_call(self):
        recs = self._records(DEMO, ST.run_ids(DEMO)[0])
        ids = {c["id"] for r in recs if r["role"] == "assistant" for c in r.get("tool_calls", [])}
        used = [r["tool_call_id"] for r in recs if r["role"] == "tool"]
        self.assertTrue(used, "the demo run should have tool results")
        self.assertTrue(set(used) <= ids, "every tool result must name a tool call that was made")
        self.assertEqual(len(used), len(set(used)), "one result per call")

    def test_system_prompt_and_issue_are_carried(self):
        recs = self._records(DEMO, ST.run_ids(DEMO)[0])
        roles = [r["role"] for r in recs]
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        self.assertLess(roles.index("system"), roles.index("user"))


# --------------------------------------------------------------------------- 4. Inspect AI export
try:
    import inspect_ai  # noqa: F401
    HAS_INSPECT = True
except ImportError:
    HAS_INSPECT = False


@unittest.skipUnless(HAS_INSPECT, "inspect_ai is an optional extra: pip install 'harnesslab[inspect]'")
class TestInspectExport(unittest.TestCase):
    def _roundtrip(self, name, run_id):
        from inspect_ai.log import read_eval_log, write_eval_log
        log = E._inspect_log(name, run_id)
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, f"{run_id}.json")
            write_eval_log(log, p)
            return read_eval_log(p)

    def test_round_trips_through_read_eval_log(self):
        rid = ST.run_ids(DEMO)[0]
        row = next(r for r in ST.rows(DEMO) if r["run_id"] == rid)
        back = self._roundtrip(DEMO, rid)
        self.assertEqual(back.status, "success")
        self.assertEqual(len(back.samples), 1)
        s = back.samples[0]
        self.assertEqual(s.id, rid)
        self.assertEqual(s.epoch, row["repeat_index"] + 1)
        self.assertEqual(s.scores["hidden_pass"].value, 1.0 if row["hidden_pass"] else 0.0)
        usage = s.model_usage[row["model"]]
        self.assertEqual(usage.input_tokens, row["input_tokens"])
        self.assertEqual(usage.output_tokens, row["output_tokens"])
        self.assertEqual(s.metadata["harnesslab"]["harness_id"], row["harness_id"])

    def test_messages_survive_with_their_tool_calls(self):
        rid = ST.run_ids(DEMO)[0]
        s = self._roundtrip(DEMO, rid).samples[0]
        kinds = [type(m).__name__ for m in s.messages]
        self.assertIn("ChatMessageSystem", kinds)
        self.assertIn("ChatMessageUser", kinds)
        self.assertIn("ChatMessageAssistant", kinds)
        self.assertIn("ChatMessageTool", kinds)
        call_ids = {c.id for m in s.messages if type(m).__name__ == "ChatMessageAssistant"
                    for c in (m.tool_calls or [])}
        result_ids = {m.tool_call_id for m in s.messages if type(m).__name__ == "ChatMessageTool"}
        self.assertTrue(result_ids)
        self.assertTrue(result_ids <= call_ids)

    @unittest.skipUnless(_has("real_swe_agent_500"), "real trajectories not present")
    def test_imported_run_round_trips(self):
        rid = ST.run_ids("real_swe_agent_500")[0]
        back = self._roundtrip("real_swe_agent_500", rid)
        self.assertEqual(back.samples[0].id, rid)
        self.assertIn("hidden_pass", back.samples[0].scores)

    def test_endpoint_bytes_are_readable(self):
        from inspect_ai.log import read_eval_log
        from inspect_ai.log._file import eval_log_json
        rid = ST.run_ids(DEMO)[0]
        body = eval_log_json(E._inspect_log(DEMO, rid))
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "x.json")
            with open(p, "wb") as f:
                f.write(body if isinstance(body, bytes) else body.encode())
            self.assertEqual(read_eval_log(p).samples[0].id, rid)


# --------------------------------------------------------------------------- 5. CSV / zip
class TestCsvExports(unittest.TestCase):
    def test_index_csv_row_and_column_count(self):
        rows = ST.rows(DEMO)
        text = E.index_csv(rows)
        parsed = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(parsed), len(rows))
        self.assertEqual(parsed[0]["run_id"], rows[0]["run_id"])
        self.assertIn("hidden_pass", parsed[0])
        self.assertIn(parsed[0]["hidden_pass"], {"0", "1"})
        # list-valued columns are joined, never left as a Python repr
        self.assertNotIn("[", parsed[0]["files_touched"])

    def test_index_csv_is_empty_for_no_rows(self):
        self.assertEqual(E.index_csv([]), "")

    def test_cells_csv_carries_cis(self):
        cells = M.cells(ST.rows(DEMO))
        parsed = list(csv.DictReader(io.StringIO(E.cells_csv(cells))))
        self.assertEqual(len(parsed), len(cells))
        for row, cell in zip(parsed, cells):
            self.assertEqual(row["harness"], cell["harness"])
            self.assertEqual(row["model"], cell["model"])
            lo, hi, p = float(row["pass1_ci95_lo"]), float(row["pass1_ci95_hi"]), float(row["pass1"])
            self.assertLessEqual(lo, p + 1e-9)
            self.assertGreaterEqual(hi, p - 1e-9)
            self.assertAlmostEqual(p, cell["pass@1"], places=12)

    def test_cells_csv_never_writes_nan_or_inf(self):
        text = E.cells_csv(M.cells(ST.rows(DEMO)))
        for bad in ("nan", "inf", "NaN", "Infinity"):
            self.assertNotIn(bad, text)


class TestBundle(unittest.TestCase):
    def test_bundle_contents(self):
        data = E._bundle_bytes(DEMO)
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIsNone(z.testzip())
        names = z.namelist()
        self.assertEqual(len(names), len(set(names)), "no duplicate entries")
        for expected in (f"{DEMO}/index.jsonl", f"{DEMO}/index.csv", f"{DEMO}/cells.csv",
                         f"{DEMO}/MANIFEST.json", f"{DEMO}/README.txt"):
            self.assertIn(expected, names)
        rows = ST.rows(DEMO)
        self.assertEqual(len([n for n in names if n.endswith("/ledger.jsonl")]), len(rows))
        self.assertTrue(any(n.startswith(f"{DEMO}/harnesses/") for n in names))
        # index.jsonl inside the zip must parse back to exactly the rows on disk
        inner = [json.loads(l) for l in z.read(f"{DEMO}/index.jsonl").decode().splitlines() if l.strip()]
        self.assertEqual(inner, rows)

    def test_manifest_describes_every_file(self):
        z = zipfile.ZipFile(io.BytesIO(E._bundle_bytes(DEMO)))
        man = json.loads(z.read(f"{DEMO}/MANIFEST.json"))
        self.assertEqual(man["schema"], "harnesslab.bundle/1")
        self.assertEqual(man["runs"], len(ST.rows(DEMO)))
        listed = {f["path"] for f in man["files"]}
        self.assertEqual(listed, set(z.namelist()) - {f"{DEMO}/MANIFEST.json"})
        for f in man["files"]:
            self.assertEqual(f["bytes"], len(z.read(f["path"])))

    def test_limit_and_toggles_shrink_the_bundle(self):
        small = zipfile.ZipFile(io.BytesIO(E._bundle_bytes(DEMO, include_ledgers=False,
                                                           include_patches=False, limit=2)))
        self.assertEqual(len([n for n in small.namelist() if n.endswith("/summary.json")]), 2)
        self.assertEqual([n for n in small.namelist() if n.endswith("/ledger.jsonl")], [])


# --------------------------------------------------------------------------- 6. HTTP surface
class TestEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
        except ImportError as e:                                      # pragma: no cover
            raise unittest.SkipTest(f"fastapi TestClient unavailable: {e}")
        from harnesslab.backend.app import app
        cls.client = TestClient(app)
        cls.rid = ST.run_ids(DEMO)[0]

    def test_formats_lists_what_is_available(self):
        body = self.client.get("/api/export/formats").json()
        ids = {f["id"] for f in body["formats"]}
        self.assertEqual(ids, {"trajectory", "inspect", "index_csv", "cells_csv", "bundle"})
        self.assertEqual({f["id"] for f in body["formats"] if f["available"]} >= {"trajectory", "bundle"}, True)

    def test_every_export_endpoint_responds(self):
        cases = [
            (f"/api/export/{DEMO}/runs/{self.rid}.trajectory.jsonl", "application/x-ndjson"),
            (f"/api/export/{DEMO}/index.csv", "text/csv"),
            (f"/api/export/{DEMO}/cells.csv", "text/csv"),
            (f"/api/export/{DEMO}/bundle.zip", "application/zip"),
        ]
        for url, ctype in cases:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn(ctype, r.headers["content-type"], url)
            self.assertIn("attachment", r.headers.get("content-disposition", ""), url)
            self.assertGreater(len(r.content), 0, url)

    def test_every_format_the_server_offers_downloads_as_a_file(self):
        """Driven by formats(), not by a list here.

        The export screen tells the reader that a download button saves a file and leaves the
        study on screen, and it builds every button from a path template formats() handed it.
        That promise holds only if EVERY offered shape sets Content-Disposition -- a new format
        added to formats() without one would break it silently, because the case list above is
        written by hand and a new id is not in it.
        """
        body = self.client.get("/api/export/formats").json()
        offered = [f for f in body["formats"] if f.get("available", True)]
        self.assertGreater(len(offered), 0)
        for f in offered:
            url = f["path"].replace("{dir}", DEMO).replace("{run_id}", self.rid)
            self.assertNotIn("{", url, f"{f['id']}: the path has a hole the test cannot fill")
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn("attachment", r.headers.get("content-disposition", ""),
                          f"{f['id']} is offered on screen as a download but does not send one")
            if f.get("media_type"):
                self.assertIn(f["media_type"], r.headers["content-type"], url)

    def test_run_id_is_not_swallowed_by_the_suffix(self):
        r = self.client.get(f"/api/export/{DEMO}/runs/{self.rid}.trajectory.jsonl")
        first = json.loads(r.text.splitlines()[0])
        self.assertEqual(first["role"], "meta")
        self.assertEqual(r.headers["x-trajectory-schema"], E.TRAJECTORY_SCHEMA)

    def test_unknown_dir_and_run_are_404(self):
        self.assertEqual(self.client.get("/api/export/nope/index.csv").status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/export/{DEMO}/runs/nope.trajectory.jsonl").status_code, 404)

    def test_index_csv_honours_filters(self):
        rows = ST.rows(DEMO)
        h = sorted({r["harness_id"] for r in rows})[0]
        text = self.client.get(f"/api/export/{DEMO}/index.csv", params={"harness": h}).text
        parsed = list(csv.DictReader(io.StringIO(text)))
        self.assertTrue(parsed)
        self.assertEqual({p["harness_id"] for p in parsed}, {h})

    @unittest.skipUnless(HAS_INSPECT, "inspect_ai not installed")
    def test_inspect_endpoint_is_readable_by_inspect(self):
        from inspect_ai.log import read_eval_log
        r = self.client.get(f"/api/export/{DEMO}/runs/{self.rid}.inspect.json")
        self.assertEqual(r.status_code, 200)
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "x.json")
            with open(p, "wb") as f:
                f.write(r.content)
            self.assertEqual(read_eval_log(p).samples[0].id, self.rid)


if __name__ == "__main__":
    unittest.main()


class TestStoreDegradesGracefully(unittest.TestCase):
    """SQLite needs POSIX locking; OneDrive/SMB/FUSE mounts may refuse it outright.

    The index is a derived cache, so neither a hostile filesystem nor an unwritable path may take
    the platform down: it falls back to a local sidecar database, and if even that is impossible,
    to reading index.jsonl.
    """

    def setUp(self):
        ST.close_all()

    def tearDown(self):
        ST.close_all()

    def test_sidecar_path_is_local_and_keyed_by_lab_root(self):
        a = ST._sidecar_path("/net/share/lab/data/.harnesslab_index.sqlite")
        b = ST._sidecar_path("/other/lab/data/.harnesslab_index.sqlite")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith(tempfile.gettempdir()))
        self.assertEqual(a, ST._sidecar_path("/net/share/lab/data/.harnesslab_index.sqlite"))

    def test_unhostable_path_falls_back_to_a_sidecar(self):
        # a path whose parent is a *file* can never hold a database
        bad = os.path.join(tempfile.mkdtemp(), "notadir")
        with open(bad, "w") as f:
            f.write("x")
        self.addCleanup(ST.close_all)
        cx = ST._db(os.path.join(bad, "index.sqlite"))
        self.assertIsNotNone(cx.execute("SELECT 1").fetchone())

    def test_queries_match_load_index_when_sqlite_is_unavailable(self):
        """With every SQLite path refused, the helpers still return the analysis-layer answer."""
        from harnesslab.core.analysis import load_index
        want = load_index(os.path.join(M.RUNS_ROOT, DEMO))
        boom = lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error"))
        real_refresh, real_connect = ST.refresh, ST._connect
        ST.refresh, ST._connect = boom, boom
        try:
            self.assertEqual(ST.rows(DEMO), want)
            self.assertEqual(ST.run_ids(DEMO), [r["run_id"] for r in want])
            self.assertTrue(any(d["name"] == DEMO for d in ST.overview()))
            one = want[0]["harness_id"]
            self.assertEqual(ST.rows(DEMO, harness_id=one),
                             [r for r in want if r["harness_id"] == one])
            self.assertEqual(ST.rows(DEMO, harness_id=""), want)   # falsy filter ignored
        finally:
            ST.refresh, ST._connect = real_refresh, real_connect
