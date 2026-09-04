# Goals

This project replaces LightBurn for one person, one machine: an Atomstack X10/A10
running vendor firmware V1.055 (Oct 13 2023), 365 x 305 mm bed, S-max 1000, over USB
at 115200 baud.

That scope is the whole point. LightBurn has to work with hundreds of controllers it
cannot inspect, so it trusts the operator to know what their machine will do. This
controller knows exactly one machine, has its `$$` dump checked into the repo as a
fixture, and refuses to move when the live settings disagree with it. Narrowing the
target buys guarantees LightBurn cannot offer.

## What success looks like

A normal working session goes: open the app, connect, home, confirm the head is at
bottom-left, draw or open a design, set speed and power, Frame it with the laser off,
send the job, watch it run. No G-code editing, no console, no reaching for LightBurn
to finish something this app started.

The bar is daily use. If a job means exporting to LightBurn to actually cut it, the
feature is not done.

## The safety contract

This is where the app should be better than LightBurn, not merely equal.

Motion is locked until the app has proof. Every guarded action needs a fresh Idle
status report with machine X/Y, a parser confirmation of `M5 S0`, zero reported laser
power, and live `$$` settings matching the observed profile. A status report older
than 1.5 seconds is stale and blocks motion.

Homing is not a coordinate. The firmware acknowledging `$H` proves nothing about where
the head physically is, so the app asks the operator to look at the machine and confirm
bottom-left before it will treat that position as app zero. Any reset, alarm, unexpected
movement, or settings change throws the reference away and requires homing again.

Nothing gets sent that the app did not generate. There is no free-form command box.
Job commands pass a whitelist grammar before the first byte goes out, and the job must
end with `M5` then `S0` or it is rejected outright. Frame moves the head with the laser
off, always.

Automated tests never home, jog, Frame, or fire the machine. Hardware checks are
read-only unless a person is standing at the machine.

These rules exist because the failure mode is a fire, not a stack trace.

## What this deliberately will not do

No firmware writes. The app reads `$$` and compares. It never sets a value.

No arbitrary G-code console. The whitelist is the feature.

No support for other machines, other firmwares, or a generic device profile system.
If the fixture stops matching, that is a signal to investigate, not to add a dropdown.

No cloud, no telemetry, no update checks, no network calls of any kind beyond the
future Wi-Fi transport to this same machine.

No camera alignment, no galvo support, no rotary until the core is boringly reliable.

## Feature parity targets

Working now: connection and profile verification, normalized bottom-left coordinates,
guarded Home, Jog, Frame, direct job streaming with pause and resume, STOP/RESET,
rectangles, ellipses, lines, vector text, burn tests, material presets, direct
manipulation with undo and redo, snapping, alignment, layer order, zoom and pan,
project files, job preview with time and distance estimates, and multi-selection with
group transforms.

Still needed for the daily-use bar, roughly in order:

1. Rotation and mirroring with transformed bounds and exact output paths.
2. SVG import with curves flattened at a controlled tolerance, then DXF.
3. Named cut layers sharing speed, power, and pass count, with output toggles.
4. Dirty state, autosave, crash recovery, recent files, and project migration tests.
5. Richer text layout, kerning, text on a path, welding and boolean operations.
6. Job origin modes, array copies, and repeatable production runs.
7. Calibration and focus tests, plus a guided first-run setup.

Item 3 is the one that matters most for real work. Cutting and engraving the same
design at different settings in one job is the difference between a toy and a tool.

## What personalization actually buys

The fixture in `atomstack/fixtures/observed.txt` is this machine's real `$$` output,
not a template. `EXPECTED` in `protocol.py` pins nine settings that must match before
anything moves. Feed limits come from live `$110`/`$111`, not from a config file
someone can type a wrong number into.

The UI can assume one bed size, one power scale, and one operator who is standing next
to the machine in a workshop. That is why the layout is a single window with connection
state always visible, why there is no onboarding wizard, and why STOP is red and never
scrolls away.

## How we know it works

Three checks gate every release, and all three have to pass.

Simulation coverage runs the full guard state machine against a synthetic transport.
Packaged-executable verification launches the real `.exe`, confirms it opens no serial
ports in demo mode, and records its SHA256. A read-only soak against the physical
controller on COM3 watches status reports without commanding motion.

Two known weaknesses in that scheme, both open:

The simulator was written alongside the controller and accepts exactly what the
controller emits, so a passing test proves the two files agree, not that V1.055 agrees
with either. Replaying recorded bytes from the real machine would fix this.

`ui.py` is about 43 percent of the source and almost none of it is tested. The
controller rejects invalid requests, but it cannot tell a valid wrong coordinate from a
valid right one, so any bug in the UI's coordinate math reaches the machine unchallenged.

Until both are closed, "verified" means verified in simulation.
