"""Coordinate math the controller cannot check for us.

A guard rejects an out-of-bounds jog. It cannot reject an in-bounds jog to the
wrong corner, so these tests exist to catch sign errors, axis swaps and off-by-one
anchors before they reach the machine.
"""
import unittest

from atomstack.geometry import BED_X, BED_Y
from atomstack.viewport import (
    anchor_point, arrow_target, clamp_to_bed, clamp_zoom, corner_handles,
    fit_viewport, handle_at, inside_bed, snap_value, topmost_at, within_bounds,
    zoom_pan_correction, OPPOSITE, ROTATE_HANDLE, resize_from_handle,
    rotated_handles, rotation_from_pointer,
)


class ViewportMapping(unittest.TestCase):
    def setUp(self):
        self.view = fit_viewport(1000, 800, 80, 50)

    def test_bed_origin_maps_to_the_transform_origin(self):
        self.assertEqual(self.view.to_canvas(0, 0), (self.view.x0, self.view.y0))

    def test_roundtrip_is_stable_across_the_bed(self):
        for point in ((0, 0), (BED_X, BED_Y), (12.5, 300), (364.9, 0.1)):
            px, py = self.view.to_canvas(*point)
            back = self.view.to_bed(px, py)
            self.assertAlmostEqual(back[0], point[0], places=9)
            self.assertAlmostEqual(back[1], point[1], places=9)

    def test_bed_y_grows_upward_while_canvas_y_grows_down(self):
        low = self.view.to_canvas(0, 0)
        high = self.view.to_canvas(0, BED_Y)
        self.assertLess(high[1], low[1])

    def test_bed_x_grows_rightward_on_both_axes(self):
        self.assertLess(self.view.to_canvas(0, 0)[0], self.view.to_canvas(BED_X, 0)[0])

    def test_far_corner_lands_inside_the_canvas_with_the_expected_margin(self):
        px, py = self.view.to_canvas(BED_X, BED_Y)
        self.assertAlmostEqual(px - self.view.x0, BED_X * self.view.scale)
        self.assertAlmostEqual(self.view.y0 - py, BED_Y * self.view.scale)

    def test_pixel_and_millimetre_conversions_are_inverses(self):
        self.assertAlmostEqual(self.view.millimetres(self.view.pixels(7.5)), 7.5)


class ViewportFitting(unittest.TestCase):
    def test_matches_the_machine_tab_formula_it_replaced(self):
        for width, height, zoom in ((1000, 800, 1.0), (1440, 980, 2.5), (640, 480, 1.0)):
            base = min((width - 80) / 365, (height - 50) / 305)
            scale = max(0.01, base * zoom)
            expected = (width / 2 - 182.5 * scale, height / 2 + 152.5 * scale, scale)
            view = fit_viewport(width, height, 80, 50, zoom)
            self.assertAlmostEqual(view.x0, expected[0])
            self.assertAlmostEqual(view.y0, expected[1])
            self.assertAlmostEqual(view.scale, expected[2])

    def test_matches_the_design_canvas_formula_it_replaced(self):
        width, height, zoom, pan_x, pan_y = 1200, 900, 1.75, -40.0, 22.0
        base = min((width - 64) / BED_X, (height - 50) / BED_Y)
        scale = max(0.01, base * zoom)
        view = fit_viewport(width, height, 64, 50, zoom, pan_x, pan_y)
        self.assertAlmostEqual(view.x0, width / 2 - BED_X * scale / 2 + pan_x)
        self.assertAlmostEqual(view.y0, height / 2 + BED_Y * scale / 2 + pan_y)

    def test_scale_never_reaches_zero_on_a_collapsed_canvas(self):
        for width, height in ((0, 0), (80, 50), (10, 10), (-5, -5)):
            self.assertGreaterEqual(fit_viewport(width, height, 80, 50).scale, 0.01)

    def test_zoom_scales_the_bed_but_keeps_it_centred(self):
        one = fit_viewport(1000, 800, 80, 50, 1.0)
        two = fit_viewport(1000, 800, 80, 50, 2.0)
        self.assertAlmostEqual(two.scale, one.scale * 2)
        centre_one = one.to_canvas(BED_X / 2, BED_Y / 2)
        centre_two = two.to_canvas(BED_X / 2, BED_Y / 2)
        self.assertAlmostEqual(centre_one[0], centre_two[0])
        self.assertAlmostEqual(centre_one[1], centre_two[1])

    def test_pan_shifts_the_bed_by_exactly_the_pixel_offset(self):
        plain = fit_viewport(1000, 800, 64, 50, 1.0)
        panned = fit_viewport(1000, 800, 64, 50, 1.0, 30.0, -12.0)
        self.assertAlmostEqual(panned.x0 - plain.x0, 30.0)
        self.assertAlmostEqual(panned.y0 - plain.y0, -12.0)


