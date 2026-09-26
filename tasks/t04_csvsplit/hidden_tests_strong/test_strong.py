import unittest, csv, io, random
from tinycsv import split_row

class TestRowsStrong(unittest.TestCase):
    def test_against_stdlib(self):
        rng = random.Random(1)
        alphabet = ['a', 'b', ',', '"', ' ']
        for _ in range(300):
            fields = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 5))) for _ in range(rng.randint(1, 4))]
            buf = io.StringIO()
            csv.writer(buf, lineterminator="").writerow(fields)
            line = buf.getvalue()
            self.assertEqual(split_row(line), fields, line)
    def test_trailing_empty(self):
        self.assertEqual(split_row("a,b,"), ["a", "b", ""])

if __name__ == "__main__":
    unittest.main()
