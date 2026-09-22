import math
import os
from bisect import bisect_right
from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView, QPushButton


from app.layers.base import BaseLayer, LayerType
from app.layers.timeline import Timeline, Track, Clip
from app.layers.dub_subtitle import DubSubtitleLayer
from app.runtime_paths import subprocess_hidden_kwargs


class EditorTimeline(QGraphicsView):
    """Dynamic multi-track timeline for layer-based video editing."""

    seekRequested = Signal(float)
    seekRequestedMs = Signal(int)
    scrubStarted = Signal()
    scrubFinished = Signal(int)
    layerSelected = Signal(str)
    layerMoved = Signal(str, float, float)
    playheadMoved = Signal(float)
    segmentSelected = Signal(int)
    segmentTimingChanged = Signal(int, float, float)
    # Emitted for every layer after its left/right timeline handle is dragged.
    # Subtitle segments keep their index-based signal above for the existing
    # transcript editor, while overlay layers are addressed by their id.
    layerTimingChanged = Signal(str, float, float)
    segmentTimingEditStarted = Signal(int, float, float)
    zoomChanged = Signal(int)
    layoutChanged = Signal()
    addLayerRequested = Signal()
    selectionRangeChanged = Signal(float, float)
    selectionRangeCleared = Signal()
    selectionModeChanged = Signal(bool)

    RULER_HEIGHT = 30
    TRACK_HEADER_W = 0
    # Keep a small grab area before time-zero bars. Track labels live in a
    # separate fixed widget, so this is content padding rather than header
    # width.
    CONTENT_LEFT_PAD = 8
    TRACK_LABEL_H = 24
    TRACK_MIN_H = 32
    TRACK_DEFAULT_H = 56
    REGION_TRACK_ROW_H = 60
    CHILD_TRACK_H = 48
    CHROME_H = 24
    HANDLE_W = 8
    MIN_DUR = 0.1
    SNAP_THRESHOLD = 0.05
    DEFAULT_PPS = 100
    MIN_PPS = 30
    MAX_PPS = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: #0d1220; border: none;")
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.pixels_per_second = self.DEFAULT_PPS
        self._duration = 10.0
        self._duration_explicit = False
        self._base_video_duration = 0.0
        self._playhead = 0.0
        self._selection_range: tuple[float, float] | None = None
        self._selection_drag = None
        self._selection_mode = False
        self._playing = False
        self._timeline: Timeline | None = None
        self._track_heights: dict[str, int] = {}
        self._drag_state = None
        self._hover_layer_id: str = ""
        self._selected_layer_id: str = ""
        # Playback may highlight the current subtitle, but a subtitle that
        # the user explicitly clicks or edits must remain selected.
        self._manual_subtitle_selection = False
        self._highlighted_speaker: str = ""
        # Presentation-only track hiding. Never write this to Track.visible:
        # preview and export must continue using the real project visibility.
        self._timeline_hidden_track_ids: set[str] = set()
        self._resize_refresh_pending = False
        self._segment_indices: dict[str, int] = {}
        # Playback repaints occur several times per second. Keep the static
        # subtitle overlap layout between edits instead of sorting every TS1
        # segment again on every paint.
        # Per overlap-stacked track: row assignment, row count, start-sorted
        # layers, their starts, and a monotonic prefix of maximum end times.
        # The latter three let playback paint only cues that can intersect
        # the viewport instead of walking a whole long TS1 track per repaint.
        self._overlap_layout_cache: dict[str, tuple[dict[str, int], int, list, list[float], list[float]]] = {}
        # Preserve subtitle row assignments by layer identity across model
        # refreshes.  This prevents inserting an earlier cue from reflowing
        # every existing cue to different rows.
        self._overlap_row_assignments: dict[str, dict[str, int]] = {}
        self._waveform_samples: list[float] = []
        self._waveform_duration_s = 0.0
        self._video_thumbnails: list[tuple[float, object]] = []
        self._video_time_warps: list[dict] = []
        self._has_add_btn = False
        self._voice_sync_mode: str = "Smart"
        self._playhead_follow_animation = QPropertyAnimation(self.horizontalScrollBar(), b"value", self)
        self._playhead_follow_animation.setDuration(180)
        self._playhead_follow_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._manual_navigation_active = False
        self._return_to_playhead_button = QPushButton("Return to Playhead", self.viewport())
        self._return_to_playhead_button.setCursor(Qt.PointingHandCursor)
        self._return_to_playhead_button.setStyleSheet(
            "QPushButton { background:#1b3852; color:#d9f3ff; border:1px solid #4a8cff; "
            "border-radius:9px; padding:4px 9px; font:600 10px 'Segoe UI'; }"
            "QPushButton:hover { background:#244b6d; }"
        )
        self._return_to_playhead_button.clicked.connect(self._return_to_playhead)
        self._return_to_playhead_button.hide()
        self.horizontalScrollBar().sliderPressed.connect(self._begin_manual_navigation)
        self.horizontalScrollBar().actionTriggered.connect(self._on_horizontal_scroll_action)
        self.horizontalScrollBar().valueChanged.connect(lambda _value: self._update_return_to_playhead_button())

        self._init_default_tracks()

        self.horizontalScrollBar().setStyleSheet(
            "QScrollBar:horizontal{border:none;background:#142030;height:12px;margin:0}"
            "QScrollBar::handle:horizontal{background:#35506f;min-width:30px;border-radius:6px}"
            "QScrollBar::handle:horizontal:hover{background:#416287}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0}"
        )
        self.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical{border:none;background:#142030;width:12px;margin:0}"
            "QScrollBar::handle:vertical{background:#35506f;min-height:30px;border-radius:6px}"
            "QScrollBar::handle:vertical:hover{background:#416287}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
        )

    def _init_default_tracks(self) -> None:
        self._timeline = Timeline(duration=self._duration)
        self._timeline.tracks = [
            Track(name="V1 Video", type=LayerType.VIDEO, height=80),
            Track(name="A1 Audio", type=LayerType.AUDIO, height=80),
        ]
        for t in self._timeline.tracks:
            self._track_heights[t.id] = t.height
        self._redraw()

    def reset_timeline(self) -> None:
        """Completely reset the timeline widget to fresh initial default tracks."""
        self._selected_layer_id = ""
        self._manual_subtitle_selection = False
        self._hover_layer_id = ""
        self._highlighted_speaker = ""
        self._drag_state = None
        self._timeline_hidden_track_ids.clear()
        self._segment_indices.clear()
        self._overlap_layout_cache.clear()
        self._overlap_row_assignments.clear()
        self._waveform_samples = []
        self._waveform_duration_s = 0.0
        self._video_thumbnails = []
        self._video_time_warps = []
        self._duration = 0.0
        self._duration_explicit = False
        self._base_video_duration = 0.0
        self._playhead = 0.0
        self._playing = False
        self._selection_range = None
        self._selection_mode = False
        self._track_heights.clear()
        if hasattr(self, "_return_to_playhead_button"):
            self._return_to_playhead_button.hide()
        if hasattr(self, "horizontalScrollBar"):
            self.horizontalScrollBar().setValue(0)
        self._init_default_tracks()


    def is_track_shown_on_timeline(self, track) -> bool:
        return bool(getattr(track, "visible", True) and track.id not in self._timeline_hidden_track_ids)

    def set_track_shown_on_timeline(self, track_id: str, shown: bool) -> None:
        track_id = str(track_id or "")
        if not track_id:
            return
        if shown:
            self._timeline_hidden_track_ids.discard(track_id)
        else:
            self._timeline_hidden_track_ids.add(track_id)
            if self._selected_layer_id:
                track, _layer = self._find_layer_by_id(self._selected_layer_id)
                if track is not None and track.id == track_id:
                    self._selected_layer_id = ""
        self._redraw()

    def set_video_time_warps(self, warps: list[dict]) -> None:
        self._video_time_warps = list(warps or [])
        self._redraw()

    def get_video_time_warps(self) -> list[dict]:
        return list(self._video_time_warps)

    def compute_warp_timeline_spans(self) -> list[dict]:
        from app.services.time_warp_service import TimeWarpService
        warps = getattr(self, "_video_time_warps", [])
        if not warps:
            return []
        sorted_warps = sorted(warps, key=lambda w: float(w.get("time", 0.0)))
        spans = []
        for w in sorted_warps:
            w_type = w.get("type", "freeze")
            dur = float(w.get("duration", 0.0))
            if dur <= 0:
                continue
            speed_val = float(w.get("speed", 1.0) or 1.0)
            if w_type == "slow":
                m_start = float(w.get("media_start", w.get("time", 0.0)))
                m_end = float(w.get("media_end", m_start))
                t_start = TimeWarpService.media_to_timeline_time(m_start, warps)
                t_end = TimeWarpService.media_to_timeline_time(m_end, warps)
                badge_text = f"🐢 {speed_val:.2f}x (+{dur:.1f}s)"
            else:
                anchor = float(w.get("time", 0.0))
                t_end = TimeWarpService.media_to_timeline_time(anchor, warps)
                t_start = max(0.0, t_end - dur)
                badge_text = f"⏸ +{dur:.1f}s"
            spans.append({
                "id": w.get("id", ""),
                "type": w_type,
                "t_start": round(t_start, 3),
                "t_end": round(t_end, 3),
                "duration": dur,
                "speed": speed_val,
                "badge_text": badge_text,
                "segment_index": w.get("segment_index"),
            })
        return spans

    # ---- Legacy API (drop-in replacement for existing TimelineWidget) ----

    def set_segments(self, segments: list) -> None:
        from app.layers.sync_bridge import sync_segments_to_dub_subtitle_layers
        if not self._timeline:
            self._init_default_tracks()

        self._overlap_layout_cache.clear()
        self._overlap_row_assignments.clear()

        seg_dicts = []
        for seg in segments:
            d = seg if isinstance(seg, dict) else (seg.to_dict() if hasattr(seg, "to_dict") else {})
            seg_dicts.append(d)

        sync_segments_to_dub_subtitle_layers(self._timeline, seg_dicts)

        # Register any new tracks in _track_heights (height adapts
        # to the number of layers in the track).
        for t in self._timeline.tracks:
            if t.id not in self._track_heights:
                self._track_heights[t.id] = self._compute_track_height(t)

        self._segment_indices.clear()
        for t in self._timeline.tracks:
            # Older/restored projects can keep TS1 as a regular Subtitle
            # track. Both subtitle track types use the same segment metadata.
            if self._is_subtitle_track(t):
                for layer in t.layers:
                    # Prefer the original segment index stored in metadata
                    # (set by sync_segments_to_dub_subtitle_layers). Falls
                    # back to z_index for layers created without metadata.
                    seg_idx = None
                    if isinstance(layer.metadata, dict):
                        raw = layer.metadata.get("_seg_index")
                        if raw is not None:
                            try:
                                seg_idx = int(raw)
                            except (TypeError, ValueError):
                                seg_idx = None
                    if seg_idx is None:
                        seg_idx = int(getattr(layer, "z_index", 0) or 0)
                    self._segment_indices[layer.id] = seg_idx

        base_dur = float(getattr(self, "_base_video_duration", 0.0) or 0.0)
        if base_dur <= 0.0 and self._timeline is not None:
            for t in self._timeline.tracks:
                if t.type == LayerType.VIDEO and t.layers:
                    try:
                        base_dur = max(base_dur, float(getattr(t.layers[0], "end", 0.0) or 0.0))
                    except (TypeError, ValueError):
                        pass
        if base_dur <= 0.0:
            base_dur = float(self._duration or 0.0)

        end_times = [float(d.get("end", 0.0)) for d in seg_dicts if float(d.get("end", 0.0)) > 0]
        max_seg_end = max(end_times) if end_times else base_dur
        self._duration = max(base_dur, max_seg_end)
        self._duration_explicit = True
        if self._timeline is not None:
            self._timeline.duration = self._duration
            for t in self._timeline.tracks:
                if t.type in (LayerType.VIDEO, LayerType.AUDIO):
                    for layer in t.layers:
                        layer.end = self._duration

        self._ensure_tracks_populated()
        self._redraw()

    def segment_index_for_layer_id(self, layer_id: str) -> int:
        """Return the canonical subtitle index for a timeline layer.

        Prefer the current layer metadata over the cache so a selection stays
        correct while TS1 is being rebuilt after an edit or project restore.
        """
        track, layer = self._find_layer_by_id(str(layer_id or ""))
        if layer is not None and self._is_subtitle_track(track):
            metadata = getattr(layer, "metadata", {}) or {}
            try:
                return int(metadata.get("_seg_index", getattr(layer, "z_index", -1)))
            except (TypeError, ValueError):
                pass
        return int(self._segment_indices.get(str(layer_id or ""), -1))

    def set_highlighted_speaker(self, speaker: str = "") -> None:
        self._highlighted_speaker = str(speaker or "").strip()
        self.viewport().update()

    def _ensure_tracks_populated(self):
        if not self._timeline:
            return
        from app.layers.audio import AudioLayer
        from app.layers.video import VideoLayer
        from app.layers.transform import Transform

        v1 = a1 = None
        for t in self._timeline.tracks:
            if t.name == "V1 Video":
                v1 = t
            elif t.name == "A1 Audio":
                a1 = t

        dur = self._duration
        if not self._duration_explicit or dur <= 0.0:
            for t in self._timeline.tracks:
                for l in t.layers:
                    dur = max(dur, l.end)
            self._duration = dur

        if v1 and not v1.layers:
            v1.layers.append(VideoLayer(
                name="V1 Video", source="",
                start=0.0, end=dur,
                transform=Transform(x=0, y=0, scale_x=1.0, scale_y=1.0),
            ))
        elif v1 and v1.layers:
            for l in v1.layers:
                l.end = dur

        if a1 and not a1.layers:
            a1.layers.append(AudioLayer(
                name="A1 Audio",
                source="",
                start=0.0, end=dur,
                volume=1.0,
            ))
        elif a1 and a1.layers:
            for l in a1.layers:
                l.end = dur

    def set_duration_ms(self, ms: int) -> None:
        new_dur = max(0, ms / 1000.0)
        old_dur = self._duration
        content_dur = 0.0
        if self._timeline:
            for t in self._timeline.tracks:
                if t.name not in ("V1 Video", "A1 Audio"):
                    for l in t.layers:
                        content_dur = max(content_dur, float(getattr(l, "end", 0.0) or 0.0))

        if new_dur > 0:
            self._duration = max(new_dur, content_dur)
            self._duration_explicit = True
        else:
            self._duration = max(content_dur, old_dur)

        if self._timeline is not None:
            self._timeline.duration = self._duration
            for t in self._timeline.tracks:
                if t.type in (LayerType.VIDEO, LayerType.AUDIO):
                    for layer in t.layers:
                        layer.end = self._duration
                elif t.type == LayerType.MASK:
                    for layer in t.layers:
                        try:
                            prev_end = float(layer.end)
                        except Exception:
                            prev_end = 0.0
                        if prev_end <= 0 or abs(prev_end - old_dur) < 0.05:
                            layer.end = self._duration
        self._ensure_tracks_populated()
        self._redraw()

    set_duration = set_duration_ms

    def set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        if self._playing:
            # Playback is review mode. Never carry an in-progress segment
            # drag/resize into it; seeking remains handled separately below.
            self._drag_state = None
            self._selection_drag = None
            self._manual_subtitle_selection = False
            self.setCursor(Qt.ArrowCursor)
        if not playing:
            self._manual_navigation_active = False
            self._return_to_playhead_button.hide()
        self.viewport().update()

    def set_active_segment_index(self, index: int) -> None:
        if not self._timeline:
            return
        # Do not override a user-selected non-subtitle layer. The auto-select
        # is only meant to highlight the currently playing subtitle. If the
        # user has selected an audio/video/blur layer, leave it alone.
        current_id = str(self._selected_layer_id or "")
        if current_id:
            current_track = None
            for t in self._timeline.tracks:
                for l in t.layers:
                    if l.id == current_id:
                        current_track = t
                        break
                if current_track is not None:
                    break
            # If the previously selected layer no longer exists in the
            # timeline (e.g. a stale BlurLayer from a previous project),
            # clear the selection so the auto-select can proceed and the
            # inspector shows the correct card.
            if current_track is None:
                self._selected_layer_id = ""
                self._manual_subtitle_selection = False
            elif current_track.type not in (
                LayerType.SUBTITLE,
                LayerType.DUB_SUBTITLE,
            ):
                return
            elif self._manual_subtitle_selection:
                return
        for lid, idx in self._segment_indices.items():
            if idx == index:
                if self._selected_layer_id == lid:
                    return
                self._selected_layer_id = lid
                self.viewport().update()
                return

    def set_waveform_data(self, samples: list, duration_s: float) -> None:
        self._waveform_samples = [
            max(0.0, min(1.0, float(value)))
            for value in (samples or [])
            if isinstance(value, (int, float))
        ]
        self._waveform_duration_s = max(0.0, float(duration_s or 0.0))
        self.viewport().update()

    def set_video_thumbnails(self, thumbnails: list) -> None:
        self._video_thumbnails = [
            (max(0.0, float(timestamp)), pixmap)
            for timestamp, pixmap in (thumbnails or [])
            if pixmap is not None and not getattr(pixmap, "isNull", lambda: True)()
        ]
        self.viewport().update()

    def set_video_source(self, path: str, duration_s: float) -> None:
        from app.layers.sync_bridge import ensure_v1_a1_tracks
        if not self._timeline:
            self._init_default_tracks()
        if duration_s <= 0:
            duration_s = self._probe_video_duration(path)
        if duration_s > 0:
            ensure_v1_a1_tracks(self._timeline, path, duration_s)
            self._base_video_duration = float(duration_s)
            content_dur = 0.0
            if self._timeline:
                for t in self._timeline.tracks:
                    if t.name not in ("V1 Video", "A1 Audio"):
                        for l in t.layers:
                            content_dur = max(content_dur, float(getattr(l, "end", 0.0) or 0.0))
            self._duration = max(float(duration_s), content_dur)
            self._duration_explicit = True
            # Keep the Timeline model's duration in sync so layers that
            # span the whole video (Mask track) use the real length.
            self._timeline.duration = self._duration
            for t in self._timeline.tracks:
                if t.type in (LayerType.VIDEO, LayerType.AUDIO):
                    for layer in t.layers:
                        layer.end = self._duration
        self._redraw()

    @staticmethod
    def _probe_video_duration(path: str) -> float:
        if not path or not os.path.exists(path):
            return 0.0
        try:
            import av
            c = av.open(path)
            dur = float(c.duration) / 1000000.0 if c.duration is not None else 0.0
            c.close()
            if dur > 0.0:
                return dur
        except Exception:
            pass

        try:
            import subprocess
            from app.video_processor import _ffprobe_path
            ffprobe = _ffprobe_path()
            if not os.path.exists(ffprobe):
                return 0.0
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=30,
                **subprocess_hidden_kwargs(),
            )
            if result.returncode == 0:
                return float(result.stdout.strip() or 0)
        except Exception:
            pass
        return 0.0

    @property
    def duration(self) -> int:
        return int(self._duration * 1000)

    def enable_add_layer_button(self) -> None:
        self._has_add_btn = True

    def sync_blur_regions(self, blur_regions: list[dict] | None) -> None:
        from app.layers.sync_bridge import sync_blur_regions_to_layers
        if not self._timeline:
            self._init_default_tracks()
        sync_blur_regions_to_layers(self._timeline, blur_regions)
        self._redraw()

    def sync_tts_track(self, voice_track_path: str, duration: float = 0.0, segments: list | None = None) -> None:
        from app.layers.sync_bridge import sync_tts_to_dub_subtitle_layers
        if not self._timeline:
            self._init_default_tracks()
        sync_tts_to_dub_subtitle_layers(
            self._timeline, voice_track_path, segments=segments
        )
        # Register track height (adapts to the number of layers)
        for t in self._timeline.tracks:
            if t.id not in self._track_heights:
                self._track_heights[t.id] = self._compute_track_height(t)
        self._redraw()

    # ---- End legacy API ----

    def set_playhead(self, seconds: float) -> None:
        self._playhead = seconds
        self._follow_playhead_during_playback()
        viewport = self.viewport()
        if viewport:
            viewport.update()
        self.playheadMoved.emit(seconds)

    def selection_range(self):
        return self._selection_range

    def selection_mode(self) -> bool:
        return bool(self._selection_mode)

    def set_selection_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._selection_mode == enabled:
            return
        self._selection_mode = enabled
        self.selectionModeChanged.emit(enabled)
        self.viewport().setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        self.viewport().update()

    def set_selection_range(self, start: float, end: float) -> None:
        start, end = sorted((max(0.0, float(start)), min(self._duration, float(end))))
        if end - start < self.MIN_DUR:
            self.clear_selection_range()
            return
        self._selection_range = (start, end)
        self.selectionRangeChanged.emit(start, end)
        self.viewport().update()

    def clear_selection_range(self) -> None:
        if self._selection_range is None:
            self.set_selection_mode(False)
            return
        self._selection_range = None
        self.set_selection_mode(False)
        self.selectionRangeCleared.emit()
        self.viewport().update()

    def set_position(self, ms: int) -> None:
        if self._selection_drag is not None and self._selection_drag.get("mode") == "scrub":
            return
        self.set_playhead(ms / 1000.0)

    def _follow_playhead_during_playback(self) -> None:
        """Keep a playing playhead comfortably inside the visible timeline.

        This only moves the horizontal viewport; it never seeks media or
        changes timeline content. The short scrollbar animation avoids abrupt
        jumps once the playhead reaches the right-side follow threshold.
        """
        if not self._playing:
            return
        if self._manual_navigation_active:
            self._update_return_to_playhead_button()
            return
        viewport = self.viewport()
        scroll_bar = self.horizontalScrollBar()
        if viewport is None or scroll_bar is None:
            return
        view_width = max(1, viewport.width())
        playhead_x = self.CONTENT_LEFT_PAD + self._playhead * self.pixels_per_second - scroll_bar.value()
        follow_threshold = view_width * 0.78
        if playhead_x <= follow_threshold:
            return
        target = int(self.CONTENT_LEFT_PAD + self._playhead * self.pixels_per_second - view_width * 0.70)
        target = max(scroll_bar.minimum(), min(target, scroll_bar.maximum()))
        if target <= scroll_bar.value():
            return
        if self._playhead_follow_animation.state() == QAbstractAnimation.Running:
            self._playhead_follow_animation.stop()
        self._playhead_follow_animation.setStartValue(scroll_bar.value())
        self._playhead_follow_animation.setEndValue(target)
        self._playhead_follow_animation.start()

    def _begin_manual_navigation(self) -> None:
        if not self._playing:
            return
        if self._playhead_follow_animation.state() == QAbstractAnimation.Running:
            self._playhead_follow_animation.stop()
        self._manual_navigation_active = True
        self._update_return_to_playhead_button()

    def _on_horizontal_scroll_action(self, _action: int) -> None:
        # Scrollbar arrows, page clicks, keyboard navigation, and dragging
        # all enter the same playback-only manual navigation mode.
        self._begin_manual_navigation()

    def _update_return_to_playhead_button(self) -> None:
        button = self._return_to_playhead_button
        viewport = self.viewport()
        if not self._manual_navigation_active or not self._playing or viewport is None:
            button.hide()
            return
        playhead_x = self.CONTENT_LEFT_PAD + self._playhead * self.pixels_per_second - self.horizontalScrollBar().value()
        is_visible = 0 <= playhead_x <= viewport.width()
        if is_visible:
            button.hide()
            return
        button.adjustSize()
        button.move(
            max(6, viewport.width() - button.width() - 10),
            self.RULER_HEIGHT + 6,
        )
        button.show()
        button.raise_()

    def _return_to_playhead(self) -> None:
        viewport = self.viewport()
        if viewport is None:
            return
        scroll_bar = self.horizontalScrollBar()
        target = int(self.CONTENT_LEFT_PAD + self._playhead * self.pixels_per_second - viewport.width() * 0.70)
        target = max(scroll_bar.minimum(), min(target, scroll_bar.maximum()))
        self._manual_navigation_active = False
        self._return_to_playhead_button.hide()
        if self._playhead_follow_animation.state() == QAbstractAnimation.Running:
            self._playhead_follow_animation.stop()
        self._playhead_follow_animation.setStartValue(scroll_bar.value())
        self._playhead_follow_animation.setEndValue(target)
        self._playhead_follow_animation.start()

    def set_voice_sync_mode(self, mode: str) -> None:
        """Update the active voice-timing sync mode and re-stack the
        tracks. Timeline Priority disables row stacking because the
        audio is always cut to the segment window.
        """
        mode_key = (mode or "").strip()
        if mode_key == self._voice_sync_mode:
            return
        self._voice_sync_mode = mode_key
        self._redraw()

    def _effective_min_pps(self) -> float:
        """Dynamic minimum pixels-per-second to allow fitting and zooming out on long media."""
        if self._duration <= 0.0:
            return float(self.MIN_PPS)
        viewport_w = (self.viewport().width() - self.CONTENT_LEFT_PAD - 40) if self.viewport() else 800
        viewport_w = max(200, viewport_w)
        fit_pps = viewport_w / max(1.0, self._duration)
        return max(0.001, min(float(self.MIN_PPS), fit_pps))

    def zoom_in(self) -> None:
        new_pps = min(float(self.MAX_PPS), self.pixels_per_second * 1.25)
        self.pixels_per_second = round(new_pps, 4) if new_pps < 1.0 else max(1, int(round(new_pps)))
        self._redraw()
        self.zoomChanged.emit(self.zoom_percent())

    def zoom_out(self) -> None:
        min_pps = self._effective_min_pps()
        new_pps = max(min_pps, self.pixels_per_second * 0.8)
        self.pixels_per_second = round(new_pps, 4) if new_pps < 1.0 else max(1, int(round(new_pps)))
        self._redraw()
        self.zoomChanged.emit(self.zoom_percent())

    def fit_timeline(self) -> None:
        if self._duration > 0.0:
            viewport = self.viewport()
            viewport_w = (viewport.width() - self.CONTENT_LEFT_PAD - 50) if viewport else 800
            viewport_w = max(200, viewport_w)
            target_pps = max(0.001, min(float(self.MAX_PPS), viewport_w / self._duration))
            self.pixels_per_second = round(target_pps, 4) if target_pps < 1.0 else max(1, int(round(target_pps)))
            self.horizontalScrollBar().setValue(0)
        self._redraw()
        self.zoomChanged.emit(self.zoom_percent())

    def toggle_fit_or_reset_zoom(self) -> None:
        if self._duration <= 0.0:
            self.reset_zoom()
            return
        viewport = self.viewport()
        viewport_w = (viewport.width() - self.CONTENT_LEFT_PAD - 50) if viewport else 800
        viewport_w = max(200, viewport_w)
        fit_pps = max(0.001, min(float(self.MAX_PPS), viewport_w / self._duration))
        # If currently within 15% of fit PPS, toggle back to 100% default zoom
        if abs(self.pixels_per_second - fit_pps) < 0.15 * max(0.001, fit_pps):
            self.reset_zoom()
        else:
            self.fit_timeline()

    def reset_zoom(self) -> None:
        self.pixels_per_second = self.DEFAULT_PPS
        self._redraw()
        self.zoomChanged.emit(100)

    def zoom_percent(self) -> int:
        return max(1, int(round(self.pixels_per_second / self.DEFAULT_PPS * 100)))

    def select_layer(self, layer_id: str) -> None:
        self._selected_layer_id = layer_id
        track, _layer = self._find_layer_by_id(layer_id)
        self._manual_subtitle_selection = bool(
            layer_id and track is not None and self._is_subtitle_track(track)
        )
        self.viewport().update()

    def _redraw(self) -> None:
        if not self._timeline:
            return
        self._overlap_layout_cache.clear()
        self._overlap_row_assignments.clear()
        tl = self._timeline
        tracks = [t for t in tl.tracks if self.is_track_shown_on_timeline(t)]
        # Recompute each track's height based on its layer count so
        # tracks with more layers (e.g. multiple blur regions) expand.
        for t in tracks:
            self._track_heights[t.id] = self._compute_track_height(t)
        # The ruler is painted as a sticky viewport overlay. Reserve one
        # additional ruler-height in the scene so QGraphicsView's vertical
        # scrollbar does not count that covered area as usable track space.
        # Without this, the final track stops underneath the horizontal
        # scrollbar and cannot be clicked even after scrolling to the end.
        total_h = self.RULER_HEIGHT * 2 + sum(
            self._track_heights.get(t.id, self.TRACK_DEFAULT_H) for t in tracks
        )
        content_w = int(round(self._duration * self.pixels_per_second))
        view_w = self.viewport().width() if self.viewport() else 800
        scene_w = max(self.CONTENT_LEFT_PAD + content_w + 50, view_w)
        self._scene.setSceneRect(0, 0, scene_w, total_h)
        self.layoutChanged.emit()
        self.viewport().update()

    def _refresh_scene_bounds_for_viewport(self) -> None:
        """Refresh scroll extents after a pure widget resize.

        Track/layer data has not changed in this path, so preserve the costly
        overlap-row cache used by long subtitle tracks.  Only the scene bounds
        and viewport paint need updating.
        """
        if not self._timeline:
            return
        tracks = [t for t in self._timeline.tracks if self.is_track_shown_on_timeline(t)]
        total_h = self.RULER_HEIGHT * 2 + sum(
            self._track_heights.get(t.id, self.TRACK_DEFAULT_H) for t in tracks
        )
        content_w = int(round(self._duration * self.pixels_per_second))
        view_w = self.viewport().width() if self.viewport() else 800
        scene_w = max(self.CONTENT_LEFT_PAD + content_w + 50, view_w)
        self._scene.setSceneRect(0, 0, scene_w, total_h)
        self.viewport().update()

    def _compute_duration(self, timeline: Timeline) -> float:
        dur = 0.0
        for track in timeline.tracks:
            for layer in track.layers:
                dur = max(dur, float(getattr(layer, "end", 0.0) or 0.0))
        return max(dur, 10.0)

    def _rebuild_track_heights(self) -> None:
        if not self._timeline:
            return
        for track in self._timeline.tracks:
            self._track_heights[track.id] = self._compute_track_height(track)

    def _compute_track_height(self, track) -> int:
        """Compute a track's height. Region tracks (Blur, Logo, Mask)
        allocate one full base slot per visible layer. Subtitle and dub tracks (overlap-stacked)
        use the same CHILD_TRACK_H slot for every row — primary and
        overlap-child rows are equally small — so the whole track stays
        compact.
        """
        base = int(getattr(track, "height", None) or self.TRACK_DEFAULT_H)
        if self._uses_layer_rows(track):
            num_layers = max(1, len([l for l in track.layers if l.visible]))
            # B1, L1, and M1 intentionally share the same compact row
            # size. Older B1 projects may carry a 100px track height.
            return self.REGION_TRACK_ROW_H * num_layers
        if self._should_overlap_stack(track):
            visible = [l for l in track.layers if l.visible]
            # Sort by start time for proper overlap detection
            visible_sorted = sorted(visible, key=lambda l: float(getattr(l, "start", 0.0)))
            _, num_rows = self._compute_overlap_rows(visible_sorted, track_id=track.id)
            return self.CHILD_TRACK_H * max(1, num_rows)
        return base

    @staticmethod
    def _is_blur_track(track) -> bool:
        name = (track.name or "").lower()
        prefix = name.split(" ")[0] if name else ""
        if prefix == "b1":
            return True
        return any(getattr(l, "type", None) == LayerType.BLUR
                   for l in getattr(track, "layers", []))

    @classmethod
    def _uses_layer_rows(cls, track) -> bool:
        """Whether every visible layer receives its own vertical row.

        B1 has always worked this way. L1, M1, and T1 layers commonly span the
        full video too, so without the same layout the last painted clip
        hides every earlier logo/mask layer and they cannot be selected.
        """
        if cls._is_blur_track(track):
            return True
        name = (getattr(track, "name", "") or "").lower()
        prefix = name.split(" ")[0] if name else ""
        if prefix in ("l1", "m1", "t1"):
            return True
        return any(
            getattr(layer, "type", None) in (LayerType.MASK, LayerType.TEXT)
            for layer in getattr(track, "layers", [])
        )

    @staticmethod
    def _is_subtitle_track(track) -> bool:
        track_type = getattr(track, "type", None)
        if track_type in (LayerType.SUBTITLE, LayerType.DUB_SUBTITLE):
            return True
        return any(
            getattr(l, "type", None) in (LayerType.SUBTITLE, LayerType.DUB_SUBTITLE)
            for l in getattr(track, "layers", [])
        )

    @staticmethod
    def _is_dub_track(track) -> bool:
        # Legacy A2 Dub name prefix kept for projects that still have
        # a separate audio track from the old two-track layout.
        name = (track.name or "").lower()
        prefix = name.split(" ")[0] if name else ""
        if prefix == "a2":
            return True
        return False

    def _should_overlap_stack(self, track) -> bool:
        """True for tracks whose overlapping layers should stack vertically
        inside the same track. The new TS1 DubSubtitle layout inherits
        the stacking; legacy A2 Dub still does.

        In Timeline Priority mode the audio is always cut to the
        segment window, so no two layers can overlap in audio time.
        Stacking is disabled and the track collapses to a single row.
        """
        is_subtitle = self._is_subtitle_track(track)
        if is_subtitle:
            sync_mode = (self._voice_sync_mode or "").strip().lower()
            if sync_mode == "timeline priority":
                return False
        return is_subtitle or self._is_dub_track(track)

    def _compute_overlap_rows(self, visible_layers, track_id=""):
        """Greedy overlap-aware row assignment.

        Returns (layer_rows, num_rows) where layer_rows is a list of
        row indices (0-based) in the same order as visible_layers.
        Dynamically allocates as many rows as needed to represent true
        concurrent overlapping segments without hardcoded limits.
        A new row is started only when a layer overlaps with every
        existing row's last segment. Used for subtitle tracks so
        overlapping Sub N layers stack vertically inside the same TS1
        track, mirroring how Blur 1 and Blur 2 stack inside B1.

        Overlap detection uses `_audio_end` from layer metadata when
        present (the actual TTS audio length), so a layer whose
        generated voice bleeds past its segment end still triggers
        row stacking. The bar itself is drawn from layer.start to
        layer.end — only the overlap comparison sees the audio end.
        """
        previous = self._overlap_row_assignments.get(str(track_id), {})
        row_intervals: list[list[tuple[float, float]]] = []
        row_starts: list[list[float]] = []
        layer_rows_by_id: dict[str, int] = {}

        def can_use(row_index, start, end):
            items = row_intervals[row_index]
            if not items:
                return True
            # Fast O(1) check for sequential cues
            if start >= items[-1][1] - 0.001:
                return True
            # Fallback to bisect for out-of-order/restored layers
            idx = bisect_right(row_starts[row_index], start)
            if idx > 0 and start < items[idx - 1][1] - 0.001:
                return False
            if idx < len(items) and end > items[idx][0] + 0.001:
                return False
            return True

        def assign(layer, preferred=None, *, force_preferred=False):
            try:
                start = float(getattr(layer, "start", 0.0))
                end = float(getattr(layer, "end", 0.0))
            except (TypeError, ValueError):
                start = end = 0.0
            audio_end = end
            meta = getattr(layer, "metadata", None) or {}
            if isinstance(meta, dict):
                raw = meta.get("_audio_end")
                if raw is not None:
                    try:
                        audio_end = max(end, float(raw))
                    except (TypeError, ValueError):
                        audio_end = end
            end = max(end, audio_end)

            row_index = -1
            if force_preferred and preferred is not None and preferred >= 0:
                while len(row_intervals) <= preferred:
                    row_intervals.append([])
                    row_starts.append([])
                row_index = preferred
            elif preferred is not None and 0 <= preferred < len(row_intervals):
                if can_use(preferred, start, end):
                    row_index = preferred

            if row_index < 0:
                for idx in range(len(row_intervals)):
                    if can_use(idx, start, end):
                        row_index = idx
                        break

            if row_index < 0:
                row_index = len(row_intervals)
                row_intervals.append([])
                row_starts.append([])

            items = row_intervals[row_index]
            if not items or start >= items[-1][0]:
                items.append((start, end))
                row_starts[row_index].append(start)
            else:
                idx = bisect_right(row_starts[row_index], start)
                items.insert(idx, (start, end))
                row_starts[row_index].insert(idx, start)
            layer_rows_by_id[str(getattr(layer, "id", ""))] = row_index

        ordered = sorted(visible_layers, key=lambda layer: float(getattr(layer, "start", 0.0)))
        active_drag = getattr(self, "_drag_state", None) or {}
        selected_id = str(active_drag.get("layer_id", "") or "")
        pinned_row = -1
        if str(active_drag.get("track_id", "") or "") == str(track_id):
            try:
                pinned_row = int(active_drag.get("row_index", -1))
            except (TypeError, ValueError):
                pinned_row = -1
        # Existing, non-selected layers retain their old rows first. A layer
        # being edited keeps the row it had when the drag began; collisions
        # are clamped by the drag code instead of moving it to another row.
        stable = []
        stable_ids = set()
        for layer in ordered:
            lid = str(getattr(layer, "id", ""))
            pref = previous.get(lid)
            if pref is not None and pref >= 0 and lid != selected_id:
                stable.append(layer)
                stable_ids.add(lid)
        adaptive = [layer for layer in ordered if str(getattr(layer, "id", "")) not in stable_ids]
        for layer in stable:
            assign(layer, previous.get(str(getattr(layer, "id", ""))))
        for layer in adaptive:
            is_selected = str(getattr(layer, "id", "")) == selected_id
            assign(
                layer,
                pinned_row if is_selected and pinned_row >= 0 else previous.get(str(getattr(layer, "id", ""))),
                force_preferred=bool(is_selected and pinned_row >= 0),
            )
        layer_rows = [layer_rows_by_id.get(str(getattr(layer, "id", "")), 0) for layer in ordered]
        self._overlap_row_assignments[str(track_id)] = layer_rows_by_id
        return layer_rows, max(1, len(row_intervals))

    def _drag_pinned_row(self, track, layer):
        """Return the subtitle row captured at drag start, if applicable."""
        drag = getattr(self, "_drag_state", None) or {}
        if (
            str(drag.get("layer_id", "") or "") != str(getattr(layer, "id", "") or "")
            or str(drag.get("track_id", "") or "") != str(getattr(track, "id", "") or "")
        ):
            return None
        try:
            row = int(drag.get("row_index", -1))
        except (TypeError, ValueError):
            return None
        return row if row >= 0 else None

    def _layer_row_index(self, track, layer) -> int:
        """Resolve the current overlap row before an edit begins."""
        if track is None or not self._should_overlap_stack(track):
            return 0
        visible = [item for item in track.layers if item.visible]
        row_map = self._overlap_row_assignments.get(str(track.id), {})
        if str(getattr(layer, "id", "")) not in row_map:
            self._compute_overlap_rows(visible, track_id=track.id)
            row_map = self._overlap_row_assignments.get(str(track.id), {})
        try:
            return max(0, int(row_map.get(str(getattr(layer, "id", "")), 0)))
        except (TypeError, ValueError):
            return 0

    def _clamp_layer_resize(self, track, layer, edge: str, value: float) -> float:
        """Keep an edited layer inside its current row's neighbors.

        Resizing is intentionally local: the edited layer stops at the
        neighboring layer boundary and no other layer is moved, trimmed, or
        reassigned to another row.
        """
        if track is None:
            return value
        visible = [item for item in track.layers if item.visible]
        row_map = self._overlap_row_assignments.get(str(track.id), {})
        if self._should_overlap_stack(track):
            if str(getattr(layer, "id", "")) not in row_map:
                self._compute_overlap_rows(visible, track_id=track.id)
                row_map = self._overlap_row_assignments.get(str(track.id), {})
        elif self._uses_layer_rows(track):
            # Each overlay row is independent, so it has no same-row
            # neighbors to constrain against.
            return value
        else:
            # A normal single-row track (including legacy layer tracks) uses
            # one collision row for all visible layers.
            row_map = {str(getattr(item, "id", "")): 0 for item in visible}
        row = self._drag_pinned_row(track, layer)
        if row is None:
            row = row_map.get(str(getattr(layer, "id", "")))
        if row is None:
            return value
        neighbors = [item for item in visible
                     if item is not layer and row_map.get(str(getattr(item, "id", ""))) == row]
        if edge == "left":
            left_boundary = max(
                (float(getattr(item, "end", 0.0) or 0.0) for item in neighbors
                 if float(getattr(item, "end", 0.0) or 0.0) <= float(layer.start) + 0.001),
                default=0.0,
            )
            return max(value, left_boundary)
        right_boundary = min(
            (float(getattr(item, "start", self._duration) or self._duration) for item in neighbors
             if float(getattr(item, "start", self._duration) or self._duration) >= float(layer.end) - 0.001),
            default=self._duration,
        )
        return min(value, right_boundary)

    def _clamp_subtitle_resize(self, track, layer, edge: str, value: float) -> float:
        """Backward-compatible alias for subtitle timing callers."""
        return self._clamp_layer_resize(track, layer, edge, value)

    def _clamp_layer_move(self, track, layer, start: float, end: float) -> float:
        """Clamp a body drag without moving any neighboring layer."""
        if track is None:
            return start
        visible = [item for item in track.layers if item.visible and item is not layer]
        row_map = self._overlap_row_assignments.get(str(track.id), {})
        if self._should_overlap_stack(track):
            if str(getattr(layer, "id", "")) not in row_map:
                self._compute_overlap_rows([*visible, layer], track_id=track.id)
                row_map = self._overlap_row_assignments.get(str(track.id), {})
            row = self._drag_pinned_row(track, layer)
            if row is None:
                row = row_map.get(str(getattr(layer, "id", "")))
            visible = [item for item in visible
                       if row_map.get(str(getattr(item, "id", ""))) == row]
        elif self._uses_layer_rows(track):
            return start
        else:
            # Single-row tracks constrain against every visible neighbor.
            pass
        duration = max(self.MIN_DUR, end - start)
        original_start = float(getattr(layer, "start", start) or start)
        original_end = float(getattr(layer, "end", end) or end)
        lower = 0.0
        upper = self._duration - duration
        for item in visible:
            item_start = float(getattr(item, "start", 0.0) or 0.0)
            item_end = float(getattr(item, "end", item_start) or item_start)
            if item_end <= original_start + 0.001:
                lower = max(lower, item_end)
            elif item_start >= original_end - 0.001:
                upper = min(upper, item_start - duration)
        return max(lower, min(start, upper))

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._timeline:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)

        tracks = [t for t in self._timeline.tracks if self.is_track_shown_on_timeline(t)]
        scroll_x = self.horizontalScrollBar().value()
        # Apply the vertical scroll offset so the tracks scroll within
        # the viewport while the ruler stays sticky at the top.
        scroll_y = self.verticalScrollBar().value()
        view_w = self.viewport().width()

        y = self.RULER_HEIGHT - scroll_y

        for track in tracks:
            th = self._track_heights.get(track.id, self.TRACK_DEFAULT_H)
            self._draw_track_body(painter, track, scroll_x, y, th)
            self._draw_track_layers(painter, track, scroll_x, y, th)
            y += th

        # Playhead spans the full scene height (uses scene coords)
        self._draw_selection_range(painter, scroll_x)
        self._draw_playhead(painter, scroll_x)

        # Draw the sticky ruler LAST so it stays on top of any
        # scrolled tracks that might overlap the ruler area.
        self._draw_ruler_sticky(painter, scroll_x, view_w)

        painter.end()

    def _draw_ruler_sticky(self, painter: QPainter, scroll_x: int, view_w: int, scroll_y: int = 0) -> None:
        # The ruler is sticky at the top of the viewport (scroll_y
        # is accepted for API compatibility but not applied here).
        ruler_y = 0
        painter.fillRect(0, ruler_y, view_w, self.RULER_HEIGHT, QColor("#0a0f1a"))
        painter.setPen(QColor("#35506f"))
        painter.drawLine(0, ruler_y + self.RULER_HEIGHT - 1, view_w, ruler_y + self.RULER_HEIGHT - 1)

        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        major_interval = self._tick_interval()

        pps = max(0.001, self.pixels_per_second)
        if major_interval >= 5 and (major_interval / 5.0) * pps >= 10.0:
            tick_step = major_interval / 5.0
        elif major_interval >= 2 and (major_interval / 2.0) * pps >= 10.0:
            tick_step = major_interval / 2.0
        else:
            tick_step = float(major_interval)

        start_sec = max(0.0, (float(scroll_x) - self.CONTENT_LEFT_PAD - 20.0) / pps)
        t = math.floor(start_sec / tick_step) * tick_step
        max_t = self._duration + 5.0

        while t <= max_t:
            x = self.CONTENT_LEFT_PAD + int(round(t * self.pixels_per_second)) - scroll_x
            if x > view_w + 50:
                break
            if x > -40:
                is_major = (abs(round(t / major_interval) * major_interval - t) < 1e-4)
                if is_major:
                    painter.setPen(QColor("#35506f"))
                    painter.drawLine(x, ruler_y + self.RULER_HEIGHT - 10, x, ruler_y + self.RULER_HEIGHT)
                    if t >= 3600.0:
                        h = int(t // 3600)
                        m = int((t % 3600) // 60)
                        s = int(t % 60)
                        ts = f"{h}:{m:02d}:{s:02d}"
                    else:
                        ts = f"{int(t // 60)}:{int(t % 60):02d}"
                    painter.setPen(QColor("#6b8cb8"))
                    painter.drawText(int(x) + 2, ruler_y + 16, ts)
                else:
                    painter.setPen(QColor("#1e2d42"))
                    painter.drawLine(x, ruler_y + self.RULER_HEIGHT - 5, x, ruler_y + self.RULER_HEIGHT)
            t += tick_step

    def _draw_track_header(self, painter: QPainter, track: Track,
                           y: int, h: int) -> None:
        header_rect = QRectF(0, y, self.TRACK_HEADER_W, h)
        painter.fillRect(header_rect, QColor("#142030"))
        painter.setPen(QColor("#1e2d42"))
        painter.drawRect(header_rect)

        font = QFont("Segoe UI", 9, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#6b8cb8"))
        icon = {"video": "\u25b6", "audio": "\u266b", "subtitle": "T",
                "text": "Aa", "image": "\u25a3", "sticker": "\u2605",
                "blur": "\u25a3"}
        label = f"  {icon.get(track.type.value, '?')} {track.name or track.type.value.title()}"
        painter.drawText(QRectF(4, y + 4, self.TRACK_HEADER_W - 8, self.TRACK_LABEL_H),
                         Qt.AlignLeft, label)

        if track.locked:
            painter.setPen(QColor("#e04040"))
            painter.drawText(QRectF(self.TRACK_HEADER_W - 24, y + 4, 20, self.TRACK_LABEL_H),
                             Qt.AlignRight, "\U0001f512")

    def _draw_track_body(self, painter: QPainter, track: Track,
                         scroll_x: int, y: int, h: int) -> None:
        view_w = self.viewport().width()
        body_rect = QRectF(0, y, view_w, h)
        painter.fillRect(body_rect, QColor("#0a0f1a"))
        painter.setPen(QColor("#1e2d42"))
        painter.drawRect(body_rect)

        painter.setPen(QColor("#1e2d42"))
        # Draw only interval markers that can reach the current viewport.
        # Align with ruler tick interval so gridlines match time markers
        # and don't overwhelm the viewport on long projects or zoomed-out views.
        interval = max(1, self._tick_interval())
        pps = max(0.001, self.pixels_per_second)
        start_sec = max(0.0, (float(scroll_x) - self.CONTENT_LEFT_PAD) / pps)
        first_tick = max(interval, int(math.floor(start_sec / interval)) * interval)

        t = first_tick
        duration_limit = int(self._duration) + 1
        while t <= duration_limit:
            x = self.CONTENT_LEFT_PAD + int(round(t * self.pixels_per_second)) - scroll_x
            if x > view_w:
                break
            if x > 0:
                painter.drawLine(x, y, x, y + h)
            t += interval

    def _layers_intersecting_viewport(
        self,
        sorted_layers: list,
        sorted_starts: list[float],
        prefix_max_ends: list[float],
        scroll_x: int,
        view_w: int,
    ) -> list:
        """Return only cached subtitle layers that can paint in this viewport.

        ``prefix_max_ends`` stays monotonic, so it also finds cues that
        started before the viewport but extend into it.  Include one bar's
        minimum visual width as a small left-side guard; this preserves the
        existing 20-pixel minimum-width drawing behavior at the viewport edge.
        """
        if not sorted_layers or self.pixels_per_second <= 0:
            return []
        viewport_start = (float(scroll_x) - self.CONTENT_LEFT_PAD) / self.pixels_per_second
        viewport_end = (float(scroll_x + max(0, view_w)) - self.CONTENT_LEFT_PAD) / self.pixels_per_second
        minimum_bar_seconds = 20.0 / self.pixels_per_second
        left_time = max(0.0, viewport_start - minimum_bar_seconds)
        first = bisect_right(prefix_max_ends, left_time)
        last = bisect_right(sorted_starts, max(0.0, viewport_end))
        if first >= last:
            return []
        return sorted_layers[first:last]

    def _tick_interval(self) -> int:
        """Calculate optimal major tick interval in seconds based on current zoom level."""
        pps = max(0.001, self.pixels_per_second)
        candidate_intervals = [
            1, 2, 5, 10, 15, 30,           # seconds
            60, 120, 300, 600, 900, 1800,  # 1m, 2m, 5m, 10m, 15m, 30m
            3600, 7200, 14400, 28800       # 1h, 2h, 4h, 8h
        ]
        for interval in candidate_intervals:
            if interval * pps >= 60.0:
                return interval
        return candidate_intervals[-1]

    def _draw_track_layers(self, painter: QPainter, track: Track,
                           scroll_x: int, y: int, h: int) -> None:
        margin = 4
        view_w = self.viewport().width()
        uses_layer_rows = self._uses_layer_rows(track)
        overlap_stack = self._should_overlap_stack(track)
        # Force every bar on a subtitle track to share the same orange
        # color, regardless of the layer's runtime class or type. This
        # guarantees the track reads as a single uniform subtitle strip
        # even if a layer was hydrated with the wrong type (e.g. a
        # stale SubtitleLayer rather than a DubSubtitleLayer from an
        # older project file).
        force_subtitle_color = self._is_subtitle_track(track)
        if overlap_stack:
            cached_layout = self._overlap_layout_cache.get(track.id)
            if cached_layout is None:
                visible_layers = [layer for layer in track.layers if layer.visible]
                # Sort by start time for proper overlap detection. This is
                # deliberately cached: doing it for a long TS1 on every
                # playhead repaint was a major source of stutter.
                visible_layers_sorted = sorted(
                    visible_layers, key=lambda layer: float(getattr(layer, "start", 0.0))
                )
                layer_rows, cached_num_rows = self._compute_overlap_rows(visible_layers_sorted, track_id=track.id)
                # ``list.index(layer)`` inside the draw loop made subtitle-
                # track painting quadratic. Precompute the assignment once.
                layer_row_by_id = {
                    str(getattr(layer, "id", "")): row
                    for layer, row in zip(visible_layers_sorted, layer_rows)
                }
                sorted_starts = [float(getattr(layer, "start", 0.0) or 0.0)
                                 for layer in visible_layers_sorted]
                prefix_max_ends: list[float] = []
                max_end = float("-inf")
                for layer in visible_layers_sorted:
                    try:
                        layer_end = float(getattr(layer, "end", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        layer_end = 0.0
                    meta = getattr(layer, "metadata", None)
                    if isinstance(meta, dict):
                        raw_ae = meta.get("_audio_end")
                        if raw_ae is not None:
                            try:
                                layer_end = max(layer_end, float(raw_ae))
                            except (TypeError, ValueError):
                                pass
                    max_end = max(max_end, layer_end)
                    prefix_max_ends.append(max_end)
                cached_layout = (
                    layer_row_by_id,
                    max(1, cached_num_rows),
                    visible_layers_sorted,
                    sorted_starts,
                    prefix_max_ends,
                )
                self._overlap_layout_cache[track.id] = cached_layout
            (
                layer_row_by_id,
                num_rows,
                visible_layers_sorted,
                sorted_starts,
                prefix_max_ends,
            ) = cached_layout
            # TS1 can contain thousands of cues.  During playback the
            # timeline repaints roughly five times per second, but only a
            # handful of cues can reach the current viewport.  Find that
            # subset in O(log n + visible cues), including any long cue that
            # began before the viewport, instead of traversing all cues.
            visible_layers = self._layers_intersecting_viewport(
                visible_layers_sorted,
                sorted_starts,
                prefix_max_ends,
                scroll_x,
                view_w,
            )
            num_rows = max(1, num_rows)
            # All rows (primary + overlap-child) share the same small
            # CHILD_TRACK_H height so the whole track stays compact.
            row_slots: list[tuple[int, int]] = []
            cursor = y + margin
            for r in range(num_rows):
                row_slots.append((cursor, self.CHILD_TRACK_H))
                cursor += self.CHILD_TRACK_H
        else:
            visible_layers = [layer for layer in track.layers if layer.visible]
            num_layers = max(1, len(visible_layers))
            row_h = (h - margin * 2) / num_layers if num_layers > 0 else h
        visible_row_index = 0
        pps = max(0.001, self.pixels_per_second)
        visible_count = len(visible_layers)
        for i, layer in enumerate(visible_layers):
            x = self.CONTENT_LEFT_PAD + int(round(layer.start * pps)) - scroll_x
            actual_w = max(1, int(round(layer.duration * pps)))
            if pps < 30.0:
                w = actual_w
            else:
                w = max(actual_w, 20)

            # Prevent a bar from overlapping or bleeding into the next bar on the same row
            if overlap_stack:
                row = layer_row_by_id.get(str(getattr(layer, "id", "")), 0)
                for j in range(i + 1, min(i + 10, visible_count)):
                    if layer_row_by_id.get(str(getattr(visible_layers[j], "id", "")), 0) == row:
                        next_x = self.CONTENT_LEFT_PAD + int(round(visible_layers[j].start * pps)) - scroll_x
                        if next_x > x:
                            max_allowed = (next_x - x - 1) if pps < 30.0 else (next_x - x)
                            w = max(1, min(w, max_allowed))
                        break
            elif not uses_layer_rows and i + 1 < visible_count:
                next_x = self.CONTENT_LEFT_PAD + int(round(visible_layers[i + 1].start * pps)) - scroll_x
                if next_x > x:
                    max_allowed = (next_x - x - 1) if pps < 30.0 else (next_x - x)
                    w = max(1, min(w, max_allowed))
            overflow_w = 0
            raw_ae = None
            if isinstance(getattr(layer, "metadata", None), dict):
                raw_ae = layer.metadata.get("_audio_end")
            if raw_ae is not None:
                try:
                    ex = float(raw_ae) - float(getattr(layer, "end", 0.0) or 0.0)
                    if ex > 0.05 and float(getattr(layer, "extended_duration", 0.0) or 0.0) <= 0.001:
                        overflow_w = int(round(ex * pps))
                except (TypeError, ValueError):
                    pass
            # Off-screen bars have no visual effect. Skip row assignment,
            # QPainter path creation, labels, and glyph work for them.
            clip_x = max(x, 0)
            clip_w = min(x + w + overflow_w, view_w) - clip_x
            if clip_w <= 0:
                visible_row_index += 1
                continue
            if overlap_stack:
                row = layer_row_by_id.get(str(getattr(layer, "id", "")), 0)
                bar_y, slot_h = row_slots[row]
                bar_h = max(slot_h - margin * 2, 8)
            elif uses_layer_rows:
                z = max(0, visible_row_index)
                z = min(z, num_layers - 1)
                bar_y = y + margin + z * row_h
                bar_h = max(row_h - 2, 8)
            else:
                bar_y = y + margin
                bar_h = h - margin * 2
            is_selected = layer.id == self._selected_layer_id
            track_name = (getattr(track, "name", "") or "").split(" ")[0]
            is_subtitle_track_name = track_name in ("TS1", "S1")
            if layer.type == LayerType.BLUR:
                self._draw_blur_layer_bar(painter, layer, x, bar_y, w, bar_h, is_selected)
            else:
                self._draw_standard_layer_bar(
                    painter, layer, x, bar_y, w, bar_h, view_w,
                    is_selected, force_subtitle_color=force_subtitle_color,
                    force_subtitle_track=is_subtitle_track_name,
                    hide_label=(track.type == LayerType.AUDIO),
                )
            # V1/A1 visuals are precomputed when media is opened. Drawing
            # uses only the cached data and clips to the current viewport, so
            # playback never decodes video or reads audio samples.
            if track.type == LayerType.VIDEO:
                self._draw_video_thumbnails(painter, x, bar_y, w, bar_h, view_w)
                self._draw_video_timewarp_overlays(painter, x, bar_y, w, bar_h, view_w)
            elif track.type == LayerType.AUDIO:
                self._draw_waveform(painter, x, bar_y, w, bar_h, view_w)
            if is_selected and track.type in (LayerType.VIDEO, LayerType.AUDIO):
                painter.setPen(QPen(QColor("#4a8cff"), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(QRectF(x, bar_y, w, bar_h), 4, 4)
            visible_row_index += 1

    def _draw_waveform(self, painter: QPainter, x: int, y: float, w: int,
                       h: float, view_w: int) -> None:
        samples = self._waveform_samples
        duration_s = self._waveform_duration_s or self._duration
        if not samples or duration_s <= 0.0 or h <= 4:
            return
        left = max(0, x)
        right = min(view_w, x + w)
        if right <= left:
            return
        center_y = y + h / 2.0
        max_amp = max(2.0, (h - 8.0) / 2.0)
        painter.save()
        painter.setClipRect(QRectF(left, y, right - left, h))
        # Teal-on-teal styling keeps A1 cohesive with the application while
        # the darker waveform remains legible on its green track.
        painter.setPen(QPen(QColor("#248d82"), 1))
        painter.drawLine(left, int(center_y), right, int(center_y))
        # This is a single vertical gradient brush reused for every peak,
        # giving A1 a subtle modern highlight without changing sample count
        # or doing any additional media processing.
        waveform_gradient = QLinearGradient(0, y, 0, y + h)
        waveform_gradient.setColorAt(0.0, QColor(18, 108, 102, 135))
        waveform_gradient.setColorAt(0.5, QColor(41, 151, 136, 185))
        waveform_gradient.setColorAt(1.0, QColor(8, 67, 76, 155))
        soft_pen = QPen(QBrush(waveform_gradient), 3)
        soft_pen.setCapStyle(Qt.RoundCap)
        detail_pen = QPen(QColor("#083946"), 1)
        detail_pen.setCapStyle(Qt.RoundCap)
        # One inexpensive vertical stroke per two display pixels. Sample
        # lookup is proportional to the viewport width, never video length.
        strokes = []
        for pixel_x in range(left, right, 2):
            time_s = ((pixel_x - x) / max(1, w)) * duration_s
            index = min(len(samples) - 1, max(0, int((time_s / duration_s) * len(samples))))
            amplitude = samples[index] * max_amp
            strokes.append((pixel_x, int(center_y - amplitude), int(center_y + amplitude)))
        painter.setPen(soft_pen)
        for pixel_x, top_y, bottom_y in strokes:
            painter.drawLine(pixel_x, top_y, pixel_x, bottom_y)
        painter.setPen(detail_pen)
        for pixel_x, top_y, bottom_y in strokes:
            painter.drawLine(pixel_x, top_y, pixel_x, bottom_y)
        painter.restore()

    def _draw_video_thumbnails(self, painter: QPainter, x: int, y: float, w: int,
                               h: float, view_w: int) -> None:
        thumbnails = self._video_thumbnails
        if not thumbnails or self._duration <= 0.0 or h <= 8:
            return
        left = max(0, x)
        right = min(view_w, x + w)
        if right <= left:
            return
        target_h = max(12, int(h - 8))
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setClipRect(QRectF(left, y, right - left, h))
        for index, (timestamp_s, pixmap) in enumerate(thumbnails):
            block_end_s = (
                thumbnails[index + 1][0]
                if index + 1 < len(thumbnails)
                else self._duration
            )
            block_left = x + int(timestamp_s * self.pixels_per_second)
            block_right = x + int(block_end_s * self.pixels_per_second)
            if block_right <= left or block_left >= right:
                continue
            source_w = max(1, int(pixmap.width()))
            source_h = max(1, int(pixmap.height()))
            # Avoid turning one small cached thumbnail into an enormous,
            # blurry block. Cap horizontal enlargement at 2x its cached
            # width, then repeat that same frame when the time block is wider.
            max_tile_w = max(48, source_w * 2)
            tile_left = block_left
            while tile_left < block_right:
                tile_right = min(block_right, tile_left + max_tile_w)
                if tile_right > left and tile_left < right:
                    target_w = max(1, tile_right - tile_left)
                    target_ratio = target_w / max(1, target_h)
                    source_ratio = source_w / source_h
                    if source_ratio > target_ratio:
                        # Centered cover crop: remove equal left/right excess.
                        crop_h = source_h
                        crop_w = crop_h * target_ratio
                        crop_x = (source_w - crop_w) / 2.0
                        crop_y = 0.0
                    else:
                        # Centered cover crop for tall source frames.
                        crop_w = source_w
                        crop_h = crop_w / target_ratio
                        crop_x = 0.0
                        crop_y = (source_h - crop_h) / 2.0
                    rect = QRectF(tile_left, y + 4, target_w, target_h)
                    painter.drawPixmap(rect, pixmap, QRectF(crop_x, crop_y, crop_w, crop_h))
                tile_left = tile_right
            # A narrow translucent transition softens the hand-off to the
            # next cached source frame without blurring either thumbnail.
            if left < block_right < right:
                fade_w = 14
                edge_fade = QLinearGradient(block_right - fade_w, 0, block_right + fade_w, 0)
                edge_fade.setColorAt(0.0, QColor(10, 18, 30, 0))
                edge_fade.setColorAt(0.5, QColor(10, 18, 30, 44))
                edge_fade.setColorAt(1.0, QColor(10, 18, 30, 0))
                painter.fillRect(QRectF(block_right - fade_w, y + 4, fade_w * 2, target_h), QBrush(edge_fade))
                painter.setPen(QPen(QColor(207, 232, 239, 42), 1))
                painter.drawLine(block_right, int(y + 4), block_right, int(y + 4 + target_h))
        painter.restore()

    def _draw_video_timewarp_overlays(
        self, painter: QPainter, x: int, y: float, w: int, h: float, view_w: int
    ) -> None:
        spans = self.compute_warp_timeline_spans()
        if not spans or self._duration <= 0.0 or h <= 8:
            return
        pps = max(0.001, self.pixels_per_second)
        scroll_x = self.horizontalScrollBar().value() if self.horizontalScrollBar() else 0

        painter.save()
        clip_left = max(0, x)
        clip_right = min(view_w, x + w)
        if clip_right <= clip_left:
            painter.restore()
            return
        painter.setClipRect(QRectF(clip_left, y, clip_right - clip_left, h))

        for span in spans:
            t_start = span["t_start"]
            t_end = span["t_end"]
            w_type = span["type"]
            px_start = self.CONTENT_LEFT_PAD + int(round(t_start * pps)) - scroll_x
            px_end = self.CONTENT_LEFT_PAD + int(round(t_end * pps)) - scroll_x
            px_w = max(4, px_end - px_start)

            if px_end < 0 or px_start > view_w:
                continue

            rect = QRectF(px_start, y + 2, px_w, max(4.0, h - 4))

            if w_type == "slow":
                fill_color = QColor(139, 92, 246, 65)
                border_color = QColor(167, 139, 250, 200)
                hatch_color = QColor(196, 181, 253, 40)
                badge_bg = QColor(67, 24, 115, 220)
                badge_fg = QColor("#e9d5ff")
            else:
                fill_color = QColor(6, 182, 212, 65)
                border_color = QColor(45, 212, 191, 200)
                hatch_color = QColor(110, 231, 214, 40)
                badge_bg = QColor(14, 66, 80, 220)
                badge_fg = QColor("#a7f3d0")

            painter.fillRect(rect, fill_color)

            hatch_brush = QBrush(hatch_color, Qt.BDiagPattern)
            painter.fillRect(rect, hatch_brush)

            painter.setPen(QPen(border_color, 1.5))
            painter.drawLine(int(px_start), int(y + 2), int(px_end), int(y + 2))
            painter.drawLine(int(px_start), int(y + h - 2), int(px_end), int(y + h - 2))

            painter.setPen(QPen(QColor(255, 255, 255, 230), 2))
            painter.drawLine(int(px_start), int(y), int(px_start), int(y + h))
            painter.drawLine(int(px_end), int(y), int(px_end), int(y + h))

            if px_w >= 26:
                badge_text = span["badge_text"]
                font = QFont("Segoe UI", 7, QFont.Bold)
                painter.setFont(font)
                text_rect = painter.fontMetrics().boundingRect(badge_text)
                pill_w = text_rect.width() + 8
                pill_h = 15
                if px_w < pill_w:
                    short_text = f"🐢 {span['speed']:.2f}x" if w_type == "slow" else f"⏸ +{span['duration']:.1f}s"
                    text_rect = painter.fontMetrics().boundingRect(short_text)
                    if px_w >= text_rect.width() + 6:
                        badge_text = short_text
                        pill_w = text_rect.width() + 6
                    else:
                        continue
                pill_rect = QRectF(px_start + 3, y + 4, pill_w, pill_h)
                pill_path = QPainterPath()
                pill_path.addRoundedRect(pill_rect, 3, 3)
                painter.fillPath(pill_path, badge_bg)
                painter.setPen(QPen(border_color, 1))
                painter.drawPath(pill_path)
                painter.setPen(badge_fg)
                painter.drawText(pill_rect, Qt.AlignCenter, badge_text)

        painter.restore()

    def _draw_standard_layer_bar(self, painter, layer, x, y, w, h, view_w, is_selected, is_overflow_row: bool = False, force_subtitle_color: bool = False, force_subtitle_track: bool = False, hide_label: bool = False):
        # Every subtitle bar (DubSubtitleLayer, SubtitleLayer, or any
        # layer drawn on the TS1 track) uses the exact same fill +
        # border constants so the track reads as one uniform subtitle
        # strip regardless of which row the layer is on or whether
        # it's the currently playing/selected segment. No lighter()
        # or darker() is called on the fill — selection is shown only
        # by an accent border drawn on top.
        # Use type-based check as the primary signal so the fill is
        # uniform even if the layer was hydrated as a plain BaseLayer
        # (whose default type is VIDEO) by an older class map. The
        # isinstance and track-name checks are kept as belt-and-braces
        # for layers that have already been re-instantiated correctly.
        # The dub_text attribute sniff is a final fallback so a
        # pre-existing DubSubtitleLayer whose `type` was clobbered
        # still renders as a subtitle bar.
        layer_type = getattr(layer, "type", None)
        is_subtitle_type = layer_type in (LayerType.SUBTITLE, LayerType.DUB_SUBTITLE)
        layer_metadata = getattr(layer, "metadata", None) or {}
        segment_metadata = layer_metadata.get("_seg_dict", {}) if isinstance(layer_metadata, dict) else {}
        speaker = str(
            (segment_metadata.get("speaker", "") if isinstance(segment_metadata, dict) else "")
            or (layer_metadata.get("speaker", "") if isinstance(layer_metadata, dict) else "")
            or ""
        ).strip()
        has_dub_marker = bool(
            getattr(layer, "dub_text", None) or getattr(layer, "_seg_dict", None)
        )
        if speaker:
            # Diarization can yield more than a handful of speakers.  A
            # fixed palette repeats colors and makes distinct people look the
            # same, so derive a stable, well-separated hue from each ID.
            suffix = speaker.rsplit("_", 1)[-1]
            try:
                speaker_index = int(suffix)
            except (TypeError, ValueError):
                speaker_index = sum(ord(char) for char in speaker)
            fill = QColor.fromHsv((speaker_index * 137 + 20) % 360, 155, 205)
            border = fill.darker(140)
        elif is_subtitle_type or force_subtitle_color or force_subtitle_track or has_dub_marker:
            fill = QColor(201, 107, 42)   # #c96b2a — exact RGB, no derivation
            border = QColor(141, 75, 29)  # #8d4b1d — color.darker(140) baked in
        else:
            fill = self._layer_color(layer.type)
            border = fill.darker(140)

        # Fast path for low-zoom overview bars (Level of Detail)
        if w <= 6:
            painter.fillRect(QRectF(x, y, max(1, w), h), fill)
            if is_selected:
                painter.setPen(QPen(QColor("#4a8cff"), 1))
                painter.drawRect(QRectF(x, y, max(1, w), h))
            elif speaker and speaker == self._highlighted_speaker:
                painter.setPen(QPen(QColor("#ffe082"), 1))
                painter.drawRect(QRectF(x, y, max(1, w), h))
            elif w >= 2:
                # Distinct divider line on the right edge so adjacent bars remain visually separated
                painter.setPen(QPen(border, 1))
                painter.drawLine(int(x + w - 1), int(y), int(x + w - 1), int(y + h))
            return

        # Check for video time-warp / freeze frame extension
        extended_duration = 0.0
        warp_type = "freeze"
        warp_speed = None
        if isinstance(layer_metadata, dict):
            extended_duration = float(layer_metadata.get("extended_duration", 0.0) or 0.0)
            warp_type = layer_metadata.get("warp_type", warp_type)
            warp_speed = layer_metadata.get("warp_speed", warp_speed)
            if extended_duration <= 0.0:
                extended_duration = float(segment_metadata.get("extended_duration", 0.0) or 0.0)
                warp_type = segment_metadata.get("warp_type", warp_type)
                warp_speed = segment_metadata.get("warp_speed", warp_speed)
        if extended_duration <= 0.0:
            extended_duration = float(getattr(layer, "extended_duration", 0.0) or 0.0)

        # Check for voiceover audio overflowing past segment end (needs Khớp Voice)
        raw_audio_end = None
        if isinstance(layer_metadata, dict):
            raw_audio_end = layer_metadata.get("_audio_end")
            if raw_audio_end is None and isinstance(segment_metadata, dict):
                raw_audio_end = segment_metadata.get("_audio_end")
        if raw_audio_end is None:
            raw_audio_end = getattr(layer, "_audio_end", None)

        excess_voice = 0.0
        layer_end_val = float(getattr(layer, "end", 0.0) or 0.0)
        if raw_audio_end is not None and extended_duration <= 0.001:
            try:
                a_end = float(raw_audio_end)
                if a_end > layer_end_val + 0.05:
                    excess_voice = round(a_end - layer_end_val, 2)
            except (TypeError, ValueError):
                excess_voice = 0.0

        rect = QRectF(x, y, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)

        w_ext = 0
        w_base = w
        if extended_duration > 0.0:
            w_ext = int(round(extended_duration * self.pixels_per_second))
            # Ensure base part has at least 15px so text/cue is visible
            w_ext = min(w_ext, max(0, w - 15))
            w_base = w - w_ext

        if w_ext > 4:
            base_rect = QRectF(x, y, w_base, h)
            ext_rect = QRectF(x + w_base, y, w_ext, h)

            # Draw base bar
            base_path = QPainterPath()
            base_path.addRoundedRect(base_rect, 4, 4)
            painter.fillPath(base_path, fill)
            painter.setPen(QPen(border, 1))
            painter.drawPath(base_path)

            # Draw extended tail with distinct pattern: purple for slow, cyan for freeze
            ext_path = QPainterPath()
            ext_path.addRoundedRect(ext_rect, 4, 4)
            if warp_type == "slow":
                ext_fill = QColor(76, 29, 149)
                ext_border = QColor(139, 92, 246)
                hatch_brush = QBrush(QColor(196, 181, 253, 50), Qt.BDiagPattern)
                fg_color = QColor("#e9d5ff")
                sp_str = f"{float(warp_speed):.2f}x" if warp_speed else "slow"
                badge_text = f"🐢 {sp_str}" if w_ext < 50 else f"🐢 {sp_str} (+{extended_duration:.1f}s)"
            else:
                ext_fill = QColor(14, 66, 80)
                ext_border = QColor(20, 115, 135)
                hatch_brush = QBrush(QColor(110, 231, 214, 50), Qt.BDiagPattern)
                fg_color = QColor("#a7f3d0")
                badge_text = f"⏸ +{extended_duration:.1f}s" if w_ext >= 50 else f"+{extended_duration:.1f}s"

            painter.fillPath(ext_path, ext_fill)
            painter.fillPath(ext_path, hatch_brush)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(ext_border, 1))
            painter.drawPath(ext_path)

            # Draw icon/duration on extended tail
            if w_ext >= 24:
                painter.setPen(fg_color)
                font_ext = QFont("Segoe UI", 7, QFont.Bold)
                painter.setFont(font_ext)
                painter.drawText(ext_rect, Qt.AlignCenter, badge_text)
        elif excess_voice > 0.05:
            w_overflow = int(round(excess_voice * self.pixels_per_second))

            # Draw base bar with warning amber border if not selected
            painter.fillPath(path, fill)
            warning_border = QColor("#f59e0b") if not is_selected else border
            painter.setPen(QPen(warning_border, 1.5 if not is_selected else 1))
            painter.drawPath(path)

            # Draw overflow ghost tail extending past the segment end
            if w_overflow > 4:
                overflow_rect = QRectF(x + w, y, w_overflow, h)
                overflow_path = QPainterPath()
                overflow_path.addRoundedRect(overflow_rect, 4, 4)

                # Warm dark amber background + diagonal hatch
                overflow_fill = QColor(68, 42, 10, 200)
                overflow_border = QColor(245, 158, 11)  # #f59e0b Amber
                painter.fillPath(overflow_path, overflow_fill)

                hatch_brush = QBrush(QColor(251, 191, 36, 75), Qt.BDiagPattern)
                painter.fillPath(overflow_path, hatch_brush)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(overflow_border, 1, Qt.DashLine))
                painter.drawPath(overflow_path)

                # Badge text inside the overflow tail
                if w_overflow >= 22:
                    painter.setPen(QColor("#fde68a"))
                    font_overflow = QFont("Segoe UI", 7, QFont.Bold)
                    painter.setFont(font_overflow)
                    badge_text = f"⚡ +{excess_voice:.1f}s" if w_overflow >= 45 else f"+{excess_voice:.1f}s"
                    painter.drawText(overflow_rect, Qt.AlignCenter, badge_text)
        else:
            painter.fillPath(path, fill)
            painter.setPen(QPen(border, 1))
            painter.drawPath(path)

        if w > 40 and not hide_label:
            painter.setPen(QColor("#ffffff"))
            font = QFont("Segoe UI", 8)
            painter.setFont(font)
            if getattr(layer, "type", None) == LayerType.DUB_SUBTITLE:
                # Default: show dub_text (the voice-spoken text) on the
                # timeline bar so the user sees what the dub voice is
                # actually saying. Fall back to text, then layer name.
                label = (
                    str(getattr(layer, "dub_text", "") or "").strip()
                    or str(getattr(layer, "text", "") or "").strip()
                    or layer.name
                )
            else:
                label = layer.name or layer.type.value.title()
            short_label = os.path.basename(label) if os.path.sep in label else label
            max_label_w = min(w_base - 8, view_w - x - 4) if w_ext > 4 else min(w - 8, view_w - x - 4)
            text_rect = QRectF(x + 4, y, max(max_label_w, 10), h)
            elided = painter.fontMetrics().elidedText(short_label, Qt.ElideRight, int(text_rect.width()))
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)

            # Audio glyph on DubSubtitleLayer bars that have generated
            # voice audio. Small speaker shape on the right edge of
            # the bar.
            if (
                getattr(layer, "type", None) == LayerType.DUB_SUBTITLE
                and getattr(layer, "audio_path", "")
                and h >= 14
            ):
                # Tint glyph amber if voice is overflowing and needs sync
                glyph_x = x + w_base - 14 if w_ext > 4 else x + w - 14
                glyph_color = QColor("#fbbf24") if excess_voice > 0.05 else QColor("#ffffff")
                self._draw_audio_glyph(painter, glyph_x, y + (h - 10) / 2, glyph_color)
                painter.setBrush(Qt.NoBrush)

        if is_selected:
            painter.setPen(QPen(QColor("#4a8cff"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        elif speaker and speaker == self._highlighted_speaker:
            painter.setPen(QPen(QColor("#ffe082"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

    @staticmethod
    def _draw_audio_glyph(painter, x, y, color):
        """Draw a small speaker glyph at (x, y) to indicate the layer
        has generated voice audio. The colour is lightened to be
        visible on the bar fill.
        """
        from PySide6.QtGui import QColor as _QC
        glyph_color = _QC(
            min(color.red() + 120, 255),
            min(color.green() + 120, 255),
            min(color.blue() + 120, 255),
        )
        painter.setPen(glyph_color)
        painter.setBrush(glyph_color)
        x0 = float(x)
        y0 = float(y)
        h = 10.0
        w_box = 4.0
        # Speaker cone
        speaker = QPainterPath()
        speaker.moveTo(x0, y0 + h * 0.25)
        speaker.lineTo(x0 + w_box, y0 + h * 0.25)
        speaker.lineTo(x0 + w_box + 3, y0)
        speaker.lineTo(x0 + w_box + 3, y0 + h)
        speaker.lineTo(x0 + w_box, y0 + h * 0.75)
        speaker.lineTo(x0, y0 + h * 0.75)
        speaker.closeSubpath()
        painter.drawPath(speaker)
        # Sound waves
        for w_off, w_amp in ((6, 0.35), (8, 0.55), (10, 0.75)):
            wave = QPainterPath()
            wave.moveTo(x0 + w_box + 2 + w_off * 0.3, y0 + h * (0.5 - w_amp * 0.3))
            wave.quadTo(
                x0 + w_box + 2 + w_off,
                y0 + h * 0.5,
                x0 + w_box + 2 + w_off * 0.3,
                y0 + h * (0.5 + w_amp * 0.3),
            )
            painter.drawPath(wave)

    def _draw_blur_layer_bar(self, painter, layer, x, y, w, h, is_selected):
        color = QColor("#6b5b7b")

        # Each child layer fills the full track height (passed as h)
        # so Blur 1 and Blur 2 both span the entire B1 track. The
        # bar is drawn at the given y/h without splitting into rows.
        rect = QRectF(x, y, w, h)
        painter.fillRect(rect, QColor(color.red(), color.green(), color.blue(), 60))
        pen = QPen(QColor("#9b8bae"), 1.5, Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(rect)

        painter.setPen(QColor("#b8a8c8"))
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        label = layer.name or "Blur"
        painter.drawText(QRectF(x + 4, y, max(w - 8, 0), h),
                         Qt.AlignVCenter | Qt.AlignLeft, label)

        if is_selected:
            painter.setPen(QPen(QColor("#4a8cff"), 2, Qt.DashLine))
            painter.drawRect(rect)

    @staticmethod
    def _layer_color(layer_type: LayerType) -> QColor:
        colors = {
            LayerType.VIDEO: QColor("#2a6bcf"),
            LayerType.AUDIO: QColor("#2a9d3f"),
            LayerType.SUBTITLE: QColor("#c96b2a"),
            LayerType.DUB_SUBTITLE: QColor("#c96b2a"),
            LayerType.TEXT: QColor("#9b4dca"),
            LayerType.IMAGE: QColor("#2a9baa"),
            LayerType.STICKER: QColor("#d4a028"),
            LayerType.BLUR: QColor("#6b5b7b"),
        }
        return colors.get(layer_type, QColor("#4a5568"))


    def _draw_playhead(self, painter: QPainter, scroll_x: int) -> None:
        x = self.CONTENT_LEFT_PAD + int(self._playhead * self.pixels_per_second) - scroll_x
        if x < 0 or x > self.viewport().width():
            return
        h = max(self.viewport().height() if self.viewport() else 0, int(self._scene.height()))
        painter.setPen(QPen(QColor("#e04040"), 2))
        painter.drawLine(int(x), 0, int(x), int(h))
        painter.setBrush(QColor("#e04040"))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 8)])

    def _draw_selection_range(self, painter: QPainter, scroll_x: int) -> None:
        if not self._selection_range:
            return
        start, end = self._selection_range
        x1 = self.CONTENT_LEFT_PAD + int(start * self.pixels_per_second) - scroll_x
        x2 = self.CONTENT_LEFT_PAD + int(end * self.pixels_per_second) - scroll_x
        if x2 < 0 or x1 > self.viewport().width():
            return
        height = max(self.RULER_HEIGHT, self.viewport().height() if self.viewport() else 0, int(self._scene.height()))
        painter.fillRect(max(0, x1), 0, max(1, x2 - x1), height, QColor(74, 140, 255, 45))
        painter.setPen(QPen(QColor("#71adff"), 2))
        painter.drawLine(x1, 0, x1, height)
        painter.drawLine(x2, 0, x2, height)
        painter.setBrush(QColor("#71adff")); painter.setPen(Qt.NoPen)
        painter.drawPolygon([QPointF(x1 - 5, 2), QPointF(x1 + 5, 2), QPointF(x1, 10)])
        painter.drawPolygon([QPointF(x2 - 5, 2), QPointF(x2 + 5, 2), QPointF(x2, 10)])

    def _get_effective_layer_end(self, layer) -> float:
        """Get the effective end time for a layer."""
        return float(layer.end)

    def _hit_test_edge(self, pos, scroll_x: int, scroll_y: int = 0):
        """Return ('left'|'right', layer_id) if pos is near a bar edge,
        or ('body', layer_id) if inside the bar, or (None, '')."""
        if not self._timeline:
            return None, ""
        click_y = pos.y() + scroll_y
        y = self.RULER_HEIGHT
        margin = 4
        for track in self._timeline.tracks:
            if not self.is_track_shown_on_timeline(track):
                continue
            th = self._track_heights.get(track.id, self.TRACK_DEFAULT_H)
            if not (y <= click_y <= y + th):
                y += th
                continue
            visible_layers = [l for l in track.layers if l.visible]
            num_layers = max(1, len(visible_layers))
            uses_layer_rows = self._uses_layer_rows(track)
            overlap_stack = self._should_overlap_stack(track)
            layers_in_row = []
            if overlap_stack and num_layers > 1:
                cached_layout = self._overlap_layout_cache.get(track.id)
                if cached_layout is not None:
                    (layer_row_by_id, num_rows, visible_layers_sorted, sorted_starts, prefix_max_ends) = cached_layout
                else:
                    visible_layers_sorted = sorted(visible_layers, key=lambda l: float(getattr(l, "start", 0.0)))
                    layer_rows, num_rows = self._compute_overlap_rows(visible_layers_sorted, track_id=track.id)
                    layer_row_by_id = {str(getattr(l, "id", "")): r for l, r in zip(visible_layers_sorted, layer_rows)}
                row_slots = []
                cursor = y + margin
                for r in range(num_rows):
                    row_slots.append((cursor, self.CHILD_TRACK_H))
                    cursor += self.CHILD_TRACK_H
                row = -1
                for r, (slot_y, slot_h) in enumerate(row_slots):
                    if slot_y <= click_y <= slot_y + slot_h:
                        row = r
                        break
                if row < 0:
                    return None, ""
                layers_in_row = [l for l in visible_layers_sorted if layer_row_by_id.get(str(getattr(l, "id", "")), 0) == row]
            elif uses_layer_rows and num_layers > 1:
                row_h = (th - margin * 2) / num_layers
                rel_y = click_y - y - margin
                row = max(0, min(int(rel_y / row_h), num_layers - 1))
                layers_in_row = [visible_layers[row]]
            else:
                layers_in_row = [layer for layer in track.layers if layer.visible]

            pps = max(0.001, self.pixels_per_second)
            min_bar_w = 1 if pps < 30.0 else 20
            for layer in layers_in_row:
                lx = self.CONTENT_LEFT_PAD + int(round(layer.start * pps)) - scroll_x
                lw = max(int(round(layer.duration * pps)), min_bar_w)
                lw_hit = lw
                meta = getattr(layer, "metadata", None)
                if isinstance(meta, dict):
                    raw_ae = meta.get("_audio_end")
                    if raw_ae is not None:
                        try:
                            ex = float(raw_ae) - float(getattr(layer, "end", 0.0) or 0.0)
                            if ex > 0.05 and float(getattr(layer, "extended_duration", 0.0) or 0.0) <= 0.001:
                                lw_hit += int(round(ex * pps))
                        except (TypeError, ValueError):
                            pass
                if lx - 4 <= pos.x() <= lx + lw_hit + 4:
                    dx = pos.x() - lx
                    if dx <= self.HANDLE_W:
                        return "left", layer.id
                    if 0 <= lw - dx <= self.HANDLE_W:
                        return "right", layer.id
                    return "body", layer.id
            return None, ""
        return None, ""

    def _find_layer_by_id(self, layer_id: str):
        if not self._timeline:
            return None, None
        for track in self._timeline.tracks:
            for layer in track.layers:
                if layer.id == layer_id:
                    return track, layer
        return None, None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            pos = event.position()
            scroll_x = self.horizontalScrollBar().value()
            scroll_y = self.verticalScrollBar().value()
            in_ruler = pos.y() < self.RULER_HEIGHT
            if self._playing:
                # Review mode: any click in the timed content area is
                # navigation only. Segment bodies and resize edges must never
                # start an edit while video playback is active.
                if pos.x() >= self.CONTENT_LEFT_PAD:
                    t = self._pos_to_time(pos.x(), scroll_x)
                    if t >= 0:
                        self._manual_subtitle_selection = False
                        self.set_playhead(t)
                        self.seekRequested.emit(t)
                        self.seekRequestedMs.emit(int(t * 1000))
                        self._selection_drag = {"mode": "scrub"}
                        self.scrubStarted.emit()
                event.accept()
                return
            if in_ruler:
                t = self._pos_to_time(pos.x(), scroll_x)
                if t >= 0:
                    if not self._selection_mode:
                        # A ruler scrub is explicit playback navigation, so
                        # return control of the subtitle highlight to the
                        # playhead after a previous manual segment edit.
                        self._manual_subtitle_selection = False
                        self.set_playhead(t)
                        self.seekRequested.emit(t)
                        self.seekRequestedMs.emit(int(t * 1000))
                        self._selection_drag = {"mode": "scrub"}
                        self.scrubStarted.emit()
                        event.accept()
                        return
                    drag_mode = "new"
                    if self._selection_range:
                        start, end = self._selection_range
                        start_x = self.CONTENT_LEFT_PAD + start * self.pixels_per_second - scroll_x
                        end_x = self.CONTENT_LEFT_PAD + end * self.pixels_per_second - scroll_x
                        if abs(pos.x() - start_x) <= 8:
                            drag_mode = "start"
                        elif abs(pos.x() - end_x) <= 8:
                            drag_mode = "end"
                    # Do not seek yet. A plain click is navigation, but a
                    # drag is range editing; defer the click seek until
                    # release so a selection never moves the playhead.
                    self._selection_drag = {
                        "anchor": t, "initial": self._selection_range,
                        "mode": drag_mode, "changed": False,
                    }
                    event.accept()
                    return

            edge, lid = self._hit_test_edge(pos, scroll_x, scroll_y)
            if lid and edge in ("left", "right"):
                track, layer = self._find_layer_by_id(lid)
                if layer:
                    if bool(getattr(track, "locked", False)):
                        event.accept()
                        return
                    if bool(getattr(layer, "locked", False)):
                        self._selected_layer_id = lid
                        self.layerSelected.emit(lid)
                        self.viewport().update()
                        event.accept()
                        return
                    effective_end = self._get_effective_layer_end(layer)
                    self._drag_state = {
                        "type": f"resize_{edge}",
                        "layer_id": lid,
                        "track_id": str(getattr(track, "id", "") or ""),
                        "row_index": self._layer_row_index(track, layer),
                        "start_time": float(layer.start),
                        "end_time": float(effective_end),
                        "layer_start_x": float(self.CONTENT_LEFT_PAD + int(layer.start * self.pixels_per_second) - scroll_x),
                    }
                    self._selected_layer_id = lid
                    self.layerSelected.emit(lid)
                    idx = self.segment_index_for_layer_id(lid)
                    if idx >= 0:
                        self._manual_subtitle_selection = True
                        self.segmentTimingEditStarted.emit(idx, float(layer.start), float(effective_end))
                        self.segmentSelected.emit(idx)
                    self.viewport().update()
                    event.accept()
                    return

            elif lid:
                track, layer = self._find_layer_by_id(lid)
                if bool(getattr(track, "locked", False)):
                    event.accept()
                    return
                if bool(getattr(layer, "locked", False)):
                    self._selected_layer_id = lid
                    self.layerSelected.emit(lid)
                    self.viewport().update()
                    event.accept()
                    return
                self._selected_layer_id = lid
                self.layerSelected.emit(lid)
                idx = self.segment_index_for_layer_id(lid)
                if idx >= 0:
                    self._manual_subtitle_selection = True
                    self.segmentTimingEditStarted.emit(
                        idx, float(layer.start), float(self._get_effective_layer_end(layer))
                    )
                    self.segmentSelected.emit(idx)
                self._drag_state = {
                    "type": "move",
                    "layer_id": lid,
                    "track_id": str(getattr(track, "id", "") or ""),
                    "row_index": self._layer_row_index(track, layer),
                    "anchor_time": self._pos_to_time(pos.x(), scroll_x),
                    "start_time": float(layer.start),
                    "end_time": float(self._get_effective_layer_end(layer)),
                }
                self.viewport().update()
                event.accept()
                return

            if not self._selection_mode and pos.x() >= self.CONTENT_LEFT_PAD:
                t = self._pos_to_time(pos.x(), scroll_x)
                if t >= 0:
                    self._manual_subtitle_selection = False
                    self.set_playhead(t)
                    self.seekRequested.emit(t)
                    self.seekRequestedMs.emit(int(t * 1000))
                    self._selection_drag = {"mode": "scrub"}
                    self.scrubStarted.emit()
                    event.accept()
                    return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._selection_drag is not None and event.button() == Qt.LeftButton:
            drag = self._selection_drag
            self._selection_drag = None
            if drag.get("mode") == "scrub":
                self.scrubFinished.emit(int(self._playhead * 1000))
                event.accept()
                return
            if not drag.get("changed", False) and drag.get("mode") == "new":
                t = float(drag["anchor"])
                self.set_playhead(t)
                self.seekRequested.emit(t)
                self.seekRequestedMs.emit(int(t * 1000))
            event.accept()
            return
        if self._drag_state and event.button() == Qt.LeftButton:
            drag = self._drag_state
            self._drag_state = None
            self.setCursor(Qt.ArrowCursor)
            lid = drag["layer_id"]
            track, layer = self._find_layer_by_id(lid)
            if layer and str(getattr(track, "id", "") or "") == str(drag.get("track_id", "") or ""):
                start = float(layer.start)
                end = float(self._get_effective_layer_end(layer))
                self.layerTimingChanged.emit(lid, start, end)
                idx = self.segment_index_for_layer_id(lid)
                if idx >= 0:
                    self.segmentTimingChanged.emit(idx, start, end)
            self.viewport().update()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        scroll_x = self.horizontalScrollBar().value()
        scroll_y = self.verticalScrollBar().value()
        if self._playing:
            # The only allowed left-button interaction during playback is
            # scrub navigation established by mousePressEvent.
            if event.buttons() & Qt.LeftButton and self._selection_drag is not None:
                t = self._pos_to_time(pos.x(), scroll_x)
                if t >= 0:
                    self.set_playhead(t)
                    self.seekRequested.emit(t)
                    self.seekRequestedMs.emit(int(t * 1000))
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return

        if self._drag_state:
            drag = self._drag_state
            t = self._pos_to_time(pos.x(), scroll_x)
            t = max(0.0, min(t, self._duration))
            track, layer = self._find_layer_by_id(drag["layer_id"])
            if layer and str(getattr(track, "id", "") or "") == str(drag.get("track_id", "") or ""):
                if drag["type"] == "move":
                    delta = t - float(drag["anchor_time"])
                    original_start = float(drag["start_time"])
                    original_end = float(drag["end_time"])
                    proposed_start = original_start + delta
                    new_start = self._clamp_layer_move(
                        track, layer, proposed_start, proposed_start + (original_end - original_start)
                    )
                    layer.start = new_start
                    layer.end = new_start + (original_end - original_start)
                elif drag["type"] == "resize_left":
                    new_start = min(t, drag["end_time"] - self.MIN_DUR)
                    new_start = max(0.0, new_start)
                    new_start = self._clamp_layer_resize(track, layer, "left", new_start)
                    new_start = min(new_start, drag["end_time"] - self.MIN_DUR)
                    layer.start = new_start
                elif drag["type"] == "resize_right":
                    new_end = max(t, drag["start_time"] + self.MIN_DUR)
                    new_end = min(new_end, self._duration)
                    new_end = self._clamp_layer_resize(track, layer, "right", new_end)
                    new_end = max(new_end, drag["start_time"] + self.MIN_DUR)
                    layer.end = new_end
                # Timing is being edited in place, so the cached overlap
                # layout is no longer valid until the next paint.
                self._overlap_layout_cache.clear()
                self.viewport().update()
            event.accept()
            return

        if event.buttons() & Qt.LeftButton and self._selection_drag is not None:
            mode = self._selection_drag.get("mode")
            if mode == "scrub":
                t = self._pos_to_time(pos.x(), scroll_x)
                if t >= 0:
                    self.set_playhead(t)
                    self.seekRequested.emit(t)
                    self.seekRequestedMs.emit(int(t * 1000))
                # Auto-scroll when scrubbing near viewport edges
                viewport = self.viewport()
                if viewport:
                    vw = viewport.width()
                    if pos.x() < 30 and scroll_x > 0:
                        self.horizontalScrollBar().setValue(max(0, scroll_x - 40))
                    elif pos.x() > vw - 30:
                        max_scroll = self.horizontalScrollBar().maximum()
                        self.horizontalScrollBar().setValue(min(max_scroll, scroll_x + 40))
                self.setCursor(Qt.ArrowCursor)
                event.accept()
                return
            else:
                in_ruler = pos.y() < self.RULER_HEIGHT
                if in_ruler:
                    t = self._pos_to_time(pos.x(), scroll_x)
                    if t >= 0:
                        anchor = self._selection_drag["anchor"]
                        existing = self._selection_drag.get("initial")
                        if mode == "start" and existing:
                            self.set_selection_range(min(t, existing[1] - self.MIN_DUR), existing[1])
                            self._selection_drag["changed"] = True
                        elif mode == "end" and existing:
                            self.set_selection_range(existing[0], max(t, existing[0] + self.MIN_DUR))
                            self._selection_drag["changed"] = True
                        elif abs(t - anchor) >= self.MIN_DUR:
                            self.set_selection_range(anchor, t)
                            self._selection_drag["changed"] = True
                self.setCursor(Qt.ArrowCursor)
                event.accept()
                return

        edge, lid = self._hit_test_edge(pos, scroll_x, scroll_y)
        if lid and edge in ("left", "right"):
            self.setCursor(Qt.SizeHorCursor)
        elif lid and edge == "body":
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._playing:
            # Selection Range creation/clearing changes editing state; keep
            # it unavailable in review mode while allowing normal playback
            # shortcuts to propagate to the parent application.
            event.ignore()
            return
        if event.key() == Qt.Key_Escape:
            self.clear_selection_range(); event.accept(); return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_BracketLeft:
            current = self._selection_range or (self._playhead, self._playhead + self.MIN_DUR)
            self.set_selection_range(self._playhead, current[1]); event.accept(); return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_BracketRight:
            current = self._selection_range or (max(0.0, self._playhead - self.MIN_DUR), self._playhead)
            self.set_selection_range(current[0], self._playhead); event.accept(); return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # A splitter can emit many resize events per drag. Coalesce them to
        # one refresh per UI frame and keep the subtitle overlap cache intact.
        if not self._resize_refresh_pending:
            self._resize_refresh_pending = True
            QTimer.singleShot(16, self._flush_resize_refresh)

    def _flush_resize_refresh(self) -> None:
        self._resize_refresh_pending = False
        self._refresh_scene_bounds_for_viewport()
        self._update_return_to_playhead_button()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        # Some platforms update the horizontal scrollbar directly for a
        # shift-wheel/trackpad gesture without emitting a slider action.
        # Treat that as manual navigation too, but never interrupt our own
        # short follow/return animation.
        if (
            dx
            and self._playing
            and self._playhead_follow_animation.state() != QAbstractAnimation.Running
        ):
            self._begin_manual_navigation()
        super().scrollContentsBy(dx, dy)

    def _pos_to_time(self, x: float, scroll_x: int) -> float:
        t = (x + scroll_x - self.CONTENT_LEFT_PAD) / self.pixels_per_second
        return max(0.0, min(t, self._duration))

    def _hit_test_layer(self, pos, scroll_x: int, scroll_y: int = 0) -> str:
        if not self._timeline:
            return ""
        click_y = pos.y() + scroll_y
        y = self.RULER_HEIGHT
        margin = 4
        for track in self._timeline.tracks:
            if not self.is_track_shown_on_timeline(track):
                continue
            th = self._track_heights.get(track.id, self.TRACK_DEFAULT_H)
            if not (y <= click_y <= y + th):
                y += th
                continue
            visible_layers = [l for l in track.layers if l.visible]
            num_layers = max(1, len(visible_layers))
            uses_layer_rows = self._uses_layer_rows(track)
            overlap_stack = self._should_overlap_stack(track)
            pps = max(0.001, self.pixels_per_second)
            min_bar_w = 1 if pps < 30.0 else 20
            if overlap_stack and num_layers > 1:
                cached_layout = self._overlap_layout_cache.get(track.id)
                if cached_layout is not None:
                    (layer_row_by_id, num_rows, visible_layers_sorted, sorted_starts, prefix_max_ends) = cached_layout
                else:
                    visible_layers_sorted = sorted(visible_layers, key=lambda l: float(getattr(l, "start", 0.0)))
                    layer_rows, num_rows = self._compute_overlap_rows(visible_layers_sorted, track_id=track.id)
                    layer_row_by_id = {str(getattr(l, "id", "")): r for l, r in zip(visible_layers_sorted, layer_rows)}
                num_rows = max(1, num_rows)
                row_slots: list[tuple[int, int]] = []
                cursor = y + margin
                for r in range(num_rows):
                    row_slots.append((cursor, self.CHILD_TRACK_H))
                    cursor += self.CHILD_TRACK_H
                row = -1
                for r, (slot_y, slot_h) in enumerate(row_slots):
                    if slot_y <= click_y <= slot_y + slot_h:
                        row = r
                        break
                if row < 0:
                    return ""
                for layer in visible_layers_sorted:
                    if layer_row_by_id.get(str(getattr(layer, "id", "")), 0) != row:
                        continue
                    lx = self.CONTENT_LEFT_PAD + int(round(layer.start * pps)) - scroll_x
                    lw = max(int(round(layer.duration * pps)), min_bar_w)
                    if lx - 4 <= pos.x() <= lx + lw + 4:
                        return layer.id
                return ""
            if uses_layer_rows and num_layers > 1:
                row_h = (th - margin * 2) / num_layers
                rel_y = click_y - y - margin
                row = max(0, min(int(rel_y / row_h), num_layers - 1))
                # Find the layer in that row
                visible_count = 0
                for layer in track.layers:
                    if not layer.visible:
                        continue
                    if visible_count == row:
                        lx = self.CONTENT_LEFT_PAD + int(round(layer.start * pps)) - scroll_x
                        lw = max(int(round(layer.duration * pps)), min_bar_w)
                        if lx - 4 <= pos.x() <= lx + lw + 4:
                            return layer.id
                        break
                    visible_count += 1
                return ""
            for layer in track.layers:
                if not layer.visible:
                    continue
                lx = self.CONTENT_LEFT_PAD + int(round(layer.start * pps)) - scroll_x
                lw = max(int(round(layer.duration * pps)), min_bar_w)
                if lx - 4 <= pos.x() <= lx + lw + 4:
                    return layer.id
            return ""
        return ""
