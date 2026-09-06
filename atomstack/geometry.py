"""Geometry document and deterministic GRBL-compatible job generation."""
from dataclasses import dataclass, replace
from functools import lru_cache
import math
from .vector_text import ALIGNMENTS, FONT_FILES, text_paths

BED_X, BED_Y = 365.0, 305.0
# Stand-in rapid rate for a preview taken before the machine has reported its
# own $110/$111. Generated rapids carry no F word, so the real rate is the
# machine's; this only affects an estimate shown while disconnected.
ASSUMED_RAPID_FEED = 6000


@dataclass(frozen=True)
class Shape:
    kind: str
    x: float
    y: float
    width: float
    height: float
    speed: int = 1000
    power: int = 300
    passes: int = 1
    text: str = ""
    font_family: str = "Arial"
    note: str = ""
    rotation: float = 0.0
    mirror_x: bool = False
    mirror_y: bool = False
    layer: str = ""
    paths: tuple = ()
    mode: str = "line"
    interval: float = 0.2
    image: str = ""        # Base64 PNG of the greys to engrave, for kind "raster".
    line_spacing: float = 1.2
    letter_spacing: float = 0.0
    text_align: str = "left"

    def __post_init__(self):
        object.__setattr__(self, "paths", tuple(tuple(tuple(point) for point in path) for path in self.paths))

    def validated(self):
        if not isinstance(self.layer, str):
            raise ValueError("Layer must be a name.")
        values = (self.x, self.y, self.width, self.height)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Geometry values must be finite numbers.")
        if self.kind not in ("rectangle", "circle", "line", "text", "path", "raster"):
            raise ValueError("Unknown geometry type.")
        if self.kind == "raster":
            if not self.image or not isinstance(self.image, str):
                raise ValueError("An engraved image needs its picture stored with it.")
            if self.width <= 0 or self.height <= 0:
                raise ValueError("Width and height must be greater than zero.")
        elif self.kind in ("line", "path"):
            if self.width < 0 or self.height < 0 or (self.width == 0 and self.height == 0):
                raise ValueError("A line needs a non-zero horizontal or vertical length.")
        elif self.kind != "raster" and (self.width <= 0 or self.height <= 0):
            raise ValueError("Width and height must be greater than zero.")
        if not 60 <= self.speed <= 20000:
            raise ValueError("Speed must be between 60 and 20,000 mm/min.")
        if not 0 <= self.power <= 1000:
            raise ValueError("Power must be between 0 and 1000.")
        if not 1 <= self.passes <= 20:
            raise ValueError("Passes must be between 1 and 20.")
        if self.kind == "text":
            if not self.text or not self.text.strip() or len(self.text) > 200:
                raise ValueError("Text must contain 1–200 characters.")
            if len(self.text.splitlines()) > 20:
                raise ValueError("Text can span at most 20 lines.")
            if not all(character.isprintable() or character == chr(10) for character in self.text):
                raise ValueError("Text must contain printable characters and line breaks only.")
            if self.text_align not in ALIGNMENTS:
                raise ValueError("Text alignment must be left, center, or right.")
            if not math.isfinite(self.line_spacing) or not 0.5 <= self.line_spacing <= 4:
                raise ValueError("Line spacing must be between 0.5 and 4 times the text size.")
            if not math.isfinite(self.letter_spacing) or not -0.2 <= self.letter_spacing <= 2:
                raise ValueError("Letter spacing must be between -0.2 and 2 times the text size.")
            if self.font_family not in FONT_FILES:
                raise ValueError("Choose Arial, Segoe UI, or Consolas.")
        if self.mode not in ("line", "fill") or not math.isfinite(self.interval) or not 0.05 <= self.interval <= 5:
            raise ValueError("Choose line or fill with a line interval between 0.05 and 5 mm.")
        if self.kind == "path":
            if not self.paths or sum(map(len, self.paths)) > 100000:
                raise ValueError("Imported paths must contain at most 100,000 points.")
            if any(len(path) < 2 or any(len(pt) != 2 or not all(math.isfinite(v) and 0 <= v <= 1 for v in pt) for pt in path) for path in self.paths):
                raise ValueError("Invalid normalized path coordinates.")
        if self.mode == "fill" and any(math.dist(path[0], path[-1]) > 1e-7 for path in shape_paths(self)):
            raise ValueError("Fill requires closed outlines. Close open paths or use Line mode.")
        if not math.isfinite(self.rotation):
            raise ValueError("Rotation must be a finite angle.")
        if not isinstance(self.mirror_x, bool) or not isinstance(self.mirror_y, bool):
            raise ValueError("Mirror values must be true or false.")
        return self

    @property
    def label(self):
        angle = self.rotation % 360
        transform = f" · {angle:g}°" if angle else ""
        if self.mirror_x or self.mirror_y:
            transform += " · mirrored"
        if self.kind == "text":
            # Show the break, or a two-line label reads as one run-on word.
            preview = " / ".join(self.text.splitlines())[:20]
            return f"Text · {preview} · X {self.x:g} Y {self.y:g}{transform}"
        if self.note:
            return f"Test · {self.note}"
        return f"{self.kind.title()} · X {self.x:g} Y {self.y:g} · {self.width:g} × {self.height:g}{transform}"


