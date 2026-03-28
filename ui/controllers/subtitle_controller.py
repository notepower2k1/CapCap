import os

from PySide6.QtWidgets import QMessageBox

from workers import RewriteTranslationWorker, TranscriptionWorker, TranslationWorker


class SubtitleController:
    def __init__(self, gui):
        self.gui = gui

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

    def on_transcription_finished(self, segments):
        self.gui.transcribe_btn.setEnabled(True)
        if not segments:
            self.gui.update_project_step("transcribe", "failed")
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
            out_folder = self.gui.srt_output_folder_edit.text()
            if not os.path.exists(out_folder):
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
        self.gui._pipeline_advance("transcription")

    def run_translation(self):
        srt_source = self.gui.transcript_text.toPlainText()
        if not srt_source or not srt_source.strip():
            QMessageBox.warning(self.gui, "Error", "No transcription available to translate!")
            return

        model_path = None
        src_lang = self.gui.get_source_language_code()
        enable_polish = False
        self.gui.translated_text.setText("Translating with Microsoft Translator... please wait.")
        self.gui.translate_btn.setEnabled(False)
        self.gui.progress_bar.setValue(80)
        self.gui.update_project_step("translate_raw", "running")

        self.gui.translation_thread = TranslationWorker(srt_source, model_path, src_lang, enable_polish)
        self.gui.translation_thread.finished.connect(self.gui.on_translation_finished)
        self.gui.translation_thread.start()

    def on_translation_finished(self, translated_srt, error):
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
            out_folder = self.gui.srt_output_folder_edit.text().strip() or os.path.join(os.getcwd(), "output")
            os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, file_basename + "_vi.srt")
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write(translated_srt)
            self.gui.last_translated_srt_path = out_path
            self.gui.processed_artifacts["srt_translated"] = out_path
            self.gui.persist_translation_project_data(self.gui.current_translated_segments, out_path)
            QMessageBox.information(self.gui, "Finished", f"Process complete! Subtitle saved and loaded for preview:\n{out_path}")
        else:
            self.gui.persist_translation_project_data(self.gui.current_translated_segments)
            QMessageBox.information(self.gui, "Finished", "Translation complete!")

        self.gui.refresh_ui_state()
        self.gui._pipeline_advance("translation")

    def run_rewrite_translation(self):
        source_segments = list(self.gui.current_segments or [])
        translated_segments = list(self.gui.current_translated_segments or [])
        if not source_segments:
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Original subtitles are missing. Please create or load the original subtitle track first.")
            return
        if not translated_segments:
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Vietnamese subtitles are missing. Please translate or load them first.")
            return
        if len(source_segments) != len(translated_segments):
            QMessageBox.warning(self.gui, "Rewrite Unavailable", "Original and Vietnamese subtitle counts do not match, so rewrite cannot run safely.")
            return

        self.gui.rewrite_translation_btn.setEnabled(False)
        self.gui.rewrite_translation_btn.setText("Rewriting...")
        self.gui.progress_bar.setValue(90)
        self.gui.update_project_step("refine_translation", "running")

        self.gui.rewrite_translation_thread = RewriteTranslationWorker(
            source_segments,
            translated_segments,
            self.gui.get_source_language_code(),
        )
        self.gui.rewrite_translation_thread.finished.connect(self.gui.on_rewrite_translation_finished)
        self.gui.rewrite_translation_thread.start()

    def on_rewrite_translation_finished(self, translated_srt, error):
        self.gui.rewrite_translation_btn.setEnabled(True)
        self.gui.rewrite_translation_btn.setText("Rewrite with AI")
        if error or not translated_srt:
            self.gui.update_project_step("refine_translation", "failed")
            self.gui.show_error(
                "Rewrite Failed",
                "Could not rewrite the Vietnamese subtitles with AI.",
                error or "The AI rewrite service returned an empty result.",
            )
            self.gui.refresh_ui_state()
            return

        self.gui.translated_text.setText(translated_srt)
        self.gui.apply_edited_translation(show_message=False, force_apply=True)
        self.gui.update_project_step("refine_translation", "done")

        out_path = self.gui.last_translated_srt_path
        if not out_path:
            video_path = self.gui.video_path_edit.text().strip()
            if video_path:
                file_basename = os.path.splitext(os.path.basename(video_path))[0]
                out_folder = self.gui.srt_output_folder_edit.text().strip() or os.path.join(os.getcwd(), "output")
                os.makedirs(out_folder, exist_ok=True)
                out_path = os.path.join(out_folder, file_basename + "_vi.srt")
        if out_path:
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write(translated_srt)
            self.gui.last_translated_srt_path = out_path
            self.gui.processed_artifacts["srt_translated"] = out_path
            self.gui.persist_translation_project_data(self.gui.current_translated_segments, out_path)
        else:
            self.gui.persist_translation_project_data(self.gui.current_translated_segments)

        QMessageBox.information(self.gui, "Rewrite Complete", "Vietnamese subtitles were rewritten and updated in the subtitle editor.")
        self.gui.refresh_ui_state()

    def apply_edited_translation(self, show_message=True, force_apply=True):
        srt_text = self.gui.translated_text.toPlainText()
        segments = []
        if self.gui.keep_timeline_cb.isChecked():
            base_segments = self.gui.current_translated_segments or self.gui.current_segments
            edited_texts = self.gui.extract_subtitle_text_entries(srt_text)
            if base_segments and len(edited_texts) == len(base_segments):
                segments = [
                    {"start": base["start"], "end": base["end"], "text": edited_texts[idx]}
                    for idx, base in enumerate(base_segments)
                ]
        if not segments:
            segments = self.gui.parse_srt_to_segments(srt_text)
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
