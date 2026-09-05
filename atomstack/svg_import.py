"""Bounded SVG outline import using FontTools' SVG path parser; no external resources."""
import math
import re
import xml.etree.ElementTree as ET
from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.misc.transform import Transform
from fontTools.svgLib.path import parse_path
from .geometry import path_shape

NUMBER = r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'


def numbers(text):
    if re.sub(NUMBER, '', text).strip(' ,\t\r\n'):
        raise ValueError('Invalid SVG number list.')
    values = [float(v) for v in re.findall(NUMBER, text)]
    if not all(math.isfinite(v) for v in values):
        raise ValueError('SVG coordinates must be finite.')
    return values


def length(text):
    match = re.fullmatch(r'\s*('+NUMBER+r')\s*(mm|cm|in|pt|px)?\s*', text)
    if not match:
        raise ValueError('SVG page size must use mm, cm, in, pt, px, or plain numbers.')
    return float(match[1]) * {'mm':1,'cm':10,'in':25.4,'pt':25.4/72,'px':25.4/96,None:25.4/96}[match[2]]


def transform(text):
    result = Transform()
    tokens = list(re.finditer(r'([A-Za-z]+)\s*\(([^)]*)\)', text))
    if re.sub(r'([A-Za-z]+)\s*\(([^)]*)\)', '', text).strip(' ,\t\r\n'):
        raise ValueError('Unsupported SVG transform.')
    for token in tokens:
        name, args = token[1], numbers(token[2])
        if name == 'matrix' and len(args) == 6: t = Transform(*args)
        elif name == 'translate' and len(args) in (1,2): t = Transform().translate(args[0],args[1] if len(args)>1 else 0)
        elif name == 'scale' and len(args) in (1,2): t = Transform().scale(args[0],args[-1])
        elif name == 'rotate' and len(args) in (1,3):
            cx, cy = args[1:] if len(args)==3 else (0,0)
            t = Transform().translate(cx,cy).rotate(math.radians(args[0])).translate(-cx,-cy)
        elif name in ('skewX','skewY') and len(args)==1:
            tangent=math.tan(math.radians(args[0]))
            t=Transform(1,0,tangent,1,0,0) if name=='skewX' else Transform(1,tangent,0,1,0,0)
        else: raise ValueError('Unsupported SVG transform arguments.')
        result=result.transform(t)
    return result


class FlattenPen(BasePen):
    def __init__(self, tolerance=0.05):
        super().__init__(None)
        self.paths=[]; self.current=[]; self.tolerance=tolerance; self.count=0

    def append(self,p):
        if not all(math.isfinite(v) for v in p): raise ValueError('Invalid SVG coordinate.')
        self.count+=1
        if self.count>100000: raise ValueError('SVG is too complex; simplify paths first.')
        self.current.append(tuple(p))

    def _moveTo(self,p):
        self._endPath(); self.append(p)

    def _lineTo(self,p): self.append(p)

    def _curveToOne(self,a,b,c):
        def subdivide(p,a,b,c,depth=0):
            # Distance to the finite chord bounds control-polygon deviation,
            # including collinear curves that double back.
            dx,dy=c[0]-p[0],c[1]-p[1]; norm=dx*dx+dy*dy
            def distance(q):
                t=max(0,min(1,((q[0]-p[0])*dx+(q[1]-p[1])*dy)/norm)) if norm else 0
                return math.hypot(q[0]-p[0]-t*dx,q[1]-p[1]-t*dy)
            if max(distance(a),distance(b))<=self.tolerance:
                self.append(c); return
            if depth>=20: raise ValueError('SVG curve exceeds flattening limit.')
            mid=lambda u,v:((u[0]+v[0])/2,(u[1]+v[1])/2)
            pa,ab,bc=mid(p,a),mid(a,b),mid(b,c)
            pab,abc=mid(pa,ab),mid(ab,bc); centre=mid(pab,abc)
            subdivide(p,pa,pab,centre,depth+1); subdivide(centre,abc,bc,c,depth+1)
        subdivide(self._getCurrentPoint(),a,b,c)

    def _qCurveToOne(self,a,b):
        p=self._getCurrentPoint()
        self._curveToOne((p[0]+2*(a[0]-p[0])/3,p[1]+2*(a[1]-p[1])/3),
                         (b[0]+2*(a[0]-b[0])/3,b[1]+2*(a[1]-b[1])/3),b)

    def _closePath(self):
        if self.current and self.current[-1]!=self.current[0]: self.append(self.current[0])
        self._endPath()

    def _endPath(self):
        if len(self.current)>1: self.paths.append(self.current)
        self.current=[]


