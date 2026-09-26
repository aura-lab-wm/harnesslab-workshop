"""Documentation a clone can actually reach.

The capture spine shipped with its runbook in docs/superpowers/, which .gitignore excludes, so a
clone got the code and none of the instructions. These tests are the tripwire for that whole class
of drift: a pointer into an untracked path, a route the README never names, a test suite with no
way to run it. Each one asserts against the repository as it stands, not against a fixture.
"""
import json
import os
import re
import subprocess
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts) -> str:
    with open(os.path.join(LAB, *parts), encoding="utf-8") as f:
        return f.read()


def in_a_checkout() -> bool:
    """Whether this copy of the lab came from git at all.

    An installed wheel and an unpacked tarball are not checkouts, and `git ls-files` there answers
    "not tracked" for every path -- which this suite read as "git does not ship it" and failed the
    whole class, on a machine where the question is meaningless. Asked once, so the tracked-ness
    checks skip rather than lie."""
    try:
        r = subprocess.run(["git", "-C", LAB, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    except (OSError, ValueError):                       # no git on this machine at all
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


HAVE_GIT = in_a_checkout()

# These read repository areas the school replication package intentionally does not ship
# (harnesslab/school-package.json "exclude", and the top-level docs/ dir is simply not in
# "include" at all): the design-note tree, the developer-facing harnesslab/README.md, and the
# frontend scaffold's own README. They stay real, un-skippable checks against a full checkout;
# they only stop being reachable inside the trimmed package, where the narrower guard applies.
HAVE_ROOT_DOCS_CAPTURE = os.path.isfile(os.path.join(LAB, "docs", "capture.md"))
HAVE_HARNESSLAB_README = os.path.isfile(os.path.join(LAB, "harnesslab", "README.md"))
HAVE_FRONTEND_README = os.path.isfile(os.path.join(LAB, "harnesslab", "frontend", "README.md"))


def tracked(rel: str) -> bool:
    """True when git actually ships this path. `git check-ignore` answers for ignored files that
    exist; a file can also be simply untracked, which is the same failure to a clone."""
    r = subprocess.run(["git", "-C", LAB, "ls-files", "--error-unmatch", rel],
                       capture_output=True, text=True)
    return r.returncode == 0


@unittest.skipUnless(HAVE_ROOT_DOCS_CAPTURE, "requires the full checkout: docs/capture.md")
class TestTheCaptureRunbookIsInTheRepo(unittest.TestCase):
    def test_docs_capture_md_exists_and_is_tracked(self):
        self.assertTrue(os.path.exists(os.path.join(LAB, "docs", "capture.md")),
                        "docs/capture.md is missing: a clone has no capture instructions")
        if not HAVE_GIT:
            self.skipTest("not a git checkout: whether git ships a file is not answerable here")
        self.assertTrue(tracked("docs/capture.md"),
                        "docs/capture.md exists but git does not ship it")

    def test_the_capture_package_points_at_a_document_a_clone_has(self):
        """__init__.py named docs/superpowers/specs/..., which .gitignore excludes."""
        text = read("harnesslab", "capture", "__init__.py")
        refs = re.findall(r"docs/[\w./-]+\.md", text)
        self.assertTrue(refs, "the capture package names no design document at all")
        for ref in refs:
            self.assertTrue(os.path.exists(os.path.join(LAB, ref)),
                            f"harnesslab/capture/__init__.py points at {ref}, which is not in the repo")
            if HAVE_GIT:
                self.assertTrue(tracked(ref), f"{ref} is in the working copy but git does not ship it")


@unittest.skipUnless(HAVE_HARNESSLAB_README, "requires the full checkout: harnesslab/README.md")
class TestTheReadmeNamesEveryCaptureRoute(unittest.TestCase):
    """The API list omitted every /api/capture/* route. Read the routes off the router itself, so
    adding one and forgetting the README fails here instead of shipping."""

    def routes(self) -> list:
        from harnesslab.backend.capture_api import capture_router
        return sorted(r.path for r in capture_router(lambda: LAB).routes)

    def test_every_capture_route_appears_in_the_readme(self):
        readme = read("harnesslab", "README.md")
        self.assertTrue(self.routes(), "no capture routes found to check")
        for path in self.routes():
            self.assertIn(path, readme, f"{path} is served but harnesslab/README.md never names it")

    def test_the_readme_has_a_capture_section(self):
        readme = read("harnesslab", "README.md")
        self.assertIn("docs/capture.md", readme,
                      "harnesslab/README.md does not point at the capture runbook")


@unittest.skipUnless(HAVE_ROOT_DOCS_CAPTURE, "requires the full checkout: docs/capture.md")
class TestTheRunbookPrintsTheWholeReport(unittest.TestCase):
    """The runbook shows what `--backfill` prints, and a reader checks their own output against it.

    An abridged example is worse than none: it showed thirteen of the twenty-two keys the command
    actually prints, so nine real counters -- superseded runs, forks, unpriced models, the cache
    token split -- looked like something this build does not report. Driven by a real backfill over
    a real fixture, so the day the report gains or loses a key the runbook fails here.
    """

    def documented_keys(self) -> set:
        text = read("docs", "capture.md")
        at = text.index("--backfill --path")
        block = text[at:].split("```json", 1)[1].split("```", 1)[0]
        return set(json.loads(block))

    def real_keys(self) -> set:
        import shutil
        import tempfile
        from harnesslab.capture import backfill
        tmp = tempfile.mkdtemp(prefix="hl-doc-backfill-")
        self.addCleanup(shutil.rmtree, tmp, True)
        src, lab = os.path.join(tmp, "src"), os.path.join(tmp, "lab")
        os.makedirs(src)
        shutil.copy(os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "cc_session.jsonl"),
                    os.path.join(src, "session.jsonl"))
        report = backfill.run([src], os.path.join(lab, "data", "runs"), 1800.0,
                              archive_root=os.path.join(tmp, "archive"))
        self.assertGreaterEqual(report.get("files", 0), 1, report)
        # `sources` is per-file detail the sniffer keeps and the COMMAND drops before printing
        # (capture/__main__.py: printed, it is every session path on the machine). The runbook
        # documents what the command prints, so the comparison drops it the same way.
        report.pop("sources", None)
        return set(report)

    def test_the_example_shows_every_key_the_command_prints(self):
        real, doc = self.real_keys(), self.documented_keys()
        self.assertEqual(real - doc, set(),
                         "--backfill prints keys the runbook's example does not show")
        self.assertEqual(doc - real, set(),
                         "the runbook's example shows keys --backfill does not print")


class TestTheSwiftSuiteHasAMakeTarget(unittest.TestCase):
    def test_make_menubar_test_exists_and_runs_swift_test(self):
        makefile = read("Makefile")
        self.assertRegex(makefile, r"(?m)^menubar-test:",
                         "the Swift suite exists and no make target runs it")
        body = makefile.split("\nmenubar-test:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("swift test", body, "menubar-test does not run swift test")
        self.assertIn("macos/CaptureMenubar", body,
                      "menubar-test does not name the package directory")

    def test_menubar_test_is_phony(self):
        self.assertRegex(read("Makefile"), r"(?m)^\.PHONY:.*\bmenubar-test\b",
                         "menubar-test is missing from .PHONY")


@unittest.skipUnless(HAVE_FRONTEND_README, "requires the full checkout: harnesslab/frontend/README.md")
class TestTheFrontendReadmeIsNotBoilerplate(unittest.TestCase):
    def test_it_does_not_still_ship_the_create_vite_template(self):
        text = read("harnesslab", "frontend", "README.md")
        for tell in ("This template provides a minimal setup",
                     "two official plugins are available",
                     "Expanding the Oxlint configuration"):
            self.assertNotIn(tell, text, f"create-vite boilerplate still present: {tell!r}")


if __name__ == "__main__":
    unittest.main()
