from pathlib import Path
import re
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, QThreadPool, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QSlider, QSplitter, QVBoxLayout, QWidget,
)

from i18n import t
from services.processing_queue import acquire, editor_busy, release
from services.recap_render import (
    range_duration, recap_frame_rate, recap_preview_path, recap_subtitles, recover_scene_range,
    render_plan, render_recap_video, save_recap_preview, scene_duration, scene_range, suggest_scene_ranges,
)
from services.recap_voice import current_clip_path
from widgets.translation_progress_dialog import TranslationProgressDialog
from ui.widgets.recap_timeline import SYNC_TOLERANCE_MS, RecapSourceTimeline, RecapSpan, RecapTimeline, timecode


class _RenderSignals(QObject):
    progress = Signal(int, int)
    completed = Signal(str, str)


class _RenderJob(QRunnable):
    def __init__(self, source, clips, output_dir, *, preview, preview_key, cached_path, narrations=()):
        super().__init__()
        self.source, self.clips, self.output_dir = source, clips, output_dir
        self.preview, self.cached_path = preview, cached_path
        self.preview_key = preview_key
        self.narrations = narrations
        self.rendered_preview = None
        self.subtitle_rows = []
        self.canceled = Event()
        self.signals = _RenderSignals()

    def run(self):
        path, error = "", ""
        try:
            fps = None
            if self.narrations:
                fps = recap_frame_rate(self.source, self.canceled.is_set)
                self.subtitle_rows = recap_subtitles(self.clips, self.narrations, fps)
            video = self.cached_path
            if video is None:
                directory = self.preview_key.parent if self.preview_key is not None else self.output_dir
                video = render_recap_video(self.source, self.clips, directory, fps=fps,
                                           progress=self.signals.progress.emit, is_canceled=self.canceled.is_set)
                self.rendered_preview = Path(video).resolve()
            path = str(video) if self.preview else save_recap_preview(
                video, self.source, self.output_dir,
                progress=self.signals.progress.emit if self.cached_path else lambda *_: None,
                is_canceled=self.canceled.is_set,
            )
        except InterruptedError:
            pass
        except Exception as exc:
            error = str(exc)
        self.signals.completed.emit(path, error)