def import_svg(path):
    raw=path.read_bytes()
    if len(raw)>5_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('SVG is oversized or contains unsupported document entities.')
    try: root=ET.fromstring(raw)
    except ET.ParseError as exc: raise ValueError(f'Invalid SVG: {exc}') from exc
    if root.tag.split('}')[-1]!='svg': raise ValueError('Choose an SVG document.')
    box=numbers(root.get('viewBox',''))
    if box and (len(box)!=4 or box[2]<=0 or box[3]<=0): raise ValueError('Invalid SVG viewBox.')
    width=length(root.get('width',str(box[2] if box else 365*96/25.4)))
    height=length(root.get('height',str(box[3] if box else 305*96/25.4)))
    if not all(math.isfinite(v) and v>0 for v in (width,height)): raise ValueError('Invalid SVG page dimensions.')
    if not box: box=[0,0,width*96/25.4,height*96/25.4]
    sx,sy=width/box[2],height/box[3]
    aspect=root.get('preserveAspectRatio','xMidYMid meet')
    if aspect not in ('none','xMidYMid','xMidYMid meet'): raise ValueError('Unsupported SVG aspect alignment; use xMidYMid meet or none.')
    if aspect!='none': sx=sy=min(sx,sy)
    base=Transform(sx,0,0,-sy,(width-box[2]*sx)/2-box[0]*sx,height-(height-box[3]*sy)/2+box[1]*sy)
    shapes=[]; total=0
    def visit(node,parent,depth=0):
        nonlocal total
        if depth>50: raise ValueError("SVG groups are nested too deeply.")
        tag=node.tag.split('}')[-1]
        if tag in ('defs','metadata','title','desc','namedview'): return
        style=dict(piece.split(':',1) for piece in node.get('style','').split(';') if ':' in piece)
        style={k.strip():v.strip() for k,v in style.items()}
        # style wins over the presentation attribute, so an editor that hides a
        # layer by style cannot leave stale display="inline" firing the laser.
        if style.get('display',node.get('display'))=='none' or style.get('visibility',node.get('visibility'))=='hidden': return
        if node.get('class') or any(k in node.attrib or k in style for k in ('clip-path','mask','filter')):
            raise ValueError('SVG CSS classes, clipping, masks, and filters must be converted to paths first.')
        matrix=parent.transform(transform(node.get('transform','')))
        if tag in ('svg','g'):
            if tag=='svg' and node is not root: raise ValueError('Nested SVG viewports are unsupported; flatten the SVG first.')
            for child in node: visit(child,matrix,depth+1)
            return
        get=lambda key,default='0': float(node.get(key,default))
        if tag=='path': data=node.get('d','')
        elif tag in ('polygon','polyline'):
            pts=numbers(node.get('points',''))
            if len(pts)<4 or len(pts)%2: raise ValueError('Invalid SVG polygon.')
            data='M '+' '.join(map(str,pts))+(' Z' if tag=='polygon' else '')
        elif tag=='line': data=f'M {get("x1")} {get("y1")} L {get("x2")} {get("y2")}'
        elif tag=='rect':
            x,y,w,h=get('x'),get('y'),get('width'),get('height')
            if get('rx') or get('ry'): raise ValueError('Convert rounded rectangles to paths before importing.')
            if w<=0 or h<=0: return
            data=f'M{x} {y} h{w} v{h} h{-w} Z'
        elif tag in ('circle','ellipse'):
            x,y=get('cx'),get('cy'); rx=get('r') if tag=='circle' else get('rx'); ry=get('r') if tag=='circle' else get('ry')
            if rx<=0 or ry<=0: return
            data=f'M{x-rx} {y} A{rx} {ry} 0 1 0 {x+rx} {y} A{rx} {ry} 0 1 0 {x-rx} {y} Z'
        else: raise ValueError(f'SVG element {tag} is unsupported. Convert text and effects to vector paths first.')
        pen=FlattenPen()
        parse_path(data,TransformPen(pen,matrix)); pen._endPath()
        total+=pen.count
        if total>100000 or len(shapes)>2000: raise ValueError('SVG is too complex; simplify it first.')
        if pen.paths: shapes.append(path_shape(pen.paths))
    try: visit(root,base)
    except (TypeError,IndexError,OverflowError) as exc: raise ValueError('Invalid SVG geometry.') from exc
    if not shapes: raise ValueError('SVG contains no visible vector geometry.')
    return shapes
