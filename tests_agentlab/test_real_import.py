"""The nebius importer: run ids stay unique across downloads, and the attribution names runs.

The console resolves run:<id> across every results directory, and load_nebius always streams the
same first rows, so a student's fresh download used to share every id with the bundled
real_swe_agent_500. The feature attribution also only had scores; it now carries example runs so
the Trajectory features view can open the trajectory behind a number.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import real_import as RI   # noqa: E402


def row(inst, model, resolved, cmds):
    traj = []
    for c in cmds:
        traj += [{"role": "ai", "text": f"step\n```\n{c}\n```"}, {"role": "user", "text": "ok"}]
    return {"instance_id": inst, "model_name": model, "target": resolved, "trajectory": traj,
            "exit_status": "submitted", "generated_patch": ""}


ROWS = [
    row("a__a-1", "m70", False, ["open a.py", "edit 1:1\nx\nend_of_edit", "submit"]),          # edits, never runs after
    row("a__a-1", "m70", True, ["open a.py", "edit 1:1\nx\nend_of_edit", "python -m pytest", "submit"]),
    row("b__b-2", "m70", False, ["open b.py", "edit 1:1\ny\nend_of_edit", "submit"]),
    row("c__c-3", "m8", False, ["ls", "ls", "ls", "ls", "ls", "submit"]),
]


class RealImport(unittest.TestCase):
    def imp(self, root, name):
        with mock.patch.object(RI, "load_nebius", return_value=[dict(r) for r in ROWS]):
            RI.import_runs(len(ROWS), os.path.join(root, name))
        return [json.loads(l)["run_id"] for l in open(os.path.join(root, name, "index.jsonl"))]

    def test_two_downloads_of_the_same_rows_get_different_ids_and_the_bundled_name_keeps_its_own(self):
        with tempfile.TemporaryDirectory() as root:
            bundled, fresh = self.imp(root, "real_swe_agent_500"), self.imp(root, "real_swe_agent_500_2")
            self.assertEqual(bundled[0], "real-0000-a_a_1")
            self.assertTrue(all(i.startswith("real-") for i in fresh))
            self.assertFalse(set(bundled) & set(fresh))
            self.assertEqual(fresh, self.imp(root, "real_swe_agent_500_2"))   # stable for a directory

    def test_each_feature_names_failed_and_resolved_runs_from_distinct_tasks(self):
        with tempfile.TemporaryDirectory() as root:
            ids = self.imp(root, "sample")
            out = RI.analysis(os.path.join(root, "sample"))
            by = {a["feature"]: a for a in out["ochiai"]}
            nv = by["never_verified_after_edit"]
            self.assertEqual(nv["ef"], 3)                               # a run with no edit never verified one either
            self.assertEqual(nv["examples_fail"], [ids[0], ids[2], ids[3]])   # one per task: a__a-1, b__b-2, c__c-3
            self.assertEqual(nv["examples_pass"], [])                   # the resolved run tested after its edit
            for a in out["ochiai"]:
                self.assertLessEqual(len(a["examples_fail"]), 3)
                self.assertTrue(set(a["examples_fail"]) <= set(ids))

    def test_a_directory_whose_index_and_features_disagree_gets_no_examples(self):
        with tempfile.TemporaryDirectory() as root:
            self.imp(root, "sample")
            d = os.path.join(root, "sample")
            lines = open(os.path.join(d, "index.jsonl")).readlines()
            open(os.path.join(d, "index.jsonl"), "w").writelines(lines[:-1])
            out = RI.analysis(d)
            self.assertTrue(all(a["examples_fail"] == [] and a["examples_pass"] == [] for a in out["ochiai"]))

    def test_the_import_reports_the_download_then_the_writing(self):
        phases = []
        def load(n, seed=0, model=None, progress=None):
            progress(85, 85, "MB")
            progress(len(ROWS), n, "trajectories")
            return [dict(r) for r in ROWS]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(RI, "load_nebius", side_effect=load):
            RI.import_runs(len(ROWS), os.path.join(root, "s"), progress=lambda i, n, ph, unit: phases.append((ph, unit, i, n)))
        self.assertEqual(phases[:2], [("download", "MB", 85, 85), ("download", "trajectories", 4, 4)])
        self.assertEqual(phases[-1], ("write", "runs", 4, 4))


class Exercise7ToConsole(unittest.TestCase):
    """ex7 --to-console writes the same directory as the workbench download: features included, and
    even when its features cache from an earlier run exists."""

    def test_to_console_writes_an_importer_directory_every_time(self):
        import contextlib, io, runpy
        from harnesslab.core import real_traj as RT
        script = os.path.join(LAB, "exercises", "ex7_real_trajectories.py")
        with tempfile.TemporaryDirectory() as root:
            out, dest = os.path.join(root, "out"), os.path.join(root, "runs", "nebius_4")
            os.makedirs(out)
            for attempt in (1, 2):                               # the second run finds the features cache
                if os.path.exists(dest):
                    import shutil; shutil.rmtree(dest)
                argv = ["ex7", "--n", "4", "--out", out, "--to-console", dest]
                with mock.patch.object(RT, "load_nebius", return_value=[dict(r) for r in ROWS]), mock.patch.object(sys, "argv", argv), \
                        mock.patch.object(sys, "path", [os.path.dirname(script)] + sys.path), contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(script, run_name="__main__")
                self.assertTrue(os.path.exists(os.path.join(dest, "real_features.jsonl")), f"attempt {attempt}")
                self.assertEqual(RI.analysis(dest)["n"], 4)


class OverviewModelRuns(unittest.TestCase):
    """/api/overview carries runs per model, so the workbench can open a dataset on its biggest model."""

    def test_both_overview_builders_count_runs_per_model(self):
        from harnesslab.backend import metrics as M, store
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(RI, "load_nebius", return_value=[dict(r) for r in ROWS]):
                RI.import_runs(len(ROWS), os.path.join(root, "sample"))
            with mock.patch.object(M, "RUNS_ROOT", root):
                row = M.results_dirs()[0]
            self.assertEqual(row["model_runs"], {"m70": 3, "m8": 1})
            fast = store.overview(runs_root=root, db_path=os.path.join(root, "cache.sqlite"))[0]
            self.assertEqual(fast["model_runs"], row["model_runs"])
            self.assertEqual(set(fast), set(row))


class FetchShard(unittest.TestCase):
    """The loader reads the dataset's parquet shards over plain HTTPS (datasets streaming left the
    process unable to exit for minutes). A shard is downloaded once, never read half-written."""

    def response(self, body):
        r = mock.MagicMock()
        r.headers = {"Content-Length": str(len(body))}
        chunks = [body[i:i + (1 << 20)] for i in range(0, len(body), 1 << 20)] + [b""]
        r.read.side_effect = lambda size: chunks.pop(0)
        r.__enter__.return_value = r
        return r

    def test_downloads_once_with_progress_then_serves_the_cache(self):
        from harnesslab.core import real_traj as RT
        body = b"x" * (3 << 20)
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"HARNESSLAB_NEBIUS_CACHE": cache}):
            with mock.patch.object(RT.urllib.request, "urlopen", return_value=self.response(body)) as op:
                seen = []
                path = RT.fetch_shard(0, lambda d, t, u: seen.append((d, t, u)))
                self.assertEqual(open(path, "rb").read(), body)
                self.assertEqual(seen, [(1, 3, "MB"), (2, 3, "MB"), (3, 3, "MB")])
                self.assertIn("/datasets/nebius/SWE-agent-trajectories/resolve/main/data/train-00000-of-00012.parquet", op.call_args[0][0].full_url)
                self.assertEqual(RT.fetch_shard(0), path)
                self.assertEqual(op.call_count, 1)
            self.assertEqual(os.listdir(cache), ["train-00000-of-00012.parquet"])

    def test_a_failed_download_leaves_nothing_behind_and_says_where_it_failed(self):
        from harnesslab.core import real_traj as RT
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"HARNESSLAB_NEBIUS_CACHE": cache}):
            with mock.patch.object(RT.urllib.request, "urlopen", side_effect=RT.urllib.error.URLError("no route")):
                with self.assertRaisesRegex(RuntimeError, "could not download https://huggingface.co/.*no route"):
                    RT.fetch_shard(3)
            self.assertEqual(os.listdir(cache), [])


try:
    import pyarrow  # noqa: F401
    HAVE_ARROW = True
except ImportError:
    HAVE_ARROW = False


@unittest.skipUnless(HAVE_ARROW, "pyarrow is installed with the [real] extra")
class LoadFromShards(unittest.TestCase):
    def shards(self, cache, *parts):
        import pyarrow as pa, pyarrow.parquet as pq
        from harnesslab.core import real_traj as RT
        for i, rows in enumerate(parts):
            cols = {k: [r[k] for r in rows] for k in RT.COLUMNS}
            pq.write_table(pa.table(cols), os.path.join(cache, f"train-{i:05d}-of-00012.parquet"))

    def test_first_n_rows_in_dataset_order_across_shards_with_a_model_filter(self):
        from harnesslab.core import real_traj as RT
        offline = mock.patch.object(RT.urllib.request, "urlopen", side_effect=AssertionError("the test must not download"))
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"HARNESSLAB_NEBIUS_CACHE": cache}), \
                mock.patch.object(RT, "SHARDS", 2), offline:
            self.shards(cache, ROWS[:3], ROWS[3:] + ROWS[:2])
            seen = []
            got = RT.load_nebius(5, progress=lambda d, t, u: seen.append((d, t, u)))
            self.assertEqual([r["instance_id"] for r in got], ["a__a-1", "a__a-1", "b__b-2", "c__c-3", "a__a-1"])
            self.assertEqual(seen, [(3, 5, "trajectories"), (5, 5, "trajectories")])     # cached: no download
            self.assertEqual([r["instance_id"] for r in RT.load_nebius(5, model="m8")], ["c__c-3"])   # every shard read, one m8 run
            self.assertEqual(RT.load_nebius(3, seed=7), RT.load_nebius(3, seed=7))     # a seed is reproducible


if __name__ == "__main__":
    unittest.main()
