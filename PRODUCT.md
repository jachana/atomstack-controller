# Product

Personal Windows desktop controller for the user's Atomstack X10/A10 with existing
vendor firmware. First milestone: inspect USB at 115200, show firmware and X/Y,
normalize a confirmed physical bottom-left home, and perform guarded 1 mm jogs.
No Frame, laser firing, firmware writes, arbitrary console commands or job streaming.

## Platform
Native Windows desktop.

## Stack
User delegated the technology choice: Python 3.12, Tkinter, pyserial; standalone
Windows executable built with PyInstaller. Transport protocol permits later Wi-Fi.

## Evidence and limits
Actual fixture: V1.055 Oct 13 2023; 365 × 305 mm; S-max 1000. Negative coordinates
at home are a separate approximate user observation, not a calibration value.
Successful homing plus physical confirmation is required every session.
Only app-generated low-feed jogs are bounded. Hardware validation remains pending.

## Interaction
Operate mode. Light native controls for a desktop beside a machine (ambient lighting
is an assumption). Clear connection, diagnostics, home confirmation and jog sequence.
Physical laser disconnection must be acknowledged for motion tests.
