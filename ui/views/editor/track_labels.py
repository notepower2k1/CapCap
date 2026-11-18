import os
from ui.views.editor.timeline_palette import SECONDARY_SUBTITLE_FILL, SECONDARY_SUBTITLE_ACCENT, LOCK_ACCENT
from app.layers.stack import TRACK_GROUPS

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QSizePolicy

try:
    from runtime_paths import asset_path
except ImportError:  # pragma: no cover - source-tree fallback
    asset_path = None

try:
    from i18n import t
except ImportError:  # pragma: no cover - source-tree fallback
    from ui.i18n import t


_ICON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "icons"))
_ICON_CACHE = {}


def _asset_icon(filename: str):
    icon = _ICON_CACHE.get(filename)
    if icon is None:
        path = asset_path("icons", filename) if asset_path is not None else ""
        if not path or not os.path.exists(path):
            path = os.path.join(_ICON_DIR, filename)
        icon = QIcon(path) if os.path.exists(path) else QIcon()
        _ICON_CACHE[filename] = icon
    return icon


TRACK_ICONS: dict[str, str] = {
    "V1": "\u25b6",
    "B1": "\u25a3",
    "A1": "\u266b",
    "A2": "\u266b",
    "TS1": "T",
    "TS2": "T",
    "S1": "T",
    "M1": "\u25a0",
}

AUDIO_PREFIXES = {"A1", "A2"}
BLUR_PREFIXES = {"B1"}
LOGO_PREFIXES = {"L1"}
MASK_PREFIXES = {"M1"}
TEXT_PREFIXES = {"T1"}
SUBTITLE_PREFIXES = {"TS1", "S1"}
MUTE_PREFIXES = {"A1", "A2", "TS1"}


def display_track_name(name: str) -> str:
    """Keep model IDs intact while showing only useful names in the UI."""
    if name == 'Background':
        return t('Background')
    prefix = (name or "").split(" ")[0]
    if prefix in {"TS1", "S1"} or name == "A2 Dub":
        return t("Main subtitles")
    if prefix == "TS2":
        return t("Secondary subtitles")
    labels = {"V1": "Video", "A1": "Original audio", "A2": "Background music",
              "B1": "Blur", "L1": "Logo", "M1": "Mask", "T1": "Text"}
    return t(labels[prefix]) if prefix in labels else name


