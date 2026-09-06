"""Image operations and the outlines traced from them.

Images are generated here rather than committed, so every expectation is a
measurement of something with a known size: a rectangle of known area, a hole of
known area, a disc of known radius.
"""
import math
import unittest

import numpy as np
import pytest
from PIL import Image, ImageDraw

from atomstack.geometry import shape_bounds
from atomstack.imaging import (adjust, blur, contours, edges, image_shapes,
                               load_grayscale, otsu_level, outline_paths, simplify,
                               threshold)


def loop_area(loop):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(loop, loop[1:]))) / 2


def picture(tmp_path, draw_on, size=(300, 200), name="test.png"):
    image = Image.new("L", size, 255)
    draw_on(ImageDraw.Draw(image))
    path = tmp_path / name
    image.save(path)
    return path


class Operations(unittest.TestCase):
    def test_levels_move_brightness_the_way_they_say(self):
        grey = np.full((4, 4), 0.5, dtype=np.float32)
        self.assertAlmostEqual(float(adjust(grey, brightness=0.2).mean()), 0.7, places=5)
        self.assertAlmostEqual(float(adjust(grey, invert=True).mean()), 0.5, places=5)
        # Contrast pushes away from mid grey in both directions.
        pair = np.array([[0.25, 0.75]], dtype=np.float32)
        stretched = adjust(pair, contrast=2.0)
        self.assertLess(stretched[0][0], 0.25)
        self.assertGreater(stretched[0][1], 0.75)

    def test_levels_are_checked(self):
        grey = np.full((2, 2), 0.5, dtype=np.float32)
        for bad in ({"gamma": 0.0}, {"contrast": -1.0}, {"brightness": 5.0},
                    {"gamma": float("nan")}):
            with self.assertRaises(ValueError):
                adjust(grey, **bad)

    def test_blur_spreads_a_single_bright_pixel_without_changing_the_total(self):
        grey = np.zeros((21, 21), dtype=np.float32)
        grey[10, 10] = 1.0
        blurred = blur(grey, radius=2)
        self.assertLess(float(blurred[10, 10]), 1.0)
        self.assertGreater(float(blurred[10, 11]), 0.0)
        self.assertAlmostEqual(float(blurred.sum()), 1.0, places=3)
        self.assertIs(blur(grey, 0.0), grey)

    def test_otsu_finds_the_valley_between_two_populations(self):
        grey = np.concatenate([np.full(500, 0.1, dtype=np.float32),
                               np.full(500, 0.9, dtype=np.float32)]).reshape(20, 50)
        self.assertTrue(0.2 < otsu_level(grey) < 0.8)
        mask = threshold(grey)
        self.assertEqual(int(mask.sum()), 500)

    def test_edges_mark_the_boundary_and_not_the_flat_areas(self):
        grey = np.ones((20, 20), dtype=np.float32)
        grey[:, 10:] = 0.0
        found = edges(grey, level=0.25)
        self.assertTrue(found[:, 9].any() or found[:, 10].any())
        self.assertFalse(found[:, :5].any())
        self.assertFalse(found[:, 15:].any())

    def test_simplify_drops_points_that_lie_on_the_line(self):
        straight = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
        self.assertEqual(simplify(straight, 0.01), [(0, 0), (4, 0)])
        corner = [(0, 0), (2, 0), (2, 2)]
        self.assertEqual(len(simplify(corner, 0.01)), 3)


class Tracing(unittest.TestCase):
    def test_a_rectangle_traces_to_one_loop_of_its_own_area(self):
        mask = np.zeros((40, 60), dtype=bool)
        mask[10:30, 10:50] = True           # 20 rows by 40 columns
        loops = contours(mask)
        self.assertEqual(len(loops), 1)
        self.assertAlmostEqual(loop_area(loops[0]), 800, delta=1)

    def test_a_hole_becomes_its_own_loop(self):
        mask = np.zeros((40, 60), dtype=bool)
        mask[10:30, 10:50] = True
        mask[16:24, 20:40] = False          # 8 by 20 hole
        areas = sorted(loop_area(loop) for loop in contours(mask))
        self.assertEqual(len(areas), 2)
        self.assertAlmostEqual(areas[0], 160, delta=1)
        self.assertAlmostEqual(areas[1], 800, delta=1)

    def test_separate_regions_trace_separately(self):
        mask = np.zeros((40, 60), dtype=bool)
        mask[5:15, 5:15] = True
        mask[25:35, 40:55] = True
        self.assertEqual(len(contours(mask)), 2)

    def test_a_blank_image_says_so_rather_than_returning_nothing(self):
        with self.assertRaises(ValueError):
            contours(np.zeros((10, 10), dtype=bool))

    def test_outlines_are_placed_in_millimetres_without_stretching(self):
        mask = np.zeros((40, 60), dtype=bool)
        mask[10:30, 10:50] = True
        paths = outline_paths(mask, width_mm=120, tolerance_mm=0.01)
        xs = [x for x, _ in paths[0]]
        ys = [y for _, y in paths[0]]
        # 60 px across becomes 120 mm, so the scale is 2 mm per pixel.
        self.assertAlmostEqual(max(xs) - min(xs), 80, delta=1)
        self.assertAlmostEqual(max(ys) - min(ys), 40, delta=1)


class Importing:
    """pytest-style so tmp_path is available for real files on disk."""

    def test_a_drawn_ring_imports_with_its_hole(self, tmp_path):
        def draw(pen):
            pen.ellipse((40, 20, 240, 180), fill=0)
            pen.ellipse((110, 70, 170, 130), fill=255)
        shape, = image_shapes(picture(tmp_path, draw), width_mm=150, tolerance_mm=0.1)
        assert shape.kind == "path"
        assert len(shape.paths) == 2          # the disc and its hole
        left, bottom, right, top = shape_bounds(shape)
        assert right - left == pytest.approx(100, abs=2)   # 200 px at 0.5 mm/px
        assert top - bottom == pytest.approx(80, abs=2)

    def test_edge_mode_traces_a_gradient_that_threshold_would_miss(self, tmp_path):
        def draw(pen):
            for column in range(300):        # a smooth ramp, no flat boundary
                pen.line((column, 0, column, 200), fill=int(255 * column / 300))
        shape, = image_shapes(picture(tmp_path, draw), width_mm=100, mode="edges",
                              level=0.02, tolerance_mm=0.2)
        assert shape.paths

    def test_settings_are_carried_onto_the_imported_object(self, tmp_path):
        def draw(pen):
            pen.rectangle((50, 50, 250, 150), fill=0)
        shape, = image_shapes(picture(tmp_path, draw), width_mm=80,
                              speed=1234, power=456, passes=3)
        assert (shape.speed, shape.power, shape.passes) == (1234, 456, 3)

    def test_unreadable_and_impossible_requests_are_refused(self, tmp_path):
        missing = tmp_path / "nope.png"
        with pytest.raises(ValueError):
            load_grayscale(missing)
        def draw(pen):
            pen.rectangle((50, 50, 250, 150), fill=0)
        source = picture(tmp_path, draw)
        with pytest.raises(ValueError):
            image_shapes(source, width_mm=100, mode="sideways")
        with pytest.raises(ValueError):
            image_shapes(source, width_mm=0)
        # A threshold that selects nothing has nothing to trace.
        with pytest.raises(ValueError):
            image_shapes(source, width_mm=100, level=0.0)


if __name__ == "__main__":
    unittest.main()
