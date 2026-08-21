import os

import os

from PySide6 import QtCore
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from runtime_paths import asset_path
from widgets import MpvVideoView, VideoView
from utils.icon_utils import load_icon
from utils.media_backend import is_mpv_backend_available


def _import_editor_timeline():
    import importlib.util, os, sys
    editor_dir = os.path.join(os.path.dirname(__file__), "editor")
    path = os.path.join(editor_dir, "timeline.py")
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    spec = importlib.util.spec_from_file_location("editor_timeline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.EditorTimeline


def _set_preview_icon_button(button: QPushButton, icon_path: str, tooltip: str):
    button.setText("")
    button.setFixedSize(38, 38)
    button.setIcon(load_icon(icon_path, 18))
    button.setIconSize(QSize(18, 18))
    button.setStyleSheet("QPushButton { padding: 0; }")


class OcrRegionOverlay(QWidget):
    _HANDLE_SIZE = 10
    _MIN_W = 40
    _MIN_H = 10

    def __init__(self):
        # MPV owns a native child surface. Keep this editor above that
        # surface even when OCR is invoked temporarily from audio mode.
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self._norm = QRectF(0.0, 0.75, 1.0, 0.25)
        self._load_rect()
        self._target_view = None
        self._main_window = None
        self._requested_visible = True
        self._editable = False
        self._drag_mode = ""
        self._drag_offset = QRectF()
        self._rect_on_press = QRectF()
        self._press_pos = QRectF()
        self.hide()

    def _load_rect(self):
        v = os.getenv("OCR_SUBTITLE_RECT", "")
        try:
            parts = [float(x) for x in v.split(",")]
            if len(parts) == 4:
                self._norm = QRectF(*parts)
        except Exception:
            pass

    def _save_rect(self):
        r = self._norm
        os.environ["OCR_SUBTITLE_RECT"] = f"{r.x():.4f},{r.y():.4f},{r.width():.4f},{r.height():.4f}"
        os.environ["OCR_CROP_RATIO"] = f"{r.height():.4f}"

    def attach_to_view(self, view):
        self._target_view = view
        if view and view.window():
            self._main_window = view.window()
            self._main_window.installEventFilter(self)

    def set_editable(self, editable: bool):
        self._editable = editable
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._editable)
        if not editable:
            self._drag_mode = ""
            self.setCursor(Qt.ArrowCursor)
            try:
                self.releaseKeyboard()
            except Exception:
                pass
        else:
            self.setCursor(Qt.OpenHandCursor)
            # This is a mouse-only crop editor.  Grabbing the application's
            # keyboard here routes every keypress away from unrelated inputs
            # (API-key fields, subtitle editors, dialogs) while it is open.
            # Escape is intentionally not required to exit the editor.
        self._update_button()
        self.update()

    def _update_button(self):
        w = self._main_window
        if w is None:
            return
        btn = getattr(w, "ocr_region_btn", None)
        if btn is None:
            return
        if not bool(getattr(w, "_ocr_overlay_visible", True)):
            btn.setStyleSheet("QPushButton { color: #7a8ea3; font-weight: bold; font-size: 10px; padding: 0; }")
        elif self._editable:
            btn.setStyleSheet("QPushButton { color: #ffb450; font-weight: bold; font-size: 10px; padding: 0; }")
        else:
            btn.setStyleSheet("QPushButton { color: #6ee7d6; font-weight: bold; font-size: 10px; padding: 0; }")

    def _is_requested_visible(self) -> bool:
        if hasattr(self, "_requested_visible"):
            return bool(self._requested_visible)
        w = self._main_window
        if w is None and self._target_view is not None:
            w = self._target_view.window()
            if w is not None:
                self._main_window = w
                self._main_window.installEventFilter(self)
        if w is None:
            return True
        return bool(getattr(w, "_ocr_overlay_visible", True))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._editable:
            self.set_editable(False)
            self.releaseKeyboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        # Defensive release for overlays hidden by a mode change, modal, or
        # completed OCR run rather than through set_editable(False).
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        if obj is self._main_window:
            if event.type() == QtCore.QEvent.WindowDeactivate:
                self.hide()
            elif event.type() in (QtCore.QEvent.WindowActivate, QtCore.QEvent.Resize, QtCore.QEvent.Move, QtCore.QEvent.Show):
                if self._is_requested_visible():
                    self.sync_to_view()
                else:
                    self.hide()
        return False

    def sync_to_view(self):
        if not self._target_view:
            self.hide()
            return
        if self._main_window is None and self._target_view.window() is not None:
            self._main_window = self._target_view.window()
            self._main_window.installEventFilter(self)
        if self._main_window is not None and bool(getattr(self._main_window, "_suspend_ocr_overlay", False)):
            self.hide()
            return
        if not self._is_requested_visible():
            self.hide()
            return
        cpu_mode = os.getenv("CAPCAP_DEVICE", "cuda").strip().lower() == "cpu"
        engine = os.getenv("TRANSCRIPTION_ENGINE", "sensevoice" if cpu_mode else "whisper").strip().lower()
        alternate_ocr_active = bool(getattr(self._main_window, "_alternate_ocr_range_pending", None))
        if engine != "ocr" and not alternate_ocr_active:
            self.hide()
            return
        self._load_rect()
        top_left = self._target_view.mapToGlobal(QtCore.QPoint(0, 0))
        self.setGeometry(QtCore.QRect(top_left, self._target_view.size()))
        self.show()
        self.raise_()
        self.update()

    def _content_rect(self):
        if self._target_view and hasattr(self._target_view, "get_video_content_rect"):
            r = self._target_view.get_video_content_rect()
            if r.width() > 0 and r.height() > 0:
                return r
        return QRectF(0, 0, float(self.width()), float(self.height()))

    def _ocr_rect(self):
        c = self._content_rect()
        return QRectF(
            c.x() + self._norm.x() * c.width(),
            c.y() + self._norm.y() * c.height(),
            self._norm.width() * c.width(),
            self._norm.height() * c.height(),
        )

    def _set_ocr_rect(self, rect: QRectF):
        c = self._content_rect()
        w = max(1.0, float(c.width()))
        h = max(1.0, float(c.height()))
        bounded = QRectF(rect)
        bounded.setWidth(max(self._MIN_W, bounded.width()))
        bounded.setHeight(max(self._MIN_H, bounded.height()))
        if bounded.left() < c.left():
            bounded.moveLeft(c.left())
        if bounded.top() < c.top():
            bounded.moveTop(c.top())
        if bounded.right() > c.right():
            bounded.moveRight(c.right())
        if bounded.bottom() > c.bottom():
            bounded.moveBottom(c.bottom())
        self._norm = QRectF(
            max(0.0, (bounded.x() - c.x()) / w),
            max(0.0, (bounded.y() - c.y()) / h),
            min(1.0, bounded.width() / w),
            min(1.0, bounded.height() / h),
        )
        self._save_rect()
        self.update()

    def _handle_rects(self, rect: QRectF):
        s = float(self._HANDLE_SIZE)
        half = s / 2.0
        pts = {
            "top_left": rect.topLeft(),
            "top_right": rect.topRight(),
            "bottom_left": rect.bottomLeft(),
            "bottom_right": rect.bottomRight(),
        }
        return {k: QRectF(p.x() - half, p.y() - half, s, s) for k, p in pts.items()}

    def _hit_test(self, pos):
        r = self._ocr_rect()
        for name, hr in self._handle_rects(r).items():
            if hr.contains(pos):
                return name
        if r.contains(pos):
            return "move"
        return ""

    def mousePressEvent(self, event):
        if not self._editable or event.button() != Qt.LeftButton:
            if not self._editable and event.button() == Qt.LeftButton:
                self.set_editable(True)
                self.setCursor(Qt.OpenHandCursor)
                event.accept()
                return
            event.ignore()
            return
        pos = event.position()
        self._drag_mode = self._hit_test(pos)
        self._rect_on_press = QRectF(self._ocr_rect())
        self._press_pos = pos
        if self._drag_mode == "move":
            self._drag_offset = pos - self._rect_on_press.topLeft()
            self.setCursor(Qt.ClosedHandCursor)
        elif self._drag_mode:
            self.setCursor(Qt.SizeAllCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position()
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
            d = pos - self._press_pos
            if "left" in self._drag_mode:
                rect.setLeft(rect.left() + d.x())
            if "right" in self._drag_mode:
                rect.setRight(rect.right() + d.x())
            if "top" in self._drag_mode:
                rect.setTop(rect.top() + d.y())
            if "bottom" in self._drag_mode:
                rect.setBottom(rect.bottom() + d.y())
            if rect.width() < self._MIN_W:
                if "left" in self._drag_mode:
                    rect.setLeft(rect.right() - self._MIN_W)
                else:
                    rect.setRight(rect.left() + self._MIN_W)
            if rect.height() < self._MIN_H:
                if "top" in self._drag_mode:
                    rect.setTop(rect.bottom() - self._MIN_H)
                else:
                    rect.setBottom(rect.top() + self._MIN_H)
        self._set_ocr_rect(rect)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_mode = ""
        if self._editable:
            self.setCursor(Qt.OpenHandCursor)
        event.accept()

    def paintEvent(self, event):
        if self._target_view is None:
            return
        rect = self._ocr_rect()
        if rect.isEmpty():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        color = QColor(110, 231, 214) if not self._editable else QColor(255, 180, 80)
        a = 120 if not self._editable else 180
        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), a), 2))
        p.setBrush(QColor(color.red(), color.green(), color.blue(), a // 4))
        p.drawRoundedRect(rect, 8, 8)

        if self._editable:
            p.setBrush(QColor(255, 180, 80, 235))
            p.setPen(QPen(QColor(12, 24, 38, 220), 1))
            for hr in self._handle_rects(rect).values():
                p.drawEllipse(hr)

        p.setPen(QColor(color.red(), color.green(), color.blue(), 200))
        font = p.font()
        font.setPixelSize(11)
        p.setFont(font)
        label = f"OCR {self._norm.height()*100:.0f}%"
        p.drawText(int(rect.right() - 90), int(rect.bottom() - 6), label)
        p.end()


class OcrTranslatorOverlay(QWidget):
    """Persistent, on-demand visual-text OCR selection over the preview."""
    captureRequested = Signal()
    rectChanged = Signal(tuple)
    _HANDLE_SIZE = 10
    _MIN_W = 48
    _MIN_H = 32

    def __init__(self):
        # mpv owns a native child window, so this must be a separate tool
        # window rather than a child QWidget.  Keep it non-activating so
        # showing it cannot immediately trigger WindowDeactivate and hide it.
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self._target_view = None
        self._main_window = None
        self._norm = QRectF(0.2, 0.2, 0.6, 0.25)
        self._drag_mode = ""
        self._rect_on_press = QRectF()
        self._press_pos = QtCore.QPointF()
        self._drag_offset = QtCore.QPointF()
        self._capturing = False
        self.hide()

    def attach_to_view(self, view):
        self._target_view = view
        if view:
            self._main_window = view.window()
        if self._main_window is not None:
            self._main_window.installEventFilter(self)

    def normalized_rect(self):
        r = self._norm
        return (r.x(), r.y(), r.width(), r.height())

    def set_capturing(self, capturing):
        """Freeze selection input while the one-shot OCR worker is running."""
        self._capturing = bool(capturing)
        self._drag_mode = ""
        self.setCursor(Qt.BusyCursor if self._capturing else Qt.OpenHandCursor)
        self.update()

    def set_normalized_rect(self, values):
        try:
            x, y, w, h = [float(value) for value in values]
            self._norm = QRectF(max(0.0, x), max(0.0, y), max(0.01, w), max(0.01, h))
        except (TypeError, ValueError):
            return
        self._set_rect(self._selection_rect(), emit=False)

    def eventFilter(self, obj, event):
        if obj is self._main_window:
            if event.type() == QtCore.QEvent.WindowDeactivate:
                self.hide()
            elif event.type() in (QtCore.QEvent.WindowActivate, QtCore.QEvent.Resize, QtCore.QEvent.Move, QtCore.QEvent.Show):
                if bool(getattr(self._main_window, "_ocr_translator_active", False)):
                    self.sync_to_view()
        return False

    def sync_to_view(self):
        if not self._target_view:
            self.hide()
            return
        current_window = self._target_view.window()
        if current_window is not self._main_window:
            if self._main_window is not None:
                self._main_window.removeEventFilter(self)
            self._main_window = current_window
            if self._main_window is not None:
                self._main_window.installEventFilter(self)
        if self._main_window is None or not bool(getattr(self._main_window, "_ocr_translator_active", False)):
            self.hide()
            return
        top_left = self._target_view.mapToGlobal(QtCore.QPoint(0, 0))
        self.setGeometry(QtCore.QRect(top_left, self._target_view.size()))
        self.show()
        self.raise_()
        self.update()

    def _content_rect(self):
        if self._target_view and hasattr(self._target_view, "get_video_content_rect"):
            rect = self._target_view.get_video_content_rect()
            if rect.width() > 0 and rect.height() > 0:
                return QRectF(rect)
        return QRectF(0, 0, float(self.width()), float(self.height()))

    def _selection_rect(self):
        content = self._content_rect()
        return QRectF(
            content.x() + self._norm.x() * content.width(),
            content.y() + self._norm.y() * content.height(),
            self._norm.width() * content.width(), self._norm.height() * content.height(),
        )

    def _set_rect(self, rect, emit=True):
        content = self._content_rect()
        if content.width() <= 0 or content.height() <= 0:
            return
        bounded = QRectF(rect)
        bounded.setWidth(max(self._MIN_W, bounded.width()))
        bounded.setHeight(max(self._MIN_H, bounded.height()))
        if bounded.left() < content.left(): bounded.moveLeft(content.left())
        if bounded.top() < content.top(): bounded.moveTop(content.top())
        if bounded.right() > content.right(): bounded.moveRight(content.right())
        if bounded.bottom() > content.bottom(): bounded.moveBottom(content.bottom())
        self._norm = QRectF(
            max(0.0, (bounded.x() - content.x()) / content.width()),
            max(0.0, (bounded.y() - content.y()) / content.height()),
            min(1.0, bounded.width() / content.width()), min(1.0, bounded.height() / content.height()),
        )
        if emit:
            self.rectChanged.emit(self.normalized_rect())
        self.update()

    def _handle_rects(self, rect):
        half = self._HANDLE_SIZE / 2.0
        return {
            name: QRectF(point.x() - half, point.y() - half, self._HANDLE_SIZE, self._HANDLE_SIZE)
            for name, point in {
                "top_left": rect.topLeft(), "top_right": rect.topRight(),
                "bottom_left": rect.bottomLeft(), "bottom_right": rect.bottomRight(),
            }.items()
        }

    def _capture_button_rect(self, rect):
        return QRectF(rect.right() - 100, rect.top() + 8, 92, 25)

    def _hit_test(self, pos):
        rect = self._selection_rect()
        if self._capture_button_rect(rect).contains(pos): return "capture"
        for name, handle in self._handle_rects(rect).items():
            if handle.contains(pos): return name
        return "move" if rect.contains(pos) else ""

    def mousePressEvent(self, event):
        if self._capturing:
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            event.ignore(); return
        self._drag_mode = self._hit_test(event.position())
        self._rect_on_press = QRectF(self._selection_rect())
        self._press_pos = event.position()
        if self._drag_mode == "move":
            self._drag_offset = event.position() - self._rect_on_press.topLeft()
            self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._capturing:
            event.accept()
            return
        if not self._drag_mode:
            hit = self._hit_test(event.position())
            self.setCursor(Qt.PointingHandCursor if hit == "capture" else Qt.OpenHandCursor if hit == "move" else Qt.SizeAllCursor if hit else Qt.ArrowCursor)
            event.accept(); return
        if self._drag_mode == "capture":
            event.accept(); return
        rect = QRectF(self._rect_on_press)
        if self._drag_mode == "move":
            rect.moveTopLeft(event.position() - self._drag_offset)
        else:
            delta = event.position() - self._press_pos
            if "left" in self._drag_mode: rect.setLeft(rect.left() + delta.x())
            if "right" in self._drag_mode: rect.setRight(rect.right() + delta.x())
            if "top" in self._drag_mode: rect.setTop(rect.top() + delta.y())
            if "bottom" in self._drag_mode: rect.setBottom(rect.bottom() + delta.y())
        self._set_rect(rect)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._capturing:
            event.accept()
            return
        mode = self._drag_mode
        self._drag_mode = ""
        self.setCursor(Qt.OpenHandCursor)
        if mode == "capture" and self._capture_button_rect(self._selection_rect()).contains(event.position()):
            self.captureRequested.emit()
        event.accept()

    def paintEvent(self, event):
        rect = self._selection_rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        color = QColor(168, 85, 247)
        painter.setPen(QPen(color, 2))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 30))
        painter.drawRoundedRect(rect, 7, 7)
        capture_color = QColor(94, 70, 121) if self._capturing else QColor(color.red(), color.green(), color.blue(), 230)
        painter.setBrush(capture_color)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self._capture_button_rect(rect), 5, 5)
        painter.setPen(QColor("#ffffff"))
        font = painter.font(); font.setBold(True); font.setPixelSize(11); painter.setFont(font)
        painter.drawText(self._capture_button_rect(rect), Qt.AlignCenter, "Capturing…" if self._capturing else "Capture")
        painter.setBrush(color); painter.setPen(QPen(QColor("#121826"), 1))
        for handle in self._handle_rects(rect).values(): painter.drawEllipse(handle)
        painter.end()


class FramePreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.video_source_width = 0
        self.video_source_height = 0
        self.preview_aspect_key = "source"
        self.preview_scale_mode = "fit"
        self.preview_fill_focus_x = 0.5
        self.preview_fill_focus_y = 0.5
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.hide()

    def set_frame_image(self, image_path: str):
        self._pixmap = QPixmap(image_path)
        self.update()

    def clear_frame_image(self):
        self._pixmap = QPixmap()
        self.update()

    def set_video_dimensions(self, width: int, height: int):
        self.video_source_width = max(0, int(width or 0))
        self.video_source_height = max(0, int(height or 0))
        self.update()

    def set_preview_aspect_ratio(self, aspect_key: str):
        self.preview_aspect_key = str(aspect_key or "source").strip().lower() or "source"
        self.update()

    def set_preview_scale_mode(self, scale_mode: str):
        self.preview_scale_mode = str(scale_mode or "fit").strip().lower() or "fit"
        self.update()

    def set_preview_fill_focus(self, focus_x: float, focus_y: float):
        self.preview_fill_focus_x = max(0.0, min(1.0, float(focus_x)))
        self.preview_fill_focus_y = max(0.0, min(1.0, float(focus_y)))
        self.update()

    def _resolve_canvas_aspect_ratio(self) -> float | None:
        aspect_map = {
            "16:9": 16.0 / 9.0,
            "9:16": 9.0 / 16.0,
            "1:1": 1.0,
            "4:3": 4.0 / 3.0,
        }
        if self.preview_aspect_key in aspect_map:
            return aspect_map[self.preview_aspect_key]
        if self.video_source_width and self.video_source_height:
            return self.video_source_width / self.video_source_height
        if not self._pixmap.isNull() and self._pixmap.height() > 0:
            return self._pixmap.width() / self._pixmap.height()
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
        source_w = self.video_source_width or self._pixmap.width()
        source_h = self.video_source_height or self._pixmap.height()
        if not source_w or not source_h:
            return canvas_rect
        source_ratio = source_w / source_h
        canvas_ratio = canvas_rect.width() / canvas_rect.height() if canvas_rect.height() else source_ratio
        if self.preview_scale_mode == "fill":
            if source_ratio > canvas_ratio:
                content_h = canvas_rect.height()
                content_w = content_h * source_ratio
                overflow_w = max(0.0, content_w - canvas_rect.width())
                offset_x = canvas_rect.left() - overflow_w * self.preview_fill_focus_x
                offset_y = canvas_rect.top()
            else:
                content_w = canvas_rect.width()
                content_h = content_w / source_ratio
                offset_x = canvas_rect.left()
                overflow_h = max(0.0, content_h - canvas_rect.height())
                offset_y = canvas_rect.top() - overflow_h * self.preview_fill_focus_y
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._pixmap.isNull():
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        canvas_rect = self.get_preview_canvas_rect()
        target_rect = self.get_video_content_rect()
        painter.setClipRect(canvas_rect)
        painter.drawPixmap(target_rect, self._pixmap, QRectF(self._pixmap.rect()))


class TimelineTrackLabels(QFrame):
    def __init__(self, timeline, parent=None):
        super().__init__(parent)
        self._timeline = timeline
        self.setObjectName("timelineTrackLabels")
        self.setFixedWidth(112)
        self._rows = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = QLabel("Tracks")
        self.header.setFixedHeight(28)
        self.header.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.header.setContentsMargins(14, 0, 0, 0)
        self.header.setObjectName("helperLabel")
        layout.addWidget(self.header)

        for key, title in (("video", "Video"), ("text", "Text"), ("audio", "Audio")):
            row = QFrame()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(14, 8, 10, 8)
            row_layout.setSpacing(1)
            title_label = QLabel(title)
            title_label.setObjectName("statusHeadline")
            title_label.setStyleSheet("font-size: 12px; font-weight: 700;")
            row_layout.addStretch()
            row_layout.addWidget(title_label)
            row_layout.addStretch()
            layout.addWidget(row)
            self._rows[key] = row
        layout.addStretch()
        self.sync_to_timeline()

    def sync_to_timeline(self):
        for key, row in self._rows.items():
            visible = self._timeline.is_track_visible(key)
            row.setVisible(visible)
            layout = getattr(self._timeline, "_layout", {})
            row_height = int(layout.get(key, {}).get("h", 0)) if visible else 0
            row.setFixedHeight(row_height)


