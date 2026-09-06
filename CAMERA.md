# Bed camera: plan

A camera over the bed, calibrated so that what it sees maps to where the head
goes. Two things follow from that: placing work on a photograph of the real
material instead of on an empty rectangle, and checking after the fact that the
job landed where the design said it would.

This is a plan, not a record. Nothing here is built yet.

## What it is for

The bed is currently drawn empty. An operator lining a design up with a scrap
offcut, a knot in a board, or a mark on an anodised plate has to do it by
measuring and typing coordinates. With a calibrated camera, the material appears
under the design and placement becomes what it should be: drag the outline onto
the thing you can see.

Ranked by what they are worth against what they cost:

1. **See the bed under the design.** Position work against real material.
2. **Place by clicking on the picture.** The existing Place job dialog takes an
   anchor and a target; the camera gives the target.
3. **Trace what is on the bed.** The image import already turns a picture into
   outlines. Pointed at the bed, it cuts around an existing object.
4. **Check the result.** Photograph after the job and compare against the
   design; a systematic offset is the beam alignment being wrong.

The first is most of the value. The rest reuse machinery that already exists.

## The calibration problem

A camera does not see the bed as a rectangle. It sees a trapezoid, because the
lens is not directly above every point, and the trapezoid's edges bow, because
the lens distorts. Two separate corrections, in this order:

**Lens distortion** is a property of the camera and never changes once measured.
Radial distortion dominates: a straight line near the frame edge bows. Correcting
it means finding the coefficients that straighten known-straight lines.

**Perspective** is a property of where the camera is mounted, and changes the
moment anything is bumped. Correcting it means finding the homography — the
3x3 matrix mapping image pixels to bed millimetres — from four or more
correspondences between points whose bed coordinates are known.

Both are standard, and both are a page of numpy against a least-squares solve.
The interesting part is not the arithmetic; it is getting correspondences that
are actually true.

## How to get correspondences honestly

The tempting approach is to print a calibration sheet, lay it on the bed, and
photograph it. It is wrong here, because it calibrates the camera against a piece
of paper whose position is unknown. Every error in placing the sheet becomes an
error in the mapping, silently.

The machine can do better than a sheet, because it knows where its own head is.

**Mark the targets with the laser.** Burn a grid of small crosses at known bed
coordinates on a sheet of scrap, then photograph it. The correspondences are then
between what the camera sees and where the machine actually went, which is the
relationship that matters. It also folds in the beam offset: the marks are where
the cutting beam went, so a mapping built from them puts the design where the
beam will be, not where the positioning mark will be.

This requires firing the laser, which needs the operator present and consenting.
It cannot be automatic.

**A fallback without burning:** jog the head to a set of positions and photograph
the head itself at each. Slower, needs the head to be visually distinctive, and
gives fewer usable points. Worth having for someone who will not burn a sheet,
but the burnt grid is the honest default.

## Accuracy, and how to know it

The claim "the camera is calibrated" needs a number attached, or it is a feeling.

Hold back some of the burnt marks from the fit and measure the mapping's error on
those. Report that residual in millimetres, prominently, and refuse to offer
click-to-place if it is worse than some threshold. An operator who is told
"calibrated to 0.4 mm" can decide whether that is good enough for the job. An
operator told "calibrated" cannot.

Store the residual with the calibration, and show it wherever the camera view is
used.

## What invalidates a calibration

Silently stale calibration is the failure mode that would waste material. It must
expire when:

- The camera is moved or bumped. Cannot be detected directly; detect it by
  re-checking against a burnt mark, or by asking.
- The camera's resolution or field of view changes.
- The beam alignment offset changes, because the mapping was built from marks
  the beam made.
- The bed height changes. A camera calibrated at one material thickness is wrong
  at another: the homography assumes a plane, and moving that plane moves every
  point. This is the one operators will not expect. Either store the thickness
  the calibration was made at and warn when it changes, or calibrate at two
  heights and interpolate.

Record the date, the residual, the beam offset and the material thickness
alongside the mapping, the way the beam alignment is already stored.

## Shape of the work

Following the existing split: pure arithmetic in its own module with its own
tests, hardware at the edges, and the interface last.

**`camera.py`** — no camera, no Tk. Homography fit from correspondences, lens
distortion fit, image-to-bed and bed-to-image transforms, residual measurement.
Tested against synthetic correspondences with a known answer, including
deliberately noisy and degenerate ones (all points collinear, duplicate points),
which must be refused rather than fitted.

**`capture.py`** — the camera itself, behind the same kind of boundary as
`transports.py`. Enumerate devices, grab a frame, release. A file-backed fake for
tests, so everything above it runs without hardware.

**Calibration flow** — generate the mark grid as an ordinary job, so it goes
through the existing guards; run it with the operator watching; capture; detect
the marks; fit; report the residual; store.

**Interface** — the bed canvas already maps millimetres to pixels through
`viewport.py`, so a rectified camera frame drawn behind the geometry is a
straightforward addition. Click-to-place feeds the existing Place job dialog.

Mark detection is the piece most likely to be fiddly: finding burnt crosses on
scorched wood under uneven light. The imaging module already has threshold,
blur, edges and contours, and a cross is found by its centroid, but expect this
to need work and expect to need a manual fallback where the operator clicks the
marks they can see.

## Dependencies

Capture needs something that can talk to a webcam. OpenCV does it in one call and
is already installed here, but it is roughly 60 MB in the bundle, against 38 MB
for the whole app today. Alternatives worth measuring first: Windows Media
Foundation through a small dependency, or `pygrabber`, which wraps DirectShow and
is tiny.

Decide this by measuring the bundle, not by preference. If the answer is OpenCV,
then the imaging operations written by hand for size reasons could be revisited,
since the cost would already have been paid.

## Order to build it

1. `camera.py` with tests. No hardware needed, and it is the part that has to be
   right.
2. `capture.py` against a fake, then one real camera.
3. The burnt-grid calibration flow, ending in a residual the operator can see.
4. The bed view behind the design.
5. Click-to-place.
6. After-the-fact comparison.

Stop after 4 if the residual turns out worse than about a millimetre; below that
accuracy, seeing the bed is still useful but placing by clicking is not.

## What could make this not worth doing

Worth deciding before starting, not after:

- **The mount.** A camera that moves between sessions makes calibration a chore
  rather than a setup step. If it cannot be mounted rigidly, this feature is a
  toy.
- **The gantry.** On a machine where the head and rails cross the frame, the
  camera cannot see the whole bed at once, and the view will be partly occluded
  wherever the head happens to be parked.
- **Focus and lighting.** Burnt marks on dark wood in a dim garage may not be
  detectable, in which case the manual fallback is the feature and the automatic
  detection is decoration.

None of these are reasons not to start; all of them are reasons to build step 3
early enough to find out.
