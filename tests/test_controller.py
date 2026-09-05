import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from atomstack.controller import Controller, GuardError
from atomstack.protocol import EXPECTED, parse_status, parse_setting
from atomstack.transports import Simulator, SerialTransport


class Clock:
    def __init__(self):
        self.time = 0

    def __call__(self):
        return self.time


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = Controller(self.clock)
        self.t = Simulator(self.clock)
        self.c.attach(self.t, settle=0)
        self.pump(60)
        self.assertTrue(self.c.ready, self.c.message)

    def pump(self, steps=30):
        for _ in range(steps):
            self.clock.time += 0.05
            self.c.tick()

    def home(self):
        self.c.set_physical_laser_off(True)
        self.c.home()
        self.pump()
        self.assertEqual(self.c.phase, "home-confirm", self.c.message)
        self.c.confirm_home()
        # A status transaction can be awaiting its own ACK at this instant.
        for _ in range(4):
            if self.c.pending is None and not self.c.queue:
                break
            self.pump(1)

    def test_diagnostics_and_observed_profile(self):
        self.assertEqual(self.c.firmware, "V1.055.Oct 13 2023")
        self.assertEqual({k: self.c.settings[k] for k in EXPECTED}, EXPECTED)
        self.assertIn("G54", self.c.parameters)
        self.assertIn("M5", self.c.modal)
        for cmd in (b"$I\n", b"$$\n", b"?", b"$#\n", b"$G\n"):
            self.assertIn(cmd, self.t.writes)
        self.assertNotIn(b"M5 S0\n", self.t.writes, "Already-verified M5 S0 needs no redundant command")
        self.assertFalse(any(x.startswith((b"$H", b"$J")) for x in self.t.writes))

    def test_error_one_preserves_pending_command_and_exact_bytes(self):
        self.c.query("$I")
        self.pump(1)
        self.t.rx.clear()
        self.t.rx.append(b"error:1\r\n")
        self.pump(1)
        self.assertFalse(self.c.connected)
        self.assertIn("awaiting '$I'", self.c.message)
        self.assertIn("expected a command letter", self.c.message)
        log = "\n".join(text for kind, text in self.c.log)
        self.assertIn("24 49 0a", log)

    def test_unsolicited_error_does_not_blame_previous_command(self):
        self.c.receive("error:1")
        self.assertIn("no line command pending", self.c.message)

    def test_home_not_inferred_from_fixture_or_idle(self):
        self.assertIsNone(self.c.origin)
        with self.assertRaises(GuardError):
            self.c.jog("X", 1)
        with self.assertRaises(GuardError):
            self.c.confirm_home()

    def test_physical_disconnect_is_not_required(self):
        self.c.home()
        self.pump()
        self.assertEqual(self.c.phase, "home-confirm")

    def test_home_ack_is_not_confirmation(self):
        self.c.set_physical_laser_off(True)
        self.c.home()
        self.pump()
        self.assertIsNone(self.c.origin)
        with self.assertRaises(GuardError):
            self.c.jog("X", 1)

    def test_captures_actual_home_not_hardcoded_sample(self):
        self.c.set_physical_laser_off(True)
        self.c.home()
        self.pump()
        # A separate session may have different actual machine home coordinates.
        self.t.position = [-362, -302]
        self.c.status = parse_status("<Idle|MPos:-362,-302,0|FS:0,0>")
        self.c.confirm_home()
        self.assertEqual(self.c.origin, (-362, -302))
        self.assertEqual(self.c.app_position, (0, 0))

    def test_relative_jog_no_work_offset_dependency(self):
        self.home()
        self.c.parameters["G54"] = "250,100,0"
        self.c.jog("X", 1)
        self.pump()
        self.assertEqual(self.c.app_position, (1, 0))
        self.assertIn(b"$J=G21 G91 X1.000 F300\n", self.t.writes)

    def test_lower_bound_blocks_without_write(self):
        self.home()
        before = list(self.t.writes)
        for axis in "XY":
            with self.assertRaises(GuardError):
                self.c.jog(axis, -1)
        self.assertEqual(self.t.writes, before)

    def test_upper_bounds(self):
        self.home()
        self.c.status = parse_status("<Idle|MPos:77,4,0|FS:0,0>")
        self.assertEqual(self.c.app_position, (365, 305))
        for axis in "XY":
            with self.assertRaises(GuardError):
                self.c.jog(axis, 1)

    def test_only_one_axis_one_mm(self):
        self.home()
        for axis, step in [("Z", 1), ("X", 2), ("X", 0), ("X\nM3", 1)]:
            with self.assertRaises(GuardError):
                self.c.jog(axis, step)

    def test_guarded_jog_distance_and_speed_presets(self):
        self.home()
        self.c.jog("X", 1, 5.0, 1200)
        self.pump()
        self.assertEqual(self.c.app_position, (5, 0))
        self.assertIn(b"$J=G21 G91 X5.000 F1200\n", self.t.writes)
        for distance, feed in ((2, 300), (1, 59), (1, 1201), (float("nan"), 300)):
            with self.subTest(distance=distance, feed=feed), self.assertRaises(GuardError):
                self.c.jog("X", 1, distance, feed)

    def test_jog_target_accounts_for_selected_distance(self):
        self.home()
        self.c.status = parse_status("<Idle|MPos:67,4,0|FS:0,0>")  # App X 355, Y 305.
        self.t.position = [67, 4]
        self.c.last_position = self.c.status.machine
        self.c.jog("X", 1, 10.0, 600)
        self.pump()
        self.assertEqual(self.c.app_position, (365, 305))
        with self.assertRaises(GuardError):
            self.c.jog("X", 1, 0.1, 60)

    def test_click_to_jog_uses_absolute_machine_target_and_no_laser_words(self):
        self.home()
        self.c.jog_to(100, 50, 6000)
        self.pump(40)
        self.assertEqual(self.c.app_position, (100, 50))
        command = b"$J=G21 G90 G53 X-188.000 Y-251.000 F6000\n"
        self.assertIn(command, self.t.writes)
        self.assertNotIn(b"M4", command)
        self.assertNotIn(b" S", command)

    def test_click_to_jog_rejects_bad_coordinates_and_live_feed_limit(self):
        self.home()
        for x, y, feed in ((-1, 0, 3000), (366, 0, 3000), (0, 306, 3000),
                           (float("nan"), 0, 3000), (1, 1, 50000)):
            with self.subTest(x=x, y=y, feed=feed), self.assertRaises(GuardError):
                self.c.jog_to(x, y, feed)

    def test_click_to_jog_defers_behind_status_poll(self):
        self.home()
        self.clock.time += 0.5
        self.c.tick()
        self.assertTrue(self.c._status_poll_pending())
        self.t.rx.clear()
        self.c.jog_to(10, 20, 3000)
        self.assertEqual(self.c.deferred_motion, ("jog-to", 10.0, 20.0, 3000))
        self.c.receive("<Idle|MPos:-288,-301,0|FS:0,0>")
        self.pump(30)
        self.assertEqual(self.c.app_position, (10, 20))

    def test_motion_click_during_status_poll_is_deferred_once(self):
        self.home()
        self.pump(9)  # Dispatch the next bare status request.
        if not self.c._status_poll_pending():
            self.clock.time += 0.5
            self.c.tick()
        self.assertTrue(self.c._status_poll_pending())
        self.t.rx.clear()
        self.c.jog("X", 1, 1.0, 600)
        self.assertEqual(self.c.deferred_motion, ("jog", "X", 1, 1.0, 600))
        with self.assertRaises(GuardError):
            self.c.jog("X", 1, 1.0, 600)
        self.c.receive("<Idle|MPos:-288,-301,0|FS:0,0>")
        self.assertEqual(self.c.phase, "jog-command")
        self.pump()
        self.assertEqual(self.c.app_position, (1, 0))

    def test_repeated_click_cannot_queue_jogs(self):
        self.home()
        self.c.jog("X", 1)
        with self.assertRaises(GuardError):
            self.c.jog("X", 1)
        self.pump()
        self.assertEqual(sum(x.startswith(b"$J") for x in self.t.writes), 1)

    def test_ack_alone_does_not_complete_jog(self):
        self.home()
        self.c.jog("Y", 1)
        self.pump(1)
        self.c.receive("ok")
        self.assertEqual(self.c.phase, "jog-status")
        with self.assertRaises(GuardError):
            self.c.jog("X", 1)

    def test_wrong_endpoint_invalidates_origin(self):
        self.home()
        self.c.jog("X", 1)
        self.pump(1)
        self.c.receive("ok")
        self.c.receive("<Idle|MPos:-290,-301,0|FS:0,0>")
        self.assertFalse(self.c.connected)
        self.assertIsNone(self.c.origin)

    def test_stale_position_blocks_motion(self):
        self.home()
        self.clock.time += 2
        with self.assertRaises(GuardError):
            self.c.jog("X", 1)

    def test_missing_status_during_jog_stops(self):
        self.home()
        self.c.jog("X", 1)
        self.pump(1)
        self.t.rx.clear()
        self.clock.time += 6
        self.c.tick()
        self.assertFalse(self.c.connected)
        self.assertIn(b"\x85", self.t.writes)

    def test_frame_traces_absolute_bounded_outline_and_stays_laser_off(self):
        self.home()
        points = ((8, 18), (52, 18), (52, 52), (8, 52), (8, 18))
        self.c.frame(points, 600)
        self.pump(1200)  # 60 s: the outline is 156 mm of real motion at 600 mm/min.
        frame_writes = [x for x in self.t.writes if x.startswith(b"$J=G21 G90 G53")]
        self.assertEqual(len(frame_writes), 5)
        self.assertEqual(frame_writes[0], b"$J=G21 G90 G53 X-280.000 Y-283.000 F600\n")
        self.assertEqual(self.c.phase, "idle")
        self.assertEqual(self.c.app_position, (8, 18))
        self.assertIn("Laser remained off", self.c.message)
        self.assertFalse(any(b"M3" in x or b"M4" in x or b" S" in x for x in frame_writes))

    def test_frame_requires_home_profile_power_and_bounds(self):
        points = ((0, 0), (1, 1))
        self.c.set_physical_laser_off(True)
        with self.assertRaises(GuardError):
            self.c.frame(points)
        self.home()
        for bad_points, feed in ((((0, 0), (366, 1)), 600), (((0, 0),), 600), (points, 60)):
            with self.subTest(points=bad_points, feed=feed), self.assertRaises(GuardError):
                self.c.frame(bad_points, feed)

    def test_frame_click_during_status_poll_is_deferred_once(self):
        self.home()
        self.clock.time += 0.5
        self.c.tick()
        self.assertTrue(self.c._status_poll_pending())
        self.t.rx.clear()
        points = ((1, 1), (2, 1))
        self.c.frame(points, 300)
        self.assertEqual(self.c.deferred_motion, ("frame", points, 300))
        self.c.receive("<Idle|MPos:-288,-301,0|FS:0,0>")
        self.assertEqual(self.c.phase, "frame-command")
        self.pump(30)
        self.assertEqual(self.c.phase, "idle")

    def test_frame_wrong_endpoint_faults_and_clears_home(self):
        self.home()
        self.c.frame(((1, 1), (2, 1)), 600)
        self.pump(1)
        self.c.receive("ok")
        self.c.receive("<Idle|MPos:-280,-290,0|FS:0,0>")
        self.assertFalse(self.c.connected)
        self.assertIsNone(self.c.origin)
        self.assertIn(b"\x18", self.t.writes)

    def test_disconnect_cancels_frame_but_optional_ack_does_not(self):
        self.home()
        self.c.frame(((1, 1), (2, 1)), 180)
        self.c.disconnect()
        self.assertIn(b"\x85", self.t.writes)
        self.setUp()
        self.home()
        self.c.frame(((1, 1), (2, 1)), 180)
        self.c.set_physical_laser_off(False)
        self.assertTrue(self.c.connected)

    def test_timeout_of_command_disconnects(self):
        self.c.query("$I")
        self.pump(1)
        self.t.rx.clear()
        self.clock.time += 6
        self.c.tick()
        self.assertFalse(self.c.connected)

    def test_disconnect_and_reconnect_clear_home(self):
        self.home()
        self.c.disconnect()
        self.c.attach(Simulator(self.clock), settle=0)
        self.pump(60)
        self.assertTrue(self.c.ready)
        self.assertIsNone(self.c.origin)
        self.assertFalse(self.c.physical_laser_off)

    def test_alarm_error_reset_invalidate(self):
        for report in ("ALARM:1", "error:20", "Grbl 1.1h ['$' for help]", "<Alarm|MPos:0,0,0|FS:0,0>"):
            with self.subTest(report=report):
                self.setUp()
                self.home()
                self.c.receive(report)
                self.assertFalse(self.c.connected)
                self.assertIsNone(self.c.origin)

    def test_stop_resets_and_disconnects(self):
        self.home()
        self.c.stop()
        self.assertIn(b"\x18", self.t.writes)
        self.assertFalse(self.c.connected)

    def test_disconnect_during_homing_uses_reset(self):
        self.c.set_physical_laser_off(True)
        self.c.home()
        self.pump(1)
        self.c.disconnect()
        self.assertIn(b"\x18", self.t.writes)

    def test_optional_physical_ack_does_not_control_motion(self):
        self.home()
        self.c.jog("X", 1)
        self.c.set_physical_laser_off(False)
        self.assertTrue(self.c.connected)

    def test_generated_job_streams_directly_and_finishes_disarmed(self):
        self.home()
        lines = ("G21", "G90", "M5", "S0", "G53 G0 X-287.000 Y-300.000",
                 "M4 S250", "G53 G1 X-278.000 Y-300.000 F6000", "M5", "S0")
        self.c.run_job(lines)
        self.pump(80)
        self.assertEqual(self.c.phase, "idle", self.c.message)
        self.assertEqual(self.t.power, 0)
        self.assertIn(b"M4 S250\n", self.t.writes)
        self.assertIn(b"G53 G1 X-278.000 Y-300.000 F6000\n", self.t.writes)
        self.assertIn("Job complete", self.c.message)

    def test_job_rejects_unsupported_out_of_bounds_and_excess_feed(self):
        self.home()
        endings = ("M5", "S0")
        for lines in (("M3 S1000", *endings),
                      ("G53 G1 X100.000 Y100.000 F6000", *endings),
                      ("G53 G1 X-287.000 Y-300.000 F20001", *endings)):
            with self.subTest(lines=lines), self.assertRaises(GuardError):
                self.c.run_job(lines)

    def test_job_pause_and_resume_use_realtime_controls(self):
        self.home()
        lines = ("G21", "G90", "M5", "S0", "G53 G0 X-287.000 Y-300.000",
                 "M4 S100", "G53 G1 X-280.000 Y-300.000 F3000", "M5", "S0")
        self.c.run_job(lines)
        self.c.tick()
        self.c.pause_job()
        self.pump(2)
        self.assertTrue(self.c.job_paused)
        self.assertIn(b"!", self.t.writes)
        self.c.resume_job()
        self.assertIn(b"~", self.t.writes)
        self.pump(80)
        self.assertEqual(self.c.phase, "idle")

    def test_long_pause_keeps_the_session_alive_and_resumes(self):
        """A held machine never finishes the block the next ack is waiting on."""
        self.home()
        ox, oy = self.c.origin
        lines = ["G21", "G90", "M5", "S0"]
        for index in range(20):
            lines += [f"G53 G0 X{ox + 10:.3f} Y{oy + 10 + index:.3f}", "M4 S200",
                      f"G53 G1 X{ox + 30:.3f} Y{oy + 10 + index:.3f} F60", "M5", "S0"]
        lines += ["M5", "S0"]
        self.c.run_job(lines)
        self.pump(2000)  # Stream until the planner is full and acks lag.
        self.assertTrue(self.c.pending, "the planner should be holding an ack back")
        self.c.pause_job()
        self.pump(12000)  # Ten minutes held: nothing can arrive from the machine.
        self.assertTrue(self.c.connected, self.c.message)
        self.assertTrue(self.c.job_paused)
        self.assertTrue(self.c.phase.startswith("job"), self.c.phase)
        self.c.resume_job()
        for _ in range(200000):
            self.pump(1)
            if self.c.phase == "idle" or not self.c.connected:
                break
        self.assertEqual(self.c.phase, "idle", self.c.message)
        self.assertEqual(self.c.job_done, self.c.job_total)
        self.assertEqual(self.t.power, 0)

    def test_a_slow_job_outlasts_the_planner_without_a_false_timeout(self):
        """A full planner delays an acknowledgement far past any fixed timeout."""
        self.home()
        ox, oy = self.c.origin
        lines = ["G21", "G90", "M5", "S0"]
        for index in range(20):  # More passes than GRBL has planner blocks.
            lines += [f"G53 G0 X{ox + 10:.3f} Y{oy + 10 + index:.3f}", "M4 S200",
                      f"G53 G1 X{ox + 30:.3f} Y{oy + 10 + index:.3f} F60", "M5", "S0"]
        lines += ["M5", "S0"]
        self.c.run_job(lines)
        for _ in range(200000):
            self.pump(1)
            if self.c.phase == "idle" or not self.c.connected:
                break
        self.assertTrue(self.c.connected, self.c.message)
        self.assertEqual(self.c.phase, "idle", self.c.message)
        self.assertEqual(self.c.job_done, self.c.job_total)
        self.assertEqual(self.t.power, 0)
        # 20 mm at 60 mm/min is 20 s a pass: the job really did outlast the
        # ten seconds a fixed per-command timeout used to allow.
        self.assertGreater(self.clock.time, 20 * 20)

    def test_job_stops_on_excess_reported_power_or_wrong_final_position(self):
        self.home()
        self.c.phase = "job-command"
        self.c.job_expected_power = 100
        self.c.receive("<Run|MPos:-288,-301,0|FS:3000,200|A:S>")
        self.assertFalse(self.c.connected)
        self.setUp()
        self.home()
        self.c.phase = "job-status"
        self.c.job_final_target = (-280, -300)
        self.c.receive("<Idle|MPos:-281,-300,0|FS:0,0>")
        self.assertFalse(self.c.connected)
        self.assertIn(b"\x18", self.t.writes)

    def test_unexpected_motion_invalidates_home(self):
        self.home()
        self.c.receive("<Idle|MPos:-280,-301,0|FS:0,0>")
        self.assertFalse(self.c.connected)
        self.assertIn(b"!", self.t.writes)
        self.assertIn(b"\x85", self.t.writes)
        self.assertIn(b"\x18", self.t.writes)

    def test_run_report_while_idle_requests_stop(self):
        self.home()
        self.c.receive("<Run|MPos:-287,-301,0|FS:60,0>")
        self.assertFalse(self.c.connected)
        self.assertIn(b"\x18", self.t.writes)

    def test_direct_status_query_failure_stops_session(self):
        self.home()
        self.t.closed = True
        self.c.query("?")
        self.pump(1)
        self.assertFalse(self.c.connected)

    def test_initial_alarm_diagnostics_skip_blocked_gcode(self):
        class AlarmSimulator(Simulator):
            def write(self, data):
                super().write(data)
                if data == b"?":
                    self.rx[-1] = self.rx[-1].replace(b"Idle", b"Alarm")
        self.c.disconnect()
        transport = AlarmSimulator(self.clock)
        self.c.attach(transport, settle=0)
        self.pump(60)
        self.assertTrue(self.c.ready, self.c.message)
        self.assertNotIn(b"M5 S0\n", transport.writes)
        self.assertEqual(self.c.status.state, "Alarm")

    def test_status_is_serialized_without_empty_lines(self):
        self.assertNotIn(b"?\n", self.t.writes)
        self.assertEqual(self.t.writes[:3], [b"$I\n", b"?", b"$$\n"])
        self.c.query("$I")
        self.pump(1)
        writes = list(self.t.writes)
        self.t.rx.clear()  # Firmware has not acknowledged the line command.
        self.clock.time += 0.5
        self.c.tick()
        self.assertEqual(self.t.writes, writes, "A poll overlapped an unacknowledged command")

    def test_status_waits_for_report_not_ok(self):
        self.c.query("?")
        self.pump(1)
        self.t.rx.clear()
        writes = list(self.t.writes)
        self.clock.time += 0.5
        self.c.tick()
        self.assertEqual(self.t.writes, writes)
        self.assertEqual(self.c.pending.text, "?")
        self.c.receive("<Idle|MPos:0,0,0|FS:0,0>")
        self.assertIsNone(self.c.pending)

    def test_unexpected_ack_cannot_complete_bare_status(self):
        self.c.query("?")
        self.pump(1)
        self.t.rx.clear()
        self.c.receive("ok")
        self.assertFalse(self.c.connected)

    def test_profile_mismatch_at_connect_cannot_arm(self):
        self.c.disconnect()
        transport = Simulator(self.clock)
        transport.fixture = transport.fixture.replace("$130=365.000", "$130=400.000")
        self.c.attach(transport, settle=0)
        self.pump(60)
        self.assertFalse(self.c.ready)
        self.c.set_physical_laser_off(True)
        with self.assertRaises(GuardError):
            self.c.home()

    def test_stop_attempts_reset_if_hold_write_fails(self):
        self.home()
        original = self.t.write
        def fail_hold(data):
            if data == b"!":
                raise OSError("hold failed")
            original(data)
        self.t.write = fail_hold
        self.c.stop()
        self.assertIn(b"\x18", self.t.writes)

    def test_nonzero_power_and_accessory_state_stop(self):
        for report in ("<Idle|MPos:0,0,0|FS:0,10>", "<Idle|MPos:0,0,0|FS:0,0|A:S>"):
            self.setUp()
            self.c.receive(report)
            self.assertFalse(self.c.connected)
            self.assertIn(b"\x18", self.t.writes)

    def test_profile_change_locks(self):
        self.home()
        self.c.receive("$130=400")
        self.assertFalse(self.c.connected)

    def test_inches_report_setting_blocks_motion(self):
        self.c.settings[13] = 1
        self.c.set_physical_laser_off(True)
        with self.assertRaises(GuardError):
            self.c.home()

    def test_missing_position_and_power_block(self):
        self.home()
        for report in ("<Idle|WPos:1,2,0|FS:0,0>", "<Idle|MPos:-288,-301,0>"):
            self.c.status = parse_status(report)
            with self.assertRaises(GuardError):
                self.c.jog("X", 1)

    def test_no_freeform_command_channel(self):
        for command in ("M3 S1000", "$J=G91 X20 F2000", "$20=1", "$X", "G92 X0", "$I\nM3"):
            with self.assertRaises(GuardError):
                self.c.query(command)

    def test_home_locked_alarm_can_home_without_unlock(self):
        self.c.status = parse_status("<Alarm|MPos:-288,-301,0|FS:0,0>")
        self.c.set_physical_laser_off(True)
        self.c.home()
        self.pump()
        self.assertEqual(self.c.phase, "home-confirm")
        self.assertNotIn(b"$X\n", self.t.writes)

    def test_fragmented_usb_reports(self):
        self.t.rx.clear()
        for fragment in (b"<Idle|MP", b"os:-288,-301,0|FS:0,0>\r", b"\n"):
            self.t.rx.append(fragment)
        self.pump(3)
        self.assertEqual(self.c.status.machine, (-288, -301))

    def test_malformed_status_fails_closed(self):
        self.home()
        self.t.rx.clear()
        self.t.rx.append(b"<Idle|MPos:nan,0,0|FS:0,0>\n")
        self.pump(1)
        self.assertFalse(self.c.connected)

    def test_transport_write_failure_clears_session(self):
        self.home()
        self.t.closed = True
        self.c.jog("X", 1)
        self.pump(1)
        self.assertFalse(self.c.connected)
        self.assertIsNone(self.c.origin)


