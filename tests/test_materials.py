import json
import tempfile
import unittest
from pathlib import Path

from atomstack.materials import MaterialLibrary, MaterialPreset


class MaterialLibraryTests(unittest.TestCase):
    def test_save_reload_apply_and_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "materials.json"
            library = MaterialLibrary(path)
            library.save(MaterialPreset("Birch mark", 1200, 280, 2))
            loaded = MaterialLibrary(path)
            self.assertEqual(loaded.names, ("Birch mark",))
            self.assertEqual(loaded.get("Birch mark").power, 280)
            loaded.delete("Birch mark")
            self.assertEqual(MaterialLibrary(path).names, ())

    def test_rejects_bad_presets_and_corrupt_file_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "materials.json"
            library = MaterialLibrary(path)
            for preset in (MaterialPreset("", 1000, 100, 1), MaterialPreset("x", 59, 100, 1),
                           MaterialPreset("x", 20001, 100, 1), MaterialPreset("x", 1000, 1001, 1), MaterialPreset("x", 1000, 100, 0)):
                with self.assertRaises(ValueError):
                    library.save(preset)
            path.write_text("not json", encoding="utf-8")
            broken = MaterialLibrary(path)
            self.assertEqual(broken.names, ())
            self.assertIsNotNone(broken.load_error)


if __name__ == "__main__":
    unittest.main()
