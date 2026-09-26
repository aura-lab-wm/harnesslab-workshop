import unittest
from tinycsv import split_row

class TestRows(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(split_row("a,b,c"), ["a", "b", "c"])
    def test_quoted_comma(self):
        self.assertEqual(split_row('a,"b,c",d'), ["a", "b,c", "d"])

if __name__ == "__main__":
    unittest.main()
