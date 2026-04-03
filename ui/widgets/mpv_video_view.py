from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class _SubtitleOverlayWidget(QWidget):
    """A real-time overlay widget for MpvVideoView."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(False)
        self.current_text = ""
        self.font_name = "Segoe UI"
        self.font_size = 20
        self.font_color = QColor(255, 255, 255)
        self.alignment = "Bottom Center"
        self.x_offset = 0
        self.bottom_offset = 30
        self.custom_position_enabled = False
        self.custom_x_percent = 50
        self.custom_y_percent = 86
        self.W, self.H = 640, 96
        self.hide()

    def sync_to_view(self):
        pass

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
            self.font_size = font_size
            self.H = max(96, int(font_size * 4))
            changed = True
        if font_color and font_color != self.font_color:
            self.font_color = font_color
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

    def paintEvent(self, event):
        if not self.current_text and not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, float(self.W), float(self.H))

        if self.current_text:
            painter.setPen(self.font_color)
            font = QFont(self.font_name)
            font.setPixelSize(max(1, int(self.font_size)))
            font.setBold(True)
            painter.setFont(font)

            # Outline
            outline_offsets = [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, -1), (-1, 1), (1, 1)]
            painter.setPen(QColor(0, 0, 0, 220))
            for dx, dy in outline_offsets:
                painter.drawText(rect.translated(dx, dy), Qt.AlignCenter | Qt.TextWordWrap, self.current_text)

            painter.setPen(QColor(0, 0, 0, 120))
            painter.drawText(rect.translated(2, 2), Qt.AlignCenter | Qt.TextWordWrap, self.current_text)

            painter.setPen(self.font_color)
            painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, self.current_text)
        else:
            # Placeholder for preview mode
            painter.setPen(QPen(self.font_color, 1, Qt.DashLine))
            painter.drawRoundedRect(rect, 10, 10)
            placeholder_font = QFont(self.font_name)
            placeholder_font.setPixelSize(12)
            painter.setFont(placeholder_font)
            painter.drawText(rect, Qt.AlignCenter, "(Subtitle Preview)")




class _BlurRegionOverlayWindow(QWidget):
    HANDLE_SIZE = 12
    MIN_WIDTH = 32
    MIN_HEIGHT = 24

    def __init__(self, on_region_changed=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self._normalized_rect = QRectF(0.25, 0.2, 0.5, 0.22)
        self._drag_mode = ""
        self._drag_offset = QPointF()
        self._rect_on_press = QRectF()
        self._press_pos = QPointF()
        self._editable = False
        self._target_view = None
        self._on_region_changed = on_region_changed
        self.hide()

    def attach_to_view(self, view: QWidget):
        self._target_view = view
        self.sync_to_view()

    def set_editable(self, editable: bool):
        self._editable = bool(editable)
        self.setCursor(Qt.OpenHandCursor if self._editable else Qt.ArrowCursor)
        if self._editable:
            self.sync_to_view()
        else:
            self.hide()
        self.update()

    def clear_region(self):
        self.hide()
        self._drag_mode = ""
        self._target_view = None
        self.update()

    def has_region(self) -> bool:
        return self.isVisible()

    def sync_to_view(self):
        if not self._editable or not self._target_view or not self._target_view.isVisible():
            self.hide()
            return
        top_left = self._target_view.mapToGlobal(QPoint(0, 0))
        self.setGeometry(QRect(top_left, self._target_view.size()))
        self.show()
        self.raise_()
        self.update()

    def region_rect(self) -> QRectF:
        content_rect = self._target_view.get_video_content_rect() if self._target_view else QRectF(0, 0, float(self.width()), float(self.height()))
        width = max(1.0, float(content_rect.width()))
        height = max(1.0, float(content_rect.height()))
        return QRectF(
            content_rect.x() + self._normalized_rect.x() * width,
            content_rect.y() + self._normalized_rect.y() * height,
            self._normalized_rect.width() * width,
            self._normalized_rect.height() * height,
        )

    def _set_region_rect(self, rect: QRectF):
        content_rect = self._target_view.get_video_content_rect() if self._target_view else QRectF(0, 0, float(self.width()), float(self.height()))
        width = max(1.0, float(content_rect.width()))
        height = max(1.0, float(content_rect.height()))
        bounded = QRectF(rect)
        bounded.setWidth(max(self.MIN_WIDTH, bounded.width()))
        bounded.setHeight(max(self.MIN_HEIGHT, bounded.height()))
        if bounded.left() < content_rect.left():
            bounded.moveLeft(content_rect.left())
        if bounded.top() < content_rect.top():
            bounded.moveTop(content_rect.top())
        if bounded.right() > content_rect.right():
            bounded.moveRight(content_rect.right())
        if bounded.bottom() > content_rect.bottom():
            bounded.moveBottom(content_rect.bottom())

        self._normalized_rect = QRectF(
            max(0.0, (bounded.x() - content_rect.x()) / width),
            max(0.0, (bounded.y() - content_rect.y()) / height),
            min(1.0, bounded.width() / width),
            min(1.0, bounded.height() / height),
        )
        if callable(self._on_region_changed):
            self._on_region_changed()
        self.update()

    def _handle_rects(self, rect: QRectF) -> dict[str, QRectF]:
        s = float(self.HANDLE_SIZE)
        half = s / 2.0
        points = {
            "top_left": rect.topLeft(),
            "top_right": rect.topRight(),
            "bottom_left": rect.bottomLeft(),
            "bottom_right": rect.bottomRight(),
        }
        return {
            key: QRectF(point.x() - half, point.y() - half, s, s)
            for key, point in points.items()
        }

    def _hit_test(self, pos: QPointF) -> str:
        rect = self.region_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return ""
        for handle_name, handle_rect in self._handle_rects(rect).items():
            if handle_rect.contains(pos):
                return handle_name
        if rect.contains(pos):
            return "move"
        return ""

    def mousePressEvent(self, event):
        if not self._editable or event.button() != Qt.LeftButton:
            event.ignore()
            return
        pos = QPointF(event.position())
        self._drag_mode = self._hit_test(pos)
        self._rect_on_press = QRectF(self.region_rect())
        self._press_pos = QPointF(pos)
        if self._drag_mode == "move":
            self._drag_offset = pos - self._rect_on_press.topLeft()
            self.setCursor(Qt.ClosedHandCursor)
        elif self._drag_mode:
            self.setCursor(Qt.SizeAllCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        pos = QPointF(event.position())
        if not self._editable:
            event.ignore()
            return

        if not self._drag_mode:
            hit = self._hit_test(pos)
            if hit == "move":
                self.setCursor(Qt.OpenHandCursor)
            elif hit:
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            event.accept()
            return

        rect = QRectF(self._rect_on_press)
        if self._drag_mode == "move":
            rect.moveTopLeft(pos - self._drag_offset)
        else:
            delta = pos - self._press_pos
            if "left" in self._drag_mode:
                rect.setLeft(rect.left() + delta.x())
            if "right" in self._drag_mode:
                rect.setRight(rect.right() + delta.x())
            if "top" in self._drag_mode:
                rect.setTop(rect.top() + delta.y())
            if "bottom" in self._drag_mode:
                rect.setBottom(rect.bottom() + delta.y())
            if rect.width() < self.MIN_WIDTH:
                if "left" in self._drag_mode:
                    rect.setLeft(rect.right() - self.MIN_WIDTH)
                else:
                    rect.setRight(rect.left() + self.MIN_WIDTH)
            if rect.height() < self.MIN_HEIGHT:
                if "top" in self._drag_mode:
                    rect.setTop(rect.bottom() - self.MIN_HEIGHT)
                else:
                    rect.setBottom(rect.top() + self.MIN_HEIGHT)
        self._set_region_rect(rect)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_mode = ""
        if self._editable:
            self.setCursor(Qt.OpenHandCursor)
        event.accept()

    def paintEvent(self, event):
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.region_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        overlay_path = QPainterPath()
        overlay_path.addRoundedRect(rect, 12, 12)
        painter.fillPath(overlay_path, QColor(225, 240, 255, 78))
        painter.setPen(QPen(QColor(110, 231, 214, 220), 2))
        painter.drawRoundedRect(rect, 12, 12)

        if self._editable:
            painter.setBrush(QColor(110, 231, 214, 235))
            painter.setPen(QPen(QColor(12, 24, 38, 220), 1))
            for handle_rect in self._handle_rects(rect).values():
                painter.drawEllipse(handle_rect)


class MpvVideoView(QWidget):
    """Hosts an MPV video surface and overlays."""
    blurRegionChanged = Signal()
    subtitlePositionChanged = Signal(int, int)  # x_percent, y_percent

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: black; border-radius: 10px;")
        self.setMinimumSize(320, 180)
        self.video_source_width = 0
        self.video_source_height = 0
        self.subtitle_item = _SubtitleOverlayWidget(self)
        self.subtitle_item.raise_()
        self.video_surface = QWidget(self)
        self.video_surface.setAttribute(Qt.WA_NativeWindow, True)
        self.video_surface.setAutoFillBackground(True)
        self.video_surface.setStyleSheet("background-color: black;")
        self.blur_overlay = _BlurRegionOverlayWindow(on_region_changed=self.blurRegionChanged.emit)



    def set_video_dimensions(self, width: int, height: int):
        self.video_source_width = max(0, int(width or 0))
        self.video_source_height = max(0, int(height or 0))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.video_surface.setGeometry(self.rect())
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.blur_overlay.sync_to_view()

    def showEvent(self, event):
        super().showEvent(event)
        self.blur_overlay.sync_to_view()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.blur_overlay.hide()

    def get_video_content_rect(self) -> QRectF:
        view_w, view_h = float(self.width()), float(self.height())
        if view_w <= 0 or view_h <= 0:
            return QRectF(0, 0, 0, 0)
        if not self.video_source_width or not self.video_source_height:
            return QRectF(0, 0, view_w, view_h)

        source_ratio = self.video_source_width / self.video_source_height
        view_ratio = view_w / view_h if view_h else source_ratio
        if source_ratio > view_ratio:
            content_w = view_w
            content_h = view_w / source_ratio
            offset_x = 0.0
            offset_y = (view_h - content_h) / 2.0
        else:
            content_h = view_h
            content_w = view_h * source_ratio
            offset_x = (view_w - content_w) / 2.0
            offset_y = 0.0
        return QRectF(offset_x, offset_y, content_w, content_h)

    def reposition_subtitle(self):
        item = self.subtitle_item
        rect = self.get_video_content_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        source_w = max(1, self.video_source_width or int(rect.width()))
        source_h = max(1, self.video_source_height or int(rect.height()))
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

        x_pos = max(left_pad - item_w, min(x_pos, rect.right() + item_w))
        y_min = rect.top() - item_h
        y_max = rect.bottom()
        y_pos = max(y_min, min(y_pos, y_max))

        # We must use move() because it's a QWidget, or setGeometry
        item.move(int(x_pos), int(y_pos))
        item.setFixedSize(int(item_w), int(item_h))
        item.update()

    def set_blur_edit_enabled(self, enabled: bool):
        if enabled:
            self.blur_overlay.attach_to_view(self)
            self.blur_overlay.set_editable(True)
        else:
            self.blur_overlay.hide()
            self.blur_overlay.set_editable(False)

    def clear_blur_region(self):
        self.blur_overlay.clear_region()
        self.blurRegionChanged.emit()

    def has_blur_region(self) -> bool:
        return self.blur_overlay.has_region()

    def get_mpv_target_winid(self) -> int:
        return int(self.video_surface.winId())

    def get_blur_region_normalized(self) -> dict | None:
        if not self.blur_overlay.has_region():
            return None
        rect = self.blur_overlay._normalized_rect
        return {
            "x": round(float(rect.x()), 6),
            "y": round(float(rect.y()), 6),
            "width": round(float(rect.width()), 6),
            "height": round(float(rect.height()), 6),
        }
