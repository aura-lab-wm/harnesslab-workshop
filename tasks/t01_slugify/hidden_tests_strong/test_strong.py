import unittest
from textkit import slugify

class TestSlugStrong(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(slugify("--a--b--"), "a-b")
    def test_unicode_stripped(self):
        # non-ASCII letters must not survive into the slug
        self.assertEqual(slugify("café au lait"), "caf-au-lait")
    def test_idempotent(self):
        for s in ["Hello World", "a--b", "  x  "]:
            self.assertEqual(slugify(slugify(s)), slugify(s))
    def test_no_double_hyphen_anywhere(self):
        for s in ["a  b", "a - b", "a_-_b", "a!!b"]:
            self.assertNotIn("--", slugify(s))

if __name__ == "__main__":
    unittest.main()
