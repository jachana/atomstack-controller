"""Atomic, bounded production geometry operations."""
from dataclasses import replace
import math
from .geometry import Shape, shape_bounds, shape_paths, path_shape


def array_copies(shapes, columns, rows, gap_x, gap_y):
    if not shapes or type(columns) is not int or type(rows) is not int or not 1<=columns<=30 or not 1<=rows<=30:
        raise ValueError('Select objects and use 1–30 rows and columns.')
    if len(shapes)*columns*rows>2000 or any(not math.isfinite(v) or v<0 for v in (gap_x,gap_y)):
        raise ValueError('Array is too large or gaps are invalid.')
    bounds=[shape_bounds(s) for s in shapes]
    width=max(b[2] for b in bounds)-min(b[0] for b in bounds)
    height=max(b[3] for b in bounds)-min(b[1] for b in bounds)
    return [replace(s,x=s.x+col*(width+gap_x),y=s.y+row*(height+gap_y)).validated()
            for row in range(rows) for col in range(columns) if row or col for s in shapes]


def offset_shape(shape, distance):
    if not math.isfinite(distance) or not 0.01<=abs(distance)<=20:
        raise ValueError('Offset must be between 0.01 and 20 mm inward or outward.')
    paths=shape_paths(shape)
    if len(paths) != 1:
        raise ValueError("Offset supports one closed convex contour per object; compound paths are not supported yet.")
    result=[]
    for path in paths:
        if len(path)<4 or math.dist(path[0],path[-1])>1e-7:
            raise ValueError('Offsets require closed convex outlines. Open or concave paths are not supported yet.')
        points=path[:-1]
        area=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))
        sign=1 if area>0 else -1
        edges=[]
        for a,b in zip(points,points[1:]+points[:1]):
            dx,dy=b[0]-a[0],b[1]-a[1]; length=math.hypot(dx,dy)
            if length<1e-9: raise ValueError('Outline has duplicate points; simplify it before offsetting.')
            edges.append((dx/length,dy/length))
        new=[]
        for i,p in enumerate(points):
            ax,ay=edges[i-1]; bx,by=edges[i]; cross=ax*by-ay*bx
            if sign*cross < -1e-8:
                raise ValueError('Concave offsets are not supported yet; use a convex outline.')
            if abs(cross)<1e-10: new.append((p[0]+sign*by*distance,p[1]-sign*bx*distance)); continue
            u=(p[0]+sign*ay*distance,p[1]-sign*ax*distance)
            v=(p[0]+sign*by*distance,p[1]-sign*bx*distance)
            t=((v[0]-u[0])*by-(v[1]-u[1])*bx)/cross
            q=(u[0]+t*ax,u[1]+t*ay)
            if math.dist(q,p)>abs(distance)*20: raise ValueError('Offset creates an excessive sharp corner.')
            new.append(q)
        # Each result point must lie inside every shifted edge half-plane.
        for q in new:
            for p,(dx,dy) in zip(points,edges):
                if sign*(dx*(q[1]-p[1])-dy*(q[0]-p[0])) < -distance-1e-6:
                    raise ValueError('Inward offset collapses the outline.')
        if abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(new,new[1:]+new[:1])))<1e-7:
            raise ValueError('Offset collapses the outline.')
        result.append(new+[new[0]])
    return path_shape(result,speed=shape.speed,power=shape.power,passes=shape.passes,
                      layer=shape.layer,mode=shape.mode,interval=shape.interval)
