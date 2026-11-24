from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from i18n import t


SYNC_TOLERANCE_MS = 40


@dataclass(frozen=True)
class RecapSpan:
    start_ms: int
    end_ms: int
    clip_ms: int
    audio_ms: int

    @property
    def has_mismatch(self):
        return bool(self.clip_ms and self.audio_ms and abs(self.clip_ms - self.audio_ms) > SYNC_TOLERANCE_MS)


def timecode(ms, *, milliseconds=False):
    seconds = ms // 1000
    label = f"{seconds // 60:02d}:{seconds % 60:02d}"
    return f"{label}.{ms % 1000:03d}" if milliseconds else label


def _draw_playhead(painter, x, track_top):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#f5cc76"))
    painter.drawPolygon(QPolygonF([
        QPointF(x - 4, track_top - 6), QPointF(x + 4, track_top - 6), QPointF(x, track_top),
    ]))


class RecapTimeline(QWidget):
    picked = Signal(int, int)
    trim_started = Signal()
    trimmed = Signal(int, int, int)
    _GUTTER = 56
    _SCENE_Y = 29
    _ROW_HEIGHT = 32

    def __init__(self):
        super().__init__()
        self.setFixedHeight(70)
        self.spans = []
        self.scene = -1
        self.playhead = 0
        self._source_range = None
        self._drag = None
        self._drag_range = None
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMouseTracking(True)
        self.setAccessibleName(t("Recap timeline"))

    @property
    def duration(self):
        return self.spans[-1].end_ms if self.spans else 0

    def set_spans(self, spans, scene):
        if (spans, scene) != (self.spans, self.scene):
            self._cancel_drag()
            self.spans, self.scene = spans, scene
            self.update()

    def set_trim_range(self, source_range):
        if source_range != self._source_range:
            self._cancel_drag()
            self._source_range = source_range
            self.setToolTip(t("Drag an edge to trim the selected clip.") if source_range else "")
            self.update()

    def set_playhead(self, position):
        if self.playhead != position:
            self.playhead = position
            self.update()

    def _track_width(self):
        return max(1, self.width() - self._GUTTER - 9)

    def _x(self, ms):
        return self._GUTTER + self._track_width() * ms / max(1, self.duration)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#101826"))
        painter.setPen(QColor("#b1c3da"))
        painter.drawText(QRectF(6, self._SCENE_Y, self._GUTTER - 10, self._ROW_HEIGHT), Qt.AlignVCenter, t("Scene"))
        painter.fillRect(QRectF(self._GUTTER, self._SCENE_Y, self._track_width(), self._ROW_HEIGHT), QColor("#18283b"))
        if not self.duration:
            return
        for tick in range(5):
            position = round(self.duration * tick / 4)
            x = self._x(position)
            painter.setPen(QColor("#7189a5"))
            painter.drawLine(round(x), 24, round(x), self._SCENE_Y + self._ROW_HEIGHT)
            painter.setPen(QColor("#b1c3da"))
            painter.drawText(QRectF(x - 42 if tick == 4 else x, 4, 42, 18),
                             Qt.AlignRight if tick == 4 else Qt.AlignLeft, timecode(position))
        for scene, span in enumerate(self.spans):
            if span.end_ms > span.start_ms:
                self._draw_block(painter, scene, span)
        _draw_playhead(painter, self._x(min(self.duration, self.playhead)), self._SCENE_Y)
        self._draw_trim_handles(painter)

    def _trim_edges(self):
        if self._source_range is None or not 0 <= self.scene < len(self.spans):
            return None
        span = self.spans[self.scene]
        return self._x(span.start_ms), self._x(span.start_ms + span.clip_ms)

    def _edge_at(self, point):
        edges = self._trim_edges()
        if edges is None or not self._SCENE_Y <= point.y() < self._SCENE_Y + self._ROW_HEIGHT:
            return None
        radius = min(7, (edges[1] - edges[0]) / 3)
        for index, x in enumerate(edges):
            if abs(point.x() - x) <= radius:
                return index
        return None

    def _draw_trim_handles(self, painter):
        edges = self._trim_edges()
        if edges is None:
            return
        width = min(5, (edges[1] - edges[0]) / 3)
        for x in edges:
            painter.fillRect(QRectF(x - width / 2, self._SCENE_Y + 2, width, self._ROW_HEIGHT - 4),
                             QColor("#b1ddff"))
        if self._drag is not None:
            edge, _, scale = self._drag
            delta = self._drag_range[edge] - self._source_range[edge]
            x = round(max(self._GUTTER, min(self.width() - 1, edges[edge] + delta / scale)))
            painter.setPen(QPen(QColor("#f5cc76"), 2, Qt.DashLine))
            painter.drawLine(x, self._SCENE_Y, x, self._SCENE_Y + self._ROW_HEIGHT)
            start, end = self._drag_range
            painter.setPen(QColor("#edf6ff"))
            painter.drawText(QRectF(self._GUTTER, 4, self._track_width(), 17), Qt.AlignHCenter,
                             f"{(end - start) / 1000:.2f} s")

    def _draw_block(self, painter, scene, span):
        left = self._x(span.start_ms)
        width = max(1, self._x(span.end_ms) - left - 1)
        rect = QRectF(left, self._SCENE_Y + 2, width, self._ROW_HEIGHT - 4)
        painter.setPen(QPen(QColor("#b1ddff"), 1.2) if scene == self.scene else Qt.NoPen)
        painter.setBrush(QColor("#305f90" if span.clip_ms else "#294058"))
        painter.drawRoundedRect(rect, 4, 4)
        if 0 < span.clip_ms < span.end_ms - span.start_ms:
            video_end = self._x(span.start_ms + span.clip_ms)
            painter.fillRect(QRectF(video_end, rect.top() + 3, max(0, rect.right() - video_end - 2),
                                    rect.height() - 6), QColor("#243d57"))
        if span.has_mismatch:
            painter.fillRect(QRectF(left + 1, rect.bottom() - 3, max(0, width - 2), 3), QColor("#f5cc76"))
        if width >= 27:
            label = t("Scene {number}", number=scene + 1)
            painter.setPen(QColor("#edf6ff"))
            label = painter.fontMetrics().elidedText(label, Qt.ElideRight, max(0, int(width) - 6))
            painter.drawText(rect, Qt.AlignCenter, label)

    def mousePressEvent(self, event):
        x = event.position().x()
        if event.button() != Qt.LeftButton or not self.duration:
            return
        edge = self._edge_at(event.position())
        if edge is not None:
            self._drag = (edge, x, self.duration / self._track_width())
            self._drag_range = self._source_range[:2]
            self.setFocus()
            self.setCursor(Qt.SizeHorCursor)
            self.trim_started.emit()
            self.update()
            return
        if x < self._GUTTER:
            return
        position = min(self.duration, round((x - self._GUTTER) / self._track_width() * self.duration))
        for scene, span in enumerate(self.spans):
            if span.start_ms <= position < span.end_ms or position == span.end_ms == self.duration:
                self.picked.emit(scene, position)
                return

    def mouseMoveEvent(self, event):
        if self._drag is None:
            if self._edge_at(event.position()) is None:
                self.unsetCursor()
            else:
                self.setCursor(Qt.SizeHorCursor)
            return
        edge, origin, scale = self._drag
        start, end, source_duration = self._source_range
        delta = round((event.position().x() - origin) * scale)
        if edge == 0:
            self._drag_range = (max(0, min(start + delta, end - 100)), end)
        else:
            self._drag_range = (start, min(source_duration, max(start + 100, end + delta)))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._drag is None:
            return
        self.mouseMoveEvent(event)
        edge = self._drag[0]
        source_range = self._drag_range
        self._cancel_drag()
        if source_range != self._source_range[:2]:
            self.trimmed.emit(*source_range, edge)

    def _cancel_drag(self):
        if self._drag is not None:
            self._drag = self._drag_range = None
            self.unsetCursor()
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._drag is not None:
            self._cancel_drag()
            event.accept()
        else:
            super().keyPressEvent(event)


