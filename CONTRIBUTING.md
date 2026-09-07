# Contributing

This software drives a 10 W diode laser. A bug here does not corrupt a document,
it moves a burning head across a bed with a person standing next to it. That is
the whole reason the rules below are stricter than the size of the project would
suggest.

Contributions are welcome. Issues, questions and "this broke on my machine" are
just as useful as code.

## Before anything else: safety

- **Never send motion to hardware you are not standing next to.** Nothing in this
  repository is allowed to home, jog, frame or fire as a side effect of a test, a
  build, or a verification run. `tools/verify.py` never opens a serial port, and
  it must stay that way.
- **The simulator is the default.** `python main.py --demo` runs the whole
  application with no port open. Develop there.
- **Do not weaken a guard to make a feature work.** The gates on arming, homing,
  bed bounds and beam offset exist because the machine has no interlock of its
  own. If a guard is in your way, say so in the issue and we change it
  deliberately, with a test.
- **Hardware claims need hardware evidence.** "It should work on the machine" is
  not a claim this project makes. Either you ran it on a real controller and can
  show what it did, or the pull request says the hardware path is unverified.

## Setup

    python -m venv .venv
    .venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1
    pip install -r requirements-dev.txt
    python -m pytest -q
    python main.py --demo

Python 3.12 on Windows is what this is developed and packaged on. The core
modules are plain Python and should behave elsewhere, but the drag-and-drop path
(`atomstack/dropfiles.py`) and the packaged build are Windows-only.

Dependencies are pinned in `requirements.txt`. Adding one is a real decision: it
lands in a 20 MB single-file executable and it brings a licence with it. Prefer
the standard library, and if you do add a package, run `python tools/notices.py`
so `THIRD-PARTY-NOTICES.md` carries its terms.

## What the project is, and is not

Read the top of `README.md` first. This targets one machine — an Atomstack X10/A10
on GRBL 1.1 firmware V1.055 — and the machine profile in
`atomstack/fixtures/observed.txt` is what the app verifies against before it will
arm. Support for other machines is not off the table, but it is a design
conversation, not a patch that loosens the profile check.

## Tests

The suite is the specification. Every behavioural change comes with a test, and
the test is named as a sentence about the machine, not about the function:

    def test_a_held_job_does_not_time_out_waiting_for_acks(self):

Run the whole suite before you push:

    python -m pytest -q

Count the number, do not grep for failures. Pytest reports collection errors as
`ERROR`, not `FAILED`, and a grep for the wrong word once hid 49 broken tests in
this repository. `tools/verify.py` counts, which is why it is the check that
matters.

Prefer tests that measure over tests that read. Where a result can be sampled —
points inside a welded polygon, the coordinates a handle actually produces, the
pixels a card renders to — sample it, rather than asserting the shape of the code
that produced it.

## Verification artifacts

The evidence files in the repository root are generated, not written:

    python tools/verify.py            # run tests, rewrite the artifacts
    python tools/verify.py --check    # fail if they are out of date
    python tools/notices.py --check   # fail if third-party notices are stale

Do not hand-edit `test-results.txt`, `verification-report.json` or
`THIRD-PARTY-NOTICES.md`. A stale PASS is worse than no PASS, because it gets
read as evidence.

For anything with a visual result — a UI change, an import mode, a test card —
the proof is an image the application produced of itself:

    python main.py --verify-images evidence/report.json

The run writes a JSON report and the PNGs beside it. Attach those images to the pull request. Screenshots taken by driving the mouse
and keyboard of a real desktop are not acceptable: it has gone wrong here before,
and it types into whatever window happens to be focused.

## Building the executable

    python tools/build.py

It refuses a dirty tree on purpose. A binary that cannot be traced back to a
commit is a souvenir, not a deliverable. `--skip-tests` and `--allow-dirty` exist
for local experiments and should not be how a released build is made.

## Code style

- Match the surrounding code. There is no formatter and no linter; there is a
  house voice, and it is consistent.
- **Docstrings say why, not what.** The signature already says what. Explain the
  machine behaviour, the failure that motivated the code, or the constraint it is
  honouring. Several modules in `atomstack/` open with the bug that created them;
  that is the pattern.
- **Never `eval` user input.** Numeric fields go through
  `atomstack/expressions.py`, which walks an AST whitelist and refuses everything
  else. If you need a new operation there, add it to the whitelist with a test in
  the `Refusals` class.
- Keep GRBL protocol knowledge in `atomstack/protocol.py` and
  `atomstack/controller.py`. UI modules should not be writing G-code.
- Coordinates in the document are cut positions. The head goes to the design
  position minus the beam offset, which is why usable reach is smaller than the
  bed. Anything that asks "does this fit?" asks about reach, not about the bed.
- Bump `__version__` in `atomstack/__init__.py` when a change is worth a build.

## Commits and pull requests

Commit messages are a sentence in the imperative about what the change does for
the person using the machine, not a category prefix:

    Park the head at home when a job finishes
    Estimate travel at the machine's rapid rate, not an assumed one

One concern per pull request. In the description, say what you changed, how you
verified it, and — explicitly — what you did *not* verify, especially whether the
change has ever met real hardware. Unverified is a fine answer. Silent is not.

## Reporting problems

Open an issue with the machine, the firmware version (`$I` output), what you
asked the app to do, and what the machine did instead. If the app logged
anything, the activity log is the useful part. For anything that moved when it
should not have, say so in the first line.

## Behaviour

This project follows the Contributor Covenant, in `CODE_OF_CONDUCT.md`. The short
version: assume the person on the other end is trying to make the thing work.
Harassment or hostility gets a contribution declined regardless of its merits.
Report a problem to julio@juliocode.com.

## Licence

By contributing you agree that your contribution is licensed under the MIT
licence in `LICENSE`, the same terms as the rest of the project.
