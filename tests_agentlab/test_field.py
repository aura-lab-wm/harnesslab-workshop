"""The Field is served live and exported whole, from the same template.

`harnesslab/backend/field.html` carries exactly one `__DATA__` slot and is a *fragment*.
`GET /field` fills it with `{"live": true}`, so the page pulls its corpus from `/api/bundle`;
`--export-field` fills it with the whole bundle so one file works with no server at all. The page
reduces the bundle itself either way, which is why the export embeds it rather than doing a
second, Python-side reduction.

Ported from agent-lab, where this exercised `agentlab.serve`. The Field now lives in
`harnesslab.backend.field` behind FastAPI, and its launch panel is deliberately not wired here --
launching runs belongs to Command center on this platform -- so the old TestLaunchCmd cases are
gone rather than rewritten against a function that no longer exists.
"""
import json
import os
import re
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harnesslab.backend import field as F


def _client():
    app = FastAPI()
    app.include_router(F.router)
    return TestClient(app)


class TestFieldTemplate(unittest.TestCase):
    def test_template_has_exactly_one_data_slot_and_is_a_fragment(self):
        html = open(F.FIELD_TEMPLATE, encoding="utf-8").read()
        self.assertEqual(html.count(F.DATA_MARKER), 1)
        self.assertNotIn("<!doctype", html.lower(),
                         "field.html is a fragment; the route and the export wrap it")

    def test_template_makes_no_network_requests(self):
        """The exported Field must open offline; a webfont stylesheet would be a request."""
        html = open(F.FIELD_TEMPLATE, encoding="utf-8").read()
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("fonts.gstatic.com", html)

    def test_field_html_fills_the_slot_and_adds_the_skeleton(self):
        out = F.field_html({"live": True})
        self.assertNotIn(F.DATA_MARKER, out)
        self.assertTrue(out.lower().startswith("<!doctype html>"))
        m = re.search(r'<script id="data" type="application/json">(.*?)</script>', out, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(json.loads(m.group(1)), {"live": True})


class TestFieldExport(unittest.TestCase):
    def test_export_embeds_the_whole_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "field.html")
            info = F.export_field("prerecorded_mock", out)
            html = open(out, encoding="utf-8").read()
        self.assertNotIn(F.DATA_MARKER, html)
        m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
        payload = json.loads(m.group(1).replace("<\\/", "</"))
        self.assertIn("bundle", payload)
        self.assertNotIn("live", payload)
        self.assertGreater(len(payload["bundle"]["runs"]), 0)
        self.assertTrue(payload["bundle"]["static"])
        self.assertEqual(info["runs"], len(payload["bundle"]["runs"]))


class TestFieldRoutes(unittest.TestCase):
    def setUp(self):
        self.c = _client()

    def test_field_route_serves_the_live_page(self):
        r = self.c.get("/field")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn('"live": true', r.text)

    def test_bundle_returns_the_corpus(self):
        r = self.c.get("/api/bundle", params={"results": "prerecorded_mock"})
        self.assertEqual(r.status_code, 200)
        b = r.json()
        for key in ("runs", "tasks", "harnesses"):
            self.assertIn(key, b)
        self.assertGreater(len(b["runs"]), 0)

    def test_status_is_a_cheap_change_poll(self):
        r = self.c.get("/api/status", params={"results": "prerecorded_mock"})
        self.assertEqual(r.status_code, 200)
        st = r.json()
        for k in ("version", "runs", "running", "jobs"):
            self.assertIn(k, st)
        bundle = self.c.get("/api/bundle", params={"results": "prerecorded_mock"}).json()
        self.assertEqual(st["runs"], len(bundle["runs"]))

    def test_results_name_cannot_escape_data_runs(self):
        for bad in ("../../etc", "a/b", "/etc"):
            self.assertIn(self.c.get("/api/bundle", params={"results": bad}).status_code,
                          (400, 404), f"{bad!r} should be refused")

    def test_unknown_results_dir_is_404(self):
        self.assertEqual(self.c.get("/api/bundle", params={"results": "nope"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
