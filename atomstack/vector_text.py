"""Flatten a small set of installed Windows fonts into bounded vector contours."""
from functools import lru_cache
import math
import os
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont


FONT_FILES = {
    "Arial": "arial.ttf",
    "Segoe UI": "segoeui.ttf",
    "Consolas": "consola.ttf",
}


class _FlattenPen(BasePen):
    def __init__(self, glyph_set, x_offset=0.0, samples=10):
        super().__init__(glyph_set)
        self.x_offset = x_offset
        self.samples = samples
        self.paths = []
        self.current = []

    def _point(self, point):
        return (float(point[0]) + self.x_offset, float(point[1]))

    def _moveTo(self, point):
        self._finish(False)
        self.current = [self._point(point)]

    def _lineTo(self, point):
        self.current.append(self._point(point))

    def _curveToOne(self, p1, p2, p3):
        p0 = self._getCurrentPoint()
        for index in range(1, self.samples + 1):
            t = index / self.samples
            u = 1 - t
            point = (u**3*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t**3*p3[0],
                     u**3*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t**3*p3[1])
            self.current.append(self._point(point))

    def _qCurveToOne(self, p1, p2):
        p0 = self._getCurrentPoint()
        for index in range(1, self.samples + 1):
            t = index / self.samples
            u = 1 - t
            point = (u*u*p0[0] + 2*u*t*p1[0] + t*t*p2[0],
                     u*u*p0[1] + 2*u*t*p1[1] + t*t*p2[1])
            self.current.append(self._point(point))

    def _closePath(self):
        self._finish(True)

    def _endPath(self):
        self._finish(False)

    def _finish(self, close):
        if self.current:
            if close and self.current[-1] != self.current[0]:
                self.current.append(self.current[0])
            if len(self.current) > 1:
                self.paths.append(self.current)
        self.current = []


@lru_cache(maxsize=3)
def _font(family):
    if family not in FONT_FILES:
        raise ValueError("Choose Arial, Segoe UI, or Consolas.")
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / FONT_FILES[family]
    if not font_path.exists():
        raise ValueError(f"{family} is not installed on this computer.")
    return TTFont(font_path, lazy=True)


def text_paths(text, x, y, width, height, family="Arial"):
    if not text or not text.strip():
        raise ValueError("Text cannot be empty.")
    font = _font(family)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    advances = font["hmtx"].metrics
    raw_paths = []
    cursor = 0.0
    for character in text:
        glyph_name = cmap.get(ord(character), cmap.get(ord("?")))
        if glyph_name is None:
            continue
        pen = _FlattenPen(glyph_set, cursor)
        glyph_set[glyph_name].draw(pen)
        pen._finish(False)
        raw_paths.extend(pen.paths)
        cursor += advances[glyph_name][0]
    if not raw_paths:
        raise ValueError("The selected font cannot draw this text.")
    xs = [px for path in raw_paths for px, _ in path]
    ys = [py for path in raw_paths for _, py in path]
    left, right, bottom, top = min(xs), max(max(xs), cursor), min(ys), max(ys)
    raw_width, raw_height = right - left, top - bottom
    if raw_width <= 0 or raw_height <= 0 or not all(map(math.isfinite, (raw_width, raw_height))):
        raise ValueError("The selected text has no usable outline.")
    sx, sy = width / raw_width, height / raw_height
    return [[(x + (px-left)*sx, y + (py-bottom)*sy) for px, py in path] for path in raw_paths]
