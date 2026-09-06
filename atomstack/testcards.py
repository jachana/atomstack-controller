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
LABEL_HEIGHT = 3.5          # Millimetres; smaller than this stops being legible.
CHARACTER_WIDTH = 0.62      # Of the label height, near enough for layout.


def _label(text, x, y, height, speed, power, font="Arial"):
    width = max(height * CHARACTER_WIDTH * len(text), height)
    return Shape("text", x, y, width, height, speed=speed, power=power, passes=1,
                 text=text, font_family=font)


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
    shapes = []
    top = y + len(powers) * (cell_height + gap)
    for column, speed in enumerate(speeds):
        shapes.append(_label(f"{int(speed)}", x + column * (cell_width + gap),
                             top + LABEL_HEIGHT, LABEL_HEIGHT,
                             label_speed, label_power, font))
    for row, power in enumerate(powers):
        shapes.append(_label(f"{int(power)}", x - LABEL_HEIGHT * CHARACTER_WIDTH * 4.5,
                             y + row * (cell_height + gap) + cell_height / 2,
                             LABEL_HEIGHT, label_speed, label_power, font))
    shapes.append(_label("mm/min across   power down", x,
                         top + LABEL_HEIGHT * 3, LABEL_HEIGHT,
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
    """How much bed a card will take, labels included."""
    width = len(speeds) * (cell_width + gap) - gap
    height = len(powers) * (cell_height + gap) - gap + LABEL_HEIGHT * 5
    return width, height


def fits_bed(x, y, speeds, powers, cell_width, cell_height, gap):
    width, height = card_size(speeds, powers, cell_width, cell_height, gap)
    return (x >= 0 and y >= 0 and x + width <= BED_X and y + height <= BED_Y)
