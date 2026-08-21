import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QProgressDialog, QTextEdit, QVBoxLayout

from worker_adapters import RewriteTranslationWorker, TranscriptionWorker, TranslationWorker
from translation import TranslationOrchestrator, load_prompt_options


class SubtitleController:
    REWRITE_STYLE_PRESETS_FILE = "rewrite_style_presets.json"

    def __init__(self, gui):
        self.gui = gui

    def _show_translation_progress(self, *, is_retranslation: bool):
        """Show elapsed time for both first-time and repeated translation."""
        self._close_translation_progress()
        provider = self.gui._selected_ai_provider_label() if hasattr(self.gui, "_selected_ai_provider_label") else "selected provider"
        action = "Re-translating" if is_retranslation else "Translating"
        dialog = QProgressDialog(
            f"{action} subtitles with {provider}...\nElapsed: 00:00",
            None,
            0,
            0,
            self.gui,
        )
        dialog.setWindowTitle(f"{action} Subtitles")
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(430)
        dialog.setValue(0)
        dialog.setStyleSheet(
            "QProgressDialog { background-color: #101826; color: #e6eef9; }"
            "QLabel { color: #e6eef9; }"
            "QPushButton { background: #22344c; color: #e6eef9; border: 1px solid #36516f; border-radius: 6px; padding: 5px 14px; }"
        )
        started = time.monotonic()
        timer = QTimer(dialog)

        def update_elapsed():
            elapsed = int(time.monotonic() - started)
            dialog.setLabelText(
                f"{action} subtitles with {provider}...\n"
                f"Elapsed: {elapsed // 60:02d}:{elapsed % 60:02d}\n"
                "Large subtitle projects can take a few minutes."
            )

        timer.setInterval(1000)
        timer.timeout.connect(update_elapsed)
        timer.start()
        self.gui._translation_progress_dialog = dialog
        self.gui._translation_progress_timer = timer
        dialog.show()

    def _close_translation_progress(self):
        timer = getattr(self.gui, "_translation_progress_timer", None)
        if timer is not None:
            timer.stop()
        self.gui._translation_progress_timer = None
        dialog = getattr(self.gui, "_translation_progress_dialog", None)
        self.gui._translation_progress_dialog = None
        if dialog is not None:
            dialog.hide()
            dialog.deleteLater()

    def run_transcription(self):
        audio_src = self.gui.audio_source_edit.text()
        if not audio_src or not os.path.exists(audio_src):
            QMessageBox.warning(self.gui, "Error", "Audio source file not found! Please extract audio first.")
            return

        model_path = self.gui.get_whisper_model_path()
        lang = self.gui.get_source_language_code()

        self.gui.transcript_text.setText("Transcribing... please wait (Loading...)")
        self.gui.transcribe_btn.setEnabled(False)
        self.gui.progress_bar.setValue(40)
        self.gui.update_project_step("transcribe", "running")

        self.gui.transcription_thread = TranscriptionWorker(audio_src, model_path, lang)
        self.gui.transcription_thread.finished.connect(self.gui.on_transcription_finished)
        self.gui.transcription_thread.start()

    def on_transcription_finished(self, segments, error=""):
        self.gui.transcribe_btn.setEnabled(True)
        if error or not segments:
            self.gui.update_project_step("transcribe", "failed")
            if error:
                self.gui.show_error(
                    "Transcription Failed",
                    "Could not transcribe the audio.",
                    error,
                )
            else:
                QMessageBox.warning(self.gui, "Warning", "Transcription failed or returned no results.")
            self.gui._pipeline_fail("Transcription failed.")
            return

        self.gui.current_segments = segments
        self.gui.current_translated_segments = []
        self.gui.progress_bar.setValue(60)
        self.gui.apply_segments_to_timeline()

        srt_text = self.gui.format_to_srt(segments)
        self.gui.transcript_text.setText(srt_text)

        video_path = self.gui.video_path_edit.text()
        if video_path:
            file_basename = os.path.splitext(os.path.basename(video_path))[0]
            out_folder = self.gui.get_project_temp_dir("subtitle")
            os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, file_basename + "_original.srt")
            from subtitle_builder import generate_srt

            generate_srt(segments, out_path)
            self.gui.last_original_srt_path = out_path
            self.gui.processed_artifacts["srt_original"] = out_path
            self.gui.persist_transcription_project_data(segments, out_path)
            QMessageBox.information(self.gui, "Success", f"Transcription completed!\nOriginal SRT saved to: {out_path}")
        else:
            self.gui.persist_transcription_project_data(segments)
            QMessageBox.information(self.gui, "Success", "Transcription completed!")

        self.gui.refresh_ui_state()
        self.gui.schedule_auto_frame_preview()
        # The OCR crop is only an editor aid. Do not leave it covering the
        # preview after a normal OCR transcript has completed.
        if self.gui.get_transcription_engine() == "ocr":
            self.gui.toggle_ocr_overlay_visibility(False)
            self.gui.log("[OCR Region] Hidden after OCR transcription completed.")
        self.gui._pipeline_advance("transcription")

    def run_translation(self):
        existing = getattr(self.gui, "translation_thread", None)
        if existing is not None and existing.isRunning():
            self.gui.log("[Translation] A translation request is already running.")
            return
        srt_source = self.gui.transcript_text.toPlainText()
        if not srt_source or not srt_source.strip():
            QMessageBox.warning(self.gui, "Error", "No transcription available to translate!")
            return

        state = self.gui.ensure_current_project()
        translation_signature = self.gui.build_current_translation_signature()
        if state and translation_signature:
            cached_signature = str(state.settings.get("translation_signature", "") or "").strip()
            cached_translation_path = str(state.artifacts.get("translation_final", "") or "").strip()
            if cached_signature == translation_signature and cached_translation_path and os.path.exists(cached_translation_path):
                if not (self.gui.current_translated_segments or self.gui.translated_text.toPlainText().strip()):
                    self.gui.load_project_context(state)
                self.gui.progress_bar.setValue(100)
                self.gui.refresh_ui_state()
                QMessageBox.information(
                    self.gui,
                    "Using Existing Translation",
                    "Vietnamese subtitles are unchanged, so CapCap reused the existing generated result instead of calling AI again.",
                )
                return

        model_path = None
        src_lang = self.gui.get_source_language_code()
        enable_polish = bool(self.gui.is_ai_polish_enabled())
        is_retranslation = bool(
            self.gui.current_translated_segments or self.gui.translated_text.toPlainText().strip()
        )
        self.gui.translated_text.setText("Translating with the selected provider... please wait.")
        self.gui.translate_btn.setEnabled(False)
        self.gui.progress_bar.setValue(80)
        self.gui.update_project_step("translate_raw", "running")
        self._show_translation_progress(is_retranslation=is_retranslation)

        self.gui.translation_thread = TranslationWorker(
            srt_source, model_path, src_lang, self.gui.get_target_language_code(), enable_polish
        )
        self.gui.translation_thread.finished.connect(self.gui.on_translation_finished)
        self.gui.translation_thread.start()

    def on_translation_finished(self, translated_srt, error, fallback_notice=""):
        self._close_translation_progress()
        self.gui.translate_btn.setEnabled(True)
        if error or not translated_srt:
            self.gui.update_project_step("translate_raw", "failed")
            self.gui.show_error(
                "Translation Failed",
                "Could not complete the Vietnamese translation.",
                error or "The translator API returned an empty result.",
            )
            self.gui._pipeline_fail("Translation failed.")
            return

        self.gui.progress_bar.setValue(100)
        self.gui.translated_text.setText(translated_srt)
        self.gui.apply_edited_translation(show_message=False, force_apply=True)

        video_path = self.gui.video_path_edit.text()
        if video_path:
            file_basename = os.path.splitext(os.path.basename(video_path))[0]
            out_folder = self.gui.get_project_temp_dir("subtitle")
            os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, file_basename + "_vi.srt")
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write(translated_srt)
            self.gui.last_translated_srt_path = out_path
            self.gui.processed_artifacts["srt_translated"] = out_path
            self.gui.persist_translation_project_data(self.gui.current_translated_segments, out_path)
            message = f"Process complete! Subtitle saved and loaded for preview:\n{out_path}"
            if fallback_notice:
                message = "Translation completed using Google Translate (AI Provider unavailable).\n\n" + message
            QMessageBox.information(self.gui, "Finished", message)
        else:
            self.gui.persist_translation_project_data(self.gui.current_translated_segments)
            message = "Translation completed using Google Translate (AI Provider unavailable)." if fallback_notice else "Translation complete!"
            QMessageBox.information(self.gui, "Finished", message)

        if fallback_notice:
            self.gui.log(f"[Translation] {fallback_notice}")

        self.gui.refresh_ui_state()
        self.gui._pipeline_advance("translation")

    def _collapse_translated_segments_for_rewrite(self, source_segments, translated_segments):
        source_segments = list(source_segments or [])
        translated_segments = list(translated_segments or [])
        if not translated_segments:
            return []
        if len(source_segments) == len(translated_segments):
            collapsed = []
            for idx, seg in enumerate(translated_segments):
                item = dict(seg)
                if idx < len(source_segments):
                    item["source_text"] = source_segments[idx].get("source_text") or source_segments[idx].get("text", "")
                collapsed.append(item)
            return collapsed

        collapsed = []
        idx = 0
        while idx < len(translated_segments):
            seg = dict(translated_segments[idx] or {})
            group_id = str(seg.get("tts_group_id", "") or "").strip()
            group_items = [seg]
            idx += 1
            if group_id:
                while idx < len(translated_segments):
                    candidate = dict(translated_segments[idx] or {})
                    if str(candidate.get("tts_group_id", "") or "").strip() != group_id:
                        break
                    group_items.append(candidate)
                    idx += 1
            base_text = ' '.join(str(group_items[0].get("tts_text") or "").split()).strip()
            if not base_text:
                base_text = ' '.join(' '.join(str(item.get("text") or "").split()).strip() for item in group_items).strip()
            collapsed.append({
                "start": float(group_items[0].get("tts_group_start", group_items[0].get("start", 0.0)) or group_items[0].get("start", 0.0)),
                "end": float(group_items[-1].get("tts_group_end", group_items[-1].get("end", 0.0)) or group_items[-1].get("end", 0.0)),
                "text": base_text,
                "tts_text": base_text,
                "tts_group_id": group_id,
                "tts_group_start": float(group_items[0].get("tts_group_start", group_items[0].get("start", 0.0)) or group_items[0].get("start", 0.0)),
                "tts_group_end": float(group_items[-1].get("tts_group_end", group_items[-1].get("end", 0.0)) or group_items[-1].get("end", 0.0)),
                "words": list(group_items[0].get("words", []) or []),
                "manual_highlights": list(group_items[0].get("manual_highlights", []) or []),
            })
        if len(collapsed) == len(source_segments):
            for i, seg in enumerate(collapsed):
                seg["source_text"] = source_segments[i].get("source_text") or source_segments[i].get("text", "")
        return collapsed

    def _expand_rewrite_segments_for_current_layout(self, rewritten_segments, source_segments):
        normalized = []
        source_segments = list(source_segments or [])
        for idx, seg in enumerate(rewritten_segments or []):
            item = dict(seg)
            if idx < len(source_segments):
                base = source_segments[idx]
                item["source_text"] = base.get("source_text") or base.get("text", "")
                item.setdefault("words", list(base.get("words", []) or []))
                item.setdefault("manual_highlights", list(base.get("manual_highlights", []) or []))
            normalized.append(item)
        single_line_enabled = bool(getattr(self.gui, "subtitle_single_line_cb", None) and self.gui.subtitle_single_line_cb.isChecked())
        if not single_line_enabled:
            return normalized
        orchestrator = TranslationOrchestrator()
        provider_type, polisher = orchestrator._resolve_ai_provider()
        if not polisher.is_configured():
            polisher = None
        return orchestrator._split_segments_for_single_line(
            normalized,
            polisher=polisher,
            provider_type=provider_type,
            target_lang=self.gui.get_target_language_code(),
        )

    def run_rewrite_translation(self):
        if hasattr(self.gui, "_translation_phase_complete") and not self.gui._translation_phase_complete():
            QMessageBox.information(self.gui, "Rewrite Unavailable", "Complete the Translation phase before rewriting subtitles.")
            return
        source_segments = list(self.gui.current_segments or [])
        translated_segments = list(self.gui.current_translated_segments or [])
        if not source_segments:
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Original subtitles are missing. Please create or load the original subtitle track first.")
            return
        if not translated_segments:
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Vietnamese subtitles are missing. Please translate or load them first.")
            return
        rewrite_segments = self._collapse_translated_segments_for_rewrite(source_segments, translated_segments)
        if len(source_segments) != len(rewrite_segments):
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Could not rebuild the original subtitle groups for rewrite safely.")
            return
        self.gui._rewrite_source_segments = source_segments
        self.gui._rewrite_base_translated_segments = rewrite_segments
        self._open_rewrite_dialog(source_segments, rewrite_segments)

    def run_rewrite_selected_segment(self):
        if hasattr(self.gui, "_translation_phase_complete") and not self.gui._translation_phase_complete():
            QMessageBox.information(self.gui, "Rewrite Unavailable", "Complete the Translation phase before rewriting subtitles.")
            return
        source_segments = list(self.gui.current_segments or [])
        translated_segments = list(self.gui.current_translated_segments or [])
        if not source_segments:
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Original subtitles are missing. Please create or load the original subtitle track first.")
            return
        if not translated_segments:
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Vietnamese subtitles are missing. Please translate or load them first.")
            return
        index = int(getattr(self.gui, "_selected_segment_index", -1))
        if not (0 <= index < len(translated_segments) and index < len(source_segments)):
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Please select a subtitle block in the inspector first.")
            return

        # Kept as a compatibility entry point for old shortcuts/plugins. The
        # visible UI now uses one Rewrite button with a scope selector.
        self.gui._rewrite_initial_scope = "selected"
        self.gui._rewrite_initial_segment_index = index
        self._open_rewrite_dialog(source_segments, translated_segments, initial_scope="selected")

    def _validate_rewrite_srt(self, srt_text: str):
        normalized_text = str(srt_text or "").strip()
        expected_segments = getattr(self.gui, "_rewrite_base_translated_segments", None) or self.gui.current_translated_segments or self.gui.current_segments or []
        expected_len = len(expected_segments) or None
        is_valid, parsed_segments, validation_error = self.gui.validate_srt_text(normalized_text, expected_len=expected_len)
        if is_valid:
            return True, parsed_segments, "srt", ""
        return False, [], "invalid", validation_error or "Invalid SRT format."

    def on_rewrite_translation_finished(self, translated_srt, error):
        self.gui.rewrite_translation_btn.setEnabled(True)
        self.gui.rewrite_translation_btn.setText("Rewrite")
        if hasattr(self.gui, "_rewrite_generate_btn"):
            self.gui._rewrite_generate_btn.setEnabled(True)
            self.gui._rewrite_generate_btn.setText("Generate Preview")
        if error or not translated_srt:
            self.gui.update_project_step("refine_translation", "failed")
            self.gui.show_error(
                "Rewrite Failed",
                "Could not rewrite the Vietnamese subtitles with AI.",
                error or "The AI rewrite service returned an empty result.",
            )
            self.gui.refresh_ui_state()
            return

        if hasattr(self.gui, "_rewrite_preview_edit"):
            self.gui._rewrite_preview_ready = True
            self.gui._rewrite_preview_edit.setPlainText(translated_srt)
        if hasattr(self.gui, "_rewrite_preview_status_updater"):
            self.gui._rewrite_preview_status_updater()
        if hasattr(self.gui, "_rewrite_status_label"):
            self.gui._rewrite_status_label.setText("AI preview is ready. Review the read-only result, then press Apply to update the selected subtitles.")
            self.gui._rewrite_status_label.setStyleSheet("color: #8ad7ff; font-size: 12px; font-weight: 700;")
        self.gui.update_project_step("refine_translation", "done")
        self.gui.refresh_ui_state()

    def on_rewrite_selected_segment_finished(self, translated_srt, error):
        def _cleanup_selected_rewrite_state():
            for attr in (
                "_rewrite_source_segments",
                "_rewrite_base_translated_segments",
                "_rewrite_selected_segment_index",
                "_rewrite_selected_segment_original_label",
            ):
                if hasattr(self.gui, attr):
                    delattr(self.gui, attr)

        original_label = str(getattr(self.gui, "_rewrite_selected_segment_original_label", "") or "Rewrite Selected Subtitle")
        if hasattr(self.gui, "rewrite_selected_segment_btn"):
            self.gui.rewrite_selected_segment_btn.setEnabled(True)
            self.gui.rewrite_selected_segment_btn.setText(original_label)

        index = int(getattr(self.gui, "_rewrite_selected_segment_index", -1))
        if error or not translated_srt:
            self.gui.update_project_step("refine_translation", "failed")
            self.gui.show_error(
                "Rewrite Failed",
                "Could not rewrite the selected subtitle block with AI.",
                error or "The AI rewrite service returned an empty result.",
            )
            self.gui.refresh_ui_state()
            _cleanup_selected_rewrite_state()
            return

        if not (0 <= index < len(self.gui.current_translated_segments or [])):
            self.gui.refresh_ui_state()
            _cleanup_selected_rewrite_state()
            return

        is_valid_srt, parsed_segments, _mode, validation_error = self._validate_rewrite_srt(translated_srt)
        if not is_valid_srt or not parsed_segments:
            self.gui.show_error(
                "Rewrite Failed",
                "The AI rewrite result for this block was not in valid SRT format.",
                validation_error or translated_srt,
            )
            self.gui.update_project_step("refine_translation", "failed")
            self.gui.refresh_ui_state()
            _cleanup_selected_rewrite_state()
            return

        rewritten_text = str(parsed_segments[0].get("text", "") or "").strip()
        target_segment = self.gui.current_translated_segments[index]
        target_segment["text"] = rewritten_text
        target_segment["tts_text"] = rewritten_text
        target_segment.setdefault("manual_highlights", [])
        self.gui._reconcile_manual_highlights(target_segment)
        self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(self.gui.current_translated_segments, translated=True)
        self.gui._sync_hidden_translated_text_from_segments()
        self.gui.apply_segments_to_timeline()
        self.gui.persist_current_timeline_project_data()
        self.gui.refresh_auto_keyword_highlights(force=True)
        self.gui.schedule_live_subtitle_preview_refresh()
        self.gui.schedule_auto_frame_preview()
        self.gui.update_project_step("refine_translation", "done")
        self.gui.sync_segment_editor_rows()
        self.gui.refresh_ui_state()
        _cleanup_selected_rewrite_state()

    def apply_rewrite_preview(self):
        preview_edit = getattr(self.gui, "_rewrite_preview_edit", None)
        if not preview_edit:
            return
        translated_srt = preview_edit.toPlainText().strip()
        if not translated_srt:
            QMessageBox.warning(self.gui, "Rewrite", "Please enter or generate rewritten subtitle content first.")
            return

        is_valid_srt, parsed_segments, _validation_mode, validation_error = self._validate_rewrite_srt(translated_srt)
        if not is_valid_srt:
            QMessageBox.warning(
                self.gui,
                "Invalid SRT",
                f"Rewrite content must stay in valid SRT format.\n\n{validation_error}\n\nExample:\n1\n00:00:01,000 --> 00:00:02,000\nXin chao",
            )
            return

        rewrite_source_segments = getattr(self.gui, "_rewrite_source_segments", None) or list(self.gui.current_segments or [])
        applied_segments = self._expand_rewrite_segments_for_current_layout(parsed_segments, rewrite_source_segments)

        selected_indices = [
            int(index)
            for index in list(getattr(self.gui, "_rewrite_selected_indices", []) or [])
            if 0 <= int(index) < len(self.gui.current_translated_segments or [])
        ]
        segment_index = int(getattr(self.gui, "_rewrite_selected_segment_index", -1))
        if selected_indices:
            if len(applied_segments) != len(selected_indices):
                QMessageBox.warning(
                    self.gui,
                    "Rewrite",
                    "The AI returned a different number of cues than were checked. Nothing was changed.",
                )
                return
            for target_index, replacement in zip(selected_indices, applied_segments):
                self.gui.current_translated_segments[target_index] = replacement
            self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(self.gui.current_translated_segments, translated=True)
            normalized_srt = self.gui.format_to_srt(self.gui.current_translated_segments)
        elif segment_index >= 0 and segment_index < len(self.gui.current_translated_segments or []):
            self.gui.current_translated_segments[segment_index] = applied_segments[0] if applied_segments else dict(self.gui.current_translated_segments[segment_index])
            self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(self.gui.current_translated_segments, translated=True)
            normalized_srt = self.gui.format_to_srt(self.gui.current_translated_segments)
        else:
            self.gui.current_translated_segments = applied_segments
            self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(applied_segments, translated=True)
            normalized_srt = self.gui.format_to_srt(self.gui.current_translated_segments)

        self.gui.refresh_auto_keyword_highlights(force=True)
        self.gui.translated_text.setText(normalized_srt)
        self.gui.apply_segments_to_timeline()

        out_path = self.gui.last_translated_srt_path
        if not out_path:
            video_path = self.gui.video_path_edit.text().strip()
            if video_path:
                file_basename = os.path.splitext(os.path.basename(video_path))[0]
                out_folder = self.gui.get_project_temp_dir("subtitle")
                os.makedirs(out_folder, exist_ok=True)
                out_path = os.path.join(out_folder, file_basename + "_vi.srt")
        if out_path:
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write(normalized_srt)
            self.gui.last_translated_srt_path = out_path
            self.gui.processed_artifacts["srt_translated"] = out_path
            self.gui.persist_translation_project_data(self.gui.current_translated_segments, out_path)
        else:
            self.gui.persist_translation_project_data(self.gui.current_translated_segments)

        dialog = getattr(self.gui, "_rewrite_dialog", None)
        if dialog:
            dialog.accept()
        self.gui.schedule_live_subtitle_preview_refresh()
        self.gui.schedule_auto_frame_preview()
        self.gui.sync_segment_editor_rows()
        QMessageBox.information(self.gui, "Rewrite Applied", "The rewritten SRT was applied to the subtitle editor.")
        self.gui.refresh_ui_state()

    def _open_rewrite_dialog(self, source_segments, translated_segments, *, initial_scope="all"):
        source_segments = list(source_segments or [])
        translated_segments = list(translated_segments or [])
        selected_index = int(getattr(self.gui, "_rewrite_initial_segment_index", -1))
        if not (0 <= selected_index < len(translated_segments)):
            selected_index = int(getattr(self.gui, "_selected_segment_index", -1))
        can_select_one = 0 <= selected_index < len(translated_segments) and selected_index < len(source_segments)
        dialog = QDialog(self.gui)
        dialog.setWindowTitle("Rewrite Subtitles")
        dialog.setModal(True)
        dialog.setMinimumWidth(760)
        dialog.setMinimumHeight(640)
        dialog.setStyleSheet(
            """
            QDialog { background-color: #0f1724; }
            QLabel { color: #d7e3f4; background: transparent; }
            QLabel#statusHeadline { color: #f8fbff; font-size: 16px; font-weight: 700; }
            QLabel#helperLabel { color: #9fb3ca; font-size: 12px; }
            QComboBox, QTextEdit {
                background-color: #132033;
                color: #f8fbff;
                border: 1px solid #2f4868;
                border-radius: 10px;
                padding: 8px 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #132033;
                color: #f8fbff;
                border: 1px solid #2f4868;
                selection-background-color: #24486c;
            }
            QPushButton {
                background-color: #22344d;
                color: #f8fbff;
                border: 1px solid #34506f;
                border-radius: 10px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #29405d; }
            """
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("AI Rewrite")
        title.setObjectName("statusHeadline")
        layout.addWidget(title)

        hint = QLabel("Choose the rewrite scope and style, then ask AI for a revised subtitle. Review the read-only result and apply it when ready. For manual corrections, use Subtitle Editor.")
        hint.setObjectName("helperLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Scope:"))
        scope_combo = QComboBox(dialog)
        scope_combo.addItem("All translated subtitles", "all")
        scope_combo.addItem("Checked subtitles", "checked")
        if str(initial_scope or "all").strip().lower() == "selected" and can_select_one:
            scope_combo.setCurrentIndex(1)
        scope_row.addWidget(scope_combo, 1)
        scope_hint = QLabel("Use the checkboxes beside Rewrite SRT" if translated_segments else "No translated subtitles")
        scope_hint.setObjectName("helperLabel")
        scope_row.addWidget(scope_hint)
        layout.addLayout(scope_row)

        style_combo = QComboBox(dialog)
        for label, instruction in load_prompt_options(self.REWRITE_STYLE_PRESETS_FILE):
            style_combo.addItem(label, instruction)
        style_combo.addItem("Custom", "custom")
        layout.addWidget(style_combo)

        custom_style_cb = QCheckBox("Add extra custom instruction", dialog)
        layout.addWidget(custom_style_cb)

        custom_prompt = QTextEdit(dialog)
        custom_prompt.setPlaceholderText("Example: Keep the words very short and modern, suitable for TikTok voiceover.")
        custom_prompt.setFixedHeight(88)
        custom_prompt.setVisible(False)
        layout.addWidget(custom_prompt)

        status_label = QLabel("Choose a scope and generate an AI rewrite preview.")
        status_label.setObjectName("helperLabel")
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        preview_label = QLabel("Rewrite SRT — select cues to rewrite")
        preview_label.setObjectName("sectionTitle")
        layout.addWidget(preview_label)

        preview_split = QHBoxLayout()
        preview_split.setSpacing(8)

        check_list = QListWidget(dialog)
        check_list.setMinimumWidth(285)
        check_list.setMaximumWidth(360)
        check_list.setMinimumHeight(96)
        check_list.setStyleSheet(
            "QListWidget { background: #132033; color: #eff6ff; border: 1px solid #2f4868; border-radius: 8px; padding: 3px; }"
            "QListWidget::item { padding: 5px 4px; }"
            "QListWidget::item:selected { background: #244d70; }"
        )
        for index, segment in enumerate(translated_segments):
            excerpt = " ".join(str(segment.get("text", "") or "").split())
            if len(excerpt) > 74:
                excerpt = excerpt[:71] + "..."
            start = float(segment.get("start", 0.0) or 0.0)
            end = float(segment.get("end", start) or start)
            start_ms = max(0, int(round(start * 1000)))
            end_ms = max(start_ms, int(round(end * 1000)))
            start_time = f"{start_ms // 3600000:02d}:{(start_ms // 60000) % 60:02d}:{(start_ms // 1000) % 60:02d},{start_ms % 1000:03d}"
            end_time = f"{end_ms // 3600000:02d}:{(end_ms // 60000) % 60:02d}:{(end_ms // 1000) % 60:02d},{end_ms % 1000:03d}"
            item = QListWidgetItem(f"{index + 1:03d}  {start_time} → {end_time}\n{excerpt}", check_list)
            item.setData(Qt.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            if str(initial_scope or "all").strip().lower() == "selected" and index == selected_index:
                item.setCheckState(Qt.Checked)
        preview_split.addWidget(check_list, 1)

        preview_edit = QTextEdit(dialog)
        preview_edit.setPlaceholderText("The AI rewrite preview will appear here.")
        preview_edit.setPlainText(self.gui.format_to_srt(translated_segments))
        preview_edit.setReadOnly(True)
        preview_split.addWidget(preview_edit, 2)
        layout.addLayout(preview_split, 1)

        self.gui._rewrite_dialog = dialog
        self.gui._rewrite_preview_edit = preview_edit
        self.gui._rewrite_preview_ready = False
        self.gui._rewrite_selected_segment_index = -1
        self.gui._rewrite_selected_indices = []

        button_row = QHBoxLayout()
        button_row.addStretch()
        close_btn = QPushButton("Close", dialog)
        generate_btn = QPushButton("Generate Preview", dialog)
        apply_btn = QPushButton("Apply Rewrite", dialog)
        button_row.addWidget(close_btn)
        button_row.addWidget(generate_btn)
        button_row.addWidget(apply_btn)
        layout.addLayout(button_row)

        self.gui._rewrite_apply_btn = apply_btn
        self.gui._rewrite_generate_btn = generate_btn
        self.gui._rewrite_status_label = status_label

        def _toggle_custom_instruction(checked: bool):
            custom_prompt.setVisible(bool(checked))

        def _build_style_instruction() -> str:
            base_instruction = str(style_combo.currentData() or "").strip()
            if base_instruction == "custom":
                base_instruction = ""
            extra_instruction = custom_prompt.toPlainText().strip() if custom_style_cb.isChecked() else ""
            return " ".join(part for part in [base_instruction, extra_instruction] if part).strip()

        def _scope_segments():
            if scope_combo.currentData() == "checked":
                indices = [
                    int(item.data(Qt.UserRole))
                    for row in range(check_list.count())
                    for item in [check_list.item(row)]
                    if item.checkState() == Qt.Checked
                ]
                indices = [index for index in indices if 0 <= index < len(source_segments) and index < len(translated_segments)]
                return [source_segments[index] for index in indices], [translated_segments[index] for index in indices]
            return source_segments, translated_segments

        def _scope_indices():
            if scope_combo.currentData() != "checked":
                return []
            return [
                int(item.data(Qt.UserRole))
                for row in range(check_list.count())
                for item in [check_list.item(row)]
                if item.checkState() == Qt.Checked
                and 0 <= int(item.data(Qt.UserRole)) < len(translated_segments)
            ]

        def _reset_scope_preview():
            _source, _translated = _scope_segments()
            selected_indices = _scope_indices()
            self.gui._rewrite_selected_indices = selected_indices
            self.gui._rewrite_selected_segment_index = selected_indices[0] if len(selected_indices) == 1 else -1
            self.gui._rewrite_source_segments = _source
            self.gui._rewrite_base_translated_segments = _translated
            self.gui._rewrite_preview_ready = False
            preview_edit.setPlainText(self.gui.format_to_srt(_translated))
            if scope_combo.currentData() == "checked" and not selected_indices:
                status_label.setText("Check at least one subtitle before generating an AI rewrite.")
                scope_hint.setText("0 cues checked")
            elif scope_combo.currentData() == "checked":
                status_label.setText("Choose a scope and generate an AI rewrite preview.")
                scope_hint.setText(f"{len(selected_indices)} cue(s) checked")
            else:
                status_label.setText("Choose a scope and generate an AI rewrite preview.")
                scope_hint.setText("All translated subtitles")
            status_label.setStyleSheet("")
            apply_btn.setEnabled(False)

        def _update_preview_validity():
            current_text = preview_edit.toPlainText().strip()
            if not current_text:
                status_label.setText("Rewrite SRT is empty.")
                status_label.setStyleSheet("color: #f6c177; font-size: 12px; font-weight: 600;")
                apply_btn.setEnabled(False)
                return
            is_valid_srt, parsed_segments, validation_mode, validation_error = self._validate_rewrite_srt(current_text)
            if is_valid_srt and validation_mode == "srt":
                if getattr(self.gui, "_rewrite_preview_ready", False):
                    status_label.setText(f"AI rewrite ready. Segments: {len(parsed_segments)}.")
                    status_label.setStyleSheet("color: #78f0b0; font-size: 12px; font-weight: 700;")
                    apply_btn.setEnabled(True)
                else:
                    status_label.setText("Current subtitles are shown for reference. Generate an AI rewrite to enable Apply.")
                    status_label.setStyleSheet("color: #9fb3ca; font-size: 12px; font-weight: 600;")
                    apply_btn.setEnabled(False)
            else:
                status_label.setText(f"Invalid SRT. {validation_error or 'Keep standard blocks: index, time range, then subtitle text.'}")
                status_label.setStyleSheet("color: #ff8f8f; font-size: 12px; font-weight: 700;")
                apply_btn.setEnabled(False)

        def _start_preview_generation():
            rewrite_source_segments, rewrite_base_segments = _scope_segments()
            if not rewrite_base_segments:
                QMessageBox.information(dialog, "Rewrite", "Check at least one subtitle before generating an AI rewrite.")
                return
            style_instruction = _build_style_instruction()
            status_label.setText("Generating rewrite preview with AI...")
            apply_btn.setEnabled(False)
            generate_btn.setEnabled(False)
            generate_btn.setText("Generating...")
            self.gui.rewrite_translation_btn.setEnabled(False)
            self.gui.rewrite_translation_btn.setText("Rewriting...")
            self.gui.progress_bar.setValue(90)
            self.gui.update_project_step("refine_translation", "running")

            self.gui._rewrite_preview_status_updater = _update_preview_validity

            self.gui._rewrite_source_segments = rewrite_source_segments
            self.gui._rewrite_base_translated_segments = rewrite_base_segments
            self.gui._rewrite_selected_indices = _scope_indices()
            self.gui._rewrite_selected_segment_index = self.gui._rewrite_selected_indices[0] if len(self.gui._rewrite_selected_indices) == 1 else -1
            self.gui.rewrite_translation_thread = RewriteTranslationWorker(
                rewrite_source_segments,
                rewrite_base_segments,
                self.gui.get_source_language_code(),
                style_instruction=style_instruction,
            )
            self.gui.rewrite_translation_thread.finished.connect(self.gui.on_rewrite_translation_finished)
            self.gui.rewrite_translation_thread.start()

        def _cleanup_dialog():
            for attr in (
                "_rewrite_dialog",
                "_rewrite_preview_edit",
                "_rewrite_apply_btn",
                "_rewrite_generate_btn",
                "_rewrite_status_label",
                "_rewrite_preview_status_updater",
                "_rewrite_source_segments",
                "_rewrite_base_translated_segments",
                "_rewrite_preview_ready",
                "_rewrite_selected_segment_index",
                "_rewrite_selected_indices",
                "_rewrite_initial_scope",
                "_rewrite_initial_segment_index",
            ):
                if hasattr(self.gui, attr):
                    delattr(self.gui, attr)

        custom_style_cb.toggled.connect(_toggle_custom_instruction)
        scope_combo.currentIndexChanged.connect(lambda _index: _reset_scope_preview())

        def _handle_scope_item_changed(item):
            if item.checkState() == Qt.Checked and scope_combo.currentData() != "checked":
                scope_combo.setCurrentIndex(1)
                return
            if scope_combo.currentData() == "checked":
                _reset_scope_preview()

        check_list.itemChanged.connect(_handle_scope_item_changed)
        close_btn.clicked.connect(dialog.reject)
        generate_btn.clicked.connect(_start_preview_generation)
        apply_btn.clicked.connect(self.apply_rewrite_preview)
        dialog.finished.connect(lambda _result: _cleanup_dialog())
        self.gui._rewrite_preview_status_updater = _update_preview_validity
        _reset_scope_preview()
        _update_preview_validity()
        dialog.exec()

    def apply_edited_translation(self, show_message=True, force_apply=True):
        srt_text = self.gui.translated_text.toPlainText()
        segments = []
        if self.gui.keep_timeline_cb.isChecked():
            base_segments = self.gui.current_translated_segments or self.gui.current_segments
            edited_texts = self.gui.extract_subtitle_text_entries(srt_text)
            if base_segments and len(edited_texts) == len(base_segments):
                segments = [
                    {
                        "start": base["start"],
                        "end": base["end"],
                        "text": edited_texts[idx],
                        "words": list(base.get("words", [])),
                        "manual_highlights": list(base.get("manual_highlights", [])),
                        # SRT has no diarization fields.  Keep the existing
                        # speaker assignment when applying edited/imported
                        # translated text so timeline colors and speaker
                        # controls remain stable.
                        "speaker": str(base.get("speaker", "") or ""),
                    }
                    for idx, base in enumerate(base_segments)
                ]
        if not segments:
            segments = self.gui.parse_srt_to_segments(srt_text)
        # Imported/edited SRT files cannot carry diarization metadata.  When
        # cue order is unchanged, restore the speaker assignment from the
        # existing project regardless of the timeline-preserve preference.
        # This keeps speaker colors and per-speaker voice routing intact.
        if segments:
            metadata_base = self.gui.current_translated_segments or self.gui.current_segments
            if metadata_base and len(metadata_base) == len(segments):
                for idx, segment in enumerate(segments):
                    speaker = str(metadata_base[idx].get("speaker", "") or "").strip()
                    if speaker:
                        segment["speaker"] = speaker
        if not segments:
            if show_message:
                QMessageBox.warning(
                    self.gui,
                    "Error",
                    "Could not parse edited translated SRT.\n\nTip: Keep standard SRT format:\n1\\n00:00:01,000 --> 00:00:02,000\\ntext",
                )
            return False

        self.gui.current_translated_segments = segments
        self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(segments, translated=True)
        if force_apply:
            self.gui.apply_segments_to_timeline()

        if show_message:
            QMessageBox.information(self.gui, "Applied", f"Applied edited translation to timeline.\nSegments: {len(segments)}")
        self.gui.refresh_ui_state()
        self.gui.schedule_auto_frame_preview()
        return True
