"""harnesslab.backend.guard in GitHub Codespaces -- the workshop's main route.

Inside a codespace the student's browser reaches the server through GitHub's port-forwarding proxy
at https://<CODESPACE_NAME>-<port>.<GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN>, and that name arrives
as the Host header. The guard must trust exactly that one name, for the bound port, and nothing else
under the same domain (another student's codespace, a lookalike, a suffix).
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from fastapi import FastAPI                      # noqa: E402
from fastapi.testclient import TestClient        # noqa: E402

from harnesslab.backend import guard             # noqa: E402
from harnesslab.backend.guard import codespaces_host, security_guard, set_bound_host, set_bound_port  # noqa: E402

CS = {"CODESPACES": "true", "CODESPACE_NAME": "fuzzy-orbit-7g4q9x", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev"}
MINE = "fuzzy-orbit-7g4q9x-8765.app.github.dev"


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(security_guard())

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.post("/write")
    def write():
        return {"ok": True}
    return app


class TestCodespacesHost(unittest.TestCase):
    def setUp(self):
        self._orig = (dict(guard._BOUND_HOST), dict(guard._BOUND_PORT))
        set_bound_host("127.0.0.1")
        set_bound_port(8765)
        self.addCleanup(self._restore)
        # a real socket peer, not the in-process test transport
        self.client = TestClient(_app(), client=("10.0.0.7", 51000))

    def _restore(self):
        guard._BOUND_HOST.clear(); guard._BOUND_HOST.update(self._orig[0])
        guard._BOUND_PORT.clear(); guard._BOUND_PORT.update(self._orig[1])

    def test_name_is_built_from_the_codespaces_environment(self):
        self.assertEqual(codespaces_host(env=CS), MINE)
        self.assertEqual(codespaces_host(port=9000, env=CS), "fuzzy-orbit-7g4q9x-9000.app.github.dev")

    def test_outside_a_codespace_there_is_no_extra_host(self):
        self.assertIsNone(codespaces_host(env={}))
        self.assertIsNone(codespaces_host(env={**CS, "CODESPACES": "false"}))
        self.assertIsNone(codespaces_host(env={**CS, "CODESPACE_NAME": ""}))
        self.assertIsNone(codespaces_host(env={**CS, "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": ""}))

    def test_own_forwarded_host_passes_reads_and_same_origin_writes(self):
        with mock.patch.dict(os.environ, CS):
            r = self.client.get("/ping", headers={"host": MINE})
            self.assertEqual(r.status_code, 200, r.text)
            w = self.client.post("/write", json={}, headers={"host": MINE, "origin": f"https://{MINE}", "sec-fetch-site": "same-origin"})
            self.assertEqual(w.status_code, 200, w.text)

    def test_other_names_under_the_same_domain_are_still_rejected(self):
        with mock.patch.dict(os.environ, CS):
            for host in ("app.github.dev", "someone-else-8765.app.github.dev", "fuzzy-orbit-7g4q9x-3000.app.github.dev",
                         f"{MINE}.evil.example", "evil-fuzzy-orbit-7g4q9x-8765.app.github.dev"):
                with self.subTest(host=host):
                    self.assertEqual(self.client.get("/ping", headers={"host": host}).status_code, 400)
            w = self.client.post("/write", json={}, headers={"host": MINE, "origin": "https://someone-else-8765.app.github.dev"})
            self.assertEqual(w.status_code, 403)

    def test_without_the_environment_the_forwarded_name_is_rejected(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in CS:
                os.environ.pop(k, None)
            self.assertEqual(self.client.get("/ping", headers={"host": MINE}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
