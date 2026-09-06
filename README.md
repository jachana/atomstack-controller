# Atomstack personal controller

A native Windows desktop controller for the user's existing Atomstack X10/A10
GRBL-like firmware. It includes guarded laser-off framing and direct generated-job
sending over USB.

## Run

Open **`AtomstackController.exe`** (current version 0.14.0) or the explicitly versioned
`AtomstackController-0.14.0.exe` from this delivery folder. Close older
versions first. No Python installation
is needed for the executable. Choose **Open simulator** to explore without hardware.

To run from source with Python 3.12 or newer:

```powershell
python -m pip install -r requirements.txt
python main.py
# Or, entirely simulated:
python main.py --demo
# Explicit port connection on launch, with a live diagnostic snapshot:
python main.py --port COM3 --report live-connection.json
# Diagnostic-only terminal (30 second observation, then closes the port):
python -m atomstack.terminal --port COM3 --seconds 30 --report terminal-report.json
# Interactive terminal: $I, $$, ?, $#, $G, status, quit. No motion commands.
python -m atomstack.terminal --port COM3 --seconds 3600 --interactive
```

## First USB session

1. Close other applications connected to the laser. Connect USB; click **Refresh
   ports**, select the COM port, then **Connect USB**. A single USB candidate is
   preselected. Explicit `--port COM3` connects on launch without clicking; it never
   homes or jogs automatically.
2. Diagnostics run at **115200 baud**: `$I`, `$$`, `?`, `$#`, `$G`. If `$G` already
   confirms `M5 S0` and reported power is zero, no extra laser-off command is sent.
   Otherwise the app disarms with `M5 S0` and verifies `$G`. No settings are written. The startup delay
   accommodates controllers that reset when their USB bridge is opened.
3. Inspect the live firmware, machine position, and log. The supplied fixture is
   evidence for the expected profile; it is never substituted for a live response.
4. Click **Home machine** and accept the movement prompt. This sends `$H` and uses
   the existing firmware's homing settings, including observed `$25=3000` seek
   and `$24=300` locate feed. The 60 mm/min restriction is for jogs, not homing.
5. After a successful response and fresh Idle position, physically check the head
   is at bottom-left, then click **Confirm bottom-left**. This records the measured
   machine coordinate as this session's origin.
6. Test **X +1** once. It should move 1 mm right. Test **Y +1** once; it should move
   1 mm toward the rear/top of the bed. Stop if either direction or distance is
   wrong. Do not continue jogging to find the travel limits.

Jog step is selectable: **0.1, 1, 5, or 10 mm**. Speed presets extend to **20,000
mm/min**, the live Y-axis maximum (`$111`) on the observed controller. Every click waits
for a matching endpoint before another motion can begin. A click arriving during
the periodic position query is held until that query completes; the controls no
longer blink with each poll.

After home is confirmed, click any point inside the main bed map to jog directly to
that app X/Y position at the selected jog speed. The click is converted through the
live session origin into an absolute `G53` jog. The laser remains at M5/S0, the target
must be within bounds, and the next move stays locked until the reported endpoint
matches the requested point.

The arrow keys use the selected jog step. Repeated presses update one pending X/Y
destination while a move is running, then the controller continues to the latest
bounded target. The machine map supports mouse-wheel zoom and shows the current design
as a gray, read-only overlay. Choose a design object and use **BL**, **BR**, **TL**,
**TR**, or **Center** to jog to that exact point with the same laser-off checks.

## Geometry workspace

Use the **Design & Send** tab in the main window. Create rectangles, circles/ellipses,
diagonal lines, and text by dragging on the bed or entering exact X, Y, width and height.
Text supports Arial, Segoe UI, and Consolas and is sent as flattened vector contours.
Each shape has speed (60–20,000 mm/min), power (S0–S1000), and 1–20 passes.

