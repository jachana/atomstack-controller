"""Scan lines for engraving: what power each sweep carries, and where.

The picture is not the test. Every expectation here is a measurement of a
synthetic image whose greys are known, so a change in the arithmetic shows up as
a number rather than as a photograph that looks slightly different.
"""
import math
import unittest

import numpy as np

from atomstack.controller import Controller, GuardError
from atomstack.geometry import Document, Shape
from atomstack.raster import (MAX_INTERVAL, MIN_INTERVAL, decode, encode, engraving_grid,
                              engraving_metrics, quantise, scan_runs, shape_commands)
from atomstack.transports import Simulator


class Quantising(unittest.TestCase):
    def test_dark_burns_hardest_and_white_is_left_alone(self):
        grey = np.array([[0.0, 0.5, 1.0]], dtype=np.float32)
        power = quantise(grey, levels=16, min_power=100, max_power=800)
        self.assertEqual(int(power[0][2]), 0, "white must not fire the laser")
        self.assertGreater(int(power[0][0]), int(power[0][1]))
        self.assertLessEqual(int(power[0][0]), 800)
        self.assertGreaterEqual(int(power[0][1]), 100)

    def test_levels_are_the_only_values_that_come_out(self):
        grey = np.linspace(0, 1, 256, dtype=np.float32).reshape(1, 256)
        power = quantise(grey, levels=4, min_power=0, max_power=900)
        self.assertLessEqual(len(set(power.flatten().tolist())), 4)

    def test_impossible_settings_are_refused(self):
        grey = np.zeros((2, 2), dtype=np.float32)
        for bad in ({"levels": 1}, {"levels": 500}, {"min_power": 500, "max_power": 100},
                    {"max_power": 2000}):
            options = {"levels": 16, "min_power": 0, "max_power": 500}
            options.update(bad)
            with self.assertRaises(ValueError):
                quantise(grey, **options)


class Scanning(unittest.TestCase):
    def solid(self, value=0.0, shape=(10, 10)):
        return np.full(shape, value, dtype=np.float32)

    def test_a_solid_block_sweeps_every_line_edge_to_edge(self):
        power = quantise(self.solid(), levels=8, min_power=500, max_power=500)
        rows = scan_runs(power, box=(10, 20, 40, 20), interval=0.5)
        self.assertEqual(len(rows), 40)                      # 20 mm at 0.5 mm
        for y, runs in rows:
            self.assertEqual(len(runs), 1)
            start, end, level = runs[0]
            self.assertAlmostEqual(min(start, end), 10, places=6)
            self.assertAlmostEqual(max(start, end), 50, places=6)
            self.assertEqual(level, 500)
            self.assertTrue(20 <= y <= 40)

    def test_white_areas_are_skipped_rather_than_swept(self):
        grey = np.ones((4, 10), dtype=np.float32)
        grey[:, 4:6] = 0.0                                   # a dark stripe
        power = quantise(grey, levels=8, min_power=400, max_power=400)
        rows = scan_runs(power, box=(0, 0, 10, 4), interval=1.0)
        for _, runs in rows:
            self.assertEqual(len(runs), 1)
            start, end, _ = runs[0]
            self.assertAlmostEqual(min(start, end), 4, places=6)
            self.assertAlmostEqual(max(start, end), 6, places=6)

    def test_alternate_lines_run_the_other_way(self):
        power = quantise(self.solid(), levels=8, min_power=300, max_power=300)
        rows = scan_runs(power, box=(0, 0, 10, 5), interval=1.0, bidirectional=True)
        directions = [runs[0][1] > runs[0][0] for _, runs in rows]
        self.assertEqual(directions[:4], [True, False, True, False])
        straight = scan_runs(power, box=(0, 0, 10, 5), interval=1.0, bidirectional=False)
        self.assertTrue(all(runs[0][1] > runs[0][0] for _, runs in straight))

    def test_a_blank_image_and_impossible_intervals_are_refused(self):
        blank = quantise(np.ones((4, 4), dtype=np.float32), levels=4,
                         min_power=0, max_power=500)
        with self.assertRaises(ValueError):
            scan_runs(blank, box=(0, 0, 10, 10), interval=0.5)
        solid = quantise(self.solid(), levels=4, min_power=500, max_power=500)
        for interval in (MIN_INTERVAL / 2, MAX_INTERVAL * 2):
            with self.assertRaises(ValueError):
                scan_runs(solid, box=(0, 0, 10, 10), interval=interval)
        with self.assertRaises(ValueError):
            scan_runs(solid, box=(0, 0, 0, 10), interval=0.5)

    def test_a_job_bigger_than_the_limit_says_so(self):
        noise = np.random.default_rng(3).random((400, 400)).astype(np.float32)
        power = quantise(noise, levels=64, min_power=10, max_power=900)
        with self.assertRaises(ValueError) as caught:
            scan_runs(power, box=(0, 0, 100, 100), interval=0.25, max_runs=500)
        self.assertIn("moves to engrave", str(caught.exception))


