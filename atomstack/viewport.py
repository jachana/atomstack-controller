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


def zoom_pan_correction(before, after, scale):
    """Pan delta that keeps the bed point under the cursor fixed across a zoom.

    ``before`` and ``after`` are the same pointer position converted to bed
    coordinates with the old and new zoom. Returns ``(dx, dy)`` in canvas pixels;
    the y term is negated because the canvas y axis points down.
    """
    return (after[0] - before[0]) * scale, -(after[1] - before[1]) * scale
