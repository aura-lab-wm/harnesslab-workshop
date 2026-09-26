import unittest
from api import registry, handle

class TestHandlersHidden(unittest.TestCase):
    def test_ping(self):
        self.assertEqual(handle("ping", {}), {"ok": True})
    def test_export_v2(self):
        self.assertEqual(handle("export_v2", {"rows": [1]})["rows"], [1])
    def test_legacy_removed(self):
        self.assertNotIn("legacy_export", registry())
    def test_stats_kept(self):
        self.assertIn("stats", registry())
        self.assertEqual(handle("stats", {"rows": [1, 2]}), {"count": 2})

if __name__ == "__main__":
    unittest.main()