def _build_timeline_track_labels(timeline):
    return TimelineTrackLabels(timeline)


def build_preview_panel(gui):
    right_panel = QWidget()
    right_panel.setObjectName("rightPanel")
    right_layout = QVBoxLayout(right_panel)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(10)

    gui.preview_context_label = QLabel("Choose a video to start previewing. Subtitle and voice status will appear here as you work.")
    gui.preview_context_label.setWordWrap(True)
    gui.preview_context_label.setObjectName("previewContextLabel")
    gui.preview_context_label.hide()
    gui.frame_preview_badge_label = QLabel("Frame Preview")
    gui.frame_preview_badge_label.setObjectName("helperLabel")
    gui.frame_preview_badge_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    gui.frame_preview_badge_label.setStyleSheet(
        "QLabel { background: rgba(8, 14, 24, 0.86); border: 1px solid #35506d; "
        "border-radius: 10px; padding: 4px 10px; color: #cfe6ff; font-weight: 700; }"
    )
    gui.frame_preview_badge_label.hide()
    gui.frame_preview_status_label = QLabel("Exact frame preview updates here when available.")
    gui.frame_preview_status_label.setWordWrap(True)
    gui.frame_preview_status_label.setObjectName("helperLabel")
    gui.frame_preview_status_label.hide()
    gui.frame_preview_image_label = FramePreviewWidget()
    gui.frame_preview_image_label.hide()

    gui.video_view = MpvVideoView() if is_mpv_backend_available() else VideoView()
    # The vertical workspace is splitter-resizable.  Keep a practical but
    # compact minimum so the transport row always has its own space when the
    # user gives more room to the timeline.
    gui.video_view.setMinimumHeight(270)
    gui.video_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    gui.timeline = _import_editor_timeline()()
    # The outer Timeline card owns the usable minimum height.  Do not give
    # the nested QGraphicsView the same large minimum: the card also contains
    # its title, toolbar, margins and progress strip, which otherwise causes
    # Qt to squeeze/clip the view (including its horizontal scrollbar) when
    # the Preview is enlarged with the splitter.
    gui.timeline.setMinimumHeight(0)
    gui.timeline.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    if hasattr(gui, "voice_timing_sync_combo"):
        gui.timeline.set_voice_sync_mode(gui.voice_timing_sync_combo.currentText())
    gui.timeline.seekRequestedMs.connect(gui.set_position)
    gui.timeline.segmentSelected.connect(gui.on_timeline_segment_selected)
    gui.timeline.segmentTimingEditStarted.connect(gui.on_timeline_segment_timing_edit_started)
    gui.timeline.segmentTimingChanged.connect(gui.on_timeline_segment_timing_changed)
    gui.timeline.layerTimingChanged.connect(gui.on_timeline_layer_timing_changed)
    gui.timeline.zoomChanged.connect(lambda value: gui.timeline_zoom_label.setText(f"{value}%"))
    gui.timeline.layerSelected.connect(gui.on_timeline_layer_selected)
    gui.timeline.layoutChanged.connect(lambda: getattr(gui, '_sync_track_labels', lambda: None)())
    gui.time_label = QLabel("00:00 / 00:00")
    gui.time_label.setStyleSheet("font-weight: bold; min-width: 100px; color: #6ee7d6;")

    preview_card = QFrame()
    preview_card.setObjectName("statusCard")
    preview_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    preview_card_layout = QVBoxLayout(preview_card)
    preview_card_layout.setContentsMargins(12, 12, 12, 10)
    preview_card_layout.setSpacing(8)
    preview_card_layout.addWidget(gui.preview_context_label)
    preview_card_layout.addWidget(gui.frame_preview_status_label)
    preview_card_layout.addWidget(gui.frame_preview_image_label, 1)
    preview_card_layout.addWidget(gui.video_view, 1)
    preview_card_layout.setStretchFactor(gui.frame_preview_image_label, 1)
    preview_card_layout.setStretchFactor(gui.video_view, 1)

    icons_dir = asset_path("icons")

    def _make_sep():
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setFixedWidth(2)
        sep.setStyleSheet("QFrame { color: #444; }")
        return sep

    gui.play_btn = QPushButton()
    gui.stop_btn = QPushButton()
    gui.preview_btn = QPushButton()
    gui.blur_area_btn = QPushButton()
    gui.blur_area_btn.setCheckable(True)
    gui.blur_area_btn.setChecked(True)
    gui.blur_add_btn = QPushButton()
    gui.ocr_region_btn = QPushButton()
    gui.ocr_region_btn.setCheckable(True)
    gui.ocr_translator_btn = QPushButton("OCR Translate")
    gui.ocr_translator_btn.setCheckable(True)
    _set_preview_icon_button(gui.play_btn, os.path.join(icons_dir, "play.svg"), "Play or pause preview")
    _set_preview_icon_button(gui.stop_btn, os.path.join(icons_dir, "reset.svg"), "Reset preview to the beginning")
    _set_preview_icon_button(gui.preview_btn, os.path.join(icons_dir, "preview.svg"), "Render a fresh preview using current subtitle and audio")
    _set_preview_icon_button(gui.blur_area_btn, os.path.join(icons_dir, "blur.svg"), "Turn blur effect on or off")
    gui.blur_add_btn.setText("Blur")
    gui.blur_add_btn.setToolTip("Add a blur region")
    gui.blur_add_btn.setFixedSize(50, 38)
    gui.blur_add_btn.setEnabled(False)
    gui.blur_add_btn.setStyleSheet(
        "QPushButton { color: #ffc15e; font-weight: bold; font-size: 13px; padding: 0; }"
    )
    gui.ocr_region_btn.setText("OCR")
    gui.ocr_region_btn.setToolTip("Edit OCR subtitle region")
    gui.ocr_region_btn.setFixedSize(38, 38)
    gui.ocr_region_btn.setStyleSheet("QPushButton { color: #6ee7d6; font-weight: bold; font-size: 10px; padding: 0; }")
    gui.ocr_region_btn.hide()
    gui.ocr_translator_btn.setFixedSize(92, 38)
    gui.ocr_translator_btn.setToolTip("Capture and translate visual text from the current video frame")
    gui.ocr_translator_btn.setStyleSheet(
        "QPushButton { color: #d8b4fe; font-weight: bold; font-size: 11px; padding: 0 6px; }"
        "QPushButton:checked { background: #3b2557; border-color: #a855f7; color: #ffffff; }"
    )

    gui.preview_speed_combo = QComboBox()
    gui.preview_speed_combo.addItem("0.75x", 0.75)
    gui.preview_speed_combo.addItem("1.0x", 1.0)
    gui.preview_speed_combo.addItem("1.25x", 1.25)
    gui.preview_speed_combo.addItem("1.5x", 1.5)
    gui.preview_speed_combo.addItem("2.0x", 2.0)
    gui.preview_speed_combo.setCurrentIndex(1)

    play_group = QHBoxLayout()
    play_group.setSpacing(6)
    play_group.addWidget(gui.play_btn)
    play_group.addWidget(gui.stop_btn)
    play_group.addWidget(gui.preview_btn)

    blur_group = QHBoxLayout()
    blur_group.setSpacing(6)
    # The blur ON/OFF toggle is controlled by clicking the B1
    # track label in the timeline's left strip (like audio mute).
    gui.blur_area_btn.setVisible(False)
    # Dedicated Logo / Watermark button (beside the Blur button)
    gui.add_logo_btn = QPushButton("Logo")
    gui.add_logo_btn.setFixedSize(50, 38)
    gui.add_logo_btn.setEnabled(False)
    gui.add_logo_btn.setStyleSheet(
        "QPushButton { color: #6ee7d6; font-weight: bold; font-size: 13px; padding: 0; }"
    )
    gui.add_logo_btn.setToolTip("Add a logo or watermark image to the video")
    gui.add_logo_btn.clicked.connect(
        lambda: gui.on_add_timeline_layer("logo") if hasattr(gui, "on_add_timeline_layer") else None
    )
    # Dedicated Mask button (beside the Logo button). Adds a new
    # rectangular mask to the M1 track.
    gui.add_mask_btn = QPushButton("Mask")
    gui.add_mask_btn.setFixedSize(50, 38)
    gui.add_mask_btn.setEnabled(False)
    gui.add_mask_btn.setStyleSheet(
        "QPushButton { color: #c98c5a; font-weight: bold; font-size: 13px; padding: 0; }"
    )
    gui.add_mask_btn.setToolTip("Add a rectangular mask to the video")
    gui.add_mask_btn.clicked.connect(
        lambda: gui.on_add_timeline_layer("mask") if hasattr(gui, "on_add_timeline_layer") else None
    )
    gui.add_text_btn = QPushButton("Text")
    gui.add_text_btn.setFixedSize(50, 38)
    gui.add_text_btn.setEnabled(False)
    gui.add_text_btn.setStyleSheet(
        "QPushButton { color: #c084fc; font-weight: bold; font-size: 13px; padding: 0; }"
    )
    gui.add_text_btn.setToolTip("Add editable text to the video")
    gui.add_text_btn.clicked.connect(
        lambda: gui.on_add_timeline_layer("text") if hasattr(gui, "on_add_timeline_layer") else None
    )
    blur_group.addWidget(gui.blur_add_btn)
    blur_group.addWidget(gui.add_logo_btn)
    blur_group.addWidget(gui.add_mask_btn)
    blur_group.addWidget(gui.add_text_btn)
    blur_group.addWidget(gui.ocr_region_btn)
    blur_group.addWidget(gui.ocr_translator_btn)

    speed_group = QHBoxLayout()
    speed_group.setSpacing(6)
    speed_group.addStretch(0)
    speed_group.addWidget(QLabel("Speed"))
    speed_group.addWidget(gui.preview_speed_combo)

    transport_row = QHBoxLayout()
    transport_row.setContentsMargins(0, 0, 0, 0)
    transport_row.setSpacing(10)
    transport_row.addLayout(play_group)
    transport_row.addWidget(_make_sep())
    transport_row.addLayout(blur_group)
    transport_row.addWidget(_make_sep())
    transport_row.addLayout(speed_group, 1)
    transport_row.addWidget(gui.time_label)
    preview_card_layout.addLayout(transport_row)
    gui.frame_preview_badge_label.setParent(preview_card)
    gui.frame_preview_badge_label.raise_()

    gui.ocr_region_overlay = OcrRegionOverlay()
    gui.ocr_region_overlay.attach_to_view(gui.video_view)
    gui.ocr_translator_overlay = OcrTranslatorOverlay()
    gui.ocr_translator_overlay.attach_to_view(gui.video_view)

    def _sync_ocr_overlay():
        overlay = getattr(gui, "ocr_region_overlay", None)
        if overlay is None:
            pass
        else:
            overlay.sync_to_view()
        translator_overlay = getattr(gui, "ocr_translator_overlay", None)
        if translator_overlay is not None:
            translator_overlay.sync_to_view()

    gui._sync_ocr_overlay = _sync_ocr_overlay
    gui._preview_text_resize_refresh_pending = False

    def _flush_preview_text_resize():
        gui._preview_text_resize_refresh_pending = False
        refresh_text = getattr(gui, "_refresh_text_layer_preview", None)
        timeline = getattr(gui, "timeline", None)
        if callable(refresh_text):
            try:
                refresh_text(str(getattr(timeline, "_selected_layer_id", "") or ""))
            except (AttributeError, RuntimeError):
                # The application may still be constructing its preview state
                # or already be closing. A normal project refresh handles the
                # former; neither case should interrupt a resize.
                pass

    def _sync_canvas_bound_overlays():
        """Refresh overlays whose pixel layout depends on preview geometry.

        ``MpvVideoView`` can reposition the top-level Text overlay itself,
        but the text item's font size and padding are calculated from the
        current preview-canvas height in the main window.  A splitter resize
        therefore needs to rebuild that small payload immediately; waiting
        for the next playback tick left Text at the old scale.
        """
        _sync_ocr_overlay()
        # Preserve immediate-looking feedback (one update within a frame)
        # without rebuilding the Text payload for every mouse-move emitted by
        # a splitter drag.
        if not getattr(gui, "_preview_text_resize_refresh_pending", False):
            gui._preview_text_resize_refresh_pending = True
            QtCore.QTimer.singleShot(16, _flush_preview_text_resize)

    _ocr_video = gui.video_view
    _ocr_orig_resize = _ocr_video.resizeEvent
    def _ocr_resize_handler(event):
        _ocr_orig_resize(event)
        _sync_canvas_bound_overlays()
    _ocr_video.resizeEvent = _ocr_resize_handler
    _ocr_orig_move = _ocr_video.moveEvent
    def _ocr_move_handler(event):
        _ocr_orig_move(event)
        _sync_ocr_overlay()
    _ocr_video.moveEvent = _ocr_move_handler
    _ocr_orig_show = _ocr_video.showEvent
    def _ocr_show_handler(event):
        _ocr_orig_show(event)
        _sync_ocr_overlay()
    _ocr_video.showEvent = _ocr_show_handler
    QtCore.QTimer.singleShot(0, _sync_ocr_overlay)

    timeline_card = QFrame()
    timeline_card.setObjectName("statusCard")
    timeline_layout = QVBoxLayout(timeline_card)
    timeline_layout.setContentsMargins(12, 10, 12, 10)
    timeline_layout.setSpacing(8)

    timeline_header_layout = QHBoxLayout()
    timeline_header_layout.setSpacing(10)
    timeline_copy_layout = QVBoxLayout()
    timeline_copy_layout.setSpacing(1)
    timeline_title = QLabel("Timeline")
    timeline_title.setObjectName("statusHeadline")
    timeline_meta = QLabel("Editor-first layout with dedicated video, audio, and subtitle lanes.")
    timeline_meta.setObjectName("helperLabel")
    timeline_copy_layout.addWidget(timeline_title)
    timeline_copy_layout.addWidget(timeline_meta)
    timeline_header_layout.addLayout(timeline_copy_layout, 0)
    gui.timeline_undo_btn = QPushButton("Undo")
    gui.timeline_undo_btn.setFixedWidth(58)
    gui.timeline_undo_btn.setEnabled(False)
    gui.timeline_redo_btn = QPushButton("Redo")
    gui.timeline_redo_btn.setFixedWidth(58)
    gui.timeline_redo_btn.setEnabled(False)
    gui.timeline_split_btn = QPushButton("Split")
    gui.timeline_split_btn.setFixedWidth(58)
    gui.timeline_split_btn.setEnabled(False)
    gui.timeline_delete_btn = QPushButton("Delete")
    gui.timeline_delete_btn.setFixedWidth(62)
    gui.timeline_delete_btn.setEnabled(False)
    gui.timeline_undo_btn.clicked.connect(gui.undo_last_timeline_timing_edit)
    gui.timeline_redo_btn.clicked.connect(gui.redo_last_timeline_timing_edit)
    gui.timeline_split_btn.clicked.connect(gui.split_selected_timeline_segment)
    gui.timeline_delete_btn.clicked.connect(gui.delete_selected_timeline_segment)
    gui.timeline_selection_mode_btn = QPushButton("Select Range")
    gui.timeline_selection_mode_btn.setCheckable(True)
    gui.timeline_selection_mode_btn.setFixedWidth(96)
    gui.timeline_selection_mode_btn.setToolTip("Enable Selection Range Mode")
    gui.timeline_selection_mode_btn.setStyleSheet(
        "QPushButton:checked { background:#244b6d; border-color:#71adff; color:#ffffff; }"
    )
    gui.timeline_selection_mode_btn.toggled.connect(gui.timeline.set_selection_mode)
    gui.timeline_clear_selection_btn = QPushButton("Clear Range")
    gui.timeline_clear_selection_btn.setFixedWidth(96)
    gui.timeline_clear_selection_btn.setToolTip("Clear the timeline selection range (Esc)")
    gui.timeline_clear_selection_btn.setEnabled(False)
    gui.timeline_clear_selection_btn.hide()
    gui.timeline_clear_selection_btn.clicked.connect(gui.timeline.clear_selection_range)
    gui.timeline_alt_transcribe_btn = QPushButton("Alt Transcribe")
    gui.timeline_alt_transcribe_btn.setFixedWidth(118)
    gui.timeline_alt_transcribe_btn.setToolTip("Retranscribe the Selection Range with custom Whisper or OCR settings")
    gui.timeline_alt_transcribe_btn.hide()
    gui.timeline_alt_transcribe_btn.clicked.connect(gui.transcribe_selected_range_alternate)
    def _on_range_changed(_start, _end):
        gui.timeline_clear_selection_btn.setEnabled(True)
        gui.timeline_clear_selection_btn.show()
        if hasattr(gui, "_update_alt_transcribe_button_label"):
            gui._update_alt_transcribe_button_label()
        gui.timeline_alt_transcribe_btn.show()
        # Range signals can arrive in the middle of the media-state update.
        # Refresh once on the next event-loop turn so the action is enabled
        # after the timeline has entered paused/edit mode.
        if hasattr(gui, "refresh_ui_state"):
            QtCore.QTimer.singleShot(0, gui.refresh_ui_state)
    def _on_range_cleared():
        gui.timeline_clear_selection_btn.setEnabled(False)
        gui.timeline_clear_selection_btn.hide()
        gui.timeline_alt_transcribe_btn.hide()
    gui.timeline.selectionRangeChanged.connect(_on_range_changed)
    gui.timeline.selectionRangeCleared.connect(_on_range_cleared)
    def _on_selection_mode_changed(enabled):
        gui.timeline_selection_mode_btn.blockSignals(True)
        gui.timeline_selection_mode_btn.setChecked(bool(enabled))
        gui.timeline_selection_mode_btn.blockSignals(False)
        gui.timeline_selection_mode_btn.setText("Selecting" if enabled else "Select Range")
        gui.timeline_selection_mode_btn.setToolTip(
            "Selection Range Mode active: drag the ruler to create or adjust a range"
            if enabled else "Enable Selection Range Mode"
        )
    gui.timeline.selectionModeChanged.connect(_on_selection_mode_changed)
    gui.timeline_layers_btn = QPushButton("Layers")
    gui.timeline_layers_btn.setFixedWidth(76)
    gui.timeline_layers_btn.setToolTip("Show or hide layers on the timeline only")
    gui.timeline_layers_menu = QMenu(gui.timeline_layers_btn)
    gui.timeline_layers_menu.setStyleSheet(
        "QMenu { background: #142030; color: #dbe5f3; border: 1px solid #2f4868; padding: 5px; }"
        "QMenu::item { padding: 7px 24px 7px 10px; border-radius: 4px; }"
        "QMenu::item:selected { background: #203a56; color: #ffffff; }"
        "QMenu::item:disabled { color: #71839a; }"
        "QMenu::indicator { width: 14px; height: 14px; }"
        "QMenu::indicator:unchecked { border: 1px solid #61758e; background: #0d1220; }"
        "QMenu::indicator:checked { border: 1px solid #6ee7d6; background: #1f8d83; }"
    )
    gui.timeline_layers_menu.aboutToShow.connect(
        lambda: gui.populate_timeline_layers_menu() if hasattr(gui, "populate_timeline_layers_menu") else None
    )
    gui.timeline_layers_btn.setMenu(gui.timeline_layers_menu)
    gui.timeline_zoom_out_btn = QPushButton("-")
    gui.timeline_zoom_out_btn.setFixedWidth(34)
    gui.timeline_zoom_label = QLabel(f"{gui.timeline.zoom_percent()}%")
    gui.timeline_zoom_label.setObjectName("helperLabel")
    gui.timeline_zoom_label.setAlignment(Qt.AlignCenter)
    gui.timeline_zoom_label.setFixedWidth(48)
    gui.timeline_zoom_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    gui.timeline_zoom_in_btn = QPushButton("+")
    gui.timeline_zoom_in_btn.setFixedWidth(34)
    gui.timeline_zoom_reset_btn = QPushButton("Fit")
    gui.timeline_zoom_reset_btn.setFixedWidth(52)
    gui.timeline_zoom_out_btn.clicked.connect(gui.timeline.zoom_out)
    gui.timeline_zoom_in_btn.clicked.connect(gui.timeline.zoom_in)
    gui.timeline_zoom_reset_btn.clicked.connect(gui.timeline.reset_zoom)

    edit_group = QHBoxLayout()
    edit_group.setSpacing(4)
    edit_group.addWidget(gui.timeline_undo_btn)
    edit_group.addWidget(gui.timeline_redo_btn)
    edit_group.addWidget(_make_sep())
    edit_group.addWidget(gui.timeline_split_btn)
    edit_group.addWidget(gui.timeline_delete_btn)
    edit_group.addWidget(_make_sep())
    edit_group.addWidget(gui.timeline_selection_mode_btn)
    edit_group.addWidget(gui.timeline_clear_selection_btn)
    edit_group.addWidget(gui.timeline_alt_transcribe_btn)
    edit_group.addWidget(_make_sep())
    edit_group.addWidget(gui.timeline_layers_btn)

    gui.add_layer_btn = QPushButton("+ Layer")
    gui.add_layer_btn.setFixedWidth(82)
    gui.add_layer_btn.setToolTip("Add a new layer")
    # Keep this menu button visually consistent with the dark timeline toolbar.
    # QMenu buttons can otherwise fall back to the platform's light palette.
    gui.add_layer_btn.setStyleSheet(
        "QPushButton { background: #213248; color: #ffffff; border: 1px solid #304b69; "
        "border-radius: 10px; padding: 8px 10px; font-weight: bold; font-size: 11px; }"
        "QPushButton:hover { background: #2d4665; border-color: #4575a8; }"
        "QPushButton:pressed { background: #182a3d; border-color: #6ee7d6; }"
        "QPushButton:disabled { background: #182636; color: #8ea3bb; border-color: #29405d; }"
    )
    gui.add_layer_btn.setEnabled(False)
    gui._layer_menu = QMenu(gui.add_layer_btn)
    gui._layer_menu.setStyleSheet(
        "QMenu { background: #142030; color: #dbe5f3; border: 1px solid #2f4868; padding: 5px; }"
        "QMenu::item { padding: 7px 24px 7px 10px; border-radius: 4px; }"
        "QMenu::item:selected { background: #203a56; color: #ffffff; }"
        "QMenu::item:disabled { color: #71839a; }"
    )
    gui.add_subtitle_segment_action = gui._layer_menu.addAction(
        "Subtitle Segment", lambda: gui.on_add_timeline_layer("subtitle")
    )
    gui._layer_menu.addAction("Text", lambda: gui.on_add_timeline_layer("text"))
    gui._layer_menu.addAction("Logo / Watermark", lambda: gui.on_add_timeline_layer("logo"))
    gui._layer_menu.addAction("Blur", lambda: gui.on_add_timeline_layer("blur"))
    gui._layer_menu.addAction("Mask", lambda: gui.on_add_timeline_layer("mask"))
    gui.add_layer_btn.setMenu(gui._layer_menu)
    edit_group.addWidget(_make_sep())
    edit_group.addWidget(gui.add_layer_btn)

    timeline_header_layout.addStretch(1)
    timeline_header_layout.addLayout(edit_group)
    timeline_layout.addLayout(timeline_header_layout)

    timeline_body_layout = QHBoxLayout()
    timeline_body_layout.setContentsMargins(0, 0, 0, 0)
    timeline_body_layout.setSpacing(0)

    from views.editor.track_labels import TrackLabelBar
    gui.track_label_bar = TrackLabelBar()
    if hasattr(gui, "timeline"):
        gui.track_label_bar.set_timeline(gui.timeline)
    gui.track_label_bar.muteToggled.connect(gui.on_track_mute_toggled)
    gui.track_label_bar.blurToggled.connect(gui.on_track_blur_toggled)
    gui.track_label_bar.logoToggled.connect(gui.on_track_logo_toggled)
    gui.track_label_bar.maskToggled.connect(gui.on_track_mask_toggled)
    gui.track_label_bar.textToggled.connect(gui.on_track_text_toggled)
    gui.track_label_bar.subtitleToggled.connect(gui.on_track_subtitle_toggled)
    gui.track_label_bar.trackSelected.connect(gui.on_track_label_selected)
    gui.track_label_bar.lockToggled.connect(gui.on_timeline_track_lock_toggled)

    def _sync_labels():
        if hasattr(gui, "timeline") and gui.timeline._timeline:
            tracks = [t for t in gui.timeline._timeline.tracks if gui.timeline.is_track_shown_on_timeline(t)]
            names = [t.name for t in tracks]
            heights = [gui.timeline._track_heights.get(t.id, 80) for t in tracks]
            locked = [t.locked for t in tracks]
            muted = [t.muted for t in tracks]
            gui.track_label_bar.set_tracks(names, heights, locked, muted)
            gui.track_label_bar.set_text_shown(
                "T1 Text", bool(getattr(gui, "_text_track_preview_visible", True))
            )
            gui.track_label_bar.set_logo_shown(
                "L1 Logo", bool(getattr(gui, "_logo_track_preview_visible", True))
            )
            gui.track_label_bar.set_mask_shown(
                "M1", bool(getattr(gui, "_mask_track_preview_visible", True))
            )
            gui.track_label_bar.set_blur_on(
                "B1", bool(getattr(gui, "_blur_effect_enabled", lambda: True)())
            )
            gui.track_label_bar.set_subtitle_shown(
                "TS1", bool(getattr(gui, "_subtitle_track_preview_visible", True))
            )
    gui._sync_track_labels = _sync_labels
    _sync_labels()

    timeline_body_layout.addWidget(gui.track_label_bar)
    timeline_body_layout.addWidget(gui.timeline, 1)
    # Give the scrollable timeline body all remaining card space.  The title,
    # toolbar and progress strip keep their compact fixed height above/below.
    timeline_layout.addLayout(timeline_body_layout, 1)

    gui.progress_bar = QProgressBar()
    gui.progress_bar.setFixedHeight(8)
    gui.progress_bar.setTextVisible(False)
    timeline_layout.addWidget(gui.progress_bar)

    gui.translated_text = QTextEdit()
    gui.translated_text.setPlaceholderText("Vietnamese subtitle text will appear here. You can edit it before export.")
    gui.translated_text.hide()
    gui.transcript_text = QTextEdit()
    gui.transcript_text.setPlaceholderText("The original subtitle transcript will appear here...")
    gui.transcript_text.hide()

    inspector_shell = QWidget()
    inspector_shell.setObjectName("subtitleInspectorShell")
    inspector_shell.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    inspector_shell_layout = QHBoxLayout(inspector_shell)
    inspector_shell_layout.setContentsMargins(0, 0, 0, 0)
    inspector_shell_layout.setSpacing(0)

    # Stacked container: index 0 = subtitle inspector, 1 = audio track
    # inspector, 2 = default/empty inspector. The active card is shown
    # when the user clicks a layer on the matching track type.
    inspector_stack = QStackedWidget()
    inspector_stack.setMinimumWidth(400)
    inspector_stack.setMaximumWidth(560)
    gui.inspector_stack = inspector_stack

    inspector_card = QFrame()
    inspector_card.setObjectName("statusCard")
    inspector_card.setMinimumWidth(400)
    inspector_card.setMaximumWidth(560)
    inspector_layout = QVBoxLayout(inspector_card)
    inspector_layout.setContentsMargins(10, 6, 10, 6)
    inspector_layout.setSpacing(6)

    inspector_header = QHBoxLayout()
    inspector_header.setSpacing(8)
    inspector_copy = QVBoxLayout()
    inspector_copy.setSpacing(2)
    inspector_title = QLabel("Subtitle Inspector")
    inspector_title.setObjectName("statusHeadline")
    inspector_copy.addWidget(inspector_title)
    gui.subtitle_inspector_summary_label = QLabel("")
    gui.subtitle_inspector_summary_label.setObjectName("helperLabel")
    gui.subtitle_inspector_summary_label.setWordWrap(True)
    gui.subtitle_inspector_summary_label.setVisible(False)
    inspector_copy.addWidget(gui.subtitle_inspector_summary_label)
    inspector_header.addLayout(inspector_copy, 1)
    inspector_header.addStretch(0)
    inspector_layout.addLayout(inspector_header)

    # --- Action buttons at top ---
    inspector_actions_row = QHBoxLayout()
    inspector_actions_row.setSpacing(8)
    gui.rewrite_translation_btn = QPushButton("Rewrite")
    gui.subtitle_editor_btn = QPushButton("Subtitle Editor")
    gui.import_translation_btn = QPushButton("Import SRT")
    gui.audio_inspector_regenerate_voice_btn = QPushButton("Regenerate voice")
    gui.audio_inspector_regenerate_voice_btn.setToolTip(
        "Re-generate the dubbed voice for the currently selected segment."
    )
    gui.rewrite_translation_btn.setEnabled(False)
    gui.subtitle_editor_btn.setEnabled(False)
    gui.audio_inspector_regenerate_voice_btn.setEnabled(False)
    gui.subtitle_editor_btn.clicked.connect(gui.open_subtitle_editor)
    inspector_actions_row.addWidget(gui.rewrite_translation_btn)
    inspector_actions_row.addWidget(gui.subtitle_editor_btn)
    inspector_actions_row.addWidget(gui.import_translation_btn)
    inspector_actions_row.addWidget(gui.audio_inspector_regenerate_voice_btn)
    inspector_actions_row.addStretch(1)
    inspector_layout.addLayout(inspector_actions_row)

    # The original transcript is shown immediately above the editable
    # "Text shown on screen" field inside the selected subtitle card.  Do
    # not add a second shared copy below the toolbar.
    gui.inspector_shared_original_label = None
    gui.inspector_original_text_label = None

    gui.segment_editor_container = QWidget()
    gui.segment_editor_container.setObjectName("segmentEditorContainer")
    gui.segment_editor_layout = QVBoxLayout(gui.segment_editor_container)
    gui.segment_editor_layout.setContentsMargins(0, 0, 0, 0)
    gui.segment_editor_layout.setSpacing(10)
    inspector_layout.addWidget(gui.segment_editor_container, 1)

    # --- Audio Track Inspector ---
    # Compact card: header + volume slider + mute/solo + gain/speed. The
    # surrounding tool UI should not reflow when the user switches
    # between subtitle and audio inspectors.
    audio_inspector_card = QFrame()
    audio_inspector_card.setObjectName("statusCard")
    audio_inspector_card.setMinimumWidth(400)
    audio_inspector_card.setMaximumWidth(560)
    gui.audio_inspector_card = audio_inspector_card
    audio_layout = QVBoxLayout(audio_inspector_card)
    audio_layout.setContentsMargins(12, 10, 12, 10)
    audio_layout.setSpacing(6)

    audio_header = QHBoxLayout()
    audio_header.setSpacing(6)
    audio_header.setContentsMargins(0, 0, 0, 0)
    audio_copy = QVBoxLayout()
    audio_copy.setSpacing(0)
    audio_copy.setContentsMargins(0, 0, 0, 0)
    audio_title = QLabel("Audio")
    audio_title.setObjectName("statusHeadline")
    audio_copy.addWidget(audio_title)
    # Information labels (track name, layer count) are kept for
    # internal use (e.g. _current_audio_track_for_inspector reads
    # the track name) but hidden from the user.
    gui.audio_inspector_track_name_label = QLabel("-")
    gui.audio_inspector_track_name_label.setObjectName("helperLabel")
    gui.audio_inspector_track_name_label.setWordWrap(False)
    gui.audio_inspector_track_name_label.setVisible(False)
    audio_copy.addWidget(gui.audio_inspector_track_name_label)
    gui.audio_inspector_layer_count_label = QLabel("-")
    gui.audio_inspector_layer_count_label.setObjectName("helperLabel")
    gui.audio_inspector_layer_count_label.setWordWrap(False)
    gui.audio_inspector_layer_count_label.setVisible(False)
    audio_copy.addWidget(gui.audio_inspector_layer_count_label)
    audio_header.addLayout(audio_copy, 1)
    audio_layout.addLayout(audio_header)

    gui.audio_inspector_summary_label = QLabel("Select an audio track to view its settings.")
    gui.audio_inspector_summary_label.setObjectName("helperLabel")
    gui.audio_inspector_summary_label.setWordWrap(True)
    audio_layout.addWidget(gui.audio_inspector_summary_label)

    # Fade-in / Fade-out row
    fade_row = QHBoxLayout()
    fade_row.setSpacing(6)
    fade_row.setContentsMargins(0, 0, 0, 0)
    fade_in_label = QLabel("Fade In")
    fade_in_label.setMinimumWidth(50)
    fade_row.addWidget(fade_in_label)
    gui.audio_inspector_fade_in_spin = QDoubleSpinBox()
    gui.audio_inspector_fade_in_spin.setRange(0.0, 30.0)
    gui.audio_inspector_fade_in_spin.setSingleStep(0.1)
    gui.audio_inspector_fade_in_spin.setValue(0.0)
    gui.audio_inspector_fade_in_spin.setSuffix(" s")
    gui.audio_inspector_fade_in_spin.setMaximumWidth(80)
    fade_row.addWidget(gui.audio_inspector_fade_in_spin)
    fade_out_label = QLabel("Fade Out")
    fade_out_label.setMinimumWidth(60)
    fade_row.addWidget(fade_out_label)
    gui.audio_inspector_fade_out_spin = QDoubleSpinBox()
    gui.audio_inspector_fade_out_spin.setRange(0.0, 30.0)
    gui.audio_inspector_fade_out_spin.setSingleStep(0.1)
    gui.audio_inspector_fade_out_spin.setValue(0.0)
    gui.audio_inspector_fade_out_spin.setSuffix(" s")
    gui.audio_inspector_fade_out_spin.setMaximumWidth(80)
    fade_row.addWidget(gui.audio_inspector_fade_out_spin)
    fade_row.addStretch(1)
    audio_layout.addLayout(fade_row)

    # (dB gain and Speed sections were removed - too much clutter)
    # (Mute/Solo buttons removed - mute now toggled by clicking the
    # track label in the timeline's left strip)

    # Helpful tip
    gui.audio_inspector_tip_label = QLabel(
        "Volume and gain affect this audio track during preview. "
        "Mute toggles the track's audio output. Speed changes playback rate."
    )
    gui.audio_inspector_tip_label.setObjectName("helperLabel")
    gui.audio_inspector_tip_label.setWordWrap(True)
    audio_layout.addWidget(gui.audio_inspector_tip_label)

    # Dub voice widgets are now in the inspector's Dub tab.
    # audio_inspector_card only has volume/fade controls for A1 Audio.
    gui.audio_inspector_dub_section = QWidget()
    gui.audio_inspector_dub_section.setVisible(False)
    audio_layout.addWidget(gui.audio_inspector_dub_section)
    audio_layout.addStretch(1)

    def _add_layer_timing_controls(layout, prefix):
        """Add common start/end controls for timeline-backed overlay layers."""
        timing_row = QHBoxLayout()
        timing_row.setSpacing(6)
        timing_row.addWidget(QLabel("Start"))
        start_spin = QDoubleSpinBox()
        start_spin.setRange(0.0, 86400.0)
        start_spin.setDecimals(2)
        start_spin.setSingleStep(0.1)
        start_spin.setSuffix(" s")
        start_spin.setMinimumWidth(104)
        setattr(gui, f"{prefix}_inspector_start_spin", start_spin)
        timing_row.addWidget(start_spin, 1)
        timing_row.addWidget(QLabel("End"))
        end_spin = QDoubleSpinBox()
        end_spin.setRange(0.1, 86400.0)
        end_spin.setDecimals(2)
        end_spin.setSingleStep(0.1)
        end_spin.setSuffix(" s")
        end_spin.setMinimumWidth(104)
        setattr(gui, f"{prefix}_inspector_end_spin", end_spin)
        timing_row.addWidget(end_spin, 1)
        layout.addLayout(timing_row)

    # --- Blur Track Inspector ---
    blur_inspector_card = QFrame()
    blur_inspector_card.setObjectName("statusCard")
    blur_inspector_card.setMinimumWidth(400)
    blur_inspector_card.setMaximumWidth(560)
    gui.blur_inspector_card = blur_inspector_card
    blur_layout = QVBoxLayout(blur_inspector_card)
    blur_layout.setContentsMargins(12, 10, 12, 10)
    blur_layout.setSpacing(6)

    blur_title = QLabel("Blur")
    blur_title.setObjectName("statusHeadline")
    blur_layout.addWidget(blur_title)
    gui.blur_inspector_track_name_label = QLabel("-")
    gui.blur_inspector_track_name_label.setObjectName("helperLabel")
    gui.blur_inspector_track_name_label.setVisible(False)
    blur_layout.addWidget(gui.blur_inspector_track_name_label)
    gui.blur_inspector_layer_count_label = QLabel("-")
    gui.blur_inspector_layer_count_label.setObjectName("helperLabel")
    gui.blur_inspector_layer_count_label.setVisible(False)
    blur_layout.addWidget(gui.blur_inspector_layer_count_label)
    gui.blur_inspector_summary_label = QLabel(
        "Blur regions in this track. Use the B1 layer visibility control "
        "in the timeline to show or hide the effect."
    )
    gui.blur_inspector_summary_label.setObjectName("helperLabel")
    gui.blur_inspector_summary_label.setWordWrap(True)
    blur_layout.addWidget(gui.blur_inspector_summary_label)
    _add_layer_timing_controls(blur_layout, "blur")

    # --- Blur Radius ---
    radius_row = QHBoxLayout()
    radius_label = QLabel("Blur radius")
    radius_label.setObjectName("sectionTitle")
    radius_label.setFixedWidth(90)
    radius_row.addWidget(radius_label)
    gui.blur_inspector_radius_slider = QSlider(Qt.Horizontal)
    gui.blur_inspector_radius_slider.setRange(1, 20)
    gui.blur_inspector_radius_slider.setValue(20)
    gui.blur_inspector_radius_slider.setToolTip(
        "How strong the blur is (1 = light, 20 = heavy)."
    )
    radius_row.addWidget(gui.blur_inspector_radius_slider, 1)
    gui.blur_inspector_radius_value_label = QLabel("20")
    gui.blur_inspector_radius_value_label.setFixedWidth(28)
    radius_row.addWidget(gui.blur_inspector_radius_value_label)
    blur_layout.addLayout(radius_row)

    # --- Blur Opacity ---
    blur_opacity_row = QHBoxLayout()
    blur_opacity_label = QLabel("Opacity")
    blur_opacity_label.setObjectName("sectionTitle")
    blur_opacity_label.setFixedWidth(90)
    blur_opacity_row.addWidget(blur_opacity_label)
    gui.blur_inspector_opacity_slider = QSlider(Qt.Horizontal)
    gui.blur_inspector_opacity_slider.setRange(0, 100)
    gui.blur_inspector_opacity_slider.setValue(100)
    gui.blur_inspector_opacity_slider.setToolTip(
        "Transparency of the blurred region (0% fully transparent, "
        "100% fully visible)."
    )
    blur_opacity_row.addWidget(gui.blur_inspector_opacity_slider, 1)
    gui.blur_inspector_opacity_value_label = QLabel("100%")
    gui.blur_inspector_opacity_value_label.setFixedWidth(40)
    blur_opacity_row.addWidget(gui.blur_inspector_opacity_value_label)
    blur_layout.addLayout(blur_opacity_row)

    # --- Pixelate toggle ---
    pixelate_row = QHBoxLayout()
    pixelate_label = QLabel("Pixelate")
    pixelate_label.setObjectName("sectionTitle")
    pixelate_label.setFixedWidth(90)
    pixelate_row.addWidget(pixelate_label)
    gui.blur_inspector_pixelate_cb = QCheckBox("Enable pixelate (mosaic)")
    gui.blur_inspector_pixelate_cb.setChecked(False)
    gui.blur_inspector_pixelate_cb.setToolTip(
        "Replace the blur with a mosaic / pixelate effect over the region."
    )
    pixelate_row.addWidget(gui.blur_inspector_pixelate_cb)
    pixelate_row.addStretch(1)
    blur_layout.addLayout(pixelate_row)

    # --- Pixelate size ---
    pixel_size_row = QHBoxLayout()
    pixel_size_label = QLabel("Pixel size")
    pixel_size_label.setObjectName("sectionTitle")
    pixel_size_label.setFixedWidth(90)
    pixel_size_row.addWidget(pixel_size_label)
    gui.blur_inspector_pixel_size_slider = QSlider(Qt.Horizontal)
    gui.blur_inspector_pixel_size_slider.setRange(2, 60)
    gui.blur_inspector_pixel_size_slider.setValue(12)
    gui.blur_inspector_pixel_size_slider.setToolTip(
        "Mosaic cell size in pixels (larger = chunkier pixels)."
    )
    pixel_size_row.addWidget(gui.blur_inspector_pixel_size_slider, 1)
    gui.blur_inspector_pixel_size_value_label = QLabel("12")
    gui.blur_inspector_pixel_size_value_label.setFixedWidth(28)
    pixel_size_row.addWidget(gui.blur_inspector_pixel_size_value_label)
    blur_layout.addLayout(pixel_size_row)

    gui.blur_inspector_tip_label = QLabel(
        "Each click of the 'Blur' button adds a new blur region to this "
        "track. Toggle 'Show on preview' to hide the visual blur while "
        "keeping the layers."
    )
    gui.blur_inspector_tip_label.setObjectName("helperLabel")
    gui.blur_inspector_tip_label.setWordWrap(True)
    blur_layout.addWidget(gui.blur_inspector_tip_label)
    blur_layout.addStretch(1)

    # --- Logo Track Inspector ---
    logo_inspector_card = QFrame()
    logo_inspector_card.setObjectName("statusCard")
    logo_inspector_card.setMinimumWidth(400)
    logo_inspector_card.setMaximumWidth(560)
    gui.logo_inspector_card = logo_inspector_card
    logo_layout = QVBoxLayout(logo_inspector_card)
    logo_layout.setContentsMargins(12, 10, 12, 10)
    logo_layout.setSpacing(6)

    logo_title = QLabel("L1 Logo")
    logo_title.setObjectName("statusHeadline")
    logo_layout.addWidget(logo_title)
    gui.logo_inspector_summary_label = QLabel(
        "Adjust the watermark image on the video. Drag the logo on the "
        "preview to reposition; use the controls below for opacity and "
        "rotation."
    )
    gui.logo_inspector_summary_label.setObjectName("helperLabel")
    gui.logo_inspector_summary_label.setWordWrap(True)
    logo_layout.addWidget(gui.logo_inspector_summary_label)
    _add_layer_timing_controls(logo_layout, "logo")

    # --- Opacity ---
    opacity_row = QHBoxLayout()
    opacity_label = QLabel("Opacity")
    opacity_label.setObjectName("sectionTitle")
    opacity_label.setFixedWidth(90)
    opacity_row.addWidget(opacity_label)
    gui.logo_inspector_opacity_slider = QSlider(Qt.Horizontal)
    gui.logo_inspector_opacity_slider.setRange(0, 100)
    gui.logo_inspector_opacity_slider.setValue(100)
    gui.logo_inspector_opacity_slider.setToolTip(
        "Set the transparency of the logo (0% invisible, 100% fully visible)."
    )
    opacity_row.addWidget(gui.logo_inspector_opacity_slider, 1)
    gui.logo_inspector_opacity_value_label = QLabel("100%")
    gui.logo_inspector_opacity_value_label.setFixedWidth(40)
    opacity_row.addWidget(gui.logo_inspector_opacity_value_label)
    logo_layout.addLayout(opacity_row)

    # --- Rotation ---
    rotation_row = QHBoxLayout()
    rotation_label = QLabel("Rotation")
    rotation_label.setObjectName("sectionTitle")
    rotation_label.setFixedWidth(90)
    rotation_row.addWidget(rotation_label)
    gui.logo_inspector_rotation_slider = QSlider(Qt.Horizontal)
    gui.logo_inspector_rotation_slider.setRange(-180, 180)
    gui.logo_inspector_rotation_slider.setValue(0)
    gui.logo_inspector_rotation_slider.setToolTip(
        "Rotate the logo in degrees (-180 to 180)."
    )
    rotation_row.addWidget(gui.logo_inspector_rotation_slider, 1)
    gui.logo_inspector_rotation_value_label = QLabel("0°")
    gui.logo_inspector_rotation_value_label.setFixedWidth(40)
    rotation_row.addWidget(gui.logo_inspector_rotation_value_label)
    logo_layout.addLayout(rotation_row)

    gui.logo_inspector_tip_label = QLabel(
        "Tip: drag the logo on the video preview to reposition it; "
        "use the corner handles to resize. The X button deletes the logo."
    )
    gui.logo_inspector_tip_label.setObjectName("helperLabel")
    gui.logo_inspector_tip_label.setWordWrap(True)
    logo_layout.addWidget(gui.logo_inspector_tip_label)
    logo_layout.addStretch(1)

    # --- Mask Track Inspector ---
    # The mask is a rectangular region that changes the colour of the
    # underlying video. The user positions / resizes the region via
    # the draggable overlay (move = drag middle, resize = drag
    # corners, delete = X button). The inspector only exposes the
    # colour + opacity; position, size, and mode are not configurable
    # here. The mask is only applied to the video while the player
    # is playing (it is cleared on pause / stop).
    mask_inspector_card = QFrame()
    mask_inspector_card.setObjectName("statusCard")
    mask_inspector_card.setMinimumWidth(400)
    mask_inspector_card.setMaximumWidth(560)
    gui.mask_inspector_card = mask_inspector_card
    mask_layout = QVBoxLayout(mask_inspector_card)
    mask_layout.setContentsMargins(12, 10, 12, 10)
    mask_layout.setSpacing(6)

    mask_title = QLabel("M1 Mask")
    mask_title.setObjectName("statusHeadline")
    mask_layout.addWidget(mask_title)
    gui.mask_inspector_summary_label = QLabel(
        "Drag the mask on the video to move it. Drag a corner to "
        "resize. The X button deletes the mask. The mask is applied "
        "while the video is playing."
    )
    gui.mask_inspector_summary_label.setObjectName("helperLabel")
    gui.mask_inspector_summary_label.setWordWrap(True)
    mask_layout.addWidget(gui.mask_inspector_summary_label)
    _add_layer_timing_controls(mask_layout, "mask")

    # --- Colour ---
    color_row = QHBoxLayout()
    color_label = QLabel("Colour")
    color_label.setObjectName("sectionTitle")
    color_label.setFixedWidth(90)
    color_row.addWidget(color_label)
    gui.mask_inspector_color_btn = QPushButton("#000000")
    gui.mask_inspector_color_btn.setFixedWidth(110)
    gui.mask_inspector_color_btn.setToolTip(
        "Pick the colour that the mask will paint over the selected "
        "region while the video is playing."
    )
    color_row.addWidget(gui.mask_inspector_color_btn)
    color_row.addStretch(1)
    mask_layout.addLayout(color_row)

    # --- Opacity ---
    m_opacity_row = QHBoxLayout()
    m_opacity_label = QLabel("Opacity")
    m_opacity_label.setObjectName("sectionTitle")
    m_opacity_label.setFixedWidth(90)
    m_opacity_row.addWidget(m_opacity_label)
    gui.mask_inspector_opacity_slider = QSlider(Qt.Horizontal)
    gui.mask_inspector_opacity_slider.setRange(0, 100)
    gui.mask_inspector_opacity_slider.setValue(100)
    gui.mask_inspector_opacity_slider.setToolTip(
        "Mask transparency (0% invisible, 100% fully visible)."
    )
    m_opacity_row.addWidget(gui.mask_inspector_opacity_slider, 1)
    gui.mask_inspector_opacity_value_label = QLabel("100%")
    gui.mask_inspector_opacity_value_label.setFixedWidth(40)
    m_opacity_row.addWidget(gui.mask_inspector_opacity_value_label)
    mask_layout.addLayout(m_opacity_row)

    gui.mask_inspector_tip_label = QLabel(
        "Tip: position and resize the mask on the video preview. The "
        "mask effect is only applied while the player is playing; it "
        "is cleared on pause / stop."
    )
    gui.mask_inspector_tip_label.setObjectName("helperLabel")
    gui.mask_inspector_tip_label.setWordWrap(True)
    mask_layout.addWidget(gui.mask_inspector_tip_label)
    mask_layout.addStretch(1)

    # --- Text Layer Inspector ---
    text_inspector_card = QFrame()
    text_inspector_card.setObjectName("statusCard")
    text_inspector_card.setMinimumWidth(400)
    text_inspector_card.setMaximumWidth(560)
    gui.text_inspector_card = text_inspector_card
    text_layout = QVBoxLayout(text_inspector_card)
    text_layout.setContentsMargins(12, 10, 12, 10)
    text_layout.setSpacing(8)
    text_title = QLabel("Text Layer")
    text_title.setObjectName("statusHeadline")
    text_layout.addWidget(text_title)
    _add_layer_timing_controls(text_layout, "text")
    gui.text_inspector_content = QTextEdit()
    gui.text_inspector_content.setPlaceholderText("Enter text")
    gui.text_inspector_content.setFixedHeight(82)
    text_layout.addWidget(gui.text_inspector_content)
    font_row = QHBoxLayout()
    font_row.addWidget(QLabel("Font"))
    gui.text_inspector_font_combo = QComboBox()
    gui.text_inspector_font_combo.addItems(["Montserrat", "Roboto", "Inter", "Poppins", "Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman"])
    font_row.addWidget(gui.text_inspector_font_combo, 1)
    text_layout.addLayout(font_row)
    size_row = QHBoxLayout()
    size_row.addWidget(QLabel("Font size"))
    gui.text_inspector_size_combo = QComboBox()
    # Keep the same 60 px base used by subtitle-compatible Text layers, but
    # expose smaller presets for captions and compact labels.
    for percent in (25, 35, 50, 75, 100, 125, 150):
        gui.text_inspector_size_combo.addItem(f"{percent}%", percent)
    gui.text_inspector_size_combo.setCurrentIndex(4)
    gui.text_inspector_size_combo.setToolTip("Matches the subtitle font-size presets (60 px at 100%).")
    size_row.addWidget(gui.text_inspector_size_combo, 1)
    text_layout.addLayout(size_row)
    color_row = QHBoxLayout()
    color_row.addWidget(QLabel("Text color"))
    gui.text_inspector_color_btn = QPushButton("#FFFFFF")
    gui.text_inspector_color_btn.setFixedWidth(110)
    color_row.addWidget(gui.text_inspector_color_btn)
    color_row.addStretch(1)
    text_layout.addLayout(color_row)
    background_row = QHBoxLayout()
    background_row.addWidget(QLabel("Background"))
    gui.text_inspector_background_btn = QPushButton("None")
    gui.text_inspector_background_btn.setFixedWidth(110)
    background_row.addWidget(gui.text_inspector_background_btn)
    background_row.addStretch(1)
    text_layout.addLayout(background_row)
    background_opacity_row = QHBoxLayout()
    background_opacity_row.addWidget(QLabel("Background opacity"))
    gui.text_inspector_background_opacity_slider = QSlider(Qt.Horizontal)
    gui.text_inspector_background_opacity_slider.setRange(0, 100)
    gui.text_inspector_background_opacity_slider.setValue(50)
    gui.text_inspector_background_opacity_slider.setToolTip("Adjust the Text layer background transparency.")
    background_opacity_row.addWidget(gui.text_inspector_background_opacity_slider, 1)
    gui.text_inspector_background_opacity_value = QLabel("50%")
    gui.text_inspector_background_opacity_value.setMinimumWidth(42)
    gui.text_inspector_background_opacity_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    background_opacity_row.addWidget(gui.text_inspector_background_opacity_value)
    text_layout.addLayout(background_opacity_row)
    gui.text_inspector_summary_label = QLabel("Click or drag the selected text on the video preview to position it.")
    gui.text_inspector_summary_label.setObjectName("helperLabel")
    gui.text_inspector_summary_label.setWordWrap(True)
    text_layout.addWidget(gui.text_inspector_summary_label)
    text_layout.addStretch(1)

    # --- Default / empty inspector ---
    default_inspector_card = QFrame()
    default_inspector_card.setObjectName("statusCard")
    default_inspector_card.setMinimumWidth(400)
    default_inspector_card.setMaximumWidth(560)
    gui.default_inspector_card = default_inspector_card
    default_layout = QVBoxLayout(default_inspector_card)
    default_layout.setContentsMargins(14, 14, 14, 14)
    default_layout.setSpacing(10)
    default_title = QLabel("Track Inspector")
    default_title.setObjectName("statusHeadline")
    default_layout.addWidget(default_title)
    gui.default_inspector_summary_label = QLabel(
        "Click a layer on a track to view its settings."
    )
    gui.default_inspector_summary_label.setObjectName("helperLabel")
    gui.default_inspector_summary_label.setWordWrap(True)
    default_layout.addWidget(gui.default_inspector_summary_label)
    default_layout.addStretch(1)

    # --- Video inspector (V1 Video track) ---
    video_inspector_card = QFrame()
    video_inspector_card.setObjectName("statusCard")
    video_inspector_card.setMinimumWidth(400)
    video_inspector_card.setMaximumWidth(560)
    gui.video_inspector_card = video_inspector_card
    video_layout = QVBoxLayout(video_inspector_card)
    video_layout.setContentsMargins(14, 14, 14, 14)
    video_layout.setSpacing(10)
    video_title = QLabel("V1 Video")
    video_title.setObjectName("statusHeadline")
    video_layout.addWidget(video_title)
    gui.video_inspector_summary_label = QLabel(
        "Adjust the look of the original video. The filter is applied to the "
        "export and the live preview."
    )
    gui.video_inspector_summary_label.setObjectName("helperLabel")
    gui.video_inspector_summary_label.setWordWrap(True)
    video_layout.addWidget(gui.video_inspector_summary_label)

    # --- Preset ---
    preset_row = QHBoxLayout()
    preset_label = QLabel("Preset")
    preset_label.setObjectName("sectionTitle")
    preset_row.addWidget(preset_label)
    gui.video_inspector_preset_combo = QComboBox()
    gui.video_inspector_preset_combo.setToolTip("Choose a video filter preset.")
    preset_row.addWidget(gui.video_inspector_preset_combo, 1)
    video_layout.addLayout(preset_row)

    # --- Intensity ---
    intensity_row = QHBoxLayout()
    intensity_label = QLabel("Intensity")
    intensity_label.setObjectName("sectionTitle")
    intensity_row.addWidget(intensity_label)
    gui.video_inspector_intensity_slider = QSlider(Qt.Horizontal)
    gui.video_inspector_intensity_slider.setRange(0, 100)
    gui.video_inspector_intensity_slider.setToolTip(
        "How strongly the preset is applied (0 = off, 100 = full)."
    )
    intensity_row.addWidget(gui.video_inspector_intensity_slider, 1)
    gui.video_inspector_intensity_value_label = QLabel("0")
    gui.video_inspector_intensity_value_label.setFixedWidth(28)
    intensity_row.addWidget(gui.video_inspector_intensity_value_label)
    video_layout.addLayout(intensity_row)

    # --- Adjust sliders (brightness/contrast/saturation/...) ---
    gui.video_inspector_adjust_sliders = {}
    adjust_fields = [
        ("brightness", "Brightness"),
        ("contrast", "Contrast"),
        ("saturation", "Saturation"),
        ("temperature", "Temperature"),
        ("gamma", "Gamma"),
        ("hue", "Hue"),
    ]
    for field_key, field_label in adjust_fields:
        row = QHBoxLayout()
        lbl = QLabel(field_label)
        lbl.setObjectName("sectionTitle")
        lbl.setFixedWidth(90)
        row.addWidget(lbl)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(-100, 100)
        slider.setValue(0)
        row.addWidget(slider, 1)
        value_lbl = QLabel("0")
        value_lbl.setFixedWidth(28)
        row.addWidget(value_lbl)
        gui.video_inspector_adjust_sliders[field_key] = (slider, value_lbl)
        video_layout.addLayout(row)

    # --- Status + Reset ---
    gui.video_inspector_status_label = QLabel("")
    gui.video_inspector_status_label.setObjectName("helperLabel")
    gui.video_inspector_status_label.setWordWrap(True)
    video_layout.addWidget(gui.video_inspector_status_label)
    action_row = QHBoxLayout()
    gui.video_inspector_reset_btn = QPushButton("Reset")
    gui.video_inspector_reset_btn.setToolTip("Reset all video filters to their defaults.")
    action_row.addWidget(gui.video_inspector_reset_btn)
    action_row.addStretch(1)
    video_layout.addLayout(action_row)
    video_layout.addStretch(1)

    # Wire the stack. Each card is wrapped in a QScrollArea so tall
    # content (audio sliders, blur settings, etc.) can scroll instead
    # of being clipped by the fixed shell width.
    def _wrap_in_scroll(card_widget):
        # Wrap each card in a QScrollArea so the inspector content
        # is scrollable when it exceeds the available height. The
        # subtitle card's own internal scroll area is flattened
        # (see below) to avoid nested scroll layers.
        scroll = QScrollArea()
        scroll.setObjectName("inspectorCardScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(card_widget)
        return scroll

    gui.inspector_stack.addWidget(_wrap_in_scroll(inspector_card))        # 0: subtitle
    gui.inspector_stack.addWidget(_wrap_in_scroll(audio_inspector_card))  # 1: audio
    gui.inspector_stack.addWidget(_wrap_in_scroll(blur_inspector_card))   # 2: blur
    gui.inspector_stack.addWidget(_wrap_in_scroll(video_inspector_card))  # 3: video (V1)
    gui.inspector_stack.addWidget(_wrap_in_scroll(default_inspector_card))  # 4: default
    gui.inspector_stack.addWidget(_wrap_in_scroll(logo_inspector_card))   # 5: logo (L1)
    gui.inspector_stack.addWidget(_wrap_in_scroll(mask_inspector_card))   # 6: mask (M1)
    gui.inspector_stack.addWidget(_wrap_in_scroll(text_inspector_card))   # 7: text (T1)
    gui.subtitle_inspector_card = inspector_card
    gui.inspector_stack.setCurrentIndex(4)

    # Style the scroll bar so it matches the dark theme
    gui.inspector_stack.setStyleSheet(
        "QScrollArea#inspectorCardScroll { background: transparent; border: none; }"
        "QScrollBar:vertical { border: none; background: #142030; width: 10px; margin: 0; }"
        "QScrollBar::handle:vertical { background: #35506f; min-height: 30px; border-radius: 5px; }"
        "QScrollBar::handle:vertical:hover { background: #416287; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    )

    # Collapse handle strip removed - the track inspector is always
    # expanded. Previously this contained a toggle button and a pin
    # icon; both are now hidden and the empty strip has been removed.
    inspector_shell_layout.addWidget(gui.inspector_stack, 1)

    gui.subtitle_inspector_shell = inspector_shell
    # Initial width; the actual value is recomputed by
    # `_sync_subtitle_inspector_shell_width` based on the active card.
    inspector_shell.setMinimumWidth(34)
    inspector_shell.setMaximumWidth(800)

    workspace_row = QHBoxLayout()
    workspace_row.setSpacing(10)
    # Inspector takes more horizontal space than the preview so the
    # audio/subtitle controls and segment editor are comfortable.
    workspace_row.addWidget(preview_card, 2)
    workspace_row.addWidget(inspector_shell, 3)

    # Keep Preview + Inspector and Timeline independently resizable.  The
    # previous 3:5 layout gave a 16:9 source only enough height to occupy a
    # small central portion of a wide preview panel.  A 45:55 default gives
    # the preview about 20% more vertical room while leaving the timeline
    # comfortably usable.  MpvVideoView recalculates its canvas and overlays
    # on every resize, so this does not alter any source/canvas coordinates.
    workspace_widget = QWidget()
    workspace_widget.setObjectName("previewWorkspace")
    workspace_widget.setLayout(workspace_row)
    gui.preview_workspace_widget = workspace_widget
    gui.preview_workspace_layout = workspace_row
    gui.timeline_card = timeline_card
    # 270 px preview + transport row + card margins/spacing.  This prevents
    # the native MPV surface from extending over the transport controls when
    # the splitter is dragged upward on a smaller window.
    workspace_widget.setMinimumHeight(350)
    # Keep the original practical minimum for the editor timeline.
    timeline_card.setMinimumHeight(360)
    gui._responsive_workspace_minimum_height = 350
    gui._responsive_timeline_minimum_height = 360

    gui.preview_timeline_splitter = QSplitter(Qt.Vertical)
    gui.preview_timeline_splitter.setObjectName("previewTimelineSplitter")
    gui.preview_timeline_splitter.setChildrenCollapsible(False)
    gui.preview_timeline_splitter.setOpaqueResize(True)
    gui.preview_timeline_splitter.setHandleWidth(7)
    gui.preview_timeline_splitter.setStyleSheet(
        "QSplitter::handle { background: #1b2a3d; margin: 2px 0; }"
        "QSplitter::handle:hover { background: #3a6289; }"
    )
    gui.preview_timeline_splitter.addWidget(workspace_widget)
    gui.preview_timeline_splitter.addWidget(timeline_card)
    gui.preview_timeline_splitter.handle(1).setEnabled(True)
    gui.preview_timeline_splitter.handle(1).setCursor(Qt.SplitVCursor)
    gui.preview_timeline_splitter.setStretchFactor(0, 45)
    gui.preview_timeline_splitter.setStretchFactor(1, 55)

    # Do not permit an extreme Preview expansion to clip the Timeline card
    # below a usable viewport. The normal 45/55 layout remains unchanged;
    # this guard applies only at the lower end of the splitter range.
    gui._constraining_preview_timeline_splitter = False

    def _keep_timeline_accessible(_pos, _index):
        splitter = gui.preview_timeline_splitter
        if getattr(gui, "_constraining_preview_timeline_splitter", False):
            return
        sizes = splitter.sizes()
        if len(sizes) != 2:
            return
        min_timeline_height = int(
            getattr(gui, "_responsive_timeline_minimum_height", 360) or 360
        )
        if sizes[1] >= min_timeline_height:
            return
        available = sum(sizes)
        if available <= min_timeline_height:
            return
        gui._constraining_preview_timeline_splitter = True
        try:
            splitter.setSizes([max(1, available - min_timeline_height), min_timeline_height])
        finally:
            gui._constraining_preview_timeline_splitter = False

    gui.preview_timeline_splitter.splitterMoved.connect(_keep_timeline_accessible)
    right_layout.addWidget(gui.preview_timeline_splitter, 1)

    def _set_default_preview_timeline_sizes():
        splitter = gui.preview_timeline_splitter
        available = sum(splitter.sizes())
        if available > 0:
            preview_height = int(round(available * 0.45))
            splitter.setSizes([preview_height, max(1, available - preview_height)])

    # MainWindow calls this from its first show event, when the splitter has
    # real dimensions but before the editor's first visible paint.
    gui._set_default_preview_timeline_sizes = _set_default_preview_timeline_sizes
    return right_panel
