"""Procedurally drawn application icon (no binary assets needed)."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient


def make_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(size / 2, size / 2)
        r = size / 2 - 1
        g = QRadialGradient(c, r)
        g.setColorAt(0, QColor(255, 236, 200))
        g.setColorAt(0.18, QColor(255, 170, 60))
        g.setColorAt(0.5, QColor(120, 50, 10, 220))
        g.setColorAt(1, QColor(10, 6, 3, 255))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawEllipse(c, r, r)
        pen = QPen(QColor(255, 180, 80), max(1.0, size / 28))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for k, (rx, ry, rot) in enumerate(((0.78, 0.30, 25), (0.78, 0.30, -35), (0.62, 0.62, 0))):
            p.save()
            p.translate(c)
            p.rotate(rot)
            p.drawEllipse(QPointF(0, 0), r * rx, r * ry)
            p.restore()
        p.end()
        icon.addPixmap(pm)
    return icon
