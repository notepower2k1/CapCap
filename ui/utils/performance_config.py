"""Project-owned processing settings shared by the editor and queue worker."""
import json
import os
from PySide6.QtWidgets import QComboBox, QCheckBox, QRadioButton, QSpinBox, QDoubleSpinBox, QSlider, QLineEdit
from i18n import current_source_text, set_current_source_text

from services.processing_queue import ENV_KEYS


def capture(gui):
    controls = {}
    for name, widget in vars(gui).items():
        if name == "video_path_edit" or any(word in name.lower() for word in ("password", "api_key", "token", "secret")):
            continue
        if isinstance(widget, QComboBox):
            value = {"data": widget.currentData(), "text": current_source_text(widget)}
        elif isinstance(widget, (QCheckBox, QRadioButton)):
            value = widget.isChecked()
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox, QSlider)):
            value = widget.value()
        elif isinstance(widget, QLineEdit):
            value = widget.text()
        else:
            continue
        try:
            json.dumps(value)
        except TypeError:
            continue
        controls[name] = value
    from utils.settings_utils import get_asr_filter_settings
    original = dict(volume=gui._get_audio_track_volume("A1 Audio"), muted=gui._is_audio_track_muted("A1 Audio"))
    fx, fy = gui.get_output_fill_focus()
    overlay=getattr(gui,"ocr_region_overlay",None)
    if overlay is not None:
        overlay._save_rect()
    return dict(controls=controls, environment={k: os.environ[k] for k in ENV_KEYS if k in os.environ},
        prepare=dict(mode=gui.get_output_mode_key(), audio_handling_mode=gui.get_audio_handling_mode(),
            source_language=gui.get_source_language_code(), target_language=gui.get_target_language_code(),
            translator_ai=gui.is_ai_polish_enabled(), translator_style=gui.get_ai_style_instruction(),
            whisper_model_name=gui.get_whisper_model_name(), transcription_engine=gui.get_transcription_engine(),
            speaker_diarization=gui.is_speaker_diarization_enabled(), speaker_diarization_num_speakers=gui.get_speaker_diarization_num_speakers(),
            skip_translation=gui.is_skip_translation(), asr_options=get_asr_filter_settings(gui.settings)),
        voice=dict(voice_name=gui.get_active_voice_name(), voice_speed=gui._parse_voice_speed_value(),
            audio_handling_mode=gui.get_audio_handling_mode(), timing_sync_mode=current_source_text(gui.voice_timing_sync_combo),
            original_volume=gui.audio_a1_volume_slider.value(), dub_volume=gui.audio_a2_volume_slider.value(),
            target_language=gui.get_target_language_code(), source_language=gui.get_source_language_code(),
            elevenlabs_model_id=os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5"),
            ai_rewrite_dubbing=gui.is_ai_dubbing_rewrite_enabled(), dubbing_style_instruction=gui.get_ai_dubbing_style_instruction()),
        audio=dict(original=original, tts=gui._tts_audio_track_state(), music=gui._music_audio_tracks()),
        export=dict(subtitle_style=gui.get_subtitle_export_style(), output_quality=gui.get_output_quality_key(),
            output_fps=gui.get_output_fps_key(), output_ratio=gui.get_output_ratio_key(), output_scale_mode=gui.get_output_scale_mode_key(),
            output_fill_focus_x=fx, output_fill_focus_y=fy, video_filter_state=gui.get_video_filter_state(),
            video_quality=str(gui.settings.value("export_video_quality", "medium") or "medium")),
        use_existing_audio=gui.using_existing_audio_source(), existing_audio_path=gui.resolve_selected_audio_path() if gui.using_existing_audio_source() else "",
        output_dir=gui.final_output_folder_edit.text().strip())


def restore(gui, config, *, refresh=True):
    controls = config.get("controls", {})
    blocked = []
    try:
        for name in controls:
            widget = getattr(gui,name,None)
            if isinstance(widget, (QComboBox, QCheckBox, QRadioButton, QSpinBox, QDoubleSpinBox, QSlider, QLineEdit)):
                blocked.append((widget,widget.blockSignals(True)))
        for name, value in controls.items():
            widget = getattr(gui, name, None)
            if widget is None:
                continue
            if isinstance(widget, QComboBox) and isinstance(value, dict):
                index = widget.findData(value.get("data")) if value.get("data") is not None else -1
                if index >= 0:
                    widget.setCurrentIndex(index)
                elif widget.isEditable():
                    widget.setEditText(str(value.get("text", "")))
                else:
                    set_current_source_text(widget, value.get("text", ""))
            elif isinstance(widget, (QCheckBox, QRadioButton)):
                widget.setChecked(bool(value))
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox, QSlider)):
                widget.setValue(value)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
    finally:
        for widget, state in reversed(blocked):
            widget.blockSignals(state)
    os.environ.update({k:str(v) for k,v in config.get("environment",{}).items() if k in ENV_KEYS})
    overlay=getattr(gui,"ocr_region_overlay",None)
    if overlay is not None:
        overlay._load_rect()
    if not refresh:
        return
    gui.refresh_voice_catalog_combos()
    voice = config.get("voice", {}).get("voice_name", "")
    if voice:
        gui.set_voice_combo_value(gui.free_voice_combo, voice)
    if hasattr(gui,"omnivoice_quality_combo"):
        from engines.omnivoice_tts import inference_steps
        gui.omnivoice_quality_combo.setCurrentIndex(gui.omnivoice_quality_combo.findData(inference_steps()))
    gui.refresh_ui_state()
