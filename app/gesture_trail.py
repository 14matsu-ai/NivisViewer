"""Shared logical-coordinate gesture stroke; recognition stays with its owner."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen


def draw_gesture_trail(painter: QPainter, points) -> None:
    if len(points) < 2:
        return
    path = QPainterPath(QPointF(points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(point))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(120, 205, 255, 150))
    # Qt already maps logical coordinates to device pixels.
    pen.setWidthF(3.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()
