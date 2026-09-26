import unittest
from cfg import load_config

class TestLoaderStrong(unittest.TestCase):
    def test_bool_variants(self):
        for v in ["1", "yes", "True", "on"]:
            self.assertIs(load_config({"d": False}, {"APP_D": v})["d"], True, v)
        for v in ["0", "no", "False", "off"]:
            self.assertIs(load_config({"d": True}, {"APP_D": v})["d"], False, v)

if __name__ == "__main__":
    unittest.main()
