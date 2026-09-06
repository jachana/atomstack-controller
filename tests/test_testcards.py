"""Test cards: a grid of settings, and the labels that say which was which.

The label assertions are the point of this file. A card whose settings are only
visible in the app is scrap once it is lifted off the machine, so the tests
check that the numbers reach the G-code, not just the screen.
"""
import unittest

from atomstack.geometry import BED_X, BED_Y, Document, Shape, shape_bounds
from atomstack.testcards import (card_size, cut_card, engraving_card, fits_bed,
                                 tonal_pattern)

SPEEDS = [600, 1200, 3000]
POWERS = [200, 400, 600, 800]


def kinds(shapes):
    counted = {}
    for shape in shapes:
        counted[shape.kind] = counted.get(shape.kind, 0) + 1
    return counted


class CutCard(unittest.TestCase):
    def test_a_cell_for_every_combination_and_a_label_for_every_axis(self):
        shapes = cut_card(20, 20, SPEEDS, POWERS)
        self.assertEqual(kinds(shapes)["rectangle"], len(SPEEDS) * len(POWERS))
        # One label per speed, one per power, plus the line saying which is which.
        self.assertEqual(kinds(shapes)["text"], len(SPEEDS) + len(POWERS) + 1)

    def test_each_cell_carries_the_settings_it_is_testing(self):
        shapes = cut_card(20, 20, SPEEDS, POWERS, passes=2)
        cells = [s for s in shapes if s.kind == "rectangle"]
        self.assertEqual({(s.speed, s.power) for s in cells},
                         {(s, p) for s in SPEEDS for p in POWERS})
        self.assertTrue(all(s.passes == 2 for s in cells))

    def test_the_labels_are_cut_at_their_own_setting_not_the_cell_s(self):
        # Half the point: a cell that fails to mark must still be identifiable.
        shapes = cut_card(20, 20, SPEEDS, [0, 1000], label_speed=2500, label_power=350)
        labels = [s for s in shapes if s.kind == "text"]
        self.assertTrue(labels)
        for label in labels:
            self.assertEqual((label.speed, label.power), (2500, 350))

    def test_the_numbers_reach_the_generated_job(self):
        document = Document()
        for shape in cut_card(20, 20, [600], [400]):
            document.add(shape)
        code = document.gcode((0, 0))
        # The labels are real geometry, so they appear as cutting moves.
        text_lines = [l for l in code.splitlines() if l.startswith("; ") and "text" in l]
        self.assertTrue(any("600" in line for line in text_lines))
        self.assertTrue(any("400" in line for line in text_lines))

    def test_impossible_cards_are_refused(self):
        for bad in ({"speeds": []}, {"powers": []},
                    {"speeds": list(range(100, 1300, 100))},   # eleven steps
                    {"speeds": [10]}, {"powers": [2000]},
                    {"cell_width": 2.0}, {"gap": -1.0}):
            options = {"speeds": SPEEDS, "powers": POWERS}
            options.update(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cut_card(20, 20, **options)


class EngravingCard(unittest.TestCase):
    def test_every_cell_is_an_engraving_carrying_its_own_picture(self):
        shapes = engraving_card(20, 20, SPEEDS, POWERS)
        cells = [s for s in shapes if s.kind == "raster"]
        self.assertEqual(len(cells), len(SPEEDS) * len(POWERS))
        self.assertTrue(all(cell.image for cell in cells))
        self.assertEqual({(s.speed, s.power) for s in cells},
                         {(s, p) for s in SPEEDS for p in POWERS})

    def test_the_card_saves_and_reopens_without_the_original_file(self):
        import json
        document = Document()
        for shape in engraving_card(20, 20, [600], [400]):
            document.add(shape)
        reloaded = Document.from_payload(json.loads(json.dumps(document.to_payload())))
        engraved = [s for s in reloaded.shapes if s.kind == "raster"]
        self.assertTrue(engraved and engraved[0].image)

    def test_the_pattern_covers_solid_through_to_blank(self):
        pattern = tonal_pattern()
        self.assertAlmostEqual(float(pattern.min()), 0.0, places=3)
        self.assertAlmostEqual(float(pattern.max()), 1.0, places=3)

    def test_a_card_is_engraved_at_the_speeds_it_advertises(self):
        document = Document()
        for shape in engraving_card(20, 20, [600], [400], cell_width=8, cell_height=8,
                                    interval=1.0):
            document.add(shape)
        code = document.gcode((0, 0))
        marks = [l for l in code.splitlines() if l.startswith("G53 G1") and " S" in l]
        self.assertTrue(marks)
        for line in marks:
            self.assertIn("F600", line)
            self.assertLessEqual(int(line.rsplit(" S", 1)[1]), 400)


class Layout(unittest.TestCase):
    def test_the_size_is_reported_before_the_card_is_made(self):
        from atomstack.testcards import LEFT_MARGIN
        width, height = card_size(SPEEDS, POWERS, 12, 12, 3)
        # The cells, plus the column of power labels beside them.
        self.assertAlmostEqual(width, 3 * 15 - 3 + LEFT_MARGIN)
        self.assertGreater(height, 4 * 15 - 3)          # room for the labels
        self.assertTrue(fits_bed(20, 20, SPEEDS, POWERS, 12, 12, 3))
        self.assertFalse(fits_bed(BED_X - 10, 20, SPEEDS, POWERS, 12, 12, 3))

    def test_the_fit_check_counts_the_labels_beside_the_grid(self):
        """The power labels hang left of the first column and are still cut."""
        from atomstack.testcards import LEFT_MARGIN
        self.assertFalse(fits_bed(5, 20, SPEEDS, POWERS, 14, 14, 4))
        self.assertTrue(fits_bed(LEFT_MARGIN + 1, 20, SPEEDS, POWERS, 14, 14, 4))
        # And what fits_bed accepts really does stay on the bed.
        shapes = cut_card(LEFT_MARGIN + 1, 20, SPEEDS, POWERS,
                          cell_width=14, cell_height=14, gap=4)
        self.assertGreaterEqual(min(shape_bounds(s)[0] for s in shapes), 0)

    def test_a_card_that_would_run_off_the_bed_still_builds_but_cannot_be_sent(self):
        # Geometry may sit off the bed; sending is what refuses it.
        shapes = cut_card(BED_X - 30, BED_Y - 30, SPEEDS, POWERS)
        document = Document()
        for shape in shapes:
            document.add(shape)
        self.assertTrue(document.offbed())
        with self.assertRaises(ValueError):
            document.gcode((0, 0))


if __name__ == "__main__":
    unittest.main()