class BedBounds(unittest.TestCase):
    def test_corners_and_edges_are_inside(self):
        for point in ((0, 0), (BED_X, BED_Y), (0, BED_Y), (BED_X, 0)):
            self.assertTrue(inside_bed(*point))

    def test_just_outside_any_edge_is_rejected(self):
        for point in ((-0.001, 0), (0, -0.001), (BED_X + 0.001, 0), (0, BED_Y + 0.001)):
            self.assertFalse(inside_bed(*point))

    def test_a_swapped_axis_point_is_caught(self):
        self.assertFalse(inside_bed(BED_Y, BED_X))

    def test_clamping_pins_to_the_nearest_edge_without_swapping_axes(self):
        self.assertEqual(clamp_to_bed(-10, 900), (0.0, BED_Y))
        self.assertEqual(clamp_to_bed(9999, -4), (BED_X, 0.0))
        self.assertEqual(clamp_to_bed(100, 200), (100, 200))


class ArrowAccumulation(unittest.TestCase):
    def test_each_direction_moves_the_expected_axis_and_sign(self):
        base = (100.0, 100.0)
        self.assertEqual(arrow_target(base, "Right", 5), (105.0, 100.0))
        self.assertEqual(arrow_target(base, "Left", 5), (95.0, 100.0))
        self.assertEqual(arrow_target(base, "Up", 5), (100.0, 105.0))
        self.assertEqual(arrow_target(base, "Down", 5), (100.0, 95.0))

    def test_repeated_presses_accumulate_from_the_buffered_target(self):
        target = (10.0, 10.0)
        for _ in range(4):
            target = arrow_target(target, "Right", 2.5)
        self.assertEqual(target, (20.0, 10.0))

    def test_pressing_into_an_edge_stops_at_the_edge(self):
        self.assertEqual(arrow_target((BED_X, 50.0), "Right", 10), (BED_X, 50.0))
        self.assertEqual(arrow_target((0.0, 0.0), "Down", 10), (0.0, 0.0))

    def test_an_unknown_direction_is_an_error_rather_than_a_silent_move(self):
        with self.assertRaises(KeyError):
            arrow_target((0.0, 0.0), "Diagonal", 1)


class Anchors(unittest.TestCase):
    bounds = (10.0, 20.0, 110.0, 70.0)

    def test_every_named_corner_is_the_corner_it_claims(self):
        self.assertEqual(anchor_point(self.bounds, "BL"), (10.0, 20.0))
        self.assertEqual(anchor_point(self.bounds, "BR"), (110.0, 20.0))
        self.assertEqual(anchor_point(self.bounds, "TL"), (10.0, 70.0))
        self.assertEqual(anchor_point(self.bounds, "TR"), (110.0, 70.0))

    def test_top_anchors_sit_above_bottom_anchors(self):
        self.assertGreater(anchor_point(self.bounds, "TL")[1],
                           anchor_point(self.bounds, "BL")[1])

    def test_right_anchors_sit_right_of_left_anchors(self):
        self.assertGreater(anchor_point(self.bounds, "BR")[0],
                           anchor_point(self.bounds, "BL")[0])

    def test_centre_is_the_midpoint(self):
        self.assertEqual(anchor_point(self.bounds, "C"), (60.0, 45.0))

    def test_an_unknown_anchor_is_an_error(self):
        with self.assertRaises(KeyError):
            anchor_point(self.bounds, "middle-left")