class RecapSourceTimeline(QWidget):
    seek_requested = Signal(int)
    _MARGIN = 10

    def __init__(self):
        super().__init__()
        self.setFixedHeight(43)
        self.setAccessibleName(t("Source video timeline"))
        self.duration = 0
        self.playhead = 0
        self.cut = None

    def set_context(self, duration, cut):
        if (duration, cut) != (self.duration, self.cut):
            self.duration, self.cut = duration, cut
            self.update()

    def set_playhead(self, position):
        if self.playhead != position:
            self.playhead = position
            self.update()

    def _track_width(self):
        return max(1, self.width() - self._MARGIN * 2)

    def _x(self, position):
        return self._MARGIN + self._track_width() * position / max(1, self.duration)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101826"))
        painter.fillRect(QRectF(self._MARGIN, 24, self._track_width(), 13), QColor("#22364d"))
        if not self.duration:
            return
        if self.cut is not None:
            left, right = map(self._x, self.cut)
            painter.fillRect(QRectF(left, 24, max(2, right - left), 13), QColor("#4687c3"))
        intervals = min(4, max(1, self.width() // 96 - 1))
        for fraction in range(intervals + 1):
            position = self.duration * fraction // intervals
            x = round(self._x(position))
            painter.setPen(QColor("#7189a5"))
            painter.drawLine(x, 20, x, 23)
            painter.setPen(QColor("#b1c3da"))
            left = max(self._MARGIN, min(x - 42, self.width() - self._MARGIN - 84))
            painter.drawText(QRectF(left, 1, 84, 18), Qt.AlignCenter,
                             timecode(position, milliseconds=self.duration < 4000))
        _draw_playhead(painter, self._x(max(0, min(self.playhead, self.duration))), 24)

    def _position_at(self, x):
        ratio = (x - self._MARGIN) / self._track_width()
        return round(max(0, min(1, ratio)) * self.duration)

    def mousePressEvent(self, event):
        if self.duration and event.button() == Qt.LeftButton:
            self.seek_requested.emit(self._position_at(event.position().x()))

    def mouseMoveEvent(self, event):
        if self.duration and event.buttons() & Qt.LeftButton:
            self.seek_requested.emit(self._position_at(event.position().x()))
