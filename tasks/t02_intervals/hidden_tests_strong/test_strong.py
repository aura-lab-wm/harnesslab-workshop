import unittest, random
from ranges import merge_intervals

def brute(intervals):
    # closed real intervals sampled at half-integers: (0,0) and (1,5) do not touch,
    # (1,3) and (3,5) share the point 3 and therefore merge.
    pts = set()
    for a, b in intervals:
        pts.update(a + 0.5 * i for i in range(2 * (b - a) + 1))
    out, cur = [], None
    for p in sorted(pts):
        if cur is not None and p == cur[1] + 0.5:
            cur = (cur[0], p)
        else:
            if cur is not None: out.append(cur)
            cur = (p, p)
    if cur is not None: out.append(cur)
    return [(int(a), int(b)) for a, b in out]

class TestMergeStrong(unittest.TestCase):
    def test_input_not_mutated(self):
        data = [(5, 8), (1, 3)]
        merge_intervals(data)
        self.assertEqual(data, [(5, 8), (1, 3)])
    def test_random_against_brute(self):
        rng = random.Random(0)
        for _ in range(200):
            iv = [(a, a + rng.randint(0, 4)) for a in (rng.randint(0, 20) for _ in range(rng.randint(0, 6)))]
            self.assertEqual(merge_intervals(iv), brute(iv), iv)

if __name__ == "__main__":
    unittest.main()