class HitTesting(unittest.TestCase):
    def test_tolerance_extends_the_box_on_every_side(self):
        bounds = (10.0, 10.0, 20.0, 20.0)
        self.assertTrue(within_bounds(9.5, 15, bounds, tolerance=1))
        self.assertTrue(within_bounds(20.5, 15, bounds, tolerance=1))
        self.assertFalse(within_bounds(8.5, 15, bounds, tolerance=1))

    def test_the_topmost_overlapping_shape_wins(self):
        overlapping = [(0.0, 0.0, 50.0, 50.0), (10.0, 10.0, 20.0, 20.0)]
        self.assertEqual(topmost_at(15, 15, overlapping), 1)
        self.assertEqual(topmost_at(40, 40, overlapping), 0)

    def test_a_miss_returns_none(self):
        self.assertIsNone(topmost_at(80, 80, [(0.0, 0.0, 50.0, 50.0)]))

    def test_an_empty_document_has_nothing_to_hit(self):
        self.assertIsNone(topmost_at(0, 0, []))

    def test_handles_sit_on_the_shape_corners(self):
        handles = corner_handles(10.0, 20.0, 100.0, 50.0)
        self.assertEqual(handles["BL"], (10.0, 20.0))
        self.assertEqual(handles["TR"], (110.0, 70.0))

    def test_a_handle_is_found_only_within_tolerance(self):
        handles = corner_handles(10.0, 20.0, 100.0, 50.0)
        self.assertEqual(handle_at(10.5, 20.5, handles, 1.0), "BL")
        self.assertIsNone(handle_at(60.0, 45.0, handles, 1.0))


class ZoomHelpers(unittest.TestCase):
    def test_zoom_is_clamped_to_the_supplied_range(self):
        self.assertEqual(clamp_zoom(5.0, 1.2, 1.0, 5.0), 5.0)
        self.assertEqual(clamp_zoom(1.0, 1 / 1.2, 1.0, 5.0), 1.0)
        self.assertAlmostEqual(clamp_zoom(2.0, 1.2, 0.5, 8.0), 2.4)

    def test_pan_correction_keeps_the_cursor_point_fixed(self):
        canvas_point = (400.0, 300.0)
        before_view = fit_viewport(1000, 800, 64, 50, 1.0)
        after_view = fit_viewport(1000, 800, 64, 50, 2.0)
        before = before_view.to_bed(*canvas_point)
        after = after_view.to_bed(*canvas_point)
        dx, dy = zoom_pan_correction(before, after, after_view.scale)
        corrected = fit_viewport(1000, 800, 64, 50, 2.0, dx, dy)
        self.assertAlmostEqual(corrected.to_bed(*canvas_point)[0], before[0], places=6)
        self.assertAlmostEqual(corrected.to_bed(*canvas_point)[1], before[1], places=6)

    def test_no_zoom_change_needs_no_pan_correction(self):
        self.assertEqual(zoom_pan_correction((5.0, 5.0), (5.0, 5.0), 3.0), (0.0, -0.0))


class Snapping(unittest.TestCase):
    def test_values_round_to_the_nearest_grid_multiple(self):
        self.assertEqual(snap_value(12.4, 5), 10)
        self.assertEqual(snap_value(12.6, 5), 15)

    def test_snapping_off_returns_the_exact_value(self):
        self.assertEqual(snap_value(12.4, 5, enabled=False), 12.4)

    def test_a_nonpositive_step_cannot_divide_by_zero(self):
        self.assertEqual(snap_value(12.4, 0), 12.4)
        self.assertEqual(snap_value(12.4, -1), 12.4)


if __name__ == "__main__":
    unittest.main()


