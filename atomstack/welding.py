"""Union of closed outlines, so overlapping objects cut once rather than twice.

The method is an arrangement rather than a clipping library: every edge is cut
at each crossing with another object, the pieces that lie inside another object
are dropped, and what remains is chained back into closed loops. That is enough
for the union this app needs, and it keeps the reasoning about what survives a
boolean local to one readable rule.

An object's own filled region is decided by the even-odd rule over its own
contours, which is how a glyph's counters and an imported outline's holes are
already expressed everywhere else here.
"""
import math

EPSILON = 1e-9
# Points closer than this are the same point. Outlines arrive already flattened
# to a 0.05 mm chord tolerance, so this is far below anything meaningful.
WELD_TOLERANCE = 1e-6
# Every edge is tested against every other, so cost grows with the square of the
# outline detail. Five thousand edges takes a few seconds; past that the app
# would look frozen, so it says so instead.
MAX_WELD_EDGES = 5000


def _cross(origin, a, b):
    return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])


def point_in_region(point, contours):
    """Even-odd containment against a set of closed contours.

    Uses a half-open crossing rule so a point level with a vertex is counted
    once rather than twice.
    """
    inside = False
    x, y = point
    for contour in contours:
        for (x0, y0), (x1, y1) in zip(contour, contour[1:]):
            if (y0 > y) != (y1 > y):
                crossing = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                if crossing > x:
                    inside = not inside
    return inside


def _distance_to_edge(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    if length < EPSILON:
        return math.dist(point, a)
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length))
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


def on_boundary(point, contours, tolerance=WELD_TOLERANCE):
    """True when the point sits on a contour rather than within it."""
    return any(_distance_to_edge(point, a, b) <= tolerance for a, b in _edges(contours))


SIDE_STEP = 1e-6  # How far off an edge to look when asking which side is solid.


def _sides(piece, regions):
    """Whether the union covers the ground just left and just right of a piece.

    Asking what lies either side of an edge settles the cases that defeat a
    midpoint test: two objects meeting edge to edge have solid on both sides, so
    the shared edge is interior and goes; two objects overlapping along the same
    line have solid on one side only, so that edge is union boundary and stays.
    """
    (x0, y0), (x1, y1) = piece
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < WELD_TOLERANCE:
        return False, False
    nx, ny = -dy / length, dx / length
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    left = any(point_in_region((mx + nx * SIDE_STEP, my + ny * SIDE_STEP), r) for r in regions)
    right = any(point_in_region((mx - nx * SIDE_STEP, my - ny * SIDE_STEP), r) for r in regions)
    return left, right


def _split_points(start, end, others):
    """Parameters along start->end where it meets any edge in ``others``."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    cuts = []
    for (a, b) in others:
        ax, ay = b[0] - a[0], b[1] - a[1]
        denominator = dx * ay - dy * ax
        if abs(denominator) < EPSILON:
            continue  # Parallel, including collinear overlaps.
        t = ((a[0] - start[0]) * ay - (a[1] - start[1]) * ax) / denominator
        u = ((a[0] - start[0]) * dy - (a[1] - start[1]) * dx) / denominator
        if -EPSILON <= t <= 1 + EPSILON and -EPSILON <= u <= 1 + EPSILON:
            cuts.append(min(1.0, max(0.0, t)))
    return cuts


def _edges(contours):
    return [(a, b) for contour in contours for a, b in zip(contour, contour[1:])
            if math.dist(a, b) > WELD_TOLERANCE]


def _chain(segments):
    """Join segments end to end into closed loops."""
    def key(point):
        return (round(point[0] / WELD_TOLERANCE), round(point[1] / WELD_TOLERANCE))

    starts = {}
    for segment in segments:
        starts.setdefault(key(segment[0]), []).append(segment)
    loops = []
    remaining = len(segments)
    while remaining > 0:
        start = next((s for bucket in starts.values() for s in bucket), None)
        if start is None:
            break
        loop = [start[0]]
        current = start
        while current is not None:
            bucket = starts.get(key(current[0]), [])
            if current in bucket:
                bucket.remove(current)
                remaining -= 1
            loop.append(current[1])
            if math.dist(loop[0], current[1]) <= WELD_TOLERANCE:
                break
            following = starts.get(key(current[1]), [])
            current = following[0] if following else None
        if len(loop) > 3:
            loop[-1] = loop[0]  # Close exactly on the point it started from.
            loops.append(loop)
        starts = {k: v for k, v in starts.items() if v}
    return loops


def weld(regions):
    """Union of several regions, each a list of closed contours.

    Returns closed contours bounding the union, including any hole the objects
    enclose between them.
    """
    if len(regions) < 2:
        raise ValueError("Welding needs at least two objects.")
    for contours in regions:
        for contour in contours:
            if len(contour) < 4 or math.dist(contour[0], contour[-1]) > WELD_TOLERANCE:
                raise ValueError("Welding requires closed outlines. Close open paths first.")

    total = sum(len(_edges(contours)) for contours in regions)
    if total > MAX_WELD_EDGES:
        raise ValueError(f"These outlines carry {total} segments, more than welding can "
                         f"handle at once. Weld fewer objects, or simplify them first.")

    kept = []
    for index, contours in enumerate(regions):
        others = [region for position, region in enumerate(regions) if position != index]
        other_edges = [edge for region in others for edge in _edges(region)]
        for start, end in _edges(contours):
            cuts = sorted({0.0, 1.0, *_split_points(start, end, other_edges)})
            for first, second in zip(cuts, cuts[1:]):
                if second - first < EPSILON:
                    continue
                piece = ((start[0] + (end[0] - start[0]) * first,
                          start[1] + (end[1] - start[1]) * first),
                         (start[0] + (end[0] - start[0]) * second,
                          start[1] + (end[1] - start[1]) * second))
                left, right = _sides(piece, regions)
                if left == right:
                    continue  # Interior to the union, or outside it entirely.
                # Orient every survivor with the solid side on its left, so the
                # loops come out consistently wound and chaining is unambiguous.
                kept.append(piece if left else (piece[1], piece[0]))

    # Two objects meeting exactly along an edge contribute it twice.
    unique, seen = [], set()
    for piece in kept:
        marker = tuple(round(v / WELD_TOLERANCE) for point in piece for v in point)
        if marker not in seen:
            seen.add(marker)
            unique.append(piece)

    loops = _chain(unique)
    if not loops:
        raise ValueError("Welding produced no outline; check that the objects overlap.")
    return loops
