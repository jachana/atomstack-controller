# Product roadmap: toward LightBurn-quality daily use

> Superseded by GOALS.md, which carries the current scope and the verified
> parity table. The delivery sequence below is out of date: rotation and
> mirroring shipped and are still listed as pending.

The target is workflow quality comparable to LightBurn for this Atomstack X10/A10,
while keeping the controller deliberately narrower and safer around the observed
vendor firmware.

## Working now

- Native USB connection, profile verification, normalized bottom-left coordinates.
- Guarded Home, Jog, Frame, direct job streaming, pause/resume, and STOP/RESET.
- Rectangles, ellipses, lines, vector text, burn tests, material presets.
- Direct manipulation, exact values, undo/redo, snapping, alignment, layers/order,
  zoom/pan/fit, editable project files, and machine-map object targets.

## Delivery sequence

1. Job preview and time/distance estimates, including rapid moves and power display.
2. Multi-selection, group movement, distribution, and selection-aware alignment.
3. Rotation and mirroring with transformed bounds and exact output paths.
4. SVG import with curves flattened at a controlled tolerance; then DXF import.
5. Named cut layers with shared speed/power/pass settings and output toggles.
6. Project dirty state, autosave, crash recovery, recent files, and migration tests.
7. Richer text layout, kerning controls, path text, and welding/boolean operations.
8. Job-origin modes, array copies, rotary-device model, and repeatable production runs.
9. Calibration tools, focus/material tests, device profiles, and guided first-run setup.
10. Long physical soak, fault-injection, recovery, and operator usability passes.

Every release must keep simulation coverage, packaged-executable verification, and a
read-only real-controller soak. Automated checks never home, jog, Frame, or fire the
physical machine.
