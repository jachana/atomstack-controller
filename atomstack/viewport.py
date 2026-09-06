"""Pure bed/canvas coordinate math.

No Tk, no controller, no I/O. Every function here is a total function of its
arguments so the mapping between a pointer position and a machine coordinate can
be tested without a display.

The controller rejects invalid motion requests, but it cannot tell a valid wrong
coordinate from a valid right one. That makes this math safety-relevant: a sign
error here produces a legal jog to the wrong place. It lives in its own module for
that reason.

Canvas pixels grow down; bed millimetres grow up. Every conversion crosses that
flip exactly once.
"""
from dataclasses import dataclass
import math

from .geometry import BED_X, BED_Y

MIN_SCALE = 0.01
ANCHORS = ("BL", "BR", "TL", "TR", "C")
ARROW_DELTAS = {"Left": (-1, 0), "Right": (1, 0), "Up": (0, 1), "Down": (0, -1)}


@dataclass(frozen=True)
class Viewport:
    """Maps bed millimetres to canvas pixels. ``x0, y0`` is bed (0, 0)."""
    x0: float
    y0: float
    scale: float

    def __iter__(self):
        """Unpacks as ``x0, y0, scale`` for drawing code that wants the raw numbers."""
        return iter((self.x0, self.y0, self.scale))

    def to_canvas(self, x, y):
        return self.x0 + x * self.scale, self.y0 - y * self.scale

    def to_bed(self, px, py):
        return (px - self.x0) / self.scale, (self.y0 - py) / self.scale

    def pixels(self, millimetres):
        """Length in canvas pixels of a bed distance, for tolerances and radii."""
        return millimetres * self.scale

    def millimetres(self, pixels):
        """Length in bed millimetres of a pixel distance. Inverse of ``pixels``."""
        return pixels / self.scale


def fit_viewport(width, height, margin_x, margin_y, zoom=1.0, pan_x=0.0, pan_y=0.0,
                 bed=(BED_X, BED_Y)):
    """Centre the bed in a ``width`` x ``height`` canvas, then apply zoom and pan.

    ``margin_x``/``margin_y`` are pixel allowances for rulers and labels. The scale
    never reaches zero, so callers never divide by it and get an exception instead
    of a coordinate.
    """
    bed_x, bed_y = bed
    base = min((width - margin_x) / bed_x, (height - margin_y) / bed_y)
    scale = max(MIN_SCALE, base * zoom)
    return Viewport(width / 2 - bed_x * scale / 2 + pan_x,
                    height / 2 + bed_y * scale / 2 + pan_y,
                    scale)


def inside_bed(x, y, bed=(BED_X, BED_Y)):
    return 0 <= x <= bed[0] and 0 <= y <= bed[1]


def clamp_to_bed(x, y, bed=(BED_X, BED_Y)):
    return max(0.0, min(bed[0], x)), max(0.0, min(bed[1], y))


def clamp_zoom(zoom, factor, low, high):
    return max(low, min(high, zoom * factor))


def snap_value(value, step, enabled=True):
    """Round to the nearest grid multiple. A non-positive step disables snapping."""
    if not enabled or step <= 0:
        return value
    return round(value / step) * step


def arrow_target(base, direction, step, bed=(BED_X, BED_Y)):
    """Accumulated arrow-key destination, clamped to the bed.

    ``base`` is the previously buffered target when one exists, so repeated presses
    add up into a single guarded move rather than one move per keystroke.
    """
    if direction not in ARROW_DELTAS:
        raise KeyError(direction)
    dx, dy = ARROW_DELTAS[direction]
    return clamp_to_bed(base[0] + dx * step, base[1] + dy * step, bed)


def anchor_point(bounds, anchor):
    """Named point on a ``left, bottom, right, top`` box."""
    left, bottom, right, top = bounds
    if anchor == "BL":
        return left, bottom
    if anchor == "BR":
        return right, bottom
    if anchor == "TL":
        return left, top
    if anchor == "TR":
        return right, top
    if anchor == "C":
        return (left + right) / 2, (bottom + top) / 2
    raise KeyError(anchor)


def within_bounds(x, y, bounds, tolerance=0.0):
    left, bottom, right, top = bounds
    return (left - tolerance <= x <= right + tolerance
            and bottom - tolerance <= y <= top + tolerance)


def topmost_at(x, y, bounds_list, tolerance=0.0):
    """Index of the last (visually topmost) box containing the point, or None."""
    for index in range(len(bounds_list) - 1, -1, -1):
        if within_bounds(x, y, bounds_list[index], tolerance):
            return index
    return None


def handle_at(x, y, handles, tolerance):
    """Name of the resize handle under the point, or None.

    ``handles`` maps a name to a bed coordinate. The test is a square around each
    handle, matching how a pointer hit feels on screen.
    """
    for name, (hx, hy) in handles.items():
        if abs(x - hx) <= tolerance and abs(y - hy) <= tolerance:
            return name
    return None


def corner_handles(x, y, width, height):
    return {"BL": (x, y), "BR": (x + width, y),
            "TL": (x, y + height), "TR": (x + width, y + height)}


# Corner offsets from the box centre, as multiples of half the width and height.
CORNER_SIGNS = {"BL": (-1, -1), "BR": (1, -1), "TL": (-1, 1), "TR": (1, 1)}
OPPOSITE = {"BL": "TR", "BR": "TL", "TL": "BR", "TR": "BL"}
ROTATE_HANDLE = "ROT"


