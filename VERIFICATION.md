# Milestone verification · 2026-09-04

- **45 automated tests passed**, including serial adapter configuration and partial
  writes, observed profile, fragmented reports, finite coordinates, homing gates,
  bounds, one outstanding jog, stale data, wrong endpoints, unexpected motion,
  alarm/reset/disconnect, laser state, and independent stop attempts.
- Native UI simulator checks passed: diagnostics, disabled motion before optical
  acknowledgment/home, home confirmation, positive X exactly 1 mm, and stop.
- Default 1100 × 840 and minimum 1000 × 800 native captures reviewed. Recovery
  text and all motion controls remain visible. Earlier screen-compositor artifacts
  were eliminated by capturing the native window directly.
- Independent review scored both requested fixes resolved: stop/reset on unexpected
  motion while idle, and clean minimum-size evidence. Review was bounded to those
  findings; it is not a hardware safety certification.
- Standalone Windows x64 executable launched with its bundled simulator/fixture,
  showed verified diagnostics and locked motion, and exited normally. See
  `executable-verification.txt` for its SHA-256 and `preview-executable.png`.

**0.1.1 hardware startup verified.** The user's reported `error:1` was reproduced
on the physical V1.055 controller on COM3: adjacent `$I\n` and bare `?` writes cause
the following `$$\n` to fail. Serialized `?\n` plus its acknowledgement resolves it.
The corrected controller passed live initialization and repeated polling, reported
Idle, MPos (0, 0), zero power, and the expected machine settings. Diagnostics and
`M5 S0` were the only commands in the successful application check. No physical
homing or jog was issued. Firmware `$J` support, physical direction, homing position,
available travel and stop response remain unverified. See hardware-startup-verification.txt.

The executable is a local development build and is not code-signed. Source and
rebuild instructions are included. Windows UI was checked at this machine's current
display scaling; other DPI settings and small displays have not been verified.

## Version 0.1.2 connection work

The terminal confirmed that bare `?` requests are supported when each request waits
for its response. A read-only probe completed 35 status requests, five settings
requests, and parameter/modal queries without errors. Polls now complete on a status
report instead of sending an unnecessary newline and waiting for an empty-command
acknowledgement. Already-verified M5 S0 is not redundantly resent at startup.

The new terminal frontend maintained a ready connection on COM3 for 30 seconds.
The delivered executable was then launched with `--port COM3 --report
live-connection.json`, giving the terminal visibility into the actual running UI
without taking over its serial port. See `live-executable-verification.json` for
the final observation result and `preview-live.png` for the native window capture.

An early monitor assertion assumed the physical-disconnection checkbox and home
state would stay unchanged. Those UI states changed while the app remained ready,
so the connection monitor was corrected to observe USB health without changing
user controls or treating normal use as a connection failure. Subsequent reports
showed a confirmed home and completed jogs; the test harness issued no motion.

The reported audible beeping was not directly measured. This revision removes
unnecessary empty-command traffic; verified results concern the connection, live
reports, and absence of firmware errors, not an acoustic measurement.

## Version 0.2.0

- 56 automated tests pass. New coverage includes guarded jog speeds/distances,
  endpoint bounds for larger steps, deferring a click behind a status request,
  shape/process validation, machine-coordinate offsets, and G-code disarming.
- A 50-sample, two-second UI check confirmed that periodic status polling no longer
  toggles Home, jog, or diagnostic button states. See `blink-verification.txt`.
- Native simulator checks created rectangle and circle geometry, changed speed,
  power, and passes, rendered the editor, and exported example G-code without
  transmitting geometry commands. See `geometry-verification.txt`,
  `preview-geometry.png`, and `example-geometry.gcode`.
- The exact packaged executable launched and exited normally in simulator mode.
  Its SHA-256 is recorded in `executable-verification.txt`.
- Superseded executables were moved out of the delivery folder so an older startup
  sender cannot be opened accidentally. At that point the delivery contained only
  the 0.2.0 build; the same cleanup is repeated for each replacement build.

## Version 0.2.1

- 60 automated tests pass. New tests verify the four selectable log levels and that
  normal USB traffic is absent from the default warnings/errors view.
- The main UI defaults to a healthy “No warnings or errors” empty state. Firmware
  and settings queries are hidden under **Technical details**.
