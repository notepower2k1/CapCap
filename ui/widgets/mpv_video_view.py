from __future__ import annotations

import os
import re
import math

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBitmap, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPalette, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QLabel, QWidget


class _SubtitleOverlayWidget(QWidget):
    """A real-time overlay widget for MpvVideoView."""
    positionDragStarted = Signal()
    positionDragFinished = Signal(int, int)

    # Match the ASS export row spacing in app.video_processor.
    LINE_HEIGHT_FACTOR = 1.40
    # Space for glyph ascenders/descenders and the subtitle outline. The
    # positioning code lets this invisible safety margin extend past the
    # canvas edge, so visible text can still sit flush with the video.
    VERTICAL_PADDING = 6

    def __init__(self, parent=None):
        window_flags = Qt.Widget if parent is not None else (
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        super().__init__(parent, window_flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(False)
        self.current_text = ""
        self.current_lines: list[str] = []
        self.render_text = True
        self.font_name = "Segoe UI"
        self.font_size = 20
        self.font_color = QColor(255, 255, 255)
        self.outline_width = 2
        self.outline_color = QColor(0, 0, 0, 220)
        self.shadow_color = QColor(0, 0, 0, 180)
        self.shadow_depth = 0.0
        self.bold = True
        self.highlight_color = QColor("#FFD400")
        self.highlight_phrases: list[str] = []
        self.karaoke_word_index = -1
        self.auto_keyword_highlight = False
        self.animation_style = "static"
        self.animation_progress = 1.0
        self.alignment = "Bottom Center"
        self.background_box = False
        self.background_color = QColor(0, 0, 0, 170)
        self.single_line = False
        self.x_offset = 0
        self.bottom_offset = 30
        self.custom_position_enabled = False
        self.custom_x_percent = 50
        self.custom_y_percent = 86
        self.W, self.H = 640, 48
        self._drag_active = False
        self._editable = False
        self._suppressed = False
        self._drag_grab_offset = QPointF()
        self._target_view = parent
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setCursor(Qt.ArrowCursor)
        self.hide()

    def attach_to_view(self, view: QWidget):
        self._target_view = view

    def set_editable(self, editable: bool):
        """Allow moving this subtitle layer only for a selected TS segment."""
        self._editable = bool(editable)
        if not self._editable:
            self._drag_active = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._editable)
        self.setCursor(Qt.OpenHandCursor if self._editable else Qt.ArrowCursor)

    def set_suppressed(self, suppressed: bool):
        """Temporarily keep this top-level overlay below modal dialogs."""
        self._suppressed = bool(suppressed)
        if self._suppressed:
            super().hide()

    def show(self):
        # This is a top-level tool window so it can cover MPV's native
        # surface. Never let it be shown above an active modal dialog.
        modal = QApplication.activeModalWidget()
        if self._suppressed or (modal is not None and modal.isVisible()):
            return
        super().show()

    def is_top_level_overlay(self) -> bool:
        return self.parentWidget() is None

    def mousePressEvent(self, event):
        if not self._editable or event.button() != Qt.LeftButton:
            event.ignore()
            return
        self._drag_active = True
        self._drag_grab_offset = QPointF(event.position())
        self.setCursor(Qt.ClosedHandCursor)
        self.positionDragStarted.emit()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._drag_active:
            event.ignore()
            return
        view = self._target_view or self.parentWidget()
        if view is None or not hasattr(view, "get_preview_canvas_rect"):
            event.ignore()
            return
        canvas = view.get_preview_canvas_rect()
        if canvas.width() <= 0 or canvas.height() <= 0:
            event.ignore()
            return
        # The subtitle is a top-level overlay above MPV, not a child of the
        # preview widget. Convert through global coordinates to avoid Qt's
        # parent-hierarchy warning and get the correct canvas-relative point.
        pointer = view.mapFromGlobal(self.mapToGlobal(event.position().toPoint()))
        center_x = pointer.x() - self._drag_grab_offset.x() + self.width() / 2.0
        center_y = pointer.y() - self._drag_grab_offset.y() + self.height() / 2.0
        # The drag position represents the subtitle's anchor/centre, not the
        # outer edge of its (often wide) wrapping widget.  Constraining by
        # half the widget width limited wide subtitles to a narrow strip in
        # the middle of portrait previews.  Keep the anchor anywhere on the
        # full video canvas and let positioning use that same normalized
        # centre for preview and export.
        center_x = max(canvas.left(), min(center_x, canvas.right()))
        center_y = max(canvas.top(), min(center_y, canvas.bottom()))
        x_percent = int(round(max(0.0, min(100.0, (center_x - canvas.left()) * 100.0 / canvas.width()))))
        y_percent = int(round(max(0.0, min(100.0, (center_y - canvas.top()) * 100.0 / canvas.height()))))
        self.custom_position_enabled = True
        self.custom_x_percent = x_percent
        self.custom_y_percent = y_percent
        view.reposition_subtitle()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_active and event.button() == Qt.LeftButton:
            self._drag_active = False
            self.setCursor(Qt.OpenHandCursor)
            self.positionDragFinished.emit(self.custom_x_percent, self.custom_y_percent)
            event.accept()
            return
        event.ignore()

    def sync_to_view(self):
        if not self.is_top_level_overlay() or self._target_view is None:
            return
        # Position is set by MpvVideoView.reposition_subtitle(). This keeps
        # a top-level overlay above MPV's native child window.
        self.raise_()

    def set_text(self, text):
        # Keep explicit subtitle line breaks as individual rows. ASS renders
        # each row independently, including its background box.
        new_lines = [line for line in str(text or "").splitlines() if line] or ([] if not text else [str(text)])
        if self.current_lines != new_lines or self.current_text != text:
            self.current_text = text
            self.current_lines = new_lines
            self._update_height()
            self.update()

    def set_lines(self, lines):
        """Set multiple subtitle lines (e.g. when two segments overlap).
        Each line is drawn on its own row, stacked vertically.
        """
        cleaned = [str(line or "").strip() for line in (lines or []) if str(line or "").strip()]
        joined = "\n".join(cleaned)
        if self.current_lines != cleaned or self.current_text != joined:
            self.current_lines = cleaned
            self.current_text = joined
            self._update_height()
            self.update()

    def _update_height(self):
        new_h = max(24, sum(self._line_layout_heights()) + self.VERTICAL_PADDING * 2)
        if new_h != self.H:
            self.H = new_h
            self.update()

    def set_text_rendering(self, enabled: bool):
        """Keep this widget as a draggable hit target without painting text."""
        enabled = bool(enabled)
        # Rendering updates happen continuously during playback. They must
        # not change whether the layer captures input; that is controlled
        # solely by the selected timeline segment.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._editable)
        if self.render_text != enabled:
            self.render_text = enabled
            self.update()

    def _line_layout_heights(self) -> list[int]:
        """Return the rendered height of each subtitle entry after wrapping."""
        font = QFont(self.font_name)
        font.setPixelSize(max(1, int(self.font_size)))
        font.setBold(self.bold)
        metrics = QFontMetrics(font)
        fallback = max(24, int(self.font_size * self.LINE_HEIGHT_FACTOR))
        flags = int(Qt.AlignCenter | Qt.TextWordWrap)
        width = max(1, int(self.W))
        entries = self.current_lines or [""]
        heights = []
        for text in entries:
            bounds = metrics.boundingRect(QRect(0, 0, width, 100000), flags, text)
            heights.append(max(fallback, int(bounds.height())))
        return heights

    def set_style(self, font_name, font_size, font_color, outline_width=2, outline_color=None,
                  background_box=None, background_color=None, single_line=None, bold=None,
                  shadow_color=None, shadow_depth=None):
        self.font_name = font_name
        self.font_size = font_size
        self.font_color = font_color
        self.outline_width = max(0, float(outline_width))
        self.outline_color = outline_color or QColor(0, 0, 0, 220)
        if bold is not None:
            self.bold = bool(bold)
        if shadow_color is not None:
            self.shadow_color = QColor(shadow_color)
        if shadow_depth is not None:
            self.shadow_depth = max(0.0, float(shadow_depth))
        if background_box is not None:
            self.background_box = bool(background_box)
        if background_color is not None:
            self.background_color = background_color
        if single_line is not None:
            self.single_line = bool(single_line)
        self.H = max(24, sum(self._line_layout_heights()) + self.VERTICAL_PADDING * 2)
        self.update()

    def set_effects(self, *, highlight_color=None, highlight_phrases=None,
                    karaoke_word_index=-1, auto_keyword_highlight=False,
                    animation_style="Static", animation_progress=1.0):
        """Apply cue-specific TikTok/highlight effects to the live layer."""
        next_color = QColor(highlight_color or "#FFD400")
        next_phrases = [str(value).strip() for value in (highlight_phrases or []) if str(value).strip()]
        next_word_index = int(karaoke_word_index)
        next_auto_highlight = bool(auto_keyword_highlight)
        next_animation = str(animation_style or "Static").strip().lower()
        next_progress = max(0.0, min(1.0, float(animation_progress)))
        changed = (
            self.highlight_color != next_color
            or self.highlight_phrases != next_phrases
            or self.karaoke_word_index != next_word_index
            or self.auto_keyword_highlight != next_auto_highlight
            or self.animation_style != next_animation
            or abs(self.animation_progress - next_progress) > 0.0001
        )
        if not changed:
            return
        self.highlight_color = next_color
        self.highlight_phrases = next_phrases
        self.karaoke_word_index = next_word_index
        self.auto_keyword_highlight = next_auto_highlight
        self.animation_style = next_animation
        self.animation_progress = next_progress
        self.update()

    def _visible_lines(self) -> list[str]:
        if self.animation_style != "typewriter":
            return self.current_lines
        remaining = int(math.ceil(sum(len(line) for line in self.current_lines) * self.animation_progress))
        visible = []
        for line in self.current_lines:
            visible.append(line[:max(0, remaining)])
            remaining -= len(line)
        return visible

    def _highlight_spans(self, text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        lowered = text.lower()
        for phrase in self.highlight_phrases:
            needle = phrase.lower()
            start = 0
            while needle:
                index = lowered.find(needle, start)
                if index < 0:
                    break
                spans.append((index, index + len(needle)))
                start = index + len(needle)
        words = list(re.finditer(r"\S+", text))
        if self.karaoke_word_index >= 0 and words:
            word = words[min(self.karaoke_word_index, len(words) - 1)]
            spans.append((word.start(), word.end()))
        elif self.auto_keyword_highlight and not spans and words:
            for word in sorted(words, key=lambda match: len(match.group()), reverse=True)[:2]:
                spans.append((word.start(), word.end()))
        return spans

    def _draw_colored_text(self, painter, text: str, line_rect: QRectF, spans):
        """Draw wrapped tokens with TikTok keyword/karaoke colours."""
        metrics = QFontMetrics(painter.font())
        tokens = list(re.finditer(r"\S+\s*", text))
        rows, row_widths = [[]], [0.0]
        for token in tokens:
            token_width = float(metrics.horizontalAdvance(token.group()))
            if rows[-1] and row_widths[-1] + token_width > line_rect.width():
                rows.append([])
                row_widths.append(0.0)
            rows[-1].append((token, token_width))
            row_widths[-1] += token_width
        line_height = max(metrics.height(), int(self.font_size * self.LINE_HEIGHT_FACTOR))
        baseline = line_rect.y() + max(metrics.ascent(), (line_rect.height() - len(rows) * line_height) / 2.0 + metrics.ascent())
        for row, row_width in zip(rows, row_widths):
            x = line_rect.x() + (line_rect.width() - row_width) / 2.0
            for token, token_width in row:
                token_text = token.group()
                highlighted = any(start < token.end() and end > token.start() for start, end in spans)
                if self.shadow_depth > 0:
                    painter.setPen(self.shadow_color)
                    painter.drawText(QPointF(x + self.shadow_depth, baseline + self.shadow_depth), token_text)
                if self.outline_width > 0:
                    w = max(1.0, self.outline_width)
                    painter.setPen(self.outline_color)
                    for dx, dy in ((-w, 0), (w, 0), (0, -w), (0, w), (-w*.7, -w*.7), (w*.7, -w*.7), (-w*.7, w*.7), (w*.7, w*.7)):
                        painter.drawText(QPointF(x + dx, baseline + dy), token_text)
                painter.setPen(self.highlight_color if highlighted else self.font_color)
                painter.drawText(QPointF(x, baseline), token_text)
                x += token_width
            baseline += line_height

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
            self._update_height()
            self.update()

    def paintEvent(self, event):
        if not self.render_text:
            # A fully transparent layered window can become click-through on
            # Windows. Paint an imperceptible alpha pixel across the target so
            # the subtitle remains draggable above MPV's native surface.
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
            painter.end()
            return
        if not self.current_text and not self.current_lines and not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, float(self.W), float(self.H))

        if not self.current_lines:
            return

        visible_lines = self._visible_lines()
        progress = self.animation_progress
        if self.animation_style == "fade in":
            painter.setOpacity(progress)
        elif self.animation_style == "fade out":
            painter.setOpacity(1.0 - progress)
        elif self.animation_style == "pop in":
            scale = 0.72 + 0.28 * progress
            painter.translate(rect.center())
            painter.scale(scale, scale)
            painter.translate(-rect.center())
        elif self.animation_style == "pulse":
            scale = 1.0 + 0.08 * math.sin(progress * math.pi)
            painter.translate(rect.center())
            painter.scale(scale, scale)
            painter.translate(-rect.center())
        elif self.animation_style == "slide up":
            painter.translate(0, (1.0 - progress) * max(8.0, self.font_size * 0.8))

        painter.setPen(self.font_color)
        font = QFont(self.font_name)
        font.setPixelSize(max(1, int(self.font_size)))
        font.setBold(self.bold)
        painter.setFont(font)
        wrap_flags = Qt.AlignCenter | Qt.TextWordWrap
        line_heights = self._line_layout_heights()
        has_colored_effects = any(self._highlight_spans(line) for line in visible_lines)

        line_rects: list[QRectF] = []
        if len(visible_lines) == 1:
            line_rects = [QRectF(
                rect.x(), self.VERTICAL_PADDING, rect.width(), line_heights[0],
            )]
        else:
            y = float(self.VERTICAL_PADDING)
            for line_height in line_heights:
                line_rects.append(QRectF(rect.x(), y, rect.width(), line_height))
                y += line_height

        if self.background_box:
            painter.setPen(Qt.NoPen)
            box_color = QColor(self.background_color)
            if self.animation_style == "background appear":
                box_color.setAlpha(int(round(box_color.alpha() * progress)))
            painter.setBrush(box_color)
            metrics = painter.fontMetrics()
            # libass uses the BorderStyle=3 outline size as the box padding.
            # The old fixed 18px padding made the live box much wider than
            # the exported result.
            box_pad_x = max(2, int(round(max(2.0, self.outline_width))))
            box_pad_y = max(1, int(round(max(1.0, self.outline_width * 0.5))))
            max_width = max((metrics.boundingRect(line).width() for line in visible_lines), default=0)
            if max_width and line_rects:
                block_top = line_rects[0].top()
                block_bottom = line_rects[-1].bottom()
                text_rect = QRect(
                    int(round(rect.center().x() - max_width / 2.0)),
                    int(round(block_top)),
                    max_width,
                    max(1, int(round(block_bottom - block_top))),
                ).adjusted(-box_pad_x, -box_pad_y, box_pad_x, box_pad_y)
                painter.drawRect(QRectF(text_rect))

        if self.shadow_depth > 0 and not has_colored_effects:
            painter.setPen(self.shadow_color)
            for line_text, line_rect in zip(visible_lines, line_rects):
                painter.drawText(line_rect.translated(self.shadow_depth, self.shadow_depth), int(wrap_flags), line_text)

        if self.outline_width > 0 and not has_colored_effects:
            w = max(1.0, self.outline_width)
            offsets = [
                (-w, 0), (w, 0), (0, -w), (0, w),
                (-w*0.7, -w*0.7), (w*0.7, -w*0.7),
                (-w*0.7, w*0.7), (w*0.7, w*0.7)
            ]
            painter.setPen(self.outline_color)
            for line_text, line_rect in zip(visible_lines, line_rects):
                for dx, dy in offsets:
                    painter.drawText(line_rect.translated(dx, dy), int(wrap_flags), line_text)

        for line_text, line_rect in zip(visible_lines, line_rects):
            spans = self._highlight_spans(line_text)
            if spans:
                self._draw_colored_text(painter, line_text, line_rect, spans)
            else:
                painter.setPen(self.font_color)
                painter.drawText(line_rect, int(wrap_flags), line_text)




class _BlurRegionOverlayWindow(QWidget):
    HANDLE_SIZE = 12
    MIN_WIDTH = HANDLE_SIZE * 2
    MIN_HEIGHT = HANDLE_SIZE * 2
    BLUR_COLOR = QColor(110, 231, 214)
    # Kept as a compatibility alias for region-placement code. Blur is a
    # single effect, so every entry deliberately resolves to one colour.
    COLORS = [BLUR_COLOR] * 5

    def __init__(self, on_region_changed=None, on_edit_finished=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self._regions: list[QRectF] = []
        self._active_index = -1
        self._drag_index = -1
        self._drag_mode = ""
        self._drag_offset = QPointF()
        self._rect_on_press = QRectF()
        self._press_pos = QPointF()
        self._editable = False
        self._target_view = None
        self._main_window = None
        self._suspended = False
        self._on_region_changed = on_region_changed
        self._on_edit_finished = on_edit_finished
        self.hide()

    def attach_to_view(self, view: QWidget):
        self._target_view = view
        if view and view.window():
            new_win = view.window()
            if self._main_window is not None and self._main_window is not new_win:
                try:
                    self._main_window.removeEventFilter(self)
                except Exception:
                    pass
            self._main_window = new_win
            try:
                self._main_window.removeEventFilter(self)
                self._main_window.installEventFilter(self)
            except Exception:
                pass
        self.sync_to_view()

    def set_editable(self, editable: bool):
        self._editable = bool(editable)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._editable)
        self.setCursor(Qt.OpenHandCursor if self._editable else Qt.ArrowCursor)
        if self._editable:
            # Only show the overlay if there are regions to edit. Do NOT
            # auto-add a region here - that previously caused a phantom
            # blur rectangle to appear on project reopen even when the
            # user had deleted all blurs. The user must press "+" to add
            # a region explicitly.
            if self._regions:
                self.sync_to_view()
            else:
                self.hide()
        else:
            self.hide()
        self.update()

    def clear_region(self):
        self._drag_mode = ""
        self._drag_index = -1
        self._active_index = -1
        self._regions = []
        self._target_view = None
        if self._main_window is not None:
            try:
                self._main_window.removeEventFilter(self)
            except Exception:
                pass
            self._main_window = None
        self._suspended = False
        self.hide()
        self.update()

    def closeEvent(self, event):
        if self._main_window is not None:
            try:
                self._main_window.removeEventFilter(self)
            except Exception:
                pass
            self._main_window = None
        super().closeEvent(event)

    def has_region(self) -> bool:
        return bool(self._regions)

    def add_region(self, emit_change: bool = True):
        index = len(self._regions)
        offset = (index % len(self.COLORS)) * 0.055
        rect = QRectF(
            min(0.72, 0.25 + offset),
            min(0.72, 0.2 + offset),
            0.32,
            0.18,
        )
        self._regions.append(rect)
        self._active_index = len(self._regions) - 1
        if emit_change and callable(self._on_region_changed):
            self._on_region_changed()
        self.sync_to_view()
        self.update()

    def set_regions(self, regions):
        previous_active = self._active_index
        next_regions = []
        raw_regions = regions
        if isinstance(raw_regions, dict):
            raw_regions = [raw_regions]
        if isinstance(raw_regions, list):
            for region in raw_regions:
                if not isinstance(region, dict):
                    continue
                try:
                    x = max(0.0, min(1.0, float(region.get("x", 0.0))))
                    y = max(0.0, min(1.0, float(region.get("y", 0.0))))
                    width = max(0.0, min(1.0 - x, float(region.get("width", 0.0))))
                    height = max(0.0, min(1.0 - y, float(region.get("height", 0.0))))
                except (TypeError, ValueError):
                    continue
                if width > 0.0 and height > 0.0:
                    next_regions.append(QRectF(x, y, width, height))
        self._regions = next_regions
        self._active_index = (
            min(previous_active, len(self._regions) - 1)
            if previous_active >= 0 else len(self._regions) - 1
        )
        self._drag_index = -1
        self._drag_mode = ""
        self.sync_to_view()
        self.update()

    def sync_to_view(self):
        if (
            self._suspended
            or not self._target_view
            or not self._target_view.isVisible()
            or not self._regions
            or not self._editable
        ):
            self.hide()
            return
        top_left = self._target_view.mapToGlobal(QPoint(0, 0))
        self.setGeometry(QRect(top_left, self._target_view.size()))
        self.show()
        self.raise_()
        self.update()

    def _queue_sync_to_view(self):
        if not (self._editable and self._regions and self._target_view and self._target_view.isVisible()):
            return
        QTimer.singleShot(0, self.sync_to_view)
        QTimer.singleShot(120, self.sync_to_view)

    def hideEvent(self, event):
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return
        except Exception:
            pass
        if getattr(self, "mouseGrabber", None) and self.mouseGrabber() is self:
            try:
                self.releaseMouse()
            except Exception:
                pass
        try:
            super().hideEvent(event)
        except (RuntimeError, ReferenceError):
            pass

    def eventFilter(self, watched, event):
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return False
        except Exception:
            pass
        try:
            if self._main_window is not None and watched is self._main_window:
                if event.type() == QEvent.WindowDeactivate:
                    self._suspended = True
                    if self.isVisible():
                        self.hide()
                elif event.type() == QEvent.WindowActivate:
                    self._suspended = False
                    if self._editable and self._regions and not self._has_active_popup():
                        self._queue_sync_to_view()
        except (RuntimeError, ReferenceError):
            return False
        return False

    def _has_active_popup(self):
        from PySide6.QtWidgets import QApplication
        for widget in QApplication.topLevelWidgets():
            if widget.isModal() and widget.isVisible():
                return True
        return False

    def _source_display_rect(self) -> QRectF:
        """Preview-space rectangle occupied by the uncropped source video."""
        if self._target_view is not None:
            rect = self._target_view.get_video_content_rect()
            if rect.width() > 0 and rect.height() > 0:
                return QRectF(rect)
        return QRectF(0, 0, float(self.width()), float(self.height()))

    def _visible_canvas_rect(self) -> QRectF:
        """The selected output canvas actually visible in the preview."""
        if self._target_view is not None:
            rect = self._target_view.get_preview_canvas_rect()
            if rect.width() > 0 and rect.height() > 0:
                return QRectF(rect)
        return QRectF(0, 0, float(self.width()), float(self.height()))

    def region_rect(self, index: int | None = None) -> QRectF:
        """Map a source-normalized region into the visible output canvas.

        The source rect can extend beyond the canvas in Fill mode.  Preview
        interaction must use only the visible portion, matching the export
        source-to-canvas transform and the Text layer's canvas bounds.
        """
        if index is None:
            index = self._active_index
        if index < 0 or index >= len(self._regions):
            return QRectF()
        content_rect = self._source_display_rect()
        width = max(1.0, float(content_rect.width()))
        height = max(1.0, float(content_rect.height()))
        normalized_rect = self._regions[index]
        mapped = QRectF(
            content_rect.x() + normalized_rect.x() * width,
            content_rect.y() + normalized_rect.y() * height,
            normalized_rect.width() * width,
            normalized_rect.height() * height,
        )
        return mapped.intersected(self._visible_canvas_rect())

    def _set_region_rect(self, rect: QRectF, index: int | None = None):
        if index is None:
            index = self._active_index
        if index < 0 or index >= len(self._regions):
            return
        content_rect = self._source_display_rect()
        visible_rect = self._visible_canvas_rect()
        width = max(1.0, float(content_rect.width()))
        height = max(1.0, float(content_rect.height()))
        # Stored geometry remains source-normalized, but editing is bounded
        # by the output canvas. In Fill mode the source display rect is wider
        # or taller than the canvas, so using it as the bound exposed areas
        # that export later crops away.
        bounds = visible_rect if visible_rect.width() > 0 and visible_rect.height() > 0 else content_rect
        bounded = QRectF(rect)
        min_width = min(float(self.MIN_WIDTH), max(1.0, bounds.width()))
        min_height = min(float(self.MIN_HEIGHT), max(1.0, bounds.height()))
        bounded.setWidth(max(min_width, bounded.width()))
        bounded.setHeight(max(min_height, bounded.height()))
        if bounded.width() > bounds.width():
            bounded.setWidth(bounds.width())
        if bounded.height() > bounds.height():
            bounded.setHeight(bounds.height())
        if bounded.left() < bounds.left():
            bounded.moveLeft(bounds.left())
        if bounded.top() < bounds.top():
            bounded.moveTop(bounds.top())
        if bounded.right() > bounds.right():
            bounded.moveRight(bounds.right())
        if bounded.bottom() > bounds.bottom():
            bounded.moveBottom(bounds.bottom())

        self._regions[index] = QRectF(
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

    def _hit_test(self, pos: QPointF) -> tuple[int, str]:
        index = self._active_index
        if index < 0 or index >= len(self._regions):
            return -1, ""
        rect = self.region_rect(index)
        if rect.width() <= 0 or rect.height() <= 0:
            return -1, ""
        for handle_name, handle_rect in self._handle_rects(rect).items():
            if handle_rect.contains(pos):
                return index, handle_name
        if rect.contains(pos):
            return index, "move"
        return -1, ""

    def set_active_index(self, index: int):
        if self._regions:
            self._active_index = max(0, min(int(index), len(self._regions) - 1))
            self.update()

    def mousePressEvent(self, event):
        if not self._editable or event.button() != Qt.LeftButton:
            event.ignore()
            return
        pos = QPointF(event.position())
        hit_index, hit_mode = self._hit_test(pos)
        self._drag_index = hit_index
        self._active_index = hit_index
        self._drag_mode = hit_mode
        self._rect_on_press = QRectF(self.region_rect(hit_index))
        self._press_pos = QPointF(pos)
        if self._drag_mode == "move":
            self._drag_offset = pos - self._rect_on_press.topLeft()
            self.setCursor(Qt.ClosedHandCursor)
        elif self._drag_mode:
            self.setCursor(Qt.SizeAllCursor)
        if self._drag_mode:
            # Keep receiving the drag even when the pointer crosses a
            # narrow region edge or a native MPV child window boundary.
            self.grabMouse()
        event.accept()

    def mouseMoveEvent(self, event):
        pos = QPointF(event.position())
        if not self._editable:
            event.ignore()
            return

        if not self._drag_mode:
            hit_index, hit = self._hit_test(pos)
            if hit_index >= 0:
                self._active_index = hit_index
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
        self._set_region_rect(rect, self._drag_index)
        event.accept()

    def mouseReleaseEvent(self, event):
        finished_drag = bool(self._drag_mode)
        self._drag_mode = ""
        self._drag_index = -1
        if self.mouseGrabber() is self:
            self.releaseMouse()
        if self._editable:
            self.setCursor(Qt.OpenHandCursor)
        if finished_drag and callable(self._on_edit_finished):
            self._on_edit_finished()
        event.accept()

    def paintEvent(self, event):
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._target_view is not None:
            # Match the native MPV surface: Fill crops to the selected
            # canvas, so editable overlay chrome must not paint into the
            # surrounding matte/frame.
            painter.setClipRect(self._target_view.get_preview_canvas_rect())
        # This is a translucent top-level overlay. Explicitly clear its
        # backing surface before repainting; otherwise stale pixels from a
        # prior active region can remain as dark rectangles in inactive
        # regions after the timeline selection changes.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        for index, _region in enumerate(self._regions):
            rect = self.region_rect(index)
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            # B1 is a single effect type: unlike generic region tools,
            # every blur layer uses the same visual identity.
            color = self.BLUR_COLOR
            # Blur regions are outlines only. Any translucent fill blends
            # with the frame beneath it and can look dark blue on inactive
            # regions, even though the logical overlay colour is identical.
            if self._editable and index == self._active_index:
                # Keep a nearly transparent hit surface across the whole
                # region. Native layered windows otherwise hit-test only
                # the visible dashed border, making center drags unreliable.
                painter.setBrush(QColor(0, 0, 0, 1))
                painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 235), 2, Qt.DashLine))
                painter.drawRect(rect)
                painter.setBrush(QColor(color.red(), color.green(), color.blue(), 235))
                painter.setPen(QPen(QColor(12, 24, 38, 220), 1))
                for handle_rect in self._handle_rects(rect).values():
                    painter.drawEllipse(handle_rect)


