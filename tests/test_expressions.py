"""Arithmetic in numeric fields, and everything it must refuse.

These fields take whatever is typed or pasted into them, so the refusals matter
more than the sums: this exists precisely so that eval is never reached.
"""
import unittest

from atomstack.expressions import evaluate


class Sums(unittest.TestCase):
    def test_a_plain_number_is_itself(self):
        self.assertEqual(evaluate("40"), 40.0)
        self.assertEqual(evaluate("40.5"), 40.5)
        self.assertEqual(evaluate("  12  "), 12.0)
        self.assertEqual(evaluate(7), 7.0)

    def test_the_four_operations_and_brackets(self):
        self.assertAlmostEqual(evaluate("25.4/2"), 12.7)
        self.assertAlmostEqual(evaluate("40*3"), 120.0)
        self.assertAlmostEqual(evaluate("100-5.5"), 94.5)
        self.assertAlmostEqual(evaluate("(10+20)*2"), 60.0)
        self.assertAlmostEqual(evaluate("-5+8"), 3.0)

    def test_inches_the_way_they_get_typed(self):
        self.assertAlmostEqual(evaluate("3*25.4"), 76.2)
        self.assertAlmostEqual(evaluate("1/2*25.4"), 12.7)

    def test_percent_needs_something_to_be_a_percent_of(self):
        self.assertAlmostEqual(evaluate("50%", percent_of=80), 40.0)
        with self.assertRaises(ValueError):
            evaluate("50%")


class Refusals(unittest.TestCase):
    def test_nothing_but_arithmetic_gets_through(self):
        for text in ("__import__('os').getcwd()",
                     "open('x')",
                     "print(1)",
                     "1 if True else 2",
                     "[1,2][0]",
                     "x",
                     "True",
                     "'40'",
                     "lambda: 1",
                     "1;2"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                evaluate(text)

    def test_empty_and_nonsense_are_refused_with_a_reason(self):
        for text in ("", "   ", "40 40", "*", "40+"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                evaluate(text)

    def test_arithmetic_that_cannot_produce_a_usable_number(self):
        for text in ("1/0", "2**4000", "9" * 61):
            with self.subTest(text=text), self.assertRaises(ValueError):
                evaluate(text)


if __name__ == "__main__":
    unittest.main()
