"""DXF import: what it measures, what it scales, and what it refuses.

The refusals matter as much as the imports. A drawing that quietly loses a
spline or a block reference would send the laser along an outline the operator
never saw, so those cases assert an error rather than a best effort.
"""
import math

import pytest

from atomstack.dxf_import import import_dxf
from atomstack.geometry import shape_bounds


def dxf(tmp_path, entities, header=()):
    """Smallest DXF document that carries the given entity tags."""
    def tags(pairs):
        return "".join("{}\n{}\n".format(code, value) for code, value in pairs)
    text = ("0\nSECTION\n2\nHEADER\n" + tags(header) + "0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n" + tags(entities) + "0\nENDSEC\n0\nEOF\n")
    path = tmp_path / "test.dxf"
    path.write_text(text, encoding="utf-8")
    return import_dxf(path)


LINE = [(0, "LINE"), (10, "10"), (20, "20"), (11, "40"), (21, "20")]
SQUARE = [(0, "LWPOLYLINE"), (90, "4"), (70, "1"),
          (10, "0"), (20, "0"), (10, "20"), (20, "0"),
          (10, "20"), (20, "10"), (10, "0"), (20, "10")]


def test_geometry_is_placed_from_the_drawing_origin(tmp_path):
    shapes = dxf(tmp_path, LINE)
    assert len(shapes) == 1
    # The drawing's lower-left corner lands on the bed origin.
    assert shape_bounds(shapes[0]) == pytest.approx((0, 0, 30, 0))


def test_closed_polyline_and_circle_measure_correctly(tmp_path):
    square, = dxf(tmp_path, SQUARE)
    assert shape_bounds(square) == pytest.approx((0, 0, 20, 10))
    assert square.paths[0][0] == square.paths[0][-1]
    circle, = dxf(tmp_path, [(0, "CIRCLE"), (10, "50"), (20, "50"), (40, "10")])
    assert shape_bounds(circle) == pytest.approx((0, 0, 20, 20))


def test_drawing_units_scale_the_result(tmp_path):
    small = [(0, "LWPOLYLINE"), (90, "4"), (70, "1"),
             (10, "0"), (20, "0"), (10, "10"), (20, "0"),
             (10, "10"), (20, "5"), (10, "0"), (20, "5")]
    inches, = dxf(tmp_path, small, header=[(9, "$INSUNITS"), (70, "1")])
    assert shape_bounds(inches) == pytest.approx((0, 0, 10 * 25.4, 5 * 25.4))
    centimetres, = dxf(tmp_path, SQUARE, header=[(9, "$INSUNITS"), (70, "5")])
    assert shape_bounds(centimetres) == pytest.approx((0, 0, 200, 100))
    unitless, = dxf(tmp_path, SQUARE)
    assert shape_bounds(unitless) == pytest.approx((0, 0, 20, 10))


def test_a_drawing_too_big_for_the_bed_imports_at_its_real_size(tmp_path):
    """It has to exist before it can be scaled down; sending is what refuses."""
    from atomstack.geometry import Document, outside_bed
    shape, = dxf(tmp_path, SQUARE, header=[(9, "$INSUNITS"), (70, "1")])  # 20 in = 508 mm
    assert shape_bounds(shape) == pytest.approx((0, 0, 20 * 25.4, 10 * 25.4))
    assert outside_bed(shape)
    document = Document()
    document.add(shape)
    with pytest.raises(ValueError, match="outside the"):
        document.gcode((0, 0))


def test_arcs_flatten_inside_the_stated_tolerance(tmp_path):
    quarter, = dxf(tmp_path, [(0, "ARC"), (10, "0"), (20, "0"), (40, "10"),
                              (50, "0"), (51, "90")])
    assert shape_bounds(quarter) == pytest.approx((0, 0, 10, 10), abs=0.05)
    for nx, ny in quarter.paths[0]:
        x, y = quarter.x + nx * quarter.width, quarter.y + ny * quarter.height
        assert abs(math.hypot(x, y) - 10) < 0.05


def test_a_bulge_becomes_the_arc_it_describes(tmp_path):
    # Bulge 1 is a half circle, so a 20 mm chord rises exactly one radius.
    # This case alone proves little: it puts the centre on the chord, where a
    # sign error in the centre offset cannot show up.
    bulged, = dxf(tmp_path, [(0, "LWPOLYLINE"), (90, "2"), (70, "0"),
                             (10, "0"), (20, "0"), (42, "1"), (10, "20"), (20, "0")])
    assert shape_bounds(bulged) == pytest.approx((0, 0, 20, 10), abs=0.05)


def test_a_partial_bulge_bows_the_right_way_by_the_right_amount(tmp_path):
    # Bulge 0.5 over a 120 mm chord: radius 75, so the arc sags 30 mm, and a
    # positive bulge sweeps counter-clockwise, which puts that sag below.
    plate = [(0, "LWPOLYLINE"), (90, "4"), (70, "1"),
             (10, "0"), (20, "0"), (42, "0.5"), (10, "120"), (20, "0"),
             (10, "120"), (20, "80"), (10, "0"), (20, "80")]
    shape, = dxf(tmp_path, plate)
    # Origin shift puts the lowest point of the sag on zero: 80 + 30 tall.
    assert shape_bounds(shape) == pytest.approx((0, 0, 120, 110), abs=0.05)
    points = [(shape.x + nx * shape.width, shape.y + ny * shape.height)
              for nx, ny in shape.paths[0]]
    assert points[0] == pytest.approx(points[-1], abs=1e-6)  # closed
    # Every point on the bowed edge stays on its 75 mm circle.
    centre = (60.0, 75.0)
    for point in points:
        if point[1] < 30:
            assert math.dist(point, centre) == pytest.approx(75, abs=0.05)


def test_old_style_polyline_ignores_its_dummy_header_point(tmp_path):
    shapes = dxf(tmp_path, [(0, "POLYLINE"), (10, "0"), (20, "0"), (70, "1"),
                            (0, "VERTEX"), (10, "100"), (20, "100"),
                            (0, "VERTEX"), (10, "120"), (20, "100"),
                            (0, "VERTEX"), (10, "120"), (20, "110"),
                            (0, "SEQEND")])
    # A stray vertex at the header's dummy point would stretch this to 120 x 110.
    assert shape_bounds(shapes[0]) == pytest.approx((0, 0, 20, 10))


def test_entities_that_cannot_be_cut_faithfully_are_refused(tmp_path):
    for entity in ("INSERT", "SPLINE", "TEXT", "HATCH", "ELLIPSE", "DIMENSION"):
        with pytest.raises(ValueError) as caught:
            dxf(tmp_path, [(0, entity), (10, "0"), (20, "0")])
        assert "unsupported" in str(caught.value)


def test_unreadable_files_are_named_rather_than_guessed(tmp_path):
    binary = tmp_path / "binary.dxf"
    binary.write_bytes(b"AutoCAD Binary DXF" + bytes([13, 10, 26, 0]) + b"rest")
    with pytest.raises(ValueError, match="ASCII"):
        import_dxf(binary)
    with pytest.raises(ValueError, match="no supported vector geometry"):
        dxf(tmp_path, [(0, "POINT"), (10, "1"), (20, "1")])
    with pytest.raises(ValueError, match="ENTITIES"):
        empty = tmp_path / "empty.dxf"
        empty.write_text("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
        import_dxf(empty)
