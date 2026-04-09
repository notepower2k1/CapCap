from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem


class SubtitleOverlayItem(QGraphicsItem):
    """A draggable graphics item to represent the subtitle overlay on VideoView."""
    W, H = 640, 96

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(10)
        self.current_text = ""
        self.font_name = "Segoe UI"
        self.font_size = 20
        self.font_color = QColor(255, 255, 255)
        self.alignment = "Bottom Center"
        self.background_box = False
        self.background_color = QColor(0, 0, 0, 170)
        self.single_line = False
        self.x_offset = 0
        self.bottom_offset = 30
        self.custom_position_enabled = False
        self.custom_x_percent = 50
        self.custom_y_percent = 86

    def set_text(self, text):
        if self.current_text != text:
            self.current_text = text
            self.update()

    def set_style(self, *, font_name=None, font_size=None, font_color=None, background_box=None, background_color=None, single_line=None):
        changed = False
        if font_name and font_name != self.font_name:
            self.font_name = font_name
            changed = True
        if font_size and font_size != self.font_size:
            self.font_size = font_size
            self.H = max(96, int(font_size * 4))
            changed = True
        if font_color and font_color != self.font_color:
            self.font_color = font_color
            changed = True
        if background_box is not None and bool(background_box) != self.background_box:
            self.background_box = bool(background_box)
            changed = True
        if background_color is not None and background_color != self.background_color:
            self.background_color = background_color
            changed = True
        if single_line is not None and bool(single_line) != self.single_line:
            self.single_line = bool(single_line)
            changed = True
        if changed:
            self.update()

    def set_alignment(self, alignment: str):
        self.alignment = alignment or "Bottom Center"
        self.update()

    def set_positioning(self, *, x_offset=None, bottom_offset=None, custom_position_enabled=None, custom_x_percent=None, custom_y_percent=None):
        if x_offset is not None:
            self.x_offset = x_offset
        if bottom_offset is not None:
            self.bottom_offset = bottom_offset
        if custom_position_enabled is not None:
            self.custom_position_enabled = bool(custom_position_enabled)
        if custom_x_percent is not None:
            self.custom_x_percent = int(custom_x_percent)
        if custom_y_percent is not None:
            self.custom_y_percent = int(custom_y_percent)
        self.update()

    def set_layout_width(self, width: int):
        width = max(160, int(width))
        if width != self.W:
            self.W = width
            self.update()

    def boundingRect(self):
        return QRectF(0, 0, self.W, self.H)

    def paint(self, painter, option, widget):
        if not self.current_text and not self.isVisible():
            return
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.boundingRect()

        if self.current_text:
            painter.setPen(self.font_color)
            font = QFont(self.font_name)
            font.setPixelSize(max(1, int(self.font_size)))
            font.setBold(True)
            painter.setFont(font)
            flags = Qt.AlignCenter if self.single_line else (Qt.AlignCenter | Qt.TextWordWrap)

            if self.background_box:
                metrics = painter.fontMetrics()
                text_rect = metrics.boundingRect(rect.toRect(), int(flags), self.current_text).adjusted(-18, -10, 18, 10)
                text_rect = text_rect.intersected(rect.toRect().adjusted(4, 4, -4, -4))
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.background_color)
                painter.drawRoundedRect(QRectF(text_rect), 14, 14)

            # Outline
            outline_offsets = [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, -1), (-1, 1), (1, 1)]
            painter.setPen(QColor(0, 0, 0, 220))
            for dx, dy in outline_offsets:
                painter.drawText(rect.translated(dx, dy), flags, self.current_text)

            painter.setPen(QColor(0, 0, 0, 120))
            painter.drawText(rect.translated(2, 2), flags, self.current_text)

            painter.setPen(self.font_color)
            painter.drawText(rect, flags, self.current_text)
        else:
            # Placeholder for preview mode
            painter.setPen(QPen(self.font_color, 1, Qt.DashLine))
            painter.drawRoundedRect(rect, 10, 10)
            placeholder_font = QFont(self.font_name)
            placeholder_font.setPixelSize(12)
            painter.setFont(placeholder_font)
            painter.drawText(rect, Qt.AlignCenter, "(Subtitle Preview)")
