"""The simulator must model the machine, not the app.

A simulator that refuses unsafe commands makes every guard test pass whether or
not the guard exists. These tests pin the opposite property: the simulator
accepts things the real firmware would accept, and the controller is the only
thing standing between the operator and a bad move.

Each pairing below sends the same unsafe command twice, once straight at the
simulator and once through the controller, and asserts the simulator says ok
while the controller refuses.
"""
import unittest

from atomstack.controller import Controller, GuardError
from atomstack.protocol import EXPECTED
from atomstack.transports import Simulator


class Clock:
    def __init__(self):
        self.time = 0

    def __call__(self):
        return self.time


def responses(sim, command):
    sim.write(command if isinstance(command, bytes) else (command + "\n").encode())
    out = b""
    while True:
        chunk = sim.read()
        if not chunk:
            return out.decode()
        out += chunk


class FixtureFidelity(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator()

    def test_settings_come_from_the_observed_dump(self):
        for key, value in EXPECTED.items():
            self.assertEqual(self.sim.settings[key], value, f"setting ${key}")

    def test_soft_limits_are_off_because_the_real_machine_reports_them_off(self):
        self.assertEqual(self.sim.settings[20], 0)
        self.assertFalse(self.sim.soft_limits)

    def test_max_power_is_read_rather_than_hardcoded(self):
        self.assertEqual(self.sim.max_power, 1000)

    def test_identity_and_settings_match_the_fixture_text(self):
        self.assertIn("V1.055.Oct 13 2023", responses(self.sim, "$I"))
        self.assertIn("$130=365.000", responses(self.sim, "$$"))


class AcceptsWhatTheFirmwareAccepts(unittest.TestCase):
    """Nothing here is safe. All of it is what GRBL would really do."""

    def setUp(self):
        self.sim = Simulator()

    def test_a_jog_far_outside_the_table_is_accepted(self):
        self.assertEqual(responses(self.sim, "$J=G21 G91 X9999.000 F300"), "ok\n")

    def test_a_feed_above_the_configured_maximum_is_accepted(self):
        self.assertEqual(responses(self.sim, "$J=G21 G91 X1.000 F999999"), "ok\n")

    def test_an_unguarded_jog_distance_is_accepted(self):
        self.assertEqual(responses(self.sim, "$J=G21 G91 Y0.037 F300"), "ok\n")

    def test_an_absolute_move_beyond_the_bed_is_accepted(self):
        self.assertEqual(responses(self.sim, "G53 G1 X5000.000 Y5000.000 F1000"), "ok\n")
        self.assertEqual(self.sim.position, [5000.0, 5000.0])

    def test_spindle_speed_clamps_to_setting_30_rather_than_erroring(self):
        self.assertEqual(responses(self.sim, "M4 S5000"), "ok\n")
        self.assertEqual(self.sim.power, 1000)

    def test_m3_is_accepted_even_though_the_app_never_sends_it(self):
        self.assertEqual(responses(self.sim, "M3 S400"), "ok\n")
        self.assertEqual(self.sim.power, 400)

    def test_only_unrecognised_commands_produce_error_20(self):
        self.assertEqual(responses(self.sim, "G2 X1 Y1 I1 J1"), "error:20\n")
        self.assertEqual(responses(self.sim, "hello"), "error:20\n")

    def test_soft_limits_would_refuse_out_of_range_when_the_machine_enables_them(self):
        self.sim.settings[20] = 1
        self.assertTrue(self.sim.soft_limits)
        self.assertEqual(responses(self.sim, "G53 G1 X5000.000 Y0.000 F1000"), "error:15\n")
        self.assertEqual(responses(self.sim, "G53 G1 X-280.000 Y-290.000 F1000"), "ok\n")

    def test_reset_returns_to_home_and_clears_power(self):
        responses(self.sim, "M4 S500")
        responses(self.sim, "G53 G1 X-100.000 Y-100.000 F1000")
        self.assertEqual(responses(self.sim, b"\x18"), "Grbl 1.1h ['$' for help]\n")
        self.assertEqual(self.sim.position, self.sim.home)
        self.assertEqual(self.sim.power, 0)

    def test_status_reports_the_live_position_and_power(self):
        responses(self.sim, "M4 S250")
        responses(self.sim, "G53 G0 X-200.500 Y-150.250")
        report = responses(self.sim, b"?")
        self.assertIn("MPos:-200.500,-150.250", report)
        self.assertIn("FS:0,250", report)


class ControllerRefusesWhatTheMachineWouldAllow(unittest.TestCase):
    """The pairing tests. Simulator says ok; controller must still say no."""

    def setUp(self):
        self.clock = Clock()
        self.controller = Controller(self.clock)
        self.sim = Simulator()
        self.controller.attach(self.sim, settle=0)
        self.pump(60)
        self.assertTrue(self.controller.ready, self.controller.message)
        self.controller.home()
        self.pump()
        self.controller.confirm_home()
        for _ in range(4):
            if self.controller.pending is None and not self.controller.queue:
                break
            self.pump(1)

    def pump(self, steps=30):
        for _ in range(steps):
            self.clock.time += 0.05
            self.controller.tick()

    def sent_since(self, before):
        return [x for x in self.sim.writes[len(before):] if x.startswith(b"$J") or x.startswith(b"G53")]

    def assert_refused(self, call, *args, **kwargs):
        before = list(self.sim.writes)
        with self.assertRaises(GuardError):
            call(*args, **kwargs)
        self.assertEqual(self.sent_since(before), [],
                         "a refused request must not put bytes on the wire")

    def test_the_bare_simulator_accepts_the_jog_the_controller_refuses(self):
        fresh = Simulator()
        self.assertEqual(responses(fresh, "$J=G21 G91 X9999.000 F300"), "ok\n")
        self.assert_refused(self.controller.jog, "X", 1, 9999.0, 300)

    def test_an_unlisted_jog_distance_is_refused_though_the_machine_would_move(self):
        fresh = Simulator()
        self.assertEqual(responses(fresh, "$J=G21 G91 X0.037 F300"), "ok\n")
        self.assert_refused(self.controller.jog, "X", 1, 0.037, 300)

    def test_a_feed_above_the_live_limit_is_refused(self):
        fresh = Simulator()
        self.assertEqual(responses(fresh, "$J=G21 G91 X1.000 F999999"), "ok\n")
        self.assert_refused(self.controller.jog, "X", 1, 1.0, 999999)

    def test_click_to_jog_outside_the_bed_is_refused(self):
        fresh = Simulator()
        self.assertEqual(responses(fresh, "G53 G1 X5000.000 Y5000.000 F1000"), "ok\n")
        self.assert_refused(self.controller.jog_to, 5000.0, 5000.0, 3000)

    def test_a_job_using_an_arc_is_refused_before_any_byte_is_sent(self):
        self.assert_refused(self.controller.run_job,
                            ["G21", "G90", "M4 S200", "G2 X1 Y1 I1 J1", "M5", "S0"])

    def test_a_job_over_the_power_ceiling_is_refused_though_grbl_would_clamp(self):
        fresh = Simulator()
        self.assertEqual(responses(fresh, "M4 S5000"), "ok\n")
        self.assertEqual(fresh.power, 1000)
        origin = self.controller.origin
        self.assert_refused(
            self.controller.run_job,
            ["G21", "G90", "M4 S5000",
             "G53 G1 X{:.3f} Y{:.3f} F1000".format(origin[0] + 10, origin[1] + 10),
             "M5", "S0"])

    def test_a_job_without_the_disarm_ending_is_refused(self):
        origin = self.controller.origin
        self.assert_refused(
            self.controller.run_job,
            ["G21", "G90", "M4 S200",
             "G53 G1 X{:.3f} Y{:.3f} F1000".format(origin[0] + 10, origin[1] + 10)])

    def test_a_free_form_command_has_no_route_to_the_wire(self):
        self.assert_refused(self.controller.query, "$X")
        self.assert_refused(self.controller.query, "M3 S1000")


if __name__ == "__main__":
    unittest.main()
