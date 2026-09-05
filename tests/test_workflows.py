import json
import tempfile
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import pytest
from atomstack.geometry import Shape, Document, CutLayer, burn_paths, path_shape, shape_bounds
from atomstack.production import array_copies, offset_shape
from atomstack.projects import ProjectStore, atomic_json
from atomstack.svg_import import import_svg


def test_fill_preserves_hole_and_matches_preview_and_output():
    outer=[(10,10),(30,10),(30,30),(10,30),(10,10)]
    inner=[(15,15),(25,15),(25,25),(15,25),(15,15)]
    shape=path_shape([outer,inner],mode='fill',interval=1)
    lines=burn_paths(shape)
    for a,b in lines:
        if 15<a[1]<25:
            assert max(a[0],b[0])<=15 or min(a[0],b[0])>=25
    doc=Document();doc.add(shape)
    assert len([s for s in doc.preview_segments() if s[0]=='burn'])==len(lines)
    code=doc.gcode((0,0))
    assert code.count('M4 S300')==len(lines)
    assert code.count('G53 G1')==len(lines)


def test_fill_ellipse_and_open_path_rejection():
    assert burn_paths(Shape('circle',10,10,20,10,mode='fill').validated())
    with pytest.raises(ValueError): Shape('line',10,10,20,0,mode='fill').validated()


def test_layer_fill_and_schema_roundtrip():
    doc=Document();doc.layers=[CutLayer('fill',2000,250,1,True,'fill',0.5)]
    doc.add(Shape('rectangle',1,1,10,10,layer='fill'))
    loaded=Document.from_payload(json.loads(json.dumps(doc.to_payload())))
    assert loaded.preview_segments()==doc.preview_segments()
    assert len(burn_paths(loaded.output_shapes()[0][1]))==20


def test_array_preserves_group_spacing_and_rejects_overflow():
    shape=Shape('rectangle',10,10,20,10)
    copies=array_copies([shape],3,2,2,3)
    assert len(copies)==5
    assert (copies[-1].x,copies[-1].y)==(54,23)
    with pytest.raises(ValueError): array_copies([shape],30,30,2,3)
    assert shape.x==10


def test_offset_is_exact_and_rejects_collapsed_or_concave_paths():
    shape=Shape('rectangle',10,10,20,10)
    assert shape_bounds(offset_shape(shape,1))==(9,9,31,21)
    assert shape_bounds(offset_shape(shape,-1))==(11,11,29,19)
    with pytest.raises(ValueError): offset_shape(shape,-6)
    concave=path_shape([[(10,10),(20,10),(15,15),(20,20),(10,20),(10,10)]])
    with pytest.raises(ValueError): offset_shape(concave,1)


def test_atomic_save_failure_preserves_previous_file(tmp_path):
    path=tmp_path/'design.json';atomic_json(path,{'keep':True})
    with patch('pathlib.Path.replace',side_effect=OSError('disk failure')):
        with pytest.raises(OSError): atomic_json(path,{'keep':False})
    assert json.loads(path.read_text())=={'keep':True}
    assert list(tmp_path.glob('*.tmp'))==[]


def test_recovery_sessions_and_recent_files(tmp_path):
    a=ProjectStore(tmp_path);b=ProjectStore(tmp_path)
    doc=Document();doc.add(Shape('rectangle',1,1,2,2))
    a.autosave(doc.to_payload(),None)
    assert b.candidates()==[a.recovery]
    recovered=Document.from_payload(json.loads(a.recovery.read_text())['document'])
    assert recovered.shapes==doc.shapes
    for i in range(12): a.remember(tmp_path/f'{i}.atomdesign')
    a.remember(tmp_path/'11.atomdesign')
    assert len(a.recent())==10
    a.clear();assert not b.candidates()


def svg(tmp_path,body,attrs='width="100mm" height="100mm" viewBox="0 0 100 100"'):
    path=tmp_path/'test.svg';path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>')
    return import_svg(path)


def test_svg_units_group_transforms_and_y_origin(tmp_path):
    shapes=svg(tmp_path,'<g transform="translate(10 20)"><rect x="0" y="0" width="20" height="10"/></g>')
    assert shape_bounds(shapes[0])==(10,70,30,80)
    shapes=svg(tmp_path,'<rect x="0" y="0" width="96" height="96"/>','width="96" height="96"')
    assert shape_bounds(shapes[0])==pytest.approx((0,0,25.4,25.4))


def test_svg_curves_and_arcs_flatten_and_roundtrip(tmp_path):
    shapes=svg(tmp_path,'<path d="M10 10 C10 30 30 30 30 10 Q20 0 10 10 Z"/><circle cx="60" cy="60" r="10"/>')
    assert len(shapes)==2
    assert len(shapes[0].paths[0])>20
    doc=Document();doc.shapes=shapes
    assert Document.from_payload(json.loads(json.dumps(doc.to_payload()))).shapes==shapes


def test_svg_hidden_geometry_is_skipped_whichever_way_it_is_hidden(tmp_path):
    visible='<rect x="20" y="20" width="10" height="10"/>'
    for hidden in ('<rect display="none" x="0" y="0" width="10" height="10"/>',
                   '<rect style="display:none" x="0" y="0" width="10" height="10"/>',
                   '<rect visibility="hidden" x="0" y="0" width="10" height="10"/>',
                   '<g style="display:none"><rect x="0" y="0" width="10" height="10"/></g>',
                   # An editor that hides by style may leave the old attribute behind.
                   '<rect display="inline" style="display:none" x="0" y="0" width="10" height="10"/>'):
        shapes=svg(tmp_path,hidden+visible)
        assert [shape_bounds(s) for s in shapes]==[(20,70,30,80)], hidden


def test_svg_rejects_silent_loss_of_effects_and_external_content(tmp_path):
    for body in ('<text>Hello</text>','<image href="https://example.com/image.png"/>','<path clip-path="url(#clip)" d="M1 1L2 2"/>'):
        with pytest.raises(ValueError): svg(tmp_path,body)


def test_material_test_grid_fill_settings():
    doc=Document();doc.add_burn_test(10,10,5,5,[1000,2000],[100,200],gap=3,passes=2,mode='fill',interval=0.5)
    assert doc.shapes[-1].x==18
    assert all(s.passes==2 and s.mode=='fill' for s in doc.shapes)
    assert all(len(burn_paths(s))==10 for s in doc.shapes)
