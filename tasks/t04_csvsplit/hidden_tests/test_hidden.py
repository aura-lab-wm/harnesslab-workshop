import unittest
from tinycsv import split_row

class TestRowsHidden(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(split_row("a,b,c"), ["a", "b", "c"])
    def test_quoted_comma(self):
        self.assertEqual(split_row('a,"b,c",d'), ["a", "b,c", "d"])
    def test_escaped_quote(self):
        self.assertEqual(split_row('\"say \"\"hi\"\"\",x'), ['say \"hi\"', "x"])
    def test_empty_fields(self):
        self.assertEqual(split_row("a,,c"), ["a", "", "c"])
    def test_single(self):
        self.assertEqual(split_row("abc"), ["abc"])
    def test_no_csv_module(self):
        import tinycsv.rows as m, inspect
        self.assertNotIn("import csv", inspect.getsource(m))

if __name__ == "__main__":
    unittest.main()
