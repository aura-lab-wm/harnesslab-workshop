import unittest
from ranges import merge_intervals

class TestMerge(unittest.TestCase):
    def test_overlap(self):
        self.assertEqual(merge_intervals([(1, 4), (2, 6)]), [(1, 6)])
    def test_touching(self):
        self.assertEqual(merge_intervals([(1, 3), (3, 5)]), [(1, 5)])

if __name__ == "__main__":
    unittest.main()