class _LogoRegionOverlayWindow(_BlurRegionOverlayWindow):
    """Logo overlay that reuses the blur overlay's full structure.

    Inherits move, resize, and corner-handle dragging. Renders the logo
    image inside the rect; deletion is handled by the shared timeline
    Delete button.
    """

    logoMoved = Signal(float, float, float, float)
    logoDeleted = Signal()
    logoEditFinished = Signal()

    def __init__(self, on_region_changed=None, on_edit_finished=None):
        super().__init__(on_region_changed=on_region_changed, on_edit_finished=on_edit_finished)
        self._pixmap = None
        self._logo_items: list[dict] = []
        self._sync_timer = None
        self._opacity: float = 1.0
        self._rotation: float = 0.0
        self._logical_visible = True

    def set_logical_visible(self, visible: bool):
        self._logical_visible = bool(visible)
        if not self._logical_visible:
            self.hide()
        elif self._regions:
            self.sync_to_view()

    def set_editable(self, editable: bool):
        """Keep the logo image visible while hiding edit chrome when idle."""
        self._editable = bool(editable)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._editable)
        self.setCursor(Qt.OpenHandCursor if self._editable else Qt.ArrowCursor)
        if self._regions and self._target_view and self._target_view.isVisible():
            self.sync_to_view()
            self.show()
            self.raise_()
        elif not self._regions:
            self.hide()
        self.update()

    def clear_region(self):
        if hasattr(self, "_sync_timer") and self._sync_timer is not None:
            try:
                self._sync_timer.stop()
            except Exception:
                pass
        if self._target_view is not None:
            try:
                self._target_view.removeEventFilter(self)
            except Exception:
                pass
        self._pixmap = None
        self._logo_items = []
        self._opacity = 1.0
        self._rotation = 0.0
        super().clear_region()

    def sync_to_view(self):
        """Keep the logo image positioned even when edit handles are hidden."""
        win = self._target_view.window() if self._target_view else None
        if getattr(win, "_project_loading_in_progress", False):
            self.hide()
            return
        if (not self._logical_visible or self._suspended or not self._target_view
                or not self._target_view.isVisible() or not self._regions):
            self.hide()
            return
        top_left = self._target_view.mapToGlobal(QPoint(0, 0))
        self.setGeometry(QRect(top_left, self._target_view.size()))
        self.show()
        self.raise_()
        self.update()

    def attach_to_view(self, view: QWidget):
        # Inherit the base behavior (event filter on main window,
        # initial sync).
        super().attach_to_view(view)
        # Also install an event filter on the target view so the
        # overlay follows it on Resize/Move (the base blur overlay
        # only filters the main window).
        if view is not None:
            try:
                view.installEventFilter(self)
            except Exception:
                pass
        # Periodic sync as a fallback to keep the overlay aligned
        # with the target view (covers missed resize/move events,
        # e.g. when the dock is resized by the splitter).
        if self._sync_timer is None:
            self._sync_timer = QTimer(self)
            self._sync_timer.setInterval(300)
            self._sync_timer.timeout.connect(self.sync_to_view)
        self._sync_timer.start()

    def eventFilter(self, watched, event):
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return False
        except Exception:
            pass
        try:
            if self._target_view is not None and watched is self._target_view and event.type() in (
                QEvent.Resize, QEvent.Move,
            ):
                QTimer.singleShot(0, self.sync_to_view)
        except (RuntimeError, ReferenceError):
            return False
        return super().eventFilter(watched, event)

    def set_pixmap(self, path):
        if path and os.path.exists(path):
            self._pixmap = QPixmap(path)
        else:
            self._pixmap = None
        if 0 <= self._active_index < len(self._logo_items):
            self._logo_items[self._active_index]["pixmap"] = self._pixmap
        self.update()

    def set_opacity(self, opacity: float):
        self._opacity = max(0.0, min(1.0, float(opacity)))
        if 0 <= self._active_index < len(self._logo_items):
            self._logo_items[self._active_index]["opacity"] = self._opacity
        self.update()

    def get_opacity(self) -> float:
        return float(self._opacity)

    def set_rotation(self, rotation: float):
        self._rotation = float(rotation) % 360.0
        if 0 <= self._active_index < len(self._logo_items):
            self._logo_items[self._active_index]["rotation"] = self._rotation
        self.update()

    def get_rotation(self) -> float:
        return float(self._rotation)

    def set_logo_rect(self, x, y, w, h):
        if not self._regions:
            self._regions.append(QRectF(0.0, 0.0, 1.0, 1.0))
            self._active_index = 0
        self._regions[self._active_index] = QRectF(
            max(0.0, min(1.0, x)),
            max(0.0, min(1.0, y)),
            max(0.01, min(1.0, w)),
            max(0.01, min(1.0, h)),
        )
        self.update()

    def get_logo_rect(self):
        if not self._regions:
            return QRectF(0.0, 0.0, 0.0, 0.0)
        return QRectF(self._regions[self._active_index])

    def add_logo(self, x=0.1, y=0.1, w=0.2, h=0.2):
        self._regions = [QRectF(x, y, w, h)]
        self._active_index = 0

    def set_logos(self, logos, active_index: int = 0):
        """Display every L1 logo while making one layer editable."""
        regions, items = [], []
        for logo in logos or []:
            try:
                x = max(0.0, min(1.0, float(logo.get("x", 0.1))))
                y = max(0.0, min(1.0, float(logo.get("y", 0.1))))
                w = max(0.01, min(1.0 - x, float(logo.get("width", 0.2))))
                h = max(0.01, min(1.0 - y, float(logo.get("height", 0.2))))
                opacity = max(0.0, min(1.0, float(logo.get("opacity", 1.0))))
                rotation = float(logo.get("rotation", 0.0)) % 360.0
            except (AttributeError, TypeError, ValueError):
                continue
            path = str(logo.get("source", "") or "")
            regions.append({"x": x, "y": y, "width": w, "height": h})
            items.append({"pixmap": QPixmap(path) if path and os.path.exists(path) else None,
                          "opacity": opacity, "rotation": rotation})
        super().set_regions(regions)
        self._logo_items = items
        if self._regions:
            self._active_index = max(0, min(int(active_index), len(self._regions) - 1))
            item = self._logo_items[self._active_index]
            self._pixmap = item["pixmap"]
            self._opacity = item["opacity"]
            self._rotation = item["rotation"]

    def _hit_test(self, pos: QPointF) -> tuple[int, str]:
        index = self._active_index
        if index < 0 or index >= len(self._regions):
            return -1, ""
        rect = self.region_rect(index)
        for handle_name, handle_rect in self._handle_rects(rect).items():
            if handle_rect.contains(pos):
                return index, handle_name
        return (index, "move") if rect.contains(pos) else (-1, "")

    def paintEvent(self, event):
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self._target_view is not None:
            painter.setClipRect(self._target_view.get_preview_canvas_rect())
        for index, _region in enumerate(self._regions):
            rect = self.region_rect(index)
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            # Apply opacity and rotation around the center of the logo.
            painter.save()
            item = self._logo_items[index] if index < len(self._logo_items) else {}
            opacity = float(item.get("opacity", self._opacity))
            rotation = float(item.get("rotation", self._rotation))
            pixmap = item.get("pixmap", self._pixmap)
            painter.setOpacity(opacity)
            cx = rect.center().x()
            cy = rect.center().y()
            painter.translate(cx, cy)
            painter.rotate(rotation)
            painter.translate(-cx, -cy)
            if pixmap is not None and not pixmap.isNull():
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                painter.drawPixmap(rect, pixmap, pixmap.rect())
            # Reset transform/opacity before drawing chrome (handles and
            # border) so they remain axis-aligned and
            # fully opaque even when the logo is rotated or faded.
            painter.restore()
            if self._editable and index == self._active_index:
                pen = QPen(QColor(110, 231, 214, int(255 * max(opacity, 0.4))),
                           2, Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect)
            if self._editable and index == self._active_index:
                painter.setBrush(QColor(110, 231, 214))
                painter.setPen(QPen(QColor(12, 24, 38, 220), 1))
                for handle_rect in self._handle_rects(rect).values():
                    painter.drawEllipse(handle_rect)
