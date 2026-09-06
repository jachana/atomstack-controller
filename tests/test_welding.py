"""Welding: the union of several closed outlines.

The core test is membership. A point is inside the welded result exactly when it
was inside one of the objects that went in, and that single property catches
almost anything a boolean can get wrong, so it is checked against thousands of
random points rather than against hand-written expected coordinates.
"""
import math
import random
import unittest

from atomstack.welding import point_in_region, weld


def square(x, y, width, height):
    return [(x, y), (x + width, y), (x + width, y + height), (x, y + height), (x, y)]


def polygon(cx, cy, radius, sides, turn=0.0):
    points = [(cx + radius * math.cos(turn + i * 2 * math.pi / sides),
               cy + radius * math.sin(turn + i * 2 * math.pi / sides)) for i in range(sides)]
    return points + [points[0]]


def area(loop):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(loop, loop[1:]))) / 2


class Membership(unittest.TestCase):
    """Inside the weld must mean inside one of the originals."""

    def assert_matches(self, regions, span=(-10, 45), samples=4000, seed=5):
        loops = weld(regions)
        random.seed(seed)
        for _ in range(samples):
            point = (random.uniform(*span), random.uniform(*span))
            expected = any(point_in_region(point, region) for region in regions)
            self.assertEqual(point_in_region(point, loops), expected,
                             f"disagreed at {point}")
        return loops

    def test_two_overlapping_squares_become_one_outline(self):
        loops = self.assert_matches([[square(0, 0, 10, 10)], [square(5, 5, 10, 10)]])
        self.assertEqual(len(loops), 1)
        self.assertAlmostEqual(area(loops[0]), 175.0, places=6)  # 100 + 100 - 25

    def test_objects_that_only_touch_along_an_edge_merge_without_a_seam(self):
        loops = self.assert_matches([[square(0, 0, 10, 10)], [square(10, 0, 10, 10)]])
        self.assertEqual(len(loops), 1)
        self.assertAlmostEqual(area(loops[0]), 200.0, places=6)
        # The shared line is interior now, so no point of it is left behind.
        for x, y in loops[0]:
            self.assertFalse(abs(x - 10) < 1e-9 and 0 < y < 10)

    def test_separate_objects_stay_separate(self):
        loops = self.assert_matches([[square(0, 0, 10, 10)], [square(20, 0, 10, 10)]])
        self.assertEqual(len(loops), 2)

    def test_a_hole_enclosed_between_objects_survives(self):
        bars = [[square(0, 0, 30, 4)], [square(0, 26, 30, 4)],
                [square(0, 0, 4, 30)], [square(26, 0, 4, 30)]]
        loops = self.assert_matches(bars)
        self.assertEqual(len(loops), 2)
        outer, inner = sorted(loops, key=area, reverse=True)
        self.assertAlmostEqual(area(outer), 900.0, places=6)
        self.assertAlmostEqual(area(inner), 484.0, places=6)  # 22 x 22

    def test_a_bar_across_a_ring_divides_its_hole(self):
        ring = [square(0, 0, 20, 20), square(5, 5, 10, 10)]
        loops = self.assert_matches([ring, [square(8, -2, 4, 24)]])
        self.assertEqual(len(loops), 3)  # outline plus the two halves of the hole

    def test_an_object_inside_another_disappears_into_it(self):
        loops = self.assert_matches([[square(0, 0, 20, 20)], [square(5, 5, 5, 5)]])
        self.assertEqual(len(loops), 1)
        self.assertAlmostEqual(area(loops[0]), 400.0, places=6)

    def test_many_random_overlapping_polygons(self):
        random.seed(19)
        for trial in range(12):
            regions = []
            for _ in range(random.choice((2, 3, 4))):
                regions.append([polygon(random.uniform(5, 25), random.uniform(5, 25),
                                        random.uniform(4, 12), random.choice((3, 4, 5, 6)),
                                        random.uniform(0, math.pi))])
            with self.subTest(trial=trial):
                self.assert_matches(regions, span=(-10, 40), samples=1500, seed=trial)


class Refusals(unittest.TestCase):
    def test_outlines_too_detailed_to_weld_say_so_rather_than_hanging(self):
        # Cost is quadratic in edge count, so the limit is a real answer.
        dense = [polygon(0, 0, 10, 6000)]
        with self.assertRaises(ValueError) as caught:
            weld([dense, [square(0, 0, 10, 10)]])
        self.assertIn("segments", str(caught.exception))

    def test_welding_needs_two_closed_objects(self):
        with self.assertRaises(ValueError):
            weld([[square(0, 0, 10, 10)]])
        with self.assertRaises(ValueError):
            weld([[[(0, 0), (10, 0), (10, 10)]], [square(5, 5, 10, 10)]])


if __name__ == "__main__":
    unittest.main()