The Design tab starts in **Select / move** mode. Click an object to select it, drag it
to move, or drag any corner handle to resize. Arrow keys nudge by the selected grid
step; hold Shift for ten steps. Undo/Redo support up to 100 geometry changes, with
Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z, Ctrl+D, and Delete shortcuts. Duplicate, align-to-bed,
layer ordering, and exact numeric edits are available beside the canvas.

Ctrl-click adds or removes objects from a multi-selection; Ctrl+A selects the complete
design. A dashed blue box shows the combined selection. Dragging, arrow nudging,
duplicating, deleting, alignment, and layer ordering operate on the selected set as one
bounded group. Select at least three objects to distribute their centers horizontally
or vertically from the **Align to bed** menu. With multiple objects selected, **Apply
values** changes their shared speed, power, and pass count without replacing geometry.

Use the mouse wheel or Ctrl++/Ctrl+- to zoom, middle-drag to pan, and **Fit bed** or
Ctrl+0 to restore the full workspace. Grid snapping supports 0.1, 0.5, 1, 5, and
10 mm. **New**, **Open**, and **Save design** store editable `.atomdesign` project
files; saving a design does not export G-code or communicate with the machine.

**Preview job** opens a read-only execution preview before Frame or Send. It shows
laser-off travel as dashed gray lines, laser paths from blue to red as power rises,
and a scrub/play control for command order. The summary reports object and move count,
laser and travel distance, maximum power, and an estimated motion time. Preview sends
no command and does not require a machine connection.

**Material library** stores named speed, power, and pass combinations in the current
Windows user's AppData folder. Set the three values, click **Save current**, and later
choose **Apply** to copy that preset onto the selected geometry. The library records
the user's settings; it does not claim that a preset is safe for a particular material.

**Create burn-test grid** adds 1–10 speed columns and 1–10 power rows. Each cell is
labeled `F` for speed and `S` for power in the workspace; labels are visual and are
not burned. The cells become ordinary rectangle geometry for framing and G-code
and direct USB sending.

**Send job to machine** generates bounded `G53` moves from the confirmed bottom-left
origin and streams them directly over USB. Each line must be acknowledged before the
next is sent. Only the generator's `G21`, `G90`, `G53 G0/G1`, `M4`, `M5`, and `S`
forms are accepted; coordinates, power, and feed are checked again before transmission.
Pause sends feed hold, Resume sends cycle start, and STOP/RESET disarms and closes the
session. Starting a job requires a final confirmation showing its maximum power.

The dashed orange rectangle is the exact **Frame outline**, with a 2 mm margin around
all shapes and clamped to the 365 × 305 mm app bounds. Choose a speed up to 20,000
mm/min, then click **Frame outline · laser off**. The button enables only after the
live profile and zero-power state are verified, bottom-left home is confirmed, and
geometry exists. Physical optical-power disconnection is optional and no longer a gate.

Frame sends five serialized absolute jog segments in measured machine coordinates.
It never sends `M3`, `M4`, or an `S` power word. Every corner must return `ok` and a
fresh Idle position within 0.05 mm before the next segment begins. Stop/reset,
disconnect, timeout, wrong endpoint, unexpected power,
or controller fault cancels the outline and discards the home reference.

## Activity log

The default **Warnings and errors** view hides normal USB traffic. Other choices are
**Errors only**, **Information**, and **USB traffic**. The empty state confirms there
are no current problems. Firmware/settings query buttons and live profile details
are hidden until **Technical details** is checked.

**STOP / RESET** (or Escape) requests feed hold, jog cancel and a soft reset, closes
the connection and clears the origin. It is a USB stop request, not a hardware
emergency stop. Use the physical power switch if the connection is unresponsive.

An initial homing-locked `Alarm` status can be homed if diagnostics report `M5 S0`
and zero power and the profile matches.
The app does not send `$X` to bypass the homing lock. Reported `ALARM:n`, command
errors, Door/Hold/Sleep/Check states and runtime resets close the session.

## Coordinate policy