class ParserTests(unittest.TestCase):
    def test_observed_fixture(self):
        lines = (Path(__file__).parents[1] / "atomstack/fixtures/observed.txt").read_text().splitlines()
        settings = dict(filter(None, (parse_setting(x) for x in lines)))
        self.assertEqual({k: settings[k] for k in EXPECTED}, EXPECTED)
        status = parse_status(lines[-1])
        self.assertEqual(status.machine, (0, 0))
        self.assertEqual(status.fields["APP"], "51")
        self.assertEqual(status.fields["USB"], "0")

    def test_wpos_requires_same_report_wco(self):
        self.assertIsNone(parse_status("<Idle|WPos:1,2,0|FS:0,0>").machine)
        self.assertEqual(parse_status("<Idle|WPos:1,2,0|WCO:-288,-301,0>").machine, (-287, -299))

    def test_invalid_coordinates_rejected(self):
        for line in ("<Idle|MPos:nan,0>", "<Idle|MPos:inf,1>", "<Idle|MPos:1>", "<Idle|MPos:1,2|MPos:3,4>", "<Idle|MPos:1,2"):
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse_status(line)


class SerialAdapterTests(unittest.TestCase):
    @patch("serial.Serial")
    def test_115200_nonblocking_no_reset_toggle(self, serial_class):
        serial = serial_class.return_value
        transport = SerialTransport("COM_TEST")
        serial_class.assert_called_once_with(port=None, baudrate=115200, timeout=0, write_timeout=0.5)
        self.assertFalse(serial.dtr)
        self.assertFalse(serial.rts)
        self.assertEqual(serial.port, "COM_TEST")
        serial.open.assert_called_once()
        serial.write.return_value = 3
        transport.write(b"$I\n")
        serial.write.assert_called_once_with(b"$I\n")
        serial.in_waiting = 2
        serial.read.return_value = b"ok"
        self.assertEqual(transport.read(), b"ok")
        serial.read.assert_called_once_with(2)
        transport.close()
        serial.close.assert_called_once()

    @patch("serial.Serial")
    def test_partial_usb_write_is_an_error(self, serial_class):
        serial_class.return_value.write.return_value = 1
        transport = SerialTransport("COM_TEST")
        with self.assertRaises(OSError):
            transport.write(b"$I\n")


if __name__ == "__main__":
    unittest.main()
