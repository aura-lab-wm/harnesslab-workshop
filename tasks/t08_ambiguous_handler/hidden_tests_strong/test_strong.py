import unittest, inspect
import api.handlers as h

class TestHandlersStrong(unittest.TestCase):
    def test_function_gone(self):
        self.assertFalse(hasattr(h, "legacy_export"))
    def test_no_dangling_reference(self):
        self.assertNotIn("legacy_export", inspect.getsource(h))

if __name__ == "__main__":
    unittest.main()
