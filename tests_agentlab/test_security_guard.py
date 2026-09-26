"""harnesslab.backend.guard -- the Host allow-list and the same-origin write check.

Two threats, one middleware (see guard.py's module docstring for the full reasoning):

  1. DNS rebinding: a hostile domain resolved to 127.0.0.1 makes a real browser send a request whose
     *connection* is local but whose `Host` header is still the hostile domain.
  2. CSRF: any web page the operator has open can fire an unsafe request at this process today,
     because nothing here checks where a POST/PUT/PATCH/DELETE came from.

Most tests build a tiny standalone app with only `security_guard()` wired in, so the guard's own
logic is exercised in isolation from the rest of the API surface (the same style
test_capture_control.py's `TestControlRoute` already uses for `private_results_guard`). A handful of
tests hit the real `harnesslab.backend.app.app` singleton, to prove the wiring in app.py actually
runs and runs BEFORE routing -- without touching anything that writes to disk or spends money.
"""
from __future__ import annotations

import os
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from fastapi import FastAPI, Request           # noqa: E402
from fastapi.testclient import TestClient       # noqa: E402

from harnesslab.backend import guard            # noqa: E402
from harnesslab.backend.guard import (          # noqa: E402
    ENV_EXTRA_HOSTS, security_guard, set_bound_host,
)


def _tiny_app() -> FastAPI:
    """A GET and a bodyless POST, guarded by nothing but security_guard -- so a rejection can only
    be the guard's doing, never a route's own validation."""
    app = FastAPI()
    app.middleware("http")(security_guard())

    @app.get("/ping")
    def ping(request: Request):
        return {"ok": True, "host": request.headers.get("host")}

    @app.post("/write")
    def write():
        return {"ok": True}

    return app


