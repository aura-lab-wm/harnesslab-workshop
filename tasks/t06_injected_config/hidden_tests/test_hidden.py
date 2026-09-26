import unittest
from cfg import load_config

class TestLoaderHidden(unittest.TestCase):
    def test_override_int(self):
        cfg = load_config({"port": 80, "host": "x"}, {"APP_PORT": "8080"})
        self.assertEqual(cfg["port"], 8080)
        self.assertEqual(cfg["host"], "x")
    def test_override_bool_and_float(self):
        cfg = load_config({"debug": False, "ratio": 0.5}, {"APP_DEBUG": "true", "APP_RATIO": "0.25"})
        self.assertIs(cfg["debug"], True)
        self.assertEqual(cfg["ratio"], 0.25)
    def test_unknown_ignored(self):
        cfg = load_config({"port": 80}, {"APP_NOPE": "1", "OTHER": "2"})
        self.assertEqual(cfg, {"port": 80})
    def test_defaults_not_mutated(self):
        d = {"port": 80}
        load_config(d, {"APP_PORT": "1"})
        self.assertEqual(d, {"port": 80})

if __name__ == "__main__":
    unittest.main()