class Grid(unittest.TestCase):
    def test_the_image_is_resampled_to_one_sample_per_interval(self):
        grey = np.zeros((900, 600), dtype=np.float32)
        grid = engraving_grid(grey, box=(0, 0, 60, 90), interval=0.5)
        self.assertEqual(grid.shape, (180, 120))     # 90/0.5 rows, 60/0.5 columns

    def test_metrics_measure_the_sweep_and_the_time_it_takes(self):
        power = quantise(np.zeros((10, 10), dtype=np.float32), levels=4,
                         min_power=500, max_power=500)
        rows = scan_runs(power, box=(0, 0, 60, 10), interval=1.0)
        metrics = engraving_metrics(rows, speed=3000)
        self.assertEqual(metrics["rows"], 10)
        self.assertAlmostEqual(metrics["burn_distance"], 600, places=6)
        self.assertAlmostEqual(metrics["seconds"], 600 / 3000 * 60, places=6)



def ramp(size=32):
    return np.linspace(0, 1, size, dtype=np.float32)[None, :].repeat(size, 0)


class StoredWithTheDesign(unittest.TestCase):
    def test_the_picture_survives_being_saved_and_reopened(self):
        original = ramp()
        shape = Shape("raster", 10, 10, 30, 30, image=encode(original), interval=1.0)
        document = Document()
        document.add(shape)
        import json
        reloaded = Document.from_payload(json.loads(json.dumps(document.to_payload())))
        restored = decode(reloaded.shapes[0].image)
        self.assertEqual(restored.shape, original.shape)
        self.assertLess(float(np.abs(restored - original).max()), 0.01)

    def test_an_engraving_without_its_picture_is_refused(self):
        with self.assertRaises(ValueError):
            Shape("raster", 10, 10, 30, 30).validated()


class AsAJob(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.controller = Controller(self.clock)
        self.transport = Simulator(self.clock)
        self.controller.attach(self.transport, settle=0)
        for _ in range(120):
            self.clock.time += 0.05
            self.controller.tick()
        self.assertEqual(self.controller.home_state, "Confirmed", self.controller.message)

    def engraving(self, **changes):
        values = dict(kind="raster", x=20, y=20, width=30, height=30,
                      speed=3000, power=500, interval=1.0, image=encode(ramp()))
        values.update(changes)
        document = Document()
        document.add(Shape(**values))
        return document

    def test_an_engraving_streams_and_finishes_disarmed(self):
        document = self.engraving()
        lines = document.gcode(self.controller.origin).splitlines()
        self.controller.run_job(lines)
        for _ in range(400000):
            self.clock.time += 0.05
            self.controller.tick()
            if self.controller.phase == "idle" or not self.controller.connected:
                break
        self.assertTrue(self.controller.connected, self.controller.message)
        self.assertEqual(self.controller.phase, "idle")
        self.assertEqual(self.transport.power, 0)
        self.assertEqual(self.controller.job_done, self.controller.job_total)

    def test_power_rides_on_the_move_and_is_bounded_by_the_machine(self):
        marks = [line for line in self.engraving().gcode(self.controller.origin).splitlines()
                 if line.startswith("G53 G1") and " S" in line]
        self.assertTrue(marks)
        for line in marks:
            self.assertLessEqual(int(line.rsplit(" S", 1)[1]), 500)
        # The controller refuses more than the machine says it can do.
        ceiling = self.controller.settings.get(30, 0)
        with self.assertRaises(GuardError):
            self.controller._validate_job_command(
                f"G53 G1 X{self.controller.origin[0]+10:.3f} "
                f"Y{self.controller.origin[1]+10:.3f} F3000 S{int(ceiling)+1}",
                self.controller.status.machine)
        # And refuses power on a rapid, which would mark while repositioning.
        with self.assertRaises(GuardError):
            self.controller._validate_job_command(
                f"G53 G0 X{self.controller.origin[0]+10:.3f} "
                f"Y{self.controller.origin[1]+10:.3f} S200",
                self.controller.status.machine)

    def test_a_blank_picture_is_refused_before_anything_is_sent(self):
        white = np.ones((16, 16), dtype=np.float32)
        document = self.engraving(image=encode(white))
        with self.assertRaises(ValueError):
            document.gcode(self.controller.origin)


class _Clock:
    def __init__(self):
        self.time = 0

    def __call__(self):
        return self.time


if __name__ == "__main__":
    unittest.main()
