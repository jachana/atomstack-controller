import unittest
from atomstack.viewport import proportional_size,resize_from_handle,rotated_handles

class RatioTests(unittest.TestCase):
 def test_numeric_dimensions(self):
  self.assertEqual(proportional_size(40,20,80,20),(80,40))
  self.assertEqual(proportional_size(40,20,40,10),(20,10))
  self.assertEqual(proportional_size(40,20,80,40),(80,40))
  with self.assertRaises(ValueError): proportional_size(40,20,80,30)
  with self.assertRaises(ValueError): proportional_size(0,20,80,30)
 def test_rotated_mirrored_resize_preserves_opposite_corner(self):
  for angle in (0,45,135,270):
   for mirrored in (False,True):
    box=(40,40,40,20)
    before=rotated_handles(box,angle,mirrored,False)
    result=resize_from_handle('TR',(130,120),box,angle,mirrored,False,minimum=.1,keep_ratio=True)
    after=rotated_handles((result['x'],result['y'],result['width'],result['height']),angle,mirrored,False)
    self.assertAlmostEqual(result['width']/result['height'],2)
    self.assertAlmostEqual(before['BL'][0],after['BL'][0])
    self.assertAlmostEqual(before['BL'][1],after['BL'][1])
