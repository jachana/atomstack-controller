import unittest
from atomstack.geometry import Shape, shape_bounds
from atomstack.placement import place_shapes, reachable_x, ANCHORS

class PlacementTests(unittest.TestCase):
 def test_every_anchor_preserves_group_spacing(self):
  shapes=[Shape('rectangle',-100,-100,20,20),Shape('circle',-70,-100,10,10)]
  for name,(ax,ay) in ANCHORS.items():
   result=place_shapes(shapes,name,(100,100),-12.5)
   bounds=[shape_bounds(s) for s in result]
   left,bottom=min(b[0] for b in bounds),min(b[1] for b in bounds)
   right,top=max(b[2] for b in bounds),max(b[3] for b in bounds)
   self.assertAlmostEqual(left+ax*(right-left),100)
   self.assertAlmostEqual(bottom+ay*(top-bottom),100)
   self.assertEqual(result[1].x-result[0].x,30)
  self.assertEqual(shapes[0].x,-100)
 def test_offset_edges_and_invalid_targets(self):
  self.assertEqual(reachable_x(-12.5),(0,352.5))
  self.assertEqual(reachable_x(12.5),(12.5,365))
  shape=Shape('rectangle',20,20,40,40)
  for target in ((350,20),(-1,20),(20,300),(float('nan'),20)):
   with self.assertRaises(ValueError): place_shapes([shape],'Bottom-left',target,-12.5)
 def test_rotated_shape_uses_actual_bounds(self):
  shape=Shape('rectangle',20,20,40,10,rotation=45)
  result=place_shapes([shape],'Bottom-left',(20,20),-12.5)[0]
  self.assertAlmostEqual(shape_bounds(result)[0],20)
  self.assertAlmostEqual(shape_bounds(result)[1],20)