def _time(ms):
    if type(ms) is not int:
        return "—"
    seconds, fraction = divmod(ms, 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}.{fraction // 100}"


class _SourceTimeSpinBox(QDoubleSpinBox):
    _COMPLETE = re.compile(r"([0-9]{1,8}):([0-5]?[0-9](?:[.,][0-9]{1,3})?)")
    _PARTIAL = re.compile(r"[0-9]{0,8}(?::[0-5]?[0-9]?(?:[.,][0-9]{0,3})?)?")

    def textFromValue(self, value):
        seconds, fraction = divmod(round(value * 1000), 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}.{fraction:03d}"

    def valueFromText(self, text):
        match = self._COMPLETE.fullmatch(text.strip())
        if match is None:
            return self.value()
        minutes, seconds = match.groups()
        return int(minutes) * 60 + float(seconds.replace(",", "."))

    def validate(self, text, position):
        value = text.strip()
        if self._COMPLETE.fullmatch(value):
            if self.minimum() <= self.valueFromText(value) <= self.maximum():
                return QValidator.Acceptable, text, position
        if self._PARTIAL.fullmatch(value):
            return QValidator.Intermediate, text, position
        return QValidator.Invalid, text, position


class RecapStudioController(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.gui, self.panel = owner, owner.gui, owner.recap
        self.player = None
        self.duration_ms = 0
        self._source_duration_ms = 0
        self._source_position_ms = 0
        self._preview_active = False
        self._preview_path = None
        self._preview_key = None
        self._restore_preview_on_open = False
        self._restore_preview_timer = QTimer(self)
        self._restore_preview_timer.setSingleShot(True)
        self._restore_preview_timer.timeout.connect(self._restore_saved_preview)
        self._pending_seek = None
        self._needs_first_frame = False
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._apply_pending_seek)
        self._play_end = None
        self.job = None
        self.draft_in_render = None
        self.progress_dialog = None
        self.message = ""
        self._source_rows = []
        self._last_trim = None
        self._following_playhead = False

        self.dialog = QDialog(self.gui)
        self.dialog.setObjectName("recapStudioDialog")
        self.dialog.setStyleSheet("QDialog#recapStudioDialog { background: #101826; }")
        self.dialog.setWindowTitle(t("Recap studio"))
        self.dialog.setWindowModality(Qt.WindowModal)
        self.dialog.setWindowFlag(Qt.WindowMaximizeButtonHint)
        self.dialog.resize(1280, 800)
        self.dialog.finished.connect(self._on_closed)
        layout = QVBoxLayout(self.dialog)
        actions = QHBoxLayout()
        self.duration_summary = QLabel()
        self.duration_summary.setObjectName("sectionTitle")
        actions.addWidget(self.duration_summary, 1)
        self.avoid_overlap_cb = QCheckBox(t("Avoid scene overlap"))
        self.avoid_overlap_cb.setChecked(True)
        self.avoid_overlap_cb.setToolTip(t("Prefer source clips that overlap less when scenes are selected automatically."))
        self.avoid_overlap_cb.toggled.connect(self._on_overlap_changed)
        actions.addWidget(self.avoid_overlap_cb)
        actions.addWidget(self.panel.generate_all_voice_btn)
        self.include_subtitles_cb = QCheckBox(t("Create subtitles from narration"))
        actions.addWidget(self.include_subtitles_cb)
        self.use_btn = QPushButton(t("Open recap as a new project"))
        self.use_btn.clicked.connect(self.render)
        actions.addWidget(self.use_btn)
        layout.addLayout(actions)
        self.voice_progress_row = QWidget()
        voice_progress = QHBoxLayout(self.voice_progress_row)
        voice_progress.setContentsMargins(0, 0, 0, 0)
        self.voice_progress_label = QLabel()
        self.voice_progress_label.setWordWrap(True)
        self.voice_progress_label.setTextFormat(Qt.PlainText)
        voice_progress.addWidget(self.voice_progress_label, 2)
        self.voice_progress_bar = QProgressBar()
        self.voice_progress_bar.setTextVisible(False)
        voice_progress.addWidget(self.voice_progress_bar, 1)
        self.cancel_voice_btn = QPushButton(t("Cancel"))
        self.cancel_voice_btn.clicked.connect(self.owner.recap_voice.cancel)
        voice_progress.addWidget(self.cancel_voice_btn)
        self.voice_progress_row.hide()
        layout.addWidget(self.voice_progress_row)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self.panel)
        layout.addWidget(split, 1)

        preview = QWidget()
        split.addWidget(preview)
        split.addWidget(self.panel.inspector)
        split.setSizes([270, 660, 350])
        split.setStretchFactor(1, 1)
        right = QVBoxLayout(preview)
        right.setContentsMargins(0, 0, 0, 0)
        preview_header = QHBoxLayout()
        self.source_btn = QPushButton(t("Source video"))
        self.source_btn.setToolTip(t("Watch the source video and choose clip ranges."))
        self.source_btn.clicked.connect(self.show_source)
        self.preview_btn = QPushButton(t("Recap preview"))
        self.preview_btn.clicked.connect(self.preview_recap)
        for button in (self.source_btn, self.preview_btn):
            button.setCheckable(True)
            button.setObjectName("workflowTabBtn")
            preview_header.addWidget(button, 1)
        right.addLayout(preview_header)
        self.preview_host = QVBoxLayout()
        right.addLayout(self.preview_host, 1)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self.seek)
        right.addWidget(self.slider)
        self.source_timeline = RecapSourceTimeline()
        self.source_timeline.seek_requested.connect(self.seek)
        right.addWidget(self.source_timeline)
        self.source_cut_label = QLabel()
        self.source_cut_label.setWordWrap(True)
        right.addWidget(self.source_cut_label)
        playback = QHBoxLayout()
        self.play_btn = QPushButton(t("Play"))
        self.play_btn.clicked.connect(self.play)
        playback.addWidget(self.play_btn)
        self.pause_btn = QPushButton(t("Pause"))
        self.pause_btn.clicked.connect(self.pause)
        playback.addWidget(self.pause_btn)
        self.position_input = QDoubleSpinBox()
        self.position_input.setDecimals(3)
        self.position_input.setMaximum(0)
        self.position_input.setSuffix(" s")
        self.position_input.setKeyboardTracking(False)
        self.position_input.valueChanged.connect(lambda seconds: self.seek(round(seconds * 1000)))
        playback.addWidget(self.position_input)
        self.video_duration = QLabel()
        playback.addWidget(self.video_duration, 1)
        right.addLayout(playback)

        self.timeline = RecapTimeline()
        self.timeline.picked.connect(self._timeline_picked)
        self.timeline.trim_started.connect(self.pause)
        self.timeline.trimmed.connect(self._trim_from_timeline)
        layout.addWidget(self.timeline)
        self._build_timeline_trim(layout)
        self.mismatch_status = QLabel()
        self.mismatch_status.setWordWrap(True)
        layout.addWidget(self.mismatch_status)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.panel.selection_changed.connect(self._scene_selected)
        self.panel.edited.connect(self.refresh)
        self.refresh()

    def _build_timeline_trim(self, layout):
        self.timeline_trim = QWidget()
        scene_layout = QVBoxLayout(self.timeline_trim)
        scene_layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.trim_label = QLabel()
        controls.addWidget(self.trim_label, 1)
        self.clip_start = _SourceTimeSpinBox()
        self.clip_end = _SourceTimeSpinBox()
        for spin, label in ((self.clip_start, "Start"), (self.clip_end, "End")):
            spin.setDecimals(3)
            spin.setSingleStep(.1)
            spin.setKeyboardTracking(False)
            spin.setMinimumWidth(110)
            caption = QLabel(t(label))
            caption.setBuddy(spin)
            controls.addWidget(caption)
            controls.addWidget(spin)
        self.apply_trim_btn = QPushButton(t("Apply trim"))
        self.apply_trim_btn.clicked.connect(self.apply_trim)
        controls.addWidget(self.apply_trim_btn)
        self.play_scene = QPushButton(t("Preview selected scene"))
        self.play_scene.clicked.connect(self.preview_scene)
        controls.addWidget(self.play_scene)
        self.auto_scene = QPushButton(t("Suggest scene again"))
        self.auto_scene.clicked.connect(self.auto_select_scene)
        controls.addWidget(self.auto_scene)
        self.restore_cut_btn = QPushButton(t("Restore scene cut"))
        self.restore_cut_btn.setToolTip(t("Restore the automatic cut saved before your first edit."))
        self.restore_cut_btn.clicked.connect(self.restore_scene_cut)
        controls.addWidget(self.restore_cut_btn)
        scene_layout.addLayout(controls)
        self.scene_status = QLabel()
        self.scene_status.setWordWrap(True)
        self.scene_status.setTextFormat(Qt.PlainText)
        scene_layout.addWidget(self.scene_status)
        layout.addWidget(self.timeline_trim)

    @property
    def busy(self):
        return self.job is not None

    @Slot()
    def open(self):
        self.owner.refresh()
        source = self.gui.video_path_edit.text().strip()
        if not source or not self.owner.project_root:
            return
        if not self.dialog.isVisible():
            self._restore_preview_on_open = True
            self.include_subtitles_cb.setChecked(False)
        self.gui.media_player.pause()
        if self.player is None:
            self.player = QMediaPlayer(self.dialog)
            self.audio_output = QAudioOutput(self.player)
            self.player.setAudioOutput(self.audio_output)
            video = QVideoWidget(self.dialog)
            video.setMinimumSize(340, 245)
            video.setStyleSheet("background: #101826;")
            self.preview_host.addWidget(video)
            self.player.setVideoOutput(video)
            self.player.durationChanged.connect(self._duration_changed)
            self.player.positionChanged.connect(self._position_changed)
            self.player.playbackStateChanged.connect(self._playback_changed)
            self.player.seekableChanged.connect(self._seekable_changed)
            self.player.mediaStatusChanged.connect(self._media_status_changed)
            self.player.errorOccurred.connect(self._preview_error)
        if not self._preview_active and self.player.source() != QUrl.fromLocalFile(str(Path(source).resolve())):
            self._switch_video(source, preview=False)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.refresh()
        self._schedule_preview_restore()

    def _switch_video(self, path, *, preview, position=None):
        if self.player is None:
            return
        self.pause()
        self.owner.recap_voice._stop_preview()
        self._preview_active = preview
        self._pending_seek = position
        self._needs_first_frame = True
        self._seek_timer.stop()
        self._duration_changed(0)
        with QSignalBlocker(self.slider), QSignalBlocker(self.position_input):
            self.slider.setValue(0)
            self.position_input.setValue(0)
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))

    @Slot()
    def show_source(self):
        self._restore_preview_on_open = False
        if self._preview_active and not self.busy:
            self._switch_video(self.gui.video_path_edit.text().strip(), preview=False,
                               position=self._source_position_ms)
        self._update_preview_selection()

    def seek_source(self, position):
        if self._preview_active and not self.busy:
            self._switch_video(self.gui.video_path_edit.text().strip(), preview=False, position=position)
        else:
            self.seek(position)

    def pause(self):
        self._play_end = None
        if self.player is not None:
            self.player.pause()

    @Slot()
    def play(self):
        if self.player is not None and self.play_btn.isEnabled():
            self.owner.recap_voice._stop_preview()
            if self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    def _update_preview_selection(self):
        self.source_btn.setChecked(not self._preview_active)
        self.preview_btn.setChecked(self._preview_active)

    def show_voice_progress(self, message="", *, completed=0, total=0, canceling=False):
        self.voice_progress_label.setText(message)
        self.voice_progress_bar.setRange(0, max(1, total))
        self.voice_progress_bar.setValue(completed)
        self.voice_progress_bar.setVisible(total > 0)
        self.cancel_voice_btn.setVisible(total > 0)
        self.cancel_voice_btn.setEnabled(not canceling)
        self.voice_progress_row.setVisible(bool(message))

    @Slot(int)
    def seek(self, position):
        self._play_end = None
        if self.player is not None and not self.busy:
            if self._can_seek_now():
                self.player.setPosition(max(0, min(position, self.duration_ms)))
            else:
                self._pending_seek = position

    def _can_seek_now(self):
        return (self.player is not None and self.duration_ms > 0 and self.player.isSeekable()
                and self.player.mediaStatus() not in (QMediaPlayer.NoMedia, QMediaPlayer.LoadingMedia,
                                                       QMediaPlayer.InvalidMedia))

    @Slot(bool)
    def _seekable_changed(self, seekable):
        if seekable and self._pending_seek is not None:
            self._seek_timer.start(0)
        self.refresh()

    def _media_status_changed(self, status):
        if status == QMediaPlayer.LoadedMedia:
            if self._needs_first_frame:
                self._needs_first_frame = False
                self.player.pause()
            if self._pending_seek is not None:
                self._seek_timer.start(0)
        self.refresh()

    @Slot()
    def _apply_pending_seek(self):
        if self._pending_seek is not None and self._can_seek_now():
            position, self._pending_seek = self._pending_seek, None
            self.seek(position)

    @Slot(int)
    def _duration_changed(self, duration_ms):
        self.duration_ms = max(0, duration_ms)
        if self.duration_ms and not self._preview_active:
            self._source_duration_ms = self.duration_ms
        with QSignalBlocker(self.slider):
            self.slider.setRange(0, self.duration_ms)
        with QSignalBlocker(self.position_input):
            self.position_input.setMaximum(self.duration_ms / 1000)
        self.video_duration.setText(f"/ {_time(self.duration_ms)}" if self.duration_ms else "")
        self._seekable_changed(self.player is not None and self.player.isSeekable())
        if self.duration_ms and not self._preview_active:
            self.owner.refresh()
            self._schedule_preview_restore()

    @Slot(int)
    def _position_changed(self, position):
        if not self._preview_active and self.duration_ms:
            self._source_position_ms = position
            self.source_timeline.set_playhead(position)
        if not self.slider.isSliderDown():
            with QSignalBlocker(self.slider):
                self.slider.setValue(position)
        if not self.position_input.hasFocus():
            with QSignalBlocker(self.position_input):
                self.position_input.setValue(position / 1000)
        if self._play_end is not None and position >= self._play_end:
            end = self._play_end
            self.pause()
            self.seek(end)
        if self._preview_active:
            self.timeline.set_playhead(position)
            scene = next((index for index, span in enumerate(self.timeline.spans)
                          if span.start_ms <= position < span.end_ms), None)
            if scene is not None and not self.panel.narration.hasFocus():
                self._following_playhead = True
                try:
                    self.panel.segment_list.setCurrentRow(scene)
                finally:
                    self._following_playhead = False

    def _playback_changed(self, state):
        voice_busy = self.owner.recap_voice.busy
        status = self.player.mediaStatus() if self.player is not None else QMediaPlayer.NoMedia
        playing = state == QMediaPlayer.PlayingState and status != QMediaPlayer.EndOfMedia
        can_play = (status not in (QMediaPlayer.NoMedia, QMediaPlayer.LoadingMedia, QMediaPlayer.InvalidMedia)
                    and not self.busy and self.owner.job is None and not voice_busy)
        self.play_btn.setEnabled(can_play and not playing)
        self.pause_btn.setEnabled(can_play and playing)

    def _preview_error(self, _error, message):
        self.message = t("Could not play the recap preview. {error}" if self._preview_active
                         else "Could not preview the recap source. {error}", error=message)
        self.refresh()

    def _selected_segment(self):
        draft = self.panel.draft
        index = self.panel.segment_list.currentRow()
        return draft["segments"][index] if draft and 0 <= index < len(draft["segments"]) else None

    def _selected_range(self):
        segment = self._selected_segment()
        return scene_range(segment) if segment is not None else None

    @Slot()
    def _scene_selected(self):
        self.refresh()
        if self._following_playhead:
            return
        index = self.panel.segment_list.currentRow()
        if 0 <= index < len(self.timeline.spans):
            position = self.timeline.spans[index].start_ms
            self.timeline.set_playhead(position)
            if self._preview_active:
                self.seek(position)
            else:
                clip = self._selected_range()
                if clip and type(clip.get("start_ms")) is int:
                    self.seek_source(clip["start_ms"])

    @Slot(int, int)
    def _timeline_picked(self, scene, position):
        if self.busy:
            return
        self._following_playhead = True
        try:
            self.panel.segment_list.setCurrentRow(scene)
        finally:
            self._following_playhead = False
        self.timeline.set_playhead(position)
        self.refresh()
        if self._preview_active:
            self.seek(position)
        else:
            selected = self._selected_range()
            if selected and type(selected.get("start_ms")) is int:
                offset = position - self.timeline.spans[scene].start_ms
                duration = range_duration(selected, self._source_duration_ms) or 1
                offset = min(offset, duration - 1)
                self.seek_source(selected["start_ms"] + offset)

    def _write_clip(self, clip):
        segment = self._selected_segment()
        current = scene_range(segment)
        if (current and current.get("selection") == "auto"
                and range_duration(current, self._source_duration_ms) is not None
                and "original_scene" not in segment):
            segment["original_scene"] = dict(current)
        segment["scene"] = clip
        self.panel.edited.emit()

    def _original_scene_to_restore(self, segment):
        if segment is None:
            return None
        original = segment.get("original_scene")
        current = scene_range(segment) or {}
        if range_duration(original, self._source_duration_ms) is None:
            if not current or current.get("selection") == "auto" or not self._source_rows:
                return None
            original = recover_scene_range(self.panel.draft, self.panel.segment_list.currentRow(),
                                           self._source_rows, self.owner.project_root, self._source_duration_ms)
            if original is None:
                return None
        if any(original[key] != current.get(key) for key in ("start_ms", "end_ms")):
            return original
        return None

    @Slot()
    def restore_scene_cut(self):
        if not self.restore_cut_btn.isEnabled():
            return
        segment = self._selected_segment()
        original = self._original_scene_to_restore(segment)
        if original is not None:
            self.pause()
            self.message = ""
            segment["original_scene"] = dict(original)
            self._write_clip({**original, "selection": "manual"})
            self.seek_source(original["start_ms"])

    @Slot()
    def apply_trim(self):
        if not self.apply_trim_btn.isEnabled():
            return
        clip = {"start_ms": round(self.clip_start.value() * 1000),
                "end_ms": round(self.clip_end.value() * 1000), "selection": "manual"}
        if range_duration(clip, self._source_duration_ms) is None:
            self.message = t("Adjust the scene markers to select at least 0.1 seconds.")
            self.refresh()
            return
        self.message = ""
        self._write_clip(clip)

    @Slot(int, int, int)
    def _trim_from_timeline(self, start, end, edge):
        if not self.apply_trim_btn.isEnabled() or not self._selected_range():
            return
        self.clip_start.setValue(start / 1000)
        self.clip_end.setValue(end / 1000)
        self.pause()
        self.apply_trim()
        self.seek_source(start if edge == 0 else end - 1)

    def update_scene_suggestions(self, rows, evidence_is_current):
        draft = self.panel.draft
        self._source_rows = rows if draft and evidence_is_current else []
        if self._source_rows and not self.busy and suggest_scene_ranges(
            draft, self._source_rows, self.owner.project_root, self._source_duration_ms,
        ):
            self.owner.recap_save_timer.start()

    @Slot(bool)
    def _on_overlap_changed(self, checked):
        draft = self.panel.draft
        if draft is None:
            return
        draft["avoid_scene_overlap"] = checked
        if self._source_rows:
            suggest_scene_ranges(draft, self._source_rows, self.owner.project_root,
                                 self._source_duration_ms, reselect_automatic=True)
        self.panel.edited.emit()

    @Slot()
    def auto_select_scene(self):
        index = self.panel.segment_list.currentRow()
        if index < 0 or not self.auto_scene.isEnabled():
            return
        if suggest_scene_ranges(self.panel.draft, self._source_rows, self.owner.project_root,
                                self._source_duration_ms, reset_index=index):
            self.panel.edited.emit()

    @Slot()
    def preview_scene(self):
        clip = self._selected_range()
        if self.player is None or self._preview_active or range_duration(clip, self._source_duration_ms) is None:
            return
        self.owner.recap_voice._stop_preview()
        self.seek(clip["start_ms"])
        self._play_end = clip["end_ms"]
        self.player.play()

    @Slot()
    def refresh(self):
        draft = self.panel.draft
        segment = self._selected_segment()
        root = self.owner.project_root
        clips = render_plan(draft, root, self._source_duration_ms) if draft and root else []
        ready = len(clips)
        cached = self._cached_preview(clips) if self._preview_active or self._preview_path is not None else None
        if self._preview_active and not self.busy and cached is None:
            self.message = t("The draft changed. Preview the recap again to see the updates.")
            self.show_source()
        total = len(draft["segments"]) if draft else 0
        free = not self.busy and self.owner.job is None and not self.owner.recap_voice.busy
        with QSignalBlocker(self.avoid_overlap_cb):
            self.avoid_overlap_cb.setChecked(not draft or draft.get("avoid_scene_overlap", True))
        self.avoid_overlap_cb.setEnabled(bool(draft and self._source_duration_ms) and free)
        can_seek = self.player is not None and self.player.isSeekable()
        self._playback_changed(self.player.playbackState() if self.player is not None else QMediaPlayer.StoppedState)
        self.slider.setEnabled(can_seek and free)
        self.slider.setVisible(self._preview_active)
        self.position_input.setEnabled(can_seek and free)
        self._update_preview_selection()
        self.source_btn.setEnabled(free and self.player is not None)
        selected_clip = self._selected_range()
        cut_duration = range_duration(selected_clip, self._source_duration_ms)
        cut = (selected_clip["start_ms"], selected_clip["end_ms"]) if cut_duration is not None else None
        self.source_timeline.set_context(self._source_duration_ms, cut)
        self.source_timeline.set_playhead(self._source_position_ms)
        self.source_timeline.setVisible(not self._preview_active)
        self.source_timeline.setEnabled(can_seek and free and not self._preview_active)
        cut_label = ""
        if cut is not None:
            cut_label = t("Cut {start}–{end} · {seconds} s",
                          start=timecode(cut[0], milliseconds=True), end=timecode(cut[1], milliseconds=True),
                          seconds=f"{cut_duration / 1000:.3f}".rstrip("0").rstrip("."))
        self.source_cut_label.setText(cut_label)
        self.source_cut_label.setVisible(not self._preview_active and bool(cut_label))
        self.play_scene.setEnabled(cut is not None and free and can_seek and not self._preview_active)
        self.auto_scene.setEnabled(bool(segment and self._source_rows and self._source_duration_ms and free))
        restorable = free and self._original_scene_to_restore(segment) is not None
        self.restore_cut_btn.setEnabled(restorable)
        saved_cut = segment and range_duration(segment.get("original_scene"), self._source_duration_ms) is not None
        self.restore_cut_btn.setToolTip(t(
            "Rebuild the original suggestion from subtitles and narration."
            if restorable and not saved_cut else "Restore the automatic cut saved before your first edit."
        ))
        can_render = free and total > 0 and ready == total
        self.use_btn.setEnabled(can_render)
        self.include_subtitles_cb.setEnabled(free)
        self.preview_btn.setEnabled(can_render and self.player is not None)
        blocked = []
        if draft and root and ready < total:
            missing_scenes, missing_audio = [], []
            for index, item in enumerate(draft["segments"], 1):
                if scene_duration(item, self._source_duration_ms) is None:
                    missing_scenes.append(str(index))
                if current_clip_path(root, item, draft.get("voice_id")) is None:
                    missing_audio.append(str(index))
            if not self._source_duration_ms:
                blocked.append(t("Waiting for the source video to load."))
            elif missing_scenes:
                blocked.append(t("Scenes need a valid clip: {scenes}", scenes=", ".join(missing_scenes)))
            if missing_audio:
                blocked.append(t("Scenes need narration: {scenes}", scenes=", ".join(missing_audio)))
        self.preview_btn.setToolTip("\n".join(blocked) or t("Watch the assembled recap with narration."))
        self.use_btn.setToolTip("\n".join(blocked))
        self._update_trim_controls(segment, selected_clip, free)
        self._update_timeline(draft, root)
        mismatches = [str(number) for number, span in enumerate(self.timeline.spans, 1) if span.has_mismatch]
        self.mismatch_status.setVisible(bool(mismatches))
        self.mismatch_status.setText(t("Scenes with duration mismatch: {scenes}", scenes=", ".join(mismatches))
                                     if mismatches else "")
        details = ""
        if segment:
            scene = selected_clip or {}
            audio = current_clip_path(root, segment, draft.get("voice_id")) if root else None
            audio_ms = segment["audio"]["duration_ms"] if audio else None
            duration = scene_duration(segment, self._source_duration_ms)
            details = t("Video {video} · Narration {audio}", video=_time(duration), audio=_time(audio_ms))
            if duration is not None and audio_ms is not None and abs(duration - audio_ms) > SYNC_TOLERANCE_MS:
                label = ("Narration is {seconds} s longer." if audio_ms > duration
                         else "Video is {seconds} s longer.")
                details += " · " + t(label, seconds=f"{abs(duration - audio_ms) / 1000:.2f}".rstrip("0").rstrip("."))
            if duration is None and scene.get("start_ms") is not None and scene.get("end_ms") is not None:
                details += "\n" + t("Adjust the scene markers to select at least 0.1 seconds.")
        self.scene_status.setText(details)
        status = self.message
        if not status and self._preview_path is not None and self._source_duration_ms and cached is None:
            status = t("The draft changed. Preview the recap again to see the updates.")
        if not status and blocked:
            status = " · ".join(blocked)
        if not status and draft:
            status = t("{ready}/{total} recap segments ready", ready=ready, total=total)
        self.status.setText(status)

    def _update_trim_controls(self, segment, selected, free):
        trim = (id(segment), (selected or {}).get("start_ms"),
                (selected or {}).get("end_ms"), self._source_duration_ms)
        if trim != self._last_trim:
            self._last_trim = trim
            for spin, value in ((self.clip_start, trim[1]), (self.clip_end, trim[2])):
                spin.setMaximum(self._source_duration_ms / 1000)
                spin.setValue((value if type(value) is int else 0) / 1000)
        can_edit = bool(free and segment and self._source_duration_ms >= 100)
        for spin in (self.clip_start, self.clip_end):
            spin.setEnabled(can_edit)
        self.apply_trim_btn.setEnabled(can_edit)
        self.timeline_trim.setVisible(segment is not None)
        self.trim_label.setText(t("Scene {scene} in source video", scene=self.panel.segment_list.currentRow() + 1))
        valid_range = can_edit and range_duration(selected, self._source_duration_ms) is not None
        self.timeline.set_trim_range(trim[1:] if valid_range else None)

    def _update_timeline(self, draft, root):
        position, spans = 0, []
        for segment in draft["segments"] if draft and root else []:
            duration = scene_duration(segment, self._source_duration_ms) or 0
            current = current_clip_path(root, segment, draft.get("voice_id"))
            audio = segment["audio"]["duration_ms"] if current else 0
            end = position + max(duration, audio)
            spans.append(RecapSpan(position, end, duration, audio))
            position = end
        self.timeline.set_spans(spans, self.panel.segment_list.currentRow())
        self.panel.set_scene_spans([f"{timecode(span.start_ms)}–{timecode(span.end_ms)}" for span in spans])
        self.duration_summary.setText(t("Source {source} → Recap {recap}",
                                        source=timecode(self._source_duration_ms), recap=timecode(position)))

    @Slot()
    def render(self):
        self._start_render(preview=False)

    @Slot()
    def preview_recap(self):
        self._update_preview_selection()
        if not self._preview_active:
            self._start_render(preview=True)

    def _cache_key(self, clips):
        if clips:
            return recap_preview_path(self.owner.project_root, self.gui.video_path_edit.text().strip(), clips)
        return None

    def _cached_preview(self, clips):
        key = self._cache_key(clips)
        if key is not None:
            candidate = self._preview_path if key == self._preview_key else key
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
            except OSError:
                pass
        return None

    def _schedule_preview_restore(self):
        if self._restore_preview_on_open and self._source_duration_ms and self.dialog.isVisible():
            self._restore_preview_timer.start(0)

    def _restore_saved_preview(self):
        if not self._restore_preview_on_open or not self._source_duration_ms or not self.dialog.isVisible():
            return
        self._restore_preview_on_open = False
        draft = self.panel.draft
        if draft is None or self.busy:
            return
        clips = render_plan(draft, self.owner.project_root, self._source_duration_ms)
        if len(clips) == len(draft["segments"]):
            cached = self._cached_preview(clips)
            if cached is not None:
                self.message = ""
                self._show_recap_preview(cached)

    def _show_recap_preview(self, path):
        if path != self._preview_path:
            self._preview_path = self._preview_key = path
        if self._preview_active and self.player.source() == QUrl.fromLocalFile(str(path)):
            self.pause()
            self.seek(0)
        else:
            self._switch_video(path, preview=True)

    def _start_render(self, *, preview):
        draft = self.panel.draft
        root = self.owner.project_root
        button = self.preview_btn if preview else self.use_btn
        if draft is None or not root or not button.isEnabled():
            return
        clips = render_plan(draft, root, self._source_duration_ms)
        if len(clips) != len(draft["segments"]):
            self.refresh()
            return
        cached = self._cached_preview(clips)
        if preview and cached is not None:
            self.message = ""
            self._show_recap_preview(cached)
            return
        mismatched = sum(abs(clip.end_ms - clip.start_ms - clip.audio_ms)
                         > SYNC_TOLERANCE_MS for clip in clips)
        if mismatched and QMessageBox.question(
            self.dialog, t("Duration mismatch"),
            t("{count} scenes have different video and narration durations. Continue with the last frame held when "
              "video is shorter, or silence when narration is shorter?", count=mismatched),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        if editor_busy() or not acquire(self):
            self.message = t("Wait for the current processing task to finish.")
            self.refresh()
            return
        self.pause()
        self.owner.recap_voice._stop_preview()
        self.owner._save_recap_edits()
        self.draft_in_render = draft
        source = self.gui.video_path_edit.text().strip()
        output_dir = (Path(root) / "analysis" / "recap_preview" if preview
                      else Path(self.gui.workspace_root) / "output" / "recap")
        narrations = tuple(segment["narration"] for segment in draft["segments"]) if (
            not preview and self.include_subtitles_cb.isChecked()
        ) else ()
        self.job = _RenderJob(source, clips, output_dir, preview=preview, preview_key=self._cache_key(clips),
                              cached_path=cached, narrations=narrations)
        self.job.signals.progress.connect(self._render_progress)
        self.job.signals.completed.connect(self._render_completed)
        self.message = t("Rendering recap video…")
        self.progress_dialog = TranslationProgressDialog(self.dialog, provider="FFmpeg", video_task="recap_render")
        self.progress_dialog.canceled.connect(self.cancel)
        self.progress_dialog.show()
        self.owner.refresh()
        QThreadPool.globalInstance().start(self.job)

    @Slot(int, int)
    def _render_progress(self, completed, total):
        if self.progress_dialog is not None:
            self.progress_dialog.set_progress(completed, total)

    @Slot()
    def cancel(self):
        if self.job is not None:
            self.job.canceled.set()
            self.message = t("Recap render canceled.")
            self._close_progress()
            self.refresh()

    def _close_progress(self):
        dialog, self.progress_dialog = self.progress_dialog, None
        if dialog is not None:
            dialog.dispose()

    @Slot(str, str)
    def _render_completed(self, path, error):
        self._close_progress()
        job = self.job
        current = self.panel.draft is self.draft_in_render
        canceled = job is not None and job.canceled.is_set()
        self.job = self.draft_in_render = None
        release(self)
        if current and not canceled:
            self.message = t("Could not render the recap. {error}", error=error) if error else ""
            if error:
                self.gui.log(f"[Recap Render] {error}")
            if path and job is not None:
                if job.rendered_preview is not None and (job.preview or job.preview_key is not None):
                    old_path = self._preview_path
                    self._preview_path = self._store_preview(job.rendered_preview, job.preview_key)
                    self._preview_key = job.preview_key
                    if old_path != self._preview_path:
                        self._remove_preview(old_path)
                if job.preview:
                    self._show_recap_preview(self._preview_path)
                else:
                    if job.preview_key is None:
                        self._remove_preview(job.rendered_preview)
                    self._open_rendered_project(path, job.subtitle_rows)
            elif job is not None:
                self._remove_preview(job.rendered_preview)
        elif job is not None:
            self._remove_preview(job.rendered_preview)
        self.owner.refresh()

    def _store_preview(self, path, destination):
        if destination is None:
            return path
        try:
            if path != destination:
                path.replace(destination)
        except OSError as exc:
            self.gui.log(f"[Recap Render] Could not cache the preview: {exc}")
            return path
        for previous in destination.parent.glob("preview-*.mp4"):
            if previous != destination and re.fullmatch(r"preview-[0-9a-f]{64}\.mp4", previous.name):
                self._remove_preview(previous)
        return destination

    @staticmethod
    def _remove_preview(path):
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _open_rendered_project(self, path, subtitle_rows):
        try:
            state = self.gui.project_service.ensure_project(path, input_language="vi")
            state.set_setting("recap_project", True)
            state.set_setting("tts_skipped", True)
            state.set_step_status("translate_raw", "skipped")
            state.set_step_status("refine_translation", "skipped")
            state.set_step_status("generate_tts", "skipped")
            state.set_step_status("mix_audio", "skipped")
            if subtitle_rows:
                state.set_setting("transcript_imported", True)
                self.gui.project_bridge.persist_transcription(state, subtitle_rows)
            else:
                state.set_step_status("transcribe", "skipped")
            self.gui.project_service.save_project(state)
            self.gui.persist_current_timeline_project_data()
            if getattr(self.gui, "performance_mode", False):
                self.gui.save_processing_profile()
            self.dialog.accept()
            self.gui.load_video_project(path)
            if Path(getattr(self.gui, "_current_video_path", "")).resolve() != Path(path).resolve():
                raise RuntimeError(t("Open the saved recap from the launcher to continue."))
        except Exception as exc:
            self.gui.show_error(t("Recap video saved"), path, str(exc))

    @Slot(int)
    def _on_closed(self, _result):
        self._restore_preview_on_open = False
        self._restore_preview_timer.stop()
        self.cancel()
        self.pause()
        self.owner.recap_voice._stop_preview()
        self._preview_active = False
        if self.player is not None:
            self.player.setSource(QUrl())
        self._seek_timer.stop()
        self._pending_seek = None
        self._needs_first_frame = False
        self.duration_ms = 0
        self._source_duration_ms = 0
        self._source_position_ms = 0

    def clear_project(self):
        self.draft_in_render = None
        self._source_rows = []
        if self.dialog.isVisible():
            self.dialog.reject()
        else:
            self.cancel()
        self._preview_path = self._preview_key = None
        self.message = ""
