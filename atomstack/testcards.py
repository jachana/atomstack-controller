"""Test cards for finding settings, with the settings burned onto the card.

A grid of squares at different speeds and powers is only useful while it is
still on the machine. Once it is lifted off, an unlabelled card is a piece of
scrap with marks on it. Both cards here burn their own axis labels, so the card
answers the question later, on the bench, without the app open.

Labels are cut at their own fixed setting rather than the setting of the cell
they describe, because the point of the exercise is that some of those cells
will not mark the material at all.
"""
import math

from .geometry import BED_X, BED_Y, Shape

MAX_STEPS = 10
LABEL_HEIGHT = 5.0          # Millimetres. Outline text much smaller than this
                            # reads as a smudge once it is burnt into grain.
MIN_LABEL, MAX_LABEL = 4.0, 9.0
LABEL_INTERVAL = 0.15       # Fill spacing: solid strokes, not hollow outlines.
CHARACTER_WIDTH = 0.66      # Of the label height, near enough for layout.
# The power labels sit to the left of the first column, so a card occupies this
# much more bed than its cells do. Leaving it out of the fit check told the
# operator a card fitted and then refused to send it.
def left_margin(cell_height):
    """Room the power labels need beside the first column."""
    height = label_height(cell_height)
    return height * CHARACTER_WIDTH * 4 + height * 0.6


LEFT_MARGIN = LABEL_HEIGHT * CHARACTER_WIDTH * 4 + LABEL_HEIGHT * 0.6


def _label(text, x, y, height, speed, power, font="Arial"):
    """A filled label.

    Outline text at this size leaves two hairlines per stroke, which on grain is
    hard to read and easy to mistake for the grain itself. Filling it costs a
    little time and gives a solid mark.
    """
    width = max(height * CHARACTER_WIDTH * len(text), height)
    return Shape("text", x, y, width, height, speed=speed, power=power, passes=1,
                 text=text, font_family=font, mode="fill", interval=LABEL_INTERVAL)


def label_height(cell_height):
    """Labels grow with the cells, within what stays legible and affordable."""
    return min(MAX_LABEL, max(MIN_LABEL, cell_height * 0.32))


def _check(speeds, powers, cell_width, cell_height, gap):
    if not speeds or not powers or len(speeds) > MAX_STEPS or len(powers) > MAX_STEPS:
        raise ValueError(f"Use between 1 and {MAX_STEPS} speeds and power levels.")
    if any(not 60 <= int(s) <= 20000 for s in speeds):
        raise ValueError("Speeds must be between 60 and 20,000 mm/min.")
    if any(not 0 <= int(p) <= 1000 for p in powers):
        raise ValueError("Power must be between 0 and 1000.")
    if cell_width < 5 or cell_height < 5:
        raise ValueError("Test cells must be at least 5 mm across.")
    if gap < 0 or not math.isfinite(gap):
        raise ValueError("The gap between cells cannot be negative.")


def _grid_labels(x, y, speeds, powers, cell_width, cell_height, gap,
                 label_speed, label_power, font):
    """Speeds along the top, powers down the left, and what they mean."""
    height = label_height(cell_height)
    margin = left_margin(cell_height)
    shapes = []
    top = y + len(powers) * (cell_height + gap) - gap
    for column, speed in enumerate(speeds):
        text = f"{int(speed)}"
        width = max(height * CHARACTER_WIDTH * len(text), height)
        # Centred over its column, so a label belongs to a column by eye.
        shapes.append(_label(text, x + column * (cell_width + gap) + (cell_width - width) / 2,
                             top + height * 0.6, height, label_speed, label_power, font))
    for row, power in enumerate(powers):
        text = f"{int(power)}"
        width = max(height * CHARACTER_WIDTH * len(text), height)
        shapes.append(_label(text, x - margin + (margin - width - height * 0.3),
                             y + row * (cell_height + gap) + (cell_height - height) / 2,
                             height, label_speed, label_power, font))
    shapes.append(_label("F across  S down", x, top + height * 2.2, height * 0.8,
                         label_speed, label_power, font))
    return shapes


def cut_card(x, y, speeds, powers, cell_width=12.0, cell_height=12.0, gap=3.0,
             passes=1, label_speed=3000, label_power=300, font="Arial"):
    """Squares cut at every combination, to find what goes through the material.

    The cells are outlines rather than filled: cutting through is what is being
    looked for, and a filled cell would burn the middle away either way.
    """
    _check(speeds, powers, cell_width, cell_height, gap)
    if not 1 <= int(passes) <= 20:
        raise ValueError("Passes must be between 1 and 20.")
    shapes = []
    for row, power in enumerate(powers):
        for column, speed in enumerate(speeds):
            shapes.append(Shape("rectangle",
                                x + column * (cell_width + gap),
                                y + row * (cell_height + gap),
                                cell_width, cell_height,
                                int(speed), int(power), int(passes),
                                note=f"F{int(speed)} · S{int(power)}"))
    shapes.extend(_grid_labels(x, y, speeds, powers, cell_width, cell_height, gap,
                               label_speed, label_power, font))
    return [shape.validated() for shape in shapes]


def tonal_pattern(size=96):
    """A patch that shows what a setting does: a ramp, plus solid and white.

    A photograph would test the same settings, but a known ramp is readable as a
    result rather than as a picture: where it goes black is where the material
    saturates, and where it stops changing is where more power buys nothing.
    """
    import numpy as np
    grey = np.zeros((size, size), dtype=np.float32)
    ramp = np.linspace(1.0, 0.0, size, dtype=np.float32)
    grey[:, :] = ramp[None, :]
    band = max(2, size // 8)
    grey[:band, :] = 0.0            # solid, to show maximum burn
    grey[-band:, :] = 1.0           # blank, to show the unburnt material
    return grey


def engraving_card(x, y, speeds, powers, image=None, cell_width=16.0, cell_height=16.0,
                   gap=4.0, interval=0.2, label_speed=3000, label_power=300, font="Arial"):
    """The same patch engraved at every combination of speed and power.

    Every cell carries its own copy of the picture, so the card is a design like
    any other: it saves, reopens and sends without needing the original file.
    """
    from .raster import encode

    _check(speeds, powers, cell_width, cell_height, gap)
    grey = tonal_pattern() if image is None else image
    stored = encode(grey)
    shapes = []
    for row, power in enumerate(powers):
        for column, speed in enumerate(speeds):
            shapes.append(Shape("raster",
                                x + column * (cell_width + gap),
                                y + row * (cell_height + gap),
                                cell_width, cell_height,
                                int(speed), int(power), 1,
                                image=stored, interval=interval,
                                note=f"F{int(speed)} · S{int(power)}"))
    shapes.extend(_grid_labels(x, y, speeds, powers, cell_width, cell_height, gap,
                               label_speed, label_power, font))
    return [shape.validated() for shape in shapes]


def card_size(speeds, powers, cell_width, cell_height, gap):
    """How much bed a card takes, counting the labels on both axes."""
    label = label_height(cell_height)
    width = left_margin(cell_height) + len(speeds) * (cell_width + gap) - gap
    height = len(powers) * (cell_height + gap) - gap + label * 3.5
    return width, height


def fits_bed(x, y, speeds, powers, cell_width, cell_height, gap):
    """Whether a card placed here stays on the bed, labels and all."""
    width, height = card_size(speeds, powers, cell_width, cell_height, gap)
    margin = left_margin(cell_height)
    return (x - margin >= 0 and y >= 0
            and x - margin + width <= BED_X and y + height <= BED_Y)