class TestSecurityGuard(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_tiny_app())
        # every test starts from the default bound host / no extra env, whatever an earlier test
        # (or an earlier test module, since unittest discover shares one interpreter) left behind
        self._orig_bound = dict(guard._BOUND_HOST)
        self._orig_env = os.environ.get(ENV_EXTRA_HOSTS)
        set_bound_host("127.0.0.1")
        os.environ.pop(ENV_EXTRA_HOSTS, None)
        self.addCleanup(self._restore)

    def _restore(self):
        guard._BOUND_HOST.clear()
        guard._BOUND_HOST.update(self._orig_bound)
        if self._orig_env is None:
            os.environ.pop(ENV_EXTRA_HOSTS, None)
        else:
            os.environ[ENV_EXTRA_HOSTS] = self._orig_env

    # ---------------------------------------------------------------- Host allow-list
    def test_hostile_host_is_rejected(self):
        r = self.client.get("/ping", headers={"host": "evil.example"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Host", r.text)

    def test_empty_host_is_rejected(self):
        r = self.client.get("/ping", headers={"host": ""})
        self.assertEqual(r.status_code, 400)

    def test_testserver_host_is_trusted_only_from_the_in_process_test_transport(self):
        """`Host: testserver` must pass from this repo's own TestClient-based tests (the default
        in-process ASGI transport, whose peer is hard-coded to ("testclient", 50000) -- see
        guard.py's comment above `_TEST_CLIENT_PEER`) but must NOT pass from anything that looks
        like a real socket connection, which is exactly what a DNS-rebinding attacker who got the
        name "testserver" to resolve to 127.0.0.1 would produce."""
        r = self.client.get("/ping", headers={"host": "testserver"})
        self.assertEqual(r.status_code, 200, "the in-process test transport must still be trusted")

        real_client = TestClient(_tiny_app(), client=("203.0.113.5", 54321))
        r2 = real_client.get("/ping", headers={"host": "testserver"})
        self.assertEqual(r2.status_code, 400,
                          "a request with a real (non-test-transport) peer must not be able to use "
                          "Host: testserver to get past the guard")

    def test_each_allowed_host_form_passes(self):
        for host in ("127.0.0.1", "127.0.0.1:8840", "localhost", "localhost:8840",
                     "[::1]", "[::1]:8840", "LOCALHOST", "localhost."):
            with self.subTest(host=host):
                r = self.client.get("/ping", headers={"host": host})
                self.assertEqual(r.status_code, 200, f"{host!r} should be allowed: {r.text}")

    def test_bound_host_is_allowed_when_not_a_wildcard(self):
        set_bound_host("192.168.1.5")
        r = self.client.get("/ping", headers={"host": "192.168.1.5:8840"})
        self.assertEqual(r.status_code, 200)
        # a host that only differs from the bound one still isn't on the list
        r2 = self.client.get("/ping", headers={"host": "192.168.1.6:8840"})
        self.assertEqual(r2.status_code, 400)

    def test_a_wildcard_bind_does_not_become_an_allowed_host_itself(self):
        """--host 0.0.0.0 (the Docker case) must not literally allow `Host: 0.0.0.0`; loopback stays
        allowed on its own merits (the docker-run container maps 127.0.0.1 straight through)."""
        set_bound_host("0.0.0.0")
        r = self.client.get("/ping", headers={"host": "0.0.0.0:8840"})
        self.assertEqual(r.status_code, 400)
        r2 = self.client.get("/ping", headers={"host": "127.0.0.1:8840"})
        self.assertEqual(r2.status_code, 200)

    def test_confusable_subdomains_are_rejected_not_matched_by_prefix(self):
        for host in ("127.0.0.1.evil.example", "localhost.evil.example",
                     "evil.example127.0.0.1", "evil-localhost"):
            with self.subTest(host=host):
                r = self.client.get("/ping", headers={"host": host})
                self.assertEqual(r.status_code, 400, f"{host!r} must not be treated as loopback")

    def test_extra_hosts_env_is_honoured(self):
        r = self.client.get("/ping", headers={"host": "lab.internal:8840"})
        self.assertEqual(r.status_code, 400)
        os.environ[ENV_EXTRA_HOSTS] = "lab.internal, other.example"
        try:
            r = self.client.get("/ping", headers={"host": "lab.internal:8840"})
            self.assertEqual(r.status_code, 200)
            r2 = self.client.get("/ping", headers={"host": "other.example"})
            self.assertEqual(r2.status_code, 200)
        finally:
            os.environ.pop(ENV_EXTRA_HOSTS, None)

    def test_a_read_with_a_bad_host_never_reaches_the_route(self):
        r = self.client.get("/ping", headers={"host": "evil.example"})
        self.assertNotIn("ok", r.text)

    # ---------------------------------------------------------------- Origin (unsafe methods only)
    def test_hostile_origin_on_a_write_is_403(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1", "origin": "https://evil.example",
                                                 "content-type": "application/json"})
        self.assertEqual(r.status_code, 403)

    def test_null_origin_is_403(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1", "origin": "null",
                                                 "content-type": "application/json"})
        self.assertEqual(r.status_code, 403)

    def test_non_http_origin_scheme_is_403(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1", "origin": "chrome-extension://abcdef",
                                                 "content-type": "application/json"})
        self.assertEqual(r.status_code, 403)

    def test_matching_origin_on_a_write_is_not_rejected(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1:8840",
                                                 "content-type": "application/json"})
        self.assertEqual(r.status_code, 200)

    def test_origin_port_is_not_required_to_match_the_host_port(self):
        """The dev server: the browser is same-origin with Vite on :5173, whose proxy forwards to
        this app on :8765 with the Origin header carried through unchanged. Any port on an allowed
        HOST is enough -- the allow-list is about the host, not a specific port pairing."""
        r = self.client.post("/write", headers={"host": "127.0.0.1:8840", "origin": "http://localhost:5173",
                                                 "content-type": "application/json"})
        self.assertEqual(r.status_code, 200)

    def test_an_absent_origin_is_allowed_through_to_the_next_check(self):
        """No Origin header is what curl, a CLI, and this repo's own TestClient-based POST tests all
        look like -- none of them can be a hostile web page (see guard.py's docstring)."""
        r = self.client.post("/write", headers={"host": "127.0.0.1", "content-type": "application/json"})
        self.assertEqual(r.status_code, 200)

    # ---------------------------------------------------------------- Sec-Fetch-Site
    def test_cross_site_sec_fetch_site_is_403(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1", "sec-fetch-site": "cross-site",
                                                 "content-type": "application/json"})
        self.assertEqual(r.status_code, 403)

    def test_same_site_sec_fetch_site_is_also_403(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1", "sec-fetch-site": "same-site",
                                                 "content-type": "application/json"})
        self.assertEqual(r.status_code, 403)

    def test_same_origin_or_none_sec_fetch_site_is_allowed(self):
        for value in ("same-origin", "none"):
            with self.subTest(value=value):
                r = self.client.post("/write", headers={"host": "127.0.0.1", "sec-fetch-site": value,
                                                         "content-type": "application/json"})
                self.assertEqual(r.status_code, 200)

    # ---------------------------------------------------------------- Content-Type
    def test_text_plain_body_to_a_json_route_is_rejected(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1", "content-type": "text/plain"},
                             content=b"{}")
        self.assertEqual(r.status_code, 403)

    def test_form_urlencoded_body_is_rejected(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1"}, data={"a": "1"})
        self.assertEqual(r.status_code, 403)

    def test_missing_content_type_on_a_write_is_rejected(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1"}, content=b"")
        self.assertEqual(r.status_code, 403)

    def test_json_content_type_with_a_charset_is_allowed(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1",
                                                 "content-type": "application/json; charset=utf-8"},
                             content=b"{}")
        self.assertEqual(r.status_code, 200)

    def test_multipart_content_type_is_rejected(self):
        """multipart/form-data is one of the three CORS-safelisted "simple request" content types a
        hostile page can fire without a preflight -- same class as text/plain and urlencoded above.
        No route on this app reads a form body, so there is nothing legitimate to lose by requiring
        strict application/json instead of leaving this one class of simple request open."""
        r = self.client.post("/write", headers={"host": "127.0.0.1",
                                                 "content-type": "multipart/form-data; boundary=X"},
                             content=b"--X--")
        self.assertEqual(r.status_code, 403)

    # ---------------------------------------------------------------- combined / ordering
    def test_same_origin_post_with_the_correct_origin_and_json_is_not_rejected_by_the_guard(self):
        r = self.client.post("/write", headers={"host": "127.0.0.1:8840", "origin": "http://127.0.0.1:8840",
                                                 "content-type": "application/json"}, json={})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_a_bad_host_is_checked_before_the_write_checks(self):
        """The Host check must reject even when Origin/content-type would otherwise pass -- it runs
        first, for every method, not only on writes."""
        r = self.client.post("/write", headers={"host": "evil.example", "origin": "http://127.0.0.1",
                                                 "content-type": "application/json"}, json={})
        self.assertEqual(r.status_code, 400)

    def test_get_requests_are_not_subject_to_the_origin_or_content_type_checks(self):
        r = self.client.get("/ping", headers={"host": "127.0.0.1", "origin": "https://evil.example"})
        self.assertEqual(r.status_code, 200)


class TestSecurityGuardOnTheRealApp(unittest.TestCase):
    """Wiring sanity: the guard registered in app.py actually runs, and runs before routing --
    without spending money, launching a job, or writing anything to disk."""

    def setUp(self):
        self._orig_bound = dict(guard._BOUND_HOST)
        self.addCleanup(lambda: (guard._BOUND_HOST.clear(), guard._BOUND_HOST.update(self._orig_bound)))
        set_bound_host("127.0.0.1")

    def test_get_api_results_is_unaffected_by_the_guard(self):
        from harnesslab.backend.app import app
        r = TestClient(app).get("/api/results")
        self.assertEqual(r.status_code, 200)

    def test_get_api_results_with_a_hostile_host_is_rejected_before_the_route_runs(self):
        from harnesslab.backend.app import app
        r = TestClient(app).get("/api/results", headers={"host": "evil.example"})
        self.assertEqual(r.status_code, 400)

    def test_a_hostile_origin_write_never_reaches_the_route(self):
        """/api/jobs/{jid}/cancel with a made-up jid would 404 from the route (the id isn't in JOBS)
        if the guard let it through; getting 403 instead proves the guard, not the route, answered."""
        from harnesslab.backend.app import app
        r = TestClient(app).post("/api/jobs/not-a-real-job/cancel",
                                  headers={"host": "127.0.0.1", "origin": "https://evil.example",
                                          "content-type": "application/json"})
        self.assertEqual(r.status_code, 403)

    def test_a_same_origin_write_reaches_the_route(self):
        """The mirror image of the test above: same request, allowed Origin -- the guard steps
        aside and the ROUTE's own 404 (unknown job id) is what comes back, proving nothing was
        launched or written by this request."""
        from harnesslab.backend.app import app
        r = TestClient(app).post("/api/jobs/not-a-real-job/cancel",
                                  headers={"host": "127.0.0.1", "origin": "http://127.0.0.1",
                                          "content-type": "application/json"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