class _MaskRegionOverlayWindow(_BlurRegionOverlayWindow):
    """Mask overlay that reuses the blur overlay's full structure.

    Inherits move (drag the middle) and corner-resize. Deletion is handled
    by the shared timeline Delete button, matching the other layer types.
    Renders a single rectangular region with the active mask colour so the
    user can see what they are editing.

    The mask mpv filter itself never applies a blur between solid and
    pixelate modes — it draws a coloured rectangle (solid) or
    pixelates the cropped region (pixelate). The overlay here is only
    a UI affordance for the user to position the region.
    """

    maskMoved = Signal(float, float, float, float)
    maskDeleted = Signal()
    maskEditFinished = Signal()

    # Mask overlay accent colour (used for the border + handles).
    ACCENT = QColor(201, 140, 90)  # M1 mask colour

    def __init__(self, on_region_changed=None, on_edit_finished=None):
        super().__init__(on_region_changed=on_region_changed, on_edit_finished=on_edit_finished)
        self._fill_color = QColor(201, 140, 90)
        self._sync_timer = None

    def set_fill_color(self, color: str | QColor):
        """Set the fill colour used to draw the overlay rectangle."""
        if isinstance(color, QColor):
            self._fill_color = QColor(color)
        else:
            try:
                self._fill_color = QColor(str(color))
            except Exception:
                self._fill_color = QColor(201, 140, 90)
        self.update()

    def get_mask_rect(self) -> QRectF:
        if not self._regions or self._active_index < 0:
            return QRectF(0.0, 0.0, 0.0, 0.0)
        return QRectF(self._regions[self._active_index])

    def add_mask(self, x: float = 0.3, y: float = 0.3, w: float = 0.4, h: float = 0.2):
        self._regions = [QRectF(x, y, w, h)]
        self._active_index = 0

    def set_mask_rect(self, x: float, y: float, w: float, h: float):
        self._regions = [QRectF(
            max(0.0, min(1.0, x)),
            max(0.0, min(1.0, y)),
            max(0.01, min(1.0, w)),
            max(0.01, min(1.0, h)),
        )]
        self.update()

    def set_mask_regions(self, regions, active_index: int = 0):
        """Display every M1 region while making one layer editable."""
        super().set_regions(regions)
        if self._regions:
            self._active_index = max(0, min(int(active_index), len(self._regions) - 1))

    def _hit_test(self, pos: QPointF) -> tuple[int, str]:
        """Only the selected mask can be edited from the preview.

        Other M1 regions remain visible as context, while timeline selection
        determines the active layer and avoids accidentally updating a
        different layer through the single-layer inspector binding.
        """
        index = self._active_index
        if index < 0 or index >= len(self._regions):
            return -1, ""
        rect = self.region_rect(index)
        for handle_name, handle_rect in self._handle_rects(rect).items():
            if handle_rect.contains(pos):
                return index, handle_name
        return (index, "move") if rect.contains(pos) else (-1, "")

    def attach_to_view(self, view: QWidget):
        super().attach_to_view(view)
        # Re-sync on view resize/move (the blur overlay already filters
        # the main window; we additionally filter the target view so
        # the mask follows when only the view is resized).
        if view is not None:
            try:
                view.installEventFilter(self)
            except Exception:
                pass
        if self._sync_timer is None:
            self._sync_timer = QTimer(self)
            self._sync_timer.setInterval(300)
            self._sync_timer.timeout.connect(self.sync_to_view)
        self._sync_timer.start()

    def eventFilter(self, watched, event):
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return False
        except Exception:
            pass
        try:
            if self._target_view is not None and watched is self._target_view and event.type() in (
                QEvent.Resize, QEvent.Move,
            ):
                QTimer.singleShot(0, self.sync_to_view)
        except (RuntimeError, ReferenceError):
            return False
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self._target_view is not None:
            painter.setClipRect(self._target_view.get_preview_canvas_rect())
        for index, _region in enumerate(self._regions):
            rect = self.region_rect(index)
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            # The actual mask effect is rendered by MPV. The editor overlay
            # must remain transparent so selecting a layer cannot tint or
            # change the opacity of the video beneath it.
            accent = QColor(self.ACCENT)
            pen = QPen(accent, 2, Qt.DashLine)
            if self._editable and index == self._active_index:
                painter.setPen(pen)
                # Preserve a full-region hit target while keeping the mask
                # editor visually transparent over the video.
                painter.setBrush(QColor(0, 0, 0, 1))
                painter.drawRect(rect)
                painter.setBrush(accent)
                painter.setPen(QPen(QColor(12, 24, 38, 220), 1))
                for handle_rect in self._handle_rects(rect).values():
                    painter.drawEllipse(handle_rect)
