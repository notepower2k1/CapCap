import os
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, QThreadPool, Signal, Slot

from i18n import t
from language_support import available_voice_engines
from runtime_profile import is_remote_profile
from services.engine_runtime import EngineRuntime
from services.recap_voice import audio_path, current_clip_path, synthesize_recap_segment


class _VoiceSignals(QObject):
    clip_ready = Signal(int, object)
    progress = Signal(int, int)
    completed = Signal(str)


class _RecapVoiceJob(QRunnable):
    def __init__(self, root, clips, voice, dictionary):
        super().__init__()
        self.root, self.clips, self.voice = root, clips, voice
        self.dictionary = dictionary
        self.model_id = (os.getenv("ELEVENLABS_MODEL") or "eleven_turbo_v2_5") if voice.startswith("elevenlabs:") else None
        self.canceled = Event()
        self.signals = _VoiceSignals()

    def run(self):
        error = ""
        try:
            engine = EngineRuntime()
            total = len(self.clips)
            for count, (index, narration) in enumerate(self.clips, 1):
                if self.canceled.is_set():
                    break
                audio = synthesize_recap_segment(
                    engine, self.root, index, narration, self.voice,
                    model_id=self.model_id, normalizer_dictionary=self.dictionary, is_canceled=self.canceled.is_set,
                )
                self.signals.clip_ready.emit(index, audio)
                self.signals.progress.emit(count, total)
        except InterruptedError:
            pass
        except Exception as exc:
            error = str(exc)
        self.signals.completed.emit(error)


