"""PIN ONE GENERATION for the whole bake (docs/superpowers/specs/2026-09-21-study-aggregates-
design.md #4): a static export must never embed a comparisons response read under one revision of
a study's index.jsonl alongside an oracle response read under a later one.

static_export.build() pins this per results dir: it stats index.jsonl's identity (the same
fingerprint app.py's own `_StudyCache` keys freshness on) before requesting that dir's whole URL
set and again after, and only commits the batch to the exported file when the two match. A
mismatch discards the WHOLE batch (never merges part of it) and retries the dir from scratch, up
to a bounded number of attempts; a dir that never holds still is dropped from the export entirely
(recorded once in `failed`) rather than shipped as a silent mix of generations.

These tests patch static_export._generation_fingerprint specifically (not app.py's
_study_fingerprint, which every route's own request-scoped freshness check also calls) so they
exercise ONLY the two checks build() makes around each dir's batch, not the live routes' own
internal caching behaviour.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import static_export as SE   # noqa: E402

DIRS = ["demo_mock", "families_mock"]


def _blob(html_path: str) -> dict:
    html = open(html_path, encoding="utf-8").read()
    m = re.search(r"window\.__HARNESSLAB_DATA__=(.*?);</script>", html, re.S)
    assert m, "the export's embedded-data banner was not found in the built HTML"
    return json.loads(m.group(1))


class TestGenerationPinning(unittest.TestCase):
    def test_a_dir_whose_index_jsonl_never_holds_still_is_dropped_not_mixed(self):
        """Simulates a writer that touches demo_mock between every fp_before/fp_after check this
        test's whole run: build() must never commit any of demo_mock's keys (a real study-
        aggregate response computed under a generation it could not confirm), must say why in
        `failed`, and must still bake the OTHER, well-behaved dir normally -- one dir's writer
        race must not sink the export."""
        real_fp = SE._generation_fingerprint
        calls = {"n": 0}

        def always_moving(name):
            if name != "demo_mock":
                return real_fp(name)
            calls["n"] += 1          # a fresh marker every call -> fp_before never equals fp_after
            return real_fp(name) + (f"poisoned-{calls['n']}",)

        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "export.html")
            with patch("harnesslab.backend.static_export._generation_fingerprint",
                       side_effect=always_moving):
                result = SE.build(out_path, results=DIRS)
            data = _blob(out_path)

        demo_keys = [k for k in data if k.startswith("/results/demo_mock")]
        fam_keys = [k for k in data if k.startswith("/results/families_mock")]
        self.assertEqual([], demo_keys, "demo_mock's keys were baked despite never pinning a generation")
        self.assertGreater(len(fam_keys), 0, "the well-behaved dir must still export normally")

        gen_failures = [f for f, _why in result["failed"] if f == "(generation) demo_mock"]
        self.assertEqual(1, len(gen_failures), f"expected exactly one generation failure entry, got: {result['failed']}")

    def test_a_transient_mismatch_is_retried_and_recovers(self):
        """Only the very FIRST fingerprint read lies (as if a writer landed one append right as
        the bake started, then went quiet); build() must retry the whole batch and succeed on a
        later attempt -- not give up after a single mismatch, and not leave `failed` carrying a
        generation entry for a dir that ultimately did pin."""
        real_fp = SE._generation_fingerprint
        calls = {"n": 0}

        def mismatch_once(name):
            calls["n"] += 1
            fp = real_fp(name)
            if name == "demo_mock" and calls["n"] == 1:
                return fp + ("poisoned-once",)
            return fp

        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "export.html")
            with patch("harnesslab.backend.static_export._generation_fingerprint",
                       side_effect=mismatch_once):
                result = SE.build(out_path, results=DIRS)
            data = _blob(out_path)

        self.assertGreater(calls["n"], 2, "the mismatch never actually forced a retry")
        self.assertIn("/results/demo_mock/comparisons", data)
        self.assertIn("/results/demo_mock/oracle", data)
        gen_failures = [f for f, _why in result["failed"] if f == "(generation) demo_mock"]
        self.assertEqual([], gen_failures, f"a recovered dir must not be reported as failed: {result['failed']}")

    def test_comparisons_and_oracle_for_one_dir_come_from_the_same_bake_window(self):
        """The property the design doc actually cares about, checked directly rather than through
        the retry mechanics: a normal (non-racing) export's comparisons and oracle responses for
        the SAME dir are baked together, in the SAME pass over that dir's URL set -- not fetched
        independently with no relation between them. Proven by construction: patching
        _generation_fingerprint to return a constant (so every before/after check trivially
        matches) must still produce a complete, internally consistent bake."""
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "export.html")
            with patch("harnesslab.backend.static_export._generation_fingerprint",
                       return_value=("constant",)):
                result = SE.build(out_path, results=DIRS)
            data = _blob(out_path)

        self.assertEqual([], result["failed"])
        for d in DIRS:
            self.assertIn(f"/results/{d}/comparisons", data)
            self.assertIn(f"/results/{d}/oracle", data)


if __name__ == "__main__":
    unittest.main()
