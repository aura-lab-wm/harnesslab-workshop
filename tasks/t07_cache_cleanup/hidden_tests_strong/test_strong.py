import unittest, tempfile, os
from store import DiskCache

class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t

class TestCacheStrong(unittest.TestCase):
    def test_corrupt_file_treated_as_missing(self):
        with tempfile.TemporaryDirectory() as d:
            c = DiskCache(d, ttl=10, now=Clock())
            with open(os.path.join(d, "bad.json"), "w") as f:
                f.write("not json")
            self.assertIsNone(c.get("bad"))

if __name__ == "__main__":
    unittest.main()
