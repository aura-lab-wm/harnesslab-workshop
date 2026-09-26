import unittest
from units import parse_duration

class TestDurationHidden(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(parse_duration("45s"), 45)
    def test_compound(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2m5s"), 125)
    def test_bad(self):
        for bad in ["", "h", "10", "1x", "1h30"]:
            with self.assertRaises(ValueError):
                parse_duration(bad)

if __name__ == "__main__":
    unittest.main()
