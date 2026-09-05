"""Travel limits come from the machine, not from a constant in the source.

The bed size used to be written as 365 x 305 in four modules. The profile guard
already refuses to arm unless the live $130/$131 say exactly that, so the numbers
agreed in practice, but nothing forced them to. These tests pin the direction of
the dependency: bounds follow the machine's own report, and a controller that has
not read them refuses everything.
"""
import unittest

from atomstack.controller import Controller, GuardError
from atomstack.transports import Simulator


class Clock:
    def __init__(self):
        self.time = 0

    def __call__(self):
        return self.time


class BoundsBeforeSettingsAreRead(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(Clock())

    def test_travel_is_unknown_on_a_fresh_controller(self):
        self.assertIsNone(self.controller.travel)

    def test_every_point_is_outside_when_travel_is_unknown(self):
        for point in ((0, 0), (100, 100), (364, 304)):
            self.assertFalse(self.controller._inside(point),
                             "bounds must fail closed before $130/$131 are read")

    def test_a_half_read_profile_still_fails_closed(self):
        self.controller.settings[130] = 365.0
        self.assertIsNone(self.controller.travel)
        self.assertFalse(self.controller._inside((10, 10)))

    def test_a_nonsense_travel_value_fails_closed(self):
        self.controller.settings[130] = 0.0
        self.controller.settings[131] = 305.0
        self.assertIsNone(self.controller.travel)
        self.assertFalse(self.controller._inside((10, 10)))

    def test_the_error_message_admits_the_limits_are_unknown(self):
        self.assertIn("not been read", self.controller.bounds_text)


class BoundsFollowTheMachine(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(Clock())

    def set_travel(self, span_x, span_y):
        self.controller.settings[130] = span_x
        self.controller.settings[131] = span_y

    def test_travel_reports_what_the_machine_said(self):
        self.set_travel(365.0, 305.0)
        self.assertEqual(self.controller.travel, (365.0, 305.0))

    def test_corners_of_the_reported_bed_are_inside(self):
        self.set_travel(365.0, 305.0)
        for point in ((0, 0), (365.0, 305.0), (0, 305.0), (365.0, 0)):
            self.assertTrue(self.controller._inside(point))

    def test_a_smaller_reported_bed_shrinks_the_bounds(self):
        self.set_travel(200.0, 150.0)
        self.assertTrue(self.controller._inside((200.0, 150.0)))
        self.assertFalse(self.controller._inside((201.0, 150.0)))
        self.assertFalse(self.controller._inside((200.0, 151.0)))

    def test_a_larger_reported_bed_widens_the_bounds(self):
        self.set_travel(600.0, 400.0)
        self.assertTrue(self.controller._inside((500.0, 350.0)))

    def test_axes_are_not_interchangeable(self):
        self.set_travel(365.0, 305.0)
        self.assertTrue(self.controller._inside((360.0, 300.0)))
        self.assertFalse(self.controller._inside((300.0, 360.0)))

    def test_the_error_message_quotes_the_live_numbers(self):
        self.set_travel(200.0, 150.0)
        self.assertIn("0-200 X", self.controller.bounds_text)
        self.assertIn("0-150 Y", self.controller.bounds_text)


class BoundsOnAConnectedSession(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.controller = Controller(self.clock)
        self.sim = Simulator(self.clock)
        self.controller.attach(self.sim, settle=0)
        for _ in range(60):
            self.clock.time += 0.05
            self.controller.tick()
        self.assertTrue(self.controller.ready, self.controller.message)

    def test_travel_comes_from_the_observed_dump(self):
        self.assertEqual(self.controller.travel, (365.0, 305.0))

    def test_the_refusal_message_uses_the_machines_numbers(self):
        if self.controller.home_state != "Confirmed":
            self.controller.home()
            for _ in range(30):
                self.clock.time += 0.05
                self.controller.tick()
            if self.controller.phase == "home-confirm":
                self.controller.confirm_home()
        for _ in range(4):
            if self.controller.pending is None and not self.controller.queue:
                break
            self.clock.time += 0.05
            self.controller.tick()
        with self.assertRaises(GuardError) as caught:
            self.controller.jog_to(500.0, 500.0, 3000)
        self.assertIn("0-365 X", str(caught.exception))
        self.assertIn("0-305 Y", str(caught.exception))

    def test_losing_the_travel_settings_blocks_motion(self):
        del self.controller.settings[130]
        self.assertIsNone(self.controller.travel)
        self.assertFalse(self.controller._inside((10, 10)))


if __name__ == "__main__":
    unittest.main()
