import json
import os

try:
    from i18n import current_source_text, set_current_source_text
except ImportError:
    from ui.i18n import current_source_text, set_current_source_text


def save_user_settings(gui):
    s = gui.settings
    s.setValue("output_mode", current_source_text(gui.output_mode_combo))
    # Output/canvas choices are intentionally session-local. Remove legacy
    # cached values so reopening another project always starts from defaults.
    for key in ("output_quality", "output_fps", "output_ratio", "output_scale_mode"):
        s.remove(key)
    # Project-dependent output/filter/style values are intentionally
    # not stored in global QSettings. Remove keys written by older builds.
    for key in (
        "video_filter_preset", "video_filter_intensity", "video_filter_overrides", "video_filter_modified",
        "premium_voice_name", "premium_voice_value", "voice_tier",
        "subtitle_font", "subtitle_size", "subtitle_animation", "subtitle_animation_time",
        "subtitle_position_mode", "subtitle_align", "subtitle_custom_x", "subtitle_custom_y",
        "subtitle_x_offset", "subtitle_vertical_offset", "subtitle_color", "subtitle_background_color",
        "subtitle_background", "subtitle_background_width", "subtitle_background_shape", "subtitle_background_radius",
        "subtitle_outline", "subtitle_background_alpha", "subtitle_bold", "subtitle_speaker_colors",
        "subtitle_auto_keyword_highlight", "subtitle_highlight_color", "subtitle_highlight_mode",
        "audio_handling_mode",
    ):
        s.remove(key)

    # Subtitle style controls (serialized to JSON)
    if hasattr(gui, "_current_subtitle_style_controls_state"):
        try:
            style_payload = gui._current_subtitle_style_controls_state()
            if style_payload:
                # Ensure single_line is NOT persisted as True
                style_payload["single_line"] = False
                s.setValue("subtitle_style_controls", json.dumps(style_payload))
                if "preset" in style_payload:
                    s.setValue("subtitle_preset", str(style_payload["preset"]))
        except Exception:
            pass

    # Voice settings
    if hasattr(gui, "voice_engine_combo"):
        s.setValue("voice_engine", str(gui.voice_engine_combo.currentData() or gui.voice_engine_combo.currentText() or ""))
    if hasattr(gui, "free_voice_combo"):
        s.setValue("free_voice_value", str(gui.free_voice_combo.currentData() or ""))
        s.setValue("free_voice_name", str(gui.free_voice_combo.currentText() or ""))
    if hasattr(gui, "voice_gender_combo"):
        s.setValue("voice_gender", str(gui.voice_gender_combo.currentText() or "Female"))
    if hasattr(gui, "voice_speed_spin"):
        s.setValue("voice_speed", str(gui.voice_speed_spin.currentText() or "1.0x (Normal)"))
    if hasattr(gui, "voice_timing_sync_combo"):
        s.setValue("voice_timing_sync_mode", current_source_text(gui.voice_timing_sync_combo))

    # Default transcription engine
    current_engine = gui.get_transcription_engine() if hasattr(gui, "get_transcription_engine") else os.getenv("TRANSCRIPTION_ENGINE", "sensevoice")
    s.setValue("default_transcription_engine", current_engine)

    s.setValue("source_lang", current_source_text(gui.lang_whisper_combo))
    s.setValue("whisper_model_name", getattr(gui, "selected_whisper_model_name", "auto"))
    s.setValue("final_output_folder", gui.final_output_folder_edit.text())
    s.setValue("audio_folder", gui.audio_folder_edit.text())
    s.setValue("srt_output_folder", gui.srt_output_folder_edit.text())
    s.setValue("voice_output_folder", gui.voice_output_folder_edit.text())
    s.setValue("audio_source", gui.audio_source_edit.text())
    # Speaker diarization choices are intentionally session-local and not cached.
    s.remove("speaker_diarization")
    s.remove("speaker_diarization_num_speakers")
    s.setValue("background_audio", gui.bg_music_edit.text())
    s.setValue("mixed_audio", gui.mixed_audio_edit.text())
    for key in ("use_existing_audio", "keep_audio", "keep_timeline"):
        s.remove(key)
    if hasattr(gui, "anchor_inspector_cb"):
        s.setValue("anchor_inspector", gui.anchor_inspector_cb.isChecked())
    s.setValue("auto_preview_frame", gui.auto_preview_frame_cb.isChecked())
    if hasattr(gui, "ai_dubbing_rewrite_cb"):
        s.setValue("ai_dubbing_rewrite", gui.ai_dubbing_rewrite_cb.isChecked())
    if hasattr(gui, "toggle_advanced_btn"):
        s.setValue("advanced_section_open", gui.toggle_advanced_btn.isChecked())
    s.setValue("translation_provider", os.getenv("OPENAI_PROVIDER", "google"))
    env_batch = str(os.getenv("CAPCAP_AI_TRANSLATION_MAX_SEGMENTS", "")).strip()
    if env_batch.isdigit() and int(env_batch) > 0:
        s.setValue("translation_batch_size", int(env_batch))
    s.setValue("translation_preset_id", os.getenv("CAPCAP_TRANSLATION_PRESET_ID", "general_default"))
    s.setValue("auto_translation_context", os.getenv("CAPCAP_AUTO_TRANSLATION_CONTEXT", "1"))


