import math
import unittest
from atomstack.controller import GuardError
from atomstack.geometry import Document, Shape
import test_controller


class BeamAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.session = test_controller.SessionTests()
        self.session.setUp()
        self.session.home()
        self.c = self.session.c
        self.c.set_beam_offset(-12.5)

    def document(self, x=30):
        doc = Document()
        doc.shapes.append(Shape('rectangle', x, 30, 10, 10, speed=3000, power=100))
        return doc

    def test_cut_matches_mark_and_finishes_laser_off(self):
        doc = self.document()
        before = doc.to_dict() if hasattr(doc, 'to_dict') else repr(doc.shapes)
        code = self.c.prepare_job(doc)
        origin = self.c.origin
        self.assertIn(f'G53 G0 X{origin[0]+42.5:.3f} Y{origin[1]+30:.3f}', code)
        self.c.run_job(code.splitlines())
        self.session.pump(1200)
        self.assertEqual(self.c.phase, 'idle', self.c.message)
        self.assertTrue(self.c.laser_off)
        self.assertEqual(self.c.job_expected_power, 0)
        self.assertEqual(before, doc.to_dict() if hasattr(doc, 'to_dict') else repr(doc.shapes))

    def test_right_edge_rejected_before_any_write(self):
        writes = list(self.session.t.writes)
        with self.assertRaisesRegex(GuardError, 'head travel'):
            self.c.prepare_job(self.document(345))
        self.assertEqual(writes, self.session.t.writes)

    def test_frame_follows_mark_without_cut_compensation(self):
        self.c.frame(((30,30),(40,30)), 600)
        self.assertEqual(self.c.target, (self.c.origin[0]+30,self.c.origin[1]+30))
        with self.assertRaises(GuardError):
            self.c.set_beam_offset(0)

    def test_positive_and_zero_offsets(self):
        for offset in (12.5, 0):
            self.c.set_beam_offset(offset)
            code = self.c.prepare_job(self.document())
            self.assertIn(f'G53 G0 X{self.c.origin[0]+30-offset:.3f}', code)

    def test_invalid_alignment_blocks_job(self):
        for value in (math.nan, math.inf, 51):
            with self.assertRaises(GuardError):
                self.c.set_beam_offset(value)
        self.c.alignment_valid = False
        with self.assertRaises(GuardError):
            self.c.prepare_job(self.document())
        with self.assertRaises(GuardError):
            self.c.run_job(('M5','S0'))
