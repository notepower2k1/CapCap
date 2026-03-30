def save_user_settings(gui):
    s = gui.settings
    s.setValue("output_mode", gui.output_mode_combo.currentText())
    s.setValue("source_lang", gui.lang_whisper_combo.currentText())
    s.setValue("whisper_model_name", gui.get_whisper_model_name())
    s.setValue("final_output_folder", gui.final_output_folder_edit.text())
    s.setValue("audio_folder", gui.audio_folder_edit.text())
    s.setValue("srt_output_folder", gui.srt_output_folder_edit.text())
    s.setValue("voice_output_folder", gui.voice_output_folder_edit.text())
    s.setValue("audio_source", gui.audio_source_edit.text())
    s.setValue("background_audio", gui.bg_music_edit.text())
    s.setValue("mixed_audio", gui.mixed_audio_edit.text())
    s.setValue("free_voice_name", gui.free_voice_combo.currentText())
    s.setValue("free_voice_value", gui.free_voice_combo.currentData())
    s.setValue("premium_voice_name", gui.premium_voice_combo.currentText())
    s.setValue("premium_voice_value", gui.premium_voice_combo.currentData())
    if gui.use_premium_voice_radio.isChecked():
        voice_tier = "premium"
    else:
        voice_tier = "free"
    s.setValue("voice_tier", voice_tier)
    s.setValue("use_existing_audio", gui.use_existing_audio_radio.isChecked())
    s.setValue("keep_audio", gui.keep_audio_cb.isChecked())
    s.setValue("keep_timeline", gui.keep_timeline_cb.isChecked())
    s.setValue("auto_preview_frame", gui.auto_preview_frame_cb.isChecked())
    s.setValue("subtitle_font", gui.subtitle_font_combo.currentText())
    s.setValue("subtitle_size", gui.subtitle_font_size_spin.value())
    s.setValue("subtitle_animation", gui.subtitle_animation_combo.currentText())
    s.setValue("subtitle_animation_time", gui.subtitle_animation_time_spin.value())
    s.setValue("subtitle_preset", gui.get_selected_subtitle_preset())
    s.setValue("subtitle_align", gui.subtitle_align_combo.currentText())
    s.setValue("subtitle_x_offset", gui.subtitle_x_offset_spin.value())
    s.setValue("subtitle_vertical_offset", gui.subtitle_bottom_offset_spin.value())
    s.setValue("subtitle_color", gui.subtitle_color_hex)
    s.setValue("subtitle_background", gui.subtitle_background_cb.isChecked())
    s.setValue("subtitle_bold", gui.subtitle_bold_cb.isChecked())
    s.setValue("subtitle_auto_keyword_highlight", gui.subtitle_keyword_highlight_cb.isChecked())
    s.setValue("subtitle_highlight_color", gui.subtitle_highlight_color_combo.currentText())
    s.setValue("subtitle_highlight_mode", gui.subtitle_highlight_mode_combo.currentText())
    s.setValue("voice_speed", gui.voice_speed_spin.currentText())
    s.setValue("audio_handling_mode", gui.get_audio_handling_mode())
    s.setValue("voice_gender", gui.voice_gender_combo.currentText())
    s.setValue("voice_timing_sync_mode", gui.voice_timing_sync_combo.currentText())
    s.setValue("voice_gain", gui.voice_gain_spin.value())
    s.setValue("bg_gain", gui.bg_gain_spin.value())
    if hasattr(gui, "toggle_advanced_btn"):
        s.setValue("advanced_section_open", gui.toggle_advanced_btn.isChecked())


