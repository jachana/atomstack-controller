"""Image loading and the operations needed to get outlines out of a picture.

The operations are the familiar ones — grayscale, levels, blur, threshold, edge
detection, contour tracing, simplification — implemented here on numpy arrays
rather than pulled in from OpenCV, which would multiply the size of the packaged
executable for a page of arithmetic. They are kept behind this module so the
implementation can be swapped without touching anything that calls it.

Nothing here knows about the machine. It turns a file into polylines in
millimetres, and the one function that builds geometry from them does it the
same way the SVG and DXF importers do.
"""
import math

import numpy as np
from PIL import Image, ImageOps

from .geometry import path_shape

MAX_PIXELS = 4_000_000  # About 2000x2000. Bigger images are scaled down first.
MAX_CONTOUR_POINTS = 100_000


def load_grayscale(path, max_pixels=MAX_PIXELS):
    """Read any supported image as a float array of 0..1 brightness."""
    try:
        with Image.open(path) as opened:
            opened.draft("L", opened.size)  # Let JPEG decode smaller if it can.
            # A photograph from a phone is usually stored in the sensor's
            # orientation with a tag saying which way is up. Without this the
            # outline comes out turned on its side from what the operator saw.
            picture = ImageOps.exif_transpose(opened).convert("L")
            if picture.width * picture.height > max_pixels:
                scale = math.sqrt(max_pixels / (picture.width * picture.height))
                picture = picture.resize((max(1, int(picture.width * scale)),
                                          max(1, int(picture.height * scale))),
                                         Image.LANCZOS)
            grey = np.asarray(picture, dtype=np.float32) / 255.0
    except OSError as exc:
        raise ValueError(f"Could not read the image: {exc}") from exc
    if grey.ndim != 2 or grey.size == 0:
        raise ValueError("That file does not contain a usable image.")
    return grey


def adjust(grey, brightness=0.0, contrast=1.0, gamma=1.0, invert=False):
    """Levels, in the order a darkroom would apply them."""
    if not all(map(math.isfinite, (brightness, contrast, gamma))):
        raise ValueError("Image adjustments must be finite numbers.")
    if not 0.05 <= gamma <= 20 or not 0 <= contrast <= 10 or not -1 <= brightness <= 1:
        raise ValueError("Brightness -1..1, contrast 0..10, gamma 0.05..20.")
    out = (grey - 0.5) * contrast + 0.5 + brightness
    out = np.clip(out, 0.0, 1.0) ** (1.0 / gamma)
    return 1.0 - out if invert else out


def blur(grey, radius=1.0):
    """Separable box blur, repeated three times to approximate a Gaussian."""
    if not math.isfinite(radius) or not 0 <= radius <= 50:
        raise ValueError("Blur radius must be between 0 and 50 pixels.")
    size = int(round(radius))
    if size < 1:
        return grey
    kernel = np.ones(2 * size + 1, dtype=np.float32) / (2 * size + 1)
    out = grey
    for _ in range(3):
        out = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="same"), 1, out)
        out = np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="same"), 0, out)
    return out


