import unittest, tempfile, os
from store import DiskCache

class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t

class TestCacheHidden(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            c = DiskCache(d, ttl=10, now=Clock())
            c.set("a", 1)
            self.assertEqual(c.get("a"), 1)
    def test_expiry_removes_file(self):
        with tempfile.TemporaryDirectory() as d:
            clk = Clock()
            c = DiskCache(d, ttl=10, now=clk)
            c.set("a", 1)
            clk.t += 11
            self.assertIsNone(c.get("a"))
            self.assertFalse(os.path.exists(os.path.join(d, "a.json")))
    def test_default(self):
        with tempfile.TemporaryDirectory() as d:
            c = DiskCache(d, ttl=10, now=Clock())
            self.assertEqual(c.get("missing", 7), 7)
    def test_boundary_not_expired(self):
        with tempfile.TemporaryDirectory() as d:
            clk = Clock()
            c = DiskCache(d, ttl=10, now=clk)
            c.set("a", 1)
            clk.t += 10
            self.assertEqual(c.get("a"), 1)

if __name__ == "__main__":
    unittest.main()
