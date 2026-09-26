import unittest
from textkit import slugify

class TestSlugHidden(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")
    def test_punct(self):
        self.assertEqual(slugify("Hello,  World!"), "hello-world")
    def test_edges(self):
        self.assertEqual(slugify("--a--b--"), "a-b")
    def test_empty(self):
        self.assertEqual(slugify(""), "")
    def test_only_punct(self):
        self.assertEqual(slugify("!!!"), "")
    def test_digits(self):
        self.assertEqual(slugify("Python 3.11 rocks"), "python-3-11-rocks")

if __name__ == "__main__":
    unittest.main()