- Movement labels now say Left, Right, Back, and Front. Distance includes `mm`;
  speeds use Very slow through Very fast with their exact mm/min values.
- Polling stability passes across 50 UI samples. Geometry and main-window native
  screenshots were recaptured after the interface changes.

## Version 0.3.0

- 67 automated tests pass. Frame-specific checks cover the five bounded absolute
  jog segments, confirmed-home and laser-off gates, deferred clicks, invalid speed
  and geometry, wrong endpoint failure, disconnect, and removal of the physical
  laser acknowledgement.
- The geometry simulator smoke test traces the dashed orange 2 mm outline and
  confirms that Frame sends no `M3`, `M4`, or `S` laser word.
- Physical USB connection is checked with the packaged executable. Physical Frame
  remains intentionally untested because the test harness cannot establish that
  optical power is disconnected.

## Version 0.4.0

- 73 automated tests pass. Added checks cover vector text contours and export,
  single-line input safety, burn-test speed/power assignment and bed overflow, and
  material-library persistence, deletion, validation, and corrupt-file recovery.
- The native geometry smoke test creates rectangle, circle, Arial text, and a 3 × 2
  burn-test grid; exports all of them; renders the combined Frame outline; and then
  completes that Frame in the simulator without `M3`, `M4`, or `S` words.
- Burn-test generation and G-code export are tested in software. This release does
  not automatically run an exported job or physically fire the laser.

## Version 0.5.0

- 77 automated tests pass, including generated-command allowlisting, direct job
  streaming, final M5/S0 state, live feed-limit rejection, and pause/resume controls.
- The combined geometry smoke test sends vector text and a burn-test grid through the
  simulator, observes M4 power commands, and verifies completion at zero power.
- The packaged executable receives a read-only physical USB connection soak. Automated
  tests do not fire the physical laser or run a physical job.

## Version 0.6.0

- 80 automated tests pass. Click-to-jog coverage checks absolute origin conversion,
  the absence of laser words, invalid coordinates, the live feed ceiling, and safe
  deferral behind the controller's status transaction.
- The native UI smoke test clicks the rendered bed at app X100/Y50 and verifies that
  the simulator reaches exactly X100/Y50 before another motion becomes available.

## Version 0.7.0

- The native geometry smoke test uses the editor embedded in the main application,
  creates all geometry types, frames them, and streams the simulated job without
  opening a second workspace window.
- Native captures verify the persistent connection/status/STOP header and both the
  Design & Send and Machine & Jog tabs at the supported window size.

## Version 0.8.0

- The native UI smoke test verifies machine-map zoom and reset, the gray design object
  list, a guarded move to the selected object's top-right point, and three arrow presses
  accumulating into one final target.
- Native captures verify the overlay, target controls, selected position, and full-bed
  layout at both the normal and minimum supported window sizes.

## Version 0.9.0

- The native geometry smoke test creates the existing geometry fixture, duplicates it,
  undoes/redoes it, aligns and nudges an object, moves and resizes through canvas events,
  zooms, pans, fits the bed, and restores the exact starting geometry through Undo.
- The same test saves an editable `.atomdesign` file, clears the workspace, reopens the
  project, and verifies an exact shape round trip before Frame and direct job streaming.
- Machine and Design arrow keys are routed by the active tab so design nudging can never
  become a machine jog.
- Horizontal and vertical line geometry is covered at model and native canvas-event
  levels; a zero-length line remains invalid.

## Version 0.10.0

- Preview model tests verify ordered rapid/burn segments, distance totals, duration,
  maximum power, empty documents, and invalid start positions.
- The native geometry smoke test opens the preview over the complete geometry fixture,
  verifies non-zero rapid and burn metrics, scrubs to 50%, and captures the rendered
  preview before the existing Frame and direct-send checks.

## Version 0.11.0

- Native smoke coverage selects three heterogeneous objects and verifies group
  duplicate/undo/redo, bounded nudge, combined alignment, equal-center distribution,
  canvas drag with preserved offsets, group delete, and complete restoration by Undo.
- A dedicated native capture verifies extended list selection, selected-object styling,
  the combined dashed boundary, and an explicit selected-count inspector state.
