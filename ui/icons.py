"""Small vector icons drawn with QPainter.

Keeping the icons in code means the sidebar needs no external image assets and
the strokes stay crisp at any size / DPI.  All coordinates below are expressed
on a 24×24 grid and scaled to the rendered pixmap.
"""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from .theme import ACCENT, MUTED, TEXT

_SIZE = 64
_GRID = 24.0
_STROKE = 1.8

AMBER = "#e0a458"


def _render(draw, color: str) -> QIcon:
    """Rasterise *draw* (a callable painting on the 24×24 grid) into an icon."""
    pixmap = QPixmap(_SIZE, _SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(_SIZE / _GRID, _SIZE / _GRID)

    pen = QPen(QColor(color), _STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    draw(painter)
    painter.end()
    return QIcon(pixmap)


def _folder_path() -> QPainterPath:
    path = QPainterPath()
    path.moveTo(3.0, 8.0)
    path.lineTo(3.0, 7.0)
    path.quadTo(3.0, 6.0, 4.0, 6.0)
    path.lineTo(8.2, 6.0)
    path.lineTo(10.2, 8.0)
    path.lineTo(20.0, 8.0)
    path.quadTo(21.0, 8.0, 21.0, 9.0)
    path.lineTo(21.0, 18.0)
    path.quadTo(21.0, 19.0, 20.0, 19.0)
    path.lineTo(4.0, 19.0)
    path.quadTo(3.0, 19.0, 3.0, 18.0)
    path.closeSubpath()
    return path


def _tag_path() -> QPainterPath:
    path = QPainterPath()
    path.moveTo(3.0, 3.0)
    path.lineTo(3.0, 12.6)
    path.lineTo(10.8, 20.4)
    path.quadTo(12.0, 21.6, 13.2, 20.4)
    path.lineTo(20.4, 13.2)
    path.quadTo(21.6, 12.0, 20.4, 10.8)
    path.lineTo(12.6, 3.0)
    path.closeSubpath()
    return path


@lru_cache(maxsize=None)
def folder_icon(color: str = MUTED) -> QIcon:
    """A plain folder outline (used for library rows)."""
    return _render(lambda p: p.drawPath(_folder_path()), color)


@lru_cache(maxsize=None)
def folder_plus_icon(color: str = ACCENT) -> QIcon:
    """Folder with a plus sign — the "add library" action."""
    def draw(p: QPainter) -> None:
        p.drawPath(_folder_path())
        p.drawLine(QPointF(12.0, 10.8), QPointF(12.0, 16.2))
        p.drawLine(QPointF(9.3, 13.5), QPointF(14.7, 13.5))

    return _render(draw, color)


@lru_cache(maxsize=None)
def photos_icon(color: str = MUTED) -> QIcon:
    """Picture frame (all photographs)."""
    def draw(p: QPainter) -> None:
        p.drawRoundedRect(QRectF(3.0, 5.0, 18.0, 14.5), 2.0, 2.0)
        p.drawEllipse(QRectF(7.0, 8.4, 2.6, 2.6))
        skyline = QPainterPath()
        skyline.moveTo(5.2, 16.8)
        skyline.lineTo(9.2, 12.4)
        skyline.lineTo(12.6, 15.6)
        skyline.lineTo(15.4, 12.8)
        skyline.lineTo(19.0, 16.8)
        p.drawPath(skyline)

    return _render(draw, color)


@lru_cache(maxsize=None)
def untagged_icon(color: str = MUTED) -> QIcon:
    """Tag outline with a slash (photos without tags)."""
    def draw(p: QPainter) -> None:
        p.drawPath(_tag_path())
        p.drawEllipse(QRectF(6.3, 6.3, 2.4, 2.4))
        p.drawLine(QPointF(3.4, 3.4), QPointF(20.6, 20.6))

    return _render(draw, color)


@lru_cache(maxsize=None)
def missing_icon(color: str = AMBER) -> QIcon:
    """Warning triangle (photos whose file is gone)."""
    def draw(p: QPainter) -> None:
        triangle = QPainterPath()
        triangle.moveTo(12.0, 3.8)
        triangle.lineTo(21.0, 19.2)
        triangle.quadTo(21.5, 20.2, 20.3, 20.2)
        triangle.lineTo(3.7, 20.2)
        triangle.quadTo(2.5, 20.2, 3.0, 19.2)
        triangle.closeSubpath()
        p.drawPath(triangle)
        p.drawLine(QPointF(12.0, 9.4), QPointF(12.0, 14.2))
        p.drawPoint(QPointF(12.0, 17.2))

    return _render(draw, color)


@lru_cache(maxsize=None)
def tag_icon(color: str = MUTED) -> QIcon:
    """Tag outline (tag manager button)."""
    def draw(p: QPainter) -> None:
        p.drawPath(_tag_path())
        p.drawEllipse(QRectF(6.3, 6.3, 2.4, 2.4))

    return _render(draw, color)


@lru_cache(maxsize=None)
def fullscreen_icon(color: str = TEXT) -> QIcon:
    """Four corners (full-screen toggle)."""
    def draw(p: QPainter) -> None:
        corners = QPainterPath()
        corners.moveTo(4.0, 9.0)
        corners.lineTo(4.0, 5.5)
        corners.quadTo(4.0, 4.0, 5.5, 4.0)
        corners.lineTo(9.0, 4.0)

        corners.moveTo(15.0, 4.0)
        corners.lineTo(18.5, 4.0)
        corners.quadTo(20.0, 4.0, 20.0, 5.5)
        corners.lineTo(20.0, 9.0)

        corners.moveTo(20.0, 15.0)
        corners.lineTo(20.0, 18.5)
        corners.quadTo(20.0, 20.0, 18.5, 20.0)
        corners.lineTo(15.0, 20.0)

        corners.moveTo(9.0, 20.0)
        corners.lineTo(5.5, 20.0)
        corners.quadTo(4.0, 20.0, 4.0, 18.5)
        corners.lineTo(4.0, 15.0)
        p.drawPath(corners)

    return _render(draw, color)


@lru_cache(maxsize=None)
def pin_icon(color: str = ACCENT) -> QIcon:
    """Map pin (geotag)."""
    def draw(p: QPainter) -> None:
        pin = QPainterPath()
        pin.moveTo(12.0, 21.0)
        pin.cubicTo(12.0, 21.0, 5.0, 14.2, 5.0, 9.3)
        pin.cubicTo(5.0, 5.3, 8.1, 3.0, 12.0, 3.0)
        pin.cubicTo(15.9, 3.0, 19.0, 5.3, 19.0, 9.3)
        pin.cubicTo(19.0, 14.2, 12.0, 21.0, 12.0, 21.0)
        pin.closeSubpath()
        p.drawPath(pin)
        p.drawEllipse(QRectF(9.8, 7.1, 4.4, 4.4))

    return _render(draw, color)


@lru_cache(maxsize=None)
def edit_icon(color: str = MUTED) -> QIcon:
    """Pencil (edit coordinates)."""
    def draw(p: QPainter) -> None:
        pencil = QPainterPath()
        pencil.moveTo(4.0, 20.0)
        pencil.lineTo(5.0, 16.0)
        pencil.lineTo(16.2, 4.8)
        pencil.quadTo(17.4, 3.6, 18.6, 4.8)
        pencil.lineTo(19.2, 5.4)
        pencil.quadTo(20.4, 6.6, 19.2, 7.8)
        pencil.lineTo(8.0, 19.0)
        pencil.closeSubpath()
        p.drawPath(pencil)
        p.drawLine(QPointF(15.2, 5.8), QPointF(18.2, 8.8))

    return _render(draw, color)


@lru_cache(maxsize=None)
def trash_icon(color: str = MUTED) -> QIcon:
    """Trash can (remove geotag)."""
    def draw(p: QPainter) -> None:
        p.drawLine(QPointF(4.0, 6.8), QPointF(20.0, 6.8))
        lid = QPainterPath()
        lid.moveTo(9.0, 6.8)
        lid.lineTo(9.0, 4.8)
        lid.quadTo(9.0, 3.8, 10.0, 3.8)
        lid.lineTo(14.0, 3.8)
        lid.quadTo(15.0, 3.8, 15.0, 4.8)
        lid.lineTo(15.0, 6.8)
        p.drawPath(lid)
        body = QPainterPath()
        body.moveTo(6.0, 6.8)
        body.lineTo(7.0, 20.0)
        body.quadTo(7.1, 21.0, 8.1, 21.0)
        body.lineTo(15.9, 21.0)
        body.quadTo(16.9, 21.0, 17.0, 20.0)
        body.lineTo(18.0, 6.8)
        p.drawPath(body)
        p.drawLine(QPointF(10.2, 10.5), QPointF(10.5, 17.2))
        p.drawLine(QPointF(13.8, 10.5), QPointF(13.5, 17.2))

    return _render(draw, color)


@lru_cache(maxsize=None)
def filter_icon(color: str = MUTED) -> QIcon:
    """Sliders (filters)."""
    def draw(p: QPainter) -> None:
        p.drawLine(QPointF(4.0, 6.5), QPointF(20.0, 6.5))
        p.drawLine(QPointF(4.0, 12.0), QPointF(20.0, 12.0))
        p.drawLine(QPointF(4.0, 17.5), QPointF(20.0, 17.5))
        p.drawEllipse(QRectF(7.4, 4.6, 3.8, 3.8))
        p.drawEllipse(QRectF(12.8, 10.1, 3.8, 3.8))
        p.drawEllipse(QRectF(5.8, 15.6, 3.8, 3.8))

    return _render(draw, color)


@lru_cache(maxsize=None)
def calendar_icon(color: str = MUTED) -> QIcon:
    """Calendar (date filters)."""
    def draw(p: QPainter) -> None:
        p.drawRoundedRect(QRectF(3.5, 5.0, 17.0, 15.0), 2.0, 2.0)
        p.drawLine(QPointF(3.5, 10.0), QPointF(20.5, 10.0))
        p.drawLine(QPointF(8.0, 3.2), QPointF(8.0, 7.0))
        p.drawLine(QPointF(16.0, 3.2), QPointF(16.0, 7.0))
        p.drawPoint(QPointF(7.8, 13.4))
        p.drawPoint(QPointF(12.0, 13.4))
        p.drawPoint(QPointF(16.2, 13.4))
        p.drawPoint(QPointF(7.8, 16.8))
        p.drawPoint(QPointF(12.0, 16.8))

    return _render(draw, color)


@lru_cache(maxsize=None)
def camera_icon(color: str = MUTED) -> QIcon:
    """Camera body (camera filter / properties)."""
    def draw(p: QPainter) -> None:
        p.drawRoundedRect(QRectF(3.0, 7.2, 18.0, 12.8), 2.2, 2.2)
        bump = QPainterPath()
        bump.moveTo(8.2, 7.2)
        bump.lineTo(9.6, 5.0)
        bump.quadTo(10.0, 4.4, 10.7, 4.4)
        bump.lineTo(13.3, 4.4)
        bump.quadTo(14.0, 4.4, 14.4, 5.0)
        bump.lineTo(15.8, 7.2)
        p.drawPath(bump)
        p.drawEllipse(QRectF(8.8, 10.2, 6.4, 6.4))
        p.drawEllipse(QRectF(10.6, 12.0, 2.8, 2.8))

    return _render(draw, color)


@lru_cache(maxsize=None)
def check_circle_icon(color: str = MUTED) -> QIcon:
    """Circle with a check mark (availability filter)."""
    def draw(p: QPainter) -> None:
        p.drawEllipse(QRectF(3.0, 3.0, 18.0, 18.0))
        check = QPainterPath()
        check.moveTo(7.8, 12.2)
        check.lineTo(10.8, 15.2)
        check.lineTo(16.4, 8.8)
        p.drawPath(check)

    return _render(draw, color)


@lru_cache(maxsize=None)
def dot_icon(color: str) -> QIcon:
    """Filled colour dot used as a tag/category bullet."""
    pixmap = QPixmap(_SIZE, _SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(_SIZE / _GRID, _SIZE / _GRID)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(8.5, 8.5, 7.0, 7.0))
    painter.end()
    return QIcon(pixmap)