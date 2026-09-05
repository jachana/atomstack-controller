"""Bounded ASCII DXF outline import; no external resources and no new dependencies.

The rules match svg_import: anything that would silently change what gets cut is
refused with an instruction rather than approximated. Arcs and bulges are
flattened to the same tolerance the SVG importer uses, so a curve imported from
either format lands in the same place.
"""
import math
from .geometry import path_shape

TOLERANCE = 0.05  # Millimetres of chord deviation, as in svg_import.
MAX_POINTS = 100000
MAX_SHAPES = 2000
# $INSUNITS, in millimetres per drawing unit. 0 means the file never said.
UNITS = {0: 1.0, 1: 25.4, 2: 304.8, 3: 1609344.0, 4: 1.0, 5: 10.0, 6: 1000.0,
         8: 0.0000254, 9: 0.001, 10: 914.4, 11: 1e-7, 12: 1e-6, 13: 1e-3, 14: 0.1}
SUPPORTED = {'LINE', 'LWPOLYLINE', 'POLYLINE', 'VERTEX', 'SEQEND', 'CIRCLE', 'ARC', 'POINT'}
# Named so the message can say what to do about each one.
REFUSALS = {
    'INSERT': 'block references',
    'SPLINE': 'splines',
    'ELLIPSE': 'ellipses',
    'TEXT': 'text',
    'MTEXT': 'text',
    'DIMENSION': 'dimensions',
    'HATCH': 'hatches',
    'LEADER': 'leaders',
    'MLINE': 'multilines',
    '3DFACE': '3D faces',
    'SOLID': 'solids',
}


def read_tags(text):
    """DXF is a flat list of (group code, value) pairs, two lines each."""
    lines = text.splitlines()
    if len(lines) % 2:
        lines = lines[:-1]  # A trailing newline is not a broken file.
    tags = []
    for index in range(0, len(lines), 2):
        code = lines[index].strip()
        if not code.lstrip('-').isdigit():
            raise ValueError('This is not a readable ASCII DXF file.')
        tags.append((int(code), lines[index + 1].strip()))
    return tags


def number(value, what='coordinate'):
    try:
        result = float(value)
    except ValueError:
        raise ValueError(f'DXF {what} is not a number: {value!r}.') from None
    if not math.isfinite(result):
        raise ValueError(f'DXF {what} must be finite.')
    return result


def arc_points(centre, radius, start, sweep, scale):
    """Flatten an arc so the chord never sags more than the tolerance.

    The sagitta of a chord subtending ``step`` is ``r(1-cos(step/2))``, so the
    step follows from the tolerance and the radius in millimetres.
    """
    if radius <= 0 or sweep == 0:
        return []
    millimetres = radius * scale
    if millimetres <= TOLERANCE:
        step = abs(sweep)  # Smaller than the tolerance; a single chord is exact enough.
    else:
        step = 2 * math.acos(max(-1.0, min(1.0, 1 - TOLERANCE / millimetres)))
    count = max(2, min(4096, int(math.ceil(abs(sweep) / step)) + 1))
    return [(centre[0] + radius * math.cos(start + sweep * i / (count - 1)),
             centre[1] + radius * math.sin(start + sweep * i / (count - 1)))
            for i in range(count)]


def bulge_points(start, end, bulge, scale):
    """A polyline bulge is tan(quarter of the included angle)."""
    if not bulge:
        return [end]
    angle = 4 * math.atan(bulge)
    chord = math.dist(start, end)
    if chord < 1e-12 or abs(math.sin(angle / 2)) < 1e-12:
        return [end]
    radius = chord / (2 * abs(math.sin(angle / 2)))
    midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    # Distance from the chord's midpoint to the centre, along the left-hand
    # normal. One signed term covers minor and major arcs in both directions.
    offset = (chord / 2) / math.tan(angle / 2)
    normal = (-(end[1] - start[1]) / chord, (end[0] - start[0]) / chord)
    centre = (midpoint[0] + normal[0] * offset, midpoint[1] + normal[1] * offset)
    begin = math.atan2(start[1] - centre[1], start[0] - centre[0])
    return arc_points(centre, radius, begin, angle, scale)[1:]


