"""The numbers the handout, the package docs and the first-run guide quote about the shipped data
are typed into prose. This test recomputes each one from the shipped runs with the same functions
the exercises call, so a re-recorded dataset or an edited sentence cannot leave the text quoting
figures the data no longer gives.

Runs in a git checkout and inside the extracted school package alike; if a dataset it needs is not
on disk it skips rather than fails.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harnesslab.core.analysis import factorial, filter_rows, load_index, paired_bootstrap  # noqa: E402

RUNS = os.path.join(ROOT, "data", "runs")
HARNESSES = ("baseline", "no_test_tool")          # ex8's default --harnesses
TERMS = {"model": "model (A)", "harness": "harness (B)", "interaction": "model x harness (AB)",
         "task": "task (block)", "cell × task": "cell x task", "residual": "residual (repeats)"}


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _rows(*dirs):
    for d in dirs:
        if not os.path.isfile(os.path.join(RUNS, d, "index.jsonl")):
            raise unittest.SkipTest(f"dataset {d} not on disk")
    rows = [r for d in dirs for r in load_index(os.path.join(RUNS, d))]
    return [r for r in rows if r["harness_id"] in HARNESSES]


def _count(d):
    path = os.path.join(RUNS, d, "index.jsonl")
    if not os.path.isfile(path):
        raise unittest.SkipTest(f"dataset {d} not on disk")
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _quoted_shares(sentence):
    """{'model': 12.2, ..., 'interaction': 0.0 for '≈ 0'} from one handout sentence."""
    out = {}
    for label in TERMS:
        m = re.search(re.escape(label) + r" (≈ 0|\d+(?:\.\d)?%)", sentence)
        if m:
            out[label] = 0.0 if m.group(1) == "≈ 0" else float(m.group(1)[:-1])
    return out


class HandoutMatchesTheData(unittest.TestCase):
    def assert_shares(self, sentence, rows):
        quoted = _quoted_shares(sentence)
        self.assertEqual(set(quoted), set(TERMS), f"could not read every share from: {sentence!r}")
        shares = {t["term"]: t["share"] * 100 for t in factorial(rows)["table"]}
        for label, value in quoted.items():
            actual = shares[TERMS[label]]
            tolerance = 0.5 if value == 0.0 else 0.051    # '≈ 0' means under half a point
            self.assertAlmostEqual(actual, value, delta=tolerance,
                                   msg=f"HANDOUT says {label} {value}%, the data gives {actual:.2f}%")

    def test_real_runs_variance_shares(self):
        rows = _rows("llma4se_live", "live")
        text = _read("HANDOUT.md")
        m = re.search(r"On the (\d+) real runs the shares are: ([^\n]+\n[^\n]+)", text)
        self.assertIsNotNone(m, "HANDOUT no longer carries the real-run share sentence")
        self.assertEqual(int(m.group(1)), len(rows))
        self.assert_shares(m.group(2).replace("\n", " "), rows)
        self.assertIn(f"{len(rows)} runs", text)

    def test_real_runs_section_2_paragraph(self):
        rows = _rows("llma4se_live", "live")
        text = _read("HANDOUT.md")
        m = re.search(r"On these (\d+) real runs the model explains \*\*([\d.]+)%\*\*.*?the harness \*\*([\d.]+)%\*\* "
                      r"and their interaction ([\d.]+)%; the task block takes ([\d.]+)%, cell × task ([\d.]+)% and\s+"
                      r"the run-to-run residual \*\*([\d.]+)%\*\*", text, re.S)
        self.assertIsNotNone(m, "HANDOUT's exercise-8 section 2 paragraph changed shape")
        self.assertEqual(int(m.group(1)), len(rows))
        shares = [t["share"] * 100 for t in factorial(rows)["table"]]
        for quoted, actual in zip(map(float, m.groups()[1:]), shares):
            self.assertAlmostEqual(actual, quoted, delta=0.051)

    def test_mock_control_variance_shares(self):
        rows = _rows("prerecorded_mock", "prerecorded_mock_weak")
        m = re.search(r"The 2 × 2 mock version \(command above\)\s+gives ([^—]+)—", _read("HANDOUT.md"))
        self.assertIsNotNone(m, "HANDOUT no longer carries the mock-control share sentence")
        self.assert_shares(m.group(1).replace("\n", " "), rows)

    def test_qwen_paired_harness_effect(self):
        rows = _rows("llma4se_live", "live")
        m = re.search(r"costs Qwen3\.8 27B ([\d.]+) points \(95% CI \[−([\d.]+), −([\d.]+)\]\)", _read("HANDOUT.md"))
        self.assertIsNotNone(m, "HANDOUT no longer quotes the Qwen paired effect")
        model = "qwen/qwen3.8-27b"
        pb = paired_bootstrap(filter_rows(rows, harness_id=HARNESSES[0], model=model),
                              filter_rows(rows, harness_id=HARNESSES[1], model=model))
        self.assertAlmostEqual(-pb["mean_diff"] * 100, float(m.group(1)), delta=0.051)
        self.assertAlmostEqual(-pb["ci95"][0] * 100, float(m.group(2)), delta=0.051)
        self.assertAlmostEqual(-pb["ci95"][1] * 100, float(m.group(3)), delta=0.051)


def _package_datasets():
    import json
    return set(json.loads(_read("harnesslab/school-package.json"))["datasets"])


class RunCountsMatchTheData(unittest.TestCase):
    """'2,208 runs', '1,632', '576', '500' appear in the guide, the package page and the docs."""

    SOURCES = ["SCHOOL_PACKAGE.md", "README.md", "harnesslab/school-package.json",
               "harnesslab/frontend/src/rig/views/workspace.jsx"]   # the Rig's guide and package pages

    def test_quoted_counts(self):
        llma, live, swe = _count("llma4se_live"), _count("live"), _count("real_swe_agent_500")
        expected = {"collected": llma + live, "llma4se_live": llma, "live": live, "real_swe_agent_500": swe}
        for rel in self.SOURCES:
            if not os.path.isfile(os.path.join(ROOT, rel)):
                continue                                   # the package README replaces the checkout's
            text = _read(rel)
            for n in re.findall(r"([\d,]+) runs of (?:eight|five) ", text) + re.findall(r"([\d,]+) real runs", text):
                self.assertIn(int(n.replace(",", "")), {expected["collected"], llma},
                              f"{rel} quotes {n} runs; the data gives {expected}")
            for real, total in re.findall(r"([\d,]+) of the ([\d,]+) included runs are real", text):
                shipped = sum(_count(d) for d in os.listdir(RUNS)
                              if os.path.isfile(os.path.join(RUNS, d, "index.jsonl")) and d in _package_datasets())
                self.assertEqual(int(real.replace(",", "")), expected["collected"] + swe, f"{rel}: real-run total")
                self.assertEqual(int(total.replace(",", "")), shipped, f"{rel}: included-run total")
            for d in ("llma4se_live", "live", "real_swe_agent_500"):
                for n in re.findall(r"`" + d + r"` \| ([\d,]+) \|", text):
                    self.assertEqual(int(n.replace(",", "")), expected[d], f"{rel}: {d} row")


if __name__ == "__main__":
    unittest.main()