```text
app X = current machine X − confirmed home machine X
app Y = current machine Y − confirmed home machine Y

Example only: home MPos (−288, −301) → app (0, 0)
             MPos (−287, −301)      → app (1, 0)
```

`−288, −301` came from the user's approximate observation, not a complete homing
report. It is used in synthetic tests only. The live origin is never hardcoded or
persisted. `WPos` is converted only when `WCO` exists in the **same** report;
cached work offsets are not trusted. Live `$13=0` is required for millimeter reports.

App bounds are X **0–365 mm** and Y **0–305 mm**, based on observed `$130/$131`.
These are a provisional app envelope, **not verified physical clearance** or a
replacement for firmware soft limits. Home pull-off and the unusual machine
origin may affect actual usable travel. Firmware `$20=0` remains unchanged.
Bounds cover only app-generated jogs; homing is controlled by the firmware.
Do not use a second sender, Wi-Fi control, or manually move the axes during a session.

Each manual jog is a single-axis `$J=G21 G91 X1.000 F60`-style command. There is no queued
repeat, key-hold motion, fallback `G1`, arbitrary command input, job import, or laser
test. Frame uses bounded `$J=G21 G90 G53 X… Y… F…` segments without laser words. After `ok`, a fresh Idle report must
match the expected endpoint within 0.05 mm before another step is allowed.
Status older than 1.5 seconds prevents motion and invalidates a confirmed session.

## Architecture

- `atomstack/protocol.py`: finite numeric parsing, vendor status field preservation,
  settings profile and diagnostic command allowlist.
- `atomstack/controller.py`: acknowledgement tracking, timeouts, homing lifecycle,
  laser-off checks, position normalization and guarded motion.
- `atomstack/transports.py`: byte-oriented `Transport` protocol, USB adapter and
  synthetic simulator. Wi-Fi can implement the same read/write/close boundary;
  no vendor network protocol is assumed or connected in this milestone.
- `atomstack/ui.py`: native Tkinter desktop panel, port selection and session log.
- `atomstack/terminal.py`: diagnostic-only terminal with optional interactive input.
- `atomstack/diagnostics.py`: live JSON snapshots for inspecting the same running UI
  from a terminal, without opening a second serial connection.
- `atomstack/fixtures/observed.txt`: actual firmware/settings output with network
  identifiers omitted. Simulator G54/G92/modal and post-home reports are synthetic.

Only one request is in flight. Normal commands end with a newline and complete on
`ok`; bare `?` status queries complete on their `<...>` response. On the user's
V1.055 controller, overlapping bare `?` with `$I` reproduced `error:1` on the next
`$$`. Version 0.1.1 used serialized `?\n`; 0.1.2 keeps serialization but removes
the unnecessary newline/empty-command traffic. Polling waits while another command
(including homing) is outstanding. USB reads
are nonblocking and fragmented lines are assembled. Disconnect, error, timeout,
restart, unexplained movement, mismatched endpoints or changed safety settings
discard the home reference. Reconnection always starts unhomed.

## Verification and remaining work

```powershell
python -m unittest discover -s tests -v
```

The test suite covers the observed profile, parsing, coordinate conversion,
boundary rejection, homing confirmation, one-step serialization, stale/malformed
reports, timeout, alarm/reset/disconnect, unexpected laser state and failed writes.
The native UI smoke test used the simulator and actual widget button invocation;
see `ui-verification.txt` and the preview images.

**USB startup diagnostics and repeated status polling are verified on the user's
physical V1.055 controller.** See `hardware-startup-verification.txt`.
Automated hardware checks do not issue physical motion; vendor `$J` behavior remains
outside those checks.
If `$J` is rejected, the app locks and does not fall back to ordinary movement.
The automated hardware checks issued no physical motion. The simulator is a software
test double, not evidence of the machine's actual travel or safety behavior.

