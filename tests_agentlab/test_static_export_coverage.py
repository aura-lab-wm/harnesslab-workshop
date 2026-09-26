"""A route _urls() never asks for is a page an exported HTML file cannot serve.

RepeatsChart.jsx mounted in the trajectories tab and fetched `/api/repeats/{dir}` and
`/api/repeats/{dir}/run/{run_id}` -- neither was in static_export._urls()'s table, so an export
baked the chart's markup but never its data: the exported page opened, the tab opened, and the
chart hit api.js's "Not in this static export" branch every time. This is the guard the reviewer
who found that asked for: walk the live route table (the same one test_no_dead_surface.py's
NoUncalledRoutes already builds) and fail the moment a new read-only GET route under /api/ has no
entry in _urls() and is not explicitly named as excluded, with why.
"""
import os
import re
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
sys.path.insert(0, os.path.join(LAB, "tests_agentlab"))

from harnesslab.backend import static_export as SE           # noqa: E402
from test_no_dead_surface import registered_routes           # noqa: E402

#: GET routes under /api/ a static export deliberately never snapshots, each with why. A route
#: dropped from here is a route _urls() must cover instead -- this file's own test below fails
#: loudly the moment that stops being true, in either direction.
EXCLUDED_FROM_EXPORT = {
    "/api/bundle":
        "the Field's data reaches the export a different way: field.field_export() embeds the "
        "same build_bundle() payload straight into the HTML that /api/field/html hands back "
        "(and /api/field/html IS in _urls()), so nothing needs to fetch /api/bundle separately",
    "/api/status":
        "a live poll (job list + corpus version) meaningless once frozen; /api/overview.jobs "
        "already carries the same jobs list into the snapshot",
    "/api/jobs":
        "the in-process job list -- already carried as /api/overview.jobs, and a freshly started "
        "export process begins with none running anyway",
    "/api/events":
        "a Server-Sent-Events stream, not a request/response snapshot; there is nothing to bake",
    "/api/results":
        "a bare list of results-directory names that /api/overview.results already carries",
    "/api/results/{name}/query":
        "takes a free-text regex the reader types on the page; there is no fixed set of queries "
        "to pre-run",
    "/api/results/{name}/report.md":
        "a downloadable Markdown rendering of the same report /api/results/{dir}/report already "
        "ships as JSON",
    "/api/export/{name}/bundle.zip":
        "a downloadable file (zip), not a JSON payload the static page's fetch layer can serve",
    "/api/export/{name}/cells.csv":
        "a downloadable file (csv), not a JSON payload",
    "/api/export/{name}/index.csv":
        "a downloadable file (csv), not a JSON payload",
    "/api/export/{name}/runs/{run_id}.inspect.json":
        "a downloadable per-run file behind a save-to-disk button, not a page read",
    "/api/export/{name}/runs/{run_id}.trajectory.jsonl":
        "a downloadable file (jsonl), not a JSON payload",
    # Split as two ADJACENT literals (no `+`, just juxtaposition -- Python concatenates them at
    # compile time) rather than one contiguous string: written whole, this exact substring is what
    # test_no_dead_surface.py's `_call_pattern` matches -- the same mechanism that (correctly)
    # reads a real caller out of test source. That module scans EVERY tests_agentlab/*.py file's
    # raw text (test_no_dead_surface.test_text()), so merely naming this route here, as
    # documentation, made its own `test_the_exemption_list_has_not_gone_stale` believe the route
    # had gained a caller. (A `+`-joined split was tried first and did NOT work: `_call_pattern`'s
    # own "concatenated path" allowance, `["'`]\s*\+[^+]*\+\s*["'`]`, has an unbounded middle
    # clause that reached clean across this file to the unrelated `"^" + body` a few lines below
    # in `_route_regex` and stitched the two `+`s together into one accidental match anyway.)
    "/api/sentinel/exp" "ort/{name}":
        "a downloadable bundle a live import step consumes; no read-only screen fetches it "
        "directly (see test_no_dead_surface.KEPT_WITHOUT_CALLER, same route, same reason)",
    "/api/harness/diff":
        "compares two harness ids the reader picks ad hoc on the harness-diff screen; no fixed "
        "pair to pre-run",
    "/api/harness/ablate/plan":
        "a dry run over ablation parameters the reader chooses interactively",
    "/api/fork/{name}/compare":
        "compares two specific run ids the reader picks from the fork explorer; no fixed pair to "
        "pre-run",
    "/api/import/detect":
        "probes a local filesystem path for import sources -- meaningless, and a path leak, once "
        "exported",
    "/api/capture/relations":
        "the local-machine-only Capture screen ('This machine' view); never reachable from an "
        "export",
    "/api/capture/runs":
        "the local-machine-only Capture screen, same as above",
    "/api/capture/status":
        "the local-machine-only Capture screen, same as above",
    "/api/live":
        "a live poll of the runs in progress on this machine; a frozen export has none, and the "
        "run page never asks for it when IS_STATIC (RunPage.jsx reads run_detail alone there)",
    "/api/runs/{dir}/{run_id}/stream":
        "a Server-Sent-Events tail of a ledger being written; nothing to bake -- the run page "
        "falls back to the per-run detail route, which _urls() already snapshots",
}