class TrackLabelBar(QFrame):
    """Fixed left-side label strip showing track names. Pairs with EditorTimeline."""

    muteToggled = Signal(str, bool)  # track_name, is_muted
    blurToggled = Signal(str, bool)  # track_name, is_enabled
    logoToggled = Signal(str, bool)  # track_name, is_shown
    maskToggled = Signal(str, bool)  # track_name, is_shown
    textToggled = Signal(str, bool)  # track_name, is_shown
    subtitleToggled = Signal(str, bool)  # track_name, is_shown
    trackSelected = Signal(str)  # track_name
    lockToggled = Signal(str, bool)  # track_name, is_locked
    stackMoved = Signal(str, str, bool)  # dragged track, target track, before target

    TRACK_HEADER_W = 188
    RULER_HEIGHT = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.TRACK_HEADER_W)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #142030; border: none;")
        self._track_names: list[str] = []
        self._track_heights: list[int] = []
        self._track_locked: list[bool] = []
        self._track_muted: list[bool] = []
        self._track_blur_on: list[bool] = []
        self._track_logo_shown: list[bool] = []
        self._track_mask_shown: list[bool] = []
        self._track_text_shown: list[bool] = []
        self._track_subtitle_shown: list[bool] = []
        self._controls_enabled = True
        self._timeline_widget = None  # for vertical scroll sync
        self._drag_stack = ""
        self._stack_target = -1
        self._stack_before = True

    def set_timeline(self, timeline):
        """Store a reference to the timeline so the labels can be
        scrolled in sync with the timeline's vertical scrollbar."""
        self._timeline_widget = timeline
        # Repaint the labels whenever the timeline scrolls or its
        # geometry changes so the track label positions follow the
        # layer content.
        try:
            sb = timeline.verticalScrollBar()
            if sb is not None:
                sb.valueChanged.connect(self.update)
        except Exception:
            pass
        try:
            # Repaint on horizontal scroll and viewport changes too
            timeline.horizontalScrollBar().valueChanged.connect(self.update)
        except Exception:
            pass

    def set_tracks(self, names: list, heights: list, locked: list = None, muted: list = None):
        previous = {}
        for i, old_name in enumerate(self._track_names):
            prefix = old_name.split(" ")[0] if old_name else ""
            if prefix in BLUR_PREFIXES:
                previous[old_name] = self._track_blur_on[i] if i < len(self._track_blur_on) else True
            elif prefix in LOGO_PREFIXES:
                previous[old_name] = self._track_logo_shown[i] if i < len(self._track_logo_shown) else True
            elif prefix in MASK_PREFIXES:
                previous[old_name] = self._track_mask_shown[i] if i < len(self._track_mask_shown) else True
            elif prefix in TEXT_PREFIXES:
                previous[old_name] = self._track_text_shown[i] if i < len(self._track_text_shown) else True
            elif prefix in SUBTITLE_PREFIXES:
                previous[old_name] = self._track_subtitle_shown[i] if i < len(self._track_subtitle_shown) else True
        self._track_names = names
        self._track_heights = heights
        self._track_locked = locked or [False] * len(names)
        self._track_muted = muted or [False] * len(names)
        # Default blur effect to ON for B1 tracks
        self._track_blur_on = [
            previous.get(n, True) if n.split(" ")[0] in BLUR_PREFIXES else True for n in names
        ]
        # Default logo visibility to ON for L1 tracks
        self._track_logo_shown = [
            previous.get(n, True) if n.split(" ")[0] in LOGO_PREFIXES else True for n in names
        ]
        # Default mask visibility to ON for M1 tracks
        self._track_mask_shown = [
            previous.get(n, True) if n.split(" ")[0] in MASK_PREFIXES else True for n in names
        ]
        self._track_text_shown = [
            previous.get(n, True) if n.split(" ")[0] in TEXT_PREFIXES else True for n in names
        ]
        self._track_subtitle_shown = [
            previous.get(n, True) if n.split(" ")[0] in SUBTITLE_PREFIXES else True for n in names
        ]
        self.update()

    def set_controls_enabled(self, enabled: bool):
        self._controls_enabled = bool(enabled)
        self.update()

    @staticmethod
    def _draw_visibility_icon(painter, x: float, y: float, h: float, hidden: bool, color: QColor):
        """Draw a font-independent eye icon for the visibility control."""
        cx, cy = x + 14.0, y + h / 2.0
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        asset = _asset_icon("preview.svg")
        if not asset.isNull():
            painter.drawPixmap(int(x + 5), int(y + max(0, (h - 18) / 2)), asset.pixmap(18, 18))
            if hidden:
                painter.setPen(QPen(color, 1.5))
                painter.drawLine(QPointF(cx - 7, cy - 6), QPointF(cx + 7, cy + 6))
            painter.restore()
            return
        painter.setPen(QPen(color, 1.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(cx - 8), int(cy - 4), 16, 8)
        if not hidden:
            painter.setBrush(color)
            painter.drawEllipse(int(cx - 2.5), int(cy - 2.5), 5, 5)
        else:
            painter.drawLine(QPointF(cx - 7, cy - 6), QPointF(cx + 7, cy + 6))
        painter.restore()

    @staticmethod
    def _draw_mute_icon(painter, x: float, y: float, h: float, muted: bool, color: QColor):
        """Draw a compact speaker/mute icon without relying on emoji fonts."""
        cx, cy = x + 14.0, y + h / 2.0
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        asset = _asset_icon("volume_mute.svg" if muted else "volume_up.svg")
        if not asset.isNull():
            painter.drawPixmap(int(x + 5), int(y + max(0, (h - 18) / 2)), asset.pixmap(18, 18))
            painter.restore()
            return
        painter.setPen(QPen(color, 1.4))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([
            QPointF(cx - 7, cy - 2.5), QPointF(cx - 3, cy - 2.5),
            QPointF(cx + 2, cy - 7), QPointF(cx + 2, cy + 7),
            QPointF(cx - 3, cy + 2.5), QPointF(cx - 7, cy + 2.5),
        ]))
        painter.setBrush(Qt.NoBrush)
        if muted:
            painter.drawLine(QPointF(cx + 4, cy - 5), QPointF(cx + 10, cy + 5))
            painter.drawLine(QPointF(cx + 10, cy - 5), QPointF(cx + 4, cy + 5))
        else:
            painter.drawArc(int(cx + 1), int(cy - 6), 12, 12, -50 * 16, 100 * 16)
        painter.restore()

    @staticmethod
    def _draw_lock_icon(painter, x: float, y: float, h: float, locked: bool, color: QColor):
        """Draw the bundled SVG lock/unlock artwork."""
        asset = _asset_icon("lock.svg" if locked else "unlock.svg")
        if asset.isNull():
            painter.save()
            painter.setPen(QPen(color, 1.5))
            painter.drawRect(int(x + 8), int(y + h / 2), 12, 9)
            painter.drawArc(int(x + 10), int(y + h / 2 - 8), 8, 12, 0, 180 * 16)
            painter.restore()
            return
        painter.drawPixmap(int(x + 5), int(y + max(0, (h - 18) / 2)), asset.pixmap(18, 18))

    def _get_track_heights(self) -> list:
        """Return the current track heights, preferring the timeline's
        live values so the labels stay in sync with dynamic heights."""
        if self._timeline_widget is not None and hasattr(
            self._timeline_widget, "_track_heights"
        ):
            tl_heights = self._timeline_widget._track_heights
            if self._track_names and tl_heights:
                # Use timeline's heights, falling back to our cached
                # values for any track not in the timeline's dict.
                return [tl_heights.get(n, h) for n, h in zip(self._track_names, self._track_heights)]
        return self._track_heights

    def set_muted(self, name: str, muted: bool):
        for i, n in enumerate(self._track_names):
            if n == name and i < len(self._track_muted):
                self._track_muted[i] = muted
                self.update()
                return

    def set_blur_on(self, name: str, on: bool):
        for i, n in enumerate(self._track_names):
            if n == name and i < len(self._track_blur_on):
                self._track_blur_on[i] = on
                self.update()
                return

    def set_logo_shown(self, name: str, shown: bool):
        for i, n in enumerate(self._track_names):
            if n == name and i < len(self._track_logo_shown):
                self._track_logo_shown[i] = shown
                self.update()
                return

    def set_mask_shown(self, name: str, shown: bool):
        for i, n in enumerate(self._track_names):
            if n == name and i < len(self._track_mask_shown):
                self._track_mask_shown[i] = shown
                self.update()
                return

    def set_text_shown(self, name: str, shown: bool):
        for i, n in enumerate(self._track_names):
            if n == name and i < len(self._track_text_shown):
                self._track_text_shown[i] = bool(shown)
                self.update()
                return

    def set_subtitle_shown(self, name: str, shown: bool):
        for i, n in enumerate(self._track_names):
            if n == name and i < len(self._track_subtitle_shown):
                self._track_subtitle_shown[i] = bool(shown)
                self.update()
                return

    def mousePressEvent(self, event: QMouseEvent):
        # Track labels select/focus only. Visibility/mute and lock changes
        # are handled by the two dedicated icon cells on the right.
        if event.button() == Qt.LeftButton:
            idx = self._track_index_at(event.position().y())
            if 0 <= idx < len(self._track_names):
                name = self._track_names[idx]
                prefix = name.split(" ")[0] if name else ""
                x = event.position().x()
                if x < 22 and name in TRACK_GROUPS:
                    if self._controls_enabled and not self._track_locked[idx]:
                        self._drag_stack = name
                        self._stack_target = -1
                        self.setCursor(Qt.ClosedHandCursor)
                    event.accept()
                    return
                if x >= self.TRACK_HEADER_W - 84 and not self._controls_enabled:
                    event.accept()
                    return
                if x < self.TRACK_HEADER_W - 84:
                    self.trackSelected.emit(name)
                    event.accept()
                    return
                mute_boundary = self.TRACK_HEADER_W - (36 if prefix in AUDIO_PREFIXES else 56)
                if x < mute_boundary:
                    if prefix in MUTE_PREFIXES:
                        new_muted = not self._track_muted[idx]
                        self._track_muted[idx] = new_muted
                        self.update()
                        self.muteToggled.emit(name, new_muted)
                    event.accept()
                    return
                if x < self.TRACK_HEADER_W - 28:
                    if prefix in SUBTITLE_PREFIXES:
                        new_state = not self._track_subtitle_shown[idx]
                        self._track_subtitle_shown[idx] = new_state
                        self.update()
                        self.subtitleToggled.emit(name, new_state)
                        event.accept()
                        return
                    if prefix in BLUR_PREFIXES:
                        new_state = not self._track_blur_on[idx]
                        self._track_blur_on[idx] = new_state
                        self.update()
                        self.blurToggled.emit(name, new_state)
                    elif prefix in LOGO_PREFIXES:
                        new_state = not self._track_logo_shown[idx]
                        self._track_logo_shown[idx] = new_state
                        self.update()
                        self.logoToggled.emit(name, new_state)
                    elif prefix in MASK_PREFIXES:
                        new_state = not self._track_mask_shown[idx]
                        self._track_mask_shown[idx] = new_state
                        self.update()
                        self.maskToggled.emit(name, new_state)
                    elif prefix in TEXT_PREFIXES:
                        new_state = not self._track_text_shown[idx]
                        self._track_text_shown[idx] = new_state
                        self.update()
                        self.textToggled.emit(name, new_state)
                    event.accept()
                    return
                if event.position().x() >= self.TRACK_HEADER_W - 28:
                    new_locked = not bool(self._track_locked[idx] if idx < len(self._track_locked) else False)
                    if idx < len(self._track_locked):
                        self._track_locked[idx] = new_locked
                    self.update()
                    self.lockToggled.emit(name, new_locked)
                    event.accept()
                    return
                if prefix in MUTE_PREFIXES:
                    new_muted = not self._track_muted[idx]
                    self._track_muted[idx] = new_muted
                    self.update()
                    self.muteToggled.emit(name, new_muted)
                    event.accept()
                    return
                if prefix in BLUR_PREFIXES:
                    new_state = not (
                        self._track_blur_on[idx]
                        if idx < len(self._track_blur_on)
                        else False
                    )
                    if idx < len(self._track_blur_on):
                        self._track_blur_on[idx] = new_state
                    self.update()
                    self.blurToggled.emit(name, new_state)
                    event.accept()
                    return
                if prefix in LOGO_PREFIXES:
                    new_state = not (
                        self._track_logo_shown[idx]
                        if idx < len(self._track_logo_shown)
                        else False
                    )
                    if idx < len(self._track_logo_shown):
                        self._track_logo_shown[idx] = new_state
                    self.update()
                    self.logoToggled.emit(name, new_state)
                    event.accept()
                    return
                if prefix in MASK_PREFIXES:
                    new_state = not (
                        self._track_mask_shown[idx]
                        if idx < len(self._track_mask_shown)
                        else False
                    )
                    if idx < len(self._track_mask_shown):
                        self._track_mask_shown[idx] = new_state
                    self.update()
                    self.maskToggled.emit(name, new_state)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        # Show a context tooltip for the dedicated controls and for the
        # label-selection area.
        idx = self._track_index_at(event.position().y())
        if self._drag_stack:
            self._stack_target = idx if 0 <= idx < len(self._track_names) and self._track_names[idx] in TRACK_GROUPS else -1
            if self._stack_target >= 0:
                top = self._track_top(idx)
                self._stack_before = event.position().y() < top + self._track_heights[idx] / 2
            self.update()
            return
        is_clickable = False
        if 0 <= idx < len(self._track_names):
            prefix = self._track_names[idx].split(" ")[0] if self._track_names[idx] else ""
            is_clickable = bool(prefix)
        x = event.position().x()
        if is_clickable and x < 22:
            name = self._track_names[idx]
            draggable = name in TRACK_GROUPS and self._controls_enabled and not self._track_locked[idx]
            if name in BLUR_PREFIXES:
                self.setToolTip(t("Blur stays on the source video below translated subtitles"))
            else:
                self.setToolTip(t("Drag to change overlay order") if draggable else t("Fixed layer order"))
            self.setCursor(Qt.OpenHandCursor if draggable else Qt.ArrowCursor)
            return
        if is_clickable and x >= self.TRACK_HEADER_W - 84 and not self._controls_enabled:
            self.setToolTip(t("Pause playback to edit layer controls"))
            self.setCursor(Qt.ForbiddenCursor)
            return
        if is_clickable and x >= self.TRACK_HEADER_W - 28:
            self.setToolTip(t("Unlock layer") if self._track_locked[idx] else t("Lock layer"))
        elif is_clickable and x >= self.TRACK_HEADER_W - 56 and prefix in SUBTITLE_PREFIXES:
            self.setToolTip(t("Hide subtitle track") if self._track_subtitle_shown[idx] else t("Show subtitle track"))
        elif is_clickable and x >= self.TRACK_HEADER_W - 56 and (prefix in BLUR_PREFIXES or prefix in LOGO_PREFIXES or prefix in MASK_PREFIXES or prefix in TEXT_PREFIXES):
            hidden = False
            if prefix in BLUR_PREFIXES:
                hidden = not self._track_blur_on[idx]
            elif prefix in LOGO_PREFIXES:
                hidden = not self._track_logo_shown[idx]
            elif prefix in MASK_PREFIXES:
                hidden = not self._track_mask_shown[idx]
            elif prefix in TEXT_PREFIXES:
                hidden = not self._track_text_shown[idx]
            self.setToolTip(t("Show layer") if hidden else t("Hide layer"))
        elif is_clickable and x >= self.TRACK_HEADER_W - 84 and prefix in MUTE_PREFIXES:
            self.setToolTip(t("Unmute track") if self._track_muted[idx] else t("Mute track"))
            self.setCursor(Qt.PointingHandCursor)
            return
        if is_clickable and x >= self.TRACK_HEADER_W - 84:
            self.setToolTip("")
            self.setCursor(Qt.PointingHandCursor)
            return
        if is_clickable and event.position().x() >= self.TRACK_HEADER_W - 32:
            self.setToolTip(t("Unlock layer") if self._track_locked[idx] else t("Lock layer"))
        else:
            self.setToolTip("")
        self.setCursor(Qt.PointingHandCursor if is_clickable else Qt.ArrowCursor)

    def leaveEvent(self, event):
        self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_stack:
            source, target = self._drag_stack, self._stack_target
            self._drag_stack, self._stack_target = "", -1
            self.unsetCursor()
            self.update()
            if self._controls_enabled and 0 <= target < len(self._track_names):
                self.stackMoved.emit(source, self._track_names[target], self._stack_before)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _track_top(self, index):
        scroll = self._timeline_widget.verticalScrollBar().value() if self._timeline_widget else 0
        return self.RULER_HEIGHT + sum(self._track_heights[:index]) - scroll

    def _track_index_at(self, y: float) -> int:
        # Add the timeline's vertical scroll offset so the click
        # position is converted to the un-scrolled label space.
        scroll_y = 0
        if self._timeline_widget is not None:
            try:
                sb = self._timeline_widget.verticalScrollBar()
                if sb is not None:
                    scroll_y = int(sb.value())
            except Exception:
                pass
        yp = (y + scroll_y) - self.RULER_HEIGHT
        if yp < 0:
            return -1
        for i, h in enumerate(self._track_heights):
            if yp < h:
                return i
            yp -= h
        return -1

    def _label_lines(self, name: str, max_text_w: int, fm: QFontMetrics) -> list[str]:
        """Split the track label into lines that fit the available width.

        Strategy:
        - Keep the prefix token (V1, A1, A2, S1, B1) on the first line.
        - Remaining text is word-wrapped to fit the column.
        - If everything still fits on one line, no split is done.
        """
        parts = (name or "").split(" ", 1)
        prefix = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if not rest:
            return [prefix]

        words = rest.split(" ")
        line1 = prefix
        first_line_words: list[str] = []
        for w in words:
            candidate = (line1 + " " + w).strip()
            if fm.horizontalAdvance(candidate) <= max_text_w:
                line1 = candidate
                first_line_words.append(w)
            else:
                break
        if not first_line_words:
            # First word after prefix alone is too wide; put it on line 2.
            first_line_words = []
            line1 = prefix
        remaining_words = words[len(first_line_words):]
        if not remaining_words:
            return [line1]

        # Word-wrap the remaining text.
        lines: list[str] = [line1]
        current = ""
        for w in remaining_words:
            candidate = (current + " " + w).strip() if current else w
            if fm.horizontalAdvance(candidate) <= max_text_w:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = w
        if current:
            lines.append(current)
        return lines

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Apply the timeline's vertical scroll offset so the labels
        # scroll in sync with the layer content (stays aligned).
        scroll_y = 0
        if self._timeline_widget is not None:
            try:
                sb = self._timeline_widget.verticalScrollBar()
                if sb is not None:
                    scroll_y = int(sb.value())
            except Exception:
                pass

        y = self.RULER_HEIGHT - scroll_y

        # Use the timeline's live track heights so the labels expand
        # or shrink with the layer count.
        track_heights = self._get_track_heights()

        def _color(name: str) -> QColor:
            prefix = name.split(" ")[0] if name else ""
            palette = {"V1": QColor("#2a6bcf"), "B1": QColor("#6b5b7b"),
                       "A1": QColor("#2a9d3f"), "A2": QColor("#2a9d3f"),
                       "TS1": QColor("#c96b2a"),
                       "TS2": QColor(SECONDARY_SUBTITLE_FILL),
                       "S1": QColor("#c96b2a"),
                       "M1": QColor("#8c5a2a")}
            return palette.get(prefix, QColor("#6b8cb8"))

        font = QFont("Segoe UI", 9, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        # Reserve compact cells on every track. Audio uses mute + lock;
        # subtitles use mute + visibility + lock; overlays use visibility + lock.
        icon_col_w = 84
        text_x = 24
        text_w = self.TRACK_HEADER_W - text_x - icon_col_w - 4

        for i, name in enumerate(self._track_names):
            h = track_heights[i] if i < len(track_heights) else 60
            c = _color(name)
            muted = self._track_muted[i] if i < len(self._track_muted) else False
            blur_on = self._track_blur_on[i] if i < len(self._track_blur_on) else False
            prefix = name.split(" ")[0] if name else ""
            locked = bool(self._track_locked[i] if i < len(self._track_locked) else False)

            logo_hidden = (prefix in LOGO_PREFIXES
                           and i < len(self._track_logo_shown)
                           and not self._track_logo_shown[i])
            mask_hidden = (prefix in MASK_PREFIXES
                           and i < len(self._track_mask_shown)
                           and not self._track_mask_shown[i])
            text_hidden = (prefix in TEXT_PREFIXES
                           and i < len(self._track_text_shown)
                           and not self._track_text_shown[i])
            subtitle_hidden = (prefix in SUBTITLE_PREFIXES
                               and i < len(self._track_subtitle_shown)
                               and not self._track_subtitle_shown[i])
            if muted or logo_hidden or mask_hidden or text_hidden or subtitle_hidden:
                bg = QColor("#1a1a2e")
            elif prefix in BLUR_PREFIXES and not blur_on:
                bg = QColor("#1a1a2e")  # dimmed when blur is off
            else:
                bg = QColor("#142030")
            if locked:
                bg = QColor("#29251b")
            painter.fillRect(0, y, self.TRACK_HEADER_W, h, bg)
            painter.setPen(QPen(QColor("#1e2d42"), 1))
            painter.drawRect(0, y, self.TRACK_HEADER_W - 1, h - 1)

            painter.setPen(QPen(c, 0))
            painter.fillRect(0, y, 3, h, c)
            painter.setPen(QPen(QColor("#9cb4cc"), 1.3))
            center = y + h / 2
            if name in TRACK_GROUPS:
                for dy in (-4, 0, 4):
                    painter.drawLine(QPointF(8, center + dy), QPointF(16, center + dy))
            else:
                painter.drawEllipse(QRectF(9, center - 5, 6, 6))
                painter.drawLine(QPointF(12, center + 1), QPointF(12, center + 6))

            dim_text = (muted or logo_hidden or mask_hidden or text_hidden or subtitle_hidden
                        or (prefix in BLUR_PREFIXES and not blur_on))
            text_color = QColor("#555") if dim_text else QColor(SECONDARY_SUBTITLE_ACCENT if prefix == "TS2" else "#ffffff")
            painter.setPen(text_color)
            display_name = display_track_name(name)
            model = getattr(self._timeline_widget, "_timeline", None)
            track = next((track for track in getattr(model, "tracks", []) if track.name == name), None)
            if name in {"TS1", "S1", "A2 Dub"}:
                if track is not None and (getattr(track, "metadata", {}) or {}).get("subtitle_hidden_on_video"):
                    display_name += " · " + t("Hidden on video")
            lines = self._label_lines(display_name, text_w, fm)
            if not lines:
                lines = [name or ""]
            line_h = fm.height()
            total_h = line_h * len(lines)
            start_y = y + max(2, (h - total_h) // 2)
            for li, line in enumerate(lines):
                painter.drawText(text_x, start_y + li * line_h,
                                 text_w, line_h,
                                 Qt.AlignLeft | Qt.AlignVCenter, line)

            # (ON/OFF label removed - blur state is shown by track dimming)

            # Dedicated visibility control for subtitles and overlays.
            if prefix in SUBTITLE_PREFIXES or prefix in BLUR_PREFIXES or prefix in LOGO_PREFIXES or prefix in MASK_PREFIXES or prefix in TEXT_PREFIXES:
                hidden = subtitle_hidden or logo_hidden or mask_hidden or text_hidden or (prefix in BLUR_PREFIXES and not blur_on)
                icon_color = QColor("#5a2525") if hidden else QColor("#4f5c6e")
                if not self._controls_enabled:
                    icon_color = QColor("#36404d")
                self._draw_visibility_icon(painter, self.TRACK_HEADER_W - 56, y + 4, h - 8, hidden, icon_color)
            # Audio/subtitle mute control occupies the left control cell.
            if prefix in MUTE_PREFIXES:
                icon_color = QColor("#5a2525") if muted else QColor("#4f5c6e")
                if not self._controls_enabled:
                    icon_color = QColor("#36404d")
                # Keep A1's two controls visually grouped. TS1 retains the
                # left column for mute so its three controls stay ordered.
                mute_x = self.TRACK_HEADER_W - 70 if prefix in AUDIO_PREFIXES else self.TRACK_HEADER_W - 84
                self._draw_mute_icon(painter, mute_x, y + 4, h - 8, muted, icon_color)

            # Per-track lock control. It affects only this editable track,
            # never preview visibility or export.
            if prefix:
                locked = bool(self._track_locked[i] if i < len(self._track_locked) else False)
                if locked:
                    painter.fillRect(QRectF(self.TRACK_HEADER_W - 27, y + h / 2 - 12, 25, 24), QColor("#71531d"))
                icon_color = QColor(LOCK_ACCENT) if locked else QColor("#8394aa")
                if not self._controls_enabled:
                    icon_color = QColor("#4a5563")
                self._draw_lock_icon(painter, self.TRACK_HEADER_W - 28, y + 4, h - 8, locked, icon_color)

            y += h

        if self._stack_target >= 0 and self._drag_stack:
            line = self._track_top(self._stack_target)
            if not self._stack_before:
                line += self._track_heights[self._stack_target]
            painter.setPen(QPen(QColor("#5eead4"), 2))
            painter.drawLine(QPointF(0, line), QPointF(self.width(), line))
        painter.end()
