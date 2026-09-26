import unittest
from limiter import TokenBucket

class FakeClock:
    def __init__(self, t=0.0): self.t = t
    def __call__(self): return self.t

class TestBucketStrong(unittest.TestCase):
    def test_zero_clock_first_call(self):
        clock = FakeClock(0.0)
        b = TokenBucket(capacity=1, rate=1.0, now=clock)
        self.assertTrue(b.allow())
    def test_refill_after_zero_start(self):
        clock = FakeClock(0.0)
        b = TokenBucket(capacity=1, rate=1.0, now=clock)
        b.allow()
        clock.t = 1.0
        self.assertTrue(b.allow(), "refill must work when the first timestamp was 0")
    def test_long_idle_then_burst(self):
        clock = FakeClock(0.0)
        b = TokenBucket(capacity=5, rate=0.1, now=clock)
        clock.t = 1e6
        self.assertEqual(sum(b.allow() for _ in range(10)), 5)

if __name__ == "__main__":
    unittest.main()
