import sys
import os
import time
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QFileDialog, QCheckBox, QTextEdit, QComboBox,
                             QGroupBox, QSlider, QFrame, QProgressBar, QMessageBox,
                             QScrollArea,
                             QSpinBox, QColorDialog, QDoubleSpinBox, QTabWidget, QDialog, QSizePolicy,
                             QRadioButton)
from PySide6.QtCore import Qt, QUrl, QTimer, QSettings
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

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
        self.load_user_settings()

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

    def save_user_settings(self):
        save_user_settings_impl(self)

    def load_user_settings(self):
        load_user_settings_impl(self)

    def ensure_current_project(self):
        video_path = self.video_path_edit.text().strip()
        state = self.project_bridge.ensure_project(
            video_path=video_path,
            mode=self.get_output_mode_key(),
            translator_ai=self.is_ai_polish_enabled(),
            input_language=self.lang_whisper_combo.currentText().strip() or "auto",
            target_language="vi",
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
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            return
        if not self.video_path_edit.text().strip() or not self.get_active_segments():
            return
        self.frame_preview_status_label.setText("Updating exact frame preview for the selected timeline position...")
        self.seek_frame_preview_timer.start()

    def trigger_seek_frame_preview(self):
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            return
        self.start_exact_frame_preview(show_dialog=False)

    def update_frame_preview_thumbnail(self, image_path: str):
        update_frame_preview_thumbnail_impl(self, image_path, QPixmap, Qt)

    def cleanup_file_if_exists(self, path: str):
        cleanup_file_if_exists_impl(path)

    def get_output_mode_key(self):
        value = self.output_mode_combo.currentText() if hasattr(self, "output_mode_combo") else "Vietnamese subtitles + voice"
        return get_output_mode_key(value)

    def is_ai_polish_enabled(self):
        return bool(getattr(self, "enable_ai_polish_cb", None) and self.enable_ai_polish_cb.isChecked())

    def on_output_mode_changed(self, value: str):
        mode = self.get_output_mode_key()
        self.workflow_hint_label.setText(build_workflow_hint(mode, self.is_ai_polish_enabled()))

        show_voice = mode in ("voice", "both")
        self.voiceover_btn.setVisible(show_voice)
        self.preview_btn.setVisible(show_voice)
        self.mixed_audio_edit.setEnabled(show_voice)
        self.use_generated_audio_radio.setVisible(show_voice)
        self.use_existing_audio_radio.setVisible(show_voice)
        self.audio_source_hint_label.setVisible(show_voice)

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
        self.update_preview_context_label(guidance["has_subtitles"], guidance["has_voice_audio"])

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
        self.subtitle_color_btn.setText(f"Text Color: {self.subtitle_color_hex}")
        self.update_subtitle_preview_style()

    def update_subtitle_preview_style(self):
        if not hasattr(self, "video_view"):
            return
        item = self.video_view.subtitle_item
        source_h = max(1, getattr(self.video_view, "video_source_height", 0) or 1080)
        preview_rect = self.video_view.get_video_content_rect()
        preview_h = max(1.0, preview_rect.height() or float(self.video_view.height()) or 1.0)
        export_font_size = int(self.subtitle_font_size_spin.value())
        preview_font_size = max(10, int(round(export_font_size * (preview_h / source_h))))
        item.set_style(
            font_name=self.subtitle_font_combo.currentText().strip() or "Segoe UI",
            font_size=preview_font_size,
            font_color=QColor(self.subtitle_color_hex),
        )
        item.set_alignment(self.subtitle_align_combo.currentText())
        item.set_positioning(
            x_offset=int(self.subtitle_x_offset_spin.value()),
            bottom_offset=int(self.subtitle_bottom_offset_spin.value()),
        )
        self.video_view.reposition_subtitle()
        self.schedule_auto_frame_preview()

    def get_subtitle_export_style(self):
        alignment_map = {
            "Bottom Left": 1,
            "Bottom Center": 2,
            "Bottom Right": 3,
            "Center": 5,
            "Top Center": 8,
        }
        export_font_size = max(1, int(round(self.subtitle_font_size_spin.value() * self.subtitle_export_font_scale)))
        return {
            "font_name": self.subtitle_font_combo.currentText().strip() or "Arial",
            "font_size": export_font_size,
            "font_color": self._hex_to_ass_color(self.subtitle_color_hex),
            "alignment": alignment_map.get(self.subtitle_align_combo.currentText(), 2),
            "margin_v": int(self.subtitle_bottom_offset_spin.value()),
        }

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
        if hasattr(self, "voice_name_combo"):
            self.voice_name_combo.setEnabled(generated_mode and mode in ("voice", "both"))
            self.bg_music_edit.setEnabled(generated_mode and mode in ("voice", "both"))
        self.run_all_btn.setEnabled(v_ok and not self._pipeline_active)
        self.preview_frame_btn.setEnabled(v_ok and bool(self.get_active_segments()))
        self.preview_5s_btn.setEnabled(v_ok)
        self.export_btn.setEnabled(can_export)
        if hasattr(self, "tabs"):
            self.tabs.setTabEnabled(1, v_ok)
            self.tabs.setTabEnabled(2, v_ok and mode in ("voice", "both"))
        self.update_guidance_panel()

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
        return self.subtitle_controller.apply_edited_translation(show_message=show_message, force_apply=force_apply)



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
        voice_name = self.voice_name_combo.currentText().strip()
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
