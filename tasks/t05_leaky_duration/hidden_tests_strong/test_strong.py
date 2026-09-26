import unittest
from units import parse_duration

class TestDurationStrong(unittest.TestCase):
    def test_whitespace(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
    def test_zero(self):
        self.assertEqual(parse_duration("0s"), 0)
    def test_repeated_units_sum(self):
        self.assertEqual(parse_duration("1m1m"), 120)

if __name__ == "__main__":
    unittest.main()
