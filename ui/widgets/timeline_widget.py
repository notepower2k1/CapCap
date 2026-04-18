from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView


class TimelineWidget(QGraphicsView):
    """CapCut-style timeline for subtitle preview and seeking."""

    seekRequested = Signal(int)
    segmentSelected = Signal(int)
    segmentTimingChanged = Signal(int, float, float)
    RULER_HEIGHT = 28
    VIDEO_ROW_Y = 36
    VIDEO_ROW_H = 46
    AUDIO_ROW_Y = 88
    AUDIO_ROW_H = 46
    SUBTITLE_ROW_Y = 140
    SUBTITLE_ROW_H = 82
    SCENE_HEIGHT = 232
    RESIZE_HANDLE_PX = 10
    MIN_SEGMENT_DURATION = 0.1
    SEGMENT_GAP = 0.03
    SNAP_THRESHOLD = 0.05

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(252)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: #0d1220; border: none;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.Antialiasing)

        self.horizontalScrollBar().setStyleSheet(
            """
            QScrollBar:horizontal {
                border: none;
                background: #111;
                height: 10px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: #333;
                min-width: 30px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #444;
            }
            """
        )

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.pixels_per_second = 100
        self.duration = 0
        self.segments = []
        self.playhead = None
        self.is_moving_playhead = False
        self._playing = False
        self._active_segment_index = -1
        self._last_position_ms = 0
        self._drag_mode = ""
        self._drag_segment_index = -1

    def _segment_index_at_scene_pos(self, scene_pos):
        x_pos = float(scene_pos.x())
        y_pos = float(scene_pos.y())
        in_audio_lane = self.AUDIO_ROW_Y <= y_pos <= (self.AUDIO_ROW_Y + self.AUDIO_ROW_H)
        in_subtitle_lane = self.SUBTITLE_ROW_Y <= y_pos <= (self.SUBTITLE_ROW_Y + self.SUBTITLE_ROW_H)
        if not (in_audio_lane or in_subtitle_lane):
            return -1
        for idx, seg in enumerate(self.segments):
            start_x = float(seg.get("start", 0.0)) * self.pixels_per_second
            end_x = float(seg.get("end", 0.0)) * self.pixels_per_second
            if start_x <= x_pos <= max(start_x + 12, end_x):
                return idx
        return -1

    def _in_audio_lane(self, scene_pos):
        y_pos = float(scene_pos.y())
        return self.AUDIO_ROW_Y <= y_pos <= (self.AUDIO_ROW_Y + self.AUDIO_ROW_H)

    def _resize_edge_at_scene_pos(self, scene_pos, segment_index):
        if segment_index < 0 or segment_index >= len(self.segments):
            return ""
        if not self._in_audio_lane(scene_pos):
            return ""
        seg = self.segments[segment_index]
        x_pos = float(scene_pos.x())
        start_x = float(seg.get("start", 0.0)) * self.pixels_per_second
        end_x = float(seg.get("end", 0.0)) * self.pixels_per_second
        if abs(x_pos - start_x) <= self.RESIZE_HANDLE_PX:
            return "left"
        if abs(x_pos - end_x) <= self.RESIZE_HANDLE_PX:
            return "right"
        return ""

    def _neighbor_bounds_for_segment(self, segment_index):
        prev_end = 0.0
        next_start = max(0.0, self.duration / 1000.0)
        if segment_index > 0:
            prev_end = float(self.segments[segment_index - 1].get("end", 0.0))
        if segment_index + 1 < len(self.segments):
            next_start = float(self.segments[segment_index + 1].get("start", next_start))
        return prev_end, next_start

    def set_playing(self, playing):
        self._playing = playing

    def set_duration(self, ms):
        self.duration = ms
        self.refresh()

    def set_segments(self, segments):
        self.segments = segments
        self.refresh()

    def set_active_segment_index(self, index):
        index = int(index)
        if index != self._active_segment_index:
            self._active_segment_index = index
            self.refresh()

    def refresh(self):
        self._scene.clear()
        width = (self.duration / 1000.0) * self.pixels_per_second
        width = max(width, self.viewport().width()) + 140
        self._scene.setSceneRect(0, 0, width, self.SCENE_HEIGHT)

        ruler_pen = QPen(QColor(80, 80, 80), 1)
        font = QFont("Segoe UI", 7)
        for sec in range(0, int(self.duration / 1000) + 5):
            x_pos = sec * self.pixels_per_second
            is_major = sec % 5 == 0
            height = 14 if is_major else 6
            self._scene.addLine(x_pos, 0, x_pos, height, ruler_pen)
            if is_major:
                mins, secs = divmod(sec, 60)
                label = self._scene.addText(f"{mins:02d}:{secs:02d}", font)
                label.setDefaultTextColor(QColor(150, 150, 150))
                label.setPos(x_pos + 2, -2)

        lane_pen = QPen(QColor("#1e3045"), 1)
        lane_specs = [
            (self.VIDEO_ROW_Y, self.VIDEO_ROW_H, QColor("#0f1b2b")),
            (self.AUDIO_ROW_Y, self.AUDIO_ROW_H, QColor("#101c2f")),
            (self.SUBTITLE_ROW_Y, self.SUBTITLE_ROW_H, QColor("#0e1828")),
        ]
        for row_y, row_h, fill in lane_specs:
            lane = self._scene.addRect(0, row_y, width, row_h, lane_pen, fill)
            lane.setZValue(-20)

        for sec in range(0, int(self.duration / 1000) + 5):
            x_pos = sec * self.pixels_per_second
            guide_pen = QPen(QColor(255, 255, 255, 18), 1)
            self._scene.addLine(x_pos, self.VIDEO_ROW_Y, x_pos, self.SCENE_HEIGHT, guide_pen)

        if self.duration > 0:
            full_clip_w = max(24, (self.duration / 1000.0) * self.pixels_per_second)
            video_clip = self._scene.addRect(
                4,
                self.VIDEO_ROW_Y + 7,
                max(20, full_clip_w - 8),
                self.VIDEO_ROW_H - 14,
                QPen(QColor("#4ecdc4"), 1),
                QColor(78, 205, 196, 48),
            )
            video_text = self._scene.addText("Main Video", QFont("Segoe UI", 8, QFont.Bold))
            video_text.setDefaultTextColor(QColor("#dffaf8"))
            video_text.setPos(14, self.VIDEO_ROW_Y + 14)

        for idx, seg in enumerate(self.segments):
            start_x = seg["start"] * self.pixels_per_second
            end_x = seg["end"] * self.pixels_per_second
            seg_w = max(16, end_x - start_x)
            is_active = idx == self._active_segment_index
            audio_rect = self._scene.addRect(
                start_x,
                self.AUDIO_ROW_Y + 11,
                seg_w,
                self.AUDIO_ROW_H - 22,
                QPen(QColor("#8ef7ee") if is_active else QColor("#6bd6d2"), 1),
                QColor(117, 241, 235, 110) if is_active else QColor(87, 211, 206, 72),
            )
            if is_active:
                left_handle = self._scene.addRect(
                    start_x - 2,
                    self.AUDIO_ROW_Y + 10,
                    4,
                    self.AUDIO_ROW_H - 20,
                    QPen(QColor("#c7fffb"), 1),
                    QColor("#c7fffb"),
                )
                right_handle = self._scene.addRect(
                    end_x - 2,
                    self.AUDIO_ROW_Y + 10,
                    4,
                    self.AUDIO_ROW_H - 20,
                    QPen(QColor("#c7fffb"), 1),
                    QColor("#c7fffb"),
                )
                left_handle.setZValue(5)
                right_handle.setZValue(5)

            waveform_item = self._scene.addText("▁▂▃▄▅▃▂▁ ▁▃▅▃▁", QFont("Segoe UI Symbol", 7))
            waveform_item.setDefaultTextColor(QColor("#b9faf6"))
            waveform_item.setPos(start_x + 6, self.AUDIO_ROW_Y + 14)

            start_x = seg["start"] * self.pixels_per_second
            end_x = seg["end"] * self.pixels_per_second
            seg_w = max(12, end_x - start_x)
            fill_color = QColor(245, 190, 92, 180) if is_active else QColor(229, 172, 75, 92)
            border_color = QColor(250, 220, 120) if is_active else QColor(120, 92, 40)

            rect = self._scene.addRect(0, 0, seg_w, self.SUBTITLE_ROW_H - 18, QPen(border_color, 1), fill_color)
            rect.setPos(start_x, self.SUBTITLE_ROW_Y + 9)
            clean_txt = seg["text"].replace("\n", " ").strip()
            if len(clean_txt) > 30:
                clean_txt = clean_txt[:27] + "..."
            text_item = self._scene.addText(clean_txt, QFont("Segoe UI", 8, QFont.Bold if is_active else QFont.Normal))
            text_item.setDefaultTextColor(QColor("#1c1204") if is_active else QColor("#fff3df"))
            text_item.setPos(start_x + 6, self.SUBTITLE_ROW_Y + 16)

        self.playhead = self._scene.addLine(0, 0, 0, self.SCENE_HEIGHT, QPen(QColor("#60f7ea"), 2))
        if self.playhead:
            self.playhead.setZValue(1000)
            self.set_position(self._last_position_ms)

    def set_position(self, ms):
        self._last_position_ms = int(ms)
        if not self.playhead:
            return
        x_pos = (ms / 1000.0) * self.pixels_per_second
        self.playhead.setLine(x_pos, 0, x_pos, self.SCENE_HEIGHT)

        if self._playing:
            view_rect = self.viewport().rect()
            scene_rect = self.mapToScene(view_rect).boundingRect()
            if x_pos > scene_rect.right() - 100 or x_pos < scene_rect.left():
                self.centerOn(x_pos, self.SCENE_HEIGHT / 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            segment_index = self._segment_index_at_scene_pos(scene_pos)
            if segment_index >= 0:
                self.segmentSelected.emit(segment_index)
                resize_edge = self._resize_edge_at_scene_pos(scene_pos, segment_index)
                if resize_edge:
                    self._drag_mode = resize_edge
                    self._drag_segment_index = segment_index
                    return
            self.is_moving_playhead = True
            self.handle_seek(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_mode and self._drag_segment_index >= 0:
            scene_pos = self.mapToScene(event.position().toPoint())
            seg = self.segments[self._drag_segment_index]
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            cursor_seconds = max(0.0, float(scene_pos.x()) / self.pixels_per_second)
            prev_end, next_start = self._neighbor_bounds_for_segment(self._drag_segment_index)
            if self._drag_mode == "left":
                min_start = max(0.0, prev_end + self.SEGMENT_GAP)
                max_start = end - self.MIN_SEGMENT_DURATION
                start = max(min_start, min(cursor_seconds, max_start))
                if abs(start - min_start) <= self.SNAP_THRESHOLD:
                    start = min_start
            elif self._drag_mode == "right":
                min_end = start + self.MIN_SEGMENT_DURATION
                max_end = next_start - self.SEGMENT_GAP if self._drag_segment_index + 1 < len(self.segments) else max(0.0, self.duration / 1000.0)
                if max_end < min_end:
                    max_end = min_end
                end = min(max(cursor_seconds, min_end), max_end)
                if self._drag_segment_index + 1 < len(self.segments) and abs(end - max_end) <= self.SNAP_THRESHOLD:
                    end = max_end
            max_duration = max(0.0, self.duration / 1000.0)
            start = max(0.0, min(start, max_duration if max_duration > 0 else start))
            end = max(start + self.MIN_SEGMENT_DURATION, min(end, max_duration if max_duration > 0 else end))
            self.segmentTimingChanged.emit(self._drag_segment_index, start, end)
        elif self.is_moving_playhead:
            self.handle_seek(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.is_moving_playhead = False
        self._drag_mode = ""
        self._drag_segment_index = -1
        super().mouseReleaseEvent(event)

    def handle_seek(self, pos):
        scene_pos = self.mapToScene(pos)
        ms = int((scene_pos.x() / self.pixels_per_second) * 1000)
        ms = max(0, min(ms, self.duration))
        self.seekRequested.emit(ms)