def _frame(shape_box, rotation, mirror_x, mirror_y):
    """Centre and the world directions of the box's own axes.

    Mirroring happens in the box's own frame before rotation, matching how the
    geometry module transforms points, so a handle sits where its corner is
    drawn rather than where an unmirrored box would put it.
    """
    x, y, width, height = shape_box
    angle = math.radians(rotation % 360)
    cosine, sine = math.cos(angle), math.sin(angle)
    mx, my = (-1 if mirror_x else 1), (-1 if mirror_y else 1)
    centre = (x + width / 2, y + height / 2)
    return centre, (mx * cosine, mx * sine), (-my * sine, my * cosine)


def transformed_point(shape_box, rotation, mirror_x, mirror_y, offset):
    """World position of a point given as an offset from the box centre."""
    centre, axis_x, axis_y = _frame(shape_box, rotation, mirror_x, mirror_y)
    return (centre[0] + offset[0] * axis_x[0] + offset[1] * axis_y[0],
            centre[1] + offset[0] * axis_x[1] + offset[1] * axis_y[1])


def rotated_handles(shape_box, rotation, mirror_x=False, mirror_y=False, rotation_gap=0.0):
    """Corner handles where they are actually drawn, plus the rotation grip.

    ``rotation_gap`` places the grip that far beyond the top edge; pass a
    distance in bed millimetres so it stays a constant size on screen.
    """
    width, height = shape_box[2], shape_box[3]
    handles = {name: transformed_point(shape_box, rotation, mirror_x, mirror_y,
                                       (sx * width / 2, sy * height / 2))
               for name, (sx, sy) in CORNER_SIGNS.items()}
    if rotation_gap:
        handles[ROTATE_HANDLE] = transformed_point(shape_box, rotation, mirror_x, mirror_y,
                                                   (0, height / 2 + rotation_gap))
    return handles


def resize_from_handle(handle, pointer, shape_box, rotation, mirror_x=False, mirror_y=False,
                       minimum=0.0, keep_ratio=False):
    """New x/y/width/height for dragging ``handle`` to ``pointer``.

    The opposite corner is the anchor: it stays exactly where it was, so a
    rotated box grows along its own axes instead of the bed's. Returns the
    unrotated box, because rotation is stored separately.
    """
    if handle not in CORNER_SIGNS:
        raise KeyError(handle)
    anchor_name = OPPOSITE[handle]
    anchor = transformed_point(shape_box, rotation, mirror_x, mirror_y,
                               (CORNER_SIGNS[anchor_name][0] * shape_box[2] / 2,
                                CORNER_SIGNS[anchor_name][1] * shape_box[3] / 2))
    _, axis_x, axis_y = _frame(shape_box, rotation, mirror_x, mirror_y)
    delta = (pointer[0] - anchor[0], pointer[1] - anchor[1])
    # The axes are orthonormal, so projecting onto them inverts the rotation.
    width = abs(delta[0] * axis_x[0] + delta[1] * axis_x[1])
    height = abs(delta[0] * axis_y[0] + delta[1] * axis_y[1])
    if keep_ratio and shape_box[2] > 0 and shape_box[3] > 0:
        factor = max(width / shape_box[2], height / shape_box[3],
                     minimum / shape_box[2], minimum / shape_box[3])
        width, height = shape_box[2] * factor, shape_box[3] * factor
    width, height = max(minimum, width), max(minimum, height)
    # Put the centre back where it has to be for the anchor to have not moved.
    sx, sy = CORNER_SIGNS[anchor_name]
    centre_x = anchor[0] - (sx * width / 2) * axis_x[0] - (sy * height / 2) * axis_y[0]
    centre_y = anchor[1] - (sx * width / 2) * axis_x[1] - (sy * height / 2) * axis_y[1]
    return {"x": centre_x - width / 2, "y": centre_y - height / 2,
            "width": width, "height": height}


def rotation_from_pointer(pointer, shape_box, mirror_y=False, step=0.0):
    """Angle in degrees that points the box's top edge at ``pointer``.

    ``step`` snaps the result, so a held drag can land on exact angles.
    """
    centre = (shape_box[0] + shape_box[2] / 2, shape_box[1] + shape_box[3] / 2)
    dx, dy = pointer[0] - centre[0], pointer[1] - centre[1]
    if math.hypot(dx, dy) < 1e-9:
        return 0.0
    # The grip sits on the box's +y axis, which mirroring turns around.
    reference = 90.0 if not mirror_y else -90.0
    angle = math.degrees(math.atan2(dy, dx)) - reference
    if step > 0:
        angle = round(angle / step) * step
    return angle % 360


def zoom_pan_correction(before, after, scale):
    """Pan delta that keeps the bed point under the cursor fixed across a zoom.

    ``before`` and ``after`` are the same pointer position converted to bed
    coordinates with the old and new zoom. Returns ``(dx, dy)`` in canvas pixels;
    the y term is negated because the canvas y axis points down.
    """
    return (after[0] - before[0]) * scale, -(after[1] - before[1]) * scale


def proportional_size(old_width, old_height, width, height):
    """Resolve a single edited dimension, rejecting conflicting paired edits."""
    if not all(math.isfinite(v) and v > 0 for v in (old_width, old_height, width, height)):
        raise ValueError("Keep ratio requires non-zero width and height.")
    changed_w = not math.isclose(width, old_width)
    changed_h = not math.isclose(height, old_height)
    if changed_w and changed_h and not math.isclose(width/old_width, height/old_height, rel_tol=1e-5):
        raise ValueError("Keep ratio is on: edit only width or height, or turn it off.")
    factor = height/old_height if changed_h and not changed_w else width/old_width
    return old_width*factor, old_height*factor