class _TextLayerOverlayWindow(QWidget):
    """Top-level, editable overlay for ordinary text layers."""
    layerSelected = Signal(str)
    layerMoved = Signal(str, float, float)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._target_view, self._items, self._active_id = None, [], ""
        self._drag_id, self._drag_offset, self._suppressed = "", QPointF(), False
        self._editable = False
        self.hide()

    def attach_to_view(self, view): self._target_view = view
    def set_suppressed(self, suppressed):
        self._suppressed = bool(suppressed)
        win = self._target_view.window() if self._target_view else None
        is_loading = getattr(win, "_project_loading_in_progress", False)
        if self._suppressed or is_loading:
            super().hide()
        elif self._items and self._target_view and self._target_view.isVisible():
            self.sync_to_view()
            super().show()
            self.raise_()
    def set_editable(self, editable):
        """Make the tool overlay interactive only in paused Edit Mode."""
        self._editable = bool(editable)
        if not self._editable:
            self._drag_id = ""
            self.setCursor(Qt.ArrowCursor)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._editable)
        self._update_input_mask()
        self.update()
    def set_items(self, items, active_id=""):
        self._items, self._active_id = list(items or []), str(active_id or "")
        self.sync_to_view()
        self._update_input_mask()
        self.update()
        win = self._target_view.window() if self._target_view else None
        is_loading = getattr(win, "_project_loading_in_progress", False)
        if self._items and not self._suppressed and not is_loading and self._target_view and self._target_view.isVisible():
            self.show()
            self.raise_()
        else:
            self.hide()
    def sync_to_view(self):
        if not self._target_view:
            return
        win = self._target_view.window() if self._target_view else None
        if getattr(win, "_project_loading_in_progress", False):
            self.hide()
            return
        canvas = self._target_view.get_preview_canvas_rect()
        if canvas.width() > 0 and canvas.height() > 0:
            canvas_rect = canvas.toRect() if hasattr(canvas, "toRect") else canvas
            self.setGeometry(QRect(self._target_view.mapToGlobal(canvas_rect.topLeft()), canvas_rect.size()))
            self._update_input_mask()

    def _update_input_mask(self):
        """Let clicks pass through unused parts of this full-canvas tool window."""
        region = QRegion()
        if not self._editable:
            self.setMask(region)
            return
        for item in self._items:
            rect, *_ = self._rect_for(item)
            region |= QRegion(rect.adjusted(-4, -4, 4, 4).toAlignedRect())
        self.setMask(region)
    def _rect_for(self, item):
        from app.layers.text_renderer import layout_text_layer
        layout = layout_text_layer(item, self.width(), self.height())
        return layout.rect, layout.font, layout.metrics, layout.lines
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.TextAntialiasing)
        for item in self._items:
            from app.layers.text_renderer import layout_text_layer, paint_text_layer
            layout = layout_text_layer(item, self.width(), self.height())
            rect = layout.rect
            paint_text_layer(painter, item, layout)
            if str(item.get("id", "")) == self._active_id:
                painter.setPen(QPen(QColor("#6ee7d6"), 1, Qt.DashLine)); painter.setBrush(Qt.NoBrush); painter.drawRect(rect)
    def mousePressEvent(self, event):
        if not self._editable:
            event.ignore(); return
        if event.button() == Qt.LeftButton:
            pos = QPointF(event.position())
            for item in reversed(self._items):
                rect, *_ = self._rect_for(item)
                if rect.contains(pos):
                    self._active_id = self._drag_id = str(item.get("id", "")); self._drag_offset = pos - rect.center()
                    # Selection refreshes the inspector and preview
                    # synchronously. Emit it first, then install the full
                    # canvas mask so that refresh cannot replace it with the
                    # text-sized click mask mid-drag.
                    self.layerSelected.emit(self._active_id)
                    # The normal input mask is limited to text bounds so other
                    # preview tools remain clickable. Once a drag starts it
                    # must cover the full canvas, otherwise Qt stops sending
                    # move events as soon as the pointer leaves the text.
                    self.setMask(QRegion(self.rect()))
                    self.setCursor(Qt.ClosedHandCursor); self.update(); event.accept(); return
        event.ignore()
    def mouseMoveEvent(self, event):
        if not self._editable:
            event.ignore(); return
        if not self._drag_id or self.width() <= 0 or self.height() <= 0: return
        point = QPointF(event.position()) - self._drag_offset
        item = next((candidate for candidate in self._items
                     if str(candidate.get("id", "")) == self._drag_id), None)
        if item is None:
            return
        # x/y are the text block's center, unlike a resize handle's top-left
        # coordinates.  Clamping the center alone lets a wide/tall text block
        # cross the canvas edge, which made Text behave differently from the
        # other visual layers.  Keep the whole rendered block inside the
        # preview canvas while preserving its current layout metrics.
        rect, *_ = self._rect_for(item)
        half_w = max(0.0, float(rect.width()) / (2.0 * self.width()))
        half_h = max(0.0, float(rect.height()) / (2.0 * self.height()))
        if half_w >= 0.5:
            x = 0.5
        else:
            x = max(half_w, min(1.0 - half_w, point.x() / self.width()))
        if half_h >= 0.5:
            y = 0.5
        else:
            y = max(half_h, min(1.0 - half_h, point.y() / self.height()))
        item["x"], item["y"] = x, y
        self.layerMoved.emit(self._drag_id, x, y); self.update(); event.accept()
    def mouseReleaseEvent(self, event):
        if self._drag_id:
            self._drag_id = ""
            self._update_input_mask()
            self.setCursor(Qt.OpenHandCursor)
            event.accept()