def _duration_text(ms):
    seconds, fraction = divmod(ms, 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}.{fraction // 100}"


class RecapVoiceController(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.gui, self.panel = owner, owner.gui, owner.recap
        self.job = None
        self.job_draft = None
        self._completed_clips = 0
        self.preview_path = None
        self.message = ""
        self._catalog = None
        self._options = {}
        self._restricted = set()
        self._voice_menu_key = None
        self.panel.selection_changed.connect(self.refresh)
        self.panel.edited.connect(self.refresh)
        self.panel.voice_engine_combo.currentIndexChanged.connect(self.refresh)
        self.panel.voice_gender_combo.currentIndexChanged.connect(self.refresh)
        self.panel.recap_voice_combo.activated.connect(self.choose)
        self.panel.generate_segment_voice_btn.clicked.connect(self.generate_selected)
        self.panel.generate_all_voice_btn.clicked.connect(self.generate_all)
        self.panel.play_voice_btn.clicked.connect(self.listen)

    @property
    def busy(self):
        return self.job is not None

    def _voice_options(self, engine="all", gender="any"):
        catalog = self.gui.voice_catalog_entries_all or self.gui.voice_catalog_entries
        if catalog is not self._catalog:
            self._catalog = catalog
            self._options.clear()
            self._restricted = {self.gui._voice_catalog_data_value(entry) for entry in catalog
                                if not entry.get("is_usable_for_api", True)}
            combo = self.panel.voice_engine_combo
            selected = combo.currentData()
            with QSignalBlocker(combo):
                combo.clear()
                combo.addItem(t("All"), "all")
                for label, value in available_voice_engines("vi", catalog):
                    if value != "omnivoice" or not is_remote_profile():
                        combo.addItem(t(label), value)
                combo.setCurrentIndex(max(0, combo.findData(selected)))
        key = (engine, gender)
        if key not in self._options:
            entries = self.gui._voice_display_entries(target_language="vi", engine=engine, gender=gender)
            if engine == "piper":
                entries.extend(self.gui._voice_display_entries(target_language="vi", engine="edge", gender=gender))
            self._options[key] = {value: label for label, value in entries if value not in self._restricted
                                  and (not value.startswith("omnivoice:") or not is_remote_profile())}
        return self._options[key]

    def _voice(self, draft, options):
        if isinstance(draft.get("voice_id"), str) and draft["voice_id"]:
            return draft["voice_id"]
        current = self.gui.get_active_voice_name()
        return current if current in options else ""

    def _clip(self, segment, voice):
        return current_clip_path(self.owner.project_root, segment, voice)

    @Slot()
    def refresh(self):
        panel = self.panel
        draft = panel.draft
        if draft is None:
            return
        options = self._voice_options()
        voice = self._voice(draft, options)
        panel.voice_label.setText(t("Recap voice: {voice}", voice=options.get(voice, voice)) if voice
                                  else t("Choose a Vietnamese voice for recap."))
        matches = self._voice_options(panel.voice_engine_combo.currentData() or "all",
                                     panel.voice_gender_combo.currentData() or "any")
        panel.voice_label.setVisible(not voice or voice not in matches)
        menu_key = (tuple(matches.items()), voice)
        if menu_key != self._voice_menu_key:
            self._voice_menu_key = menu_key
            combo = panel.recap_voice_combo
            with QSignalBlocker(combo):
                combo.clear()
                combo.addItem(t("Choose recap voice" if matches else "No voices match the filters."), "")
                for value, label in matches.items():
                    combo.addItem(label, value)
                combo.setCurrentIndex(max(0, combo.findData(voice)))
        durations = [_duration_text(segment["audio"]["duration_ms"]) if self._clip(segment, voice) else ""
                     for segment in draft["segments"]]
        panel.set_clip_durations(durations)
        index = panel.segment_list.currentRow()
        segment = draft["segments"][index] if index >= 0 else None
        free = not self.busy and self.owner.job is None and not self.owner.recap_studio.busy
        panel.voice_engine_combo.setEnabled(free)
        panel.voice_gender_combo.setEnabled(free)
        panel.recap_voice_combo.setEnabled(free and bool(matches))
        panel.generate_segment_voice_btn.setEnabled(
            bool(free and voice in options and segment and segment["narration"].strip())
        )
        panel.generate_all_voice_btn.setEnabled(bool(free and voice in options and any(
            item["narration"].strip() and not duration for item, duration in zip(draft["segments"], durations)
        )))
        panel.generate_all_voice_btn.setToolTip("" if voice in options else t("Choose a Vietnamese voice for recap."))
        duration = durations[index] if index >= 0 else ""
        panel.play_voice_btn.setEnabled(bool(free and duration))
        status = self.message
        if not status and duration:
            status = t("Clip duration {time}", time=duration)
        elif not status and segment and segment.get("audio"):
            status = t("Generate this clip again to use the current narration and voice.")
        panel.voice_status.setText(status)
        panel.voice_status.setVisible(bool(status))

    @Slot(int)
    def choose(self, index):
        draft = self.panel.draft
        if draft is None or self.busy or self.owner.job is not None or self.owner.recap_studio.busy:
            return
        voice = self.panel.recap_voice_combo.itemData(index)
        if voice and voice in self._voice_options():
            self._stop_preview()
            draft["voice_id"] = voice
            self.message = ""
            self.owner._save_recap_edits()
            self.owner.refresh()
        else:
            self._voice_menu_key = None
            self.refresh()

    @Slot()
    def generate_selected(self):
        index = self.panel.segment_list.currentRow()
        if index >= 0:
            self._start(index)

    @Slot()
    def generate_all(self):
        self._start(None)

    def _start(self, selected_index):
        draft, root = self.panel.draft, self.owner.project_root
        if draft is None or not root or self.busy or self.owner.job is not None or self.owner.recap_studio.busy:
            return
        if self.gui._omnivoice_quality_busy():
            self.message = t("Wait for the current voice task to finish.")
            self.refresh()
            return
        options = self._voice_options()
        voice = self._voice(draft, options)
        if voice not in options:
            return
        clips = [(index, segment["narration"].strip()) for index, segment in enumerate(draft["segments"])
                 if segment["narration"].strip() and (index == selected_index or
                    (selected_index is None and not self._clip(segment, voice)))]
        if not clips or not self.gui.ensure_required_resources("Recap voice", include_voice=True, voice_name=voice):
            return
        state = self.gui.current_project_state
        if self.panel.draft is not draft or state is None or state.project_root != root:
            return
        self._stop_preview()
        dictionary = dict(state.settings.get("normalizer_dictionary") or {})
        draft["voice_id"] = voice
        self.owner._save_recap_edits()
        self.message = ""
        self.job_draft = draft
        self.job = _RecapVoiceJob(root, clips, voice, dictionary)
        self.job.signals.clip_ready.connect(self._on_clip)
        self.job.signals.progress.connect(self._on_progress)
        self.job.signals.completed.connect(self._on_completed)
        self.owner.recap_studio.pause()
        self._on_progress(0, len(clips))
        self.owner.refresh()
        QThreadPool.globalInstance().start(self.job)

    @Slot(int, object)
    def _on_clip(self, index, audio):
        state = self.gui.current_project_state
        draft = self.panel.draft
        if (self.job is None or self.job.canceled.is_set() or draft is None or draft is not self.job_draft
                or state is None or state.project_root != self.job.root):
            if self.job:
                self._remove_clip(self.job.root, audio)
            return
        segment = draft["segments"][index]
        previous = segment.get("audio")
        segment["audio"] = audio
        if self.owner._save_recap_edits():
            self._remove_clip(self.job.root, previous)
        self.owner.refresh()

    @staticmethod
    def _remove_clip(root, audio):
        previous_path = audio_path(root, audio)
        if previous_path is not None:
            try:
                previous_path.unlink()
            except OSError:
                pass

    @Slot(int, int)
    def _on_progress(self, completed, total):
        if self.job_draft is None or self.job.canceled.is_set():
            return
        self._completed_clips = completed
        self.message = t("Generating recap voice ({completed}/{total})…", completed=completed, total=total)
        self.owner.recap_studio.show_voice_progress(self.message, completed=completed, total=total)
        self.refresh()

    @Slot()
    def cancel(self):
        if self.job is None:
            return
        self.job.canceled.set()
        self.message = t("Canceling recap voice generation…")
        self.owner.recap_studio.show_voice_progress(self.message, completed=self._completed_clips,
                                                    total=len(self.job.clips), canceling=True)
        self.refresh()

    @Slot(str)
    def _on_completed(self, error):
        belongs_to_draft = self.job_draft is not None and self.panel.draft is self.job_draft
        canceled = self.job is not None and self.job.canceled.is_set()
        self.job = self.job_draft = None
        if belongs_to_draft:
            if canceled:
                self.message = t("Recap voice generation canceled.")
            elif error:
                self.message = t("Could not generate recap voice. {error}", error=error)
            else:
                self.message = ""
            if error:
                self.gui.log(f"[Recap Voice] {error}")
            result = self.message or t("Recap narration ready for {count} clips.", count=self._completed_clips)
            self.owner.recap_studio.show_voice_progress(result)
        self.owner.refresh()

    @Slot()
    def listen(self):
        draft = self.panel.draft
        index = self.panel.segment_list.currentRow()
        if draft is None or index < 0 or self.busy:
            return
        path = self._clip(draft["segments"][index], self._voice(draft, self._voice_options()))
        if path is None:
            self.refresh()
            return
        try:
            self.owner.recap_studio.pause()
            self.gui.play_audio_preview_file(str(path))
            self.preview_path = str(path)
        except (OSError, RuntimeError) as exc:
            self.message = t("Could not play the recap clip. {error}", error=str(exc))
            self.refresh()

    def _stop_preview(self):
        if self.preview_path is not None and self.preview_path == getattr(self.gui, "_last_audio_preview_path", None):
            self.gui.stop_audio_preview()
        self.preview_path = None

    def clear_project(self):
        self._stop_preview()
        if self.job is not None:
            self.job.canceled.set()
        self.job_draft = None
        self.message = ""
        self.owner.recap_studio.show_voice_progress()