@dataclass(frozen=True)
class CutLayer:
    name: str
    speed: int = 1000
    power: int = 300
    passes: int = 1
    enabled: bool = True
    mode: str = "line"
    interval: float = 0.2

    def validated(self):
        if not isinstance(self.name, str) or not self.name.strip() or self.name != self.name.strip() or len(self.name) > 40 or not self.name.isprintable():
            raise ValueError("Layer name must contain 1–40 printable characters without surrounding spaces.")
        if type(self.enabled) is not bool:
            raise ValueError("Layer output must be on or off.")
        if any(type(v) is not int for v in (self.speed, self.power, self.passes)):
            raise ValueError("Layer speed, power, and passes must be whole numbers.")
        Shape("rectangle", 0, 0, 1, 1, self.speed, self.power, self.passes, mode=self.mode, interval=self.interval).validated()
        return self


def entry_point(shape):
    """Where cutting this object starts, which is where the head must arrive."""
    return shape_paths(shape)[0][0]


def exit_point(shape):
    """Where cutting it ends, which is where the next move starts from."""
    return shape_paths(shape)[-1][-1]


def _encloses_box(outer, inner):
    """True when one bounding box strictly contains another."""
    ol, ob, orr, ot = outer
    il, ib, ir, it = inner
    return ol <= il and ob <= ib and orr >= ir and ot >= it and (
        ol < il or ob < ib or orr > ir or ot > it)


MAX_ORDERED = 400  # Beyond this, ordering costs more than the travel it saves.


def ordered_for_travel(items, start=(0.0, 0.0)):
    """Order objects to shorten travel, cutting enclosed objects first.

    ``items`` are (index, shape) pairs and come back reordered. An object that
    encloses another still waiting is held back: cutting a plate's outline
    before its own holes frees the part while there is still work to do on it.

    Entry and exit points and the containment relation are computed once, so the
    walk itself is a plain nearest-neighbour scan. Past ``MAX_ORDERED`` objects
    even that is too slow to run on every redraw, and design order is kept.
    """
    if len(items) > MAX_ORDERED:
        return list(items)
    entries = [entry_point(shape) for _, shape in items]
    exits = [exit_point(shape) for _, shape in items]
    boxes = [shape_bounds(shape) for _, shape in items]

    # blocked[i] counts objects still waiting that i encloses.
    encloses = [[] for _ in items]
    blocked = [0] * len(items)
    for i, outer in enumerate(boxes):
        for j, inner in enumerate(boxes):
            if i != j and _encloses_box(outer, inner):
                encloses[i].append(j)
                blocked[i] += 1

    remaining = set(range(len(items)))
    position = start
    result = []
    while remaining:
        ready = [i for i in remaining if blocked[i] == 0] or list(remaining)
        nearest = min(ready, key=lambda i: math.dist(position, entries[i]))
        remaining.discard(nearest)
        for i in range(len(items)):
            if nearest in encloses[i] and blocked[i]:
                blocked[i] -= 1
        result.append(items[nearest])
        position = exits[nearest]
    return result


