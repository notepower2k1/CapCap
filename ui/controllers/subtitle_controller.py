import os

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout

from workers import RewriteTranslationWorker, TranscriptionWorker, TranslationWorker
from translation import TranslationOrchestrator


class SubtitleController:
    REWRITE_STYLE_PRESETS = [
        ("Natural short video", "Make the Vietnamese sound natural, concise, conversational, and easy to read quickly in short videos."),
        ("TikTok natural", "Make the Vietnamese feel natural, modern, and casual like a strong TikTok creator voice, while keeping the meaning accurate and concise."),
        ("Punchy viral", "Make the Vietnamese feel punchy and attention-grabbing for short videos, but keep the meaning accurate and avoid exaggeration."),
        ("Sales voiceover", "Make the Vietnamese sound persuasive, benefit-driven, and smooth for a sales voiceover, but keep claims grounded in the original meaning."),
        ("Short storytelling", "Make the Vietnamese flow like short-form storytelling: natural, engaging, emotionally clear, and easy to follow line by line."),
        ("Neutral dubbing", "Make the Vietnamese smooth, neutral, clear, and easy for voice dubbing."),
        ("Clean subtitle", "Make the Vietnamese compact, clean, and very easy to scan as on-screen subtitles."),
        ("Custom", "custom"),
    ]

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
            self.gui._rewrite_preview_edit.setPlainText(translated_srt)
        if hasattr(self.gui, "_rewrite_preview_status_updater"):
            self.gui._rewrite_preview_status_updater()
        if hasattr(self.gui, "_rewrite_status_label"):
            self.gui._rewrite_status_label.setText("AI preview is ready. You can keep editing it, then press Apply when the SRT format is valid.")
            self.gui._rewrite_status_label.setStyleSheet("color: #8ad7ff; font-size: 12px; font-weight: 700;")
        self.gui.update_project_step("refine_translation", "done")
        self.gui.refresh_ui_state()

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
        self.gui.current_translated_segments = applied_segments
        self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(applied_segments, translated=True)
        self.gui.refresh_ai_keyword_highlights(force=True)
        normalized_srt = self.gui.format_to_srt(self.gui.current_translated_segments)
        self.gui.translated_text.setText(normalized_srt)
        self.gui.apply_segments_to_timeline()

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
        QMessageBox.information(self.gui, "Rewrite Applied", "The rewritten SRT was applied to the subtitle editor.")
        self.gui.refresh_ui_state()

    def _open_rewrite_dialog(self, source_segments, translated_segments):
        dialog = QDialog(self.gui)
        dialog.setWindowTitle("Rewrite Subtitle")
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

        title = QLabel("Rewrite subtitle")
        title.setObjectName("statusHeadline")
        layout.addWidget(title)

        hint = QLabel("You can edit the Vietnamese subtitle directly here in SRT format. Use AI only if you want a rewrite suggestion, then review and apply it.")
        hint.setObjectName("helperLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        style_combo = QComboBox(dialog)
        for label, instruction in self.REWRITE_STYLE_PRESETS:
            style_combo.addItem(label, instruction)
        layout.addWidget(style_combo)

        custom_style_cb = QCheckBox("Add extra custom instruction", dialog)
        layout.addWidget(custom_style_cb)

        custom_prompt = QTextEdit(dialog)
        custom_prompt.setPlaceholderText("Example: Keep the words very short and modern, suitable for TikTok voiceover.")
        custom_prompt.setFixedHeight(88)
        custom_prompt.setVisible(False)
        layout.addWidget(custom_prompt)

        status_label = QLabel("Edit the SRT below or generate an AI suggestion.")
        status_label.setObjectName("helperLabel")
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        preview_label = QLabel("Rewrite SRT")
        preview_label.setObjectName("sectionTitle")
        layout.addWidget(preview_label)

        preview_edit = QTextEdit(dialog)
        preview_edit.setPlaceholderText("Enter valid SRT content here.")
        preview_edit.setPlainText(self.gui.format_to_srt(translated_segments))
        layout.addWidget(preview_edit, 1)

        self.gui._rewrite_dialog = dialog
        self.gui._rewrite_preview_edit = preview_edit

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

        def _update_preview_validity():
            current_text = preview_edit.toPlainText().strip()
            if not current_text:
                status_label.setText("Rewrite SRT is empty.")
                status_label.setStyleSheet("color: #f6c177; font-size: 12px; font-weight: 600;")
                apply_btn.setEnabled(False)
                return
            is_valid_srt, parsed_segments, validation_mode, validation_error = self._validate_rewrite_srt(current_text)
            if is_valid_srt and validation_mode == "srt":
                status_label.setText(f"Valid SRT. Segments: {len(parsed_segments)}.")
                status_label.setStyleSheet("color: #78f0b0; font-size: 12px; font-weight: 700;")
                apply_btn.setEnabled(True)
            else:
                status_label.setText(f"Invalid SRT. {validation_error or 'Keep standard blocks: index, time range, then subtitle text.'}")
                status_label.setStyleSheet("color: #ff8f8f; font-size: 12px; font-weight: 700;")
                apply_btn.setEnabled(False)

        def _start_preview_generation():
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

            rewrite_source_segments = getattr(self.gui, "_rewrite_source_segments", source_segments)
            rewrite_base_segments = getattr(self.gui, "_rewrite_base_translated_segments", translated_segments)
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
            ):
                if hasattr(self.gui, attr):
                    delattr(self.gui, attr)

        custom_style_cb.toggled.connect(_toggle_custom_instruction)
        close_btn.clicked.connect(dialog.reject)
        generate_btn.clicked.connect(_start_preview_generation)
        apply_btn.clicked.connect(self.apply_rewrite_preview)
        preview_edit.textChanged.connect(_update_preview_validity)
        dialog.finished.connect(lambda _result: _cleanup_dialog())
        self.gui._rewrite_preview_status_updater = _update_preview_validity
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
                    }
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