class MpvVideoView(QWidget):
    """Hosts an MPV video surface and overlays."""
    blurRegionChanged = Signal()
    blurEditFinished = Signal()
    subtitlePositionChanged = Signal(int, int)  # x_percent, y_percent
    subtitleDragStarted = Signal()
    framingChanged = Signal(float, float)
    logoMoved = Signal(float, float, float, float)  # x, y, w, h
    logoDeleted = Signal()
    logoEditFinished = Signal()
    maskRegionChanged = Signal()
    maskMoved = Signal(float, float, float, float)  # x, y, w, h
    maskDeleted = Signal()
    maskEditFinished = Signal()
    textLayerSelected = Signal(str)
    textLayerMoved = Signal(str, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: black; border-radius: 10px;")
        self.setMinimumSize(320, 180)
        self.video_source_width = 0
        self.video_source_height = 0
        # Subtitle layout follows the final export canvas, which can differ
        # from the input media when an output quality or ratio is selected.
        self.subtitle_render_width = 0
        self.subtitle_render_height = 0
        self.preview_aspect_key = "source"
        self.preview_scale_mode = "fit"
        self.preview_fill_focus_x = 0.5
        self.preview_fill_focus_y = 0.5
        # Fill framing is a project/output setting. It must not change from
        # ordinary mouse drags over the preview; a future explicit framing
        # tool can opt in through set_preview_framing_edit_enabled().
        self._preview_framing_edit_enabled = False
        self._framing_drag_active = False
        self._framing_drag_start = QPointF()
        self._framing_drag_focus = (0.5, 0.5)
        self._subtitle_event_filter_installed = False
        # Logical TS1 visibility is owned by the project/timeline.  Native
        # top-level overlays may be hidden by Windows during Alt-Tab or a
        # modal dialog, but restoring the window must never override this.
        self._subtitle_track_visible = True
        self._logo_track_visible = True
        # A top-level overlay is required here: MPV renders into a native
        # child window that can otherwise cover ordinary Qt child widgets.
        self.subtitle_item = _SubtitleOverlayWidget()
        self.subtitle_item.attach_to_view(self)
        self.subtitle_item.positionDragStarted.connect(self.subtitleDragStarted.emit)
        self.subtitle_item.positionDragFinished.connect(self.subtitlePositionChanged.emit)
        # Create the additional top-level overlay only when the project has
        # a text layer. This keeps ordinary video opening identical to the
        # established preview path.
        self.text_overlay = None
        self.video_surface = QWidget(self)
        self.video_surface.setAttribute(Qt.WA_NativeWindow, True)
        self.video_surface.setAttribute(Qt.WA_NoSystemBackground, True)
        self.video_surface.setAutoFillBackground(True)
        surface_pal = self.video_surface.palette()
        surface_pal.setColor(QPalette.Window, QColor(0, 0, 0))
        self.video_surface.setPalette(surface_pal)
        self.video_surface.setStyleSheet("background-color: black;")
        self.blur_overlay = _BlurRegionOverlayWindow(
            on_region_changed=self.blurRegionChanged.emit,
            on_edit_finished=self.blurEditFinished.emit,
        )
        self._blur_event_filter_installed = False
        self.mask_overlay = _MaskRegionOverlayWindow(
            on_region_changed=self.maskRegionChanged.emit,
            on_edit_finished=self.maskEditFinished.emit,
        )
        # Forward the overlay's internal delete signal to the public
        # MpvVideoView.maskDeleted signal so the main window can react
        # to X-button clicks on the mask overlay. Without this the
        # maskDeleted signal is emitted but nothing listens, so the
        # layer is never removed from the timeline.
        self.mask_overlay.maskDeleted.connect(self.maskDeleted.emit)
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
        # Do not call self.video_surface.show() or winId() here. It will be shown naturally in
        # showEvent() once the main window is displayed, avoiding an orphan native surface.
        self._sync_preview_stack()


    def _sync_preview_stack(self):
        self._update_preview_clip_mask()
        self._update_video_surface_mask()
        self.video_surface.lower()
        self.subtitle_item.raise_()
        if self.text_overlay is not None:
            self.text_overlay.raise_()
        self.ratio_badge.raise_()

    def _update_preview_clip_mask(self):
        """Clip native MPV content to the rounded preview frame on Windows."""
        if self.width() <= 0 or self.height() <= 0:
            return
        try:
            bitmap = QBitmap(self.size())
            bitmap.fill(Qt.color0)
            painter = QPainter(bitmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(Qt.color1)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
            painter.end()
            self.setMask(bitmap)
        except Exception:
            # Masking is cosmetic; never prevent video playback if a native
            # window platform does not support it.
            pass

    def _update_video_surface_mask(self):
        """Round the native MPV child so it follows the device-frame corners."""
        if self.video_surface.width() <= 0 or self.video_surface.height() <= 0:
            return
        try:
            bitmap = QBitmap(self.video_surface.size())
            bitmap.fill(Qt.color0)
            painter = QPainter(bitmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(Qt.color1)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.video_surface.rect().adjusted(1, 1, -1, -1), 8, 8)
            painter.end()
            self.video_surface.setMask(bitmap)
        except Exception:
            pass

    def set_text_layers(self, layers, active_id=""):
        """Update every ordinary text layer visible above the MPV surface."""
        if self.text_overlay is None:
            self.text_overlay = _TextLayerOverlayWindow()
            self.text_overlay.attach_to_view(self)
            self.text_overlay.layerSelected.connect(self.textLayerSelected.emit)
            self.text_overlay.layerMoved.connect(self.textLayerMoved.emit)
        self.text_overlay.set_items(layers, active_id)

    def set_video_dimensions(self, width: int, height: int):
        self.video_source_width = max(0, int(width or 0))
        self.video_source_height = max(0, int(height or 0))
        content_rect = self.get_video_content_rect().toRect()
        self.video_surface.setGeometry(self._video_surface_rect())
        self._sync_preview_stack()
        self._update_ratio_badge()
        self.reposition_subtitle()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.video_surface.setGeometry(self._video_surface_rect())
        self._sync_preview_stack()
        self.reposition_subtitle()
        self._update_ratio_badge()
        self.blur_overlay.sync_to_view()
        self.mask_overlay.sync_to_view()
        if self.text_overlay is not None:
            self.text_overlay.sync_to_view()
            win = self.window()
            is_loading = getattr(win, "_project_loading_in_progress", False)
            if self.text_overlay._items and not self.text_overlay._suppressed and not is_loading:
                self.text_overlay.show()
                self.text_overlay.raise_()
        self.update()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()
        self.mask_overlay.sync_to_view()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._blur_event_filter_installed:
            self._blur_event_filter_installed = True
            _win = self.window()
            if _win:
                _win.installEventFilter(self.blur_overlay)
        if not self._subtitle_event_filter_installed:
            self._subtitle_event_filter_installed = True
            _win = self.window()
            if _win:
                _win.installEventFilter(self)
        self.video_surface.show()
        self._sync_preview_stack()
        self._update_ratio_badge()
        self.reposition_subtitle()
        # Optional overlays may be restored before the native video surface
        # receives its final geometry. Reconnect their visible content now;
        # selection continues to control only edit chrome.
        win = self.window()
        is_loading = getattr(win, "_project_loading_in_progress", False)
        if not is_loading:
            if self.text_overlay is not None and self.text_overlay._items and not self.text_overlay._suppressed:
                self.text_overlay.sync_to_view()
                self.text_overlay.show()
                self.text_overlay.raise_()
            if (self._logo_track_visible
                    and getattr(self, "logo_overlay", None) is not None
                    and getattr(self.logo_overlay, "_regions", None)):
                self.logo_overlay.sync_to_view()
                self.logo_overlay.show()
                self.logo_overlay.raise_()
            if self._subtitle_track_visible and self.subtitle_item.current_text:
                self.subtitle_item.show()
        self.blur_overlay.sync_to_view()
        self.mask_overlay.sync_to_view()

    def eventFilter(self, watched, event):
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return False
        except Exception:
            pass
        try:
            if watched is self.window():
                event_type = event.type()
                if event_type == QEvent.WindowActivate:
                    QTimer.singleShot(0, self._restore_subtitle_overlay)
                else:
                    blocked_type = getattr(QEvent.Type, "WindowBlocked", None)
                    if event_type != QEvent.WindowDeactivate and event_type != blocked_type:
                        return False
                    self.subtitle_item.hide()
                    if self.text_overlay is not None:
                        self.text_overlay.hide()
        except (RuntimeError, ReferenceError):
            return False
        return False

    def _restore_subtitle_overlay(self):
        if not self.isVisible():
            return
        win = self.window()
        if getattr(win, "_project_loading_in_progress", False):
            return
        modal = QApplication.activeModalWidget()
        if modal is not None and modal.isVisible():
            return
        if self._subtitle_track_visible and self.subtitle_item.current_text:
            self.reposition_subtitle()
            self.subtitle_item.show()
            self.subtitle_item.raise_()
        if (self.text_overlay is not None and self.text_overlay._items
                and not self.text_overlay._suppressed):
            self.text_overlay.sync_to_view()
            self.text_overlay.show()
            self.text_overlay.raise_()

    def set_subtitle_track_visible(self, visible: bool):
        """Set logical TS1 visibility independently of native window focus."""
        self._subtitle_track_visible = bool(visible)
        win = self.window()
        if getattr(win, "_project_loading_in_progress", False) or not self._subtitle_track_visible:
            self.subtitle_item.hide()
        elif self.subtitle_item.current_text and self.isVisible():
            self.reposition_subtitle()
            self.subtitle_item.show()
            self.subtitle_item.raise_()

    def set_logo_track_visible(self, visible: bool):
        """Preserve L1 Hide/Show across native surface show/focus events."""
        self._logo_track_visible = bool(visible)
        overlay = getattr(self, "logo_overlay", None)
        if overlay is None:
            return
        if hasattr(overlay, "set_logical_visible"):
            overlay.set_logical_visible(self._logo_track_visible)

    def set_subtitle_render_dimensions(self, width: int, height: int):
        self.subtitle_render_width = max(0, int(width or 0))
        self.subtitle_render_height = max(0, int(height or 0))
        self.reposition_subtitle()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.subtitle_item.hide()
        self.blur_overlay.hide()
        self.mask_overlay.hide()
        if self.text_overlay is not None:
            self.text_overlay.hide()

    def set_preview_aspect_ratio(self, aspect_key: str):
        self.preview_aspect_key = str(aspect_key or "source").strip().lower() or "source"
        self.video_surface.setGeometry(self._video_surface_rect())
        self._sync_preview_stack()
        self._update_ratio_badge()
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()
        self.mask_overlay.sync_to_view()
        if self.text_overlay is not None:
            self.text_overlay.sync_to_view()
        self.update()

    def set_preview_scale_mode(self, scale_mode: str):
        self.preview_scale_mode = str(scale_mode or "fit").strip().lower() or "fit"
        self.video_surface.setGeometry(self._video_surface_rect())
        self._sync_preview_stack()
        self._update_ratio_badge()
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()
        self.mask_overlay.sync_to_view()
        if self.text_overlay is not None:
            self.text_overlay.sync_to_view()
        self.update()
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()
        self.mask_overlay.sync_to_view()
        if self.text_overlay is not None:
            self.text_overlay.sync_to_view()
        self.update()

    def set_preview_fill_focus(self, focus_x: float, focus_y: float):
        self.preview_fill_focus_x = max(0.0, min(1.0, float(focus_x)))
        self.preview_fill_focus_y = max(0.0, min(1.0, float(focus_y)))
        self.video_surface.setGeometry(self._video_surface_rect())
        self._sync_preview_stack()
        self._update_ratio_badge()
        self.reposition_subtitle()
        self.blur_overlay.sync_to_view()
        self.mask_overlay.sync_to_view()
        if self.text_overlay is not None:
            self.text_overlay.sync_to_view()
        self.update()

    def _video_surface_rect(self):
        """Keep the native MPV child clipped to the selected output canvas.

        For Fill, the source is zoomed/cropped by MPV inside this canvas. The
        normalized overlay geometry still uses ``get_video_content_rect`` so
        Blur/Mask/Text/Logo remain aligned with the visible source area.
        """
        is_fill = str(getattr(self, "preview_scale_mode", "fit") or "fit").strip().lower() == "fill"
        if is_fill:
            rect = self.get_preview_canvas_rect().toRect()
        else:
            rect = self.get_video_content_rect().toRect()
        # Leave the simulated device/frame stroke visible on both sides.
        # Floating canvas coordinates can round asymmetrically on odd pixel
        # widths, so use a symmetric one-pixel inset for the native surface.
        inset = 2 if is_fill else 1
        return rect.adjusted(inset, inset, -inset, -inset) if rect.width() > inset * 2 and rect.height() > inset * 2 else rect

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

        # Match the ASS export safe area: its 60px left/right margins live in
        # source-video coordinates, not in the much smaller preview widget.
        # Treating preview pixels as source pixels made the live layer far too
        # narrow on portrait previews and therefore wrapped extra lines.
        source_w = max(1, int(self.subtitle_render_width or self.video_source_width or rect.width()))
        source_h = max(1, int(self.subtitle_render_height or self.video_source_height or rect.height()))
        scale_x = rect.width() / source_w
        scale_y = rect.height() / source_h
        side_margin_px = 60 * scale_x

        desired_width = max(1, min(int(rect.width() - 2 * side_margin_px), int((source_w - 120) * scale_x)))
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

        if item.custom_position_enabled:
            # Match the drag anchor rule above: a subtitle may be positioned
            # anywhere across the canvas, including flush to an edge.  The
            # wide wrap widget is allowed to extend beyond the canvas so it
            # does not artificially restrict the anchor to the centre.
            x_pos = max(rect.left() - item_w / 2.0, min(x_pos, rect.right() - item_w / 2.0))
            y_pos = max(rect.top() - item_h / 2.0, min(y_pos, rect.bottom() - item_h / 2.0))
        else:
            x_pos = max(left_pad - item_w, min(x_pos, rect.right() + item_w))
            y_min = rect.top() - item_h
            y_max = rect.bottom()
            y_pos = max(y_min, min(y_pos, y_max))

        # The subtitle uses a top-level overlay so it stays above MPV's
        # native surface. Convert the preview-local position to global
        # screen coordinates before moving it.
        if item.is_top_level_overlay():
            target_position = self.mapToGlobal(QPoint(int(x_pos), int(y_pos)))
        else:
            target_position = QPoint(int(x_pos), int(y_pos))
        target_size = (int(item_w), int(item_h))
        current_size = (item.width(), item.height())
        if item.pos() == target_position and current_size == target_size:
            return
        item.move(target_position)
        if current_size != target_size:
            item.setFixedSize(*target_size)
        item.sync_to_view()
        item.update()

    def _can_drag_framing(self) -> bool:
        if not bool(getattr(self, "_preview_framing_edit_enabled", False)):
            return False
        if str(getattr(self, "preview_scale_mode", "fit") or "fit").strip().lower() != "fill":
            return False
        canvas_rect = self.get_preview_canvas_rect()
        content_rect = self.get_video_content_rect()
        return content_rect.width() > canvas_rect.width() + 0.5 or content_rect.height() > canvas_rect.height() + 0.5

    def set_preview_framing_edit_enabled(self, enabled: bool):
        """Opt in to Fill-focus dragging from a dedicated future tool only."""
        self._preview_framing_edit_enabled = bool(enabled)
        if not self._preview_framing_edit_enabled:
            self._framing_drag_active = False
            self.unsetCursor()

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

    def add_blur_region(self):
        self.blur_overlay.attach_to_view(self)
        self.blur_overlay.add_region()

    def set_blur_regions_normalized(self, regions):
        self.blur_overlay.set_regions(regions)

    def set_blur_active_index(self, index: int):
        """Choose the sole editable B1 region; all others remain visible."""
        self.blur_overlay.set_active_index(index)

    def clear_blur_region(self):
        self.blur_overlay.clear_region()
        self.blurRegionChanged.emit()

    # --- Mask / M1 overlay ---
    def add_mask_region(self, *, x: float = 0.3, y: float = 0.3,
                        w: float = 0.4, h: float = 0.2,
                        color: str | QColor = "#c98c5a"):
        self.mask_overlay.attach_to_view(self)
        self.mask_overlay.add_mask(x, y, w, h)
        self.mask_overlay.set_fill_color(color)
        self.mask_overlay.set_editable(True)
        self.mask_overlay.sync_to_view()

    def set_mask_region(self, *, x: float, y: float, w: float, h: float,
                        color: str | QColor | None = None,
                        editable: bool = True):
        self.mask_overlay.attach_to_view(self)
        self.mask_overlay.set_mask_rect(x, y, w, h)
        if color is not None:
            self.mask_overlay.set_fill_color(color)
        self.mask_overlay.set_editable(bool(editable))
        self.mask_overlay.sync_to_view()

    def set_mask_regions(self, regions, *, active_index: int = 0,
                         editable: bool = True):
        """Show all mask outlines and make one selected M1 layer editable."""
        self.mask_overlay.attach_to_view(self)
        self.mask_overlay.set_mask_regions(regions, active_index)
        self.mask_overlay.set_editable(bool(editable))
        self.mask_overlay.sync_to_view()

    def clear_mask_region(self):
        self.mask_overlay.set_editable(False)
        self.mask_overlay.clear_region()

    # --- Logo / Watermark overlay ---
    def set_logo(self, path: str, x: float, y: float, w: float, h: float):
        """Show the logo overlay at the given normalized position/size.

        The overlay reuses the blur overlay structure, so it supports
        drag-to-move and corner-resize. Layer deletion is handled by the
        shared timeline Delete button. Connect to logoMoved to react to
        position changes.
        """
        if not hasattr(self, "logo_overlay") or self.logo_overlay is None:
            def _on_logo_region_changed():
                if not hasattr(self, "logo_overlay") or self.logo_overlay is None:
                    return
                r = self.logo_overlay.get_logo_rect()
                if r.width() > 0 and r.height() > 0:
                    self.logoMoved.emit(r.x(), r.y(), r.width(), r.height())
            self.logo_overlay = _LogoRegionOverlayWindow(
                on_region_changed=_on_logo_region_changed,
                on_edit_finished=self.logoEditFinished.emit,
            )
            self.logo_overlay.logoDeleted.connect(self.logoDeleted.emit)
        self.logo_overlay.set_pixmap(path)
        self.logo_overlay.add_logo(x, y, w, h)
        self.logo_overlay.set_editable(True)
        self.logo_overlay.attach_to_view(self)
        self.logo_overlay.sync_to_view()
        # Delayed syncs in case the view geometry isn't ready yet
        QTimer.singleShot(50, self.logo_overlay.sync_to_view)
        QTimer.singleShot(200, self.logo_overlay.sync_to_view)
        QTimer.singleShot(500, self.logo_overlay.sync_to_view)

    def set_logos(self, logos, *, active_index: int = 0, editable: bool = True):
        """Render all L1 logos; only the selected layer is editable."""
        if not hasattr(self, "logo_overlay") or self.logo_overlay is None:
            self.set_logo("", 0.1, 0.1, 0.2, 0.2)
        self.logo_overlay.set_logos(logos, active_index)
        # The overlay may have existed while L1 was hidden in a previous
        # project/session. Always apply the current logical track state when
        # replacing its content so a visible L1 reliably becomes visible.
        self.logo_overlay.set_logical_visible(self._logo_track_visible)
        self.logo_overlay.set_editable(bool(editable))
        self.logo_overlay.attach_to_view(self)
        self.logo_overlay.sync_to_view()

    def update_logo_rect(self, x: float, y: float, w: float, h: float):
        if hasattr(self, "logo_overlay") and self.logo_overlay is not None:
            self.logo_overlay.set_logo_rect(x, y, w, h)
            self.logo_overlay.sync_to_view()

    def set_logo_opacity(self, opacity: float):
        if hasattr(self, "logo_overlay") and self.logo_overlay is not None:
            self.logo_overlay.set_opacity(opacity)

    def set_logo_rotation(self, rotation: float):
        if hasattr(self, "logo_overlay") and self.logo_overlay is not None:
            self.logo_overlay.set_rotation(rotation)

    def clear_logo(self):
        if hasattr(self, "logo_overlay") and self.logo_overlay is not None:
            self.logo_overlay.set_editable(False)
            self.logo_overlay.clear_region()

    def clear_text(self):
        if hasattr(self, "text_overlay") and self.text_overlay is not None:
            self.text_overlay.set_editable(False)
            self.text_overlay.set_items([])
            self.text_overlay.hide()

    def has_blur_region(self) -> bool:
        return self.blur_overlay.has_region()

    def get_mpv_target_winid(self) -> int:
        return int(self.video_surface.winId())

    def get_blur_region_normalized(self) -> dict | list[dict] | None:
        if not self.blur_overlay.has_region():
            return None
        regions = [
            {
                "x": round(float(rect.x()), 6),
                "y": round(float(rect.y()), 6),
                "width": round(float(rect.width()), 6),
                "height": round(float(rect.height()), 6),
            }
            for rect in self.blur_overlay._regions
        ]
        if len(regions) == 1:
            return regions[0]
        return regions
