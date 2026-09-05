"""Canvas behaviour driven against a real Tk window in demo mode.

These tests build the actual App, so they cover the wiring between the widgets
and the coordinate helpers rather than the helpers alone. Nothing here opens a
serial port, and every motion request is expected to be refused because no home
has been confirmed.

Tk does not survive repeated create/destroy cycles inside one interpreter, so the
whole class shares one root and one App, and setUp returns that App to a known
state instead of rebuilding it. Skipped when Tk cannot open a display.
"""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    import tkinter as tk
except ImportError:
    tk = None

from atomstack.geometry import BED_X, BED_Y, Shape, shape_bounds


def fake_event(x, y):
    """Tk delivers objects with .x/.y; the canvas code needs nothing else."""
    return types.SimpleNamespace(x=x, y=y)


class CanvasBehaviour(unittest.TestCase):
    root = None
    app = None

    @classmethod
    def setUpClass(cls):
        if tk is None:
            raise unittest.SkipTest("tkinter is not installed")
        from atomstack.ui import App
        cls.state_folder = tempfile.TemporaryDirectory()
        cls.env_patch = mock.patch.dict("os.environ", {"ATOMSTACK_STATE_DIR": cls.state_folder.name})
        cls.env_patch.start()
        cls.save_prompt = mock.patch("atomstack.ui.messagebox.askyesnocancel", return_value=False)
        cls.save_prompt.start()
        try:
            cls.root = tk.Tk()
        except Exception as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")
        cls.app = App(cls.root, demo=True)
        cls.root.update()
        cls.app.canvas.update()
        cls.app.geometry.bed.update()

    @classmethod
    def tearDownClass(cls):
        cls.save_prompt.stop()
        cls.env_patch.stop()
        cls.state_folder.cleanup()
        if cls.app is not None:
            try:
                cls.app.controller.disconnect()
            except Exception:
                pass
        if cls.root is not None:
            cls.root.destroy()
            cls.root = None

    def setUp(self):
        editor = self.app.geometry
        editor.invalidate_preview()
        editor.document.shapes.clear()
        editor.document.layers.clear()
        editor.selected = None
        editor.selection.clear()
        editor.interaction = None
        editor.undo_stack.clear()
        editor.redo_stack.clear()
        editor.design_path = None
        editor.view_zoom = 1.0
        editor.pan_x = editor.pan_y = 0.0
        self.app.pending_key_target = None
        self.app.click_target = None
        self.app.view_zoom = 1.0
        self.app.controller.message = ""
        self.app.refresh_machine_objects()
        self.root.update()

    def rectangle(self, x=50.0, y=40.0, width=100.0, height=60.0):
        shape = Shape(kind="rectangle", x=x, y=y, width=width, height=height,
                      speed=1000, power=200)
        self.app.geometry.document.shapes.append(shape)
        self.app.refresh_machine_objects()
        return shape

    def test_demo_mode_opens_no_serial_port(self):
        self.assertEqual(type(self.app.controller.transport).__name__, "Simulator")

    def test_machine_bed_click_resolves_to_the_clicked_millimetre(self):
        view = self.app.machine_bed_transform()
        for point in ((0.0, 0.0), (200.0, 100.0), (BED_X, BED_Y)):
            px, py = view.to_canvas(*point)
            back = self.app.machine_bed_transform().to_bed(px, py)
            self.assertAlmostEqual(back[0], point[0], places=6)
            self.assertAlmostEqual(back[1], point[1], places=6)

    def test_click_outside_the_bed_is_refused_without_a_jog(self):
        view = self.app.machine_bed_transform()
        px = view.to_canvas(BED_X + 40, 10)[0]
        py = view.to_canvas(0, 10)[1]
        self.app.click_bed(fake_event(px, py))
        self.assertIn("outlined machine bed", self.app.controller.message)
        self.assertIsNone(self.app.controller.target)

    def test_arrow_keys_buffer_nothing_before_a_confirmed_home(self):
        self.app.arrow_jog("Right")
        self.assertIsNone(self.app.pending_key_target)

    def test_object_anchors_are_the_corners_of_the_selected_shape(self):
        from atomstack.viewport import anchor_point
        bounds = shape_bounds(self.rectangle())
        self.assertEqual(anchor_point(bounds, "BL"), (50.0, 40.0))
        self.assertEqual(anchor_point(bounds, "BR"), (150.0, 40.0))
        self.assertEqual(anchor_point(bounds, "TL"), (50.0, 100.0))
        self.assertEqual(anchor_point(bounds, "TR"), (150.0, 100.0))
        self.assertEqual(anchor_point(bounds, "C"), (100.0, 70.0))

    def test_object_jog_is_refused_before_a_confirmed_home(self):
        self.rectangle()
        self.app.machine_object.set(self.app.machine_object_box.cget("values")[0])
        self.app.controller.message = ""
        self.app.jog_object("TR")
        self.assertNotEqual(self.app.controller.message, "")
        self.assertIsNone(self.app.controller.target)

    def test_object_jog_without_a_selection_asks_for_one(self):
        self.app.jog_object("TR")
        self.assertIn("design object", self.app.controller.message)

    def test_design_canvas_hit_testing_finds_and_misses_a_shape(self):
        self.rectangle()
        editor = self.app.geometry
        view = editor.transform()
        self.assertEqual(editor.shape_at(fake_event(*view.to_canvas(100.0, 70.0))), 0)
        self.assertIsNone(editor.shape_at(fake_event(*view.to_canvas(300.0, 250.0))))

    def test_zoom_keeps_the_point_under_the_cursor_fixed(self):
        self.rectangle()
        editor = self.app.geometry
        cursor = fake_event(*editor.transform().to_canvas(100.0, 70.0))
        before = editor.to_bed(cursor, clamp=False)
        editor.zoom_by(1.2, cursor)
        after = editor.to_bed(cursor, clamp=False)
        self.assertAlmostEqual(after[0], before[0], places=4)
        self.assertAlmostEqual(after[1], before[1], places=4)

    def test_zoom_without_a_cursor_does_not_move_the_pan(self):
        editor = self.app.geometry
        editor.zoom_by(1.2)
        self.assertEqual((editor.pan_x, editor.pan_y), (0.0, 0.0))
        self.assertAlmostEqual(editor.view_zoom, 1.2)

    def test_both_canvases_redraw_without_error(self):
        self.rectangle(10.0, 10.0, 40.0, 30.0)
        self.app.draw_bed()
        self.app.geometry.draw()

    def test_rotated_selection_shows_exact_bounds_without_inactive_resize_handles(self):
        editor = self.app.geometry
        editor.document.shapes.append(Shape("rectangle", 10, 20, 40, 10, rotation=90))
        editor.set_selection((0,), 0)
        editor.refresh()
        self.assertEqual(editor.bounds_text.get(), "Transformed bounds · L 25 · B 5 · R 35 · T 45")
        self.assertEqual(editor.bed.find_withtag("resize-handle"), ())
        editor.document.shapes[0] = Shape("rectangle", 10, 20, 40, 10)
        editor.draw()
        self.assertEqual(len(editor.bed.find_withtag("resize-handle")), 4)

    def test_text_resize_handles_match_the_nominal_editable_box(self):
        editor = self.app.geometry
        text = Shape("text", 10, 20, 100, 30, text="I")
        editor.document.shapes.append(text)
        editor.set_selection((0,), 0)
        editor.refresh()
        view = editor.transform()
        right_bottom = view.to_canvas(text.x + text.width, text.y)
        self.assertEqual(editor.handle_at(fake_event(*right_bottom)), "BR")
        handle_centres = []
        for item in editor.bed.find_withtag("resize-handle"):
            left, top, right, bottom = editor.bed.coords(item)
            handle_centres.append(((left + right) / 2, (top + bottom) / 2))
        self.assertTrue(any(abs(x-right_bottom[0]) < 0.01 and abs(y-right_bottom[1]) < 0.01
                            for x, y in handle_centres))

    def test_design_save_uses_v2_and_v1_files_load_with_transform_defaults(self):
        editor = self.app.geometry
        with tempfile.TemporaryDirectory() as folder:
            save_path = Path(folder) / "current.atomdesign"
            editor.document.shapes.append(Shape("rectangle", 10, 20, 40, 10, rotation=90, mirror_x=True))
            editor.design_path = save_path
            editor.save_design()
            saved = json.loads(save_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], 4)
            self.assertEqual(saved["shapes"][0]["rotation"], 90)
            self.assertTrue(saved["shapes"][0]["mirror_x"])
            self.assertEqual(list(Path(folder).glob("*.tmp")), [],
                             "the temporary used for the atomic save must not be left behind")


            legacy_path = Path(folder) / "legacy.atomdesign"
            legacy_path.write_text(json.dumps({
                "format": "atomstack-design", "version": 1,
                "shapes": [{"kind": "rectangle", "x": 1, "y": 2, "width": 3, "height": 4}],
            }), encoding="utf-8")
            with mock.patch("atomstack.ui.filedialog.askopenfilename", return_value=str(legacy_path)):
                editor.open_design()
            self.assertEqual(editor.design_path, legacy_path)
            self.assertEqual(editor.document.shapes[0].rotation, 0)
            self.assertFalse(editor.document.shapes[0].mirror_x)

    def test_unsupported_project_does_not_change_document_path_or_history(self):
        editor = self.app.geometry
        editor.document.shapes.append(Shape("rectangle", 1, 2, 3, 4))
        editor.design_path = Path("kept.atomdesign")
        before = (tuple(editor.document.shapes), editor.design_path, len(editor.undo_stack))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "future.atomdesign"
            path.write_text(json.dumps({"format": "atomstack-design", "version": 99, "shapes": []}), encoding="utf-8")
            with mock.patch("atomstack.ui.filedialog.askopenfilename", return_value=str(path)):
                editor.open_design()
        self.assertEqual((tuple(editor.document.shapes), editor.design_path, len(editor.undo_stack)), before)
        self.assertIn("Unsupported Atomstack design version", editor.message.get())

    def test_undo_after_open_restores_both_geometry_and_project_path(self):
        editor = self.app.geometry
        original = Shape("rectangle", 1, 2, 3, 4)
        editor.document.shapes.append(original)
        original_path = Path("original.atomdesign")
        editor.design_path = original_path
        with tempfile.TemporaryDirectory() as folder:
            second_path = Path(folder) / "second.atomdesign"
            second_path.write_text(json.dumps({
                "format": "atomstack-design", "version": 2,
                "shapes": [{"kind": "line", "x": 10, "y": 20, "width": 5, "height": 0}],
            }), encoding="utf-8")
            with mock.patch("atomstack.ui.filedialog.askopenfilename", return_value=str(second_path)):
                editor.open_design()
            self.assertEqual(editor.design_path, second_path)
            editor.undo()
        self.assertEqual(editor.document.shapes, [original])
        self.assertEqual(editor.design_path, original_path)

    def test_design_change_closes_an_open_job_preview(self):
        editor = self.app.geometry
        self.rectangle(10, 20, 40, 30)
        editor.set_selection((0,), 0)
        editor.open_preview()
        self.root.update()
        preview = editor.preview_window
        self.assertIsNotNone(preview)
        editor.transform_selection("rotate_right")
        self.root.update()
        self.assertIsNone(editor.preview_window)
        self.assertFalse(preview.window.winfo_exists())

    def test_layer_assignment_toggle_and_undo_are_atomic(self):
        from atomstack.layer_ui import LayerWindow
        editor = self.app.geometry
        self.rectangle(10, 20, 40, 30)
        editor.set_selection((0,), 0)
        manager = LayerWindow(editor)
        editor.layer_window = manager
        try:
            manager.add()
            self.root.update()
            manager.assign()
            self.assertEqual(editor.document.shapes[0].layer, 'Engrave')
            self.assertEqual(editor.document.output_shapes()[0][1].speed, 3000)
            editor.fields['x'].set('12')
            editor.apply()
            self.assertEqual(editor.document.shapes[0].layer, 'Engrave')
            self.assertEqual(editor.document.shapes[0].x, 12)
            editor.open_preview()
            preview = editor.preview_window
            manager.enabled.set(False)
            manager.save()
            self.assertEqual(editor.document.output_shapes(), ())
            self.assertFalse(preview.window.winfo_exists())
            self.assertTrue(editor.preview_button.instate(['disabled']))
            editor.undo()
            self.assertTrue(editor.document.layers[0].enabled)
            editor.undo()  # numeric geometry edit
            editor.undo()  # assignment
            self.assertEqual(editor.document.shapes[0].layer, '')
        finally:
            manager.window.destroy()
            editor.layer_window = None

    def test_unsaved_cancel_and_successful_save(self):
        editor = self.app.geometry
        editor.saved_payload = editor.document.to_payload()
        self.rectangle()
        self.assertTrue(editor.dirty())
        with mock.patch("atomstack.ui.messagebox.askyesnocancel", return_value=None):
            self.assertFalse(editor.confirm_discard())
        with tempfile.TemporaryDirectory() as directory:
            editor.design_path = Path(directory)/"saved.atomdesign"
            editor.save_design()
            self.assertFalse(editor.dirty())
            editor.set_selection((0,),0)
            editor.nudge("Right")
            self.assertTrue(editor.dirty())
            editor.undo()
            self.assertFalse(editor.dirty())

    def test_imported_paths_survive_numeric_editing(self):
        from atomstack.geometry import path_shape
        editor = self.app.geometry
        shape = path_shape([[(10,10),(20,10),(20,20),(10,10)]])
        editor.document.shapes.append(shape)
        editor.set_selection((0,),0)
        editor.refresh()
        editor.fields["x"].set("30")
        editor.apply()
        self.assertEqual(editor.document.shapes[0].paths, shape.paths)
        self.assertEqual(editor.document.shapes[0].x,30)

    def test_recovery_restores_unsaved_document(self):
        from atomstack.projects import ProjectStore
        from atomstack.geometry import Document
        editor = self.app.geometry
        with tempfile.TemporaryDirectory() as directory:
            source = ProjectStore(directory)
            document = Document()
            document.add(Shape("rectangle",10,10,20,10))
            source.autosave(document.to_payload(),None)
            original_store = editor.project_store
            try:
                editor.project_store = ProjectStore(directory)
                editor.recover_design()
                self.assertEqual(editor.document.shapes,document.shapes)
                self.assertTrue(editor.dirty())
                self.assertTrue(source.recovery.exists())
            finally:
                editor.project_store = original_store


if __name__ == "__main__":
    unittest.main()
