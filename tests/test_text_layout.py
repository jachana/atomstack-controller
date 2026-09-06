"""Text layout: several lines, tracking, leading and alignment.

Everything is scaled to fill the object's box, so the assertions here are about
proportion and position within that box rather than absolute millimetres.
"""
import unittest

from atomstack.geometry import Shape
from atomstack.vector_text import text_paths


def bbox(paths):
    xs = [x for path in paths for x, _ in path]
    ys = [y for path in paths for _, y in path]
    return min(xs), min(ys), max(xs), max(ys)


def rows(paths, split):
    """Split laid-out paths into the upper and lower line by a y threshold."""
    upper = [path for path in paths if min(y for _, y in path) >= split]
    lower = [path for path in paths if min(y for _, y in path) < split]
    return upper, lower


class TextLayout(unittest.TestCase):
    BOX = (0.0, 0.0, 40.0, 20.0)

    def paths(self, text, **options):
        return text_paths(text, *self.BOX, "Arial", **options)

    def test_a_second_line_sits_below_the_first(self):
        single = self.paths("AB")
        double = self.paths("AB\nCD")
        self.assertGreater(len(double), len(single))
        upper, lower = rows(double, 10.0)
        self.assertTrue(upper and lower)
        # The two rows do not overlap vertically.
        self.assertGreaterEqual(min(y for path in upper for _, y in path),
                                max(y for path in lower for _, y in path))
        # Both lines are still inside the box.
        left, bottom, right, top = bbox(double)
        self.assertGreaterEqual(left, -1e-9)
        self.assertGreaterEqual(bottom, -1e-9)
        self.assertLessEqual(right, 40.0 + 1e-9)
        self.assertLessEqual(top, 20.0 + 1e-9)

    def test_wider_line_spacing_makes_the_glyphs_smaller_in_the_same_box(self):
        def cap_height(spacing):
            upper, _ = rows(self.paths("AB\nCD", line_spacing=spacing), 10.0)
            ys = [y for path in upper for _, y in path]
            return max(ys) - min(ys)
        self.assertGreater(cap_height(1.2), cap_height(2.5))

    def test_letter_spacing_pushes_glyphs_apart_within_the_block(self):
        def first_gap(tracking):
            paths = self.paths("AA", letter_spacing=tracking)
            lefts = sorted(min(x for x, _ in path) for path in paths)
            return lefts[-1] - lefts[0]
        # Scaled into the same box, more tracking means a bigger share of the
        # width is space, so the two glyphs' left edges spread further apart.
        self.assertGreater(first_gap(0.5), first_gap(0.0))

    def test_alignment_places_a_short_line_against_the_chosen_edge(self):
        def short_line_centre(align):
            paths = self.paths("I\nWWW", align=align)
            upper, _ = rows(paths, 10.0)
            xs = [x for path in upper for x, _ in path]
            return (min(xs) + max(xs)) / 2

        left, centre, right = (short_line_centre(a) for a in ("left", "center", "right"))
        self.assertLess(left, centre)
        self.assertLess(centre, right)
        # Centring puts the short line near the middle of the 40 mm box.
        self.assertAlmostEqual(centre, 20.0, delta=3.0)

    def test_a_blank_line_still_takes_up_its_row(self):
        tight = self.paths("A\nB")
        spaced = self.paths("A\n\nB")
        # With a row of nothing in between, the same ink is scaled smaller.
        tight_upper, _ = rows(tight, 10.0)
        spaced_upper, _ = rows(spaced, 13.0)
        tight_height = max(y for path in tight_upper for _, y in path) - \
            min(y for path in tight_upper for _, y in path)
        spaced_height = max(y for path in spaced_upper for _, y in path) - \
            min(y for path in spaced_upper for _, y in path)
        self.assertGreater(tight_height, spaced_height)

    def test_layout_options_are_checked(self):
        with self.assertRaises(ValueError):
            self.paths("A", align="middle")
        with self.assertRaises(ValueError):
            self.paths("   ")



class TextObjectLabel(unittest.TestCase):
    def test_a_multi_line_label_shows_where_the_break_is(self):
        shape = Shape("text", 0, 0, 10, 5, text="TWO" + chr(10) + "LINES")
        self.assertIn("TWO / LINES", shape.label)


if __name__ == "__main__":
    unittest.main()