def load_user_settings(gui):
    s = gui.settings
    gui.output_mode_combo.setCurrentText(s.value("output_mode", gui.output_mode_combo.currentText()))
    source_lang = s.value("source_lang", gui.lang_whisper_combo.currentText())
    gui.selected_whisper_model_name = str(s.value("whisper_model_name", getattr(gui, "selected_whisper_model_name", "base")) or "base").strip().lower()
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
    gui.set_voice_combo_value(gui.free_voice_combo, s.value("free_voice_value", gui.free_voice_combo.currentData()))
    gui.set_voice_combo_value(gui.premium_voice_combo, s.value("premium_voice_value", gui.premium_voice_combo.currentData()))
    voice_tier = str(s.value("voice_tier", "free")).lower()
    gui.use_premium_voice_radio.setChecked(voice_tier == "premium")
    gui.use_free_voice_radio.setChecked(voice_tier != "premium")
    gui.keep_audio_cb.setChecked(str(s.value("keep_audio", gui.keep_audio_cb.isChecked())).lower() == "true")
    gui.keep_timeline_cb.setChecked(str(s.value("keep_timeline", gui.keep_timeline_cb.isChecked())).lower() == "true")
    auto_preview_enabled = str(s.value("auto_preview_frame", "false")).lower() == "true"
    if gui.auto_preview_frame_cb.isHidden():
        auto_preview_enabled = False
        s.setValue("auto_preview_frame", False)
    gui.auto_preview_frame_cb.setChecked(auto_preview_enabled)
    gui.subtitle_font_combo.setCurrentText(s.value("subtitle_font", gui.subtitle_font_combo.currentText()))
    gui.subtitle_font_size_spin.setValue(int(s.value("subtitle_size", gui.subtitle_font_size_spin.value())))
    gui.subtitle_animation_combo.setCurrentText(s.value("subtitle_animation", gui.subtitle_animation_combo.currentText()))
    gui.subtitle_animation_time_spin.setValue(float(s.value("subtitle_animation_time", gui.subtitle_animation_time_spin.value())))
    preset_key = str(s.value("subtitle_preset", gui.get_selected_subtitle_preset())).lower()
    if preset_key == "youtube":
        gui.subtitle_preset_youtube_radio.setChecked(True)
    elif preset_key == "minimal":
        gui.subtitle_preset_minimal_radio.setChecked(True)
    elif preset_key == "custom":
        gui.subtitle_preset_custom_radio.setChecked(True)
    else:
        gui.subtitle_preset_tiktok_radio.setChecked(True)
    gui.subtitle_align_combo.setCurrentText(s.value("subtitle_align", gui.subtitle_align_combo.currentText()))
    gui.subtitle_x_offset_spin.setValue(int(s.value("subtitle_x_offset", gui.subtitle_x_offset_spin.value())))
    gui.subtitle_bottom_offset_spin.setValue(int(s.value("subtitle_vertical_offset", gui.subtitle_bottom_offset_spin.value())))
    gui.subtitle_color_hex = str(s.value("subtitle_color", gui.subtitle_color_hex)).upper()
    gui.subtitle_color_btn.setText(gui.subtitle_color_hex)
    gui.subtitle_background_cb.setChecked(str(s.value("subtitle_background", gui.subtitle_background_cb.isChecked())).lower() == "true")
    gui.subtitle_bold_cb.setChecked(str(s.value("subtitle_bold", gui.subtitle_bold_cb.isChecked())).lower() == "true")
    gui.subtitle_keyword_highlight_cb.setChecked(str(s.value("subtitle_auto_keyword_highlight", gui.subtitle_keyword_highlight_cb.isChecked())).lower() == "true")
    gui.subtitle_highlight_color_combo.setCurrentText(str(s.value("subtitle_highlight_color", gui.subtitle_highlight_color_combo.currentText())))
    gui.subtitle_highlight_mode_combo.setCurrentText(str(s.value("subtitle_highlight_mode", gui.subtitle_highlight_mode_combo.currentText())))
    gui.voice_speed_spin.setCurrentText(s.value("voice_speed", gui.voice_speed_spin.currentText()))
    audio_handling_mode = str(s.value("audio_handling_mode", gui.get_audio_handling_mode())).strip().lower()
    audio_handling_index = gui.audio_handling_combo.findData(audio_handling_mode)
    if audio_handling_index >= 0:
        gui.audio_handling_combo.setCurrentIndex(audio_handling_index)
    gui.voice_gender_combo.setCurrentText(s.value("voice_gender", gui.voice_gender_combo.currentText()))
    gui.voice_timing_sync_combo.setCurrentText(s.value("voice_timing_sync_mode", gui.voice_timing_sync_combo.currentText()))
    gui.voice_gain_spin.setValue(float(s.value("voice_gain", gui.voice_gain_spin.value())))
    gui.bg_gain_spin.setValue(float(s.value("bg_gain", gui.bg_gain_spin.value())))
    use_existing = str(s.value("use_existing_audio", "false")).lower() == "true"
    gui.use_existing_audio_radio.setChecked(use_existing)
    gui.use_generated_audio_radio.setChecked(not use_existing)
    advanced_open = str(s.value("advanced_section_open", "false")).lower() == "true"
    if hasattr(gui, "toggle_advanced_btn"):
        gui.toggle_advanced_btn.setChecked(advanced_open)
    else:
        gui.on_advanced_toggled(advanced_open)
    gui.on_audio_source_mode_changed()
    gui.on_subtitle_preset_changed()
    gui.update_subtitle_preview_style()
    gui.on_output_mode_changed(gui.output_mode_combo.currentText())
    gui.refresh_ui_state()
