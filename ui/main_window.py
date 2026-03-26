import sys
import os
import re
import time
import json
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QFileDialog, QCheckBox, QTextEdit, QComboBox,
                             QGroupBox, QSlider, QFrame, QProgressBar, QMessageBox,
                             QScrollArea,
                             QSpinBox, QColorDialog, QDoubleSpinBox, QTabWidget, QDialog, QSizePolicy, QInputDialog,
                             QRadioButton)
from PySide6.QtCore import Qt, QUrl, QTimer, QSettings
from PySide6.QtGui import QColor, QPixmap, QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

APP_PATH = os.path.join(os.path.dirname(__file__), '..', 'app')
if APP_PATH not in sys.path:
    sys.path.append(APP_PATH)

from services import GUIProjectBridge, ProjectService
from controllers import PipelineController, PreviewController, SubtitleController
from helpers import (
    build_guidance_state,
    build_preview_context_text,
    build_workflow_hint,
    extract_subtitle_text_entries,
    format_segments_to_srt,
    format_timestamp,
    get_export_button_label,
    get_output_mode_key,
    parse_srt_to_segments,
)
from video_processor import srt_to_ass
from utils.display_utils import (
    cleanup_temp_preview_files as cleanup_temp_preview_files_impl,
    clear_log as clear_log_impl,
    log_message as log_message_impl,
    show_error as show_error_impl,
    show_frame_preview_dialog as show_frame_preview_dialog_impl,
    show_processed_files as show_processed_files_impl,
)
from utils.file_dialog_utils import (
    browse_audio_folder as browse_audio_folder_impl,
    browse_audio_source as browse_audio_source_impl,
    browse_background_audio as browse_background_audio_impl,
    browse_existing_mixed_audio as browse_existing_mixed_audio_impl,
    browse_srt_output_folder as browse_srt_output_folder_impl,
    browse_voice_output_folder as browse_voice_output_folder_impl,
    cleanup_file_if_exists as cleanup_file_if_exists_impl,
    open_folder as open_folder_impl,
)
from utils.media_utils import (
    browse_video as browse_video_impl,
    duration_changed as duration_changed_impl,
    position_changed as position_changed_impl,
    refresh_video_dimensions as refresh_video_dimensions_impl,
    set_position as set_position_impl,
    setup_media_player as setup_media_player_impl,
    stop_video as stop_video_impl,
    toggle_play as toggle_play_impl,
    update_duration_label as update_duration_label_impl,
    update_frame_preview_thumbnail as update_frame_preview_thumbnail_impl,
)
from utils.settings_utils import load_user_settings as load_user_settings_impl, save_user_settings as save_user_settings_impl
from views import build_main_window_ui
from widgets import TimelineWidget, VideoView
from workers import (
    ExtractionWorker,
    SegmentAudioPreviewWorker,
    VocalSeparationWorker,
    VoiceOverWorker,
)

# Import our backend modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))
from video_processor import get_video_dimensions

class VideoTranslatorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Subtitle Translator - Antigravity")
        self.settings = QSettings("CapCap", "VideoTranslatorGUI")
        self.setAcceptDrops(True)
        
        # Maximize and prevent resizing
        self.setWindowState(Qt.WindowMaximized)
        # To strictly prevent resizing after maximizing:
        self.setFixedSize(QApplication.primaryScreen().availableGeometry().size())
        
        # Stylesheet for Premium Dark Mode
        self.setStyleSheet("""
            QMainWindow {
                background-color: #101826;
            }
            QWidget {
                color: #dbe5f3;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            #centralWidget {
                background-color: #101826;
            }
            #leftPanelArea {
                background-color: #162133;
                border-right: 1px solid #28364c;
            }
            #leftPanelContainer {
                background-color: #162133;
            }
            #rightPanel {
                background-color: #101826;
            }
            QGroupBox {
                border: 1px solid #30425b;
                border-radius: 14px;
                margin-top: 25px;
                font-weight: bold;
                color: #f3f7fb;
                background-color: #1b273a;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #8ad7ff;
            }
            QFrame#heroCard, QFrame#statusCard, QFrame#sideInfoCard {
                background-color: #0f1724;
                border: 1px solid #2d425d;
                border-radius: 14px;
            }
            QLabel#heroTitle {
                font-size: 20px;
                font-weight: 700;
                color: #f8fbff;
            }
            QLabel#heroBody, QLabel#statusBody, QLabel#helperLabel, QLabel#previewContextLabel {
                color: #a9b8cb;
                line-height: 1.35em;
            }
            QLabel#sectionTitle {
                font-size: 13px;
                font-weight: 700;
                color: #8ad7ff;
            }
            QLabel#statusHeadline {
                font-size: 16px;
                font-weight: 700;
                color: #f8fbff;
            }
            QLabel#statusPill {
                background-color: #1d3a52;
                color: #9fe5ff;
                border: 1px solid #336180;
                border-radius: 999px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton {
                background-color: #24364f;
                color: #ffffff;
                border: 1px solid #335171;
                border-radius: 10px;
                padding: 10px 18px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2d4665;
                border-color: #4575a8;
            }
            QPushButton#mainActionBtn {
                background-color: #4ed0b3;
                color: #0b1620;
                border: none;
                font-size: 13px;
                border-bottom: 2px solid #258971;
            }
            QPushButton#mainActionBtn:hover {
                background-color: #66ddc2;
            }
            QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #111927;
                border: 1px solid #31445d;
                border-radius: 10px;
                color: #ffffff;
                padding: 8px;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #8ad7ff;
            }
            QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
                background-color: #0d1420;
                color: #8b9bb0;
                border: 1px solid #243447;
            }
            QLineEdit::placeholder, QTextEdit {
                selection-background-color: #325173;
            }
            QProgressBar {
                border: 1px solid #2a3a50;
                border-radius: 10px;
                text-align: center;
                background-color: #111927;
                color: white;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5ed5c9, stop:1 #2b9f96);
                border-radius: 10px;
            }
            QLabel {
                background: transparent;
                color: #dbe5f3;
                font-size: 12px;
            }
            QCheckBox {
                background: transparent;
                color: #dbe5f3;
            }
            QRadioButton {
                background: transparent;
                color: #dbe5f3;
            }
            QScrollArea {
                border: none;
                background-color: #162133;
            }
            QScrollBar:vertical {
                border: none;
                background: #142030;
                width: 12px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #35506f;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #416287;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            /* Fix ComboBox Dropdown colors */
            QComboBox QAbstractItemView {
                background-color: #111927;
                color: #ffffff;
                selection-background-color: #325173;
                border: 1px solid #31445d;
                outline: none;
            }
            QMessageBox {
                background-color: #101826;
            }
            QMessageBox QLabel {
                color: #e6eef9;
                background: transparent;
            }
            QMessageBox QPushButton {
                min-width: 96px;
            }
            QTabWidget::pane {
                border: 1px solid #30425b;
                border-radius: 12px;
                background: #111927;
                top: -1px;
            }
            QTabBar::tab {
                background: #1d2c40;
                color: #a8bad2;
                padding: 9px 14px;
                border: 1px solid #30425b;
                border-bottom: none;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                min-width: 110px;
            }
            QTabBar::tab:selected {
                background: #111927;
                color: #8ad7ff;
            }
        """)

        # -----------------------------
        # State (must exist before setup_ui)
        # -----------------------------
        # Track generated/selected artifacts for quick inspection.
        # Keys are stable IDs, values are absolute file paths.
        self.processed_artifacts = {}
        self.workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.project_service = ProjectService(self.workspace_root)
        self.project_bridge = GUIProjectBridge(self.project_service)
        self.subtitle_controller = SubtitleController(self)
        self.pipeline_controller = PipelineController(self)
        self.preview_controller = PreviewController(self)
        self.current_project_state = None
        self.current_segment_models = []
        self.current_translated_segment_models = []

        # Simple pipeline runner (Run All)
        self._pipeline_active = False
        self._pipeline_step = ""

        # Log buffer (UI panel)
        self._log_lines = []

        self.setup_ui()
        self.setup_media_player()
        self.setup_audio_preview_player()
        self.load_user_settings()
        self.refresh_saved_subtitle_style_presets()

    def get_selected_subtitle_preset(self) -> str:
        if getattr(self, "subtitle_preset_custom_radio", None) and self.subtitle_preset_custom_radio.isChecked():
            return "custom"
        if getattr(self, "subtitle_preset_youtube_radio", None) and self.subtitle_preset_youtube_radio.isChecked():
            return "youtube"
        if getattr(self, "subtitle_preset_minimal_radio", None) and self.subtitle_preset_minimal_radio.isChecked():
            return "minimal"
        return "tiktok"

    def get_subtitle_preset_config(self, preset_key: str | None = None) -> dict:
        preset = (preset_key or self.get_selected_subtitle_preset()).lower()
        presets = {
            "tiktok": {
                "label": "TikTok",
                "font_name": "Montserrat",
                "font_size": 64,
                "font_color": "#FFFFFF",
                "highlight_color": "#00E5FF",
                "outline_color": "#000000",
                "outline_width": 7,
                "shadow_color": "#000000",
                "shadow_depth": 2,
                "shadow_alpha": 0.7,
                "background_box": False,
                "background_color": "#000000",
                "background_alpha": 0.0,
                "animation": "Pop In",
                "bold": True,
                "summary": "Bold, large, white subtitle with heavy black stroke and punchy pop-in. Best for short-form, high-energy captions.",
            },
            "youtube": {
                "label": "YouTube",
                "font_name": "Roboto",
                "font_size": 52,
                "font_color": "#FFFFFF",
                "highlight_color": "#FFFFFF",
                "outline_color": "#000000",
                "outline_width": 3,
                "shadow_color": "#000000",
                "shadow_depth": 1,
                "shadow_alpha": 0.35,
                "background_box": True,
                "background_color": "#000000",
                "background_alpha": 0.4,
                "animation": "Fade In",
                "bold": False,
                "summary": "Clean white subtitle with subtle box background and soft fade. Built for long-form readability.",
            },
            "minimal": {
                "label": "Short",
                "font_name": "Inter",
                "font_size": 48,
                "font_color": "#FFFFFF",
                "highlight_color": "#FFFFFF",
                "outline_color": "#000000",
                "outline_width": 0,
                "shadow_color": "#000000",
                "shadow_depth": 1,
                "shadow_alpha": 0.15,
                "background_box": False,
                "background_color": "#000000",
                "background_alpha": 0.0,
                "animation": "Slide Up",
                "bold": False,
                "summary": "Light, modern caption with almost no stroke and a gentle slide/fade entrance.",
            },
            "custom": {
                "label": "Custom",
                "font_name": self.subtitle_font_combo.currentText().strip() or "Arial",
                "font_size": int(self.subtitle_font_size_spin.value()),
                "font_color": self.subtitle_color_hex,
                "highlight_color": "#00E5FF",
                "outline_color": "#000000",
                "outline_width": 3,
                "shadow_color": "#000000",
                "shadow_depth": 1,
                "shadow_alpha": 0.3,
                "background_box": bool(self.subtitle_background_cb.isChecked()),
                "background_color": "#000000",
                "background_alpha": 0.35,
                "animation": self.subtitle_animation_combo.currentText().strip() or "Static",
                "bold": bool(self.subtitle_bold_cb.isChecked()),
                "summary": "Fully manual preset. Font, size, color, animation and background follow your own selections.",
            },
        }
        return presets.get(preset, presets["tiktok"]).copy()

    def parse_srt_to_segments(self, srt_text):
        return parse_srt_to_segments(srt_text)

    def extract_subtitle_text_entries(self, srt_text):
        return extract_subtitle_text_entries(srt_text)

    def format_to_srt(self, segments):
        return format_segments_to_srt(segments)

    def format_timestamp(self, seconds):
        return format_timestamp(seconds)

    def setup_ui(self):
        build_main_window_ui(self)

    def setup_audio_preview_player(self):
        self.audio_preview_player = QMediaPlayer(self)
        self.audio_preview_output = QAudioOutput(self)
        self.audio_preview_player.setAudioOutput(self.audio_preview_output)
        self._last_audio_preview_path = ""
        self._segment_preview_threads = {}

    # -----------------------------
    # Logging + error helpers
    # -----------------------------
    def log(self, message: str):
        log_message_impl(self, message)

    def clear_log(self):
        clear_log_impl(self)

    def show_error(self, title: str, short_msg: str, details: str = ""):
        show_error_impl(self, title, short_msg, details)

    def stabilize_button(self, button: QPushButton, min_width: int = 220, min_height: int = 42):
        button.setMinimumWidth(min_width)
        button.setMinimumHeight(min_height)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def make_helper_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("helperLabel")
        return label

    def using_existing_audio_source(self) -> bool:
        return hasattr(self, "use_existing_audio_radio") and self.use_existing_audio_radio.isChecked()

    def resolve_selected_audio_path(self) -> str:
        if self.using_existing_audio_source():
            return self.mixed_audio_edit.text().strip()
        return (
            self.processed_artifacts.get("mixed_vi")
            or self.last_mixed_vi_path
            or self.last_voice_vi_path
            or ""
        ).strip()

    def on_audio_source_mode_changed(self):
        if not hasattr(self, "audio_source_hint_label"):
            return
        if self.using_existing_audio_source():
            self.audio_source_hint_label.setText(
                "Preview and export will use the file in 'Existing mixed audio'. Generated voice and background settings are ignored until you switch back."
            )
        else:
            self.audio_source_hint_label.setText(
                "Preview and export will use the audio generated by CapCap, including the background mix when available. Existing mixed audio is ignored until you switch to it."
            )
        self.refresh_ui_state()

    def on_advanced_toggled(self, checked: bool):
        if hasattr(self, "tabs"):
            self.tabs.setVisible(checked)
        if hasattr(self, "advanced_group"):
            self.advanced_group.setTitle("ADVANCED" if checked else "ADVANCED (click to open)")

    def on_auto_preview_toggled(self, checked: bool):
        if checked:
            self.schedule_auto_frame_preview()
        else:
            self.auto_frame_preview_timer.stop()
            self.seek_frame_preview_timer.stop()

    def schedule_live_subtitle_preview_refresh(self):
        if not hasattr(self, "live_subtitle_preview_timer"):
            return
        self.live_subtitle_preview_timer.start()

    def refresh_live_subtitle_preview(self):
        self.live_preview_segments, self.live_preview_editor_name = self._resolve_live_preview_segments()
        self.sync_live_subtitle_preview()

    def save_user_settings(self):
        save_user_settings_impl(self)

    def load_user_settings(self):
        load_user_settings_impl(self)

    def _highlight_color_hex(self) -> str:
        mapping = {
            "Yellow": "#FFD400",
            "Cyan": "#00E5FF",
            "Green": "#5CFF95",
            "Pink": "#FF6BD6",
        }
        return mapping.get(self.subtitle_highlight_color_combo.currentText().strip(), "#FFD400")

    def _saved_subtitle_style_payload(self) -> dict:
        return {
            "preset": self.get_selected_subtitle_preset(),
            "font": self.subtitle_font_combo.currentText().strip(),
            "size": int(self.subtitle_font_size_spin.value()),
            "color": self.subtitle_color_hex,
            "position": self.subtitle_align_combo.currentText().strip(),
            "animation": self.subtitle_animation_combo.currentText().strip(),
            "animation_time": float(self.subtitle_animation_time_spin.value()),
            "background": bool(self.subtitle_background_cb.isChecked()),
            "bold": bool(self.subtitle_bold_cb.isChecked()),
            "auto_keyword_highlight": bool(self.subtitle_keyword_highlight_cb.isChecked()),
            "highlight_color": self.subtitle_highlight_color_combo.currentText().strip(),
            "highlight_mode": self.subtitle_highlight_mode_combo.currentText().strip(),
        }

    def _read_saved_subtitle_style_presets(self) -> dict:
        raw_value = self.settings.value("saved_subtitle_styles", "{}")
        try:
            parsed = json.loads(raw_value) if isinstance(raw_value, str) else dict(raw_value)
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def refresh_saved_subtitle_style_presets(self):
        if not hasattr(self, "saved_subtitle_style_combo"):
            return
        saved = self._read_saved_subtitle_style_presets()
        self.saved_subtitle_style_combo.blockSignals(True)
        self.saved_subtitle_style_combo.clear()
        self.saved_subtitle_style_combo.addItem("My Presets", "")
        for name in sorted(saved.keys(), key=str.lower):
            self.saved_subtitle_style_combo.addItem(name, name)
        self.saved_subtitle_style_combo.setCurrentIndex(0)
        self.saved_subtitle_style_combo.blockSignals(False)

    def save_current_subtitle_style_preset(self):
        name, ok = QInputDialog.getText(self, "Save Style", "Preset name:")
        if not ok or not (name or "").strip():
            return
        preset_name = name.strip()
        saved = self._read_saved_subtitle_style_presets()
        saved[preset_name] = self._saved_subtitle_style_payload()
        self.settings.setValue("saved_subtitle_styles", json.dumps(saved, ensure_ascii=False))
        self.refresh_saved_subtitle_style_presets()
        idx = self.saved_subtitle_style_combo.findData(preset_name)
        if idx >= 0:
            self.saved_subtitle_style_combo.setCurrentIndex(idx)

    def load_selected_subtitle_style_preset(self, index: int):
        if index <= 0:
            return
        preset_name = self.saved_subtitle_style_combo.itemData(index)
        saved = self._read_saved_subtitle_style_presets()
        preset = saved.get(preset_name or "")
        if not isinstance(preset, dict):
            return

        key = str(preset.get("preset", "tiktok")).lower()
        if key == "youtube":
            self.subtitle_preset_youtube_radio.setChecked(True)
        elif key == "minimal":
            self.subtitle_preset_minimal_radio.setChecked(True)
        elif key == "custom":
            self.subtitle_preset_custom_radio.setChecked(True)
        else:
            self.subtitle_preset_tiktok_radio.setChecked(True)

        self.subtitle_font_combo.setCurrentText(str(preset.get("font", self.subtitle_font_combo.currentText())))
        self.subtitle_font_size_spin.setValue(int(preset.get("size", self.subtitle_font_size_spin.value())))
        self.subtitle_color_hex = str(preset.get("color", self.subtitle_color_hex)).upper()
        self.subtitle_color_btn.setText(self.subtitle_color_hex)
        self.subtitle_align_combo.setCurrentText(str(preset.get("position", self.subtitle_align_combo.currentText())))
        self.subtitle_animation_combo.setCurrentText(str(preset.get("animation", self.subtitle_animation_combo.currentText())))
        self.subtitle_animation_time_spin.setValue(float(preset.get("animation_time", self.subtitle_animation_time_spin.value())))
        self.subtitle_background_cb.setChecked(bool(preset.get("background", self.subtitle_background_cb.isChecked())))
        self.subtitle_bold_cb.setChecked(bool(preset.get("bold", self.subtitle_bold_cb.isChecked())))
        self.subtitle_keyword_highlight_cb.setChecked(bool(preset.get("auto_keyword_highlight", self.subtitle_keyword_highlight_cb.isChecked())))
        self.subtitle_highlight_color_combo.setCurrentText(str(preset.get("highlight_color", self.subtitle_highlight_color_combo.currentText())))
        self.subtitle_highlight_mode_combo.setCurrentText(str(preset.get("highlight_mode", self.subtitle_highlight_mode_combo.currentText())))
        self.on_subtitle_preset_changed()

    def ensure_current_project(self):
        video_path = self.video_path_edit.text().strip()
        state = self.project_bridge.ensure_project(
            video_path=video_path,
            mode=self.get_output_mode_key(),
            translator_ai=self.is_ai_polish_enabled(),
            input_language=self.get_source_language_code(),
            target_language=self.get_target_language_code(),
        )
        if not state:
            return None
        self.current_project_state = state
        self.processed_artifacts.update(state.artifacts)
        return state

    def update_project_step(self, step_name: str, status: str):
        state = self.ensure_current_project()
        if not state:
            return
        self.project_bridge.update_step(state, step_name, status)

    def update_project_artifact(self, artifact_name: str, path: str):
        state = self.ensure_current_project()
        if not state or not path:
            return
        self.processed_artifacts[artifact_name] = path
        self.project_bridge.update_artifact(state, artifact_name, path)

    def _dict_segments_to_models(self, segments, *, translated=False):
        return self.project_bridge.dict_segments_to_models(segments, translated=translated)

    def _sync_segment_models_from_current_segments(self):
        self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
        self.current_translated_segment_models = self._dict_segments_to_models(
            self.current_translated_segments,
            translated=True,
        )

    def persist_transcription_project_data(self, raw_segments, srt_path=""):
        state = self.ensure_current_project()
        if not state:
            return
        self.current_segment_models = self.project_bridge.persist_transcription(state, raw_segments, srt_path)

    def persist_translation_project_data(self, translated_segments, srt_path=""):
        state = self.ensure_current_project()
        if not state:
            return
        self.current_translated_segment_models = self.project_bridge.persist_translation(
            state,
            self.current_segment_models,
            translated_segments,
            srt_path,
        )

    def load_project_context(self, state):
        if not state:
            return
        context = self.project_bridge.load_context(state)
        self.processed_artifacts.update(context["artifacts"])
        self.last_original_srt_path = context["last_original_srt_path"] or self.last_original_srt_path
        self.last_translated_srt_path = context["last_translated_srt_path"] or self.last_translated_srt_path
        self.last_extracted_audio = context["last_extracted_audio"] or self.last_extracted_audio
        self.last_vocals_path = context["last_vocals_path"] or self.last_vocals_path
        self.last_music_path = context["last_music_path"] or self.last_music_path
        self.last_voice_vi_path = context["last_voice_vi_path"] or self.last_voice_vi_path
        self.last_mixed_vi_path = context["last_mixed_vi_path"] or self.last_mixed_vi_path
        self.current_segment_models = context["current_segment_models"]
        self.current_translated_segment_models = context["current_translated_segment_models"]
        self.current_segments = context["current_segments"]
        self.current_translated_segments = context["current_translated_segments"]
        if self.last_vocals_path and os.path.exists(self.last_vocals_path):
            self.audio_source_edit.setText(self.last_vocals_path)
        elif self.last_extracted_audio and os.path.exists(self.last_extracted_audio):
            self.audio_source_edit.setText(self.last_extracted_audio)
        if self.last_music_path and os.path.exists(self.last_music_path):
            if hasattr(self, "bg_music_edit") and not self.bg_music_edit.text().strip():
                self.bg_music_edit.setText(self.last_music_path)
        if self.current_segments:
            self.transcript_text.setText(self.format_to_srt(self.current_segments))
        if self.current_translated_segments:
            self.translated_text.setText(self.format_to_srt(self.current_translated_segments))
        if self.current_translated_segments or self.current_segments:
            self.apply_segments_to_timeline()

    def schedule_auto_frame_preview(self):
        if not hasattr(self, "auto_preview_frame_cb") or not self.auto_preview_frame_cb.isChecked():
            return
        if getattr(self, "_pipeline_active", False):
            return
        if not self.video_path_edit.text().strip() or not self.get_active_segments():
            return
        self.frame_preview_status_label.setText("Refreshing exact frame preview...")
        self.auto_frame_preview_timer.start()

    def trigger_auto_frame_preview(self):
        self.start_exact_frame_preview(show_dialog=False)

    def schedule_seek_frame_preview(self):
        if not hasattr(self, "auto_preview_frame_cb") or not self.auto_preview_frame_cb.isChecked():
            return
        if getattr(self, "_pipeline_active", False):
            return
        if self.media_player.is_playing():
            return
        if not self.video_path_edit.text().strip() or not self.get_active_segments():
            return
        self.frame_preview_status_label.setText("Updating exact frame preview for the selected timeline position...")
        self.seek_frame_preview_timer.start()

    def trigger_seek_frame_preview(self):
        if self.media_player.is_playing():
            return
        self.start_exact_frame_preview(show_dialog=False)

    def update_frame_preview_thumbnail(self, image_path: str):
        update_frame_preview_thumbnail_impl(self, image_path, QPixmap, Qt)

    def cleanup_file_if_exists(self, path: str):
        cleanup_file_if_exists_impl(path)

    def get_output_mode_key(self):
        value = self.output_mode_combo.currentText() if hasattr(self, "output_mode_combo") else "Vietnamese subtitles + voice"
        return get_output_mode_key(value)

    def get_source_language_code(self):
        if not hasattr(self, "lang_whisper_combo"):
            return "auto"
        value = self.lang_whisper_combo.currentData()
        if value:
            return str(value)
        return self.lang_whisper_combo.currentText().strip() or "auto"

    def get_target_language_code(self):
        if not hasattr(self, "lang_target_combo"):
            return "vi"
        value = self.lang_target_combo.currentData()
        if value:
            return str(value)
        label = self.lang_target_combo.currentText().strip().lower()
        if "english" in label:
            return "en"
        return "vi"

    def is_ai_polish_enabled(self):
        return bool(getattr(self, "enable_ai_polish_cb", None) and self.enable_ai_polish_cb.isChecked())

    def on_output_mode_changed(self, value: str):
        mode = self.get_output_mode_key()
        self.workflow_hint_label.setText(build_workflow_hint(mode, self.is_ai_polish_enabled()))

        show_voice = mode in ("voice", "both")
        if hasattr(self, "voice_section_card"):
            self.voice_section_card.setVisible(show_voice)
        self.voiceover_btn.setVisible(show_voice)
        self.preview_btn.setVisible(show_voice)
        self.mixed_audio_edit.setEnabled(show_voice)
        self.use_generated_audio_radio.setVisible(show_voice)
        self.use_existing_audio_radio.setVisible(show_voice)
        self.audio_source_hint_label.setVisible(show_voice)

        if hasattr(self, "output_subtitle_radio"):
            self.output_subtitle_radio.setChecked(mode == "subtitle")
            self.output_voice_radio.setChecked(mode == "voice")
            self.output_both_radio.setChecked(mode == "both")
        self.export_btn.setText(get_export_button_label(mode))
        self.refresh_ui_state()

    def update_guidance_panel(self):
        guidance = build_guidance_state(
            video_path=self.video_path_edit.text(),
            transcript_text=self.transcript_text.toPlainText(),
            translated_text=self.translated_text.toPlainText(),
            translated_srt_path=self.last_translated_srt_path,
            selected_audio_path=self.resolve_selected_audio_path(),
            mode=self.get_output_mode_key(),
            pipeline_active=getattr(self, "_pipeline_active", False),
            mode_label=self.output_mode_combo.currentText(),
        )

        self.workflow_status_badge.setText(guidance["badge"])
        self.next_step_label.setText(guidance["headline"])
        self.readiness_label.setText(guidance["readiness"])
        self.update_progress_checklist()
        self.update_preview_context_label(guidance["has_subtitles"], guidance["has_voice_audio"])

    def update_project_header(self):
        video_path = self.video_path_edit.text().strip()
        if video_path:
            video_name = os.path.basename(video_path)
            self.project_title_label.setText(f"Project: {video_name}")
            self.upload_status_label.setText(f"âœ” {video_name} uploaded")
        else:
            self.project_title_label.setText("Project: No video selected")
            self.upload_status_label.setText("No video uploaded yet")

    def toggle_controls_panel(self):
        currently_visible = bool(getattr(self, "left_panel_scroll_area", None) and self.left_panel_scroll_area.isVisible())
        self.set_controls_panel_visible(not currently_visible)

    def set_controls_panel_visible(self, visible: bool):
        if hasattr(self, "left_panel_scroll_area"):
            self.left_panel_scroll_area.setVisible(visible)
        if hasattr(self, "toggle_controls_btn"):
            self.toggle_controls_btn.setText("Hide Controls" if visible else "Show Controls")

    def update_progress_checklist(self):
        steps = getattr(getattr(self, "current_project_state", None), "steps", {}) or {}

        has_audio = bool(
            (self.last_extracted_audio and os.path.exists(self.last_extracted_audio))
            or (self.audio_source_edit.text().strip() and os.path.exists(self.audio_source_edit.text().strip()))
            or steps.get("extract_audio") == "done"
        )
        has_subtitle = bool(self.current_segments or self.transcript_text.toPlainText().strip()) or steps.get("transcribe") == "done"
        has_translation = bool(self.current_translated_segments or self.last_translated_srt_path) or steps.get("translate_raw") == "done"
        has_voice = bool(self.resolve_selected_audio_path() and os.path.exists(self.resolve_selected_audio_path()))
        translation_running = steps.get("translate_raw") == "running" or steps.get("refine_translation") == "running"
        voice_running = steps.get("generate_tts") == "running" or steps.get("mix_audio") == "running"

        self.progress_audio_label.setText(("âœ” " if has_audio else "â¬œ ") + "Audio analyzed")
        self.progress_subtitle_label.setText(("âœ” " if has_subtitle else "â¬œ ") + "Subtitle created")
        if translation_running:
            self.progress_translate_label.setText("â³ Translating...")
        else:
            self.progress_translate_label.setText(("âœ” " if has_translation else "â¬œ ") + "Translating")

        if self.get_output_mode_key() == "subtitle":
            self.progress_voice_label.setText("â¬œ Generating voice (not needed)")
        elif voice_running:
            self.progress_voice_label.setText("â³ Generating voice")
        else:
            self.progress_voice_label.setText(("âœ” " if has_voice else "â¬œ ") + "Generating voice")

    def update_preview_context_label(self, has_subtitles: bool, has_voice_audio: bool):
        subtitle_source = "Vietnamese review track" if self.current_translated_segments else ("original subtitle track" if self.current_segments else "no subtitle track yet")
        audio_source = "existing mixed audio" if self.using_existing_audio_source() else "generated Vietnamese voice"
        self.preview_context_label.setText(
            build_preview_context_text(
                video_ready=bool(self.video_path_edit.text().strip()),
                has_subtitles=has_subtitles,
                has_voice_audio=has_voice_audio,
                subtitle_source=subtitle_source,
                audio_source=audio_source,
            )
        )

    def choose_subtitle_color(self):
        color = QColorDialog.getColor(QColor(self.subtitle_color_hex), self, "Choose Subtitle Color")
        if not color.isValid():
            return
        self.subtitle_color_hex = color.name().upper()
        self.subtitle_color_btn.setText(self.subtitle_color_hex)
        self.update_subtitle_preview_style()

    def update_subtitle_preview_style(self):
        if not hasattr(self, "video_view"):
            return
        item = self.video_view.subtitle_item
        source_h = max(1, getattr(self.video_view, "video_source_height", 0) or 1080)
        preview_rect = self.video_view.get_video_content_rect()
        preview_h = max(1.0, preview_rect.height() or float(self.video_view.height()) or 1.0)
        preset = self.get_subtitle_preset_config()
        export_font_size = int(self.subtitle_font_size_spin.value())
        preview_font_size = max(10, int(round(export_font_size * (preview_h / source_h))))
        font_name = (
            self.subtitle_font_combo.currentText().strip()
            if self.get_selected_subtitle_preset() == "custom"
            else preset.get("font_name", "Segoe UI")
        )
        item.set_style(
            font_name=font_name or preset.get("font_name", "Segoe UI"),
            font_size=preview_font_size,
            font_color=QColor(self.subtitle_color_hex),
        )
        item.set_alignment(self.subtitle_align_combo.currentText())
        item.set_positioning(
            x_offset=int(self.subtitle_x_offset_spin.value()),
            bottom_offset=int(self.subtitle_bottom_offset_spin.value()),
        )
        self.video_view.reposition_subtitle()
        self.sync_live_subtitle_preview()
        self.schedule_auto_frame_preview()

    def get_subtitle_export_style(self, segments=None):
        alignment_map = {
            "Bottom Left": 1,
            "Bottom Center": 2,
            "Bottom": 2,
            "Bottom Right": 3,
            "Center": 5,
            "Top Center": 8,
            "Top": 8,
        }
        preset = self.get_subtitle_preset_config()
        is_custom = self.get_selected_subtitle_preset() == "custom"
        export_font_size = max(1, int(round(int(self.subtitle_font_size_spin.value()) * self.subtitle_export_font_scale)))
        style_segments = segments if segments is not None else self.get_active_segments()
        return {
            "font_name": (
                self.subtitle_font_combo.currentText().strip()
                if is_custom
                else preset.get("font_name", "Arial")
            ) or preset.get("font_name", "Arial"),
            "font_size": export_font_size,
            "font_color": self._hex_to_ass_color(self.subtitle_color_hex),
            "highlight_color": self._hex_to_ass_color(self._highlight_color_hex()),
            "outline_color": self._hex_to_ass_color(preset.get("outline_color", "#000000")),
            "outline_width": float(preset.get("outline_width", 2)),
            "shadow_color": self._hex_to_ass_color(preset.get("shadow_color", "#000000")),
            "shadow_depth": float(preset.get("shadow_depth", 1)),
            "shadow_alpha": float(preset.get("shadow_alpha", 0.0)),
            "background_color": self._hex_to_ass_color(preset.get("background_color", "#000000")),
            "background_alpha": float(preset.get("background_alpha", 0.0)),
            "animation": (
                self.subtitle_animation_combo.currentText().strip()
                if is_custom
                else preset.get("animation", "Static")
            ) or preset.get("animation", "Static"),
            "animation_duration": float(self.subtitle_animation_time_spin.value()),
            "alignment": alignment_map.get(self.subtitle_align_combo.currentText(), 2),
            "margin_v": int(self.subtitle_bottom_offset_spin.value()),
            "background_box": bool(self.subtitle_background_cb.isChecked() if is_custom else preset.get("background_box", False)),
            "bold": bool(self.subtitle_bold_cb.isChecked() if is_custom else preset.get("bold", False)),
            "preset_key": self.get_selected_subtitle_preset(),
            "auto_keyword_highlight": bool(self.subtitle_keyword_highlight_cb.isChecked())
            and self.subtitle_highlight_mode_combo.currentText().strip() in ("Auto", "Auto + Manual"),
            "manual_highlights": (
                [list(seg.get("manual_highlights", [])) for seg in (style_segments or [])]
                if self.subtitle_highlight_mode_combo.currentText().strip() in ("Manual", "Auto + Manual")
                else [[] for _ in (style_segments or [])]
            ),
        }

    def on_subtitle_preset_changed(self):
        preset = self.get_subtitle_preset_config()
        is_custom = self.get_selected_subtitle_preset() == "custom"
        if not is_custom:
            self.subtitle_font_combo.setCurrentText(preset.get("font_name", "Arial"))
            self.subtitle_animation_combo.setCurrentText(preset.get("animation", "Static"))
            self.subtitle_background_cb.setChecked(bool(preset.get("background_box", False)))
            self.subtitle_bold_cb.setChecked(bool(preset.get("bold", False)))
        self.subtitle_font_combo.setEnabled(is_custom)
        self.subtitle_animation_combo.setEnabled(is_custom)
        self.subtitle_background_cb.setEnabled(is_custom)
        self.subtitle_bold_cb.setEnabled(is_custom)
        if hasattr(self, "subtitle_preset_summary_label"):
            self.subtitle_preset_summary_label.setText(
                f"{preset.get('label', 'Preset')}: {preset.get('summary', '')}"
            )
        self._update_animation_time_visibility()
        self.update_subtitle_preview_style()

    def _update_animation_time_visibility(self):
        current_animation = self.subtitle_animation_combo.currentText().strip().lower()
        show_animation_time = current_animation != "static"
        if hasattr(self, "subtitle_animation_time_label"):
            self.subtitle_animation_time_label.setVisible(show_animation_time)
        if hasattr(self, "subtitle_animation_time_spin"):
            self.subtitle_animation_time_spin.setVisible(show_animation_time)

    def on_subtitle_animation_changed(self):
        self._update_animation_time_visibility()
        self.update_subtitle_preview_style()

    def refresh_video_dimensions(self, path: str):
        refresh_video_dimensions_impl(self, path, get_video_dimensions)

    def _hex_to_ass_color(self, hex_color: str) -> str:
        color = QColor(hex_color)
        return f"&H00{color.blue():02X}{color.green():02X}{color.red():02X}"

    def export_final_video(self):
        self.preview_controller.export_final_video()

    def preview_five_seconds(self):
        self.preview_controller.preview_five_seconds()

    def start_exact_frame_preview(self, show_dialog: bool = True):
        self.preview_controller.start_exact_frame_preview(show_dialog=show_dialog)

    def preview_exact_frame(self):
        self.preview_controller.start_exact_frame_preview(show_dialog=True)

    def build_subtitle_preview_srt(self, start_seconds: float, duration_seconds: float):
        return self.preview_controller.build_subtitle_preview_srt(start_seconds, duration_seconds)

    def build_full_active_subtitle_srt(self):
        return self.preview_controller.build_full_active_subtitle_srt()

    def _format_compact_editor_timestamp(self, seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        minutes, sec = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    def _segment_editor_display_rows(self):
        base_segments = self.current_segments or []
        translated_segments = self.current_translated_segments or []
        row_count = max(len(base_segments), len(translated_segments))
        rows = []
        for idx in range(row_count):
            base = base_segments[idx] if idx < len(base_segments) else {}
            translated = translated_segments[idx] if idx < len(translated_segments) else {}
            reference = translated or base
            rows.append(
                {
                    "start": float(reference.get("start", 0.0)),
                    "end": float(reference.get("end", 0.0)),
                    "original": str(base.get("text", "")),
                    "translated": str(translated.get("text", "")),
                    "manual_highlights": list(translated.get("manual_highlights", [])),
                }
            )
        return rows

    def _normalize_manual_highlight(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").replace("\u2029", " ").replace("\n", " ")).strip()

    def _reconcile_manual_highlights(self, segment: dict):
        text = str(segment.get("text", ""))
        cleaned = []
        seen = set()
        for phrase in segment.get("manual_highlights", []):
            normalized = self._normalize_manual_highlight(phrase)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen or key not in text.lower():
                continue
            seen.add(key)
            cleaned.append(normalized)
        segment["manual_highlights"] = cleaned

    def _sync_segment_highlight_chip_row(self, index: int):
        if index < 0 or index >= len(getattr(self, "_segment_editor_rows", [])):
            return
        row = self._segment_editor_rows[index]
        chip_layout = row.get("highlight_chip_layout")
        placeholder = row.get("highlight_placeholder")
        if chip_layout is None:
            return

        while chip_layout.count():
            item = chip_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        highlights = []
        if index < len(self.current_translated_segments):
            highlights = list(self.current_translated_segments[index].get("manual_highlights", []))

        if placeholder:
            placeholder.setVisible(not highlights)

        for phrase in highlights:
            chip = QPushButton(f"[ {phrase} ]")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(
                "QPushButton { background-color: #173049; color: #9fe5ff; border: 1px solid #356081; border-radius: 999px; padding: 4px 10px; font-size: 11px; }"
                "QPushButton:hover { background-color: #214161; }"
            )
            chip.clicked.connect(lambda _=False, idx=index, value=phrase: self.remove_segment_manual_highlight(idx, value))
            chip_layout.addWidget(chip)
        chip_layout.addStretch()

    def add_segment_manual_highlight(self, index: int, editor: QTextEdit):
        if index < 0 or index >= len(self.current_translated_segments):
            QMessageBox.warning(self, "Highlight", "Please prepare translated subtitles first.")
            return

        selected_text = self._normalize_manual_highlight(editor.textCursor().selectedText())
        if not selected_text:
            QMessageBox.warning(self, "Highlight", "Select the translated text you want to highlight first.")
            return

        segment = self.current_translated_segments[index]
        segment.setdefault("manual_highlights", [])
        existing = {self._normalize_manual_highlight(item).lower() for item in segment.get("manual_highlights", [])}
        if selected_text.lower() not in existing:
            segment["manual_highlights"].append(selected_text)
        self._reconcile_manual_highlights(segment)
        self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
        self._sync_segment_highlight_chip_row(index)
        self._sync_hidden_translated_text_from_segments()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def remove_segment_manual_highlight(self, index: int, phrase: str):
        if index < 0 or index >= len(self.current_translated_segments):
            return
        target = self._normalize_manual_highlight(phrase).lower()
        segment = self.current_translated_segments[index]
        segment["manual_highlights"] = [
            item for item in segment.get("manual_highlights", [])
            if self._normalize_manual_highlight(item).lower() != target
        ]
        self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
        self._sync_segment_highlight_chip_row(index)
        self._sync_hidden_translated_text_from_segments()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def _clear_segment_editor_rows(self):
        if not hasattr(self, "segment_editor_layout"):
            return
        while self.segment_editor_layout.count():
            item = self.segment_editor_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                while child_layout.count():
                    child_item = child_layout.takeAt(0)
                    child_widget = child_item.widget()
                    if child_widget:
                        child_widget.deleteLater()

    def toggle_original_subtitle_visibility(self):
        show_original = bool(getattr(self, "show_original_subtitle_cb", None) and self.show_original_subtitle_cb.isChecked())
        for row in getattr(self, "_segment_editor_rows", []):
            row["original_label"].setVisible(show_original and bool(row["original_label"].text().strip()))

    def sync_segment_editor_rows(self):
        if not hasattr(self, "segment_editor_layout") or getattr(self, "_syncing_segment_editor", False):
            return

        self._syncing_segment_editor = True
        try:
            self._clear_segment_editor_rows()
            self._segment_editor_rows = []
            rows = self._segment_editor_display_rows()
            if not rows:
                empty_state = QFrame()
                empty_state.setObjectName("statusCard")
                empty_state.setMinimumHeight(180)
                empty_state.setStyleSheet(
                    "QFrame#statusCard { background-color: #132132; border: 1px dashed #35506f; border-radius: 16px; }"
                )
                empty_layout = QVBoxLayout(empty_state)
                empty_layout.setContentsMargins(18, 18, 18, 18)
                empty_layout.setSpacing(8)
                empty_layout.addStretch()
                empty_title = QLabel("Subtitle editor is waiting for content")
                empty_title.setObjectName("statusHeadline")
                empty_title.setAlignment(Qt.AlignCenter)
                empty_body = QLabel("Subtitle editor will appear here once transcript or translation is ready.")
                empty_body.setObjectName("helperLabel")
                empty_body.setWordWrap(True)
                empty_body.setAlignment(Qt.AlignCenter)
                empty_layout.addWidget(empty_title)
                empty_layout.addWidget(empty_body)
                empty_layout.addStretch()
                self.segment_editor_layout.addWidget(empty_state)
                self.segment_editor_layout.addStretch()
                return

            show_original = bool(getattr(self, "show_original_subtitle_cb", None) and self.show_original_subtitle_cb.isChecked())
            for idx, row in enumerate(rows):
                card = QFrame()
                card.setObjectName("statusCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 12, 12, 12)
                card_layout.setSpacing(6)

                header_layout = QHBoxLayout()
                timestamp_label = QLabel(f"[{self._format_compact_editor_timestamp(row['start'])}]")
                timestamp_label.setObjectName("sectionTitle")
                preview_btn = QPushButton("🔊")
                preview_btn.setFixedWidth(44)
                preview_btn.clicked.connect(lambda _=False, idx=idx: self.preview_segment_audio(idx))
                highlight_btn = QPushButton("Highlight")
                highlight_btn.setFixedWidth(88)
                header_layout.addWidget(timestamp_label)
                header_layout.addStretch()
                header_layout.addWidget(highlight_btn)
                header_layout.addWidget(preview_btn)
                original_label = QLabel(row["original"] or "")
                original_label.setWordWrap(True)
                original_label.setObjectName("helperLabel")
                original_label.setVisible(show_original and bool(row["original"].strip()))

                arrow_label = QLabel("→")
                arrow_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #8ad7ff;")
                translated_editor = QTextEdit()
                translated_editor.setAcceptRichText(False)
                translated_editor.setPlainText(row["translated"])
                translated_editor.setMinimumHeight(60)
                translated_editor.setMaximumHeight(92)
                translated_editor.textChanged.connect(
                    lambda idx=idx, editor=translated_editor: self.on_segment_translation_edited(idx, editor)
                )
                highlight_btn.clicked.connect(
                    lambda _=False, idx=idx, editor=translated_editor: self.add_segment_manual_highlight(idx, editor)
                )

                highlight_meta_layout = QHBoxLayout()
                highlight_meta_layout.setContentsMargins(0, 0, 0, 0)
                highlight_meta_layout.setSpacing(6)
                highlight_placeholder = QLabel("[ Suggest highlight ]")
                highlight_placeholder.setObjectName("helperLabel")
                highlight_chip_container = QWidget()
                highlight_chip_layout = QHBoxLayout(highlight_chip_container)
                highlight_chip_layout.setContentsMargins(0, 0, 0, 0)
                highlight_chip_layout.setSpacing(6)
                highlight_meta_layout.addWidget(highlight_placeholder)
                highlight_meta_layout.addWidget(highlight_chip_container, 1)

                card_layout.addLayout(header_layout)
                card_layout.addWidget(original_label)
                card_layout.addWidget(arrow_label)
                card_layout.addWidget(translated_editor)
                card_layout.addLayout(highlight_meta_layout)
                self.segment_editor_layout.addWidget(card)
                self._segment_editor_rows.append(
                    {
                        "frame": card,
                        "original_label": original_label,
                        "translated_editor": translated_editor,
                        "preview_button": preview_btn,
                        "highlight_button": highlight_btn,
                        "highlight_placeholder": highlight_placeholder,
                        "highlight_chip_layout": highlight_chip_layout,
                    }
                )
                self._sync_segment_highlight_chip_row(idx)

            self.segment_editor_layout.addStretch()
            self._set_segment_editor_highlight(self._find_active_segment_index(self.media_player.position(), self.live_preview_segments or self.get_active_segments()))
        finally:
            self._syncing_segment_editor = False

    def sync_segment_editor_from_hidden_text(self):
        if getattr(self, "_syncing_hidden_editor_text", False):
            return

        transcript_text = self.transcript_text.toPlainText().strip()
        if transcript_text and not transcript_text.lower().startswith("transcribing..."):
            parsed_transcript = self.parse_srt_to_segments(transcript_text)
            if parsed_transcript:
                self.current_segments = parsed_transcript

        translated_text = self.translated_text.toPlainText().strip()
        if translated_text and not translated_text.lower().startswith("translating with "):
            base_segments = self.current_translated_segments or self.current_segments
            parsed_translated = self._segments_from_editor_text(translated_text, base_segments)
            if parsed_translated:
                self.current_translated_segments = parsed_translated

        self.sync_segment_editor_rows()

    def _sync_hidden_translated_text_from_segments(self):
        if getattr(self, "_syncing_segment_editor", False):
            return
        self._syncing_hidden_editor_text = True
        try:
            self.translated_text.setText(self.format_to_srt(self.current_translated_segments))
        finally:
            self._syncing_hidden_editor_text = False

    def on_segment_translation_edited(self, index: int, editor: QTextEdit):
        if getattr(self, "_syncing_segment_editor", False):
            return

        base_segments = self.current_segments or self.current_translated_segments
        if not base_segments or index >= len(base_segments):
            return

        if len(self.current_translated_segments) != len(base_segments):
            self.current_translated_segments = [
                {
                    "start": float(base.get("start", 0.0)),
                    "end": float(base.get("end", 0.0)),
                    "text": str(self.current_translated_segments[idx].get("text", "")) if idx < len(self.current_translated_segments) else "",
                }
                for idx, base in enumerate(base_segments)
            ]

        self.current_translated_segments[index]["text"] = editor.toPlainText().strip()
        self.current_translated_segments[index].setdefault("manual_highlights", [])
        self._reconcile_manual_highlights(self.current_translated_segments[index])
        self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
        self._sync_segment_highlight_chip_row(index)
        self._sync_hidden_translated_text_from_segments()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def _set_segment_editor_highlight(self, active_index: int):
        rows = getattr(self, "_segment_editor_rows", [])
        for idx, row in enumerate(rows):
            if idx == active_index:
                row["frame"].setStyleSheet("QFrame#statusCard { background-color: #153149; border: 1px solid #5fb9ff; border-radius: 14px; }")
                self.segment_editor_scroll.ensureWidgetVisible(row["frame"], 0, 36)
            else:
                row["frame"].setStyleSheet("")

    def play_audio_preview_file(self, audio_path: str):
        if not audio_path or not os.path.exists(audio_path):
            raise FileNotFoundError("Audio preview file was not found.")
        self.audio_preview_player.stop()
        self.audio_preview_player.setSource(QUrl.fromLocalFile(audio_path))
        self.audio_preview_player.play()
        self._last_audio_preview_path = audio_path

    def preview_current_audio_track(self):
        audio_path = self.resolve_selected_audio_path()
        if not audio_path or not os.path.exists(audio_path):
            QMessageBox.warning(self, "Missing Voice", "Please generate voice first before using Preview audio.")
            return
        try:
            self.play_audio_preview_file(audio_path)
            self.log(f"[Audio Preview] playing {audio_path}")
        except Exception as exc:
            self.show_error("Audio Preview Failed", "Could not preview the current audio track.", str(exc))

    def preview_segment_audio(self, index: int):
        if index < 0 or index >= len(self.current_translated_segments or self.current_segments):
            QMessageBox.warning(self, "Missing Subtitle", "This subtitle line is not ready yet.")
            return

        source_segments = self.current_translated_segments or self.current_segments
        text = str(source_segments[index].get("text", "")).strip()
        if not text:
            QMessageBox.warning(self, "Missing Subtitle", "This subtitle line is empty.")
            return

        voice_name = str(self.voice_name_combo.currentData() or self.voice_name_combo.currentText()).strip() or "vi-VN-HoaiMyNeural"
        row = self._segment_editor_rows[index] if index < len(self._segment_editor_rows) else None
        if row:
            row["preview_button"].setEnabled(False)
            row["preview_button"].setText("...")

        worker = SegmentAudioPreviewWorker(self.workspace_root, index, text, voice_name)
        worker.finished.connect(self.on_segment_audio_preview_ready)
        self._segment_preview_threads[index] = worker
        worker.start()

    def on_segment_audio_preview_ready(self, index: int, audio_path: str, error: str):
        row = self._segment_editor_rows[index] if index < len(self._segment_editor_rows) else None
        if row:
            row["preview_button"].setEnabled(True)
            row["preview_button"].setText("🔊")
        self._segment_preview_threads.pop(index, None)

        if error:
            self.show_error("Audio Preview Failed", "Could not generate preview audio for this subtitle.", error)
            return

        try:
            self.play_audio_preview_file(audio_path)
        except Exception as exc:
            self.show_error("Audio Preview Failed", "Could not play the generated preview audio.", str(exc))

    def download_subtitle(self):
        srt_text = self.translated_text.toPlainText().strip()
        if not srt_text:
            QMessageBox.warning(self, "Missing Subtitle", "No translated subtitle is ready yet.")
            return
        suggested_name = os.path.splitext(os.path.basename(self.video_path_edit.text().strip() or "subtitle"))[0] + "_vi.srt"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Subtitle", suggested_name, "Subtitle Files (*.srt)")
        if not file_path:
            return
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(srt_text)
        QMessageBox.information(self, "Saved", f"Subtitle saved to:\n\n{file_path}")

    def download_original_script(self):
        script_text = self.transcript_text.toPlainText().strip()
        if not script_text:
            QMessageBox.warning(self, "Missing Script", "No original script is ready yet.")
            return
        base_name = os.path.splitext(os.path.basename(self.video_path_edit.text().strip() or "original"))[0] + "_original"
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Original Script",
            base_name,
            "Subtitle Files (*.srt);;Text Files (*.txt)",
        )
        if not file_path:
            return
        output_text = script_text
        if "txt" in (selected_filter or "").lower() or file_path.lower().endswith(".txt"):
            parsed_segments = self.parse_srt_to_segments(script_text)
            output_text = "\n\n".join(seg.get("text", "").strip() for seg in parsed_segments if seg.get("text", "").strip())
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(output_text)
        QMessageBox.information(self, "Saved", f"Original script saved to:\n\n{file_path}")

    def on_export_finished(self, output_path, error):
        self.preview_controller.on_export_finished(output_path, error)

    def on_quick_preview_ready(self, output_path, error):
        self.preview_controller.on_quick_preview_ready(output_path, error)

    def on_exact_frame_ready(self, output_path, error):
        self.preview_controller.on_exact_frame_ready(output_path, error)

    def show_frame_preview_dialog(self, image_path: str):
        show_frame_preview_dialog_impl(self, image_path, QPixmap, Qt)

    # -----------------------------
    # Subtitle source handling
    # -----------------------------
    def get_active_segments(self):
        return self.current_translated_segments or self.current_segments or []

    def apply_segments_to_timeline(self):
        segs = self.get_active_segments()
        self.timeline.set_segments(segs if segs else [])
        self.video_view.subtitle_item.hide()
        self.sync_live_subtitle_preview()

    def _segments_from_editor_text(self, srt_text: str, base_segments):
        srt_text = (srt_text or "").strip()
        if not srt_text:
            return []

        if self.keep_timeline_cb.isChecked() and base_segments:
            edited_texts = self.extract_subtitle_text_entries(srt_text)
            if edited_texts and len(edited_texts) == len(base_segments):
                return [
                    {
                        "start": float(base["start"]),
                        "end": float(base["end"]),
                        "text": edited_texts[idx],
                        "manual_highlights": list(base.get("manual_highlights", [])),
                    }
                    for idx, base in enumerate(base_segments)
                ]

        parsed_segments = self.parse_srt_to_segments(srt_text)
        if base_segments and len(parsed_segments) == len(base_segments):
            for idx, segment in enumerate(parsed_segments):
                segment["manual_highlights"] = list(base_segments[idx].get("manual_highlights", []))
        return parsed_segments

    def _write_live_preview_assets(self, segments):
        if not segments:
            self.live_preview_subtitle_path = ""
            self.live_preview_ass_path = ""
            return "", ""

        preview_dir = os.path.join(os.getcwd(), "temp")
        os.makedirs(preview_dir, exist_ok=True)
        preview_srt_path = os.path.join(preview_dir, "live_preview_subtitle.srt")

        from subtitle_builder import generate_srt

        generate_srt(segments, preview_srt_path)
        self.live_preview_subtitle_path = preview_srt_path
        video_path = self.video_path_edit.text().strip()
        if video_path and os.path.exists(video_path):
            self.refresh_video_dimensions(video_path)
        video_width = getattr(self.video_view, "video_source_width", 0) or 1920
        video_height = getattr(self.video_view, "video_source_height", 0) or 1080
        subtitle_style = self.get_subtitle_export_style(segments=segments)
        self.live_preview_ass_path = srt_to_ass(
            preview_srt_path,
            video_width=video_width,
            video_height=video_height,
            alignment=subtitle_style.get("alignment", 2),
            margin_v=subtitle_style.get("margin_v", 30),
            font_name=subtitle_style.get("font_name", "Arial"),
            font_size=subtitle_style.get("font_size", 18),
            font_color=subtitle_style.get("font_color", "&H00FFFFFF"),
            background_box=subtitle_style.get("background_box", False),
            animation_style=subtitle_style.get("animation", "Static"),
            highlight_color=subtitle_style.get("highlight_color", "&H00FFFFFF"),
            outline_color=subtitle_style.get("outline_color", "&H00000000"),
            outline_width=subtitle_style.get("outline_width", 2.0),
            shadow_color=subtitle_style.get("shadow_color", "&H80000000"),
            shadow_depth=subtitle_style.get("shadow_depth", 1.0),
            background_color=subtitle_style.get("background_color", "&H80000000"),
            background_alpha=subtitle_style.get("background_alpha", 0.5),
            bold=subtitle_style.get("bold", False),
            preset_key=subtitle_style.get("preset_key", ""),
            auto_keyword_highlight=subtitle_style.get("auto_keyword_highlight", False),
            animation_duration=subtitle_style.get("animation_duration", 0.22),
            manual_highlights=subtitle_style.get("manual_highlights", []),
        )
        self.processed_artifacts["subtitle_preview_srt"] = self.live_preview_subtitle_path
        self.processed_artifacts["subtitle_preview_ass"] = self.live_preview_ass_path
        return self.live_preview_subtitle_path, self.live_preview_ass_path

    def _resolve_live_preview_segments(self):
        translated_text = self.translated_text.toPlainText().strip()
        if translated_text and not translated_text.lower().startswith("translating with "):
            base_segments = self.current_translated_segments or self.current_segments
            translated_segments = self._segments_from_editor_text(translated_text, base_segments)
            if translated_segments:
                return translated_segments, "translated"

        transcript_text = self.transcript_text.toPlainText().strip()
        if transcript_text and not transcript_text.lower().startswith("transcribing..."):
            transcript_segments = self._segments_from_editor_text(transcript_text, self.current_segments)
            if transcript_segments:
                return transcript_segments, "transcript"

        return [], ""

    def _resolve_live_preview_subtitle_path(self):
        segments, editor_name = self._resolve_live_preview_segments()
        self.live_preview_segments = segments
        self.live_preview_editor_name = editor_name
        return self._write_live_preview_assets(segments)

    def _find_active_segment_index(self, position_ms: int, segments):
        position_seconds = max(0.0, float(position_ms) / 1000.0)
        for idx, seg in enumerate(segments or []):
            if float(seg["start"]) <= position_seconds <= float(seg["end"]):
                return idx
        return -1

    def _set_editor_highlight(self, editor, active_index: int):
        if not editor:
            return

        selections = []
        text = editor.toPlainText()
        block_pattern = re.compile(
            r"(^|\n\n)(\d+\n\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}\n.*?)(?=\n\n\d+\n|\Z)",
            re.DOTALL,
        )
        chunks = [(match.start(2), match.end(2)) for match in block_pattern.finditer(text)]

        if 0 <= active_index < len(chunks):
            start, end = chunks[active_index]
            selection = QTextEdit.ExtraSelection()
            selection.cursor = editor.textCursor()
            selection.cursor.setPosition(start)
            selection.cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection.format.setBackground(QColor("#183248"))
            selection.format.setForeground(QColor("#EAF6FF"))
            selections.append(selection)
            temp_cursor = editor.textCursor()
            temp_cursor.setPosition(start)
            editor.setTextCursor(temp_cursor)
            editor.ensureCursorVisible()

        editor.setExtraSelections(selections)

    def update_playback_subtitle_highlight(self, position_ms: int):
        try:
            segments = self.live_preview_segments or self.get_active_segments()
            active_index = self._find_active_segment_index(position_ms, segments)
            self.timeline.set_active_segment_index(active_index)

            target_editor = None
            if self.live_preview_editor_name == "translated":
                target_editor = self.translated_text
            elif self.live_preview_editor_name == "transcript":
                target_editor = self.transcript_text
            elif self.current_translated_segments:
                target_editor = self.translated_text
            elif self.current_segments:
                target_editor = self.transcript_text

            self._set_segment_editor_highlight(active_index)
            self._set_editor_highlight(self.translated_text, active_index if target_editor is self.translated_text else -1)
            self._set_editor_highlight(self.transcript_text, active_index if target_editor is self.transcript_text else -1)
        except Exception as exc:
            self.log(f"[Preview] subtitle highlight skipped: {exc}")

    def sync_live_subtitle_preview(self):
        if not hasattr(self, "media_player"):
            return
        subtitle_srt_path, subtitle_ass_path = self._resolve_live_preview_subtitle_path()
        subtitle_path = subtitle_ass_path or subtitle_srt_path
        if not subtitle_path and self.current_translated_segments and self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path):
            subtitle_path = self.last_translated_srt_path
        elif not subtitle_path and self.current_segments and self.last_original_srt_path and os.path.exists(self.last_original_srt_path):
            subtitle_path = self.last_original_srt_path

        if subtitle_path:
            self.media_player.set_subtitle_file(subtitle_path, self.get_subtitle_export_style())
        else:
            self.media_player.clear_subtitle()

    def refresh_ui_state(self):
        """Basic enable/disable rules to guide user flow."""
        v_ok = bool(self.video_path_edit.text().strip()) and os.path.exists(self.video_path_edit.text().strip())
        a_ok = bool(self.audio_source_edit.text().strip()) and os.path.exists(self.audio_source_edit.text().strip())
        has_translated_text = bool(self.translated_text.toPlainText().strip())
        selected_audio_path = self.resolve_selected_audio_path()
        has_voice_audio = bool(selected_audio_path and os.path.exists(selected_audio_path))
        mode = self.get_output_mode_key()
        can_export = False
        if mode == "subtitle":
            can_export = v_ok and bool(self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path))
        elif mode == "voice":
            can_export = v_ok and has_voice_audio
        else:
            can_export = (
                v_ok
                and has_voice_audio
                and bool(self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path))
            )

        self.extract_btn.setEnabled(v_ok)
        self.vocal_sep_btn.setEnabled(a_ok)
        self.transcribe_btn.setEnabled(a_ok)
        self.translate_btn.setEnabled(bool(self.transcript_text.toPlainText().strip()))
        self.apply_translated_btn.setEnabled(has_translated_text)
        generated_mode = not self.using_existing_audio_source()
        self.voiceover_btn.setEnabled(has_translated_text and generated_mode and mode in ("voice", "both"))
        self.preview_btn.setEnabled(v_ok and has_voice_audio and mode in ("voice", "both"))
        if hasattr(self, "preview_audio_btn"):
            self.preview_audio_btn.setEnabled(has_voice_audio)
        if hasattr(self, "voice_name_combo"):
            self.voice_name_combo.setEnabled(generated_mode and mode in ("voice", "both"))
            self.bg_music_edit.setEnabled(generated_mode and mode in ("voice", "both"))
        self.run_all_btn.setEnabled(v_ok and not self._pipeline_active)
        self.preview_frame_btn.setEnabled(v_ok and bool(self.get_active_segments()))
        self.preview_5s_btn.setEnabled(v_ok)
        self.export_btn.setEnabled(can_export)
        if hasattr(self, "download_subtitle_btn"):
            self.download_subtitle_btn.setEnabled(bool(self.translated_text.toPlainText().strip()))
        if hasattr(self, "download_original_btn"):
            self.download_original_btn.setEnabled(bool(self.transcript_text.toPlainText().strip()))
        if hasattr(self, "tabs"):
            self.tabs.setTabEnabled(1, v_ok)
            self.tabs.setTabEnabled(2, v_ok and mode in ("voice", "both"))
        self.update_guidance_panel()

    def dragEnterEvent(self, event):
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            for url in mime_data.urls():
                local_path = url.toLocalFile()
                if local_path and os.path.splitext(local_path)[1].lower() in {".mp4", ".mkv", ".avi", ".mov"}:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            event.ignore()
            return
        for url in mime_data.urls():
            local_path = url.toLocalFile()
            if local_path and os.path.splitext(local_path)[1].lower() in {".mp4", ".mkv", ".avi", ".mov"}:
                self.video_path_edit.setText(local_path)
                self.media_player.setSource(QUrl.fromLocalFile(local_path))
                self.refresh_video_dimensions(local_path)
                self.play_btn.setText("Play")
                self.timeline.set_segments([])
                self.timeline.set_playing(False)
                self.current_segments = []
                self.current_translated_segments = []
                self.current_segment_models = []
                self.current_translated_segment_models = []
                self.current_project_state = self.ensure_current_project()
                self.load_project_context(self.current_project_state)
                self.media_player.pause()
                self.media_player.setPosition(0)
                self.refresh_ui_state()
                self.sync_live_subtitle_preview()
                self.schedule_auto_frame_preview()
                event.acceptProposedAction()
                return
        event.ignore()

    def run_extraction(self):
        v_path = self.video_path_edit.text()
        if not v_path: return
        
        target_dir = self.audio_folder_edit.text()
        file_basename = os.path.splitext(os.path.basename(v_path))[0]
        a_path = os.path.join(target_dir, file_basename + ".wav")
        
        self.progress_bar.setValue(10)
        self.update_project_step("extract_audio", "running")
        self.extraction_thread = ExtractionWorker(v_path, a_path)
        self.extraction_thread.finished.connect(self.on_extraction_finished)
        self.extraction_thread.start()

    def on_extraction_finished(self, success, path):
        self.progress_bar.setValue(30)
        self.extract_btn.setEnabled(True)
        if success:
            self.last_extracted_audio = path
            self.audio_source_edit.setText(path)
            self.processed_artifacts["audio_extracted"] = path
            self.update_project_artifact("extracted_audio", path)
            self.update_project_step("extract_audio", "done")
            QMessageBox.information(self, "Success", "Audio extraction completed!")
        else:
            self.update_project_step("extract_audio", "failed")
            self.show_error("Error", "Extraction failed.", str(path))
            self._pipeline_fail("Extraction failed.")
            return

        self.refresh_ui_state()
        self._pipeline_advance("extraction")

    def run_vocal_separation(self):
        audio_src = self.audio_source_edit.text()
        if not audio_src or not os.path.exists(audio_src):
            QMessageBox.warning(self, "Error", "Please extract audio or select a source first!")
            return
        
        target_dir = self.audio_folder_edit.text()
        self.progress_bar.setValue(35)
        self.vocal_sep_btn.setEnabled(False)
        self.vocal_sep_btn.setText("Separating... (AI Processing)")
        self.update_project_step("separate_audio", "running")
        
        self.vocal_thread = VocalSeparationWorker(audio_src, target_dir)
        self.vocal_thread.finished.connect(self.on_vocal_separation_finished)
        self.vocal_thread.start()

    def on_vocal_separation_finished(self, vocal, music, error):
        self.vocal_sep_btn.setEnabled(True)
        self.vocal_sep_btn.setText("Separate Voice and Background")
        self.progress_bar.setValue(50)
        
        if error:
            self.update_project_step("separate_audio", "failed")
            err_lower = error.lower()
            missing_demucs = (
                "no module named" in err_lower and "demucs" in err_lower
            ) or (
                "demucs is not installed" in err_lower
            ) or (
                "requires the 'demucs' library" in err_lower
            )
            if missing_demucs:
                QMessageBox.warning(
                    self,
                    "Dependency Missing",
                    "Vocal Separation requires the 'demucs' library.\n\n"
                    "Please run (using the same Python you run this app with):\n"
                    "python -m pip install demucs\n\n"
                    f"Details:\n{error}",
                )
            else:
                QMessageBox.critical(self, "Error", f"Separation failed:\n\n{error}")
            self.log(error)
            self.refresh_ui_state()
            return
        
        if vocal and os.path.exists(vocal):
            self.audio_source_edit.setText(vocal)
            self.last_extracted_audio = vocal
            self.last_vocals_path = vocal
            self.last_music_path = music
            self.processed_artifacts["vocals"] = vocal
            self.update_project_artifact("vocals", vocal)
            if music:
                self.processed_artifacts["music"] = music
                self.update_project_artifact("music", music)
                # Auto-fill background for STEP 4
                if not self.bg_music_edit.text().strip():
                    self.bg_music_edit.setText(music)
            self.update_project_step("separate_audio", "done")
            QMessageBox.information(self, "Success", 
                f"Audio stems separated!\n\nVocals: {os.path.basename(vocal)}\nBackground: {os.path.basename(music)}\n\nVocals are now selected for transcription.")
            self._pipeline_advance("separation")
        else:
            self.update_project_step("separate_audio", "failed")
            self._pipeline_fail("Separation did not produce output.")
        self.refresh_ui_state()

    def run_transcription(self):
        self.subtitle_controller.run_transcription()

    def on_transcription_finished(self, segments):
        self.subtitle_controller.on_transcription_finished(segments)

    def run_translation(self):
        self.subtitle_controller.run_translation()

    def on_translation_finished(self, translated_srt, error):
        self.subtitle_controller.on_translation_finished(translated_srt, error)

    def apply_edited_translation(self, show_message=True, force_apply=True):
        result = self.subtitle_controller.apply_edited_translation(show_message=show_message, force_apply=force_apply)
        if result:
            self.sync_segment_editor_rows()
        return result



    def setup_media_player(self):
        setup_media_player_impl(self)

    def browse_video(self):
        browse_video_impl(self)

    def browse_audio_folder(self):
        browse_audio_folder_impl(self)

    def browse_srt_output_folder(self):
        browse_srt_output_folder_impl(self)

    def browse_audio_source(self):
        browse_audio_source_impl(self)

    def browse_background_audio(self):
        browse_background_audio_impl(self)

    def browse_existing_mixed_audio(self):
        browse_existing_mixed_audio_impl(self)

    def browse_voice_output_folder(self):
        browse_voice_output_folder_impl(self)

    def run_voiceover(self):
        translated_srt = self.translated_text.toPlainText().strip()
        if not translated_srt:
            QMessageBox.warning(self, "Error", "No translated SRT available. Please run translation first (STEP 3).")
            return

        segments = self.parse_srt_to_segments(translated_srt)
        if not segments:
            QMessageBox.warning(self, "Error", "Translated SRT could not be parsed to segments.")
            return

        out_dir = self.voice_output_folder_edit.text().strip() or os.path.join(os.getcwd(), "output")
        bg_path = self.bg_music_edit.text().strip()
        voice_name = str(self.voice_name_combo.currentData() or self.voice_name_combo.currentText()).strip()
        voice_gain = float(self.voice_gain_spin.value())
        bg_gain = float(self.bg_gain_spin.value())

        self.voiceover_btn.setEnabled(False)
        self.voiceover_btn.setText("Generating... (TTS)")
        self.progress_bar.setValue(85)
        self.update_project_step("generate_tts", "running")
        if bg_path:
            self.update_project_step("mix_audio", "running")

        project_state_path = self.project_service.project_file(self.current_project_state.project_root) if self.current_project_state else ""
        self.voice_thread = VoiceOverWorker(
            self.workspace_root,
            segments,
            out_dir,
            bg_path,
            voice_name,
            voice_gain,
            bg_gain,
            project_state_path,
        )
        self.voice_thread.finished.connect(self.on_voiceover_finished)
        self.voice_thread.start()

    def on_voiceover_finished(self, voice_track, mixed, error):
        self.voiceover_btn.setEnabled(True)
        self.voiceover_btn.setText("Generate Voice / Mix")
        self.progress_bar.setValue(100)

        if error:
            self.update_project_step("generate_tts", "failed")
            if self.bg_music_edit.text().strip():
                self.update_project_step("mix_audio", "failed")
            QMessageBox.critical(self, "Error", f"Voiceover failed:\n\n{error}")
            self._pipeline_fail("Voiceover failed.")
            return

        if voice_track and os.path.exists(voice_track):
            self.last_voice_vi_path = voice_track
            self.processed_artifacts["voice_vi"] = voice_track
            self.update_project_artifact("voice_vi", voice_track)
            self.update_project_step("generate_tts", "done")
        if mixed and os.path.exists(mixed):
            self.last_mixed_vi_path = mixed
            self.processed_artifacts["mixed_vi"] = mixed
            self.update_project_artifact("mixed_vi", mixed)
            self.update_project_step("mix_audio", "done")
        elif self.bg_music_edit.text().strip():
            self.update_project_step("mix_audio", "skipped")

        if mixed:
            QMessageBox.information(self, "Success", f"Generated Vietnamese voice and mixed audio:\n\nVoice: {voice_track}\nMixed: {mixed}")
        else:
            QMessageBox.information(self, "Success", f"Generated Vietnamese voice track:\n\n{voice_track}\n\n(Background not provided, so no mix was created.)")

        self._pipeline_advance("voiceover")

    def preview_video_with_mixed_audio(self):
        self.preview_controller.preview_video_with_mixed_audio()

    def on_preview_ready(self, preview_path, error):
        self.preview_controller.on_preview_ready(preview_path, error)

    def run_all_pipeline(self):
        self.pipeline_controller.run_all_pipeline()

    def on_prepare_workflow_finished(self, project_state_path, error):
        self.pipeline_controller.on_prepare_workflow_finished(project_state_path, error)

    def _pipeline_advance(self, completed_step: str):
        self.pipeline_controller.pipeline_advance(completed_step)

    def _pipeline_fail(self, reason: str):
        self.pipeline_controller.pipeline_fail(reason)

    def _pipeline_done(self):
        self.pipeline_controller.pipeline_done()

    def open_folder(self, path):
        open_folder_impl(self, path)

    def show_processed_files(self):
        show_processed_files_impl(self)

    def cleanup_temp_preview_files(self):
        cleanup_temp_preview_files_impl(self)

    def closeEvent(self, event):
        try:
            self.save_user_settings()
            self.cleanup_temp_preview_files()
        finally:
            super().closeEvent(event)

    def toggle_play(self):
        toggle_play_impl(self)

    def stop_video(self):
        stop_video_impl(self)

    def position_changed(self, position):
        position_changed_impl(self, position)

    def duration_changed(self, duration):
        duration_changed_impl(self, duration)

    def set_position(self, position):
        set_position_impl(self, position)

    def update_duration_label(self, current, total):
        update_duration_label_impl(self, current, total)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoTranslatorGUI()
    window.show()
    sys.exit(app.exec())