class Document:
    def __init__(self):
        self.shapes = []
        self.layers = []
        # Shorten travel within each layer. Layer sequence is never reordered:
        # which layer runs first is the operator's decision, not a distance.
        self.optimise_order = True

    def validate_layers(self):
        names = [layer.validated().name for layer in self.layers]
        if len(set(names)) != len(names):
            raise ValueError("Layer names must be unique.")
        if any(shape.layer and shape.layer not in names for shape in self.shapes):
            raise ValueError("A shape refers to a missing cut layer.")
        layers = {layer.name: layer for layer in self.layers}
        for shape in self.shapes:
            if shape.layer:
                layer = layers[shape.layer]
                replace(shape, speed=layer.speed, power=layer.power, passes=layer.passes,
                        mode=layer.mode, interval=layer.interval).validated()

    def output_shapes(self):
        """Resolve one common output plan for Frame, preview, and machine sending.

        Named layers execute in list order; unassigned objects follow in design
        order and retain individual settings (including burn-test cells).
        """
        self.validate_layers()
        groups = []
        for layer in self.layers:
            if layer.enabled:
                groups.append([(i, replace(shape, speed=layer.speed, power=layer.power,
                                           passes=layer.passes, mode=layer.mode, interval=layer.interval).validated())
                               for i, shape in enumerate(self.shapes) if shape.layer == layer.name])
        groups.append([(i, shape.validated()) for i, shape in enumerate(self.shapes) if not shape.layer])
        return tuple(item for group in groups for item in group)

    def cut_plan(self, start=(0.0, 0.0)):
        """The output plan in the order it will actually be cut.

        Sending and previewing both come through here, so what the preview draws
        is the path the head takes. Ordering happens per layer and never across
        them: which layer runs first is the operator's decision, not a distance.
        """
        self.validate_layers()
        plan, position = [], start
        for layer in [l.name for l in self.layers if l.enabled] + [None]:
            group = [item for item in self.output_shapes()
                     if (item[1].layer == layer if layer else not item[1].layer)]
            if self.optimise_order and len(group) > 1:
                group = ordered_for_travel(group, position)
            plan.extend(group)
            if group:
                position = exit_point(group[-1][1])
        return tuple(plan)

    def to_payload(self):
        self.validate_layers()
        return {"format": "atomstack-design", "version": 5,
                "bed": {"width": BED_X, "height": BED_Y},
                "shapes": [shape.validated().__dict__ for shape in self.shapes],
                "layers": [layer.__dict__ for layer in self.layers],
                "optimise_order": self.optimise_order}

    @classmethod
    def from_payload(cls, data):
        if not isinstance(data, dict) or data.get("format") != "atomstack-design" or not isinstance(data.get("shapes"), list):
            raise ValueError("This is not an Atomstack design file.")
        version = data.get("version", 1)
        if type(version) is not int or version not in (1, 2, 3, 4, 5):
            raise ValueError(f"Unsupported Atomstack design version: {version}.")
        document = cls()
        document.shapes = [Shape(**item).validated() for item in data["shapes"]]
        if version >= 3:
            if not isinstance(data.get("layers"), list):
                raise ValueError("Design layers must be a list.")
            document.layers = [CutLayer(**item).validated() for item in data["layers"]]
        if "optimise_order" in data:
            if not isinstance(data["optimise_order"], bool):
                raise ValueError("Cut order optimisation must be on or off.")
            document.optimise_order = data["optimise_order"]
        document.validate_layers()
        return document

    def add(self, shape):
        self.shapes.append(shape.validated())
        return len(self.shapes) - 1

    def update(self, index, **changes):
        self.shapes[index] = replace(self.shapes[index], **changes).validated()

    def delete(self, index):
        del self.shapes[index]

    def add_burn_test(self, x, y, cell_width, cell_height, speeds, powers, gap=2.0, passes=1, mode="line", interval=0.2):
        if not speeds or not powers or len(speeds) > 10 or len(powers) > 10:
            raise ValueError("Burn test requires 1–10 speeds and 1–10 power levels.")
        if cell_width < 5 or cell_height < 5 or gap < 0:
            raise ValueError("Test cells must be at least 5 mm; gap cannot be negative.")
        indices = []
        candidates = []
        for row, power in enumerate(powers):
            for column, speed in enumerate(speeds):
                candidates.append(Shape("rectangle", x + column*(cell_width+gap), y + row*(cell_height+gap),
                                              cell_width, cell_height, int(speed), int(power), passes,
                                              note=f"F{int(speed)} · S{int(power)}", mode=mode, interval=interval).validated())
        for shape in candidates:
            indices.append(self.add(shape))
        return indices

    def offbed(self):
        """Enabled objects that lie off the bed, as (index, shape) pairs."""
        return tuple((index, shape) for index, shape in self.output_shapes() if outside_bed(shape))

    def require_on_bed(self, action):
        off = self.offbed()
        if off:
            names = ", ".join(str(index + 1) for index, _ in off[:4])
            more = "" if len(off) <= 4 else f" and {len(off) - 4} more"
            raise ValueError(
                f"Object {names}{more} {'lies' if len(off) == 1 else 'lie'} outside the "
                f"{BED_X:g} × {BED_Y:g} mm bed. Scale or move it in before {action}.")

    def frame_points(self, margin=2.0):
        output = self.output_shapes()
        if not output:
            raise ValueError("Enable a layer or add geometry before framing.")
        self.require_on_bed("framing")
        if not math.isfinite(margin) or margin < 0 or margin > 20:
            raise ValueError("Frame margin must be between 0 and 20 mm.")
        bounds = [shape_bounds(shape) for _, shape in output]
        left = max(0.0, min(value[0] for value in bounds) - margin)
        bottom = max(0.0, min(value[1] for value in bounds) - margin)
        right = min(BED_X, max(value[2] for value in bounds) + margin)
        top = min(BED_Y, max(value[3] for value in bounds) + margin)
        return ((left, bottom), (right, bottom), (right, top), (left, top), (left, bottom))

    def gcode(self, machine_origin=None):
        output = self.cut_plan()
        if not output:
            raise ValueError("Enable a layer or add geometry before sending a job.")
        self.require_on_bed("sending")
        if machine_origin is None or len(machine_origin) != 2 or not all(math.isfinite(v) for v in machine_origin):
            raise ValueError("A confirmed live machine origin is required for safe G-code export.")
        ox, oy = machine_origin
        lines = ["; Atomstack personal controller - machine-coordinate export",
                 f"; Confirmed machine origin: X{ox:.3f} Y{oy:.3f}", "G21", "G90", "M5", "S0"]
        for source_index, shape in output:
            index = source_index + 1
            shape.validated()
            paths = () if shape.kind == "raster" else burn_paths(shape)
            # Text may now span lines, and this goes inside a ';' comment:
            # anything that is not printable would end the comment and put the
            # rest of the operator's text on the wire as a command.
            safe_text = shape.text.encode("ascii", "replace").decode("ascii")
            safe_text = "".join(c if c.isprintable() else " " for c in safe_text)
            description = f"text '{safe_text}'" if shape.kind == "text" else shape.kind
            lines.append(f"; {index}: {description} X{shape.x:g} Y{shape.y:g} {shape.width:g}x{shape.height:g} - F{shape.speed} S{shape.power} - {shape.passes} pass(es)")
            if shape.kind == "raster":
                from .raster import shape_commands
                for pass_number in range(shape.passes):
                    lines.append(f"; pass {pass_number + 1}")
                    lines.extend(shape_commands(shape, machine_origin, shape.speed))
                continue
            for pass_number in range(shape.passes):
                lines.append(f"; pass {pass_number + 1}")
                for points in paths:
                    lines.extend((f"G53 G0 X{ox + points[0][0]:.3f} Y{oy + points[0][1]:.3f}", f"M4 S{shape.power}"))
                    lines.extend(f"G53 G1 X{ox + x:.3f} Y{oy + y:.3f} F{shape.speed}" for x, y in points[1:])
                    lines.extend(("M5", "S0"))
        lines.extend(("M5", "S0", "; End - generated by Atomstack personal controller"))
        return "\n".join(lines) + "\n"

    def preview_segments(self, start=(0.0, 0.0), rapid_feed=None):
        """Return the exact ordered rapid/burn geometry used by generated jobs.

        Generated rapids carry no F word, so the machine runs them at its own
        rate. Callers that have read $110/$111 pass it in; ASSUMED_RAPID_FEED
        stands in only until the machine has said what it can do.
        """
        if not self.output_shapes():
            raise ValueError("Enable a layer or add geometry before previewing.")
        if len(start) != 2 or not all(math.isfinite(value) for value in start):
            raise ValueError("Preview start must be a finite X/Y point.")
        output = self.cut_plan(start)
        rapid_feed = rapid_feed or ASSUMED_RAPID_FEED
        segments = []
        current = tuple(start)
        for shape_index, shape in output:
            shape.validated()
            for pass_index in range(shape.passes):
                for points in burn_paths(shape):
                    first = points[0]
                    if current != first:
                        segments.append(("rapid", current, first, rapid_feed, 0, shape_index, pass_index))
                    for point in points[1:]:
                        segments.append(("burn", first, point, shape.speed, shape.power, shape_index, pass_index))
                        first = point
                    current = first
        return tuple(segments)

    def job_metrics(self, start=(0.0, 0.0), rapid_feed=None):
        segments = self.preview_segments(start, rapid_feed)
        rapid_distance = burn_distance = seconds = 0.0
        for kind, first, second, feed, _power, _shape, _pass in segments:
            distance = math.hypot(second[0] - first[0], second[1] - first[1])
            if kind == "rapid":
                rapid_distance += distance
            else:
                burn_distance += distance
            seconds += distance / feed * 60
        return {"segments": len(segments), "rapid_distance": rapid_distance,
                "burn_distance": burn_distance, "estimated_seconds": seconds,
                "max_power": max(shape.power for _, shape in self.output_shapes())}


