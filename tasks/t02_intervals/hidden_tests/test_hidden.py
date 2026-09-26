import unittest
from ranges import merge_intervals

class TestMergeHidden(unittest.TestCase):
    def test_overlap(self):
        self.assertEqual(merge_intervals([(1, 4), (2, 6)]), [(1, 6)])
    def test_touching(self):
        self.assertEqual(merge_intervals([(1, 3), (3, 5)]), [(1, 5)])
    def test_unsorted(self):
        self.assertEqual(merge_intervals([(5, 8), (1, 3), (2, 4)]), [(1, 4), (5, 8)])
    def test_disjoint(self):
        self.assertEqual(merge_intervals([(1, 2), (4, 5)]), [(1, 2), (4, 5)])
    def test_contained(self):
        self.assertEqual(merge_intervals([(1, 10), (2, 3)]), [(1, 10)])
    def test_empty(self):
        self.assertEqual(merge_intervals([]), [])

if __name__ == "__main__":
    unittest.main()
