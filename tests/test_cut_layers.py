import unittest
from dataclasses import replace
from atomstack.geometry import CutLayer, Document, Shape


class LayerTests(unittest.TestCase):
    def document(self):
        doc = Document()
        doc.layers = [CutLayer('Engrave', 3000, 150, 2), CutLayer('Cut', 600, 700)]
        doc.add(Shape('rectangle', 100, 100, 20, 20, layer='Cut'))
        doc.add(Shape('line', 10, 10, 5, 0, layer='Engrave'))
        doc.add(Shape('line', 30, 30, 5, 0, speed=900, power=100))
        return doc

    def test_output_preview_and_gcode_share_layer_order_and_settings(self):
        doc = self.document()
        self.assertEqual([(i, s.speed, s.power, s.passes) for i, s in doc.output_shapes()],
                         [(1, 3000, 150, 2), (0, 600, 700, 1), (2, 900, 100, 1)])
        burns = [s for s in doc.preview_segments() if s[0] == 'burn']
        self.assertEqual([s[5] for s in burns], [1, 1, 0, 0, 0, 0, 2])
        code = doc.gcode((0, 0))
        self.assertLess(code.index('M4 S150'), code.index('M4 S700'))
        self.assertIn('F3000', code)
        self.assertEqual(code.count('M4 S150'), 2)

    def test_output_off_excludes_geometry_from_frame_preview_and_send(self):
        doc = self.document()
        doc.layers[1] = replace(doc.layers[1], enabled=False)
        self.assertEqual(doc.frame_points(0)[2], (35, 30))
        self.assertNotIn('M4 S700', doc.gcode((0, 0)))
        self.assertTrue(all(s[5] != 0 for s in doc.preview_segments()))
        self.assertEqual(doc.job_metrics()['max_power'], 150)

    def test_all_disabled_rejects_output(self):
        doc = self.document()
        doc.shapes = doc.shapes[:2]
        doc.layers = [replace(l, enabled=False) for l in doc.layers]
        for call in (doc.frame_points, doc.preview_segments, lambda: doc.gcode((0, 0))):
            with self.assertRaises(ValueError): call()

    def test_project_roundtrip_preserves_layers_assignments_order_and_switches(self):
        doc = self.document()
        doc.layers.reverse()
        doc.layers[0] = replace(doc.layers[0], enabled=False)
        loaded = Document.from_payload(doc.to_payload())
        self.assertEqual(loaded.layers, doc.layers)
        self.assertEqual(loaded.shapes, doc.shapes)
        self.assertEqual(loaded.gcode((0, 0)), doc.gcode((0, 0)))

    def test_broken_references_and_duplicate_names_rejected(self):
        doc = self.document()
        payload = doc.to_payload()
        payload['layers'] = []
        with self.assertRaises(ValueError): Document.from_payload(payload)
        doc.layers.append(doc.layers[0])
        with self.assertRaises(ValueError): doc.gcode((0, 0))

    def test_invalid_process_values_and_names_rejected(self):
        for changes in ({'name':'bad\nname'}, {'speed':3000.5}, {'power':1001},
                        {'passes':0}, {'enabled':'false'}):
            with self.assertRaises(ValueError): replace(CutLayer('test'), **changes).validated()
