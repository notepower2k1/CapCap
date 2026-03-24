from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem


class SubtitleOverlayItem(QGraphicsItem):
    """A draggable subtitle preview item rendered inside the QGraphicsScene."""

    W, H = 640, 96

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setZValue(10)
        self.current_text = ""
        self.font_name = "Segoe UI"
        self.font_size = 20
        self.font_color = QColor(255, 255, 255)
        self.alignment = "Bottom Center"
        self.x_offset = 0
        self.bottom_offset = 30

    def set_text(self, text):
        if self.current_text != text:
            self.current_text = text
            self.update()

    def set_style(self, *, font_name=None, font_size=None, font_color=None):
        changed = False
        if font_name and font_name != self.font_name:
            self.font_name = font_name
            changed = True
        if font_size and font_size != self.font_size:
            self.prepareGeometryChange()
            self.font_size = font_size
            self.H = max(96, int(font_size * 4))
            changed = True
        if font_color and font_color != self.font_color:
            self.font_color = font_color
            changed = True
        if changed:
            self.update()

    def set_positioning(self, *, x_offset=None, bottom_offset=None):
        if x_offset is not None:
            self.x_offset = x_offset
        if bottom_offset is not None:
            self.bottom_offset = bottom_offset

    def set_alignment(self, alignment: str):
        self.alignment = alignment or "Bottom Center"

    def set_layout_width(self, width: int):
        width = max(160, int(width))
        if width != self.W:
            self.prepareGeometryChange()
            self.W = width
            self.update()

    def boundingRect(self):
        return QRectF(0, 0, self.W, self.H)

    def paint(self, painter, option, widget=None):
        if not self.current_text and not self.isVisible():
            return

        rect = QRectF(0, 0, self.W, self.H)
        painter.setRenderHint(QPainter.Antialiasing)

        if self.current_text:
            painter.setPen(self.font_color)
            font = QFont(self.font_name)
            font.setPixelSize(max(1, int(self.font_size)))
            font.setBold(True)
            painter.setFont(font)

            outline_offsets = [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, -1), (-1, 1), (1, 1)]
            painter.setPen(QColor(0, 0, 0, 220))
            for dx, dy in outline_offsets:
                painter.drawText(rect.translated(dx, dy), Qt.AlignCenter | Qt.TextWordWrap, self.current_text)

            painter.setPen(QColor(0, 0, 0, 120))
            painter.drawText(rect.translated(2, 2), Qt.AlignCenter | Qt.TextWordWrap, self.current_text)

            painter.setPen(self.font_color)
            painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, self.current_text)
        else:
            painter.setPen(QPen(self.font_color, 1, Qt.DashLine))
            painter.drawRoundedRect(rect, 10, 10)

            placeholder_font = QFont(self.font_name)
            placeholder_font.setPixelSize(12)
            painter.setPen(self.font_color)
            painter.setFont(placeholder_font)
            painter.drawText(rect, Qt.AlignCenter, "(Subtitle Preview Area)")
