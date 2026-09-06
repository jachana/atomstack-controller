"""Job placement in positioning-mark coordinates, independent of machine motion."""
from dataclasses import replace
import math
from .geometry import shape_bounds, BED_X, BED_Y

ANCHORS = {"Bottom-left": (0, 0), "Bottom-center": (.5, 0), "Bottom-right": (1, 0),
           "Middle-left": (0, .5), "Center": (.5, .5), "Middle-right": (1, .5),
           "Top-left": (0, 1), "Top-center": (.5, 1), "Top-right": (1, 1)}


def reachable_x(offset):
    if not math.isfinite(offset) or abs(offset) > 50:
        raise ValueError("Beam offset must be between -50 and 50 mm.")
    return max(0, offset), min(BED_X, BED_X + offset)


def place_shapes(shapes, anchor, target, offset):
    if not shapes or anchor not in ANCHORS or len(target) != 2 or not all(math.isfinite(v) for v in target):
        raise ValueError("Choose geometry, an anchor, and finite X/Y coordinates.")
    bounds = [shape_bounds(s) for s in shapes]
    left, bottom = min(b[0] for b in bounds), min(b[1] for b in bounds)
    right, top = max(b[2] for b in bounds), max(b[3] for b in bounds)
    ax, ay = ANCHORS[anchor]
    dx, dy = target[0] - (left + ax*(right-left)), target[1] - (bottom + ay*(top-bottom))
    low, high = reachable_x(offset)
    if left+dx < low-1e-7 or right+dx > high+1e-7 or bottom+dy < -1e-7 or top+dy > BED_Y+1e-7:
        raise ValueError(f"Placement exceeds cutting reach: X {low:g}–{high:g}, Y 0–{BED_Y:g} mm. Move inward.")
    return [replace(s, x=s.x+dx, y=s.y+dy).validated() for s in shapes]
