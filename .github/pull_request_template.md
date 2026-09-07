## What this changes

<!-- One or two sentences, in terms of what the machine or the operator does differently. -->

## How it was verified

- [ ] `python -m pytest -q` passes (paste the count)
- [ ] `python tools/verify.py --check` is clean, or the artifacts are regenerated in this PR
- [ ] Images attached for anything visual (`python main.py --verify-images <report.json>`)

## What was NOT verified

<!-- Required. Say plainly whether this has met real hardware, and which paths are
     untested. "Simulator only, never sent to a machine" is a perfectly good answer. -->

## Safety

- [ ] No guard on arming, homing, bounds or beam offset was loosened
- [ ] Nothing in the test or build path can open a serial port or move the head
