import unittest
from units import parse_duration

class TestDuration(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(parse_duration("45s"), 45)
    def test_compound(self):
        self.assertEqual(parse_duration("1h30m"), 5400)

if __name__ == "__main__":
    unittest.main()
