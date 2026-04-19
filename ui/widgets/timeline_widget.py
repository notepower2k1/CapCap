from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView


class TimelineWidget(QGraphicsView):
    """CapCut-style timeline for subtitle preview and seeking."""

    seekRequested = Signal(int)
    segmentSelected = Signal(int)
    segmentTimingEditStarted = Signal(int, float, float)
    segmentTimingChanged = Signal(int, float, float)
    zoomChanged = Signal(int)
    layoutChanged = Signal()

    RULER_HEIGHT = 28
    TRACK_GAP = 6
    VIDEO_ROW_H = 46
    AUDIO_ROW_H = 46
    SUBTITLE_ROW_H = 60
    FIXED_SCENE_HEIGHT = 212
    VIEW_CHROME_HEIGHT = 24

    RESIZE_HANDLE_PX = 10
    MIN_SEGMENT_DURATION = 0.1
    SEGMENT_GAP = 0.0
    SNAP_THRESHOLD = 0.05
    DEFAULT_PX_PER_SECOND = 100
    MIN_PX_PER_SECOND = 40
    MAX_PX_PER_SECOND = 260

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.FIXED_SCENE_HEIGHT + self.VIEW_CHROME_HEIGHT)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: #0d1220; border: none;")
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.Antialiasing)

        self.horizontalScrollBar().setStyleSheet(
            """
            QScrollBar:horizontal {
                border: none;
                background: #142030;
                height: 12px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: #35506f;
                min-width: 30px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #416287;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            """
        )

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.pixels_per_second = self.DEFAULT_PX_PER_SECOND
        self.duration = 0
        self.segments = []
        self.playhead = None
        self.is_moving_playhead = False
        self._playing = False
        self._active_segment_index = -1
        self._last_position_ms = 0
        self._drag_mode = ""
        self._drag_segment_index = -1
        self._drag_offset_seconds = 0.0
        self._drag_feedback_start = 0.0
        self._drag_feedback_end = 0.0
        self._track_visibility = {
            "video": True,
            "audio": True,
            "subtitle": True,
        }
        self._video_thumbnails = []
        self._waveform_samples = []
        self._waveform_duration_s = 0.0
        self._layout = self._compute_layout()

    def _compute_layout(self):
        layout = {}
        track_specs = (
            ("subtitle", self.SUBTITLE_ROW_H),
            ("audio", self.AUDIO_ROW_H),
            ("video", self.VIDEO_ROW_H),
        )
        visible_tracks = [(name, base_h) for name, base_h in track_specs if self._track_visibility.get(name, True)]
        visible_count = len(visible_tracks)
        top_padding = self.RULER_HEIGHT + 8
        bottom_padding = 12
        gap_total = self.TRACK_GAP * max(0, visible_count - 1)
        available_height = max(
            0,
            self.FIXED_SCENE_HEIGHT - top_padding - bottom_padding - gap_total,
        )
        total_weight = sum(base_h for _name, base_h in visible_tracks) or 1
        assigned_heights = {}
        used_height = 0
        for idx, (name, base_h) in enumerate(visible_tracks):
            if idx == visible_count - 1:
                height = max(28, available_height - used_height)
            else:
                height = max(28, int(round(available_height * (base_h / total_weight))))
                used_height += height
            assigned_heights[name] = height

        y_cursor = top_padding
        for name, _base_h in track_specs:
            visible = bool(self._track_visibility.get(name, True))
            if visible:
                height = assigned_heights.get(name, 0)
                layout[name] = {"visible": True, "y": y_cursor, "h": height}
                y_cursor += height + self.TRACK_GAP
            else:
                layout[name] = {"visible": False, "y": y_cursor, "h": 0}

        layout["scene_height"] = self.FIXED_SCENE_HEIGHT
        return layout

    def is_track_visible(self, track_name: str) -> bool:
        return bool(self._track_visibility.get(str(track_name).lower(), False))

    def set_track_visibility(self, track_name: str, visible: bool):
        key = str(track_name).lower().strip()
        if key not in self._track_visibility:
            return
        visible = bool(visible)
        if self._track_visibility[key] == visible:
            return
        self._track_visibility[key] = visible
        self.refresh()
        self.layoutChanged.emit()

    def set_waveform_data(self, samples, duration_s: float):
        normalized_samples = []
        for sample in list(samples or []):
            if isinstance(sample, (list, tuple)):
                normalized_samples.append([float(max(0.0, min(1.0, value))) for value in sample])
            else:
                normalized_samples.append(float(max(0.0, min(1.0, sample))))
        normalized_duration = max(0.0, float(duration_s or 0.0))
        if normalized_samples == self._waveform_samples and abs(normalized_duration - self._waveform_duration_s) < 0.0001:
            return
        self._waveform_samples = normalized_samples
        self._waveform_duration_s = normalized_duration
        self.refresh()

    def set_video_thumbnails(self, thumbnails):
        normalized = []
        for item in list(thumbnails or []):
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            timestamp_s, pixmap = item
            if not isinstance(pixmap, QPixmap) or pixmap.isNull():
                continue
            normalized.append((float(timestamp_s), pixmap))
        self._video_thumbnails = normalized
        self.refresh()

    def _waveform_slice(self, start_s: float, end_s: float, bars: int):
        if not self._waveform_samples or self._waveform_duration_s <= 0.0 or bars <= 0:
            return []
        total = len(self._waveform_samples)
        start_ratio = max(0.0, min(1.0, float(start_s) / self._waveform_duration_s))
        end_ratio = max(0.0, min(1.0, float(end_s) / self._waveform_duration_s))
        if end_ratio <= start_ratio:
            return []
        values = []
        span = end_ratio - start_ratio
        for idx in range(bars):
            local_start = start_ratio + span * (idx / bars)
            local_end = start_ratio + span * ((idx + 1) / bars)
            sample_start = min(total - 1, int(local_start * total))
            sample_end = max(sample_start + 1, min(total, int(local_end * total)))
            neighbor_start = max(0, sample_start - 2)
            neighbor_end = min(total, sample_end + 2)
            chunk = self._waveform_samples[sample_start:sample_end]
            neighbor_chunk = self._waveform_samples[neighbor_start:neighbor_end]
            if not chunk:
                values.append(0.0)
            elif isinstance(chunk[0], list):
                band_count = len(chunk[0])
                aggregated = []
                for band_idx in range(band_count):
                    current_values = [float(column[band_idx]) for column in chunk if band_idx < len(column)]
                    nearby_values = [float(column[band_idx]) for column in neighbor_chunk if band_idx < len(column)]
                    peak = max(current_values) if current_values else 0.0
                    nearby_peak = max(nearby_values) if nearby_values else 0.0
                    mixed = max(peak, nearby_peak * 0.82)
                    aggregated.append(max(0.03, mixed))
                values.append(aggregated)
            else:
                current_peak = max(float(value) for value in chunk)
                nearby_peak = max(float(value) for value in neighbor_chunk) if neighbor_chunk else 0.0
                values.append(max(0.03, current_peak, nearby_peak * 0.82))
        return values

    def _segment_index_at_scene_pos(self, scene_pos):
        x_pos = float(scene_pos.x())
        y_pos = float(scene_pos.y())
        audio_row = self._layout.get("audio", {})
        subtitle_row = self._layout.get("subtitle", {})
        in_audio_lane = audio_row.get("visible") and audio_row["y"] <= y_pos <= (audio_row["y"] + audio_row["h"])
        in_subtitle_lane = subtitle_row.get("visible") and subtitle_row["y"] <= y_pos <= (subtitle_row["y"] + subtitle_row["h"])
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
        audio_row = self._layout.get("audio", {})
        return bool(audio_row.get("visible") and audio_row["y"] <= y_pos <= (audio_row["y"] + audio_row["h"]))

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

    def zoom_percent(self) -> int:
        return int(round((self.pixels_per_second / self.DEFAULT_PX_PER_SECOND) * 100))

    def set_zoom(self, pixels_per_second):
        target = max(self.MIN_PX_PER_SECOND, min(int(pixels_per_second), self.MAX_PX_PER_SECOND))
        if target == int(self.pixels_per_second):
            return
        self.pixels_per_second = target
        self.refresh()
        self.center_on_position(self._last_position_ms)
        self.zoomChanged.emit(self.zoom_percent())

    def zoom_in(self):
        self.set_zoom(self.pixels_per_second + 20)

    def zoom_out(self):
        self.set_zoom(self.pixels_per_second - 20)

    def reset_zoom(self):
        self.set_zoom(self.DEFAULT_PX_PER_SECOND)

    def set_duration(self, ms):
        self.duration = ms
        self.refresh()
        if not self._playing and self._last_position_ms <= 0:
            self.horizontalScrollBar().setValue(0)

    def set_segments(self, segments):
        self.segments = segments
        self.refresh()
        if not self._playing and self._last_position_ms <= 0:
            self.horizontalScrollBar().setValue(0)

    def set_active_segment_index(self, index):
        index = int(index)
        if index != self._active_segment_index:
            self._active_segment_index = index
            self.refresh()

    def refresh(self):
        self._scene.clear()
        self._layout = self._compute_layout()
        width = (self.duration / 1000.0) * self.pixels_per_second
        width = max(width, self.viewport().width()) + 140
        scene_height = self._layout["scene_height"]
        self._scene.setSceneRect(0, 0, width, scene_height)
        self.setFixedHeight(self.FIXED_SCENE_HEIGHT + self.VIEW_CHROME_HEIGHT)

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
        lane_fills = {
            "video": QColor("#0f1b2b"),
            "audio": QColor("#101c2f"),
            "subtitle": QColor("#0e1828"),
        }
        for track_name, fill in lane_fills.items():
            row = self._layout.get(track_name, {})
            if not row.get("visible"):
                continue
            lane = self._scene.addRect(0, row["y"], width, row["h"], lane_pen, fill)
            lane.setZValue(-20)

        guide_pen = QPen(QColor(255, 255, 255, 18), 1)
        for sec in range(0, int(self.duration / 1000) + 5):
            x_pos = sec * self.pixels_per_second
            self._scene.addLine(x_pos, self.RULER_HEIGHT, x_pos, scene_height, guide_pen)

        video_row = self._layout.get("video", {})
        if self.duration > 0 and video_row.get("visible"):
            full_clip_w = max(24, (self.duration / 1000.0) * self.pixels_per_second)
            video_rect = self._scene.addRect(
                4,
                video_row["y"] + 7,
                max(20, full_clip_w - 8),
                video_row["h"] - 14,
                QPen(QColor("#4ecdc4"), 1),
                QColor(78, 205, 196, 48),
            )
            clip_x = 4
            clip_y = video_row["y"] + 7
            clip_w = max(20, full_clip_w - 8)
            clip_h = video_row["h"] - 14
            if self._video_thumbnails:
                thumb_items = sorted(self._video_thumbnails, key=lambda item: item[0])
                tile_w = max(42.0, min(92.0, clip_h * 1.45))
                tile_count = max(1, int((clip_w + tile_w - 1) // tile_w))
                for idx in range(tile_count):
                    progress = idx / max(1, tile_count - 1)
                    sample_idx = min(len(thumb_items) - 1, int(round(progress * (len(thumb_items) - 1))))
                    _timestamp_s, pixmap = thumb_items[sample_idx]
                    slot_x = clip_x + (idx * tile_w)
                    slot_w = min(tile_w, (clip_x + clip_w) - slot_x)
                    if slot_w <= 2:
                        continue
                    target_w = max(16, int(round(slot_w - 2)))
                    target_h = max(16, int(round(clip_h - 2)))
                    scaled = pixmap.scaled(
                        target_w,
                        target_h,
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation,
                    )
                    crop_x = max(0, int((scaled.width() - target_w) / 2))
                    crop_y = max(0, int((scaled.height() - target_h) / 2))
                    cropped = scaled.copy(
                        crop_x,
                        crop_y,
                        min(target_w, scaled.width()),
                        min(target_h, scaled.height()),
                    )
                    item = self._scene.addPixmap(cropped)
                    item.setPos(slot_x + 1, clip_y + 1)
                    item.setOpacity(0.92)
                    item.setZValue(-5)

        audio_row = self._layout.get("audio", {})
        subtitle_row = self._layout.get("subtitle", {})
        for idx, seg in enumerate(self.segments):
            start_x = float(seg.get("start", 0.0)) * self.pixels_per_second
            end_x = float(seg.get("end", 0.0)) * self.pixels_per_second
            seg_w = max(16, end_x - start_x)
            is_active = idx == self._active_segment_index

            if audio_row.get("visible"):
                self._scene.addRect(
                    start_x,
                    audio_row["y"] + 11,
                    seg_w,
                    audio_row["h"] - 22,
                    QPen(QColor("#8ef7ee") if is_active else QColor("#6bd6d2"), 1),
                    QColor(117, 241, 235, 110) if is_active else QColor(87, 211, 206, 72),
                )
                if is_active:
                    left_handle = self._scene.addRect(
                        start_x - 2,
                        audio_row["y"] + 10,
                        4,
                        audio_row["h"] - 20,
                        QPen(QColor("#c7fffb"), 1),
                        QColor("#c7fffb"),
                    )
                    right_handle = self._scene.addRect(
                        end_x - 2,
                        audio_row["y"] + 10,
                        4,
                        audio_row["h"] - 20,
                        QPen(QColor("#c7fffb"), 1),
                        QColor("#c7fffb"),
                    )
                    left_handle.setZValue(5)
                    right_handle.setZValue(5)

                waveform_values = self._waveform_slice(
                    float(seg.get("start", 0.0)),
                    float(seg.get("end", 0.0)),
                    max(18, min(160, int(seg_w // 2))),
                )
                if waveform_values:
                    wave_top = audio_row["y"] + 8.0
                    wave_bottom = audio_row["y"] + audio_row["h"] - 6.0
                    wave_height = max(10.0, wave_bottom - wave_top)
                    step = seg_w / max(1, len(waveform_values))
                    column_energies = []
                    for amp in waveform_values:
                        if isinstance(amp, list):
                            band_values = [float(value) for value in amp]
                            if band_values:
                                energy = sum(value * value for value in band_values) / len(band_values)
                                column_energies.append(energy ** 0.5)
                            else:
                                column_energies.append(0.0)
                        else:
                            column_energies.append(float(amp))

                    smoothed = []
                    for idx_energy, value in enumerate(column_energies):
                        prev_v = column_energies[idx_energy - 1] if idx_energy > 0 else value
                        next_v = column_energies[idx_energy + 1] if idx_energy + 1 < len(column_energies) else value
                        smoothed.append((prev_v * 0.2) + (value * 0.6) + (next_v * 0.2))

                    for wave_idx, amp in enumerate(smoothed):
                        x_pos = start_x + (wave_idx * step) + (step / 2.0)
                        intensity = max(0.12, float(amp) ** 0.78)
                        spike_height = max(3.0, wave_height * intensity)
                        hue = int((wave_idx / max(1, len(smoothed) - 1)) * 300.0)
                        color = QColor.fromHsv(hue, 190 if is_active else 165, 255 if is_active else 235, 235)
                        wave_pen = QPen(color, max(1.2, min(2.4, step * 0.34)))
                        wave_pen.setCapStyle(Qt.RoundCap)
                        self._scene.addLine(
                            x_pos,
                            wave_bottom - spike_height,
                            x_pos,
                            wave_bottom,
                            wave_pen,
                        )
                else:
                    fallback_pen = QPen(QColor("#8fece5" if is_active else "#67cfc8"), 1.0)
                    fallback_pen.setCapStyle(Qt.RoundCap)
                    baseline_y = audio_row["y"] + (audio_row["h"] / 2.0)
                    step = max(3.0, min(8.0, seg_w / 10.0))
                    x_pos = start_x + 4.0
                    while x_pos < end_x - 2.0:
                        self._scene.addLine(x_pos, baseline_y - 2.0, x_pos, baseline_y + 2.0, fallback_pen)
                        x_pos += step
                if is_active and self._drag_mode and idx == self._drag_segment_index:
                    badge_text = f"{self._format_time(self._drag_feedback_start)} - {self._format_time(self._drag_feedback_end)}"
                    badge_width = max(124, len(badge_text) * 7)
                    badge = self._scene.addRect(
                        start_x,
                        audio_row["y"] - 20,
                        badge_width,
                        18,
                        QPen(QColor("#5fb9ff"), 1),
                        QColor(17, 33, 51, 220),
                    )
                    badge.setZValue(15)
                    badge_label = self._scene.addText(badge_text, QFont("Segoe UI", 7, QFont.Bold))
                    badge_label.setDefaultTextColor(QColor("#dff7ff"))
                    badge_label.setPos(start_x + 8, audio_row["y"] - 19)
                    badge_label.setZValue(16)

            if subtitle_row.get("visible"):
                fill_color = QColor(245, 190, 92, 180) if is_active else QColor(229, 172, 75, 92)
                border_color = QColor(250, 220, 120) if is_active else QColor(120, 92, 40)
                rect = self._scene.addRect(0, 0, max(12, seg_w), subtitle_row["h"] - 18, QPen(border_color, 1), fill_color)
                rect.setPos(start_x, subtitle_row["y"] + 9)
                clean_txt = str(seg.get("text", "")).replace("\n", " ").strip()
                if len(clean_txt) > 30:
                    clean_txt = clean_txt[:27] + "..."
                text_item = self._scene.addText(clean_txt, QFont("Segoe UI", 8, QFont.Bold if is_active else QFont.Normal))
                text_item.setDefaultTextColor(QColor("#1c1204") if is_active else QColor("#fff3df"))
                text_item.setPos(start_x + 6, subtitle_row["y"] + 16)

        self.playhead = self._scene.addLine(0, 0, 0, scene_height, QPen(QColor("#60f7ea"), 2))
        if self.playhead:
            self.playhead.setZValue(1000)
            self.set_position(self._last_position_ms)

    def center_on_position(self, ms):
        if self.duration <= 0:
            return
        x_pos = (float(ms) / 1000.0) * self.pixels_per_second
        self.centerOn(x_pos, self._layout.get("scene_height", self.FIXED_SCENE_HEIGHT) / 2)

    def set_position(self, ms):
        self._last_position_ms = int(ms)
        if not self.playhead:
            return
        x_pos = (ms / 1000.0) * self.pixels_per_second
        self.playhead.setLine(x_pos, 0, x_pos, self._layout.get("scene_height", self.FIXED_SCENE_HEIGHT))

        if self._playing:
            view_rect = self.viewport().rect()
            scene_rect = self.mapToScene(view_rect).boundingRect()
            if x_pos > scene_rect.right() - 100 or x_pos < scene_rect.left():
                self.centerOn(x_pos, self._layout.get("scene_height", self.FIXED_SCENE_HEIGHT) / 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            segment_index = self._segment_index_at_scene_pos(scene_pos)
            if segment_index >= 0:
                self.segmentSelected.emit(segment_index)
                if self._in_audio_lane(scene_pos):
                    resize_edge = self._resize_edge_at_scene_pos(scene_pos, segment_index)
                    self._drag_mode = resize_edge or "move"
                    self._drag_segment_index = segment_index
                    segment = self.segments[segment_index]
                    self.segmentTimingEditStarted.emit(
                        segment_index,
                        float(segment.get("start", 0.0)),
                        float(segment.get("end", 0.0)),
                    )
                    self._drag_feedback_start = float(segment.get("start", 0.0))
                    self._drag_feedback_end = float(segment.get("end", 0.0))
                    cursor_seconds = max(0.0, float(scene_pos.x()) / self.pixels_per_second)
                    self._drag_offset_seconds = cursor_seconds - float(segment.get("start", 0.0))
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
            elif self._drag_mode == "move":
                duration = max(self.MIN_SEGMENT_DURATION, end - start)
                min_start = max(0.0, prev_end + self.SEGMENT_GAP)
                max_start = max(min_start, next_start - self.SEGMENT_GAP - duration) if self._drag_segment_index + 1 < len(self.segments) else max(0.0, (self.duration / 1000.0) - duration)
                start = cursor_seconds - self._drag_offset_seconds
                start = min(max(start, min_start), max_start)
                if abs(start - min_start) <= self.SNAP_THRESHOLD:
                    start = min_start
                if abs(start - max_start) <= self.SNAP_THRESHOLD:
                    start = max_start
                end = start + duration
            max_duration = max(0.0, self.duration / 1000.0)
            start = max(0.0, min(start, max_duration if max_duration > 0 else start))
            end = max(start + self.MIN_SEGMENT_DURATION, min(end, max_duration if max_duration > 0 else end))
            self._drag_feedback_start = start
            self._drag_feedback_end = end
            self.segmentTimingChanged.emit(self._drag_segment_index, start, end)
        elif self.is_moving_playhead:
            self.handle_seek(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.is_moving_playhead = False
        self._drag_mode = ""
        self._drag_segment_index = -1
        self._drag_offset_seconds = 0.0
        self._drag_feedback_start = 0.0
        self._drag_feedback_end = 0.0
        self.refresh()
        super().mouseReleaseEvent(event)

    def handle_seek(self, pos):
        scene_pos = self.mapToScene(pos)
        ms = int((scene_pos.x() / self.pixels_per_second) * 1000)
        ms = max(0, min(ms, self.duration))
        self.seekRequested.emit(ms)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    @staticmethod
    def _format_time(seconds):
        total_ms = max(0, int(round(float(seconds) * 1000)))
        mins, rem_ms = divmod(total_ms, 60000)
        secs, ms = divmod(rem_ms, 1000)
        return f"{mins:02d}:{secs:02d}.{ms:03d}"