def path_points(shape):
    return shape_paths(shape)[0]


def _base_shape_paths(shape):
    x, y, w, h = shape.x, shape.y, shape.width, shape.height
    if shape.kind == "raster":
        # An engraving is swept, not traced, but its box is what everything
        # else needs: bounds, selection, the frame outline and placement.
        return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
    if shape.kind == "path":
        return [[(x+px*w, y+py*h) for px, py in path] for path in shape.paths]
    if shape.kind == "rectangle":
        return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
    if shape.kind == "line":
        return [[(x, y), (x + w, y + h)]]
    if shape.kind == "text":
        return text_paths(shape.text, x, y, w, h, shape.font_family,
                          shape.line_spacing, shape.letter_spacing, shape.text_align)
    cx, cy = x + w / 2, y + h / 2
    return [[(cx + w / 2 * math.cos(2 * math.pi * i / 72),
              cy + h / 2 * math.sin(2 * math.pi * i / 72)) for i in range(73)]]


@lru_cache(maxsize=512)
def _cached_shape_paths(shape):
    paths = _base_shape_paths(shape)
    angle = math.radians(shape.rotation % 360)
    cosine, sine = math.cos(angle), math.sin(angle)
    cx, cy = shape.x + shape.width/2, shape.y + shape.height/2
    transformed = []
    for path in paths:
        points = []
        for x, y in path:
            local_x, local_y = x-cx, y-cy
            if shape.mirror_x:
                local_x = -local_x
            if shape.mirror_y:
                local_y = -local_y
            points.append((cx + local_x*cosine-local_y*sine,
                           cy + local_x*sine+local_y*cosine))
        transformed.append(tuple(points))
    return tuple(transformed)