Version 0.3.0 adds the guarded Frame outline. Its path, serialization, endpoint
verification, cancellation, and lack of laser commands are tested in the simulator.
No physical Frame was issued during development because software cannot verify that
the optical-power connector is actually disconnected.

Version 0.4.0 adds editable vector text, generated speed/power burn-test grids, and
a persistent material library. These features share the existing geometry bounds,
frame preview, and validation.

Version 0.5.0 removes the physical-disconnection requirement, raises manual and Frame
presets to the live 20,000 mm/min XY ceiling, and adds direct generated-job streaming
with explicit start confirmation, pause/resume, endpoint verification, and disarming.

Version 0.6.0 adds click-to-jog on the main bed map with a visible destination marker,
absolute machine-coordinate conversion, bounds checks, and endpoint verification.

Version 0.7.0 merges design and machine control into one 1280 × 920 window. Persistent
connection/status and STOP controls sit above **Design & Send** and **Machine & Jog**
tabs; the editor is embedded directly instead of opening a separate window.

Version 0.8.0 adds buffered arrow-key jogging, mouse-wheel zoom, a read-only gray
design overlay in Machine & Jog, and bounded moves to any selected object's four
corners or center.

Version 0.9.0 adds a complete editing pass to Design & Send: selection, drag move,
corner resize, 100-step undo/redo, duplicate/delete, arrow nudge, configurable grid
snap, zoom/pan/fit, bed alignment, layer ordering, exact editing, and editable
`.atomdesign` project save/open. Design arrow keys remain separate from machine jog.

Version 0.10.0 adds a read-only job preview generated from the same paths used for
direct sending, including rapid versus burn visualization, power intensity, execution
scrubbing/playback, distance totals, maximum power, and a motion-time estimate.

Version 0.11.0 adds true multi-selection with Ctrl-click and Ctrl+A, combined-bounds
dragging and nudging, group duplicate/delete, group alignment and layer movement,
horizontal/vertical distribution, shared process settings, and atomic Undo for every
group operation.

Version 0.12.0 adds 90-degree rotation and horizontal/vertical mirroring for individual
objects and selections. The canvas, machine overlay, Frame outline, job preview, and
direct machine output all use the same transformed paths and enforced bed bounds. The
inspector shows the exact transformed or combined bounds. Project files now use schema
version 2, continue to open version 1 files, and reject unknown future versions without
changing the current design. Undo restores the project filename together with geometry,
and an open job preview closes as soon as the design changes so it cannot misrepresent
the job that will be sent. Rotated objects can still be sized numerically; drag-resize
handles are hidden until arbitrary-angle handles are implemented.

