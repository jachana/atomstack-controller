# Goals

This project replaces LightBurn for one person, one machine: an Atomstack X10/A10
running vendor firmware V1.055 (Oct 13 2023), 365 x 305 mm bed, S-max 1000, over USB
at 115200 baud.

That scope is the whole point. LightBurn has to work with hundreds of controllers it
cannot inspect, so it trusts the operator to know what their machine will do. This
controller knows exactly one machine, has its `$$` dump checked into the repo as a
fixture, and refuses to move when the live settings disagree with it. Narrowing the
target buys guarantees LightBurn cannot offer.

This document is the authority on scope. Where `ROADMAP.md` disagrees, this file wins.

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

No multi-user features, no licensing, no plugin system. One operator, one machine.

## Where this stands against LightBurn

Verified against the code, not against `ROADMAP.md`, which is out of date on rotation.

| Capability | Status | Notes |
| --- | --- | --- |
| Connect, verify profile, read firmware | Done | Five read-only commands, no writes |
| Home with physical confirmation | Done | Confirmation gate has no LightBurn equivalent |
| Jog, click-to-jog, arrow targets | Done | Every path through one guard |
| Frame outline | Done | Laser off, 2 mm margin, verified endpoints |
| Send job, pause, resume, stop | Done | Whitelist grammar, verified final endpoint |
| Rectangle, ellipse, line | Done | |
| Vector text | Done | Arial, Segoe UI, Consolas, flattened contours |
| Burn test grid | Done | Speed by column, power by row |
| Material presets | Done | Speed, power, passes, saved to APPDATA |
| Direct manipulation, undo, redo, snapping | Done | 100-step history |
| Alignment, distribution, multi-selection | Done | Group bounds clamped to the bed |
| Rotate 90 degrees, flip H and V | Done | Bounds enforced on the transformed path |
| Z-order (bring forward, send backward) | Done | This is stacking order, not cut layers |
| Zoom, pan, fit | Done | Cursor-anchored zoom fixed 2026-09-04 |
| Project files | Done | Open and save, no dirty-state tracking |
| Job preview with time and distance | Done | Separate read-only window |
| Named cut layers | Done | Shared settings, execution order, output switches; v0.13 |
| SVG import | Missing | Blocks every design not drawn in-app |
| DXF import | Missing | |
| Arbitrary-angle rotation | Missing | Model stores any angle; UI offers only 90 |
| Dirty state, autosave, crash recovery | Missing | Unsaved work is currently lost silently |
| Recent files | Missing | |
| Kerning, text on a path | Missing | |
| Weld and boolean operations | Missing | |
| Job origin modes, array copies | Missing | |
| Rotary | Out of scope for now | |
| Camera alignment | Out of scope | |
| Print and cut, node editing, offsets | Out of scope | |

Named cut layers now manage shared settings, output toggles, and execution order.
Shapes already supported different settings in a single job; layers make those
operations easier to organize and edit together.

SVG import is second and for the same reason: without it, only designs drawn inside
this app can be cut, which rules out anything from a vector editor.

## Delivery order

1. Named cut layers with shared speed, power and pass count, plus output toggles.
2. SVG import with curves flattened at a controlled tolerance.
3. Dirty state, autosave, crash recovery, recent files, and project migration tests.
4. Arbitrary-angle rotation in the UI, since the model already carries the angle.
5. DXF import.
6. Richer text layout, kerning, text on a path, weld and boolean operations.
7. Job origin modes and array copies for repeat production.
8. Calibration and focus tests, plus a guided first-run setup.

Items 1 through 3 are what daily use actually requires. Everything after is comfort.

## What personalization actually buys

The fixture in `atomstack/fixtures/observed.txt` is this machine's real `$$` output,
not a template. `EXPECTED` in `protocol.py` pins nine settings that must match before
anything moves. Feed limits come from live `$110`/`$111`, not from a config file
someone can type a wrong number into.

The UI can assume one bed size, one power scale, and one operator who is standing next
to the machine in a workshop. That is why the layout is a single window with connection
state always visible, why there is no onboarding wizard, and why STOP is red and never
scrolls away.

## Constraints

Windows desktop only. Python 3.12, Tkinter, pyserial, packaged with PyInstaller into a
single executable that runs without a Python install.

No third-party runtime beyond pyserial and fonttools. Every dependency added is a
dependency that has to be trusted with a machine that can start a fire.

The transport boundary in `transports.py` stays narrow enough that a Wi-Fi adapter can
implement `read`, `write` and `close` without the controller changing.

The operator is the author. There is no support burden, no backwards-compatibility
promise to strangers, and no reason to keep a feature that stopped being useful.

## Non-functional goals

The app opens and is usable in under three seconds from a cold double-click.

A job that is running must never be blocked by the UI. Status polling and command
serialisation already share one queue; that must stay true as features land.

No silent data loss. Once item 3 in the delivery order is done, closing the app with
unsaved work must warn, and a crash must leave a recoverable file.

Failures are loud and specific. Every refusal names the condition that failed, because
a message like "not ready" costs the operator a diagnostic session.

## How we know it works

Three checks gate every release, and all three have to pass.

Simulation coverage runs the full guard state machine against a synthetic transport.
Packaged-executable verification launches the real `.exe`, confirms it opens no serial
ports in demo mode, and records its SHA256. A read-only soak against the physical
controller on COM3 watches status reports without commanding motion.

A change is done when the tests pass, the packaged executable launches, and anything
visual has been looked at in a running window. Test output alone is not evidence for
UI work.

## Known gaps in the verification story

The simulator in `transports.py` was written alongside the controller and accepts
exactly what the controller emits, down to the hardcoded feed and distance lists. A
passing test proves the two files agree, not that V1.055 agrees with either. Replaying
recorded bytes from the real machine would close this. Until then, "verified" means
verified in simulation.

Motion on real hardware has never been validated. The only hardware evidence is a
60-second read-only soak. Jog, Frame and job streaming have been exercised against the
simulator alone.

Verification artifacts in the repo root are pasted transcripts. Nothing regenerates
them and nothing fails when they go stale, and at least one already has:
`test-results.txt` claims 83 tests when the suite collects 132. A stale PASS is worse
than no PASS.

`ui.py` is the largest file and most of it is still untested. The coordinate math was
extracted into `viewport.py` and covered, which found a real zoom bug, but widget
state, the inspector, project file round-trips and the job send path have no automated
coverage.

## Open questions

How should a cut layer relate to the material presets that already exist? A preset is
speed, power and passes, which is the same triple a layer needs. They should probably
be one concept rather than two.

Should SVG import flatten curves at a fixed tolerance or one the operator sets? A fixed
value is simpler and harder to get wrong.

The negative machine coordinates at home, roughly MPos (-288, -301), are a user
observation and not a calibration. Nothing depends on the exact values today. Before
job origin modes land, that needs to be either measured properly or designed around.

Is a read-only hardware soak enough to keep claiming safety, or does the project need a
supervised motion test with the laser physically disconnected? The safety contract is
currently argued from code review rather than from evidence.