def shape_paths(shape):
    return [list(path) for path in _cached_shape_paths(shape)]



def outside_bed(shape):
    """True when any part of the shape lies off the bed.

    Geometry is allowed to sit there. An import can arrive larger than the
    machine and be scaled or moved in, which cannot happen if the document
    refuses to hold it. What must not happen is sending it, so the machine
    facing paths ask this question instead of the constructor.
    """
    left, bottom, right, top = shape_bounds(shape)
    tolerance = 1e-7
    return (left < -tolerance or bottom < -tolerance
            or right > BED_X + tolerance or top > BED_Y + tolerance)

def shape_bounds(shape):
    points = [point for path in _cached_shape_paths(shape) for point in path]
    def stable(value):
        if abs(value) < 1e-10:
            return 0.0
        if abs(value-BED_X) < 1e-10:
            return BED_X
        if abs(value-BED_Y) < 1e-10:
            return BED_Y
        return value
    return tuple(stable(value) for value in
                 (min(point[0] for point in points), min(point[1] for point in points),
                  max(point[0] for point in points), max(point[1] for point in points)))


def path_shape(paths, **settings):
    """Store world paths as normalized geometry so ordinary transforms still work."""
    points = [point for path in paths for point in path]
    if not points:
        raise ValueError("No usable paths found.")
    left, right = min(p[0] for p in points), max(p[0] for p in points)
    bottom, top = min(p[1] for p in points), max(p[1] for p in points)
    width, height = right-left, top-bottom
    normalized = tuple(tuple(((x-left)/width if width else 0, (y-bottom)/height if height else 0)
                              for x, y in path) for path in paths)
    return Shape("path", left, bottom, width, height, paths=normalized, **settings).validated()