class RotatedHandleMath(unittest.TestCase):
    """Handles must sit on the corners the geometry module actually draws."""

    BOX = (40.0, 30.0, 60.0, 20.0)

    def geometry_corners(self, rotation, mirror_x=False, mirror_y=False):
        from atomstack.geometry import Shape, shape_paths
        shape = Shape("rectangle", *self.BOX, rotation=rotation,
                      mirror_x=mirror_x, mirror_y=mirror_y).validated()
        path = shape_paths(shape)[0]
        return {"BL": path[0], "BR": path[1], "TR": path[2], "TL": path[3]}

    def test_unrotated_handles_match_the_plain_corners(self):
        self.assertEqual(rotated_handles(self.BOX, 0), corner_handles(*self.BOX))

    def test_handles_follow_rotation_and_mirroring(self):
        for rotation in (0, 15, 90, 137.5, 250, 359):
            for mirror_x, mirror_y in ((False, False), (True, False), (False, True), (True, True)):
                expected = self.geometry_corners(rotation, mirror_x, mirror_y)
                actual = rotated_handles(self.BOX, rotation, mirror_x, mirror_y)
                for name, point in expected.items():
                    with self.subTest(rotation=rotation, mirror=(mirror_x, mirror_y), corner=name):
                        self.assertAlmostEqual(actual[name][0], point[0], places=9)
                        self.assertAlmostEqual(actual[name][1], point[1], places=9)

    def test_the_rotation_grip_sits_beyond_the_top_edge(self):
        grip = rotated_handles(self.BOX, 0, rotation_gap=5)[ROTATE_HANDLE]
        self.assertAlmostEqual(grip[0], 70.0)
        self.assertAlmostEqual(grip[1], 55.0)
        turned = rotated_handles(self.BOX, 90, rotation_gap=5)[ROTATE_HANDLE]
        self.assertAlmostEqual(turned[0], 55.0)
        self.assertAlmostEqual(turned[1], 40.0)

    def test_resizing_a_rotated_box_holds_the_opposite_corner_still(self):
        for rotation in (0, 30, 90, 200, 315):
            for handle in ("BL", "BR", "TL", "TR"):
                for mirror_x in (False, True):
                    before = rotated_handles(self.BOX, rotation, mirror_x)
                    pointer = (before[handle][0] + 7.5, before[handle][1] - 3.25)
                    box = resize_from_handle(handle, pointer, self.BOX, rotation, mirror_x)
                    after = rotated_handles((box["x"], box["y"], box["width"], box["height"]),
                                            rotation, mirror_x)
                    anchor = OPPOSITE[handle]
                    with self.subTest(rotation=rotation, handle=handle, mirror_x=mirror_x):
                        self.assertAlmostEqual(after[anchor][0], before[anchor][0], places=9)
                        self.assertAlmostEqual(after[anchor][1], before[anchor][1], places=9)
                        # The dragged corner ends up under the pointer.
                        self.assertAlmostEqual(after[handle][0], pointer[0], places=9)
                        self.assertAlmostEqual(after[handle][1], pointer[1], places=9)

    def test_resize_matches_the_unrotated_behaviour_and_honours_a_minimum(self):
        box = resize_from_handle("TR", (80, 40), self.BOX, 0)
        self.assertEqual((box["x"], box["y"], box["width"], box["height"]), (40, 30, 40, 10))
        collapsed = resize_from_handle("TR", (40, 30), self.BOX, 0, minimum=0.1)
        self.assertEqual((collapsed["width"], collapsed["height"]), (0.1, 0.1))

    def test_rotation_follows_the_pointer_and_snaps(self):
        centre_right = (100.0, 40.0)
        self.assertAlmostEqual(rotation_from_pointer(centre_right, self.BOX), 270.0)
        self.assertAlmostEqual(rotation_from_pointer((70.0, 90.0), self.BOX), 0.0)
        self.assertAlmostEqual(rotation_from_pointer((72.0, 90.0), self.BOX, step=15), 0.0)
        self.assertAlmostEqual(rotation_from_pointer(centre_right, self.BOX, mirror_y=True), 90.0)
        self.assertEqual(rotation_from_pointer((70.0, 40.0), self.BOX), 0.0)