def entity_paths(kind, tags, scale):
    """World paths for one entity, in drawing units."""
    values = {}
    for code, value in tags:
        values.setdefault(code, []).append(value)

    def one(code, default=None, what='coordinate'):
        if code not in values:
            if default is None:
                raise ValueError(f'DXF {kind} is missing a required value.')
            return default
        return number(values[code][0], what)

    if kind == 'POINT':
        return []  # A point cuts nothing.
    if kind == 'LINE':
        return [[(one(10), one(20)), (one(11), one(21))]]
    if kind == 'CIRCLE':
        centre, radius = (one(10), one(20)), one(40, what='radius')
        if radius <= 0:
            return []
        return [arc_points(centre, radius, 0.0, 2 * math.pi, scale)]
    if kind == 'ARC':
        centre, radius = (one(10), one(20)), one(40, what='radius')
        start, end = math.radians(one(50, what='angle')), math.radians(one(51, what='angle'))
        sweep = (end - start) % (2 * math.pi) or 2 * math.pi
        if radius <= 0:
            return []
        return [arc_points(centre, radius, start, sweep, scale)]

    # LWPOLYLINE and POLYLINE both reduce to vertices with optional bulges.
    xs = [number(v) for v in values.get(10, [])]
    ys = [number(v) for v in values.get(20, [])]
    if len(xs) != len(ys) or len(xs) < 2:
        return []
    bulges = [0.0] * len(xs)
    # Bulge 42 belongs to the vertex it follows; LWPOLYLINE interleaves them.
    order = [(code, value) for code, value in tags if code in (10, 42)]
    index = -1
    for code, value in order:
        if code == 10:
            index += 1
        elif 0 <= index < len(bulges):
            bulges[index] = number(value, 'bulge')
    closed = int(number(values.get(70, ['0'])[0], 'flag')) & 1
    points = list(zip(xs, ys))
    path = [points[0]]
    pairs = list(zip(points, points[1:])) + ([(points[-1], points[0])] if closed else [])
    for position, (start, end) in enumerate(pairs):
        path.extend(bulge_points(start, end, bulges[position], scale))
    return [path]


def import_dxf(path):
    raw = path.read_bytes()
    if len(raw) > 20_000_000:
        raise ValueError('DXF is oversized; simplify the drawing first.')
    if raw[:22].lstrip().startswith(b'AutoCAD Binary DXF'):
        raise ValueError('Binary DXF is unsupported. Save the drawing as ASCII DXF.')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('cp1252', errors='replace')
    tags = read_tags(text)

    scale = 1.0
    for index, (code, value) in enumerate(tags):
        if code == 9 and value == '$INSUNITS' and index + 1 < len(tags):
            units = int(number(tags[index + 1][1], 'unit code'))
            if units not in UNITS:
                raise ValueError('DXF uses drawing units this app does not know.')
            scale = UNITS[units]
            break

    starts = [i for i, (code, value) in enumerate(tags) if code == 0 and value == 'SECTION']
    entity_ranges = []
    for start in starts:
        name = next((v for c, v in tags[start + 1:start + 3] if c == 2), '')
        if name != 'ENTITIES':
            continue
        end = next((i for i, (c, v) in enumerate(tags[start:], start)
                    if c == 0 and v == 'ENDSEC'), len(tags))
        entity_ranges.append((start, end))
    if not entity_ranges:
        raise ValueError('DXF contains no ENTITIES section.')

    shapes, total = [], 0
    for start, end in entity_ranges:
        breaks = [i for i in range(start, end) if tags[i][0] == 0]
        for position, index in enumerate(breaks):
            kind = tags[index][1]
            if kind in REFUSALS:
                raise ValueError(f'DXF {REFUSALS[kind]} are unsupported. '
                                 'Explode blocks and convert curves and text to lines, '
                                 'arcs or polylines before importing.')
            if kind not in SUPPORTED:
                continue
            stop = breaks[position + 1] if position + 1 < len(breaks) else end
            body = tags[index + 1:stop]
            if kind == 'POLYLINE':
                # Vertices are separate entities up to SEQEND, and the header
                # carries a dummy 10/20 point that is not one of them.
                following = [i for i in breaks[position + 1:]
                             if tags[i][1] in ('VERTEX', 'SEQEND')]
                closing = next((i for i in following if tags[i][1] == 'SEQEND'), end)
                first = next((i for i in following if tags[i][1] == 'VERTEX'), closing)
                header = [tag for tag in tags[index + 1:first] if tag[0] not in (10, 20)]
                body = header + tags[first:closing]
            elif kind in ('VERTEX', 'SEQEND'):
                continue
            for path in entity_paths(kind, body, scale):
                if len(path) < 2:
                    continue
                total += len(path)
                if total > MAX_POINTS or len(shapes) >= MAX_SHAPES:
                    raise ValueError('DXF is too complex; simplify it first.')
                shapes.append([(x * scale, y * scale) for x, y in path])
    if not shapes:
        raise ValueError('DXF contains no supported vector geometry.')

    # DXF y already grows upward, like the bed, so only the origin has to move:
    # the drawing is placed with its lower-left corner at the bed origin.
    left = min(x for path in shapes for x, _ in path)
    bottom = min(y for path in shapes for _, y in path)
    return [path_shape([[(x - left, y - bottom) for x, y in path]]) for path in shapes]
