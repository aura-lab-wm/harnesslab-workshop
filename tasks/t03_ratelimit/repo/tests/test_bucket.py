import unittest
from limiter import TokenBucket

class FakeClock:
    def __init__(self, t=0.0): self.t = t
    def __call__(self): return self.t

class TestBucket(unittest.TestCase):
    def test_capacity(self):
        clock = FakeClock(1.0)
        b = TokenBucket(capacity=3, rate=1.0, now=clock)
        self.assertEqual([b.allow() for _ in range(4)], [True, True, True, False])
    def test_no_overfill(self):
        clock = FakeClock(1.0)
        b = TokenBucket(capacity=3, rate=1.0, now=clock)
        for _ in range(3): b.allow()
        clock.t += 100
        self.assertEqual([b.allow() for _ in range(4)], [True, True, True, False])

if __name__ == "__main__":
    unittest.main()
