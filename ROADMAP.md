# Product roadmap: toward LightBurn-quality daily use

> GOALS.md carries the authoritative scope and verified parity table.

The target is workflow quality comparable to LightBurn for this Atomstack X10/A10,
while keeping the controller deliberately narrower and safer around the observed
vendor firmware.

## Working now

- Native USB connection, profile verification, normalized bottom-left coordinates.
- Guarded Home, Jog, Frame, direct job streaming, pause/resume, and STOP/RESET.
- Rectangles, ellipses, lines, vector text, burn tests, material presets.
- Direct manipulation, exact values, undo/redo, snapping, alignment, layers/order,
  zoom/pan/fit, editable project files, and machine-map object targets.
- Job preview, multi-selection, group operations, rotation, mirroring, and transformed
  bounds shared by the editor, Frame, preview, and direct output.

## Delivery sequence

1. Named cut layers with shared speed/power/pass settings and output toggles.
2. SVG import with curves flattened at a controlled tolerance; then DXF import.
3. Project dirty state, autosave, crash recovery, recent files, and migration tests.
4. Arbitrary-angle rotation controls and rotated drag handles.
5. Richer text layout, kerning controls, path text, and welding/boolean operations.
6. Job-origin modes, array copies, rotary-device model, and repeatable production runs.
7. Calibration tools, focus/material tests, device profiles, and guided first-run setup.
8. Long physical soak, fault-injection, recovery, and operator usability passes.

Every release must keep simulation coverage, packaged-executable verification, and a
read-only real-controller soak. Automated checks never home, jog, Frame, or fire the
physical machine.
