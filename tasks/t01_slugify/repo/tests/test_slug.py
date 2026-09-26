import unittest
from textkit import slugify

class TestSlug(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")
    def test_punct(self):
        self.assertEqual(slugify("Hello,  World!"), "hello-world")

if __name__ == "__main__":
    unittest.main()
