"""Turn a picture into scan lines whose laser power follows its greys.

Cutting follows an outline; engraving sweeps the whole area and varies power as
it goes. The machine reports $32=1, so it is in laser mode and scales power with
feed through acceleration, which is what makes a swept image come out evenly.

This module produces runs of constant power as data. Emitting them as G-code is
the geometry module's job, and validating what reaches the wire is the
controller's, so the arithmetic here can be tested on its own.
"""
import base64
import io
import math

import numpy as np
from PIL import Image

from .imaging import resample

MAX_RUNS = 200_000        # A job larger than this takes longer to send than to cut.
MIN_INTERVAL = 0.02       # Finer than the beam is width, so it only costs time.
MAX_INTERVAL = 2.0
RASTER_LEVELS = 32   # Power steps in an engraving; more only adds commands.


def engraving_grid(grey, box, interval):
    """The image resampled to one sample per line interval, both ways."""
    _, _, width, height = box
    columns = max(1, int(round(width / interval)))
    rows = max(1, int(round(height / interval)))
    return resample(grey, columns, rows)


def quantise(grey, levels, min_power, max_power, white_is_blank=True):
    """Map brightness to laser power in ``levels`` steps.

    Dark burns hardest: a photograph's blacks are where the most energy goes.
    White is left at zero so the head can skip it rather than sweep it at a
    power that still marks the material.
    """
    if not 2 <= levels <= 256:
        raise ValueError("Use between 2 and 256 power levels.")
    if not 0 <= min_power <= max_power <= 1000:
        raise ValueError("Power must rise from 0 to at most 1000.")
    darkness = np.clip(1.0 - np.asarray(grey, dtype=np.float32), 0.0, 1.0)
    steps = np.floor(darkness * levels).clip(0, levels - 1)
    power = min_power + (steps / max(1, levels - 1)) * (max_power - min_power)
    power = np.rint(power).astype(np.int32)
    if white_is_blank:
        power[steps == 0] = 0
    return power


def scan_runs(power, box, interval, bidirectional=True, max_runs=MAX_RUNS):
    """Rows of constant-power runs across the image, in millimetres.

    Each run is ``(start_x, end_x, power)`` and each row is ``(y, runs)``. Rows
    alternate direction when ``bidirectional``, because turning the head around
    at the end of every line and coming back empty doubles the sweeping.
    """
    if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
        raise ValueError(f"Line interval must be between {MIN_INTERVAL} and "
                         f"{MAX_INTERVAL} mm.")
    x0, y0, width, height = box
    if width <= 0 or height <= 0:
        raise ValueError("An engraved image needs a positive width and height.")
    power = np.asarray(power)
    rows_of_pixels, columns = power.shape
    line_count = max(1, int(round(height / interval)))
    pixel_width = width / columns

    rows, total = [], 0
    for line in range(line_count):
        # Sample the middle of each strip, and walk up the bed as y increases.
        centre = (line + 0.5) / line_count
        y = y0 + centre * height
        source = min(rows_of_pixels - 1, int((1.0 - centre) * rows_of_pixels))
        values = power[source]
        runs = []
        start = 0
        for column in range(1, columns + 1):
            if column < columns and values[column] == values[start]:
                continue
            level = int(values[start])
            if level > 0:
                runs.append((x0 + start * pixel_width, x0 + column * pixel_width, level))
            start = column
        if not runs:
            continue
        if bidirectional and line % 2:
            runs = [(end, begin, level) for begin, end, level in reversed(runs)]
        total += len(runs)
        if total > max_runs:
            raise ValueError(f"This image needs more than {max_runs} moves to engrave. "
                             "Use a coarser line interval, fewer levels, or a smaller size.")
        rows.append((y, runs))
    if not rows:
        raise ValueError("Nothing to engrave: every pixel came out blank at this setting.")
    return rows


def engraving_metrics(rows, speed):
    """How far the head sweeps and how long that takes, ignoring travel."""
    distance = sum(abs(end - start) for _, runs in rows for start, end, _ in runs)
    return {"rows": len(rows),
            "runs": sum(len(runs) for _, runs in rows),
            "burn_distance": distance,
            "seconds": distance / speed * 60 if speed else math.inf}


def encode(grey):
    """Store greys with the design, so a saved job does not depend on a file."""
    picture = Image.fromarray((np.clip(grey, 0, 1) * 255).astype(np.uint8), mode="L")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def decode(data):
    try:
        picture = Image.open(io.BytesIO(base64.b64decode(data)))
        return np.asarray(picture.convert("L"), dtype=np.float32) / 255.0
    except Exception as exc:
        raise ValueError(f"The stored image could not be read: {exc}") from exc


def shape_commands(shape, origin, feed):
    """The G-code that engraves one raster object, in machine coordinates.

    Each row is a jump to its first mark, then one move per run of constant
    power. Power is carried on the move itself, which is what laser mode is
    for: the beam changes with the position rather than between stops.
    """
    ox, oy = origin
    grid = engraving_grid(decode(shape.image),
                          (shape.x, shape.y, shape.width, shape.height), shape.interval)
    power = quantise(grid, levels=RASTER_LEVELS, min_power=0, max_power=shape.power)
    rows = scan_runs(power, (shape.x, shape.y, shape.width, shape.height), shape.interval)
    lines = []
    for y, runs in rows:
        here = runs[0][0]
        lines.append(f"G53 G0 X{ox + here:.3f} Y{oy + y:.3f}")
        lines.append("M4 S0")
        for start, end, level in runs:
            if abs(start - here) > 1e-6:
                # A gap of white between marks: cross it with the beam off.
                lines.append(f"G53 G1 X{ox + start:.3f} Y{oy + y:.3f} F{feed} S0")
            lines.append(f"G53 G1 X{ox + end:.3f} Y{oy + y:.3f} F{feed} S{level}")
            here = end
        lines.extend(("M5", "S0"))
    return lines
