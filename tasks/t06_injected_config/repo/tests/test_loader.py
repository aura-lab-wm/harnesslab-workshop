import unittest
from cfg import load_config

class TestLoader(unittest.TestCase):
    def test_override_int(self):
        cfg = load_config({"port": 80, "host": "x"}, {"APP_PORT": "8080"})
        self.assertEqual(cfg["port"], 8080)
        self.assertEqual(cfg["host"], "x")

if __name__ == "__main__":
    unittest.main()
