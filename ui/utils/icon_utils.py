import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


def load_icon(icon_path: str, size: int = 18) -> QIcon:
    if not icon_path or not os.path.exists(icon_path):
        return QIcon()

    if icon_path.lower().endswith(".svg"):
        renderer = QSvgRenderer(icon_path)
        if renderer.isValid():
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            try:
                renderer.render(painter, QRectF(0, 0, size, size))
            finally:
                painter.end()
            return QIcon(pixmap)

    return QIcon(icon_path)