Build the standalone executable from this folder (PyInstaller 6.x):

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name AtomstackController --add-data "atomstack/fixtures;atomstack/fixtures" main.py
```

Protocol references: [GRBL jogging](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Jogging)
and [GRBL interface](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Interface).
These document upstream GRBL; compatibility with Atomstack V1.055 still needs
hardware confirmation.


## Cut layers (0.13.0)

Open **Cut layers…** in Design & Send. Add a named layer, set speed, power, passes,
and Output on, then select objects in the design and choose **Assign selected objects**.
Layers run from top to bottom; Move up/down changes execution order. Unassigned objects
run last, keeping their individual settings (including burn-test values).

Layer settings are shared: use **Save changes** to update every assigned object.
The inspector displays the effective settings and directs you to Cut layers for edits.
**Use individual settings** detaches selected objects while preserving their current
speed/power/passes and enables their output. Output-off objects stay visible in gray
but are excluded from Frame, preview, estimates, and direct machine output.

Undo/Redo includes layer settings, order, assignments, and output switches. Project
schema 3 persists layers; schema 1 and 2 designs still open without changing their
individual process settings. Preview closes when layers change.


## Daily-workflow tools (0.14.0)

- **File → Import SVG** imports vector paths, basic shapes, groups, transforms, and
  curves at a 0.05 mm flattening tolerance. Page units and viewBox are respected;
  the imported artwork must fit the bed. SVG text must first be converted to paths.
  Images, CSS classes, clipping, masks, filters, rounded-rectangle elements, and
  unsupported viewport alignments are rejected rather than silently omitted.
- **File → Save as / Recent designs / Recover autosave** supports everyday project
  work. Unsaved changes are shown beside the file controls. New, Open, and Close ask
  whether to save. Changes are autosaved every ten seconds to an independent session
  file under APPDATA/AtomstackController/designs; startup offers recovery. Autosave
  is a recovery copy, never an overwrite of the original design.
- **Cut layers** is now an inspector tab beside the canvas. Choose **fill**, set
  spacing in millimetres, and save the layer to engrave solid interiors. Fill uses
  alternating horizontal lines with even-odd holes. Open paths cannot be filled.
  Each traverse between scan lines is laser-off. Preview and direct output use the
  same fill paths. Fine/complex fills are bounded to keep the application responsive.
- **Pan** supports left-drag navigation, alongside middle-drag and wheel zoom.
  Moving objects shows alignment reference guides. **Arrange** aligns selected
  edges or centers to the primary object, creates arrays, and adds offset outlines.
  Arrays preserve group spacing and are rejected atomically if any copy leaves the
  bed. Offsets support a single closed convex contour per object, with positive
  outward and negative inward distances; compound/concave offsets are not yet supported.
- **Burn-test grid** now accepts cell gap, passes, line/fill mode, and fill spacing.
  Each cell retains individual speed and power. Compare the physical results, select
  the preferred cell, and use Save current in the material library. Existing material
  presets can also be loaded directly into the cut-layer editor before saving changes.

Projects use schema 4 and continue loading schema 1–3. Imported paths, fill settings,
arrays, and layer edits participate in Undo/Redo and atomic project saves. SVG import
is vector-outline import, not full browser rendering, and this release is not full
LightBurn feature parity.

## Beam alignment (0.14.1)

The app uses the reported approximate cutting-beam offset of **12.5 mm left**
of the positioning mark (X = -12.5 mm) when no saved alignment exists.
Open **Beam alignment** to refine or disable it (0 mm). Settings are saved locally,
not in project files or firmware. This value is an estimate, not a measured calibration.
Jog, coordinates, and laser-off Frame refer to the positioning mark. For cutting,
head X = design X - beam offset, so -12.5 shifts the head right by 12.5 mm.
The design and preview remain in intended cut coordinates. Both head travel and
cutting-beam endpoints are checked before sending; the last 12.5 mm of the right
side is unreachable for cutting with this offset. Framing also checks cut reachability.
Offsets cannot change during motion or a queued motion request. Invalid saved
alignment blocks sending until a valid value is saved. Tests use the simulator;
physical alignment still needs verification on the machine.

## Workspace 0.15

Dark compact controls, a larger light canvas with millimetre rulers, a left drawing
rail, and top numeric X/Y/width/height/angle fields follow the supplied LightBurn
layout reference. The right dock holds Objects, Cut layers, and Move tabs plus
fixed job controls and materials. Move uses the same guarded home/confirm/jog
commands; the full keyboard and mouse navigator remains in Machine & Jog.
The interface is an implemented subset, not complete LightBurn feature parity.

## Placement 0.16

Connect & home explicitly starts the configured automatic homing cycle after
controller checks. The cycle endpoint supplies app zero. Physical orientation
and the approximate beam offset still require supervised verification.

Place job moves all enabled objects together using nine bounding-box anchors
and a target in positioning-mark coordinates. Use current positioning mark
requires a fresh guarded position. Placement changes geometry and is undoable
and saved with the project; it does not move the machine. Disabled output is
left in place. Offset-aware travel checks reject unreachable placements.
The canvas shades the unreachable X strip and displays Mark/Cut positions
when homed. This release also includes the intervening DXF, text layout, rotated
handles, off-bed editing, and welding commits.
