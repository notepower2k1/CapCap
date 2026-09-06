import json
import os


def save_user_settings(gui):
    s = gui.settings
    s.setValue("output_mode", gui.output_mode_combo.currentText())
    # Output/canvas choices are intentionally session-local. Remove legacy
    # cached values so reopening another project always starts from defaults.
    for key in ("output_quality", "output_fps", "output_ratio", "output_scale_mode"):
        s.remove(key)
    # Project-dependent output/filter/style/voice values are intentionally
    # not stored in global QSettings. Remove keys written by older builds.
    for key in (
        "video_filter_preset", "video_filter_intensity", "video_filter_overrides", "video_filter_modified",
        "free_voice_name", "free_voice_value", "voice_engine", "premium_voice_name", "premium_voice_value",
        "voice_tier", "subtitle_font", "subtitle_size", "subtitle_animation", "subtitle_animation_time",
        "subtitle_preset", "subtitle_position_mode", "subtitle_align", "subtitle_custom_x", "subtitle_custom_y",
        "subtitle_x_offset", "subtitle_vertical_offset", "subtitle_color", "subtitle_background_color",
        "subtitle_background", "subtitle_background_width", "subtitle_background_shape", "subtitle_background_radius",
        "subtitle_outline", "subtitle_background_alpha", "subtitle_bold", "subtitle_speaker_colors",
        "subtitle_auto_keyword_highlight", "subtitle_highlight_color", "subtitle_highlight_mode",
        "voice_speed", "audio_handling_mode", "voice_gender", "voice_timing_sync_mode",
    ):
        s.remove(key)
    s.setValue("source_lang", gui.lang_whisper_combo.currentText())
    s.setValue("whisper_model_name", getattr(gui, "selected_whisper_model_name", "auto"))
    s.setValue("final_output_folder", gui.final_output_folder_edit.text())
    s.setValue("audio_folder", gui.audio_folder_edit.text())
    s.setValue("srt_output_folder", gui.srt_output_folder_edit.text())
    s.setValue("voice_output_folder", gui.voice_output_folder_edit.text())
    s.setValue("audio_source", gui.audio_source_edit.text())
    # Speaker diarization choices are also intentionally not cached.
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


def load_user_settings(gui):
    s = gui.settings
    # Generation is always Subtitle + Voice; ignore legacy single-output
    # preferences while preserving the hidden compatibility combo.
    gui.output_mode_combo.setCurrentText("Vietnamese subtitles + voice")
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
    source_lang = s.value("source_lang", gui.lang_whisper_combo.currentText())
    gui.selected_whisper_model_name = str(
        s.value("whisper_model_name", getattr(gui, "selected_whisper_model_name", "auto")) or "auto"
    ).strip().lower()
    # Older versions only offered Medium and persisted it as the implicit
    # default. Migrate that legacy default to Auto once Small is available.
    small_model_dir = os.path.join(gui.workspace_root, "models", "faster_whisper", "small")
    if gui.selected_whisper_model_name == "medium" and os.path.isdir(small_model_dir):
        gui.selected_whisper_model_name = "auto"
    source_index = gui.lang_whisper_combo.findText(source_lang)
    if source_index < 0:
        source_index = gui.lang_whisper_combo.findData(source_lang)
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
    ):
        v = str(s.value(s_k, os.getenv(env_k, def_v)) or def_v).strip()
        os.environ[env_k] = v
    # Voice selection, audio mode, subtitle style, and filters use the widget
    # defaults for a new session/project; they are not inherited globally.
    if hasattr(gui, "use_premium_voice_radio"):
        gui.use_premium_voice_radio.setChecked(False)
    if hasattr(gui, "use_free_voice_radio"):
        gui.use_free_voice_radio.setChecked(True)
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
        gui.timeline.set_voice_sync_mode(gui.voice_timing_sync_combo.currentText())
    
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
    gui.on_subtitle_preset_changed()
    if hasattr(gui, "_capture_subtitle_custom_style_state"):
        gui._capture_subtitle_custom_style_state()
    gui.update_subtitle_preview_style()
    gui.on_output_mode_changed(gui.output_mode_combo.currentText())

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
