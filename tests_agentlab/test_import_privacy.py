"""An import may not carry a private session store out of the private directory.

`data/runs/captured/` is private on purpose: `results_scope.PRIVATE_DIRS` names it,
`private_guard` answers 404 for it on every route without the opt-in header, and the static
export never reaches it. None of that is about the BYTES -- it is about the directory NAME.

So `harnesslab import ~/.claude/projects --results-dir demo_mock` walks the whole boundary
around: the same transcripts land in a directory the API serves to anyone, `--export` freezes
into a single shareable HTML file, and `/api/export/demo_mock/bundle.zip` hands out as a zip.
Before the CLI and the import screen there was no reachable way to ask for that; there is now,
so the refusal has to live at the one choke point both of them go through.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import importers as I               # noqa: E402
from harnesslab.capture import adapters as CA               # noqa: E402

FIX = os.path.join(LAB, "tests_agentlab", "fixtures", "importers")
CC = os.path.join(FIX, "claude_code_session.jsonl")


class TestWhereASessionStoreLives(unittest.TestCase):
    def test_every_adapter_declared_home_counts_as_a_private_source(self):
        """The registry already says where each source keeps its sessions; that is the list."""
        roots = CA.private_source_roots()
        self.assertTrue(roots)
        for a in CA.REGISTRY:
            for r in a.default_roots:
                self.assertIn(os.path.realpath(os.path.expanduser(r)), roots,
                              f"{a.name} declares {r} and it is not recognised as private")

    def test_an_allow_listed_root_counts_too(self):
        """Whatever the operator told capture to watch is a session store by definition."""
        tmp = tempfile.mkdtemp(prefix="hl-allow-")
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = os.path.join(tmp, "capture.json")
        watched = os.path.join(tmp, "sessions")
        os.makedirs(watched)
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"paths": [watched]}, f)
        old = os.environ.get("HARNESSLAB_CAPTURE_CONFIG")
        os.environ["HARNESSLAB_CAPTURE_CONFIG"] = cfg
        try:
            self.assertIn(os.path.realpath(watched), CA.private_source_roots())
        finally:
            if old is None:
                os.environ.pop("HARNESSLAB_CAPTURE_CONFIG", None)
            else:
                os.environ["HARNESSLAB_CAPTURE_CONFIG"] = old


class TestTheImportRefuses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-priv-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = os.path.join(self.tmp, "runs")
        os.makedirs(self.runs)
        # a "session store": an allow-listed root holding one real Claude Code session
        self.store = os.path.join(self.tmp, "store")
        os.makedirs(self.store)
        shutil.copy(CC, os.path.join(self.store, "session.jsonl"))
        cfg = os.path.join(self.tmp, "capture.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"paths": [self.store]}, f)
        self._old = os.environ.get("HARNESSLAB_CAPTURE_CONFIG")
        os.environ["HARNESSLAB_CAPTURE_CONFIG"] = cfg
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("HARNESSLAB_CAPTURE_CONFIG", None)
        else:
            os.environ["HARNESSLAB_CAPTURE_CONFIG"] = self._old

    def test_a_private_source_will_not_land_in_a_public_directory(self):
        with self.assertRaises(ValueError) as cm:
            I.import_path(self.store, "demo_public", runs_root=self.runs)
        said = str(cm.exception)
        self.assertIn("captured", said, "the refusal does not name the directory that would work")
        self.assertFalse(os.path.exists(os.path.join(self.runs, "demo_public")),
                         "it wrote the directory anyway")

    def test_a_file_inside_one_is_refused_the_same_way(self):
        with self.assertRaises(ValueError):
            I.import_path(os.path.join(self.store, "session.jsonl"), "demo_public", runs_root=self.runs)

    def test_the_private_directory_itself_is_where_it_may_go(self):
        r = I.import_path(self.store, "captured", runs_root=self.runs)
        self.assertGreaterEqual(r["imported"], 1, r)

    def test_a_path_nobody_watches_is_not_restricted(self):
        """The guard is about session stores, not about imports: a batch of traces sitting in a
        downloads folder is exactly what this command is for."""
        loose = os.path.join(self.tmp, "downloads")
        os.makedirs(loose)
        shutil.copy(CC, os.path.join(loose, "session.jsonl"))
        r = I.import_path(loose, "demo_public", runs_root=self.runs)
        self.assertGreaterEqual(r["imported"], 1, r)


if __name__ == "__main__":
    unittest.main()
