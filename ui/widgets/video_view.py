from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView

from .subtitle_overlay import SubtitleOverlayItem


class VideoView(QGraphicsView):
    """Hosts video and subtitle overlay in one scene."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: black; border-radius: 10px;")
        self.setRenderHint(QPainter.Antialiasing)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.video_item = QGraphicsVideoItem()
        self._scene.addItem(self.video_item)

        self.subtitle_item = SubtitleOverlayItem()
        self._scene.addItem(self.subtitle_item)
        self.subtitle_item.hide()
        self.video_source_width = 0
        self.video_source_height = 0
        self.preview_aspect_key = "source"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width, height = self.width(), self.height()
        self._scene.setSceneRect(0, 0, width, height)
        content_rect = self.get_video_content_rect()
        self.video_item.setPos(content_rect.topLeft())
        self.video_item.setSize(QSizeF(content_rect.width(), content_rect.height()))
        self.reposition_subtitle()
        self.viewport().update()

    def set_video_dimensions(self, width: int, height: int):
        self.video_source_width = max(0, int(width or 0))
        self.video_source_height = max(0, int(height or 0))
        content_rect = self.get_video_content_rect()
        self.video_item.setPos(content_rect.topLeft())
        self.video_item.setSize(QSizeF(content_rect.width(), content_rect.height()))
        self.reposition_subtitle()

    def set_preview_aspect_ratio(self, aspect_key: str):
        self.preview_aspect_key = str(aspect_key or "source").strip().lower() or "source"
        content_rect = self.get_video_content_rect()
        self.video_item.setPos(content_rect.topLeft())
        self.video_item.setSize(QSizeF(content_rect.width(), content_rect.height()))
        self.reposition_subtitle()
        self.viewport().update()

    def _resolve_canvas_aspect_ratio(self) -> float | None:
        aspect_key = str(getattr(self, "preview_aspect_key", "source") or "source").strip().lower()
        aspect_map = {
            "16:9": 16.0 / 9.0,
            "9:16": 9.0 / 16.0,
            "1:1": 1.0,
            "4:3": 4.0 / 3.0,
        }
        if aspect_key in aspect_map:
            return aspect_map[aspect_key]
        if self.video_source_width and self.video_source_height:
            return self.video_source_width / self.video_source_height
        return None

    def get_preview_canvas_rect(self) -> QRectF:
        view_w, view_h = float(self.width()), float(self.height())
        if view_w <= 0 or view_h <= 0:
            return QRectF(0, 0, 0, 0)
        canvas_ratio = self._resolve_canvas_aspect_ratio()
        if not canvas_ratio:
            return QRectF(0, 0, view_w, view_h)

        view_ratio = view_w / view_h if view_h else canvas_ratio

        if canvas_ratio > view_ratio:
            content_w = view_w
            content_h = view_w / canvas_ratio
            offset_x = 0
            offset_y = (view_h - content_h) / 2
        else:
            content_h = view_h
            content_w = view_h * canvas_ratio
            offset_x = (view_w - content_w) / 2
            offset_y = 0

        return QRectF(offset_x, offset_y, content_w, content_h)

    def get_video_content_rect(self) -> QRectF:
        canvas_rect = self.get_preview_canvas_rect()
        if canvas_rect.width() <= 0 or canvas_rect.height() <= 0:
            return QRectF(0, 0, 0, 0)
        if not self.video_source_width or not self.video_source_height:
            return canvas_rect

        source_ratio = self.video_source_width / self.video_source_height
        canvas_ratio = canvas_rect.width() / canvas_rect.height() if canvas_rect.height() else source_ratio

        if source_ratio > canvas_ratio:
            content_w = canvas_rect.width()
            content_h = content_w / source_ratio
            offset_x = canvas_rect.left()
            offset_y = canvas_rect.top() + (canvas_rect.height() - content_h) / 2.0
        else:
            content_h = canvas_rect.height()
            content_w = content_h * source_ratio
            offset_x = canvas_rect.left() + (canvas_rect.width() - content_w) / 2.0
            offset_y = canvas_rect.top()
        return QRectF(offset_x, offset_y, content_w, content_h)

    def reposition_subtitle(self):
        item = self.subtitle_item
        rect = self.get_preview_canvas_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        source_w = max(1, int(rect.width()))
        source_h = max(1, int(rect.height()))
        scale_x = rect.width() / source_w
        scale_y = rect.height() / source_h
        side_margin_px = 60 * scale_x

        desired_width = min(int(rect.width() - 2 * side_margin_px), max(160, int((source_w - 120) * scale_x)))
        item.set_layout_width(desired_width)

        item_w, item_h = item.W, item.H
        left_pad = rect.left() + side_margin_px
        right_limit = rect.right() - item_w - side_margin_px
        if item.custom_position_enabled:
            x_pos = rect.left() + (rect.width() * item.custom_x_percent / 100.0) - (item_w / 2.0)
            y_pos = rect.top() + (rect.height() * item.custom_y_percent / 100.0) - (item_h / 2.0)
        else:
            if item.alignment == "Bottom Left":
                x_pos = left_pad
            elif item.alignment == "Bottom Right":
                x_pos = right_limit
            else:
                x_pos = rect.left() + (rect.width() - item_w) / 2

            x_pos += item.x_offset * scale_x
            if item.alignment == "Top Center":
                y_pos = rect.top() + (item.bottom_offset * scale_y)
            elif item.alignment == "Center":
                y_pos = rect.top() + (rect.height() - item_h) / 2 + (item.bottom_offset * scale_y)
            else:
                y_pos = rect.bottom() - item_h - (item.bottom_offset * scale_y)

        x_pos = max(left_pad - item_w, min(x_pos, rect.right() + item_w)) # Allow slightly off-screen
        y_min = rect.top() - item_h
        y_max = rect.bottom()
        y_pos = max(y_min, min(y_pos, y_max))
        item.setPos(QPointF(x_pos, y_pos))

    def paintEvent(self, event):
        super().paintEvent(event)
        canvas_rect = self.get_preview_canvas_rect()
        if canvas_rect.width() <= 0 or canvas_rect.height() <= 0:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)

        outer = QPainterPath()
        outer.addRect(QRectF(self.viewport().rect()))
        inner = QPainterPath()
        inner.addRoundedRect(canvas_rect, 12, 12)
        matte = outer.subtracted(inner)
        painter.fillPath(matte, QColor(2, 8, 16, 190))
        painter.setPen(QPen(QColor(78, 117, 158, 180), 1.5))
        painter.drawRoundedRect(canvas_rect, 12, 12)

        aspect_key = str(getattr(self, "preview_aspect_key", "source") or "source").strip().lower()
        if aspect_key != "source":
            label = aspect_key.upper()
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(label) + 18
            text_h = metrics.height() + 8
            chip_rect = QRectF(
                canvas_rect.right() - text_w - 10,
                canvas_rect.top() + 10,
                text_w,
                text_h,
            )
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(12, 24, 38, 210))
            painter.drawRoundedRect(chip_rect, 9, 9)
            painter.setPen(QColor(183, 227, 255))
            painter.drawText(chip_rect, Qt.AlignCenter, label)
