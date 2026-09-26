import unittest, tempfile
from store import DiskCache

class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t

class TestCache(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            c = DiskCache(d, ttl=10, now=Clock())
            c.set("a", 1)
            self.assertEqual(c.get("a"), 1)
    def test_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            clk = Clock()
            c = DiskCache(d, ttl=10, now=clk)
            c.set("a", 1)
            clk.t += 11
            self.assertIsNone(c.get("a"))

if __name__ == "__main__":
    unittest.main()