def load_user_settings(gui):
    s = gui.settings
    # Generation is always Subtitle + Voice; ignore legacy single-output
    # preferences while preserving the hidden compatibility combo.
    set_current_source_text(gui.output_mode_combo, "Vietnamese subtitles + voice")
    if hasattr(gui, "output_quality_combo"):
        gui.output_quality_combo.setCurrentIndex(0)
    if hasattr(gui, "output_fps_combo"):
        gui.output_fps_combo.setCurrentIndex(0)
    if hasattr(gui, "output_ratio_combo"):
        gui.output_ratio_combo.setCurrentIndex(0)
    if hasattr(gui, "output_scale_mode_combo"):
        gui.output_scale_mode_combo.setCurrentIndex(0)
    filter_preset = "original"
    filter_intensity = 75
    filter_overrides = {}
    filter_modified = {}
    if hasattr(gui, "set_video_filter_state"):
        gui.set_video_filter_state(filter_preset, filter_intensity, filter_overrides, filter_modified)
    source_lang = s.value("source_lang", "zh")
    gui.selected_whisper_model_name = str(
        s.value("whisper_model_name", getattr(gui, "selected_whisper_model_name", "auto")) or "auto"
    ).strip().lower()
    source_index = gui.lang_whisper_combo.findData(source_lang)
    if source_index < 0:
        source_index = gui.lang_whisper_combo.findText(source_lang)
    if source_index >= 0:
        gui.lang_whisper_combo.setCurrentIndex(source_index)
    gui.final_output_folder_edit.setText(s.value("final_output_folder", gui.final_output_folder_edit.text()))
    gui.audio_folder_edit.setText(s.value("audio_folder", gui.audio_folder_edit.text()))
    gui.srt_output_folder_edit.setText(s.value("srt_output_folder", gui.srt_output_folder_edit.text()))
    gui.voice_output_folder_edit.setText(s.value("voice_output_folder", gui.voice_output_folder_edit.text()))
    gui.audio_source_edit.setText(s.value("audio_source", gui.audio_source_edit.text()))
    gui.bg_music_edit.setText(s.value("background_audio", gui.bg_music_edit.text()))
    gui.mixed_audio_edit.setText(s.value("mixed_audio", gui.mixed_audio_edit.text()))
    for env_k, s_k, def_v in (
        ("CAPCUT_STT_CHUNK_SECONDS", "capcut_stt_chunk_seconds", "300"),
        ("CAPCUT_STT_WORKERS", "capcut_stt_workers", "5"),
        ("CAPCUT_TTS_BATCH_SIZE", "capcut_tts_batch_size", "60"),
        ("CAPCUT_TTS_WORKERS", "capcut_tts_workers", "30"),
        ("CAPCAP_TRANSLATION_PRESET_ID", "translation_preset_id", "general_default"),
        ("CAPCAP_AI_TRANSLATION_MAX_SEGMENTS", "translation_batch_size", "80"),
        ("CAPCAP_AUTO_TRANSLATION_CONTEXT", "auto_translation_context", "1"),
    ):
        v = str(os.getenv(env_k) or s.value(s_k, def_v) or def_v).strip()
        os.environ[env_k] = v
        s.setValue(s_k, v)
    active_provider = (os.getenv("OPENAI_PROVIDER") or os.getenv("AI_POLISHER_PROVIDER") or s.value("translation_provider", "google")).strip().lower()
    if active_provider == "gemini":
        active_provider = "google_ai_studio"
    os.environ["OPENAI_PROVIDER"] = active_provider
    s.setValue("translation_provider", active_provider)

    saved_default_engine = str(s.value("default_transcription_engine", "") or "").strip().lower()
    if saved_default_engine in {"whisper", "sensevoice", "ocr", "capcut"}:
        os.environ["TRANSCRIPTION_ENGINE"] = saved_default_engine

    # Voice selection, audio mode, subtitle style, and filters use the widget
    # defaults for a new session/project; they are not inherited globally.
    if hasattr(gui, "use_premium_voice_radio"):
        gui.use_premium_voice_radio.setChecked(False)
    if hasattr(gui, "use_free_voice_radio"):
        gui.use_free_voice_radio.setChecked(True)

    # Restore Voice settings
    saved_engine = str(s.value("voice_engine", "") or "").strip()
    if saved_engine and hasattr(gui, "voice_engine_combo"):
        idx = gui.voice_engine_combo.findData(saved_engine)
        if idx < 0:
            idx = gui.voice_engine_combo.findText(saved_engine)
        if idx >= 0:
            gui.voice_engine_combo.setCurrentIndex(idx)

    saved_gender = str(s.value("voice_gender", "") or "").strip()
    if saved_gender and hasattr(gui, "voice_gender_combo"):
        idx = gui.voice_gender_combo.findText(saved_gender)
        if idx >= 0:
            gui.voice_gender_combo.setCurrentIndex(idx)

    saved_speed = str(s.value("voice_speed", "") or "").strip()
    if saved_speed and hasattr(gui, "voice_speed_spin"):
        idx = gui.voice_speed_spin.findText(saved_speed)
        if idx >= 0:
            gui.voice_speed_spin.setCurrentIndex(idx)
        else:
            gui.voice_speed_spin.setEditText(saved_speed)

    saved_free_voice = str(s.value("free_voice_value", "") or "").strip()
    if saved_free_voice and hasattr(gui, "free_voice_combo"):
        if hasattr(gui, "set_voice_combo_value"):
            gui.set_voice_combo_value(gui.free_voice_combo, saved_free_voice)
        else:
            idx = gui.free_voice_combo.findData(saved_free_voice)
            if idx >= 0:
                gui.free_voice_combo.setCurrentIndex(idx)

    saved_sync = str(s.value("voice_timing_sync_mode", "") or "").strip()
    if saved_sync and hasattr(gui, "voice_timing_sync_combo"):
        if not set_current_source_text(gui.voice_timing_sync_combo, saved_sync):
            idx = gui.voice_timing_sync_combo.findText(saved_sync)
            if idx >= 0:
                gui.voice_timing_sync_combo.setCurrentIndex(idx)

    gui.keep_timeline_cb.setChecked(True)
    if hasattr(gui, "anchor_inspector_cb"):
        gui.anchor_inspector_cb.setChecked(
            str(s.value("anchor_inspector", gui.anchor_inspector_cb.isChecked())).lower() == "true"
        )
    auto_preview_enabled = str(s.value("auto_preview_frame", "false")).lower() == "true"
    if gui.auto_preview_frame_cb.isHidden():
        auto_preview_enabled = False
        s.setValue("auto_preview_frame", False)
    gui.auto_preview_frame_cb.setChecked(auto_preview_enabled)

    # Subtitle style controls retain their UI defaults for a new session.
    if hasattr(gui, "speaker_diarization_cb"):
        gui.speaker_diarization_cb.setChecked(False)
        if hasattr(gui, "update_speaker_diarization_availability"):
            gui.update_speaker_diarization_availability()
    if hasattr(gui, "speaker_diarization_speakers_combo"):
        index = gui.speaker_diarization_speakers_combo.findData(-1)
        if index >= 0:
            gui.speaker_diarization_speakers_combo.setCurrentIndex(index)
    if hasattr(gui, "timeline"):
        gui.timeline.set_voice_sync_mode(current_source_text(gui.voice_timing_sync_combo))
    
    if hasattr(gui, "ai_dubbing_rewrite_cb"):
        gui.ai_dubbing_rewrite_cb.setChecked(str(s.value("ai_dubbing_rewrite", "true")).lower() == "true")
    gui.use_generated_audio_radio.setChecked(True)
    gui.use_existing_audio_radio.setChecked(False)
    advanced_open = str(s.value("advanced_section_open", "false")).lower() == "true"
    if hasattr(gui, "toggle_advanced_btn"):
        gui.toggle_advanced_btn.setChecked(advanced_open)
    else:
        gui.on_advanced_toggled(advanced_open)
    gui.on_audio_source_mode_changed()
    raw_style = s.value("subtitle_style_controls", None)
    applied_style = False
    if raw_style:
        try:
            style_dict = json.loads(raw_style) if isinstance(raw_style, str) else raw_style
            if isinstance(style_dict, dict) and style_dict and hasattr(gui, "_apply_subtitle_style_controls_state"):
                # Strictly force single_line to False
                style_dict["single_line"] = False
                preset = str(style_dict.get("preset", "youtube")).lower()
                radio_map = {
                    "tiktok": getattr(gui, "subtitle_preset_tiktok_radio", None),
                    "youtube": getattr(gui, "subtitle_preset_youtube_radio", None),
                    "minimal": getattr(gui, "subtitle_preset_minimal_radio", None),
                    "short": getattr(gui, "subtitle_preset_minimal_radio", None),
                    "custom": getattr(gui, "subtitle_preset_custom_radio", None),
                }
                radio = radio_map.get(preset)
                if radio is not None:
                    radio.setChecked(True)
                gui._apply_subtitle_style_controls_state(style_dict)
                if hasattr(gui, "subtitle_single_line_cb"):
                    gui.subtitle_single_line_cb.setChecked(False)
                gui._subtitle_custom_style_state = dict(style_dict)
                applied_style = True
        except Exception:
            pass
    if not applied_style:
        gui.on_subtitle_preset_changed()
        if hasattr(gui, "_capture_subtitle_custom_style_state"):
            gui._capture_subtitle_custom_style_state()

    if hasattr(gui, "subtitle_single_line_cb"):
        gui.subtitle_single_line_cb.setChecked(False)
    gui.update_subtitle_preview_style()
    gui.on_output_mode_changed(current_source_text(gui.output_mode_combo))

    # Clear stale audio/project cache if no video is selected
    video_path = gui.video_path_edit.text().strip() if hasattr(gui, "video_path_edit") else ""
    if not video_path or not os.path.exists(video_path):
        if hasattr(gui, "audio_source_edit"):
            gui.audio_source_edit.clear()
        if hasattr(gui, "current_project_state"):
            gui.current_project_state = None
        if hasattr(gui, "processed_artifacts"):
            gui.processed_artifacts.clear()

    gui.refresh_ui_state()