def otsu_level(grey):
    """The threshold that best separates the histogram into two groups."""
    counts, _ = np.histogram(grey, bins=256, range=(0.0, 1.0))
    weights = counts.astype(np.float64)
    total = weights.sum()
    if total == 0:
        return 0.5
    values = (np.arange(256) + 0.5) / 256.0
    weight_low = np.cumsum(weights)
    weight_high = total - weight_low
    sum_low = np.cumsum(weights * values)
    sum_total = sum_low[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_low = np.where(weight_low > 0, sum_low / weight_low, 0.0)
        mean_high = np.where(weight_high > 0, (sum_total - sum_low) / weight_high, 0.0)
        between = weight_low * weight_high * (mean_low - mean_high) ** 2
    # A clean image leaves a plateau of equally good thresholds between its two
    # populations. Taking the first of them puts the level on the edge of the
    # dark cluster, where a little noise flips pixels; the middle is stable.
    best = np.nanmax(between)
    plateau = np.flatnonzero(between >= best - 1e-12)
    return float(values[int(plateau[len(plateau) // 2])])


def threshold(grey, level=None):
    """Mask of the dark side of ``level``; Otsu picks it when none is given."""
    if level is None:
        level = otsu_level(grey)
    if not math.isfinite(level) or not 0.0 <= level <= 1.0:
        raise ValueError("Threshold level must be between 0 and 1.")
    return grey <= level


def edges(grey, level=0.25):
    """Sobel gradient magnitude, thresholded: the outlines in a photograph."""
    if not math.isfinite(level) or not 0.0 < level <= 1.0:
        raise ValueError("Edge level must be between 0 and 1.")
    padded = np.pad(grey, 1, mode="edge")
    gx = (padded[:-2, 2:] + 2 * padded[1:-1, 2:] + padded[2:, 2:]
          - padded[:-2, :-2] - 2 * padded[1:-1, :-2] - padded[2:, :-2])
    gy = (padded[2:, :-2] + 2 * padded[2:, 1:-1] + padded[2:, 2:]
          - padded[:-2, :-2] - 2 * padded[:-2, 1:-1] - padded[:-2, 2:])
    magnitude = np.hypot(gx, gy) / 4.0
    return magnitude >= level


# Marching squares. Each case lists the edge midpoints a cell contributes, as
# pairs, walking so the filled side stays on the left.
EDGE_TOP, EDGE_RIGHT, EDGE_BOTTOM, EDGE_LEFT = 0, 1, 2, 3
CASES = {
    1: [(EDGE_LEFT, EDGE_BOTTOM)], 2: [(EDGE_BOTTOM, EDGE_RIGHT)],
    3: [(EDGE_LEFT, EDGE_RIGHT)], 4: [(EDGE_RIGHT, EDGE_TOP)],
    5: [(EDGE_LEFT, EDGE_TOP), (EDGE_RIGHT, EDGE_BOTTOM)],
    6: [(EDGE_BOTTOM, EDGE_TOP)], 7: [(EDGE_LEFT, EDGE_TOP)],
    8: [(EDGE_TOP, EDGE_LEFT)], 9: [(EDGE_TOP, EDGE_BOTTOM)],
    10: [(EDGE_TOP, EDGE_RIGHT), (EDGE_BOTTOM, EDGE_LEFT)],
    11: [(EDGE_TOP, EDGE_RIGHT)], 12: [(EDGE_RIGHT, EDGE_LEFT)],
    13: [(EDGE_RIGHT, EDGE_BOTTOM)], 14: [(EDGE_BOTTOM, EDGE_LEFT)],
}


def _midpoint(row, column, edge):
    if edge == EDGE_TOP:
        return (column + 0.5, row)
    if edge == EDGE_RIGHT:
        return (column + 1.0, row + 0.5)
    if edge == EDGE_BOTTOM:
        return (column + 0.5, row + 1.0)
    return (column, row + 0.5)


def contours(mask, max_points=MAX_CONTOUR_POINTS):
    """Closed outlines around every filled region, in pixel coordinates.

    Holes come out as their own loops, wound the other way, which is what the
    even-odd rule used everywhere else in this app expects.
    """
    filled = np.pad(np.asarray(mask, dtype=bool), 1, mode="constant")
    if not filled.any():
        raise ValueError("Nothing to trace: the image is blank at this setting.")
    top_left = filled[:-1, :-1]
    top_right = filled[:-1, 1:]
    bottom_right = filled[1:, 1:]
    bottom_left = filled[1:, :-1]
    index = (top_left << 3) | (top_right << 2) | (bottom_right << 1) | bottom_left

    links = {}
    total = 0
    for row, column in zip(*np.nonzero((index > 0) & (index < 15))):
        for start_edge, end_edge in CASES[int(index[row, column])]:
            start = _midpoint(int(row), int(column), start_edge)
            end = _midpoint(int(row), int(column), end_edge)
            links[start] = end
            total += 1
            if total > max_points:
                raise ValueError("This image traces to too many points. "
                                 "Blur it, simplify it, or use a smaller size.")

    loops = []
    while links:
        start = next(iter(links))
        loop = [start]
        point = start
        while True:
            following = links.pop(point, None)
            if following is None:
                break
            loop.append(following)
            point = following
            if point == start:
                break
        if len(loop) > 3:
            if loop[-1] != loop[0]:
                loop.append(loop[0])
            loops.append(loop)
    return loops


def simplify(points, tolerance):
    """Douglas-Peucker, to keep an outline from carrying a point per pixel."""
    if tolerance <= 0 or len(points) < 3:
        return list(points)
    start, end = points[0], points[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    worst, index = -1.0, 0
    for position, point in enumerate(points[1:-1], 1):
        if length < 1e-12:
            distance = math.dist(point, start)
        else:
            distance = abs(dy * point[0] - dx * point[1] + end[0] * start[1]
                           - end[1] * start[0]) / length
        if distance > worst:
            worst, index = distance, position
    if worst <= tolerance:
        return [start, end]
    return simplify(points[:index + 1], tolerance)[:-1] + simplify(points[index:], tolerance)


def outline_paths(mask, width_mm, tolerance_mm=0.1, min_area_mm=1.0):
    """Traced outlines placed in millimetres, largest first.

    ``width_mm`` sets the scale; height follows the image's own proportions, so
    an imported picture is never stretched.
    """
    if not math.isfinite(width_mm) or not 1 <= width_mm <= 2000:
        raise ValueError("Image width must be between 1 and 2000 mm.")
    height, width = np.asarray(mask).shape
    scale = width_mm / width
    paths = []
    for loop in contours(mask):
        simplified = simplify(loop, tolerance_mm / scale if scale else 0.0)
        if len(simplified) < 4:
            continue
        millimetres = [(x * scale, (height - y) * scale) for x, y in simplified]
        area = abs(sum(a[0] * b[1] - b[0] * a[1]
                       for a, b in zip(millimetres, millimetres[1:]))) / 2
        if area >= min_area_mm:
            paths.append((area, millimetres))
    if not paths:
        raise ValueError("No outline survived at this size and tolerance. "
                         "Try a larger width or a smaller simplify tolerance.")
    return [path for _, path in sorted(paths, key=lambda item: item[0], reverse=True)]


MODES = ("outline", "edges")


def image_shapes(path, width_mm=100.0, mode="outline", level=None, blur_radius=0.0,
                 brightness=0.0, contrast=1.0, gamma=1.0, invert=False,
                 tolerance_mm=0.1, min_area_mm=1.0, **settings):
    """One object holding every outline traced from the image.

    The contours stay together in a single object so holes remain holes: the
    counter of a letter or the gap in a washer is a loop inside another loop,
    which is what the even-odd rule reads as empty.

    ``outline`` traces the boundary between light and dark, which suits line art
    and silhouettes. ``edges`` traces the gradient instead, which is what gets a
    drawing out of a photograph.
    """
    if mode not in MODES:
        raise ValueError("Choose outline or edges.")
    grey = load_grayscale(path)
    grey = adjust(grey, brightness, contrast, gamma, invert)
    grey = blur(grey, blur_radius)
    mask = edges(grey, 0.25 if level is None else level) if mode == "edges"         else threshold(grey, level)
    paths = outline_paths(mask, width_mm, tolerance_mm, min_area_mm)
    return [path_shape(paths, **settings)]


def resample(grey, columns, rows):
    """Area-average an image down to the grid it will actually be engraved on.

    Sweeping at a 0.2 mm line interval resolves five points per millimetre;
    feeding it twenty is not more detail, it is more commands describing detail
    the beam cannot place.
    """
    if columns < 1 or rows < 1:
        raise ValueError("Engraving grid must have at least one row and column.")
    if (rows, columns) == np.asarray(grey).shape:
        return np.asarray(grey, dtype=np.float32)
    picture = Image.fromarray((np.clip(grey, 0, 1) * 255).astype(np.uint8), mode="L")
    resized = picture.resize((int(columns), int(rows)), Image.BOX)
    return np.asarray(resized, dtype=np.float32) / 255.0
