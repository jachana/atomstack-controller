"""Cut order: shorter travel, without changing what gets cut or when.

Ordering is the one optimisation here that can make a job wrong rather than
merely slow, so these tests pin the two rules that keep it safe: a layer's
sequence is never crossed, and an object enclosed by another is cut first.
"""
import json
import math
import random
import unittest

from atomstack.geometry import CutLayer, Document, Shape, entry_point, exit_point


def scattered(count=25, seed=4):
    random.seed(seed)
    document = Document()
    for _ in range(count):
        document.add(Shape("rectangle", random.uniform(5, 300), random.uniform(5, 250), 8, 8))
    return document


def travel(document):
    return document.job_metrics()["rapid_distance"]


class Ordering(unittest.TestCase):
    def test_optimising_shortens_travel_without_changing_the_cutting(self):
        document = scattered()
        document.optimise_order = False
        plain, plain_burn = travel(document), document.job_metrics()["burn_distance"]
        document.optimise_order = True
        optimised = travel(document)
        self.assertLess(optimised, plain * 0.6)
        # The same geometry is cut, only in a different order.
        self.assertAlmostEqual(document.job_metrics()["burn_distance"], plain_burn, places=6)
        self.assertEqual({index for index, _ in document.cut_plan()},
                         set(range(len(document.shapes))))

    def test_an_enclosed_object_is_cut_before_the_one_around_it(self):
        document = Document()
        document.add(Shape("rectangle", 10, 10, 100, 100))   # added first
        document.add(Shape("circle", 30, 30, 20, 20))        # holes within it
        document.add(Shape("circle", 70, 70, 20, 20))
        order = [index for index, _ in document.cut_plan()]
        self.assertEqual(order[-1], 0, "the surrounding outline must be cut last")

    def test_layers_keep_their_sequence_whatever_the_distances(self):
        document = Document()
        document.layers = [CutLayer("engrave"), CutLayer("cut")]
        # Interleave them so distance alone would mix the two layers.
        for x in (10, 200, 40, 240):
            document.add(Shape("rectangle", x, 10, 10, 10, layer="engrave"))
            document.add(Shape("rectangle", x, 60, 10, 10, layer="cut"))
        document.optimise_order = True
        layers = [shape.layer for _, shape in document.cut_plan()]
        self.assertEqual(layers, ["engrave"] * 4 + ["cut"] * 4)

    def test_turning_it_off_keeps_the_design_order(self):
        document = scattered()
        document.optimise_order = False
        self.assertEqual([index for index, _ in document.cut_plan()],
                         list(range(len(document.shapes))))

    def test_the_preview_shows_the_order_that_will_be_cut(self):
        document = scattered(12)
        document.optimise_order = True
        planned = [entry_point(shape) for _, shape in document.cut_plan()]
        previewed = [segment[2] for segment in document.preview_segments()
                     if segment[0] == "rapid"]
        for expected, shown in zip(planned, previewed):
            self.assertAlmostEqual(math.dist(expected, shown), 0.0, places=6)

    def test_a_very_large_job_keeps_design_order_rather_than_stalling(self):
        document = Document()
        for row in range(21):
            for column in range(21):  # 441 objects, past the ordering limit
                document.add(Shape("rectangle", 5 + column * 16, 5 + row * 14, 6, 6))
        document.optimise_order = True
        self.assertEqual([index for index, _ in document.cut_plan()],
                         list(range(len(document.shapes))))

    def test_the_setting_survives_saving_and_reopening(self):
        document = scattered(3)
        document.optimise_order = False
        reloaded = Document.from_payload(json.loads(json.dumps(document.to_payload())))
        self.assertFalse(reloaded.optimise_order)
        with self.assertRaises(ValueError):
            Document.from_payload({**document.to_payload(), "optimise_order": "yes"})


class PlanEndpoints(unittest.TestCase):
    def test_entry_and_exit_are_where_cutting_starts_and_stops(self):
        shape = Shape("rectangle", 10, 20, 30, 40)
        self.assertEqual(entry_point(shape), (10, 20))
        self.assertEqual(exit_point(shape), (10, 20))  # a closed outline returns


if __name__ == "__main__":
    unittest.main()
