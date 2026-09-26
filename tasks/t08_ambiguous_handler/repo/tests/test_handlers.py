import unittest
from api import registry, handle

class TestHandlers(unittest.TestCase):
    def test_ping(self):
        self.assertEqual(handle("ping", {}), {"ok": True})
    def test_export_v2(self):
        self.assertEqual(handle("export_v2", {"rows": [1]})["rows"], [1])

if __name__ == "__main__":
    unittest.main()