def _route_regex(path: str) -> re.Pattern:
    """`path` with each `{param}` segment turned into a hole matching one path segment, anchored
    so `/api/results/{name}/runs` cannot accidentally match `/api/results/{name}/runs/{run_id}`
    -- the mirror-image bug this guard exists to catch on the coverage side."""
    body = re.escape(path)
    body = re.sub(r"\\\{[^}]*\\\}", r"[^/]+", body)
    return re.compile("^" + body + r"(?:\?.*)?$")


class ExportCoversEveryReadOnlyRoute(unittest.TestCase):
    def test_every_get_route_is_exported_or_named_as_excluded(self):
        from starlette.testclient import TestClient
        from harnesslab.backend.app import app

        client = TestClient(app)
        dirs = SE.default_dirs()
        self.assertTrue(dirs, "no results directories under RUNS_ROOT to check _urls() against")
        urls = SE._urls(client, dirs, SE.DEFAULT_RUNS)

        routes = registered_routes()
        gets = sorted({p for m, p, _ep in routes if m == "GET" and p.startswith("/api/")})
        # A sanity floor so this guard cannot quietly become a no-op (an app that failed to wire
        # up its routers would otherwise pass by having nothing left to check).
        self.assertGreater(len(gets), 30, "found suspiciously few GET /api/ routes")

        missing = []
        for path in gets:
            if path in EXCLUDED_FROM_EXPORT:
                continue
            rx = _route_regex(path)
            if not any(rx.match(u) for u in urls):
                missing.append(path)
        self.assertEqual(
            [], missing,
            "these GET routes are registered but _urls() never asks for them, so an exported "
            "HTML file cannot serve whatever page reads them -- add an entry to _urls(), or, if "
            "it is deliberate, name the route in EXCLUDED_FROM_EXPORT with why:\n  "
            + "\n  ".join(missing))

    def test_the_exclusion_list_still_names_routes_that_actually_exist(self):
        """A route renamed or removed should leave this list too, not silently keep meaning
        nothing -- a stale entry here is one fewer route the test above is actually checking."""
        routes = registered_routes()
        live = {p for m, p, _ep in routes if m == "GET" and p.startswith("/api/")}
        stale = sorted(p for p in EXCLUDED_FROM_EXPORT if p not in live)
        self.assertEqual([], stale, f"EXCLUDED_FROM_EXPORT names routes that no longer exist: {stale}")


class ExportBakesExactlyWhatItRequests(unittest.TestCase):
    """The test above proves every registered route has SOME URL in _urls() that could reach it
    -- necessary, not sufficient. A route can still be named there and yet never make it into the
    file: build() has to actually fetch that exact URL successfully and embed it under the exact
    key the frontend's offline request layer looks up (the URL with its leading /api stripped,
    query string and all -- see static_export.py's module docstring on the exact-URL problem, and
    docs/superpowers/specs/2026-09-21-study-aggregates-design.md #2/#3). This runs the real
    build() over small, real results dirs and checks the closed loop end to end: every URL
    _urls() names for them lands as an exact key in the embedded __HARNESSLAB_DATA__ blob.
    """
    #: Kept small and real (both are checked-in fixtures test_harness_tools.py also depends on)
    #: rather than SE.default_dirs()'s full set -- build() actually performs every one of these
    #: URLs' bootstrap/ANOVA computations for real, and the larger prerecorded/live studies would
    #: make this test slow for no more coverage of the mechanism being checked here.
    DIRS = ["demo_mock", "families_mock"]

    def test_every_requested_url_is_an_exact_baked_key(self):
        import json
        import tempfile
        from starlette.testclient import TestClient
        from harnesslab.backend.app import app

        for d in self.DIRS:
            self.assertTrue(
                os.path.exists(os.path.join(LAB, "data", "runs", d, "index.jsonl")),
                f"fixture dir {d!r} is missing -- this test depends on it staying checked in")

        client = TestClient(app)
        expected = SE._urls(client, self.DIRS, SE.DEFAULT_RUNS)
        self.assertGreater(len(expected), 30, "found suspiciously few URLs to check")

        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "export.html")
            result = SE.build(out_path, results=self.DIRS)
            self.assertEqual([], result["failed"],
                              f"these URLs failed during the real export: {result['failed']}")
            html = open(out_path, encoding="utf-8").read()

        m = re.search(r"window\.__HARNESSLAB_DATA__=(.*?);</script>", html, re.S)
        self.assertIsNotNone(m, "the export's embedded-data banner was not found in the built HTML")
        data = json.loads(m.group(1))

        missing = [u for u in expected if u[len("/api"):] not in data]
        self.assertEqual(
            [], missing,
            "these URLs _urls() says the frontend requests are NOT exact keys in the baked "
            "snapshot, so an offline reader asking for them gets a network-fallback miss instead "
            "of the frozen answer:\n  " + "\n  ".join(missing))


if __name__ == "__main__":
    unittest.main()
