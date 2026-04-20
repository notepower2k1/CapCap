from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QWidget


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
        self.outline_width = 2
        self.outline_color = QColor(0, 0, 0, 220)
        self.alignment = "Bottom Center"
        self.background_box = False
        self.background_color = QColor(0, 0, 0, 170)
        self.single_line = False
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

    def set_style(self, font_name, font_size, font_color, outline_width=2, outline_color=None, background_box=None, background_color=None, single_line=None):
        self.font_name = font_name
        self.font_size = font_size
        self.font_color = font_color
        self.outline_width = max(0, float(outline_width))
        self.outline_color = outline_color or QColor(0, 0, 0, 220)
        if background_box is not None:
            self.background_box = bool(background_box)
        if background_color is not None:
            self.background_color = background_color
        if single_line is not None:
            self.single_line = bool(single_line)
        self.H = max(96, int(font_size * 4))
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
            flags = Qt.AlignCenter if self.single_line else (Qt.AlignCenter | Qt.TextWordWrap)

            if self.background_box:
                metrics = painter.fontMetrics()
                text_rect = metrics.boundingRect(rect.toRect(), int(flags), self.current_text).adjusted(-18, -10, 18, 10)
                text_rect = text_rect.intersected(rect.toRect().adjusted(4, 4, -4, -4))
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.background_color)
                painter.drawRoundedRect(QRectF(text_rect), 14, 14)

            # Outline
            if self.outline_width > 0:
                painter.setPen(self.outline_color)
                # Drawing offsets based on outline width
                w = max(1.0, self.outline_width)
                offsets = [
                    (-w, 0), (w, 0), (0, -w), (0, w),
                    (-w*0.7, -w*0.7), (w*0.7, -w*0.7),
                    (-w*0.7, w*0.7), (w*0.7, w*0.7)
                ]
                for dx, dy in offsets:
                    painter.drawText(rect.translated(dx, dy), flags, self.current_text)

            painter.setPen(self.font_color)
            painter.drawText(rect, flags, self.current_text)
        else:
            return




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
    framingChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: black; border-radius: 10px;")
        self.setMinimumSize(320, 180)
        self.video_source_width = 0
        self.video_source_height = 0
        self.preview_aspect_key = "source"
        self.preview_scale_mode = "fit"
        self.preview_fill_focus_x = 0.5
        self.preview_fill_focus_y = 0.5
        self._framing_drag_active = False
        self._framing_drag_start = QPointF()
        self._framing_drag_focus = (0.5, 0.5)
        self.subtitle_item = _SubtitleOverlayWidget(self)
        self.video_surface = QWidget(self)
        self.video_surface.setAttribute(Qt.WA_NativeWindow, True)
        self.video_surface.setAutoFillBackground(True)
        self.video_surface.setStyleSheet("background-color: black;")
        self.blur_overlay = _BlurRegionOverlayWindow(on_region_changed=self.blurRegionChanged.emit)
        self.ratio_badge = QLabel(self)
        self.ratio_badge.setObjectName("previewRatioBadge")
        self.ratio_badge.setAlignment(Qt.AlignCenter)
        self.ratio_badge.setStyleSheet(
            "QLabel#previewRatioBadge {"
            "background-color: rgba(12, 24, 38, 210);"
            "color: rgb(183, 227, 255);"
            "padding: 4px 9px;"
            "border-radius: 9px;"
            "font-weight: 600;"
            "}"
        )
        self.ratio_badge.hide()
        self.video_surface.show()
        self.video_surface.lower()
        self.subtitle_item.raise_()
        self.ratio_badge.raise_()
        self.video_surface.winId()



    def set_video_dimensions(self, width: int, height: int):
        self.video_source_width = max(0, int(width or 0))
        self.video_source_height = max(0, int(height or 0))
        content_rect = self.get_video_content_rect().toRect()
        self.video_surface.setGeometry(content_rect)
        self.video_surface.lower()
        self.subtitle_item.raise_()
        self._update_ratio_badge()
        self.reposition_subtitle()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.video_surface.setGeometry(self.get_video_content_rect().toRect())
        self.video_surface.lower()
        self.subtitle_item.raise_()
        self.reposition_subtitle()
        self._update_ratio_badge()
        self.blur_overlay.sync_to_view()
        self.update()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.blur_overlay.sync_to_view()

    def showEvent(self, event):
        super().showEvent(event)
        self.video_surface.show()
        self.video_surface.lower()
        self.subtitle_item.raise_()
        self._update_ratio_badge()
        self.blur_overlay.sync_to_view()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.blur_overlay.hide()

    def set_preview_aspect_ratio(self, aspect_key: str):
        self.preview_aspect_key = str(aspect_key or "source").strip().lower() or "source"
        self.video_surface.setGeometry(self.get_video_content_rect().toRect())
        self.video_surface.lower()
        self.subtitle_item.raise_()
        self._update_ratio_badge()
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()
        self.update()

    def set_preview_scale_mode(self, scale_mode: str):
        self.preview_scale_mode = str(scale_mode or "fit").strip().lower() or "fit"
        self.video_surface.setGeometry(self.get_video_content_rect().toRect())
        self.video_surface.lower()
        self.subtitle_item.raise_()
        self._update_ratio_badge()
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()
        self.update()

    def set_preview_fill_focus(self, focus_x: float, focus_y: float):
        self.preview_fill_focus_x = max(0.0, min(1.0, float(focus_x)))
        self.preview_fill_focus_y = max(0.0, min(1.0, float(focus_y)))
        self.video_surface.setGeometry(self.get_video_content_rect().toRect())
        self.video_surface.lower()
        self.subtitle_item.raise_()
        self._update_ratio_badge()
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()
        self.update()

    def reset_preview_fill_focus(self):
        self.set_preview_fill_focus(0.5, 0.5)

    def get_preview_fill_focus(self) -> tuple[float, float]:
        return (float(self.preview_fill_focus_x), float(self.preview_fill_focus_y))

    def _update_ratio_badge(self):
        aspect_key = str(getattr(self, "preview_aspect_key", "source") or "source").strip().lower()
        if aspect_key == "source":
            self.ratio_badge.hide()
            return
        self.ratio_badge.setText(aspect_key.upper())
        self.ratio_badge.adjustSize()
        margin = 10
        canvas_rect = self.get_preview_canvas_rect().toRect()
        x_pos = canvas_rect.right() - self.ratio_badge.width() - margin
        y_pos = canvas_rect.top() + margin
        self.ratio_badge.move(max(margin, x_pos), max(margin, y_pos))
        self.ratio_badge.raise_()
        self.ratio_badge.show()

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
            offset_x = 0.0
            offset_y = (view_h - content_h) / 2.0
        else:
            content_h = view_h
            content_w = view_h * canvas_ratio
            offset_x = (view_w - content_w) / 2.0
            offset_y = 0.0
        return QRectF(offset_x, offset_y, content_w, content_h)

    def get_video_content_rect(self) -> QRectF:
        canvas_rect = self.get_preview_canvas_rect()
        if canvas_rect.width() <= 0 or canvas_rect.height() <= 0:
            return QRectF(0, 0, 0, 0)
        if not self.video_source_width or not self.video_source_height:
            return canvas_rect

        source_ratio = self.video_source_width / self.video_source_height
        canvas_ratio = canvas_rect.width() / canvas_rect.height() if canvas_rect.height() else source_ratio
        scale_mode = str(getattr(self, "preview_scale_mode", "fit") or "fit").strip().lower()
        if scale_mode == "fill":
            if source_ratio > canvas_ratio:
                content_h = canvas_rect.height()
                content_w = content_h * source_ratio
                overflow_w = max(0.0, content_w - canvas_rect.width())
                offset_x = canvas_rect.left() - overflow_w * float(getattr(self, "preview_fill_focus_x", 0.5))
                offset_y = canvas_rect.top()
            else:
                content_w = canvas_rect.width()
                content_h = content_w / source_ratio
                offset_x = canvas_rect.left()
                overflow_h = max(0.0, content_h - canvas_rect.height())
                offset_y = canvas_rect.top() - overflow_h * float(getattr(self, "preview_fill_focus_y", 0.5))
        else:
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

        x_pos = max(left_pad - item_w, min(x_pos, rect.right() + item_w))
        y_min = rect.top() - item_h
        y_max = rect.bottom()
        y_pos = max(y_min, min(y_pos, y_max))

        # We must use move() because it's a QWidget, or setGeometry
        item.move(int(x_pos), int(y_pos))
        item.setFixedSize(int(item_w), int(item_h))
        item.update()

    def _can_drag_framing(self) -> bool:
        if str(getattr(self, "preview_scale_mode", "fit") or "fit").strip().lower() != "fill":
            return False
        canvas_rect = self.get_preview_canvas_rect()
        content_rect = self.get_video_content_rect()
        return content_rect.width() > canvas_rect.width() + 0.5 or content_rect.height() > canvas_rect.height() + 0.5

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._can_drag_framing():
            pos = QPointF(event.position())
            if self.get_preview_canvas_rect().contains(pos):
                self._framing_drag_active = True
                self._framing_drag_start = pos
                self._framing_drag_focus = self.get_preview_fill_focus()
                self.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._framing_drag_active:
            pos = QPointF(event.position())
            canvas_rect = self.get_preview_canvas_rect()
            content_rect = self.get_video_content_rect()
            dx = pos.x() - self._framing_drag_start.x()
            dy = pos.y() - self._framing_drag_start.y()
            focus_x, focus_y = self._framing_drag_focus
            overflow_w = max(0.0, content_rect.width() - canvas_rect.width())
            overflow_h = max(0.0, content_rect.height() - canvas_rect.height())
            if overflow_w > 0.0:
                focus_x = max(0.0, min(1.0, focus_x - (dx / overflow_w)))
            if overflow_h > 0.0:
                focus_y = max(0.0, min(1.0, focus_y - (dy / overflow_h)))
            self.set_preview_fill_focus(focus_x, focus_y)
            self.framingChanged.emit(focus_x, focus_y)
            event.accept()
            return
        if self._can_drag_framing():
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._framing_drag_active and event.button() == Qt.LeftButton:
            self._framing_drag_active = False
            if self._can_drag_framing():
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        canvas_rect = self.get_preview_canvas_rect()
        if canvas_rect.width() <= 0 or canvas_rect.height() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        outer = QPainterPath()
        outer.addRect(QRectF(self.rect()))
        inner = QPainterPath()
        inner.addRoundedRect(canvas_rect, 12, 12)
        matte = outer.subtracted(inner)
        painter.fillPath(matte, QColor(2, 8, 16, 190))
        painter.setPen(QPen(QColor(78, 117, 158, 180), 1.5))
        painter.drawRoundedRect(canvas_rect, 12, 12)

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
