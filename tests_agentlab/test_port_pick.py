"""A busy port must never send the browser to the wrong server.

Before: uvicorn failed to bind *after* the browser had been told to open the URL, so the tab
showed whatever else was listening there -- typically an older harnesslab with a different UI.
"""
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harnesslab import __main__ as M  # noqa: E402


def _occupy():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


class PickPort(unittest.TestCase):
    def test_free_port_is_kept(self):
        s, port = _occupy(); s.close()
        self.assertEqual(M._pick_port("127.0.0.1", port, explicit=False), port)

    def test_busy_default_moves_to_the_next_free_port(self):
        s, port = _occupy()
        try:
            got = M._pick_port("127.0.0.1", port, explicit=False)
            self.assertIsNotNone(got)
            self.assertGreater(got, port)
            self.assertLessEqual(got, port + 20)
        finally:
            s.close()

    def test_busy_port_the_user_named_is_an_error_not_a_move(self):
        s, port = _occupy()
        try:
            self.assertIsNone(M._pick_port("127.0.0.1", port, explicit=True))
        finally:
            s.close()

    def test_explicit_means_flag_or_environment(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HARNESSLAB_PORT", None)
            self.assertFalse(M._port_is_explicit([]))
            self.assertTrue(M._port_is_explicit(["--port", "9000"]))
            self.assertTrue(M._port_is_explicit(["--port=9000"]))
        with mock.patch.dict(os.environ, {"HARNESSLAB_PORT": "9000"}):
            self.assertTrue(M._port_is_explicit([]))


if __name__ == "__main__":
    unittest.main()