@lru_cache(maxsize=128)
def burn_paths(shape):
    paths = shape_paths(shape)
    if shape.mode == "line":
        return tuple(tuple(path) for path in paths)
    if any(math.dist(path[0], path[-1]) > 1e-7 for path in paths):
        raise ValueError("Fill requires closed outlines.")
    # Half-open edge intersections implement even-odd fill and preserve holes.
    _, bottom, _, top = shape_bounds(shape)
    if sum(map(len, paths)) * max(1, (top-bottom)/shape.interval) > 3000000:
        raise ValueError("Fill is too complex. Increase line spacing or simplify the paths.")
    lines = []
    y = bottom + shape.interval/2
    row = 0
    while y < top:
        intersections = []
        for path in paths:
            for (x1, y1), (x2, y2) in zip(path, path[1:]):
                if (y1 <= y < y2) or (y2 <= y < y1):
                    intersections.append(x1 + (y-y1)*(x2-x1)/(y2-y1))
        intersections.sort()
        if len(intersections) % 2:
            raise ValueError("This outline cannot be filled reliably.")
        pairs = [(intersections[i], intersections[i+1]) for i in range(0, len(intersections), 2)]
        if row % 2:
            pairs.reverse()
        for left, right in pairs:
            if right-left > 1e-8:
                lines.append(((left, y), (right, y)) if not row % 2 else ((right, y), (left, y)))
        if len(lines) > 100000:
            raise ValueError("Fill is too dense. Increase line interval or reduce the design.")
        row += 1
        y = bottom + (row+0.5)*shape.interval
    if not lines:
        raise ValueError("Fill interval is too large for this shape.")
    return tuple(lines)
