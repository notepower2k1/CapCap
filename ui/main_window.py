import sys
import os
import re
import time
import json
import copy
import hashlib
import shutil
import subprocess
import threading
import webbrowser
from PySide6.QtWidgets import (
    QProgressDialog,QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QFileDialog, QCheckBox, QTextEdit, QComboBox,
                             QGroupBox, QSlider, QFrame, QProgressBar, QMessageBox,
                             QScrollArea,
                             QSpinBox, QColorDialog, QDoubleSpinBox, QTabWidget, QDialog, QSizePolicy, QInputDialog,
                             QRadioButton)
from PySide6.QtCore import Qt, QUrl, QTimer, QSettings, QSize, QEvent
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

APP_PATH = os.path.join(os.path.dirname(__file__), '..', 'app')
if APP_PATH not in sys.path:
    sys.path.append(APP_PATH)

from services import GUIProjectBridge, ProjectService, ResourceDownloadService, VoiceCatalogService
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
    validate_srt_text,
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
from utils.icon_utils import load_icon
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
from widgets.progress_dialog import BackgroundableProgressDialog
from runtime_paths import app_path, asset_path, bin_path, models_path, temp_path, workspace_root
from runtime_profile import is_remote_profile
from workers import (
    ExtractionWorker,
    ResourceDownloadWorker,
    SegmentAudioPreviewWorker,
    VoiceSamplePreviewWorker,
    VocalSeparationWorker,
    VoiceOverWorker,
)

# Import our backend modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))
from video_processor import get_video_dimensions


class _BootstrapMediaBackend:
    backend_name = "bootstrap"
    _source_path = ""

    def setSource(self, source):
        self._source_path = ""

    def play(self):
        return None

    def pause(self):
        return None

    def stop(self):
        return None

    def setPosition(self, position):
        return None

    def position(self):
        return 0

    def duration(self):
        return 0

    def playbackState(self):
        return QMediaPlayer.StoppedState

    def is_playing(self):
        return False

    def set_subtitle_file(self, subtitle_path, subtitle_style=None):
        return None

    def clear_subtitle(self):
        return None

    def set_audio_file(self, audio_path):
        return None

    def clear_audio(self):
        return None

    def set_blur_region(self, blur_region=None):
        return None

    def clear_blur_region(self):
        return None

    def set_volume(self, percent):
        return None

    def volume(self):
        return 100

    def set_muted(self, muted):
        return None

    def is_muted(self):
        return False

    def set_playback_rate(self, rate):
        return None

    def playback_rate(self):
        return 1.0

class VideoTranslatorGUI(QMainWindow):
    VOICE_ENTRY_ID_ROLE = Qt.UserRole + 1

    def __init__(self):
        super().__init__()
        title = "CapCap Video Translator"
        if is_remote_profile():
            title += " (Remote)"
        self.setWindowTitle(title)
        self.settings = QSettings("CapCap", "VideoTranslatorGUI")
        self.setAcceptDrops(True)
        self.logo_path = asset_path("capcap.png")
        if os.path.exists(self.logo_path):
            self.setWindowIcon(QIcon(self.logo_path))
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        
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
                background-color: #121b2b;
                border-right: 1px solid #223248;
            }
            #leftPanelContainer {
                background-color: #121b2b;
            }
            #rightPanel {
                background-color: #101826;
            }
            QGroupBox {
                border: none;
                border-radius: 0px;
                margin-top: 0px;
                font-weight: bold;
                color: #f3f7fb;
                background-color: transparent;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #8ad7ff;
            }
            QFrame#heroCard, QFrame#statusCard, QFrame#sideInfoCard {
                background-color: #0d1624;
                border: 1px solid #24384f;
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
            QLabel#helperLabel[filterModified="true"] {
                color: #8ad7ff;
                font-weight: 700;
            }
            QLabel#sectionTitle {
                font-size: 13px;
                font-weight: 700;
                color: #8ad7ff;
            }
            QLabel#timingChip {
                background-color: #173049;
                color: #9fe5ff;
                border: 1px solid #356081;
                border-radius: 999px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
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
            QLabel#statusChip {
                background-color: #152537;
                color: #dbe5f3;
                border: 1px solid #2e4764;
                border-radius: 999px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#statusChip[state="ok"] {
                background-color: #153528;
                color: #c8f7df;
                border: 1px solid #2f7a55;
            }
            QLabel#statusChip[state="running"] {
                background-color: #3a2d12;
                color: #ffe29a;
                border: 1px solid #9b7530;
            }
            QLabel#statusChip[state="na"] {
                background-color: #1c2430;
                color: #9fb3ca;
                border: 1px solid #3a4a5f;
            }
            QLabel#statusChip[state="pending"] {
                background-color: #152537;
                color: #dbe5f3;
                border: 1px solid #2e4764;
            }
            QPushButton {
                background-color: #213248;
                color: #ffffff;
                border: 1px solid #304b69;
                border-radius: 10px;
                padding: 8px 14px;
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
            QPushButton#secondaryActionBtn {
                background-color: #18314a;
                color: #dff4ff;
                border: 1px solid #4f88b4;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 14px;
            }
            QPushButton#secondaryActionBtn:hover {
                background-color: #21405f;
                border-color: #69a9dc;
            }
            QPushButton#secondaryActionBtn::menu-indicator {
                width: 0px;
                image: none;
            }
            QMenu#headerMoreMenu {
                background-color: #0f1724;
                color: #e6eef9;
                border: 1px solid #30425b;
                padding: 6px;
            }
            QMenu#headerMoreMenu::item {
                background-color: transparent;
                color: #e6eef9;
                padding: 8px 14px;
                border-radius: 8px;
            }
            QMenu#headerMoreMenu::item:selected {
                background-color: #213248;
                color: #ffffff;
            }
            QMenu#headerMoreMenu::separator {
                height: 1px;
                background: #2b425c;
                margin: 6px 8px;
            }
            QPushButton#workflowTabBtn {
                background-color: #162638;
                color: #9fb3ca;
                border: 1px solid #2b425c;
                border-radius: 10px;
                padding: 5px 9px;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton#workflowTabBtn:hover {
                background-color: #1c3047;
                border-color: #44698f;
            }
            QPushButton#workflowTabBtn:checked {
                background-color: #24425f;
                color: #f8fbff;
                border-color: #5fb9ff;
            }
            QStackedWidget#leftPanelStack {
                background: transparent;
            }
            QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #111927;
                border: 1px solid #31445d;
                border-radius: 10px;
                color: #ffffff;
                padding: 8px;
            }
            QScrollArea#segmentEditorScroll {
                background-color: transparent;
                border: none;
            }
            QWidget#segmentEditorContainer {
                background-color: transparent;
            }
            QFrame#segmentInspectorCard {
                background-color: #0d1624;
                border: 1px solid #24384f;
                border-radius: 0px;
            }
            QTextEdit#segmentInspectorEditor {
                background-color: #111b2b;
                border: 1px solid #35506f;
                border-radius: 10px;
                padding: 10px 12px;
            }
            QTextEdit#segmentInspectorEditor:focus {
                border: 1px solid #5fb9ff;
                background-color: #122033;
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
                background-color: #121b2b;
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
        self.workspace_root = workspace_root()
        self.project_service = ProjectService(self.workspace_root)
        self.project_bridge = GUIProjectBridge(self.project_service)
        self.voice_catalog_service = VoiceCatalogService(self.workspace_root)
        self.subtitle_controller = SubtitleController(self)
        self.pipeline_controller = PipelineController(self)
        self.preview_controller = PreviewController(self)
        self.current_project_state = None
        self.current_segment_models = []
        self.current_translated_segment_models = []
        self.selected_whisper_model_name = "base"
        self._last_audio_preview_path = ""
        self._segment_preview_threads = {}
        self._voice_sample_preview_thread = None
        self.voice_catalog_entries_all = []
        self.voice_catalog_entries = []
        self.voice_catalog_map = {}
        self._voice_signals_bound = False
        self._media_backend_ready = False
        self._preview_audio_signals_bound = False
        self.media_player = _BootstrapMediaBackend()
        self.voice_preview_dialog = None
        self._voice_preview_row_buttons = {}
        self._tracked_progress_dialogs = []
        self._resource_download_state = {
            "resource_id": "",
            "percent": 0,
            "message": "",
            "running": False,
        }
        self._timeline_timing_undo_stack = []
        self._timeline_timing_redo_stack = []
        self._suspend_timeline_undo = False
        self._timeline_waveform_cache_key = None
        self._timeline_waveform_samples = []
        self._timeline_waveform_duration_s = 0.0
        self._timeline_video_thumb_cache_key = None
        self._timeline_video_thumbnails = []
        self._pending_timeline_waveform_refresh = False
        self._pending_timeline_thumbnail_refresh = False
        self._video_filter_ui_sync = False
        self._video_filter_preset_key = "original"
        self._video_filter_intensity = 75
        self._video_filter_adjust_overrides = {
            "brightness": 0,
            "contrast": 0,
            "saturation": 0,
            "temperature": 0,
            "highlights": 0,
            "shadows": 0,
        }
        self._video_filter_user_modified = {
            "brightness": False,
            "contrast": False,
            "saturation": False,
            "temperature": False,
            "highlights": False,
            "shadows": False,
        }
        self._pending_video_filter_preview = False
        self._filter_thumbnail_visible = False
        self._play_video_filter_preview_when_ready = False
        self._filter_thumbnail_target_height = 320
        self._video_filter_preview_dirty = False
        self._video_filter_apply_requested = False
        # Simple pipeline runner (Run All)
        self._pipeline_active = False
        self._pipeline_step = ""

        # Pre-rendered video state
        self.last_preview_video_path = ""
        self.last_styled_preview_path = ""
        self.last_styled_preview_signature = ""
        self.last_exact_preview_5s_path = ""

        self._deferred_startup_stage1_done = False
        self._deferred_startup_stage2_done = False

        self.setup_ui()
        self._configure_local_voice_mode_ui()
        self._timeline_visual_refresh_timer = QTimer(self)
        self._timeline_visual_refresh_timer.setSingleShot(True)
        self._timeline_visual_refresh_timer.timeout.connect(self._run_pending_timeline_visual_refresh)
        QTimer.singleShot(0, self._run_deferred_startup_stage1)
        QTimer.singleShot(600, self._run_deferred_startup_stage2)

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
                "font_size": 68,
                "font_color": "#FFFFFF",
                "highlight_color": "#FFD400",
                "outline_color": "#000000",
                "outline_width": 7,
                "shadow_color": "#000000",
                "shadow_depth": 2,
                "shadow_alpha": 0.7,
                "background_box": False,
                "background_color": "#000000",
                "background_alpha": 0.0,
                "animation": "Word Highlight Karaoke",
                "bold": True,
                "auto_keyword_highlight": True,
                "highlight_mode": "Auto + Manual",
                "summary": "Large subtitle with karaoke-style word timing and highlighted keywords for short-form videos.",
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
                "background_alpha": 1.0,
                "animation": "Fade In",
                "bold": False,
                "auto_keyword_highlight": False,
                "highlight_mode": "Manual",
                "summary": "Clean subtitle with a solid background box for long-form readability.",
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
                "outline_width": 3 if bool(getattr(self, "subtitle_outline_cb", None) and self.subtitle_outline_cb.isChecked()) else 0,
                "shadow_color": "#000000",
                "shadow_depth": 1,
                "shadow_alpha": 0.3,
                "background_box": bool(self.subtitle_background_cb.isChecked()),
                "background_color": getattr(self, "subtitle_background_color_hex", "#000000"),
                "background_alpha": float(self.subtitle_bg_alpha_spin.value()) if hasattr(self, "subtitle_bg_alpha_spin") else 0.6,
                "animation": self.subtitle_animation_combo.currentText().strip() or "Static",
                "bold": bool(self.subtitle_bold_cb.isChecked()),
                "summary": "Fully manual preset. Font, size, color, animation and background follow your own selections.",
            },
        }
        return presets.get(preset, presets["tiktok"]).copy()

    def parse_srt_to_segments(self, srt_text):
        return parse_srt_to_segments(srt_text)

    def validate_srt_text(self, srt_text, expected_len=None):
        return validate_srt_text(srt_text, expected_len=expected_len)

    def extract_subtitle_text_entries(self, srt_text):
        return extract_subtitle_text_entries(srt_text)

    def format_to_srt(self, segments):
        return format_segments_to_srt(segments)

    def format_timestamp(self, seconds):
        return format_timestamp(seconds)

    def setup_ui(self):
        build_main_window_ui(self)

    def _run_deferred_startup_stage1(self):
        if getattr(self, "_deferred_startup_stage1_done", False):
            return
        self._deferred_startup_stage1_done = True
        self.setup_audio_preview_player()
        self.load_user_settings()
        self.refresh_saved_subtitle_style_presets()

    def _run_deferred_startup_stage2(self):
        if getattr(self, "_deferred_startup_stage2_done", False):
            return
        self._deferred_startup_stage2_done = True
        self.load_voice_preview_catalog()
        self.ensure_local_translator_auto_configured()

    def ensure_media_backend_ready(self):
        if getattr(self, "_media_backend_ready", False):
            return
        self.setup_media_player()
        if hasattr(self, "video_view") and hasattr(self.video_view, "blurRegionChanged"):
            try:
                self.video_view.blurRegionChanged.disconnect(self.apply_preview_blur_region)
            except Exception:
                pass
            self.video_view.blurRegionChanged.connect(self.apply_preview_blur_region)

    def _configure_local_voice_mode_ui(self):
        if hasattr(self, "use_free_voice_radio"):
            try:
                self.use_free_voice_radio.setChecked(True)
                self.use_free_voice_radio.setVisible(False)
                self.use_free_voice_radio.setEnabled(False)
            except Exception:
                pass
        if hasattr(self, "use_premium_voice_radio"):
            try:
                self.use_premium_voice_radio.setChecked(False)
                self.use_premium_voice_radio.setVisible(False)
                self.use_premium_voice_radio.setEnabled(False)
            except Exception:
                pass
        if hasattr(self, "premium_voice_combo"):
            try:
                self.premium_voice_combo.clear()
                self.premium_voice_combo.setVisible(False)
                self.premium_voice_combo.setEnabled(False)
            except Exception:
                pass
        if hasattr(self, "preview_voice_btn"):
            try:
                self.preview_voice_btn.setText("Preview voice")
            except Exception:
                pass
        if hasattr(self, "voice_preview_meta_label"):
            try:
                self.voice_preview_meta_label.setText("Generate a short preview audio clip with the selected local voice.")
            except Exception:
                pass

    def setup_audio_preview_player(self):
        if getattr(self, "_preview_audio_signals_bound", False):
            return
        self._preview_audio_signals_bound = True
        self.audio_preview_player = QMediaPlayer(self)
        self.audio_preview_output = QAudioOutput(self)
        self.audio_preview_player.setAudioOutput(self.audio_preview_output)
        self.voice_preview_library_player = QMediaPlayer(self)
        self.voice_preview_library_output = QAudioOutput(self)
        self.voice_preview_library_player.setAudioOutput(self.voice_preview_library_output)
        self.voice_preview_dialog = None
        self._voice_preview_row_buttons = {}

    def _voice_catalog_data_value(self, entry: dict) -> str:
        provider = str(entry.get("provider", "")).strip().lower()
        provider_voice = str(entry.get("provider_voice", "")).strip()
        entry_id = str(entry.get("id", "")).strip()
        if provider == "piper":
            return entry_id
        if provider == "edge":
            return f"edge:{provider_voice or 'vi-VN-HoaiMyNeural'}"
        return ""

    def _voice_provider_label(self, provider: str) -> str:
        provider_key = str(provider or "").strip().lower()
        if provider_key == "piper":
            return "Local"
        if provider_key == "edge":
            return "Edge"
        return str(provider or "Other").strip().title() or "Other"

    def _current_voice_tier(self) -> str:
        return "free"

    def _selected_voice_gender(self) -> str:
        if not hasattr(self, "voice_gender_combo"):
            return "any"
        return str(self.voice_gender_combo.currentText()).strip().lower()

    def _entry_has_preview_media(self, entry: dict | None) -> bool:
        if not entry:
            return False
        return bool(
            entry.get("preview_video_path")
            or entry.get("preview_video_url")
            or entry.get("preview_audio_path")
            or entry.get("preview_audio_url")
        )

    def set_voice_combo_value(self, combo, value):
        target = str(value or "").strip()
        if not combo or not target:
            return
        for index in range(combo.count()):
            item_value = str(combo.itemData(index) or "").strip()
            item_entry_id = str(combo.itemData(index, self.VOICE_ENTRY_ID_ROLE) or "").strip()
            if item_value == target or item_entry_id == target:
                combo.setCurrentIndex(index)
                return

    def _get_previewable_voice_catalog_entry(self):
        return None

    def _update_voice_preview_meta(self):
        if not hasattr(self, "voice_preview_meta_label"):
            return
        total_entries = len(self.voice_catalog_entries or [])
        if hasattr(self, "preview_voice_btn"):
            self.preview_voice_btn.setVisible(True)
            self.preview_voice_btn.setEnabled(total_entries > 0)
        if total_entries <= 0:
            self.voice_preview_meta_label.setText("No voices are available in the catalog yet.")
            return
        self.voice_preview_meta_label.setText(
            f"Local voices: {total_entries}. Click “Preview voice” to generate a short test clip."
        )

    def load_voice_preview_catalog(self):
        self._auto_sync_piper_voices_to_catalog()
        self.voice_catalog_entries_all = self.voice_catalog_service.load_catalog()
        self._apply_piper_voice_meta_overrides()
        if self.voice_preview_dialog is not None:
            self.voice_preview_dialog.close()
            self.voice_preview_dialog = None
        self.refresh_voice_catalog_combos()

    def _load_piper_voice_meta(self) -> dict:
        meta_path = models_path("piper", "voices_meta.json")
        if not os.path.exists(meta_path):
            return {}
        try:
            with open(meta_path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                return {}
            voices = payload.get("voices", {})
            return voices if isinstance(voices, dict) else {}
        except Exception:
            return {}

    def _normalize_gender_value(self, value: str) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return ""
        if raw in {"m", "male", "nam"}:
            return "male"
        if raw in {"f", "female", "nu", "ná»¯"}:
            return "female"
        if raw in {"any", "unknown", "none"}:
            return ""
        return raw

    def _voice_gender_sort_rank(self, value: str) -> int:
        normalized = self._normalize_gender_value(value)
        if normalized == "female":
            return 0
        if normalized == "male":
            return 1
        return 2

    def _voice_entry_sort_key(self, entry: dict) -> tuple:
        provider = str(entry.get("provider", "")).strip().lower()
        name = str(entry.get("name", entry.get("id", ""))).strip().lower()
        return (
            self._voice_gender_sort_rank(str(entry.get("gender", ""))),
            0 if provider == "edge" else 1,
            name,
        )

    def _apply_piper_voice_meta_overrides(self):
        voices_meta = self._load_piper_voice_meta()
        if not voices_meta:
            return
        for entry in self.voice_catalog_entries_all or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("provider", "")).strip().lower() != "piper":
                continue
            voice_id = str(entry.get("id", "")).strip()
            if not voice_id:
                continue
            meta = voices_meta.get(voice_id, {})
            if not isinstance(meta, dict):
                continue
            if "gender" in meta:
                entry["gender"] = self._normalize_gender_value(meta.get("gender", ""))

    def _auto_sync_piper_voices_to_catalog(self):
        models_dir = models_path("piper")
        if not os.path.isdir(models_dir):
            return
        catalog_path = app_path("voice_preview_catalog.json")
        os.makedirs(os.path.dirname(catalog_path), exist_ok=True)

        def titleize(voice_id: str) -> str:
            stem = str(voice_id or "").strip()
            if not stem:
                return "Voice"
            if re.match(r"^[a-z]{2}_[A-Z]{2}-", stem):
                return stem
            text = re.sub(r"[_-]+", " ", stem, flags=re.UNICODE).strip()
            text = re.sub(r"\s+", " ", text, flags=re.UNICODE)
            parts = [p for p in text.split(" ") if p]
            out = []
            for part in parts:
                if any(ch.isdigit() for ch in part):
                    out.append(part)
                else:
                    out.append(part[:1].upper() + part[1:].lower())
            return " ".join(out) if out else stem

        def language_from_piper_config(model_path: str) -> str:
            cfg_path = f"{model_path}.json"
            if not os.path.exists(cfg_path):
                return ""
            try:
                with open(cfg_path, "r", encoding="utf-8", errors="ignore") as handle:
                    head = handle.read(16384)
            except Exception:
                return ""
            match = re.search(
                r"\"espeak\"\\s*:\\s*{[^}]*\"voice\"\\s*:\\s*\"([^\"]+)\"",
                head,
                flags=re.IGNORECASE | re.DOTALL,
            )
            voice = (match.group(1).strip() if match else "").lower()
            if not voice:
                return ""
            return re.split(r"[-_]", voice, 1)[0].strip().lower()

        def provider_voice_for_model(model_path: str) -> str:
            return f"models/piper/{os.path.basename(model_path)}"

        try:
            if os.path.exists(catalog_path):
                with open(catalog_path, "r", encoding="utf-8-sig") as handle:
                    payload = json.load(handle) or {}
            else:
                payload = {}
        except Exception:
            payload = {}

        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("schema_version", 2)
        payload.setdefault("voices", [])
        voices = list(payload.get("voices", []) or [])

        by_id = {}
        for entry in voices:
            if isinstance(entry, dict) and entry.get("id"):
                by_id[str(entry.get("id")).strip()] = entry

        model_paths = sorted(
            [os.path.join(models_dir, name) for name in os.listdir(models_dir) if name.lower().endswith(".onnx")],
            key=lambda p: os.path.basename(p).lower(),
        )
        changed = False
        model_ids = set()
        if not model_paths:
            # No models => remove all Piper voices from catalog (keep non-piper voices like Edge).
            new_voices = []
            for entry in voices:
                if not isinstance(entry, dict):
                    continue
                provider = str(entry.get("provider", "")).strip().lower()
                if provider == "piper":
                    changed = True
                    continue
                new_voices.append(entry)
            if not changed:
                return
            payload["voices"] = new_voices
            try:
                with open(catalog_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
            except Exception as exc:
                try:
                    self.log(f"[Voice Catalog] Auto-sync Piper failed: {exc}")
                except Exception:
                    pass
            return

        for model_path in model_paths:
            voice_id = os.path.splitext(os.path.basename(model_path))[0]
            model_ids.add(voice_id)
            pv = provider_voice_for_model(model_path)
            lang = language_from_piper_config(model_path) or "vi"

            existing = by_id.get(voice_id)
            if isinstance(existing, dict) and str(existing.get("provider", "")).strip().lower() == "piper":
                if str(existing.get("provider_voice", "")).strip() != pv:
                    existing["provider_voice"] = pv
                    changed = True
                if not str(existing.get("language", "")).strip():
                    existing["language"] = lang
                    changed = True
                for key in ("preview_audio_url", "preview_audio_path", "preview_video_url", "preview_video_path"):
                    if key not in existing:
                        existing[key] = ""
                        changed = True
                if "tier" not in existing:
                    existing["tier"] = "free"
                    changed = True
                if "enabled" not in existing:
                    existing["enabled"] = True
                    changed = True
                if "tags" not in existing:
                    existing["tags"] = ["local", "piper"]
                    changed = True
                continue

            if voice_id == "vi_VN-vais1000-medium":
                name = "Vais1000 Medium (Local)"
            else:
                name = f"{titleize(voice_id)} (Local)"
            voices.append(
                {
                    "id": voice_id,
                    "name": name,
                    "provider": "piper",
                    "provider_voice": pv,
                    "language": lang,
                    "gender": "",
                    "tier": "free",
                    "preview_video_url": "",
                    "preview_video_path": "",
                    "preview_audio_url": "",
                    "preview_audio_path": "",
                    "enabled": True,
                    "tags": ["local", "piper"],
                }
            )
            changed = True

        # Remove Piper entries whose models were deleted.
        new_voices = []
        for entry in voices:
            if not isinstance(entry, dict):
                continue
            provider = str(entry.get("provider", "")).strip().lower()
            if provider == "piper":
                entry_id = str(entry.get("id", "")).strip()
                if not entry_id or entry_id not in model_ids:
                    changed = True
                    continue
            new_voices.append(entry)
        voices = new_voices

        if not changed:
            return

        payload["voices"] = voices
        try:
            with open(catalog_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except Exception as exc:
            try:
                self.log(f"[Voice Catalog] Auto-sync Piper failed: {exc}")
            except Exception:
                pass

    def refresh_voice_catalog_combos(self):
        self.voice_catalog_entries = []
        for entry in (self.voice_catalog_entries_all or []):
            if not entry or not isinstance(entry, dict):
                continue
            if not entry.get("enabled", True):
                continue
            provider = str(entry.get("provider", "")).strip().lower()
            if provider not in {"piper", "edge"}:
                continue
            self.voice_catalog_entries.append(entry)
        self.voice_catalog_entries.sort(key=self._voice_entry_sort_key)
        self.voice_catalog_map = {entry.get("id", ""): entry for entry in self.voice_catalog_entries if entry.get("id")}
        if not hasattr(self, "free_voice_combo"):
            return

        selected_gender = self._selected_voice_gender()
        previous_free = str(self.free_voice_combo.currentData() or "")

        self.free_voice_combo.clear()
        for entry in self.voice_catalog_entries:
            entry_gender = str(entry.get("gender", "")).strip().lower()
            if selected_gender in ("male", "female") and entry_gender not in (selected_gender, "any", ""):
                continue
            self.free_voice_combo.addItem(
                str(entry.get("name", entry.get("id", "Voice"))),
                self._voice_catalog_data_value(entry),
            )
            index = self.free_voice_combo.count() - 1
            self.free_voice_combo.setItemData(index, entry.get("id", ""), self.VOICE_ENTRY_ID_ROLE)

        if self.free_voice_combo.count() > 0:
            self.free_voice_combo.setCurrentIndex(0)
        if previous_free:
            self.set_voice_combo_value(self.free_voice_combo, previous_free)
        elif "vi_VN-vais1000-medium" in self.voice_catalog_map:
            self.set_voice_combo_value(self.free_voice_combo, "vi_VN-vais1000-medium")
        if not self._voice_signals_bound:
            self._voice_signals_bound = True
        self.on_voice_tier_changed()
        self._update_voice_preview_meta()

    def on_voice_gender_changed(self):
        self.refresh_voice_catalog_combos()

    def on_selected_voice_changed(self):
        self._update_voice_preview_meta()
        self._preload_active_voice_if_needed()

    def _preload_active_voice_if_needed(self):
        voice_name = self.get_active_voice_name()
        if not voice_name:
            return
        entry_id = str(self.free_voice_combo.currentData(self.VOICE_ENTRY_ID_ROLE) or '').strip() if hasattr(self, 'free_voice_combo') else ''
        entry = self.voice_catalog_map.get(entry_id) if hasattr(self, 'voice_catalog_map') else None
        provider = str((entry or {}).get('provider', '')).strip().lower()
        if provider != 'piper':
            return
        current_token = voice_name.strip()
        if getattr(self, '_voice_preload_inflight', '') == current_token or getattr(self, '_voice_preloaded_name', '') == current_token:
            return

        self._voice_preload_inflight = current_token

        def _worker(expected_voice: str):
            try:
                self._preload_tts_voice_impl(expected_voice)
                def _mark_ready():
                    if getattr(self, '_voice_preload_inflight', '') == expected_voice:
                        self._voice_preload_inflight = ''
                        self._voice_preloaded_name = expected_voice
                        self.log(f"[Voice] Piper voice preloaded: {expected_voice}")
                QTimer.singleShot(0, _mark_ready)
            except Exception as exc:
                def _mark_failed():
                    if getattr(self, '_voice_preload_inflight', '') == expected_voice:
                        self._voice_preload_inflight = ''
                        self.log(f"[Voice] Piper preload skipped: {exc}")
                QTimer.singleShot(0, _mark_failed)

        threading.Thread(target=_worker, args=(current_token,), daemon=True).start()

    def get_selected_premium_voice_catalog_entry(self):
        if not hasattr(self, "premium_voice_combo"):
            return None
        if not hasattr(self, "voice_catalog_entries"):
            return None
        entry_id = self.premium_voice_combo.currentData(self.VOICE_ENTRY_ID_ROLE)
        if entry_id and entry_id in self.voice_catalog_map:
            return self.voice_catalog_map[entry_id]
        current_value = str(self.premium_voice_combo.currentData() or "")
        for entry in self.voice_catalog_entries:
            if self._voice_catalog_data_value(entry) == current_value:
                return entry
        return None

    def get_active_voice_name(self) -> str:
        free_value = str(self.free_voice_combo.currentData() or "").strip() if hasattr(self, "free_voice_combo") else ""
        if free_value and free_value.startswith("edge:"):
            return free_value
        if free_value and free_value in getattr(self, "voice_catalog_map", {}):
            return free_value
        if "vi_VN-vais1000-medium" in getattr(self, "voice_catalog_map", {}):
            return "vi_VN-vais1000-medium"
        if hasattr(self, "free_voice_combo") and self.free_voice_combo.count() > 0:
            fallback_value = str(self.free_voice_combo.itemData(0) or "").strip()
            if fallback_value:
                return fallback_value
            fallback_entry_id = str(self.free_voice_combo.itemData(0, self.VOICE_ENTRY_ID_ROLE) or "").strip()
            if fallback_entry_id:
                return fallback_entry_id
        return "vi_VN-vais1000-medium"

    def on_voice_tier_changed(self):
        mode = self.get_output_mode_key() if hasattr(self, "output_mode_combo") else "both"
        if hasattr(self, "free_voice_combo"):
            self.free_voice_combo.setEnabled(True)
        if hasattr(self, "preview_voice_btn"):
            self.preview_voice_btn.setVisible(mode in ("voice", "both"))
        self._update_voice_preview_meta()

    def on_audio_mix_preset_changed(self):
        if not hasattr(self, "audio_mix_preset_combo"):
            return
        preset_key = str(self.audio_mix_preset_combo.currentData() or "custom").strip().lower()
        presets = {
            "voice_focus": {"bg_gain": -1.0, "ducking": -8.0},
            "balanced": {"bg_gain": 1.0, "ducking": -5.0},
            "music_forward": {"bg_gain": 3.0, "ducking": -3.0},
        }
        if preset_key in presets:
            values = presets[preset_key]
            if hasattr(self, "bg_gain_spin"):
                self.bg_gain_spin.setValue(float(values["bg_gain"]))
            if hasattr(self, "ducking_amount_spin"):
                self.ducking_amount_spin.setValue(float(values["ducking"]))
        self.refresh_ui_state()

    def _parse_voice_speed_value(self) -> float:
        raw = str(getattr(self, "voice_speed_spin", None).currentText() if getattr(self, "voice_speed_spin", None) else "1.0x").strip().lower()
        raw = raw.replace("x", "")
        try:
            return float(raw or "1.0")
        except ValueError:
            return 1.0

    # -----------------------------
    # Logging + error helpers
    # -----------------------------
    def log(self, message: str):
        log_message_impl(self, message)

    def clear_log(self):
        clear_log_impl(self)

    def _register_progress_dialog(self, dialog):
        if dialog is None:
            return
        self._tracked_progress_dialogs = [d for d in self._tracked_progress_dialogs if d is not None]
        if dialog not in self._tracked_progress_dialogs:
            self._tracked_progress_dialogs.append(dialog)
            try:
                dialog.destroyed.connect(lambda *_args, dlg=dialog: self._unregister_progress_dialog(dlg))
            except Exception:
                pass
        self._update_progress_reopen_button()

    def _unregister_progress_dialog(self, dialog):
        self._tracked_progress_dialogs = [d for d in self._tracked_progress_dialogs if d is not dialog]
        self._update_progress_reopen_button()

    def _active_progress_dialogs(self):
        active = []
        for dialog in list(getattr(self, "_tracked_progress_dialogs", []) or []):
            if dialog is None:
                continue
            try:
                if dialog.isVisible():
                    active.append(dialog)
                    continue
                if getattr(dialog, "isHidden", None) and not dialog.isHidden():
                    active.append(dialog)
            except Exception:
                continue
        return active

    def _update_progress_reopen_button(self):
        button = getattr(self, "show_progress_btn", None)
        if button is None:
            return
        tracked = [d for d in getattr(self, "_tracked_progress_dialogs", []) if d is not None]
        button.setVisible(bool(tracked))
        button.setEnabled(bool(tracked))

    def show_active_progress_dialog(self):
        dialogs = [d for d in getattr(self, "_tracked_progress_dialogs", []) if d is not None]
        if not dialogs:
            self._update_progress_reopen_button()
            return
        dialog = dialogs[-1]
        try:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        except Exception:
            pass
        self._update_progress_reopen_button()

    def _resource_service(self) -> ResourceDownloadService:
        return ResourceDownloadService(self.workspace_root)

    def _refresh_resource_manager_dialog(self, dialog):
        rows = getattr(dialog, "_resource_rows", {})
        if not rows:
            return
        resources = {item["id"]: item for item in self._resource_service().list_resources()}
        state = dict(getattr(self, "_resource_download_state", {}) or {})
        active_resource_id = str(state.get("resource_id", "") or "")
        worker_running = bool(state.get("running"))
        for resource_id, row in rows.items():
            item = resources.get(resource_id, row.get("item", {}))
            row["item"] = item
            status = str(item.get("status", "missing")).strip().lower()
            target_dir = str(item.get("target_dir", "")).strip()
            description = str(item.get("description", "")).strip()
            status_label = row.get("status_label")
            if status_label is not None:
                lines = [status.title()]
                if description:
                    lines.append(description)
                if target_dir:
                    lines.append(target_dir)
                status_label.setText("\n".join(lines))
            button = row.get("button")
            if button is not None:
                if worker_running and resource_id == active_resource_id:
                    button.setText("Downloading...")
                    button.setEnabled(False)
                elif status == "installed":
                    button.setText("Installed")
                    button.setEnabled(False)
                elif resource_id == "voice:pack" and status == "partial":
                    button.setText("Complete Pack")
                    button.setEnabled(not worker_running)
                else:
                    button.setText("Download")
                    button.setEnabled(not worker_running)

        footer_text = str(state.get("message", "") or "").strip() or "Select a resource to download."
        if hasattr(dialog, "_resource_footer"):
            dialog._resource_footer.setText(footer_text)
        if hasattr(dialog, "_resource_progress_bar"):
            try:
                value = int(state.get("percent", 0))
            except Exception:
                value = 0
            if worker_running and value < 0:
                dialog._resource_progress_bar.setRange(0, 0)
            else:
                dialog._resource_progress_bar.setRange(0, 100)
                dialog._resource_progress_bar.setValue(max(0, min(100, value)))

    def _on_resource_download_progress(self, percent: int, message: str):
        try:
            value = int(percent)
        except Exception:
            value = -1
        self._resource_download_state = {
            "resource_id": str(getattr(self, "_resource_download_resource_id", "") or ""),
            "percent": value,
            "message": str(message or "").strip() or "Downloading resource...",
            "running": True,
        }
        dialog = getattr(self, "_resource_download_dialog", None)
        if dialog is not None and hasattr(dialog, "_resource_footer"):
            dialog._resource_footer.setText(str(message or "").strip() or "Downloading resource...")
        if dialog is not None and hasattr(dialog, "_resource_progress_bar"):
            if value < 0:
                dialog._resource_progress_bar.setRange(0, 0)
            else:
                if dialog._resource_progress_bar.maximum() == 0:
                    dialog._resource_progress_bar.setRange(0, 100)
                dialog._resource_progress_bar.setValue(max(0, min(100, value)))

    def _on_resource_download_finished(self, resource_id: str, error: str):
        worker = getattr(self, "resource_download_worker", None)
        self.resource_download_worker = None
        self._resource_download_resource_id = ""
        self._resource_download_state = {
            "resource_id": "",
            "percent": 0 if error else 100,
            "message": "Download failed." if error else "Download completed.",
            "running": False,
        }
        dialog = getattr(self, "_resource_download_dialog", None)
        if dialog is not None and hasattr(dialog, "_resource_footer"):
            dialog._resource_footer.setText("Download failed." if error else "Download completed.")
            self._refresh_resource_manager_dialog(dialog)
        if dialog is not None and hasattr(dialog, "_resource_progress_bar"):
            dialog._resource_progress_bar.setRange(0, 100)
            dialog._resource_progress_bar.setValue(100 if not error else 0)
        if not error:
            try:
                self.load_voice_preview_catalog()
            except Exception:
                pass
            self.refresh_ui_state()
            return
        self.show_error("Download Failed", f"Could not download resource '{resource_id}'.", error)

    def _start_resource_download(self, dialog, resource_id: str):
        worker = getattr(self, "resource_download_worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(self, "Download in Progress", "A resource is already downloading.")
            return
        self._resource_download_dialog = dialog
        self._resource_download_resource_id = str(resource_id or "").strip()
        self._resource_download_state = {
            "resource_id": self._resource_download_resource_id,
            "percent": 0,
            "message": f"Preparing download: {resource_id}",
            "running": True,
        }
        if hasattr(dialog, "_resource_footer"):
            dialog._resource_footer.setText(f"Preparing download: {resource_id}")
        if hasattr(dialog, "_resource_progress_bar"):
            dialog._resource_progress_bar.setRange(0, 100)
            dialog._resource_progress_bar.setValue(0)
        worker = ResourceDownloadWorker(self.workspace_root, resource_id)
        worker.progress.connect(self._on_resource_download_progress)
        worker.finished.connect(self._on_resource_download_finished)
        self.resource_download_worker = worker
        self._refresh_resource_manager_dialog(dialog)
        worker.start()

    def _missing_resource_entries(self, *, include_whisper: bool = False, include_voice: bool = False) -> list[tuple[str, str]]:
        service = self._resource_service()
        missing: list[tuple[str, str]] = []

        if include_whisper:
            model_name = self.get_whisper_model_name()
            resource_id = f"whisper:{model_name}"
            if not service.is_resource_installed(resource_id):
                missing.append((resource_id, f"Whisper {model_name.title()} model"))

        if include_voice:
            voice_name = self.get_active_voice_name()
            if voice_name and not str(voice_name).startswith("edge:"):
                resource_id = f"voice:{voice_name}"
                if not service.is_resource_installed(resource_id):
                    voice_label = voice_name
                    voice_entry = self.voice_catalog_map.get(voice_name) if hasattr(self, "voice_catalog_map") else None
                    if isinstance(voice_entry, dict):
                        voice_label = str(voice_entry.get("name", voice_name)).strip() or voice_name
                    missing.append((resource_id, f"Local voice: {voice_label}"))

        deduped: list[tuple[str, str]] = []
        seen = set()
        for item in missing:
            if item[0] in seen:
                continue
            seen.add(item[0])
            deduped.append(item)
        return deduped

    def ensure_required_resources(self, action_label: str, *, include_whisper: bool = False, include_voice: bool = False) -> bool:
        missing = self._missing_resource_entries(include_whisper=include_whisper, include_voice=include_voice)
        if not missing:
            return True

        missing_lines = "\n".join(f"- {label}" for _resource_id, label in missing)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Missing Resources")
        box.setText(f"{action_label} cannot start because some required resources are missing.")
        box.setInformativeText(
            "Open Manage Resources and download the missing items:\n\n"
            f"{missing_lines}"
        )
        open_btn = box.addButton("Manage Resources", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self.open_resource_manager_dialog()
        return False

    def open_resource_manager_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage Resources")
        dialog.setModal(True)
        dialog.resize(760, 620)
        dialog.setStyleSheet(
            """
            QDialog { background-color: #0f1724; }
            QLabel { color: #d7e3f4; background-color: transparent; }
            QLabel#resourceTitle { color: #f8fbff; font-size: 16px; font-weight: 700; }
            QLabel#resourceHint { color: #9fb3ca; font-size: 12px; }
            QWidget#resourceContent { background-color: transparent; }
            QScrollArea { border: none; background-color: #0f1724; }
            QFrame#resourceCard { background-color: #132033; border: 1px solid #2f4868; border-radius: 12px; }
            QPushButton {
                background-color: #22344d; color: #f8fbff; border: 1px solid #34506f;
                border-radius: 10px; padding: 8px 16px; font-weight: 600; min-width: 84px;
            }
            QPushButton:hover { background-color: #29405d; }
            QPushButton:disabled { color: #8ea3bb; background-color: #182636; border-color: #29405d; }
            """
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("Download runtime resources from Hugging Face", dialog)
        title.setObjectName("resourceTitle")
        layout.addWidget(title)

        hint = QLabel(
            f"Whisper models use faster-whisper download/cache. Extra runtime files come from: {self._resource_service().repo_id} @ {self._resource_service().revision}\n"
            "Use this screen to install Whisper, GPU runtime, and local Piper voices separately.",
            dialog,
        )
        hint.setObjectName("resourceHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(scroll, 1)

        content = QWidget(dialog)
        content.setObjectName("resourceContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        scroll.setWidget(content)

        dialog._resource_rows = {}
        groups = [
            ("AI Models", "ai"),
            ("Whisper Models", "whisper"),
            ("GPU Runtime", "cuda"),
            ("Local Voices", "voice"),
        ]
        resources = self._resource_service().list_resources()
        for group_title, group_kind in groups:
            items = [item for item in resources if item.get("kind") == group_kind]
            if not items:
                continue
            group_label = QLabel(group_title, dialog)
            group_label.setObjectName("resourceTitle")
            group_label.setStyleSheet("color: #f8fbff; background-color: transparent;")
            content_layout.addWidget(group_label)
            for item in items:
                card = QFrame(dialog)
                card.setObjectName("resourceCard")
                card_layout = QHBoxLayout(card)
                card_layout.setContentsMargins(12, 12, 12, 12)
                card_layout.setSpacing(12)

                text_layout = QVBoxLayout()
                name_label = QLabel(str(item.get("name", item.get("id", "Resource"))), dialog)
                name_label.setStyleSheet("color: #f8fbff; font-weight: 700; background-color: transparent;")
                status_label = QLabel("", dialog)
                status_label.setObjectName("resourceHint")
                status_label.setStyleSheet("color: #9fb3ca; background-color: transparent;")
                status_label.setWordWrap(True)
                text_layout.addWidget(name_label)
                text_layout.addWidget(status_label)
                card_layout.addLayout(text_layout, 1)

                button = QPushButton("Download", dialog)
                button.clicked.connect(lambda _checked=False, rid=item["id"], dlg=dialog: self._start_resource_download(dlg, rid))
                card_layout.addWidget(button)

                content_layout.addWidget(card)
                dialog._resource_rows[item["id"]] = {
                    "item": item,
                    "status_label": status_label,
                    "button": button,
                }

        dialog._resource_footer = QLabel("Select a resource to download.", dialog)
        dialog._resource_footer.setObjectName("resourceHint")
        dialog._resource_footer.setWordWrap(True)
        layout.addWidget(dialog._resource_footer)

        dialog._resource_progress_bar = QProgressBar(dialog)
        dialog._resource_progress_bar.setRange(0, 100)
        dialog._resource_progress_bar.setValue(0)
        dialog._resource_progress_bar.setTextVisible(True)
        layout.addWidget(dialog._resource_progress_bar)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close", dialog)
        close_btn.clicked.connect(dialog.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._refresh_resource_manager_dialog(dialog)
        dialog.exec()

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
        mixed_path = self._normalize_local_file_path(
            self.mixed_audio_edit.text().strip() if hasattr(self, "mixed_audio_edit") else ""
        )
        use_existing = bool(hasattr(self, "use_existing_audio_radio") and self.use_existing_audio_radio.isChecked())
        return bool(use_existing and mixed_path and os.path.exists(mixed_path))

    def _normalize_local_file_path(self, path: str) -> str:
        value = str(path or "").replace("\r", "").replace("\n", "").replace("\t", " ").strip().strip('"').strip("'")
        if not value:
            return ""

        value = os.path.expandvars(os.path.expanduser(value))
        candidates = []
        if os.path.isabs(value):
            candidates.append(value)
        else:
            candidates.append(os.path.join(self.workspace_root, value))
            current_project = getattr(self, "current_project_state", None)
            if current_project and getattr(current_project, "project_root", ""):
                candidates.append(os.path.join(current_project.project_root, value))
            candidates.append(os.path.join(self.workspace_root, value))

        for candidate in candidates:
            normalized = os.path.normpath(os.path.abspath(candidate))
            if os.path.exists(normalized):
                return normalized

        fallback = candidates[0] if candidates else value
        return os.path.normpath(os.path.abspath(fallback))

    def resolve_selected_audio_path(self) -> str:
        if self.using_existing_audio_source():
            return self._normalize_local_file_path(self.mixed_audio_edit.text().strip())
        candidates = [
            self.processed_artifacts.get("mixed_vi"),
            self.last_mixed_vi_path,
            self.last_voice_vi_path,
        ]
        for candidate in candidates:
            normalized = self._normalize_local_file_path(candidate)
            if normalized and os.path.exists(normalized):
                return normalized
        return ""

    def resolve_timeline_audio_visualization_path(self) -> str:
        selected_audio = self.resolve_selected_audio_path()
        if selected_audio and os.path.exists(selected_audio):
            return selected_audio

        candidates = [
            self.audio_source_edit.text().strip() if hasattr(self, "audio_source_edit") else "",
            self.processed_artifacts.get("vocals"),
            self.processed_artifacts.get("audio_extracted"),
            self.last_vocals_path,
            self.last_extracted_audio,
        ]
        for candidate in candidates:
            normalized = self._normalize_local_file_path(candidate)
            if normalized and os.path.exists(normalized):
                return normalized
        return ""

    def refresh_timeline_waveform(self):
        if not hasattr(self, "timeline"):
            return
        audio_path = self.resolve_timeline_audio_visualization_path()
        if not audio_path or not os.path.exists(audio_path):
            self._timeline_waveform_cache_key = None
            self._timeline_waveform_samples = []
            self._timeline_waveform_duration_s = 0.0
            self.timeline.set_waveform_data([], 0.0)
            return

        try:
            stat = os.stat(audio_path)
            cache_key = (
                os.path.abspath(audio_path),
                int(stat.st_size),
                int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
            )
        except Exception:
            cache_key = (os.path.abspath(audio_path), 0, 0)

        if cache_key != self._timeline_waveform_cache_key:
            try:
                from audio_mixer import _require_pydub
                _require_pydub()
                from pydub import AudioSegment
                import numpy as np

                audio = AudioSegment.from_file(audio_path).set_channels(1)
                samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
                duration_s = max(0.0, len(audio) / 1000.0)
                if samples.size:
                    sample_rate = max(8000, int(audio.frame_rate or 16000))
                    samples = samples.astype(np.float32)
                    samples /= max(1.0, float(np.max(np.abs(samples))) or 1.0)
                    bucket_count = int(min(2400, max(600, duration_s * 40)))
                    chunk_size = max(256, int(np.ceil(samples.size / max(1, bucket_count))))
                    band_count = 8
                    spectrum = []
                    for start in range(0, samples.size, chunk_size):
                        chunk = samples[start:start + chunk_size]
                        if chunk.size < 32:
                            spectrum.append([0.0] * band_count)
                            continue
                        if chunk.size < chunk_size:
                            chunk = np.pad(chunk, (0, chunk_size - chunk.size))
                        window = np.hanning(chunk.size)
                        fft = np.fft.rfft(chunk * window)
                        magnitudes = np.abs(fft)
                        if magnitudes.size <= 1:
                            spectrum.append([0.0] * band_count)
                            continue
                        freqs = np.fft.rfftfreq(chunk.size, d=1.0 / sample_rate)
                        band_edges = np.geomspace(40.0, min(8000.0, sample_rate / 2.0), num=band_count + 1)
                        band_values = []
                        for band_idx in range(band_count):
                            mask = (freqs >= band_edges[band_idx]) & (freqs < band_edges[band_idx + 1])
                            band_mag = magnitudes[mask]
                            if band_mag.size:
                                band_values.append(float(np.mean(band_mag)))
                            else:
                                band_values.append(0.0)
                        spectrum.append(band_values)

                    if spectrum:
                        band_max = [max(column[idx] for column in spectrum) or 1.0 for idx in range(len(spectrum[0]))]
                        waveform = []
                        for column in spectrum:
                            waveform.append(
                                [
                                    min(1.0, (float(value) / float(band_max[idx])) ** 0.75)
                                    for idx, value in enumerate(column)
                                ]
                            )
                    else:
                        waveform = []
                else:
                    waveform = []
                    duration_s = 0.0
                self._timeline_waveform_cache_key = cache_key
                self._timeline_waveform_samples = waveform
                self._timeline_waveform_duration_s = duration_s
            except Exception:
                self._timeline_waveform_cache_key = cache_key
                self._timeline_waveform_samples = []
                self._timeline_waveform_duration_s = 0.0

        self.timeline.set_waveform_data(self._timeline_waveform_samples, self._timeline_waveform_duration_s)

    def schedule_timeline_visual_refresh(self, *, waveform: bool = True, thumbnails: bool = True, delay_ms: int = 40):
        if waveform:
            self._pending_timeline_waveform_refresh = True
        if thumbnails:
            self._pending_timeline_thumbnail_refresh = True
        timer = getattr(self, "_timeline_visual_refresh_timer", None)
        if timer is None:
            self._run_pending_timeline_visual_refresh()
            return
        timer.start(max(0, int(delay_ms)))

    def _run_pending_timeline_visual_refresh(self):
        refresh_waveform = bool(getattr(self, "_pending_timeline_waveform_refresh", False))
        refresh_thumbnails = bool(getattr(self, "_pending_timeline_thumbnail_refresh", False))
        self._pending_timeline_waveform_refresh = False
        self._pending_timeline_thumbnail_refresh = False
        if refresh_waveform:
            self.refresh_timeline_waveform()
        if refresh_thumbnails:
            self.refresh_timeline_video_thumbnails()

    def refresh_timeline_video_thumbnails(self):
        if not hasattr(self, "timeline"):
            return

        video_path = self._normalize_local_file_path(self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else "")
        duration_s = max(0.0, float(getattr(self.timeline, "duration", 0) or 0) / 1000.0)
        if not video_path or not os.path.exists(video_path) or duration_s <= 0.0:
            self._timeline_video_thumb_cache_key = None
            self._timeline_video_thumbnails = []
            self.timeline.set_video_thumbnails([])
            return

        try:
            stat = os.stat(video_path)
            cache_key = (
                os.path.abspath(video_path),
                int(stat.st_size),
                int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
                int(round(duration_s)),
            )
        except Exception:
            cache_key = (os.path.abspath(video_path), 0, 0, int(round(duration_s)))

        if cache_key != self._timeline_video_thumb_cache_key:
            thumbnails = []
            ffmpeg_candidates = [
                bin_path("ffmpeg", "ffmpeg.exe"),
                bin_path("ffmpeg.exe"),
                shutil.which("ffmpeg"),
                shutil.which("ffmpeg.exe"),
            ]
            ffmpeg_path = ""
            for candidate in ffmpeg_candidates:
                if candidate and os.path.isfile(candidate):
                    ffmpeg_path = candidate
                    break

            if ffmpeg_path and os.path.isfile(ffmpeg_path):
                thumb_count = max(4, min(10, int(round(duration_s / 3.0)) or 6))
                if duration_s <= 1.0:
                    timestamps = [0.0]
                else:
                    timestamps = [
                        min(duration_s - 0.05, max(0.0, ((idx + 0.5) * duration_s) / thumb_count))
                        for idx in range(thumb_count)
                    ]

                thumb_dir = self.get_project_temp_dir("timeline_video_thumbs")
                os.makedirs(thumb_dir, exist_ok=True)
                digest = hashlib.md5(f"{video_path}|{cache_key[1]}|{cache_key[2]}|{cache_key[3]}".encode("utf-8")).hexdigest()[:16]

                startupinfo = None
                creationflags = 0
                if os.name == "nt":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

                for idx, timestamp_s in enumerate(timestamps):
                    output_path = os.path.join(thumb_dir, f"{digest}_{idx:02d}.jpg")
                    if not os.path.exists(output_path):
                        cmd = [
                            ffmpeg_path,
                            "-y",
                            "-ss",
                            f"{timestamp_s:.3f}",
                            "-i",
                            video_path,
                            "-frames:v",
                            "1",
                            "-q:v",
                            "4",
                            "-vf",
                            "scale=180:-1:force_original_aspect_ratio=decrease",
                            output_path,
                        ]
                        try:
                            subprocess.run(
                                cmd,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                                timeout=20,
                                startupinfo=startupinfo,
                                creationflags=creationflags,
                            )
                        except Exception:
                            continue
                    pixmap = QPixmap(output_path)
                    if not pixmap.isNull():
                        thumbnails.append((float(timestamp_s), pixmap))

            self._timeline_video_thumb_cache_key = cache_key
            self._timeline_video_thumbnails = thumbnails

        self.timeline.set_video_thumbnails(self._timeline_video_thumbnails)

    def on_audio_source_mode_changed(self):
        if not hasattr(self, "audio_source_hint_label"):
            return
        using_existing = bool(hasattr(self, "use_existing_audio_radio") and self.use_existing_audio_radio.isChecked())
        if using_existing:
            self.audio_source_hint_label.setText(
                "Preview and export will use the file in 'Existing mixed audio'. Generated voice and background settings are ignored until you switch back."
            )
        else:
            self.audio_source_hint_label.setText(
                "Preview and export will use the audio generated by CapCap, including the background mix when available. Existing mixed audio is ignored until you switch to it."
            )
        generated_widgets = [
            "generated_audio_section_label",
            "generated_audio_section_hint",
            "bg_music_label",
            "bg_music_edit",
            "browse_bg_music_btn",
            "voice_gain_label",
            "voice_gain_spin",
            "bg_gain_label",
            "bg_gain_spin",
            "voiceover_btn",
        ]
        existing_widgets = [
            "existing_audio_section_label",
            "existing_audio_section_hint",
            "mixed_audio_label",
            "mixed_audio_edit",
            "browse_mixed_audio_btn",
        ]
        for name in generated_widgets:
            widget = getattr(self, name, None)
            if widget:
                widget.setEnabled(not using_existing)
        for name in existing_widgets:
            widget = getattr(self, name, None)
            if widget:
                widget.setEnabled(using_existing)
        self.schedule_timeline_visual_refresh(waveform=True, thumbnails=False)
        self.refresh_ui_state()

    def on_advanced_toggled(self, checked: bool):
        if hasattr(self, "tabs"):
            self.tabs.setVisible(True)
        if hasattr(self, "workflow_advanced_layout"):
            checked = True
        if hasattr(self, "toggle_advanced_btn"):
            self.toggle_advanced_btn.setText(("▼ " if checked else "▶ ") + "Advanced Settings")
        if hasattr(self, "advanced_section_content"):
            self.advanced_section_content.setVisible(bool(checked))

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

    def schedule_live_video_filter_preview(self):
        if not hasattr(self, "video_filter_preview_timer"):
            return
        self._pending_video_filter_preview = True
        if getattr(self, "_styled_preview_running", False):
            return
        self.video_filter_preview_timer.start()

    def _is_video_filter_slider_interacting(self):
        sliders = [getattr(self, "video_filter_intensity_slider", None)]
        sliders.extend(list(getattr(self, "video_filter_adjust_sliders", {}).values()))
        for slider in sliders:
            if slider is not None and slider.isSliderDown():
                return True
        return False

    def on_video_filter_slider_released(self):
        self.schedule_live_video_filter_preview()

    def is_filter_workflow_active(self) -> bool:
        stack = getattr(self, "left_panel_stack", None)
        if stack is None:
            return False
        try:
            return int(stack.currentIndex()) == 4
        except Exception:
            return False

    def _mark_video_filter_preview_dirty(self):
        self._video_filter_preview_dirty = self.has_active_video_filters()
        self._video_filter_apply_requested = False
        self.refresh_ui_state()

    def apply_current_video_filter(self):
        if not self.has_active_video_filters():
            self._video_filter_preview_dirty = False
            self._video_filter_apply_requested = False
            self.hide_filter_thumbnail_preview()
            self.refresh_ui_state()
            return
        self._video_filter_apply_requested = True
        self.refresh_ui_state()
        self.preview_controller.preview_video()

    def revert_video_filter_preview_to_source(self):
        video_path = self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        if not video_path or not os.path.exists(video_path):
            return
        self._play_video_filter_preview_when_ready = False
        self.hide_filter_thumbnail_preview()
        try:
            current_position = int(self.media_player.position())
        except Exception:
            current_position = 0
        try:
            self.media_player.pause()
        except Exception:
            pass
        try:
            self.media_player.setSource(QUrl.fromLocalFile(video_path))
            if current_position > 0:
                self.media_player.setPosition(current_position)
        except Exception:
            pass
        self.refresh_video_dimensions(video_path)
        self._preview_video_has_burned_subtitles = False
        self.sync_live_subtitle_preview()
        if hasattr(self, "timeline"):
            self.timeline.set_playing(False)
        if hasattr(self, "_refresh_preview_audio_controls"):
            self._refresh_preview_audio_controls()
        self.refresh_ui_state()

    def _can_auto_render_filter_preview(self):
        video_path = self.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            return False
        if getattr(self, "_styled_preview_running", False) or getattr(self, "_pipeline_active", False):
            return False
        if self.has_active_video_filters():
            return True
        mode = self.get_output_mode_key()
        if mode == "subtitle":
            return bool(self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path))
        if mode == "voice":
            audio_path = self.resolve_selected_audio_path()
            return bool(audio_path and os.path.exists(audio_path))
        if mode == "both":
            audio_path = self.resolve_selected_audio_path()
            return bool(
                audio_path
                and os.path.exists(audio_path)
                and self.last_translated_srt_path
                and os.path.exists(self.last_translated_srt_path)
            )
        return False

    def run_live_video_filter_preview(self):
        if getattr(self, "_styled_preview_running", False) or getattr(self, "_frame_preview_running", False):
            return
        if not getattr(self, "_pending_video_filter_preview", False):
            return
        if not self.has_active_video_filters():
            self._pending_video_filter_preview = False
            self.hide_filter_thumbnail_preview()
            return
        if not self._can_auto_render_filter_preview():
            self._pending_video_filter_preview = False
            return
        self._pending_video_filter_preview = False
        try:
            self.preview_controller.start_exact_frame_preview(show_dialog=False)
        except Exception as exc:
            self.log(f"[Filter Preview] skipped: {exc}")

    def save_user_settings(self):
        save_user_settings_impl(self)
        try:
            self.settings.setValue("premium_voice_name", "")
            self.settings.setValue("premium_voice_value", "")
            self.settings.setValue("voice_tier", "free")
        except Exception:
            pass

    def load_user_settings(self):
        load_user_settings_impl(self)
        if hasattr(self, "use_premium_voice_radio"):
            try:
                self.use_premium_voice_radio.setChecked(False)
            except Exception:
                pass
        if hasattr(self, "use_free_voice_radio"):
            try:
                self.use_free_voice_radio.setChecked(True)
            except Exception:
                pass

    def ensure_local_translator_auto_configured(self):
        provider = str(os.getenv("AI_POLISHER_PROVIDER") or "").strip().lower()
        if provider != "local":
            return

        managed_keys = [
            "LOCAL_TRANSLATOR_N_CTX",
            "LOCAL_TRANSLATOR_N_THREADS",
            "LOCAL_TRANSLATOR_N_THREADS_BATCH",
            "LOCAL_TRANSLATOR_N_BATCH",
            "LOCAL_TRANSLATOR_N_UBATCH",
            "LOCAL_TRANSLATOR_GPU_LAYERS",
            "LOCAL_TRANSLATOR_FLASH_ATTN",
        ]
        if all(str(os.getenv(key) or "").strip() for key in managed_keys):
            return

        LocalPolisherProvider = self._local_polisher_provider_cls()
        hardware_info = LocalPolisherProvider.detect_runtime_capabilities()
        recommended = LocalPolisherProvider.recommended_runtime_config(hardware_info)
        updates = {
            "LOCAL_TRANSLATOR_N_CTX": str(recommended["n_ctx"]),
            "LOCAL_TRANSLATOR_N_THREADS": str(recommended["n_threads"]),
            "LOCAL_TRANSLATOR_N_THREADS_BATCH": str(recommended["n_threads_batch"]),
            "LOCAL_TRANSLATOR_N_BATCH": str(recommended["n_batch"]),
            "LOCAL_TRANSLATOR_N_UBATCH": str(recommended["n_ubatch"]),
            "LOCAL_TRANSLATOR_GPU_LAYERS": str(recommended["gpu_layers"]),
            "LOCAL_TRANSLATOR_FLASH_ATTN": "true" if recommended["flash_attn"] else "false",
        }

        env_lines = []
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as handle:
                env_lines = handle.readlines()

        new_env_lines = []
        handled_keys = set()
        for line in env_lines:
            match = re.match(r"^([^=]+)=.*", line)
            if match:
                key = match.group(1).strip()
                if key in updates:
                    new_env_lines.append(f"{key}={updates[key]}\n")
                    handled_keys.add(key)
                    continue
            new_env_lines.append(line)

        for key, value in updates.items():
            if key not in handled_keys:
                new_env_lines.append(f"{key}={value}\n")

        with open(".env", "w", encoding="utf-8") as handle:
            handle.writelines(new_env_lines)

        for key, value in updates.items():
            os.environ[key] = value

        if hasattr(self, "log"):
            self.log(f"[Local AI] Auto-optimized for this machine: {LocalPolisherProvider.runtime_status_summary(hardware_info)}")

    @staticmethod
    def _local_polisher_provider_cls():
        from translation.providers.local_polisher import LocalPolisherProvider

        return LocalPolisherProvider

    @staticmethod
    def _preload_tts_voice_impl(voice_name: str):
        from tts_processor import preload_tts_voice

        return preload_tts_voice(voice_name)

    @staticmethod
    def _test_remote_api_connection(base_url: str, token: str) -> dict:
        previous_url = os.environ.get("CAPCAP_REMOTE_API_URL", "")
        previous_token = os.environ.get("CAPCAP_REMOTE_API_TOKEN", "")
        try:
            os.environ["CAPCAP_REMOTE_API_URL"] = (base_url or "").strip()
            if token:
                os.environ["CAPCAP_REMOTE_API_TOKEN"] = token.strip()
            else:
                os.environ.pop("CAPCAP_REMOTE_API_TOKEN", None)
            from remote_api import remote_api_get

            return remote_api_get("/health", timeout=10)
        finally:
            if previous_url:
                os.environ["CAPCAP_REMOTE_API_URL"] = previous_url
            else:
                os.environ.pop("CAPCAP_REMOTE_API_URL", None)
            if previous_token:
                os.environ["CAPCAP_REMOTE_API_TOKEN"] = previous_token
            else:
                os.environ.pop("CAPCAP_REMOTE_API_TOKEN", None)

    def _highlight_color_hex(self) -> str:
        mapping = {
            "Yellow": "#FFD400",
            "Cyan": "#00E5FF",
            "Green": "#5CFF95",
            "Pink": "#FF6BD6",
        }
        return mapping.get(self.subtitle_highlight_color_combo.currentText().strip(), "#FFD400")

    def is_custom_subtitle_position_mode(self) -> bool:
        if not hasattr(self, "subtitle_position_mode_combo"):
            return False
        return str(self.subtitle_position_mode_combo.currentData() or "anchor").strip().lower() == "custom"

    def on_subtitle_position_mode_changed(self, *_args):
        is_custom = self.is_custom_subtitle_position_mode()
        if hasattr(self, "subtitle_align_label"):
            self.subtitle_align_label.setVisible(not is_custom)
        if hasattr(self, "subtitle_align_combo"):
            self.subtitle_align_combo.setVisible(not is_custom)
        if hasattr(self, "subtitle_custom_x_label"):
            self.subtitle_custom_x_label.setVisible(is_custom)
        if hasattr(self, "subtitle_custom_x_spin"):
            self.subtitle_custom_x_spin.setVisible(is_custom)
        if hasattr(self, "subtitle_custom_y_label"):
            self.subtitle_custom_y_label.setVisible(is_custom)
        if hasattr(self, "subtitle_custom_y_spin"):
            self.subtitle_custom_y_spin.setVisible(is_custom)
        self.update_subtitle_preview_style()

    def _saved_subtitle_style_payload(self) -> dict:
        return {
            "preset": self.get_selected_subtitle_preset(),
            "font": self.subtitle_font_combo.currentText().strip(),
            "size": int(self.subtitle_font_size_spin.value()),
            "color": self.subtitle_color_hex,
            "background_color": getattr(self, "subtitle_background_color_hex", "#000000"),
            "position_mode": str(self.subtitle_position_mode_combo.currentData() or "anchor"),
            "position": self.subtitle_align_combo.currentText().strip(),
            "custom_x": int(self.subtitle_custom_x_spin.value()),
            "custom_y": int(self.subtitle_custom_y_spin.value()),
            "animation": self.subtitle_animation_combo.currentText().strip(),
            "animation_time": float(self.subtitle_animation_time_spin.value()),
            "karaoke_timing_mode": str(self.subtitle_karaoke_timing_combo.currentData() or "vietnamese"),
            "background": bool(self.subtitle_background_cb.isChecked()),
            "outline": bool(getattr(self, "subtitle_outline_cb", None) and self.subtitle_outline_cb.isChecked()),
            "background_alpha": float(self.subtitle_bg_alpha_spin.value()) if hasattr(self, "subtitle_bg_alpha_spin") else 0.6,
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
        self.subtitle_background_color_hex = str(preset.get("background_color", getattr(self, "subtitle_background_color_hex", "#000000"))).upper()
        if hasattr(self, "subtitle_background_color_btn"):
            self.subtitle_background_color_btn.setText(self.subtitle_background_color_hex)
        position_mode = str(preset.get("position_mode", self.subtitle_position_mode_combo.currentData() or "anchor")).strip().lower()
        position_mode_index = self.subtitle_position_mode_combo.findData(position_mode)
        if position_mode_index >= 0:
            self.subtitle_position_mode_combo.setCurrentIndex(position_mode_index)
        self.subtitle_align_combo.setCurrentText(str(preset.get("position", self.subtitle_align_combo.currentText())))
        self.subtitle_custom_x_spin.setValue(int(preset.get("custom_x", self.subtitle_custom_x_spin.value())))
        self.subtitle_custom_y_spin.setValue(int(preset.get("custom_y", self.subtitle_custom_y_spin.value())))
        self.subtitle_animation_combo.setCurrentText(str(preset.get("animation", self.subtitle_animation_combo.currentText())))
        self.subtitle_animation_time_spin.setValue(float(preset.get("animation_time", self.subtitle_animation_time_spin.value())))
        karaoke_mode = str(preset.get("karaoke_timing_mode", self.subtitle_karaoke_timing_combo.currentData() or "vietnamese"))
        karaoke_index = self.subtitle_karaoke_timing_combo.findData(karaoke_mode)
        if karaoke_index >= 0:
            self.subtitle_karaoke_timing_combo.setCurrentIndex(karaoke_index)
        self.subtitle_background_cb.setChecked(bool(preset.get("background", self.subtitle_background_cb.isChecked())))
        if hasattr(self, "subtitle_outline_cb"):
            self.subtitle_outline_cb.setChecked(bool(preset.get("outline", self.subtitle_outline_cb.isChecked())))
        if hasattr(self, "subtitle_bg_alpha_spin"):
            self.subtitle_bg_alpha_spin.setValue(float(preset.get("background_alpha", self.subtitle_bg_alpha_spin.value())))
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
        audio_handling_mode = self.get_audio_handling_mode()
        if str(state.settings.get("audio_handling_mode", "fast")).strip().lower() != audio_handling_mode:
            state.set_setting("audio_handling_mode", audio_handling_mode)
            self.project_service.save_project(state)
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
        normalized_path = self._normalize_local_file_path(path)
        self.processed_artifacts[artifact_name] = normalized_path
        self.project_bridge.update_artifact(state, artifact_name, normalized_path)

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
        signature = self.build_current_translation_signature()
        if signature:
            state.set_setting("translation_signature", signature)
            self.project_service.save_project(state)

    def build_current_translation_signature(self, source_segments=None):
        base_segments = list(source_segments or self.current_segments or [])
        if not base_segments:
            transcript_text = self.transcript_text.toPlainText().strip() if hasattr(self, "transcript_text") else ""
            if transcript_text:
                base_segments = self.parse_srt_to_segments(transcript_text)
        if not base_segments:
            return ""
        return self.project_service.build_translation_signature(
            base_segments,
            src_lang=self.get_source_language_code(),
            target_lang=self.get_target_language_code(),
            enable_polish=self.is_ai_polish_enabled(),
            optimize_subtitles=self.is_ai_subtitle_optimization_enabled(),
            style_instruction=self.get_ai_style_instruction(),
        )

    def build_current_voice_signature(self, segments=None, background_path=""):
        voice_segments = list(segments or [])
        if not voice_segments:
            voice_segments = self._get_voiceover_segments()
        if not voice_segments:
            return ""
        return self.project_service.build_voice_signature(
            voice_segments,
            audio_handling_mode=self.get_audio_handling_mode(),
            voice_name=self.get_active_voice_name(),
            voice_speed=self._parse_voice_speed_value(),
            timing_sync_mode=str(self.voice_timing_sync_combo.currentText()).strip(),
            background_path=background_path,
            voice_gain_db=float(self.voice_gain_spin.value()),
            bg_gain_db=float(self.bg_gain_spin.value()),
            ducking_amount_db=float(self.ducking_amount_spin.value()) if hasattr(self, "ducking_amount_spin") else -6.0,
        )

    def persist_current_timeline_project_data(self):
        state = self.ensure_current_project()
        if not state:
            return
        if self.current_segments:
            self.current_segment_models = self.project_bridge.persist_transcription(
                state,
                self.current_segments,
                self.last_original_srt_path,
            )
        if self.current_translated_segments:
            self.current_translated_segment_models = self.project_bridge.persist_translation(
                state,
                self.current_segment_models,
                self.current_translated_segments,
                self.last_translated_srt_path,
            )
            signature = self.build_current_translation_signature()
            if signature:
                state.set_setting("translation_signature", signature)
        if self.current_project_state:
            voice_signature = self.build_current_voice_signature(
                segments=self._get_voiceover_segments(),
                background_path=self.resolve_background_audio_path(),
            )
            if voice_signature:
                state.set_setting("voice_signature", voice_signature)
        self.project_service.save_project(state)

    def load_project_context(self, state):
        if not state:
            return
        audio_handling_mode = str(getattr(state, "settings", {}).get("audio_handling_mode", "") or "").strip().lower()
        if audio_handling_mode and hasattr(self, "audio_handling_combo"):
            combo_index = self.audio_handling_combo.findData(audio_handling_mode)
            if combo_index >= 0:
                self.audio_handling_combo.setCurrentIndex(combo_index)
        context = self.project_bridge.load_context(state)
        self.processed_artifacts = {}
        self.last_original_srt_path = ""
        self.last_translated_srt_path = ""
        self.last_extracted_audio = ""
        self.last_vocals_path = ""
        self.last_music_path = ""
        self.last_voice_vi_path = ""
        self.last_mixed_vi_path = ""
        self.current_segment_models = []
        self.current_translated_segment_models = []
        self.current_segments = []
        self.current_translated_segments = []
        if hasattr(self, "audio_source_edit"):
            self.audio_source_edit.clear()
        if hasattr(self, "transcript_text"):
            self.transcript_text.clear()
        if hasattr(self, "translated_text"):
            self.translated_text.clear()
        if hasattr(self, "timeline"):
            self.timeline.set_segments([])
            self.timeline.set_video_thumbnails([])
            self.timeline.set_playing(False)
        self._timeline_video_thumb_cache_key = None
        self._timeline_video_thumbnails = []
        self.processed_artifacts.update(context["artifacts"])
        self.last_original_srt_path = self._normalize_local_file_path(context["last_original_srt_path"] or self.last_original_srt_path)
        self.last_translated_srt_path = self._normalize_local_file_path(context["last_translated_srt_path"] or self.last_translated_srt_path)
        self.last_extracted_audio = self._normalize_local_file_path(context["last_extracted_audio"] or self.last_extracted_audio)
        self.last_vocals_path = self._normalize_local_file_path(context["last_vocals_path"] or self.last_vocals_path)
        self.last_music_path = self._normalize_local_file_path(context["last_music_path"] or self.last_music_path)
        self.last_voice_vi_path = self._normalize_local_file_path(context["last_voice_vi_path"] or self.last_voice_vi_path)
        self.last_mixed_vi_path = self._normalize_local_file_path(context["last_mixed_vi_path"] or self.last_mixed_vi_path)
        self.current_segment_models = context["current_segment_models"]
        self.current_translated_segment_models = context["current_translated_segment_models"]
        self.current_segments = context["current_segments"]
        self.current_translated_segments = context["current_translated_segments"]
        if self.get_audio_handling_mode() == "clean" and self.last_vocals_path and os.path.exists(self.last_vocals_path):
            self.audio_source_edit.setText(self.last_vocals_path)
        elif self.last_extracted_audio and os.path.exists(self.last_extracted_audio):
            self.audio_source_edit.setText(self.last_extracted_audio)
        elif self.last_vocals_path and os.path.exists(self.last_vocals_path):
            self.audio_source_edit.setText(self.last_vocals_path)
        if self.current_segments:
            self.transcript_text.setText(self.format_to_srt(self.current_segments))
        if self.current_translated_segments:
            self.translated_text.setText(self.format_to_srt(self.current_translated_segments))
        if self.current_translated_segments or self.current_segments:
            self.apply_segments_to_timeline()

    def resolve_background_audio_path(self) -> str:
        manual_candidate = self.bg_music_edit.text().strip() if hasattr(self, "bg_music_edit") else ""
        if manual_candidate:
            normalized = self._normalize_local_file_path(manual_candidate)
            if normalized and os.path.exists(normalized):
                self.last_music_path = normalized
                self.processed_artifacts["music"] = normalized
                return normalized

        audio_mode = self.get_audio_handling_mode()
        state_artifacts = getattr(getattr(self, "current_project_state", None), "artifacts", {}) if getattr(self, "current_project_state", None) else {}
        candidates = []
        if audio_mode == "clean":
            candidates.extend(
                [
                    getattr(self, "last_music_path", ""),
                    state_artifacts.get("music", ""),
                    getattr(self, "last_extracted_audio", ""),
                    state_artifacts.get("extracted_audio", ""),
                ]
            )
        else:
            candidates.extend(
                [
                    getattr(self, "last_extracted_audio", ""),
                    state_artifacts.get("extracted_audio", ""),
                    getattr(self, "last_music_path", ""),
                    state_artifacts.get("music", ""),
                ]
            )
        for candidate in candidates:
            normalized = self._normalize_local_file_path(candidate)
            if normalized and os.path.exists(normalized):
                if audio_mode == "clean":
                    self.last_music_path = normalized
                    self.processed_artifacts["music"] = normalized
                else:
                    self.processed_artifacts["background_source"] = normalized
                return normalized
        return ""

    def has_reusable_voice_inputs(self) -> bool:
        state = self.ensure_current_project()
        if state and not self.translated_text.toPlainText().strip():
            self.load_project_context(state)
        translated_srt = self.translated_text.toPlainText().strip()
        if not translated_srt:
            return False
        return bool(self.parse_srt_to_segments(translated_srt))

    def schedule_auto_frame_preview(self):
        if not hasattr(self, "auto_preview_frame_cb") or not self.auto_preview_frame_cb.isChecked():
            return
        if self.auto_preview_frame_cb.isHidden():
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
        if self.auto_preview_frame_cb.isHidden():
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
        widget = getattr(self, "frame_preview_image_label", None)
        if widget is not None and hasattr(widget, "set_frame_image"):
            if hasattr(self, "video_view") and self.video_view is not None:
                widget.set_video_dimensions(
                    int(getattr(self.video_view, "video_source_width", 0) or 0),
                    int(getattr(self.video_view, "video_source_height", 0) or 0),
                )
                widget.set_preview_aspect_ratio(getattr(self.video_view, "preview_aspect_key", "source"))
                widget.set_preview_scale_mode(getattr(self.video_view, "preview_scale_mode", "fit"))
                focus_x, focus_y = self.get_output_fill_focus()
                widget.set_preview_fill_focus(focus_x, focus_y)
            widget.set_frame_image(image_path)
            return
        update_frame_preview_thumbnail_impl(self, image_path, QPixmap, Qt)

    def show_filter_thumbnail_preview(self, image_path: str):
        self._filter_thumbnail_visible = True
        if hasattr(self, "preview_context_label"):
            self.preview_context_label.hide()
        if hasattr(self, "frame_preview_status_label"):
            self.frame_preview_status_label.hide()
        if hasattr(self, "frame_preview_image_label"):
            target_height = int(getattr(self, "_filter_thumbnail_target_height", 320) or 320)
            if hasattr(self, "video_view") and self.video_view is not None:
                live_height = int(self.video_view.height() or 0)
                if live_height > 0:
                    target_height = max(320, live_height)
            current_height = int(getattr(self.frame_preview_image_label, "height", lambda: 0)() or 0)
            if current_height > 0:
                target_height = max(target_height, current_height)
            self._filter_thumbnail_target_height = target_height
            if hasattr(self.frame_preview_image_label, "setMinimumHeight"):
                self.frame_preview_image_label.setMinimumHeight(target_height)
            if hasattr(self.frame_preview_image_label, "setMaximumHeight"):
                self.frame_preview_image_label.setMaximumHeight(target_height)
            self.frame_preview_image_label.show()
        if hasattr(self, "video_view"):
            self.video_view.hide()
        self.update_frame_preview_thumbnail(image_path)
        if hasattr(self, "frame_preview_badge_label"):
            self._position_frame_preview_badge()
            self.frame_preview_badge_label.show()

    def hide_filter_thumbnail_preview(self):
        self._filter_thumbnail_visible = False
        if hasattr(self, "frame_preview_badge_label"):
            self.frame_preview_badge_label.hide()
        if hasattr(self, "frame_preview_image_label") and self.frame_preview_image_label is not None:
            current_height = int(getattr(self.frame_preview_image_label, "height", lambda: 0)() or 0)
            if current_height > 0:
                self._filter_thumbnail_target_height = max(320, current_height)
        if hasattr(self, "frame_preview_image_label"):
            if hasattr(self.frame_preview_image_label, "setMaximumHeight"):
                self.frame_preview_image_label.setMaximumHeight(16777215)
            if hasattr(self.frame_preview_image_label, "clear_frame_image"):
                self.frame_preview_image_label.clear_frame_image()
            self.frame_preview_image_label.hide()
        if hasattr(self, "frame_preview_status_label"):
            self.frame_preview_status_label.hide()
        if hasattr(self, "preview_context_label"):
            self.preview_context_label.hide()
        if hasattr(self, "video_view"):
            self.video_view.show()

    def _position_frame_preview_badge(self):
        badge = getattr(self, "frame_preview_badge_label", None)
        if badge is None:
            return
        host = None
        if getattr(self, "_filter_thumbnail_visible", False):
            host = getattr(self, "frame_preview_image_label", None)
        if host is None or not host.isVisible():
            host = getattr(self, "video_view", None)
        if host is None:
            return
        badge.adjustSize()
        content_rect = None
        if hasattr(host, "get_video_content_rect"):
            try:
                content_rect = host.get_video_content_rect()
            except Exception:
                content_rect = None
        if content_rect is not None and content_rect.width() > 0 and content_rect.height() > 0:
            x = host.x() + content_rect.right() - badge.width() - 14
            y = host.y() + content_rect.top() + 14
        else:
            x = host.x() + max(12, host.width() - badge.width() - 14)
            y = host.y() + 14
        badge.move(int(x), int(y))
        badge.raise_()

    def cleanup_file_if_exists(self, path: str):
        cleanup_file_if_exists_impl(path)

    def get_workspace_temp_root(self, create: bool = False) -> str:
        root = os.path.normpath(os.path.join(self.workspace_root, "temp"))
        if create:
            os.makedirs(root, exist_ok=True)
        return root

    def get_current_project_temp_key(self) -> str:
        state = getattr(self, "current_project_state", None)
        project_id = str(getattr(state, "project_id", "") or "").strip()
        if project_id:
            return project_id
        project_root = str(getattr(state, "project_root", "") or "").strip()
        if project_root:
            return os.path.basename(os.path.normpath(project_root))
        video_path = self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        if video_path:
            video_name = os.path.splitext(os.path.basename(video_path))[0] or "project"
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", video_name).strip("_").lower() or "project"
            digest = hashlib.sha1(os.path.abspath(video_path).encode("utf-8")).hexdigest()[:8]
            return f"{slug}_{digest}"
        return "global"

    def get_project_temp_root(self, create: bool = False) -> str:
        root = os.path.normpath(
            os.path.join(
                self.get_workspace_temp_root(create=create),
                "projects",
                self.get_current_project_temp_key(),
            )
        )
        if create:
            os.makedirs(root, exist_ok=True)
        return root

    def get_project_temp_path(self, *parts: str, create_parent: bool = False) -> str:
        path = os.path.normpath(os.path.join(self.get_project_temp_root(create=create_parent), *parts))
        if create_parent:
            parent = os.path.dirname(path) if os.path.splitext(path)[1] else path
            if parent:
                os.makedirs(parent, exist_ok=True)
        return path

    def get_project_temp_dir(self, *parts: str) -> str:
        path = self.get_project_temp_path(*parts, create_parent=True)
        os.makedirs(path, exist_ok=True)
        return path
    def get_output_mode_key(self):
        value = self.output_mode_combo.currentText() if hasattr(self, "output_mode_combo") else "Vietnamese subtitles + voice"
        return get_output_mode_key(value)

    def get_output_quality_key(self):
        if not hasattr(self, "output_quality_combo"):
            return "source"
        value = self.output_quality_combo.currentData()
        if value:
            return str(value).strip().lower()
        return str(self.output_quality_combo.currentText() or "source").strip().lower() or "source"

    def get_output_fps_key(self):
        if not hasattr(self, "output_fps_combo"):
            return "source"
        value = self.output_fps_combo.currentData()
        if value:
            return str(value).strip().lower()
        return str(self.output_fps_combo.currentText() or "source").strip().lower() or "source"

    def get_output_ratio_key(self):
        if not hasattr(self, "output_ratio_combo"):
            return "source"
        value = self.output_ratio_combo.currentData()
        if value:
            return str(value).strip().lower()
        return str(self.output_ratio_combo.currentText() or "source").strip().lower() or "source"

    def get_output_scale_mode_key(self):
        if not hasattr(self, "output_scale_mode_combo"):
            return "fit"
        value = self.output_scale_mode_combo.currentData()
        if value:
            return str(value).strip().lower()
        return str(self.output_scale_mode_combo.currentText() or "fit").strip().lower() or "fit"

    def get_output_fill_focus(self):
        if hasattr(self, "video_view") and hasattr(self.video_view, "get_preview_fill_focus"):
            return self.video_view.get_preview_fill_focus()
        return (0.5, 0.5)

    def _video_filter_presets(self):
        return {
            "original": {
                "brightness": 0,
                "contrast": 0,
                "saturation": 0,
                "temperature": 0,
                "highlights": 0,
                "shadows": 0,
            },
            "bright": {
                "brightness": 20,
                "contrast": 5,
                "saturation": 5,
                "temperature": 0,
                "highlights": -10,
                "shadows": 20,
            },
            "warm": {
                "brightness": 10,
                "contrast": 5,
                "saturation": 10,
                "temperature": 25,
                "highlights": -5,
                "shadows": 10,
            },
            "vivid": {
                "brightness": 10,
                "contrast": 20,
                "saturation": 25,
                "temperature": 0,
                "highlights": -5,
                "shadows": 5,
            },
            "cool": {
                "brightness": 0,
                "contrast": 15,
                "saturation": 5,
                "temperature": -20,
                "highlights": -10,
                "shadows": -5,
            },
            "soft": {
                "brightness": 10,
                "contrast": -12,
                "saturation": 5,
                "temperature": 10,
                "highlights": -15,
                "shadows": 15,
            },
        }

    def _video_filter_lut_map(self):
        return {
            "warm": asset_path("luts", "Portrait", "Portrait3.cube"),
            "vivid": asset_path("luts", "Color Boost", "Earth_Tone_Boost.cube"),
            "cool": asset_path("luts", "Cinematic", "Cinematic-2.cube"),
        }

    def _video_filter_fields(self):
        return ("brightness", "contrast", "saturation", "temperature", "highlights", "shadows")

    def _clamp_video_filter_value(self, value):
        try:
            numeric = int(round(float(value)))
        except Exception:
            numeric = 0
        return max(-100, min(100, numeric))

    def _default_video_filter_overrides(self):
        return {field: 0 for field in self._video_filter_fields()}

    def _default_video_filter_modified_flags(self):
        return {field: False for field in self._video_filter_fields()}

    def _normalize_video_filter_preset_key(self, preset_key):
        key = str(preset_key or "original").strip().lower()
        return key if key in self._video_filter_presets() else "original"

    def _get_video_filter_base_values(self, preset_key=None):
        key = self._normalize_video_filter_preset_key(preset_key or self._video_filter_preset_key)
        return dict(self._video_filter_presets().get(key, self._video_filter_presets()["original"]))

    def _get_video_filter_scaled_values(self, preset_key=None, intensity=None):
        base_values = self._get_video_filter_base_values(preset_key)
        scale = max(0.0, min(100.0, float(intensity if intensity is not None else self._video_filter_intensity))) / 100.0
        return {
            field: self._clamp_video_filter_value(base_values.get(field, 0) * scale)
            for field in self._video_filter_fields()
        }

    def _get_video_filter_effective_values(self, preset_key=None, intensity=None, overrides=None, modified_flags=None):
        scaled_values = self._get_video_filter_scaled_values(preset_key, intensity)
        effective = {}
        active_overrides = overrides if overrides is not None else self._video_filter_adjust_overrides
        active_modified = modified_flags if modified_flags is not None else self._video_filter_user_modified
        for field in self._video_filter_fields():
            if active_modified.get(field, False):
                effective[field] = self._clamp_video_filter_value(active_overrides.get(field, 0))
            else:
                effective[field] = self._clamp_video_filter_value(scaled_values.get(field, 0))
        return effective

    def _refresh_video_filter_ui(self):
        if not hasattr(self, "video_filter_intensity_slider"):
            return
        self._video_filter_ui_sync = True
        try:
            for preset_key, button in getattr(self, "video_filter_preset_buttons", {}).items():
                button.setChecked(preset_key == self._normalize_video_filter_preset_key(self._video_filter_preset_key))

            self.video_filter_intensity_slider.setValue(int(self._video_filter_intensity))
            if hasattr(self, "video_filter_intensity_value_label"):
                self.video_filter_intensity_value_label.setText(str(int(self._video_filter_intensity)))

            for field, slider in getattr(self, "video_filter_adjust_sliders", {}).items():
                slider.setValue(int(self._video_filter_adjust_overrides.get(field, 0)))
                self._update_video_filter_slider_visual_state(field, slider)
            for field, label in getattr(self, "video_filter_adjust_value_labels", {}).items():
                label.setText(str(int(self._video_filter_adjust_overrides.get(field, 0))))
                is_modified = bool(self._video_filter_user_modified.get(field, False))
                label.setProperty("filterModified", is_modified)
                label.style().unpolish(label)
                label.style().polish(label)
        finally:
            self._video_filter_ui_sync = False

    def _update_video_filter_slider_visual_state(self, field, slider):
        if not slider:
            return
        is_modified = bool(self._video_filter_user_modified.get(field, False))
        if is_modified:
            slider.setStyleSheet(
                "QSlider::groove:horizontal {"
                "background: #223248; height: 6px; border-radius: 3px; }"
                "QSlider::sub-page:horizontal {"
                "background: #4ea6d8; border-radius: 3px; }"
                "QSlider::handle:horizontal {"
                "background: #8ad7ff; width: 14px; margin: -5px 0; border-radius: 7px; }"
            )
        else:
            slider.setStyleSheet("")

    def set_video_filter_state(self, preset_key="original", intensity=75, overrides=None, modified_flags=None):
        self._video_filter_preset_key = self._normalize_video_filter_preset_key(preset_key)
        self._video_filter_intensity = max(0, min(100, int(round(float(intensity)))))
        base_overrides = self._default_video_filter_overrides()
        base_modified_flags = self._default_video_filter_modified_flags()
        for field in self._video_filter_fields():
            if overrides and field in overrides:
                base_overrides[field] = self._clamp_video_filter_value(overrides[field])
            if modified_flags and field in modified_flags:
                base_modified_flags[field] = bool(modified_flags[field])
        self._video_filter_adjust_overrides = base_overrides
        self._video_filter_user_modified = base_modified_flags
        self._refresh_video_filter_ui()
        self.refresh_ui_state()

    def on_video_filter_preset_selected(self, preset_key):
        if self._video_filter_ui_sync:
            return
        normalized_preset = self._normalize_video_filter_preset_key(preset_key)
        seeded_overrides = self._get_video_filter_scaled_values(normalized_preset, 75)
        self.set_video_filter_state(
            normalized_preset,
            75,
            seeded_overrides,
            self._default_video_filter_modified_flags(),
        )
        self._mark_video_filter_preview_dirty()
        self.schedule_live_video_filter_preview()

    def on_video_filter_intensity_changed(self, value):
        if self._video_filter_ui_sync:
            return
        self._video_filter_intensity = max(0, min(100, int(value)))
        self._refresh_video_filter_ui()
        self.refresh_ui_state()
        self._mark_video_filter_preview_dirty()
        if not self._is_video_filter_slider_interacting():
            self.schedule_live_video_filter_preview()

    def on_video_filter_adjust_changed(self, field_key, value):
        if self._video_filter_ui_sync:
            return
        normalized_field = str(field_key or "").strip().lower()
        if normalized_field not in self._video_filter_fields():
            return
        clamped_value = self._clamp_video_filter_value(value)
        scaled_value = self._get_video_filter_scaled_values().get(normalized_field, 0)
        self._video_filter_adjust_overrides[normalized_field] = clamped_value
        self._video_filter_user_modified[normalized_field] = int(clamped_value) != int(scaled_value)
        self._refresh_video_filter_ui()
        self.refresh_ui_state()
        self._mark_video_filter_preview_dirty()
        if not self._is_video_filter_slider_interacting():
            self.schedule_live_video_filter_preview()

    def reset_video_filters(self):
        self.set_video_filter_state(
            "original",
            75,
            self._default_video_filter_overrides(),
            self._default_video_filter_modified_flags(),
        )
        self._video_filter_preview_dirty = False
        self._video_filter_apply_requested = False
        self.revert_video_filter_preview_to_source()
        self.schedule_live_video_filter_preview()

    def reset_video_filter_adjustments(self):
        seeded_overrides = self._get_video_filter_scaled_values(self._video_filter_preset_key, self._video_filter_intensity)
        self.set_video_filter_state(
            self._video_filter_preset_key,
            self._video_filter_intensity,
            seeded_overrides,
            self._default_video_filter_modified_flags(),
        )
        self._mark_video_filter_preview_dirty()
        self.schedule_live_video_filter_preview()

    def get_video_filter_state(self):
        base_values = self._get_video_filter_base_values()
        scaled_values = self._get_video_filter_scaled_values()
        effective_values = self._get_video_filter_effective_values()
        preset_key = self._normalize_video_filter_preset_key(self._video_filter_preset_key)
        lut_path = str(self._video_filter_lut_map().get(preset_key, "") or "").strip()
        if lut_path and not os.path.exists(lut_path):
            lut_path = ""
        lut_strength = 0.0
        if lut_path:
            lut_strength = max(0.0, min(0.45, (float(self._video_filter_intensity) / 100.0) * 0.45))
        active = any(abs(int(value)) > 0 for value in effective_values.values())
        return {
            "preset": preset_key,
            "intensity": int(self._video_filter_intensity),
            "base": base_values,
            "scaled": scaled_values,
            "overrides": dict(self._video_filter_adjust_overrides),
            "modified": dict(self._video_filter_user_modified),
            "final": effective_values,
            "lut_path": lut_path,
            "lut_strength": lut_strength,
            "active": active,
        }

    def has_active_video_filters(self):
        return bool(self.get_video_filter_state().get("active"))

    def on_output_ratio_changed(self, *_args):
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_preview_aspect_ratio"):
            self.video_view.set_preview_aspect_ratio(self.get_output_ratio_key())
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_preview_scale_mode"):
            self.video_view.set_preview_scale_mode(self.get_output_scale_mode_key())
        self.update_subtitle_preview_style()
        self.apply_preview_blur_region()
        self.refresh_ui_state()

    def on_output_scale_mode_changed(self, *_args):
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_preview_scale_mode"):
            self.video_view.set_preview_scale_mode(self.get_output_scale_mode_key())
        self.update_subtitle_preview_style()
        self.apply_preview_blur_region()
        self.refresh_ui_state()

    def on_preview_framing_changed(self, *_args):
        self.apply_preview_blur_region()
        self.refresh_ui_state()

    def reset_preview_framing(self):
        if hasattr(self, "video_view") and hasattr(self.video_view, "reset_preview_fill_focus"):
            self.video_view.reset_preview_fill_focus()
        self.apply_preview_blur_region()
        self.refresh_ui_state()

    def get_audio_handling_mode(self):
        if not hasattr(self, "audio_handling_combo"):
            return "fast"
        value = self.audio_handling_combo.currentData()
        if value:
            return str(value).strip().lower()
        return "fast"

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
        return getattr(self, "translator_ai_cb", None) and self.translator_ai_cb.isChecked()

    def is_ai_subtitle_optimization_enabled(self):
        return bool(getattr(self, "ai_subtitle_optimization_cb", None) and self.ai_subtitle_optimization_cb.isChecked())

    def is_ai_dubbing_rewrite_enabled(self):
        return bool(getattr(self, "ai_dubbing_rewrite_cb", None) and self.ai_dubbing_rewrite_cb.isChecked())

    def get_ai_dubbing_style_instruction(self):
        if hasattr(self, "translator_style_edit"):
            return " ".join(self.translator_style_edit.text().split()).strip()
        return ""

    def get_ai_style_instruction(self):
        style_parts = []
        if hasattr(self, "translator_style_edit"):
            custom_style = self.translator_style_edit.text().strip()
            if custom_style:
                style_parts.append(custom_style)
        if hasattr(self, "subtitle_single_line_cb") and self.subtitle_single_line_cb.isChecked():
            style_parts.append("[subtitle_layout=single_line]")
        if (
            hasattr(self, "subtitle_keyword_highlight_cb")
            and self.subtitle_keyword_highlight_cb.isChecked()
            and hasattr(self, "subtitle_highlight_mode_combo")
            and self.subtitle_highlight_mode_combo.currentText().strip() in ("Auto", "Auto + Manual")
        ):
            style_parts.append("[keyword_highlight=ai_local]")
        return " | ".join(part for part in style_parts if part).strip()

    def on_output_mode_changed(self, value: str):
        mode = self.get_output_mode_key()
        if getattr(self, "_filter_thumbnail_visible", False):
            self.hide_filter_thumbnail_preview()
        self.workflow_hint_label.setText(build_workflow_hint(mode, self.is_ai_polish_enabled()))

        show_voice = mode in ("voice", "both")
        if hasattr(self, "voice_section_card"):
            self.voice_section_card.setVisible(show_voice)
        if hasattr(self, "voiceover_btn"):
            self.voiceover_btn.setVisible(show_voice)
        if hasattr(self, "quick_preview_btn"):
            self.quick_preview_btn.setVisible(show_voice)
        if hasattr(self, "styled_preview_btn"):
            self.styled_preview_btn.setVisible(show_voice)
        self.mixed_audio_edit.setEnabled(show_voice)
        if hasattr(self, "use_generated_audio_radio"):
            self.use_generated_audio_radio.setVisible(show_voice)
        if hasattr(self, "use_existing_audio_radio"):
            self.use_existing_audio_radio.setVisible(show_voice)
        if hasattr(self, "browse_bg_music_btn"):
            self.browse_bg_music_btn.setVisible(show_voice)
        if hasattr(self, "browse_mixed_audio_btn"):
            self.browse_mixed_audio_btn.setVisible(show_voice)
        if hasattr(self, "audio_handling_combo"):
            self.audio_handling_combo.setVisible(show_voice)
        if hasattr(self, "output_subtitle_radio"):
            self.output_subtitle_radio.setChecked(mode == "subtitle")
            self.output_voice_radio.setChecked(mode == "voice")
            self.output_both_radio.setChecked(mode == "both")
        self.export_btn.setText(get_export_button_label(mode))
        self.refresh_ui_state()

    def on_left_panel_workflow_changed(self, index: int):
        # Filter thumbnail preview should only stay active while the Filter page is open.
        if int(index) != 4 and getattr(self, "_filter_thumbnail_visible", False):
            self.hide_filter_thumbnail_preview()

    def _workflow_dependency_state(self) -> dict:
        video_path = self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        has_video = bool(video_path and os.path.exists(video_path))
        return {
            "media": {"enabled": True, "reason": ""},
            "language": {"enabled": has_video, "reason": "Select a video first to transcribe and translate."},
            "voice": {"enabled": has_video, "reason": "Select a video first to configure voice and audio."},
            "style": {"enabled": has_video, "reason": "Select a video first to style subtitle output."},
            "filter": {"enabled": has_video, "reason": "Select a video first to preview and apply filters."},
            "advanced": {"enabled": True, "reason": ""},
        }

    def update_workflow_availability(self):
        states = self._workflow_dependency_state()
        current_index = int(self.left_panel_stack.currentIndex()) if hasattr(self, "left_panel_stack") else 0
        page_order = ["media", "language", "voice", "style", "filter", "advanced"]

        for page_key, state in states.items():
            container = getattr(self, "workflow_page_containers", {}).get(page_key) if hasattr(self, "workflow_page_containers") else None
            hint = getattr(self, "workflow_page_hints", {}).get(page_key) if hasattr(self, "workflow_page_hints") else None
            tab_btn = getattr(self, "workflow_tab_buttons", {}).get(page_key) if hasattr(self, "workflow_tab_buttons") else None
            enabled = bool(state.get("enabled"))
            reason = str(state.get("reason", "") or "").strip()
            if container is not None:
                container.setEnabled(enabled)
            if hint is not None:
                hint.setText("" if enabled else reason)
                hint.setVisible(not enabled and bool(reason))
            if tab_btn is not None:
                tab_btn.setEnabled(enabled)
                tab_btn.style().unpolish(tab_btn)
                tab_btn.style().polish(tab_btn)

        active_key = page_order[current_index] if 0 <= current_index < len(page_order) else "media"
        active_state = states.get(active_key, {"enabled": True})
        if not active_state.get("enabled", True):
            for fallback_key in ("media", "advanced"):
                fallback_index = page_order.index(fallback_key)
                fallback_state = states.get(fallback_key, {"enabled": True})
                if fallback_state.get("enabled", True):
                    btn = getattr(self, "workflow_tab_buttons", {}).get(fallback_key) if hasattr(self, "workflow_tab_buttons") else None
                    if btn is not None:
                        btn.setChecked(True)
                    elif hasattr(self, "left_panel_stack"):
                        self.left_panel_stack.setCurrentIndex(fallback_index)
                    break

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
        self.readiness_label.hide()
        self.update_progress_checklist()
        self.update_preview_context_label(guidance["has_subtitles"], guidance["has_voice_audio"])

    def update_project_header(self):
        video_path = self.video_path_edit.text().strip()
        if video_path:
            video_name = os.path.basename(video_path)
            self.project_title_label.setText(f"Project: {video_name}")
            self.upload_status_label.setText(f"[OK] {video_name} uploaded")
        else:
            self.project_title_label.setText("Project: No video selected")
            self.upload_status_label.setText("No video uploaded yet")

    def sync_left_panel_container_width(self):
        scroll_area = getattr(self, "left_panel_scroll_area", None)
        container = getattr(self, "left_panel_container", None)
        if not scroll_area or not container:
            return
        viewport_width = max(0, scroll_area.viewport().width())
        if viewport_width <= 0:
            return
        gutter = 10
        target_width = max(320, viewport_width - gutter)
        container.setMaximumWidth(target_width)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest):
            scroll_area = getattr(self, "left_panel_scroll_area", None)
            if scroll_area and watched in (scroll_area, scroll_area.viewport(), scroll_area.verticalScrollBar()):
                QTimer.singleShot(0, self.sync_left_panel_container_width)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Undo):
            focused = self.focusWidget()
            if isinstance(focused, (QTextEdit, QLineEdit)):
                super().keyPressEvent(event)
                return
            if self.undo_last_timeline_timing_edit():
                event.accept()
                return
        if event.matches(QKeySequence.Redo):
            focused = self.focusWidget()
            if isinstance(focused, (QTextEdit, QLineEdit)):
                super().keyPressEvent(event)
                return
            if self.redo_last_timeline_timing_edit():
                event.accept()
                return
        super().keyPressEvent(event)

    def toggle_controls_panel(self):
        currently_visible = bool(getattr(self, "left_panel_scroll_area", None) and self.left_panel_scroll_area.isVisible())
        self.set_controls_panel_visible(not currently_visible)

    def set_controls_panel_visible(self, visible: bool):
        if hasattr(self, "left_panel_scroll_area"):
            self.left_panel_scroll_area.setVisible(visible)
        if hasattr(self, "toggle_controls_action"):
            self.toggle_controls_action.setText("Hide Controls" if visible else "Show Controls")

    def update_progress_checklist(self):
        steps = getattr(getattr(self, "current_project_state", None), "steps", {}) or {}

        def set_status_chip_state(label, state: str):
            if not label:
                return
            label.setProperty("state", state)
            label.style().unpolish(label)
            label.style().polish(label)
            label.update()

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

        self.progress_audio_label.setText(("[OK] " if has_audio else "[ ] ") + "Audio")
        set_status_chip_state(self.progress_audio_label, "ok" if has_audio else "pending")
        self.progress_subtitle_label.setText(("[OK] " if has_subtitle else "[ ] ") + "Original")
        set_status_chip_state(self.progress_subtitle_label, "ok" if has_subtitle else "pending")
        if translation_running:
            self.progress_translate_label.setText("[...] Vietnamese")
            set_status_chip_state(self.progress_translate_label, "running")
        else:
            self.progress_translate_label.setText(("[OK] " if has_translation else "[ ] ") + "Vietnamese")
            set_status_chip_state(self.progress_translate_label, "ok" if has_translation else "pending")

        if self.get_output_mode_key() == "subtitle":
            self.progress_voice_label.setText("[ ] Voice N/A")
            set_status_chip_state(self.progress_voice_label, "na")
        elif voice_running:
            self.progress_voice_label.setText("[...] Voice")
            set_status_chip_state(self.progress_voice_label, "running")
        else:
            self.progress_voice_label.setText(("[OK] " if has_voice else "[ ] ") + "Voice")
            set_status_chip_state(self.progress_voice_label, "ok" if has_voice else "pending")

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

    def choose_subtitle_background_color(self):
        current = getattr(self, "subtitle_background_color_hex", "#000000")
        color = QColorDialog.getColor(QColor(current), self, "Choose Subtitle Background Color")
        if not color.isValid():
            return
        self.subtitle_background_color_hex = color.name().upper()
        if hasattr(self, "subtitle_background_color_btn"):
            self.subtitle_background_color_btn.setText(self.subtitle_background_color_hex)
        self.update_subtitle_preview_style()

    def update_subtitle_preview_style(self):
        if not hasattr(self, "video_view"):
            return
        item = self.video_view.subtitle_item
        source_h = max(1, getattr(self.video_view, "video_source_height", 0) or 1080)
        preview_rect = self.video_view.get_preview_canvas_rect() if hasattr(self.video_view, "get_preview_canvas_rect") else self.video_view.get_video_content_rect()
        preview_h = max(1.0, preview_rect.height() or float(self.video_view.height()) or 1.0)
        preset = self.get_subtitle_preset_config()
        export_font_size = int(self.subtitle_font_size_spin.value())
        single_line_enabled = bool(getattr(self, "subtitle_single_line_cb", None) and self.subtitle_single_line_cb.isChecked())
        if single_line_enabled:
            export_font_size = max(10, int(round(export_font_size * 0.84)))
        preview_font_size = max(10, int(round(export_font_size * (preview_h / source_h))))
        font_name = (
            self.subtitle_font_combo.currentText().strip()
            if self.get_selected_subtitle_preset() == "custom"
            else preset.get("font_name", "Segoe UI")
        )
        bg_alpha = float(preset.get("background_alpha", 0.0))
        bg_color = QColor(preset.get("background_color", "#000000"))
        bg_color.setAlpha(max(0, min(255, int(round(bg_alpha * 255.0)))))
        item.set_style(
            font_name=font_name or preset.get("font_name", "Segoe UI"),
            font_size=preview_font_size,
            font_color=QColor(self.subtitle_color_hex),
            outline_width=preset.get("outline_width", 2),
            outline_color=QColor(preset.get("outline_color", "#000000")),
            background_box=bool(self.subtitle_background_cb.isChecked() if self.get_selected_subtitle_preset() == "custom" else preset.get("background_box", False)),
            background_color=bg_color,
            single_line=bool(getattr(self, "subtitle_single_line_cb", None) and self.subtitle_single_line_cb.isChecked()),
        )
        item.set_alignment(self.subtitle_align_combo.currentText())
        item.set_positioning(
            x_offset=int(self.subtitle_x_offset_spin.value()),
            bottom_offset=int(self.subtitle_bottom_offset_spin.value()),
            custom_position_enabled=self.is_custom_subtitle_position_mode(),
            custom_x_percent=int(self.subtitle_custom_x_spin.value()),
            custom_y_percent=int(self.subtitle_custom_y_spin.value()),
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
        if bool(getattr(self, "subtitle_single_line_cb", None) and self.subtitle_single_line_cb.isChecked()):
            export_font_size = max(10, int(round(export_font_size * 0.84)))
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
            "karaoke_timing_mode": str(self.subtitle_karaoke_timing_combo.currentData() or "vietnamese"),
            "position_mode": "custom" if self.is_custom_subtitle_position_mode() else "anchor",
            "alignment": alignment_map.get(self.subtitle_align_combo.currentText(), 2),
            "margin_v": int(self.subtitle_bottom_offset_spin.value()),
            "custom_position_enabled": self.is_custom_subtitle_position_mode(),
            "custom_position_x": int(self.subtitle_custom_x_spin.value()),
            "custom_position_y": int(self.subtitle_custom_y_spin.value()),
            "background_box": bool(self.subtitle_background_cb.isChecked() if is_custom else preset.get("background_box", False)),
            "bold": bool(self.subtitle_bold_cb.isChecked() if is_custom else preset.get("bold", False)),
            "preset_key": self.get_selected_subtitle_preset(),
            "auto_keyword_highlight": bool(self.subtitle_keyword_highlight_cb.isChecked())
            and self.subtitle_highlight_mode_combo.currentText().strip() in ("Auto", "Auto + Manual")
            and not any(seg.get("auto_highlights") for seg in (style_segments or [])),
            "manual_highlights": self._build_render_highlight_lists(style_segments or []),
            "word_timings": [list(seg.get("words", [])) for seg in (style_segments or [])],
            "blur_region": self.video_view.get_blur_region_normalized() if hasattr(self, "video_view") else None,
            "render_subtitles": False,
        }

    def _build_render_highlight_lists(self, style_segments):
        mode = self.subtitle_highlight_mode_combo.currentText().strip() if hasattr(self, "subtitle_highlight_mode_combo") else "Auto"
        include_auto = mode in ("Auto", "Auto + Manual")
        include_manual = mode in ("Manual", "Auto + Manual")
        rows = []
        for seg in style_segments or []:
            merged = []
            seen = set()
            if include_auto:
                for phrase in seg.get("auto_highlights", []) or []:
                    normalized = self._normalize_manual_highlight(phrase)
                    key = normalized.lower()
                    if normalized and key not in seen:
                        seen.add(key)
                        merged.append(normalized)
            if include_manual:
                for phrase in seg.get("manual_highlights", []) or []:
                    normalized = self._normalize_manual_highlight(phrase)
                    key = normalized.lower()
                    if normalized and key not in seen:
                        seen.add(key)
                        merged.append(normalized)
            rows.append(merged)
        return rows

    def on_subtitle_preset_changed(self):
        preset = self.get_subtitle_preset_config()
        is_custom = self.get_selected_subtitle_preset() == "custom"
        if not is_custom:
            self.subtitle_font_combo.setCurrentText(preset.get("font_name", "Arial"))
            self.subtitle_animation_combo.setCurrentText(preset.get("animation", "Static"))
            self.subtitle_background_cb.setChecked(bool(preset.get("background_box", False)))
            if hasattr(self, "subtitle_outline_cb"):
                self.subtitle_outline_cb.setChecked(bool(preset.get("outline_width", 0) > 0))
            if hasattr(self, "subtitle_bg_alpha_spin"):
                self.subtitle_bg_alpha_spin.setValue(float(preset.get("background_alpha", self.subtitle_bg_alpha_spin.value())))
            self.subtitle_bold_cb.setChecked(bool(preset.get("bold", False)))
            if hasattr(self, "subtitle_keyword_highlight_cb"):
                self.subtitle_keyword_highlight_cb.setChecked(bool(preset.get("auto_keyword_highlight", False)))
            if hasattr(self, "subtitle_highlight_color_combo"):
                color_name = "Yellow" if preset.get("highlight_color", "").upper() == "#FFD400" else "Cyan"
                self.subtitle_highlight_color_combo.setCurrentText(color_name)
            if hasattr(self, "subtitle_highlight_mode_combo"):
                self.subtitle_highlight_mode_combo.setCurrentText(str(preset.get("highlight_mode", "Auto")))
        self.subtitle_font_combo.setEnabled(is_custom)
        self.subtitle_animation_combo.setEnabled(is_custom)
        self.subtitle_karaoke_timing_combo.setEnabled(is_custom)
        self.subtitle_background_cb.setEnabled(is_custom)
        if hasattr(self, "subtitle_outline_cb"):
            self.subtitle_outline_cb.setEnabled(is_custom)
        if hasattr(self, "subtitle_bg_alpha_spin"):
            self.subtitle_bg_alpha_spin.setEnabled(is_custom)
        self.subtitle_bold_cb.setEnabled(is_custom)
        if hasattr(self, "subtitle_preset_summary_label"):
            self.subtitle_preset_summary_label.setText(
                f"{preset.get('label', 'Preset')}: {preset.get('summary', '')}"
            )
        self._update_animation_time_visibility()
        self.on_subtitle_position_mode_changed()

    def _update_animation_time_visibility(self):
        current_animation = self.subtitle_animation_combo.currentText().strip().lower()
        show_animation_time = current_animation != "static"
        show_karaoke_timing = current_animation in ("word highlight karaoke", "typewriter")
        if hasattr(self, "subtitle_animation_time_label"):
            self.subtitle_animation_time_label.setVisible(show_animation_time)
        if hasattr(self, "subtitle_animation_time_spin"):
            self.subtitle_animation_time_spin.setVisible(show_animation_time)
        if hasattr(self, "subtitle_karaoke_timing_label"):
            self.subtitle_karaoke_timing_label.setVisible(show_karaoke_timing)
        if hasattr(self, "subtitle_karaoke_timing_combo"):
            self.subtitle_karaoke_timing_combo.setVisible(show_karaoke_timing)

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
                    "segment_index": idx,
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

    def refresh_ai_keyword_highlights(self, force: bool = False):
        if not getattr(self, "current_translated_segments", None):
            return
        if not getattr(self, "subtitle_keyword_highlight_cb", None) or not self.subtitle_keyword_highlight_cb.isChecked():
            return
        if not hasattr(self, "subtitle_highlight_mode_combo") or self.subtitle_highlight_mode_combo.currentText().strip() not in ("Auto", "Auto + Manual"):
            return

        provider = self._local_polisher_provider_cls()()
        if not provider.is_configured():
            self.log("[AI Keyword Highlight] Local provider is not configured, keeping fallback highlight behavior.")
            return

        pending_indexes = []
        pending_texts = []
        for idx, segment in enumerate(self.current_translated_segments or []):
            text = ' '.join(str(segment.get("text") or "").replace("\n", " ").split()).strip()
            if not text:
                segment["auto_highlights"] = []
                continue
            cached_key = segment.get("_auto_highlights_source_text", "")
            if not force and cached_key == text and isinstance(segment.get("auto_highlights"), list):
                continue
            pending_indexes.append(idx)
            pending_texts.append(text)

        if not pending_texts:
            return

        self.log(f"[AI Keyword Highlight] Generating highlight phrases for {len(pending_texts)} subtitle lines...")
        batch_size = 10
        resolved_batches = []
        for start_idx in range(0, len(pending_texts), batch_size):
            batch = pending_texts[start_idx:start_idx + batch_size]
            try:
                resolved_batches.extend(provider.select_keyword_highlights_batch(texts=batch, target_lang="vi", max_keywords=2))
            except Exception as exc:
                self.log(f"[AI Keyword Highlight] Fallback to built-in auto highlight: {exc}")
                return

        for idx, phrases in zip(pending_indexes, resolved_batches):
            segment = self.current_translated_segments[idx]
            text = ' '.join(str(segment.get("text") or "").replace("\n", " ").split()).strip()
            cleaned = []
            seen = set()
            lowered = text.lower()
            for phrase in phrases or []:
                normalized = self._normalize_manual_highlight(phrase)
                key = normalized.lower()
                if not normalized or key in seen or key not in lowered:
                    continue
                seen.add(key)
                cleaned.append(normalized)
            segment["auto_highlights"] = cleaned
            segment["_auto_highlights_source_text"] = text

        self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)

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
        row = self._find_segment_editor_row(index)
        if not row:
            return
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

    def _update_segment_highlight_button_state(self, index: int, editor: QTextEdit):
        row = self._find_segment_editor_row(index)
        if not row:
            return
        button = row.get("highlight_button")
        if button is None:
            return
        has_selection = bool(self._normalize_manual_highlight(editor.textCursor().selectedText()))
        button.setEnabled(has_selection)

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

    def _get_effective_selected_segment_index(self, rows=None) -> int:
        rows = rows if rows is not None else self._segment_editor_display_rows()
        if not rows:
            return -1
        selected = int(getattr(self, "_selected_segment_index", -1))
        valid_indexes = [int(row.get("segment_index", idx)) for idx, row in enumerate(rows)]
        if selected in valid_indexes:
            return selected
        active_index = self._find_active_segment_index(self.media_player.position(), self.live_preview_segments or self.get_active_segments())
        if active_index in valid_indexes:
            return active_index
        return valid_indexes[0]

    def set_selected_segment_index(self, index: int, *, sync_ui: bool = True):
        rows = self._segment_editor_display_rows()
        valid_indexes = [int(row.get("segment_index", idx)) for idx, row in enumerate(rows)]
        if not valid_indexes:
            self._selected_segment_index = -1
        elif index in valid_indexes:
            self._selected_segment_index = int(index)
        else:
            self._selected_segment_index = valid_indexes[0]
        if sync_ui:
            self.sync_segment_editor_rows()

    def on_timeline_segment_timing_edit_started(self, index: int, start: float, end: float):
        if self._suspend_timeline_undo:
            return
        last_entry = self._timeline_timing_undo_stack[-1] if self._timeline_timing_undo_stack else None
        if last_entry and str(last_entry.get("type", "timing")) == "timing" and int(last_entry.get("index", -1)) == int(index):
            if abs(float(last_entry.get("start", 0.0)) - float(start)) < 0.0001 and abs(float(last_entry.get("end", 0.0)) - float(end)) < 0.0001:
                return
        self._timeline_timing_undo_stack.append(
            {
                "type": "timing",
                "index": int(index),
                "start": float(start),
                "end": float(end),
            }
        )
        self._timeline_timing_redo_stack = []
        if len(self._timeline_timing_undo_stack) > 100:
            self._timeline_timing_undo_stack = self._timeline_timing_undo_stack[-100:]
        self._refresh_timeline_history_buttons()

    def on_timeline_segment_selected(self, index: int):
        self.set_selected_segment_index(index, sync_ui=True)
        if hasattr(self, "timeline"):
            self.timeline.set_active_segment_index(index)

    def _sync_hidden_transcript_text_from_segments(self):
        if getattr(self, "_syncing_segment_editor", False):
            return
        self._syncing_hidden_editor_text = True
        try:
            self.transcript_text.setText(self.format_to_srt(self.current_segments))
        finally:
            self._syncing_hidden_editor_text = False

    def _apply_segment_timing(self, segment: dict, start: float, end: float):
        segment["start"] = float(start)
        segment["end"] = float(end)
        if "tts_group_start" in segment or "tts_group_end" in segment:
            segment["tts_group_start"] = float(start)
            segment["tts_group_end"] = float(end)

    def _build_split_segment_pair(self, segment: dict, split_time: float):
        first = dict(segment or {})
        second = dict(segment or {})

        first["start"] = float(segment.get("start", 0.0))
        first["end"] = float(split_time)
        second["start"] = float(split_time)
        second["end"] = float(segment.get("end", split_time))

        # Keep clip content unchanged on split; only timing is divided.
        first["text"] = str(segment.get("text", "") or "")
        second["text"] = str(segment.get("text", "") or "")
        first["tts_text"] = str(segment.get("tts_text", segment.get("text", "")) or "")
        second["tts_text"] = str(segment.get("tts_text", segment.get("text", "")) or "")
        first["words"] = []
        second["words"] = []
        first["manual_highlights"] = list(segment.get("manual_highlights", []))
        second["manual_highlights"] = list(segment.get("manual_highlights", []))
        if "tts_group_start" in first or "tts_group_end" in first:
            first["tts_group_start"] = float(first["start"])
            first["tts_group_end"] = float(first["end"])
            second["tts_group_start"] = float(second["start"])
            second["tts_group_end"] = float(second["end"])
        return first, second

    def _timeline_neighbor_bounds(self, index: int):
        active_segments = list(self.get_active_segments() or [])
        prev_end = 0.0
        next_start = max(0.0, float(getattr(self.timeline, "duration", 0)) / 1000.0)
        if index > 0 and index - 1 < len(active_segments):
            prev_end = float(active_segments[index - 1].get("end", 0.0))
        if index + 1 < len(active_segments):
            next_start = float(active_segments[index + 1].get("start", next_start))
        return prev_end, next_start

    def nudge_selected_timeline_segment(self, delta_seconds: float):
        segments = list(self.get_active_segments() or [])
        if not segments:
            return
        index = int(getattr(self, "_selected_segment_index", -1))
        if not (0 <= index < len(segments)):
            index = self._find_active_segment_index(self.media_player.position(), segments)
        if not (0 <= index < len(segments)):
            return

        target = segments[index]
        start = float(target.get("start", 0.0))
        end = float(target.get("end", 0.0))
        duration = max(0.0, end - start)
        gap = float(getattr(self.timeline, "SEGMENT_GAP", 0.03))
        prev_end, next_start = self._timeline_neighbor_bounds(index)
        max_timeline = max(0.0, float(getattr(self.timeline, "duration", 0)) / 1000.0)
        min_start = max(0.0, prev_end + gap)
        if index + 1 < len(segments):
            max_start = max(min_start, next_start - gap - duration)
        else:
            max_start = max(0.0, max_timeline - duration)
        new_start = min(max(start + float(delta_seconds), min_start), max_start)
        if abs(new_start - start) < 0.0001:
            return
        new_end = new_start + duration
        self.on_timeline_segment_timing_edit_started(index, start, end)
        self.on_timeline_segment_timing_changed(index, new_start, new_end)

    def ripple_nudge_selected_timeline_segment(self, delta_seconds: float):
        segments = list(self.get_active_segments() or [])
        if not segments:
            return
        index = int(getattr(self, "_selected_segment_index", -1))
        if not (0 <= index < len(segments)):
            index = self._find_active_segment_index(self.media_player.position(), segments)
        if not (0 <= index < len(segments)):
            return

        gap = float(getattr(self.timeline, "SEGMENT_GAP", 0.0))
        max_timeline = max(0.0, float(getattr(self.timeline, "duration", 0)) / 1000.0)
        prev_end, _next_start = self._timeline_neighbor_bounds(index)
        first_start = float(segments[index].get("start", 0.0))
        last_end = float(segments[-1].get("end", 0.0))
        min_delta = max(0.0, prev_end + gap) - first_start
        max_delta = max_timeline - last_end
        actual_delta = min(max(float(delta_seconds), min_delta), max_delta)
        if abs(actual_delta) < 0.0001:
            return

        history_entry = {
            "type": "batch_timing",
            "index": int(index),
            "selected_before": int(index),
            "selected_after": int(index),
            "current_before": [],
            "current_after": [],
            "translated_before": [],
            "translated_after": [],
        }

        if 0 <= index < len(self.current_segments or []):
            history_entry["current_before"] = [copy.deepcopy(seg) for seg in self.current_segments[index:]]
            for seg in self.current_segments[index:]:
                self._apply_segment_timing(
                    seg,
                    float(seg.get("start", 0.0)) + actual_delta,
                    float(seg.get("end", 0.0)) + actual_delta,
                )
            history_entry["current_after"] = [copy.deepcopy(seg) for seg in self.current_segments[index:]]
            self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
            self._sync_hidden_transcript_text_from_segments()

        if 0 <= index < len(self.current_translated_segments or []):
            history_entry["translated_before"] = [copy.deepcopy(seg) for seg in self.current_translated_segments[index:]]
            for seg in self.current_translated_segments[index:]:
                self._apply_segment_timing(
                    seg,
                    float(seg.get("start", 0.0)) + actual_delta,
                    float(seg.get("end", 0.0)) + actual_delta,
                )
            history_entry["translated_after"] = [copy.deepcopy(seg) for seg in self.current_translated_segments[index:]]
            self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
            self._sync_hidden_translated_text_from_segments()

        self._timeline_timing_undo_stack.append(history_entry)
        self._timeline_timing_redo_stack = []
        if len(self._timeline_timing_undo_stack) > 100:
            self._timeline_timing_undo_stack = self._timeline_timing_undo_stack[-100:]
        self._refresh_timeline_history_buttons()

        self.set_selected_segment_index(index, sync_ui=True)
        if hasattr(self, "timeline"):
            self.timeline.set_active_segment_index(index)
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def _apply_timeline_structure_history_entry(self, entry: dict, *, use_after: bool):
        index = int(entry.get("index", -1))
        current_before = [copy.deepcopy(seg) for seg in list(entry.get("current_before", []) or [])]
        current_after = [copy.deepcopy(seg) for seg in list(entry.get("current_after", []) or [])]
        translated_before = [copy.deepcopy(seg) for seg in list(entry.get("translated_before", []) or [])]
        translated_after = [copy.deepcopy(seg) for seg in list(entry.get("translated_after", []) or [])]

        if self.current_segments is not None:
            replace_with = current_after if use_after else current_before
            replace_count = len(current_before if use_after else current_after)
            if current_before or current_after:
                self.current_segments[index:index + replace_count] = replace_with
                self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
                self._sync_hidden_transcript_text_from_segments()

        if self.current_translated_segments is not None:
            replace_with = translated_after if use_after else translated_before
            replace_count = len(translated_before if use_after else translated_after)
            if translated_before or translated_after:
                self.current_translated_segments[index:index + replace_count] = replace_with
                self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
                self._sync_hidden_translated_text_from_segments()

        target_index = int(entry.get("selected_after" if use_after else "selected_before", index))
        self.set_selected_segment_index(target_index, sync_ui=True)
        if hasattr(self, "timeline"):
            self.timeline.set_active_segment_index(target_index)
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def split_selected_timeline_segment(self):
        segments = list(self.get_active_segments() or [])
        if not segments:
            return
        index = int(getattr(self, "_selected_segment_index", -1))
        if not (0 <= index < len(segments)):
            index = self._find_active_segment_index(self.media_player.position(), segments)
        if not (0 <= index < len(segments)):
            QMessageBox.information(self, "Split Segment", "Please select an audio/subtitle block first.")
            return

        target = segments[index]
        split_time = float(self.media_player.position()) / 1000.0
        start = float(target.get("start", 0.0))
        end = float(target.get("end", 0.0))
        min_gap = max(0.12, getattr(self.timeline, "MIN_SEGMENT_DURATION", 0.1))
        if not (start + min_gap < split_time < end - min_gap):
            QMessageBox.information(
                self,
                "Split Segment",
                "Move the playhead inside the selected block before splitting.",
            )
            return

        split_history_entry = {
            "type": "split",
            "index": int(index),
            "selected_before": int(index),
            "selected_after": int(index + 1),
            "current_before": [],
            "current_after": [],
            "translated_before": [],
            "translated_after": [],
        }

        if 0 <= index < len(self.current_segments or []):
            split_history_entry["current_before"] = [copy.deepcopy(self.current_segments[index])]
            first, second = self._build_split_segment_pair(self.current_segments[index], split_time)
            self.current_segments[index:index + 1] = [first, second]
            split_history_entry["current_after"] = [copy.deepcopy(first), copy.deepcopy(second)]
            self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
            self._sync_hidden_transcript_text_from_segments()

        if 0 <= index < len(self.current_translated_segments or []):
            split_history_entry["translated_before"] = [copy.deepcopy(self.current_translated_segments[index])]
            first, second = self._build_split_segment_pair(self.current_translated_segments[index], split_time)
            self.current_translated_segments[index:index + 1] = [first, second]
            split_history_entry["translated_after"] = [copy.deepcopy(first), copy.deepcopy(second)]
            self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
            self._sync_hidden_translated_text_from_segments()

        self._timeline_timing_undo_stack.append(split_history_entry)
        self._timeline_timing_redo_stack = []
        if len(self._timeline_timing_undo_stack) > 100:
            self._timeline_timing_undo_stack = self._timeline_timing_undo_stack[-100:]
        self._refresh_timeline_history_buttons()

        self.set_selected_segment_index(index + 1, sync_ui=True)
        if hasattr(self, "timeline"):
            self.timeline.set_active_segment_index(index + 1)
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def delete_selected_timeline_segment(self):
        segments = list(self.get_active_segments() or [])
        if not segments:
            return
        index = int(getattr(self, "_selected_segment_index", -1))
        if not (0 <= index < len(segments)):
            index = self._find_active_segment_index(self.media_player.position(), segments)
        if not (0 <= index < len(segments)):
            QMessageBox.information(self, "Delete Segment", "Please select an audio/subtitle block first.")
            return

        remaining_count = max(0, len(segments) - 1)
        target_selection = min(index, max(0, remaining_count - 1)) if remaining_count else -1
        delete_history_entry = {
            "type": "delete",
            "index": int(index),
            "selected_before": int(index),
            "selected_after": int(target_selection),
            "current_before": [],
            "current_after": [],
            "translated_before": [],
            "translated_after": [],
        }

        if 0 <= index < len(self.current_segments or []):
            delete_history_entry["current_before"] = [copy.deepcopy(self.current_segments[index])]
            self.current_segments[index:index + 1] = []
            self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
            self._sync_hidden_transcript_text_from_segments()

        if 0 <= index < len(self.current_translated_segments or []):
            delete_history_entry["translated_before"] = [copy.deepcopy(self.current_translated_segments[index])]
            self.current_translated_segments[index:index + 1] = []
            self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
            self._sync_hidden_translated_text_from_segments()

        self._timeline_timing_undo_stack.append(delete_history_entry)
        self._timeline_timing_redo_stack = []
        if len(self._timeline_timing_undo_stack) > 100:
            self._timeline_timing_undo_stack = self._timeline_timing_undo_stack[-100:]
        self._refresh_timeline_history_buttons()

        self.set_selected_segment_index(target_selection, sync_ui=True)
        if hasattr(self, "timeline"):
            self.timeline.set_active_segment_index(target_selection)
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def on_timeline_segment_timing_changed(self, index: int, start: float, end: float):
        updated = False
        if 0 <= index < len(self.current_segments or []):
            self._apply_segment_timing(self.current_segments[index], start, end)
            self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
            self._sync_hidden_transcript_text_from_segments()
            updated = True
        if 0 <= index < len(self.current_translated_segments or []):
            self._apply_segment_timing(self.current_translated_segments[index], start, end)
            self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
            self._sync_hidden_translated_text_from_segments()
            updated = True
        if not updated:
            return
        self.set_selected_segment_index(index, sync_ui=True)
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def _refresh_timeline_history_buttons(self):
        if hasattr(self, "timeline_undo_btn"):
            self.timeline_undo_btn.setEnabled(bool(self._timeline_timing_undo_stack))
        if hasattr(self, "timeline_redo_btn"):
            self.timeline_redo_btn.setEnabled(bool(self._timeline_timing_redo_stack))

    def undo_last_timeline_timing_edit(self):
        if not self._timeline_timing_undo_stack:
            return False
        entry = self._timeline_timing_undo_stack.pop()
        if str(entry.get("type", "timing")) in {"split", "delete", "batch_timing"}:
            self._apply_timeline_structure_history_entry(entry, use_after=False)
            self._timeline_timing_redo_stack.append(entry)
            self._refresh_timeline_history_buttons()
            return True
        current_entry = None
        active_segments = self.get_active_segments()
        index = int(entry.get("index", -1))
        if 0 <= index < len(active_segments):
            current_entry = {
                "index": index,
                "start": float(active_segments[index].get("start", 0.0)),
                "end": float(active_segments[index].get("end", 0.0)),
            }
        self._suspend_timeline_undo = True
        try:
            self.on_timeline_segment_timing_changed(
                index,
                float(entry.get("start", 0.0)),
                float(entry.get("end", 0.0)),
            )
        finally:
            self._suspend_timeline_undo = False
        if current_entry:
            self._timeline_timing_redo_stack.append(current_entry)
        self._refresh_timeline_history_buttons()
        return True

    def redo_last_timeline_timing_edit(self):
        if not self._timeline_timing_redo_stack:
            return False
        entry = self._timeline_timing_redo_stack.pop()
        if str(entry.get("type", "timing")) in {"split", "delete", "batch_timing"}:
            self._apply_timeline_structure_history_entry(entry, use_after=True)
            self._timeline_timing_undo_stack.append(entry)
            self._refresh_timeline_history_buttons()
            return True
        current_entry = None
        active_segments = self.get_active_segments()
        index = int(entry.get("index", -1))
        if 0 <= index < len(active_segments):
            current_entry = {
                "index": index,
                "start": float(active_segments[index].get("start", 0.0)),
                "end": float(active_segments[index].get("end", 0.0)),
            }
        self._suspend_timeline_undo = True
        try:
            self.on_timeline_segment_timing_changed(
                index,
                float(entry.get("start", 0.0)),
                float(entry.get("end", 0.0)),
            )
        finally:
            self._suspend_timeline_undo = False
        if current_entry:
            self._timeline_timing_undo_stack.append(current_entry)
        self._refresh_timeline_history_buttons()
        return True

    def step_selected_segment(self, direction: int):
        rows = self._segment_editor_display_rows()
        valid_indexes = [int(row.get("segment_index", idx)) for idx, row in enumerate(rows)]
        if not valid_indexes:
            self.set_selected_segment_index(-1)
            return
        current = self._get_effective_selected_segment_index(rows)
        try:
            current_pos = valid_indexes.index(current)
        except ValueError:
            current_pos = 0
        target_pos = max(0, min(len(valid_indexes) - 1, current_pos + int(direction)))
        self.set_selected_segment_index(valid_indexes[target_pos], sync_ui=True)

    def _find_segment_editor_row(self, segment_index: int):
        for row in getattr(self, "_segment_editor_rows", []):
            if int(row.get("segment_index", -1)) == int(segment_index):
                return row
        return None

    def sync_segment_editor_rows(self):
        if not hasattr(self, "segment_editor_layout") or getattr(self, "_syncing_segment_editor", False):
            return

        self._syncing_segment_editor = True
        try:
            self._clear_segment_editor_rows()
            self._segment_editor_rows = []
            rows = self._segment_editor_display_rows()
            if not rows:
                self._selected_segment_index = -1
                if hasattr(self, "segment_selection_label"):
                    self.segment_selection_label.setText("No subtitle selected")
                if hasattr(self, "segment_prev_btn"):
                    self.segment_prev_btn.setEnabled(False)
                if hasattr(self, "segment_next_btn"):
                    self.segment_next_btn.setEnabled(False)
                if hasattr(self, "rewrite_selected_segment_btn"):
                    self.rewrite_selected_segment_btn.setEnabled(False)
                empty_state = QFrame(self.segment_editor_container if hasattr(self, "segment_editor_container") else None)
                empty_state.setObjectName("statusCard")
                empty_state.setMinimumHeight(180)
                empty_state.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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
                self.segment_editor_layout.addWidget(empty_state, 1)
                return

            selected_index = self._get_effective_selected_segment_index(rows)
            visible_rows = [row for row in rows if int(row.get("segment_index", -1)) == selected_index]
            if not visible_rows:
                visible_rows = [rows[0]]
                selected_index = int(visible_rows[0].get("segment_index", 0))
            self._selected_segment_index = selected_index

            if hasattr(self, "segment_selection_label"):
                self.segment_selection_label.setText(f"Block {selected_index + 1} / {len(rows)}")
            if hasattr(self, "segment_prev_btn"):
                self.segment_prev_btn.setEnabled(selected_index > 0)
            if hasattr(self, "segment_next_btn"):
                self.segment_next_btn.setEnabled(selected_index < len(rows) - 1)
            if hasattr(self, "rewrite_selected_segment_btn"):
                self.rewrite_selected_segment_btn.setEnabled(True)

            show_original = bool(getattr(self, "show_original_subtitle_cb", None) and self.show_original_subtitle_cb.isChecked())
            for row in visible_rows:
                idx = int(row.get("segment_index", 0))
                card = QFrame(self.segment_editor_container if hasattr(self, "segment_editor_container") else None)
                card.setObjectName("segmentInspectorCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 12, 12, 12)
                card_layout.setSpacing(6)

                timing_meta_layout = QHBoxLayout()
                timing_meta_layout.setContentsMargins(0, 0, 0, 0)
                timing_meta_layout.setSpacing(12)
                start_label = QLabel(f"Start  {self.format_timestamp(row['start'])}")
                start_label.setObjectName("timingChip")
                end_label = QLabel(f"End  {self.format_timestamp(row['end'])}")
                end_label.setObjectName("timingChip")
                timing_meta_layout.addWidget(start_label)
                timing_meta_layout.addWidget(end_label)
                timing_meta_layout.addStretch()

                original_label = QLabel(row["original"] or "", card)
                original_label.setWordWrap(True)
                original_label.setObjectName("helperLabel")
                original_label.setVisible(show_original and bool(row["original"].strip()))

                arrow_label = QLabel("→", card)
                arrow_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #8ad7ff;")
                translated_editor = QTextEdit(card)
                translated_editor.setObjectName("segmentInspectorEditor")
                translated_editor.setAcceptRichText(False)
                translated_editor.setPlainText(row["translated"])
                translated_editor.setMinimumHeight(96)
                translated_editor.setMaximumHeight(120)
                translated_editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                translated_editor.textChanged.connect(
                    lambda idx=idx, editor=translated_editor: self.on_segment_translation_edited(idx, editor)
                )
                translated_editor.selectionChanged.connect(
                    lambda idx=idx, editor=translated_editor: self._update_segment_highlight_button_state(idx, editor)
                )
                highlight_btn = QPushButton("Add highlight from selection", card)
                highlight_btn.setEnabled(False)
                highlight_btn.clicked.connect(
                    lambda _=False, idx=idx, editor=translated_editor: self.add_segment_manual_highlight(idx, editor)
                )

                highlight_action_layout = QHBoxLayout()
                highlight_action_layout.setContentsMargins(0, 0, 0, 0)
                highlight_action_layout.setSpacing(8)
                highlight_action_layout.addStretch()
                highlight_action_layout.addWidget(highlight_btn)

                highlight_meta_layout = QHBoxLayout()
                highlight_meta_layout.setContentsMargins(0, 0, 0, 0)
                highlight_meta_layout.setSpacing(6)
                highlight_placeholder = QLabel("", card)
                highlight_placeholder.setObjectName("helperLabel")
                highlight_chip_container = QWidget(card)
                highlight_chip_layout = QHBoxLayout(highlight_chip_container)
                highlight_chip_layout.setContentsMargins(0, 0, 0, 0)
                highlight_chip_layout.setSpacing(6)
                highlight_meta_layout.addWidget(highlight_placeholder)
                highlight_meta_layout.addWidget(highlight_chip_container, 1)

                card_layout.addLayout(timing_meta_layout)
                card_layout.addWidget(original_label)
                divider = QFrame(card)
                divider.setFrameShape(QFrame.HLine)
                divider.setStyleSheet("color: #27425d;")
                card_layout.addWidget(divider)
                card_layout.addWidget(translated_editor, 0)
                card_layout.addLayout(highlight_action_layout)
                card_layout.addLayout(highlight_meta_layout)
                for label in card.findChildren(QLabel):
                    if label.text().strip() in {"→", "â†’"}:
                        label.hide()
                self.segment_editor_layout.addWidget(card, 0)
                self._segment_editor_rows.append(
                    {
                        "segment_index": idx,
                        "frame": card,
                        "original_label": original_label,
                        "translated_editor": translated_editor,
                        "highlight_button": highlight_btn,
                        "highlight_placeholder": highlight_placeholder,
                        "highlight_chip_layout": highlight_chip_layout,
                    }
                )
                self._update_segment_highlight_button_state(idx, translated_editor)
                self._sync_segment_highlight_chip_row(idx)

            self._set_segment_editor_highlight(selected_index)
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
                    "tts_text": str(self.current_translated_segments[idx].get("tts_text", base.get("tts_text", "")) or "") if idx < len(self.current_translated_segments) else str(base.get("tts_text", "") or ""),
                    "tts_group_id": self.current_translated_segments[idx].get("tts_group_id", base.get("tts_group_id", "")) if idx < len(self.current_translated_segments) else base.get("tts_group_id", ""),
                    "tts_group_start": float(self.current_translated_segments[idx].get("tts_group_start", base.get("tts_group_start", base.get("start", 0.0))) or base.get("start", 0.0)) if idx < len(self.current_translated_segments) else float(base.get("tts_group_start", base.get("start", 0.0)) or base.get("start", 0.0)),
                    "tts_group_end": float(self.current_translated_segments[idx].get("tts_group_end", base.get("tts_group_end", base.get("end", 0.0))) or base.get("end", 0.0)) if idx < len(self.current_translated_segments) else float(base.get("tts_group_end", base.get("end", 0.0)) or base.get("end", 0.0)),
                    "words": list(base.get("words", [])),
                    "manual_highlights": list(base.get("manual_highlights", [])),
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
        for row in rows:
            row_index = int(row.get("segment_index", -1))
            if row_index == active_index:
                row["frame"].setStyleSheet("QFrame#statusCard { background-color: #153149; border: 1px solid #5fb9ff; border-radius: 14px; }")
                self.segment_editor_scroll.ensureWidgetVisible(row["frame"], 0, 36)
            else:
                row["frame"].setStyleSheet("")

    def play_audio_preview_file(self, audio_path: str):
        if not audio_path or not os.path.exists(audio_path):
            raise FileNotFoundError("Audio preview file was not found.")
        if hasattr(self, "media_player") and self.media_player.is_playing():
            self.media_player.pause()
            if hasattr(self, "timeline"):
                self.timeline.set_playing(False)
            self._refresh_preview_audio_controls()
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

    def toggle_blur_area_editing(self, checked: bool):
        if not hasattr(self, "video_view") or not hasattr(self, "blur_area_btn"):
            return
        has_video = bool(self.video_path_edit.text().strip()) and os.path.exists(self.video_path_edit.text().strip())
        if checked and not has_video:
            self.blur_area_btn.blockSignals(True)
            self.blur_area_btn.setChecked(False)
            self.blur_area_btn.blockSignals(False)
            QMessageBox.warning(self, "Blur Area", "Please load a video before adding a blur area.")
            return
        self.video_view.set_blur_edit_enabled(checked)
        self.apply_preview_blur_region()
        self._refresh_preview_audio_controls()
        if checked:
            self.log("[Blur Area] drag inside the video preview to move or resize the region.")

    def apply_preview_blur_region(self):
        if not hasattr(self, "media_player") or not hasattr(self, "video_view"):
            return
        if hasattr(self, "blur_area_btn") and self.blur_area_btn.isChecked():
            self.media_player.set_blur_region(self.video_view.get_blur_region_normalized())
        else:
            self.media_player.clear_blur_region()

    def _resolve_voice_preview_source(self, entry: dict) -> QUrl:
        preview_path = str(entry.get("preview_video_path", "")).strip()
        preview_url = str(entry.get("preview_video_url", "")).strip()
        preview_audio_path = str(entry.get("preview_audio_path", "")).strip()
        preview_audio_url = str(entry.get("preview_audio_url", "")).strip()

        if preview_path:
            if not os.path.isabs(preview_path):
                preview_path = os.path.join(self.workspace_root, preview_path)
            if not os.path.exists(preview_path):
                raise FileNotFoundError("The configured preview video file was not found.")
            return QUrl.fromLocalFile(preview_path)
        if preview_url:
            return QUrl(preview_url)
        if preview_audio_path:
            if not os.path.isabs(preview_audio_path):
                preview_audio_path = os.path.join(self.workspace_root, preview_audio_path)
            if not os.path.exists(preview_audio_path):
                raise FileNotFoundError("The configured preview audio file was not found.")
            return QUrl.fromLocalFile(preview_audio_path)
        if preview_audio_url:
            return QUrl(preview_audio_url)
        raise RuntimeError("This voice does not have preview media configured yet.")

    def _stop_voice_library_preview(self):
        try:
            self.voice_preview_library_player.stop()
            self.voice_preview_library_player.setSource(QUrl())
        except Exception:
            pass
        for button in self._voice_preview_row_buttons.values():
            button.setText("Preview")

    def _play_voice_preview_entry(self, entry: dict, button: QPushButton | None = None):
        try:
            source = self._resolve_voice_preview_source(entry)
            self._stop_voice_library_preview()
            self.voice_preview_library_player.setSource(source)
            self.voice_preview_library_player.play()
            if button is not None:
                button.setText("Playing...")
            self.log(f"[Voice Preview] playing clip for {entry.get('name', 'voice')}")
        except Exception as exc:
            self.show_error("Voice Preview Failed", "Could not play the selected voice preview clip.", str(exc))

    def _build_voice_preview_popup(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Voice Preview Library")
        dialog.setModal(False)
        dialog.resize(720, 560)
        dialog.setStyleSheet(
            """
            QDialog {
                background-color: #0f1724;
            }
            QWidget {
                background-color: #0f1724;
                color: #dbe5f3;
            }
            QScrollArea {
                border: none;
                background-color: #0f1724;
            }
            QLabel#statusHeadline {
                color: #f8fbff;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#sectionTitle {
                color: #8ad7ff;
                font-weight: 700;
            }
            QLabel#helperLabel {
                color: #9fb3ca;
            }
            QFrame#statusCard {
                background-color: #132033;
                border: 1px solid #2f4868;
                border-radius: 12px;
            }
            QPushButton {
                background-color: #22344d;
                color: #f8fbff;
                border: 1px solid #34506f;
                border-radius: 10px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #29405d;
            }
            QPushButton:disabled {
                background-color: #172435;
                color: #7f92a9;
                border-color: #24384f;
            }
            """
        )

        root_layout = QVBoxLayout(dialog)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        title = QLabel("Voice Preview Library", dialog)
        title.setObjectName("statusHeadline")
        root_layout.addWidget(title)

        hint = QLabel(
            "Preview each configured voice sample here. This popup uses a separate player and does not affect the main video timeline.",
            dialog,
        )
        hint.setObjectName("helperLabel")
        hint.setWordWrap(True)
        root_layout.addWidget(hint)

        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget(scroll)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        current_provider = None
        self._voice_preview_row_buttons = {}
        entries = sorted(
            list(self.voice_catalog_entries_all or []),
            key=lambda item: (
                str(item.get("tier", "")),
                self._voice_provider_label(str(item.get("provider", ""))),
                str(item.get("name", "")),
            ),
        )
        for entry in entries:
            provider = self._voice_provider_label(str(entry.get("provider", "")).strip())
            if provider != current_provider:
                current_provider = provider
                header = QLabel(provider, container)
                header.setObjectName("sectionTitle")
                layout.addWidget(header)

            row = QFrame(container)
            row.setObjectName("statusCard")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(10)

            label = QLabel(str(entry.get("name", entry.get("id", "Voice"))), row)
            label.setWordWrap(True)
            meta = QLabel(str(entry.get("tier", "voice")).strip().title(), row)
            meta.setObjectName("helperLabel")
            preview_btn = QPushButton("Preview", row)
            preview_btn.setEnabled(self._entry_has_preview_media(entry))
            preview_btn.clicked.connect(lambda _checked=False, item=entry, btn=preview_btn: self._play_voice_preview_entry(item, btn))

            row_layout.addWidget(label, 1)
            row_layout.addWidget(meta)
            row_layout.addWidget(preview_btn)
            layout.addWidget(row)
            self._voice_preview_row_buttons[str(entry.get("id", ""))] = preview_btn

        layout.addStretch()
        scroll.setWidget(container)
        root_layout.addWidget(scroll, 1)

        close_btn = QPushButton("Close", dialog)
        close_btn.clicked.connect(dialog.close)
        root_layout.addWidget(close_btn, 0, Qt.AlignRight)

        dialog.finished.connect(lambda _result: self._stop_voice_library_preview())
        self.voice_preview_dialog = dialog
        return dialog

    def preview_selected_voice_sample(self):
        if not (self.voice_catalog_entries or []):
            QMessageBox.information(self, "Preview voice", "No local voices are available yet. Please add Piper models to models/piper first.")
            return

        if not self.ensure_required_resources("Voice preview", include_voice=True):
            return

        if self._voice_sample_preview_thread is not None:
            QMessageBox.information(self, "Preview voice", "A preview is already being generated. Please wait a moment.")
            return

        voice_name = self.get_active_voice_name()
        voice_speed = self._parse_voice_speed_value()
        text = "Chào bạn, đây là bản xem trước giọng nói của mẫu được chọn."  # "Hello, this is a preview of the selected voice sample." in Vietnamese

        if hasattr(self, "preview_voice_btn"):
            self.preview_voice_btn.setEnabled(False)
            self.preview_voice_btn.setText("...")

        worker = VoiceSamplePreviewWorker(
            self.workspace_root,
            text,
            voice_name,
            voice_speed,
            temp_dir=self.get_project_temp_dir("voice_sample_preview"),
        )
        worker.finished.connect(self.on_voice_sample_preview_ready)
        self._voice_sample_preview_thread = worker
        worker.start()

    def on_voice_sample_preview_ready(self, audio_path: str, error: str):
        if hasattr(self, "preview_voice_btn"):
            self.preview_voice_btn.setEnabled(True)
            self.preview_voice_btn.setText("Preview voice")
        self._voice_sample_preview_thread = None

        if error:
            self.show_error("Voice Preview Failed", "Could not generate the preview audio.", error)
            return
        if not audio_path:
            self.show_error("Voice Preview Failed", "Preview audio path is missing.", "")
            return

        try:
            self.play_audio_preview_file(audio_path)
            self.log(f"[Voice Preview] playing generated sample: {audio_path}")
        except Exception as exc:
            self.show_error("Voice Preview Failed", "Could not play the generated preview audio.", str(exc))

    def preview_segment_audio(self, index: int):
        if index < 0 or index >= len(self.current_translated_segments or self.current_segments):
            QMessageBox.warning(self, "Missing Subtitle", "This subtitle line is not ready yet.")
            return

        if not self.ensure_required_resources("Subtitle audio preview", include_voice=True):
            return

        source_segments = self.current_translated_segments or self.current_segments
        text = str(source_segments[index].get("tts_text") or source_segments[index].get("text", "")).strip()
        if not text:
            QMessageBox.warning(self, "Missing Subtitle", "This subtitle line is empty.")
            return

        voice_name = self.get_active_voice_name()
        voice_speed = self._parse_voice_speed_value()
        row = self._find_segment_editor_row(index)
        if row:
            row["preview_button"].setEnabled(False)
            row["preview_button"].setText("...")

        worker = SegmentAudioPreviewWorker(
            self.workspace_root,
            index,
            text,
            voice_name,
            voice_speed,
            temp_dir=self.get_project_temp_dir("segment_audio_preview"),
        )
        worker.finished.connect(self.on_segment_audio_preview_ready)
        self._segment_preview_threads[index] = worker
        worker.start()

    def on_segment_audio_preview_ready(self, index: int, audio_path: str, error: str):
        row = self._find_segment_editor_row(index)
        if row:
            row["preview_button"].setEnabled(True)
            row["preview_button"].setIcon(load_icon(asset_path("icons", "audio_preview.svg"), 18))
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

    def import_translated_srt(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Vietnamese Subtitle",
            self.srt_output_folder_edit.text().strip() or self.workspace_root,
            "Subtitle Files (*.srt)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8-sig") as handle:
                srt_text = handle.read().strip()
        except Exception as exc:
            self.show_error("Import Failed", "Could not read the selected subtitle file.", str(exc))
            return

        if not srt_text:
            QMessageBox.warning(self, "Import Failed", "The selected subtitle file is empty.")
            return

        imported_segments = self.parse_srt_to_segments(srt_text)
        if not imported_segments:
            QMessageBox.warning(self, "Import Failed", "The selected file could not be parsed as a valid SRT subtitle.")
            return

        base_segments = self.current_segments or self.current_translated_segments
        if self.keep_timeline_cb.isChecked() and base_segments and len(base_segments) == len(imported_segments):
            merged_segments = []
            for idx, base in enumerate(base_segments):
                merged = dict(imported_segments[idx])
                merged["start"] = float(base.get("start", 0.0))
                merged["end"] = float(base.get("end", 0.0))
                merged["words"] = list(base.get("words", []))
                if "manual_highlights" in imported_segments[idx]:
                    merged["manual_highlights"] = imported_segments[idx]["manual_highlights"]
                elif base.get("manual_highlights"):
                    merged["manual_highlights"] = list(base.get("manual_highlights", []))
                merged_segments.append(merged)
            imported_segments = merged_segments
            srt_text = self.format_to_srt(imported_segments)

        self.translated_text.setText(srt_text)
        self.apply_edited_translation(show_message=False, force_apply=True)
        self.last_translated_srt_path = file_path
        self.processed_artifacts["srt_translated"] = file_path
        self.persist_translation_project_data(self.current_translated_segments, file_path)
        self.refresh_ui_state()
        QMessageBox.information(self, "Imported", f"Vietnamese subtitle was loaded into the editor.\n\n{file_path}")

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
        self.schedule_timeline_visual_refresh(waveform=True, thumbnails=True)
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
                        "tts_text": str(base.get("tts_text", "") or ""),
                        "tts_group_id": base.get("tts_group_id", ""),
                        "tts_group_start": float(base.get("tts_group_start", base.get("start", 0.0)) or 0.0),
                        "tts_group_end": float(base.get("tts_group_end", base.get("end", 0.0)) or 0.0),
                        "words": list(base.get("words", [])),
                        "manual_highlights": list(base.get("manual_highlights", [])),
                    }
                    for idx, base in enumerate(base_segments)
                ]

        parsed_segments = self.parse_srt_to_segments(srt_text)
        if base_segments and len(parsed_segments) == len(base_segments):
            for idx, segment in enumerate(parsed_segments):
                base = base_segments[idx]
                segment["words"] = list(base.get("words", []))
                segment["manual_highlights"] = list(base.get("manual_highlights", []))
                if base.get("tts_text"):
                    segment["tts_text"] = str(base.get("tts_text", "") or "")
                    segment["tts_group_id"] = base.get("tts_group_id", "")
                    segment["tts_group_start"] = float(base.get("tts_group_start", base.get("start", 0.0)) or 0.0)
                    segment["tts_group_end"] = float(base.get("tts_group_end", base.get("end", 0.0)) or 0.0)
        return parsed_segments

    def _write_live_preview_assets(self, segments):
        if not segments:
            self.live_preview_subtitle_path = ""
            self.live_preview_ass_path = ""
            self._live_preview_signature = None
            return "", ""

        preview_dir = self.get_project_temp_dir("preview")
        preview_srt_path = os.path.join(preview_dir, "live_preview_subtitle.srt")

        from subtitle_builder import generate_srt

        video_path = self.video_path_edit.text().strip()
        if (
            video_path
            and os.path.exists(video_path)
            and (
                not getattr(self.video_view, "video_source_width", 0)
                or not getattr(self.video_view, "video_source_height", 0)
            )
        ):
            self.refresh_video_dimensions(video_path)
        video_width = getattr(self.video_view, "video_source_width", 0) or 1920
        video_height = getattr(self.video_view, "video_source_height", 0) or 1080
        subtitle_style = self.get_subtitle_export_style(segments=segments)
        preview_signature = (
            video_path,
            video_width,
            video_height,
            repr(segments),
            repr(subtitle_style),
        )
        if (
            preview_signature == getattr(self, "_live_preview_signature", None)
            and self.live_preview_subtitle_path
            and os.path.exists(self.live_preview_subtitle_path)
            and self.live_preview_ass_path
            and os.path.exists(self.live_preview_ass_path)
        ):
            return self.live_preview_subtitle_path, self.live_preview_ass_path

        # Subtitle or content changed. We no longer revert the media source!
        # Because we'll disable burned-in subs in muxed previews, the rendered
        # background is already blank-subbed and can host our live overlay/mpv track comfortably.
        # This solves the user's complaint that 'it reverts to original'.

        generate_srt(segments, preview_srt_path)
        self.live_preview_subtitle_path = preview_srt_path
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
            word_timings=subtitle_style.get("word_timings", []),
            custom_position_enabled=subtitle_style.get("custom_position_enabled", False),
            custom_position_x=subtitle_style.get("custom_position_x", 50),
            custom_position_y=subtitle_style.get("custom_position_y", 86),
            single_line=subtitle_style.get("single_line", False),
        )
        self._live_preview_signature = preview_signature
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
            if active_index >= 0 and active_index != getattr(self, "_selected_segment_index", -1):
                self.set_selected_segment_index(active_index, sync_ui=True)

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

            # Update live overlay text for faster feedback
            if hasattr(self, "video_view"):
                if getattr(self, "_preview_video_has_burned_subtitles", False):
                    self.video_view.subtitle_item.set_text("")
                    self.video_view.subtitle_item.hide()
                elif 0 <= active_index < len(segments):
                    self.video_view.subtitle_item.set_text(segments[active_index].get("text", ""))
                    self.video_view.subtitle_item.show()
                else:
                    self.video_view.subtitle_item.set_text("")
                    # Don't necessarily hide if we want to show the placeholder during style editing
                    # but for now let's hide if no segment is active during playback
                    if not self.media_player.is_playing():
                         self.video_view.subtitle_item.show() # Show placeholder
                    else:
                         self.video_view.subtitle_item.hide()
                self.video_view.reposition_subtitle()
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
        has_subtitle_track = bool(self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path))
        mode = self.get_output_mode_key()
        steps = getattr(getattr(self, "current_project_state", None), "steps", {}) or {}
        voice_running = steps.get("generate_tts") == "running" or steps.get("mix_audio") == "running"
        can_export = False
        if mode == "subtitle":
            can_export = v_ok and has_subtitle_track
        elif mode == "voice":
            can_export = v_ok and has_voice_audio
        else:
            can_export = (
                v_ok
                and has_voice_audio
                and has_subtitle_track
            )

        self.extract_btn.setEnabled(v_ok)
        self.vocal_sep_btn.setEnabled(a_ok)
        self.transcribe_btn.setEnabled(a_ok)
        self.translate_btn.setEnabled(bool(self.transcript_text.toPlainText().strip()))
        self.apply_translated_btn.setEnabled(has_translated_text)
        if hasattr(self, "rewrite_translation_btn"):
            self.rewrite_translation_btn.setEnabled(bool(self.transcript_text.toPlainText().strip()) and has_translated_text)
        if hasattr(self, "rewrite_selected_segment_btn"):
            has_selected_segment = 0 <= int(getattr(self, "_selected_segment_index", -1)) < len(self.current_translated_segments or [])
            self.rewrite_selected_segment_btn.setEnabled(
                bool(self.transcript_text.toPlainText().strip()) and has_translated_text and has_selected_segment
            )
        generated_mode = not self.using_existing_audio_source()
        self.voiceover_btn.setEnabled(has_translated_text and generated_mode and mode in ("voice", "both"))
        preview_enabled = v_ok and not voice_running
        if hasattr(self, "quick_preview_btn"):
            self.quick_preview_btn.setEnabled(preview_enabled)
        if hasattr(self, "styled_preview_btn"):
            self.styled_preview_btn.setEnabled(preview_enabled)
        if hasattr(self, "preview_btn"):
            self.preview_btn.setVisible(True)
            self.preview_btn.setEnabled(preview_enabled and not getattr(self, "_styled_preview_running", False))
        if hasattr(self, "video_filter_apply_btn"):
            has_active_filters = self.has_active_video_filters() if hasattr(self, "has_active_video_filters") else False
            self.video_filter_apply_btn.setVisible(True)
            self.video_filter_apply_btn.setEnabled(
                self.is_filter_workflow_active()
                and v_ok
                and has_active_filters
                and not getattr(self, "_styled_preview_running", False)
            )
            self.video_filter_apply_btn.setText("Applying..." if getattr(self, "_video_filter_apply_requested", False) and getattr(self, "_styled_preview_running", False) else "Apply Filter")
        is_rendering_filter_preview = bool(getattr(self, "_video_filter_apply_requested", False) and getattr(self, "_styled_preview_running", False))
        if hasattr(self, "video_filter_render_status_label"):
            status_text = ""
            if not self.is_filter_workflow_active():
                status_text = ""
            elif getattr(self, "_video_filter_apply_requested", False) and getattr(self, "_styled_preview_running", False):
                status_text = "Rendering filtered preview video..."
            elif getattr(self, "_video_filter_preview_dirty", False):
                status_text = "Filter changes pending. Click Apply Filter to render motion preview."
            elif self.has_active_video_filters() if hasattr(self, "has_active_video_filters") else False:
                status_text = "Filtered preview video is ready."
            self.video_filter_render_status_label.setText(status_text)
            self.video_filter_render_status_label.setVisible(bool(status_text))
        if hasattr(self, "video_filter_render_progress"):
            self.video_filter_render_progress.setVisible(self.is_filter_workflow_active() and is_rendering_filter_preview)
        if hasattr(self, "reset_framing_btn"):
            scale_mode = self.get_output_scale_mode_key() if hasattr(self, "get_output_scale_mode_key") else "fit"
            focus_x, focus_y = self.get_output_fill_focus() if hasattr(self, "get_output_fill_focus") else (0.5, 0.5)
            framing_dirty = abs(float(focus_x) - 0.5) > 0.001 or abs(float(focus_y) - 0.5) > 0.001
            self.reset_framing_btn.setVisible(True)
            self.reset_framing_btn.setEnabled(v_ok and scale_mode == "fill" and framing_dirty)
        if hasattr(self, "play_btn"):
            self.play_btn.setEnabled(v_ok and not voice_running and not getattr(self, "_styled_preview_running", False))
        if hasattr(self, "stop_btn"):
            self.stop_btn.setEnabled(v_ok and not voice_running)
        if hasattr(self, "blur_area_btn"):
            self.blur_area_btn.setEnabled(v_ok)
        if hasattr(self, "free_voice_combo"):
            self.free_voice_combo.setEnabled(
                generated_mode
                and mode in ("voice", "both")
            )
        if hasattr(self, "premium_voice_combo"):
            self.premium_voice_combo.setEnabled(False)
        if hasattr(self, "bg_music_edit"):
            self.bg_music_edit.setEnabled(generated_mode and mode in ("voice", "both"))
        if hasattr(self, "audio_handling_combo"):
            self.audio_handling_combo.setEnabled(generated_mode and mode in ("voice", "both"))
        if hasattr(self, "mixed_audio_edit"):
            self.mixed_audio_edit.setEnabled(mode in ("voice", "both") and bool(hasattr(self, "use_existing_audio_radio") and self.use_existing_audio_radio.isChecked()))
        if hasattr(self, "preview_voice_btn"):
            self.preview_voice_btn.setVisible(mode in ("voice", "both"))
            self.preview_voice_btn.setEnabled(bool(self.voice_catalog_entries_all))
        has_timeline_segments = bool(self.get_active_segments())
        if hasattr(self, "timeline_split_btn"):
            self.timeline_split_btn.setEnabled(has_timeline_segments)
        if hasattr(self, "timeline_delete_btn"):
            self.timeline_delete_btn.setEnabled(has_timeline_segments)
        if hasattr(self, "timeline_nudge_left_btn"):
            self.timeline_nudge_left_btn.setEnabled(has_timeline_segments)
        if hasattr(self, "timeline_nudge_right_btn"):
            self.timeline_nudge_right_btn.setEnabled(has_timeline_segments)
        if hasattr(self, "timeline_ripple_left_btn"):
            self.timeline_ripple_left_btn.setEnabled(has_timeline_segments)
        if hasattr(self, "timeline_ripple_right_btn"):
            self.timeline_ripple_right_btn.setEnabled(has_timeline_segments)
        if hasattr(self, "clean_project_action"):
            self.clean_project_action.setEnabled(self._has_cleanable_project_data())
        self.run_all_btn.setEnabled(v_ok and not self._pipeline_active)
        self.preview_frame_btn.setEnabled(v_ok and bool(self.get_active_segments()))
        self.preview_5s_btn.setEnabled(v_ok)
        self.export_btn.setEnabled(can_export)
        if hasattr(self, "download_subtitle_action"):
            self.download_subtitle_action.setEnabled(bool(self.translated_text.toPlainText().strip()))
        if hasattr(self, "download_original_action"):
            self.download_original_action.setEnabled(bool(self.transcript_text.toPlainText().strip()))
        if hasattr(self, "tabs"):
            self.tabs.setTabEnabled(1, v_ok)
            self.tabs.setTabEnabled(2, v_ok and mode in ("voice", "both"))
        self.update_workflow_availability()
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
                self.ensure_media_backend_ready()
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
            self.update_project_step("separate_audio", "done")
            QMessageBox.information(self, "Success", 
                f"Audio stems separated!\n\nVocals: {os.path.basename(vocal)}\nBackground: {os.path.basename(music)}\n\nVocals are now selected for transcription.")
            self._pipeline_advance("separation")
        else:
            self.update_project_step("separate_audio", "failed")
            self._pipeline_fail("Separation did not produce output.")
        self.refresh_ui_state()

    def run_transcription(self):
        if not self.ensure_required_resources("Transcription", include_whisper=True):
            return
        self.subtitle_controller.run_transcription()

    def on_transcription_finished(self, segments, error=""):
        self.subtitle_controller.on_transcription_finished(segments, error)

    def run_translation(self):
        self.subtitle_controller.run_translation()

    def on_translation_finished(self, translated_srt, error):
        self.subtitle_controller.on_translation_finished(translated_srt, error)

    def run_rewrite_translation(self):
        self.subtitle_controller.run_rewrite_translation()

    def run_rewrite_selected_segment(self):
        self.subtitle_controller.run_rewrite_selected_segment()

    def on_rewrite_translation_finished(self, translated_srt, error):
        self.subtitle_controller.on_rewrite_translation_finished(translated_srt, error)

    def on_rewrite_selected_segment_finished(self, translated_srt, error):
        self.subtitle_controller.on_rewrite_selected_segment_finished(translated_srt, error)

    def _close_export_progress_dialog(self):
        try:
            dlg = getattr(self, "export_progress_dialog", None)
            if dlg is not None:
                self._unregister_progress_dialog(dlg)
                dlg.hide()
                dlg.deleteLater()
        finally:
            self.export_progress_dialog = None

    def _ensure_export_progress_dialog(self):
        dlg = getattr(self, "export_progress_dialog", None)
        if dlg is not None:
            return dlg
        dlg = BackgroundableProgressDialog("Preparing final export...", "Hide", 0, 100, self)
        dlg.setWindowTitle("Exporting Video")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoReset(False)
        dlg.setAutoClose(False)
        dlg.setMinimumWidth(520)
        dlg.setValue(0)
        dlg.setLabelText("Exporting final video...\n\nWaiting to start...")
        dlg.setStyleSheet(
            "QProgressDialog { background-color: #101826; color: #e6eef9; }"
            "QLabel { color: #e6eef9; background: transparent; }"
            "QPushButton { background-color: #24364f; color: #ffffff; border: 1px solid #335171; border-radius: 10px; padding: 8px 14px; font-weight: 700; }"
            "QPushButton:hover { background-color: #2d4665; border-color: #4575a8; }"
            "QProgressBar { border: 1px solid #2a3a50; border-radius: 10px; text-align: center; background-color: #111927; color: white; min-height: 16px; }"
            "QProgressBar::chunk { background-color: #4ed0b3; border-radius: 10px; }"
        )
        try:
            dlg.setCancelButtonText("Run in background")
            dlg.canceled.connect(dlg.hide)
        except Exception:
            pass
        self.export_progress_dialog = dlg
        self._register_progress_dialog(dlg)
        dlg.show()
        return dlg

    def on_export_progress(self, percent: int, message: str):
        dlg = self._ensure_export_progress_dialog()
        if dlg is None:
            return
        message_text = str(message or "Exporting final video...").strip() or "Exporting final video..."
        history = list(getattr(self, "_export_progress_messages", []) or [])
        if not history or history[-1] != message_text:
            history.append(message_text)
        self._export_progress_messages = history[-4:]
        dlg.setLabelText("Exporting final video...\n\n" + "\n".join(self._export_progress_messages))
        if percent is None or int(percent) < 0:
            dlg.setRange(0, 0)
        else:
            if dlg.maximum() == 0:
                dlg.setRange(0, 100)
            value = max(0, min(100, int(percent)))
            dlg.setValue(value)
            try:
                self.progress_bar.setValue(value)
            except Exception:
                pass
        dlg.show()

    def get_whisper_model_name(self) -> str:
        value = str(getattr(self, "selected_whisper_model_name", "base") or "base").strip().lower()
        return value if value in {"base", "medium"} else "base"

    def get_whisper_model_path(self) -> str:
        mapping = {
            "base": os.path.join(self.workspace_root, "models", "ggml-base.bin"),
            "medium": os.path.join(self.workspace_root, "models", "ggml-medium.bin"),
        }
        return mapping.get(self.get_whisper_model_name(), mapping["base"])

    def open_model_settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.setModal(True)
        dialog.setMinimumWidth(450)
        dialog.setStyleSheet(
            """
            QDialog {
                background-color: #0f1724;
            }
            QLabel {
                color: #d7e3f4;
                background: transparent;
            }
            QLabel#statusHeadline {
                color: #f8fbff;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#helperLabel {
                color: #9fb3ca;
                font-size: 12px;
            }
            QComboBox, QLineEdit {
                background-color: #132033;
                color: #f8fbff;
                border: 1px solid #2f4868;
                border-radius: 10px;
                padding: 8px 10px;
                min-height: 18px;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QComboBox QAbstractItemView {
                background-color: #132033;
                color: #f8fbff;
                border: 1px solid #2f4868;
                selection-background-color: #24486c;
                selection-color: #ffffff;
            }
            QPushButton {
                background-color: #22344d;
                color: #f8fbff;
                border: 1px solid #34506f;
                border-radius: 10px;
                padding: 8px 16px;
                font-weight: 600;
                min-width: 84px;
            }
            QPushButton:hover {
                background-color: #29405d;
            }
            QPushButton:pressed {
                background-color: #1d2d42;
            }
            """
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        remote_mode = is_remote_profile()
        LocalPolisherProvider = self._local_polisher_provider_cls() if not remote_mode else None
        # Whisper Section
        whisper_title = QLabel("Whisper model")
        whisper_title.setObjectName("statusHeadline")
        layout.addWidget(whisper_title)
        
        whisper_combo = QComboBox(dialog)
        whisper_combo.addItem("Base (Faster)", "base")
        whisper_combo.addItem("Medium (Accurate)", "medium")
        current_index = whisper_combo.findData(self.get_whisper_model_name())
        if current_index >= 0:
            whisper_combo.setCurrentIndex(current_index)
        layout.addWidget(whisper_combo)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #2f4868;")
        layout.addWidget(divider)

        remote_title = QLabel("Remote API")
        remote_title.setObjectName("statusHeadline")
        remote_title.setVisible(remote_mode)
        layout.addWidget(remote_title)

        remote_url_layout = QVBoxLayout()
        remote_url_label = QLabel("PC API URL:")
        remote_url_edit = QLineEdit(dialog)
        remote_url_edit.setText(os.getenv("CAPCAP_REMOTE_API_URL", "http://127.0.0.1:8765"))
        remote_url_layout.addWidget(remote_url_label)
        remote_url_layout.addWidget(remote_url_edit)
        remote_url_label.setVisible(remote_mode)
        remote_url_edit.setVisible(remote_mode)
        layout.addLayout(remote_url_layout)

        remote_token_layout = QVBoxLayout()
        remote_token_label = QLabel("API Token (optional):")
        remote_token_edit = QLineEdit(dialog)
        remote_token_edit.setEchoMode(QLineEdit.Password)
        remote_token_edit.setText(os.getenv("CAPCAP_REMOTE_API_TOKEN", ""))
        remote_token_layout.addWidget(remote_token_label)
        remote_token_layout.addWidget(remote_token_edit)
        remote_token_label.setVisible(remote_mode)
        remote_token_edit.setVisible(remote_mode)
        layout.addLayout(remote_token_layout)

        remote_actions_layout = QHBoxLayout()
        test_remote_btn = QPushButton("Test Connection", dialog)
        test_remote_btn.setVisible(remote_mode)
        remote_actions_layout.addWidget(test_remote_btn)
        remote_actions_layout.addStretch()
        layout.addLayout(remote_actions_layout)

        remote_hint_label = QLabel(
            "Remote mode keeps Whisper and AI translation on your PC server. "
            "This laptop build only sends extracted audio and subtitle segments over HTTP."
        )
        remote_hint_label.setObjectName("helperLabel")
        remote_hint_label.setWordWrap(True)
        remote_hint_label.setVisible(remote_mode)
        layout.addWidget(remote_hint_label)

        remote_divider = QFrame()
        remote_divider.setFrameShape(QFrame.HLine)
        remote_divider.setStyleSheet("color: #2f4868;")
        remote_divider.setVisible(remote_mode)
        layout.addWidget(remote_divider)

        # AI Translation Section
        ai_title = QLabel("AI Polish Settings")
        ai_title.setObjectName("statusHeadline")
        ai_title.setVisible(not remote_mode)
        layout.addWidget(ai_title)

        provider_layout = QHBoxLayout()
        provider_label = QLabel("Provider:")
        provider_label.setVisible(not remote_mode)
        provider_layout.addWidget(provider_label)
        provider_combo = QComboBox(dialog)
        provider_combo.addItem("Local (GGUF)", "local")
        provider_combo.addItem("Gemini", "gemini")
        current_provider = (os.getenv("AI_POLISHER_PROVIDER") or "local").strip().lower()
        if current_provider not in {"local", "gemini"}:
            current_provider = "local"
        current_provider_index = provider_combo.findData(current_provider)
        if current_provider_index >= 0:
            provider_combo.setCurrentIndex(current_provider_index)
        provider_combo.setVisible(not remote_mode)
        provider_layout.addWidget(provider_combo, 1)
        layout.addLayout(provider_layout)

        key_section_widget = QWidget(dialog)
        key_layout = QVBoxLayout(key_section_widget)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_label = QLabel("API Key:")
        key_edit = QLineEdit(dialog)
        key_edit.setEchoMode(QLineEdit.Password)
        key_layout.addWidget(key_label)
        key_layout.addWidget(key_edit)
        key_section_widget.setVisible(not remote_mode)
        layout.addWidget(key_section_widget)

        model_layout = QVBoxLayout()
        model_label = QLabel("AI Model:")
        model_edit = QLineEdit(dialog)
        model_layout.addWidget(model_label)
        model_layout.addWidget(model_edit)
        model_label.setVisible(not remote_mode)
        model_edit.setVisible(not remote_mode)
        layout.addLayout(model_layout)

        local_actions_layout = QHBoxLayout()
        browse_model_btn = QPushButton("Browse Model", dialog)
        open_models_folder_btn = QPushButton("Open Models Folder", dialog)
        open_gpu_pack_folder_btn = QPushButton("Open GPU Pack Folder", dialog)
        local_actions_layout.addWidget(browse_model_btn)
        local_actions_layout.addWidget(open_models_folder_btn)
        local_actions_layout.addWidget(open_gpu_pack_folder_btn)
        browse_model_btn.setVisible(not remote_mode)
        open_models_folder_btn.setVisible(not remote_mode)
        open_gpu_pack_folder_btn.setVisible(not remote_mode)
        layout.addLayout(local_actions_layout)

        local_download_layout = QHBoxLayout()
        manage_resources_btn = QPushButton("Manage Resources", dialog)
        open_voices_folder_btn = QPushButton("Open Voices Folder", dialog)
        local_download_layout.addWidget(manage_resources_btn)
        local_download_layout.addWidget(open_voices_folder_btn)
        manage_resources_btn.setVisible(not remote_mode)
        open_voices_folder_btn.setVisible(not remote_mode)
        layout.addLayout(local_download_layout)

        provider_hint = QLabel("")
        provider_hint.setObjectName("helperLabel")
        provider_hint.setWordWrap(True)
        provider_hint.setVisible(not remote_mode)
        layout.addWidget(provider_hint)

        local_status_label = QLabel("")
        local_status_label.setObjectName("helperLabel")
        local_status_label.setWordWrap(True)
        local_status_label.setVisible(not remote_mode)
        layout.addWidget(local_status_label)

        local_model_status_label = QLabel("")
        local_model_status_label.setObjectName("helperLabel")
        local_model_status_label.setWordWrap(True)
        local_model_status_label.setVisible(not remote_mode)
        layout.addWidget(local_model_status_label)

        local_gpu_pack_status_label = QLabel("")
        local_gpu_pack_status_label.setObjectName("helperLabel")
        local_gpu_pack_status_label.setWordWrap(True)
        local_gpu_pack_status_label.setVisible(not remote_mode)
        layout.addWidget(local_gpu_pack_status_label)

        local_voices_status_label = QLabel("")
        local_voices_status_label.setObjectName("helperLabel")
        local_voices_status_label.setWordWrap(True)
        local_voices_status_label.setVisible(not remote_mode)
        layout.addWidget(local_voices_status_label)

        local_perf_row_1_widget = QWidget(dialog)
        local_perf_row_1 = QHBoxLayout(local_perf_row_1_widget)
        local_perf_row_1.setContentsMargins(0, 0, 0, 0)
        local_context_label = QLabel("Context:")
        local_threads_label = QLabel("Threads:")
        local_n_ctx_edit = QLineEdit(dialog)
        local_n_ctx_edit.setPlaceholderText("Context")
        local_threads_edit = QLineEdit(dialog)
        local_threads_edit.setPlaceholderText("Threads")
        local_perf_row_1.addWidget(local_context_label)
        local_perf_row_1.addWidget(local_n_ctx_edit)
        local_perf_row_1.addWidget(local_threads_label)
        local_perf_row_1.addWidget(local_threads_edit)
        local_perf_row_1_widget.setVisible(not remote_mode)
        layout.addWidget(local_perf_row_1_widget)

        local_perf_row_2_widget = QWidget(dialog)
        local_perf_row_2 = QHBoxLayout(local_perf_row_2_widget)
        local_perf_row_2.setContentsMargins(0, 0, 0, 0)
        local_gpu_layers_label = QLabel("GPU Layers:")
        local_batch_label = QLabel("Batch:")
        local_gpu_layers_edit = QLineEdit(dialog)
        local_gpu_layers_edit.setPlaceholderText("GPU layers")
        local_batch_edit = QLineEdit(dialog)
        local_batch_edit.setPlaceholderText("Batch")
        local_perf_row_2.addWidget(local_gpu_layers_label)
        local_perf_row_2.addWidget(local_gpu_layers_edit)
        local_perf_row_2.addWidget(local_batch_label)
        local_perf_row_2.addWidget(local_batch_edit)
        local_perf_row_2_widget.setVisible(not remote_mode)
        layout.addWidget(local_perf_row_2_widget)

        local_perf_row_3_widget = QWidget(dialog)
        local_perf_row_3 = QHBoxLayout(local_perf_row_3_widget)
        local_perf_row_3.setContentsMargins(0, 0, 0, 0)
        local_ubatch_label = QLabel("Ubatch:")
        local_ubatch_edit = QLineEdit(dialog)
        local_ubatch_edit.setPlaceholderText("Ubatch")
        local_flash_attn_cb = QCheckBox("GPU Speed Boost", dialog)
        local_flash_attn_cb.setToolTip("Use a faster GPU attention mode when supported by the current local AI backend.")
        local_auto_optimize_btn = QPushButton("Auto Optimize", dialog)
        local_perf_row_3.addWidget(local_ubatch_label)
        local_perf_row_3.addWidget(local_ubatch_edit)
        local_perf_row_3.addWidget(local_flash_attn_cb)
        local_perf_row_3.addWidget(local_auto_optimize_btn)
        local_perf_row_3_widget.setVisible(not remote_mode)
        layout.addWidget(local_perf_row_3_widget)

        def _default_local_model_path() -> str:
            env_value = os.getenv("LOCAL_TRANSLATOR_MODEL_PATH", "").strip()
            if env_value:
                return env_value
            return os.path.join(self.workspace_root, "models", "ai", "gemma-4-E4B-it-Q4_K_M.gguf")

        def _gpu_pack_dir() -> str:
            return os.path.join(self.workspace_root, "bin", "cuda12_fw")

        def _piper_models_dir() -> str:
            return models_path("piper")

        def _update_local_asset_status():
            model_path = model_edit.text().strip() or _default_local_model_path()
            model_ready = os.path.exists(model_path)
            model_name = os.path.basename(model_path) if model_path else "No model selected"
            local_model_status_label.setText(
                f"Local AI model: {'Ready' if model_ready else 'Missing'}"
                f"{f' ({model_name})' if model_name else ''}"
            )

            gpu_pack_dir = _gpu_pack_dir()
            gpu_pack_ready = os.path.isdir(gpu_pack_dir) and os.path.exists(os.path.join(gpu_pack_dir, "cublas64_12.dll"))
            local_gpu_pack_status_label.setText(
                "Whisper GPU pack: "
                + ("Ready (optional accelerator installed)" if gpu_pack_ready else "Missing (optional, only needed for faster Whisper on NVIDIA)")
            )

            piper_dir = _piper_models_dir()
            piper_models = []
            if os.path.isdir(piper_dir):
                try:
                    piper_models = [
                        name for name in os.listdir(piper_dir)
                        if name.lower().endswith(".onnx")
                    ]
                except Exception:
                    piper_models = []
            local_voices_status_label.setText(
                "Local voices: "
                + (f"Ready ({len(piper_models)} voice model{'s' if len(piper_models) != 1 else ''} found)" if piper_models else "Missing (add Piper voice files to models/piper)")
            )

        def _apply_local_recommended_settings(force_recommended: bool = False):
            hardware_info = LocalPolisherProvider.detect_runtime_capabilities()
            recommended = LocalPolisherProvider.recommended_runtime_config(hardware_info)
            local_status_label.setText(LocalPolisherProvider.runtime_status_summary(hardware_info))
            _update_local_asset_status()
            if force_recommended or not local_n_ctx_edit.text().strip():
                local_n_ctx_edit.setText(str(recommended["n_ctx"]))
            if force_recommended or not local_threads_edit.text().strip():
                local_threads_edit.setText(str(recommended["n_threads"]))
            if force_recommended or not local_gpu_layers_edit.text().strip():
                local_gpu_layers_edit.setText(str(recommended["gpu_layers"]))
            if force_recommended or not local_batch_edit.text().strip():
                local_batch_edit.setText(str(recommended["n_batch"]))
            if force_recommended or not local_ubatch_edit.text().strip():
                local_ubatch_edit.setText(str(recommended["n_ubatch"]))
            if force_recommended or not getattr(local_flash_attn_cb, "_manual_override", False):
                local_flash_attn_cb.setChecked(bool(recommended["flash_attn"]))

        def update_provider_fields():
            p = provider_combo.currentData()
            if p == "local":
                key_edit.clear()
                model_edit.setText(_default_local_model_path())
                key_label.setText("API Key:")
                model_label.setText("Local GGUF model path:")
                key_edit.setEchoMode(QLineEdit.Normal)
                key_edit.setEnabled(False)
                key_label.setEnabled(False)
                key_section_widget.setVisible(False)
                browse_model_btn.setVisible(True)
                open_models_folder_btn.setVisible(True)
                open_gpu_pack_folder_btn.setVisible(True)
                open_voices_folder_btn.setVisible(True)
                manage_resources_btn.setVisible(True)
                provider_hint.setText("Use the local GGUF translator by default. Gemini is optional if you want faster cloud performance.")
                local_status_label.setVisible(True)
                local_model_status_label.setVisible(True)
                local_gpu_pack_status_label.setVisible(True)
                local_voices_status_label.setVisible(True)
                local_perf_row_1_widget.setVisible(True)
                local_perf_row_2_widget.setVisible(True)
                local_perf_row_3_widget.setVisible(True)
                local_n_ctx_edit.setText(os.getenv("LOCAL_TRANSLATOR_N_CTX", ""))
                local_threads_edit.setText(os.getenv("LOCAL_TRANSLATOR_N_THREADS", ""))
                local_gpu_layers_edit.setText(os.getenv("LOCAL_TRANSLATOR_GPU_LAYERS", ""))
                local_batch_edit.setText(os.getenv("LOCAL_TRANSLATOR_N_BATCH", ""))
                local_ubatch_edit.setText(os.getenv("LOCAL_TRANSLATOR_N_UBATCH", ""))
                local_flash_attn_cb._manual_override = bool(os.getenv("LOCAL_TRANSLATOR_FLASH_ATTN", "").strip())
                local_flash_attn_cb.setChecked(str(os.getenv("LOCAL_TRANSLATOR_FLASH_ATTN", "false")).strip().lower() in {"1", "true", "yes", "on"})
                _apply_local_recommended_settings(force_recommended=False)
            elif p == "gemini":
                key_edit.setText(os.getenv("GEMINI_API_KEY", ""))
                model_edit.setText(os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))
                key_label.setText("API Key:")
                model_label.setText("AI Model:")
                key_edit.setEchoMode(QLineEdit.Password)
                key_edit.setEnabled(True)
                key_label.setEnabled(True)
                key_section_widget.setVisible(True)
                browse_model_btn.setVisible(False)
                open_models_folder_btn.setVisible(False)
                open_gpu_pack_folder_btn.setVisible(False)
                open_voices_folder_btn.setVisible(False)
                manage_resources_btn.setVisible(False)
                provider_hint.setText("Use Gemini for AI translation and rewrite.")
                local_status_label.setVisible(False)
                local_model_status_label.setVisible(False)
                local_gpu_pack_status_label.setVisible(False)
                local_voices_status_label.setVisible(False)
                local_perf_row_1_widget.setVisible(False)
                local_perf_row_2_widget.setVisible(False)
                local_perf_row_3_widget.setVisible(False)
            else:
                key_edit.setText(os.getenv("GEMINI_API_KEY", ""))
                model_edit.setText(os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))
                key_label.setText("API Key:")
                model_label.setText("AI Model:")
                key_edit.setEchoMode(QLineEdit.Password)
                key_edit.setEnabled(True)
                key_label.setEnabled(True)
                key_section_widget.setVisible(True)
                browse_model_btn.setVisible(False)
                open_models_folder_btn.setVisible(False)
                open_gpu_pack_folder_btn.setVisible(False)
                open_voices_folder_btn.setVisible(False)
                manage_resources_btn.setVisible(False)
                provider_hint.setText("Use Gemini as the optional cloud provider when you want higher speed than local GGUF.")
                local_status_label.setVisible(False)
                local_model_status_label.setVisible(False)
                local_gpu_pack_status_label.setVisible(False)
                local_voices_status_label.setVisible(False)
                local_perf_row_1_widget.setVisible(False)
                local_perf_row_2_widget.setVisible(False)
                local_perf_row_3_widget.setVisible(False)

        def browse_local_model():
            current_path = model_edit.text().strip()
            start_dir = current_path if os.path.isdir(current_path) else os.path.dirname(current_path) if current_path else os.path.join(self.workspace_root, "models", "ai")
            file_path, _ = QFileDialog.getOpenFileName(
                dialog,
                "Choose GGUF model",
                start_dir,
                "GGUF Models (*.gguf);;All Files (*.*)",
            )
            if file_path:
                model_edit.setText(file_path)
                _update_local_asset_status()

        def open_models_folder():
            models_dir = os.path.join(self.workspace_root, "models", "ai")
            os.makedirs(models_dir, exist_ok=True)
            open_folder_impl(self, models_dir)

        def open_gpu_pack_folder():
            gpu_pack_dir = _gpu_pack_dir()
            os.makedirs(gpu_pack_dir, exist_ok=True)
            open_folder_impl(self, gpu_pack_dir)

        def open_voices_folder():
            voices_dir = _piper_models_dir()
            os.makedirs(voices_dir, exist_ok=True)
            open_folder_impl(self, voices_dir)

        provider_combo.currentIndexChanged.connect(update_provider_fields)
        browse_model_btn.clicked.connect(browse_local_model)
        open_models_folder_btn.clicked.connect(open_models_folder)
        open_gpu_pack_folder_btn.clicked.connect(open_gpu_pack_folder)
        open_voices_folder_btn.clicked.connect(open_voices_folder)
        manage_resources_btn.clicked.connect(self.open_resource_manager_dialog)
        local_auto_optimize_btn.clicked.connect(lambda: _apply_local_recommended_settings(force_recommended=True))
        local_flash_attn_cb.toggled.connect(lambda _checked: setattr(local_flash_attn_cb, "_manual_override", True))
        model_edit.textChanged.connect(lambda _text: _update_local_asset_status())
        def _test_remote_connection():
            try:
                payload = self._test_remote_api_connection(
                    remote_url_edit.text().strip(),
                    remote_token_edit.text().strip(),
                )
                service_name = str(payload.get("service", "capcap-remote-api") or "capcap-remote-api")
                profile_name = str(payload.get("profile", "local") or "local")
                QMessageBox.information(
                    dialog,
                    "Remote API",
                    f"Connected successfully.\n\nService: {service_name}\nProfile: {profile_name}",
                )
            except Exception as exc:
                QMessageBox.warning(
                    dialog,
                    "Remote API",
                    f"Could not connect to the PC server.\n\n{exc}",
                )
        test_remote_btn.clicked.connect(_test_remote_connection)
        if not remote_mode:
            update_provider_fields()

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_btn = QPushButton("Cancel", dialog)
        save_btn = QPushButton("Save", dialog)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)
        layout.addLayout(button_row)

        cancel_btn.clicked.connect(dialog.reject)
        save_btn.clicked.connect(dialog.accept)

        if dialog.exec() != QDialog.Accepted:
            return

        # Save Logic
        new_whisper = str(whisper_combo.currentData() or "base").strip().lower()
        new_provider = str(provider_combo.currentData()).strip()
        new_key = key_edit.text().strip()
        new_model = model_edit.text().strip()

        self.selected_whisper_model_name = new_whisper
        
        # Write back to .env
        env_lines = []
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                env_lines = f.readlines()
        
        updates = {
            "AI_POLISHER_PROVIDER": new_provider
        }
        if remote_mode:
            updates = {
                "CAPCAP_REMOTE_API_URL": remote_url_edit.text().strip() or "http://127.0.0.1:8765",
                "CAPCAP_REMOTE_API_TOKEN": remote_token_edit.text().strip(),
            }
        elif new_provider == "gemini":
            updates["GEMINI_API_KEY"] = new_key
            updates["GEMINI_MODEL"] = new_model
        else:
            updates["LOCAL_TRANSLATOR_MODEL_PATH"] = new_model
            updates["LOCAL_TRANSLATOR_N_CTX"] = local_n_ctx_edit.text().strip() or "2048"
            updates["LOCAL_TRANSLATOR_N_THREADS"] = local_threads_edit.text().strip() or "8"
            updates["LOCAL_TRANSLATOR_N_THREADS_BATCH"] = local_threads_edit.text().strip() or "8"
            updates["LOCAL_TRANSLATOR_GPU_LAYERS"] = local_gpu_layers_edit.text().strip() or "0"
            updates["LOCAL_TRANSLATOR_N_BATCH"] = local_batch_edit.text().strip() or "768"
            updates["LOCAL_TRANSLATOR_N_UBATCH"] = local_ubatch_edit.text().strip() or "384"
            updates["LOCAL_TRANSLATOR_FLASH_ATTN"] = "true" if local_flash_attn_cb.isChecked() else "false"
        
        new_env_lines = []
        handled_keys = set()
        for line in env_lines:
            match = re.match(r"^([^=]+)=.*", line)
            if match:
                k = match.group(1).strip()
                if k in updates:
                    new_env_lines.append(f"{k}={updates[k]}\n")
                    handled_keys.add(k)
                    continue
            new_env_lines.append(line)
        
        for k, v in updates.items():
            if k not in handled_keys:
                new_env_lines.append(f"{k}={v}\n")
        
        with open(".env", "w", encoding="utf-8") as f:
            f.writelines(new_env_lines)
            
        # Update os.environ so it takes effect immediately in this session
        for k, v in updates.items():
            os.environ[k] = v

        self.save_user_settings()
        QMessageBox.information(self, "Success", "Settings saved and updated!")

    def apply_edited_translation(self, show_message=True, force_apply=True):
        result = self.subtitle_controller.apply_edited_translation(show_message=show_message, force_apply=force_apply)
        if result:
            self.refresh_ai_keyword_highlights()
            self.sync_segment_editor_rows()
            return result



    def setup_media_player(self):
        if getattr(self, "_media_backend_ready", False):
            return
        previous_volume = getattr(self, "_preview_volume", 100)
        previous_muted = getattr(self, "_preview_muted", False)
        previous_speed = getattr(self, "_preview_speed", 1.0)
        setup_media_player_impl(self)
        self._preview_volume = previous_volume
        self._preview_muted = previous_muted
        self._preview_speed = previous_speed
        self._media_backend_ready = True
        self._apply_preview_audio_state()

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

    def _get_voiceover_segments(self):
        source_segments = list(self.current_translated_segments or [])
        if not source_segments:
            translated_srt = self.translated_text.toPlainText().strip()
            return self.parse_srt_to_segments(translated_srt) if translated_srt else []

        grouped_segments = []
        idx = 0
        while idx < len(source_segments):
            segment = dict(source_segments[idx])
            group_id = str(segment.get('tts_group_id', '') or '').strip()
            tts_text = ' '.join(str(segment.get('tts_text') or '').split()).strip()
            if not group_id or not tts_text:
                fallback_text = ' '.join(str(segment.get('text') or '').split()).strip()
                segment['text'] = fallback_text
                grouped_segments.append(segment)
                idx += 1
                continue

            group_items = [segment]
            cursor = idx + 1
            while cursor < len(source_segments):
                candidate = source_segments[cursor]
                if str(candidate.get('tts_group_id', '') or '').strip() != group_id:
                    break
                group_items.append(dict(candidate))
                cursor += 1

            grouped_segments.append({
                'start': float(group_items[0].get('tts_group_start', group_items[0].get('start', 0.0)) or group_items[0].get('start', 0.0)),
                'end': float(group_items[-1].get('tts_group_end', group_items[-1].get('end', 0.0)) or group_items[-1].get('end', 0.0)),
                'text': tts_text,
                'tts_text': tts_text,
                'tts_group_id': group_id,
                'source_text': ' '.join(
                    ' '.join(str(item.get('source_text') or item.get('text') or '').split()).strip()
                    for item in group_items
                ).strip(),
            })
            idx = cursor
        return grouped_segments

    def run_voiceover(self):
        if not self.ensure_required_resources("Voice generation", include_voice=True):
            return
        state = self.ensure_current_project()
        if state and not self.translated_text.toPlainText().strip():
            self.load_project_context(state)

        translated_srt = self.translated_text.toPlainText().strip()
        if not translated_srt:
            QMessageBox.warning(self, "Error", "No translated SRT available. Please run translation first (STEP 3).")
            return

        segments = self._get_voiceover_segments()
        if not segments:
            QMessageBox.warning(self, "Error", "Translated SRT could not be parsed to segments.")
            return

        out_dir = self.voice_output_folder_edit.text().strip() or os.path.join(self.workspace_root, "output")
        bg_path = self.resolve_background_audio_path()
        audio_handling_mode = self.get_audio_handling_mode()
        voice_name = self.get_active_voice_name()
        voice_speed = self._parse_voice_speed_value()
        timing_sync_mode = str(self.voice_timing_sync_combo.currentText()).strip()
        voice_gain = float(self.voice_gain_spin.value())
        bg_gain = float(self.bg_gain_spin.value())
        ducking_amount = float(self.ducking_amount_spin.value()) if hasattr(self, "ducking_amount_spin") else -6.0
        voice_signature = self.build_current_voice_signature(segments=segments, background_path=bg_path)
        if state and voice_signature:
            cached_voice_signature = str(state.settings.get("voice_signature", "") or "").strip()
            cached_voice_track = self._normalize_local_file_path(state.artifacts.get("voice_vi", "") or self.last_voice_vi_path)
            cached_mixed_track = self._normalize_local_file_path(state.artifacts.get("mixed_vi", "") or self.last_mixed_vi_path)
            required_output = cached_mixed_track if bg_path else cached_voice_track
            if cached_voice_signature == voice_signature and required_output and os.path.exists(required_output):
                self.last_voice_vi_path = cached_voice_track if cached_voice_track and os.path.exists(cached_voice_track) else self.last_voice_vi_path
                self.last_mixed_vi_path = cached_mixed_track if cached_mixed_track and os.path.exists(cached_mixed_track) else ""
                if self.last_voice_vi_path:
                    self.processed_artifacts["voice_vi"] = self.last_voice_vi_path
                    self.update_project_artifact("voice_vi", self.last_voice_vi_path)
                    self.update_project_step("generate_tts", "done")
                if bg_path:
                    if self.last_mixed_vi_path:
                        self.processed_artifacts["mixed_vi"] = self.last_mixed_vi_path
                        self.update_project_artifact("mixed_vi", self.last_mixed_vi_path)
                        self.update_project_step("mix_audio", "done")
                    else:
                        self.update_project_step("mix_audio", "skipped")
                self.log("[Voiceover] Reusing existing generated audio. Generate did not call TTS again.")
                self.progress_bar.setValue(100)
                self.schedule_timeline_visual_refresh(waveform=True, thumbnails=False)
                self.refresh_ui_state()
                self._pipeline_advance("voiceover")
                return
        
        combo_text = self.free_voice_combo.currentText() if hasattr(self, "free_voice_combo") else ""
        combo_data = self.free_voice_combo.currentData() if hasattr(self, "free_voice_combo") else ""
        combo_id = self.free_voice_combo.currentData(self.VOICE_ENTRY_ID_ROLE) if hasattr(self, "free_voice_combo") else ""
        self.log(f"[Voiceover] Selected voice: text='{combo_text}', data='{combo_data}', id='{combo_id}'")
        
        self.log(
            "[Voiceover] Starting with "
            f"audio_mode={audio_handling_mode}, "
            f"voice={voice_name}, "
            f"speed={voice_speed:.2f}, "
            f"segments={len(segments)}, "
            f"translated_chars={len(translated_srt)}, "
            f"background={bg_path or '<none>'}"
        )
        if state:
            self.log(
                "[Voiceover] State snapshot: "
                f"project={state.project_root}, "
                f"steps={dict(state.steps)}, "
                f"artifacts={dict(state.artifacts)}"
            )

        try:
            self.media_player.pause()
            self.timeline.set_playing(False)
            self._refresh_preview_audio_controls()
        except Exception:
            pass

        self.voiceover_btn.setEnabled(False)
        self.voiceover_btn.setText("Generating... (TTS)")
        self.progress_bar.setValue(85)
        self.update_project_step("generate_tts", "running")
        if bg_path:
            self.update_project_step("mix_audio", "running")
        self.refresh_ui_state()
        try:
            QApplication.processEvents()
        except Exception:
            pass
        self._pending_voice_signature = voice_signature

        project_state_path = self.project_service.project_file(self.current_project_state.project_root) if self.current_project_state else ""
        self.voice_thread = VoiceOverWorker(
            self.workspace_root,
            segments,
            out_dir,
            bg_path,
            audio_handling_mode,
            voice_name,
            voice_speed,
            timing_sync_mode,
            voice_gain,
            bg_gain,
            ducking_amount,
            project_state_path,
            self.get_project_temp_dir("tts"),
            self.is_ai_dubbing_rewrite_enabled() and self.get_output_mode_key() in ("voice", "both"),
            self.get_ai_dubbing_style_instruction(),
            self.get_source_language_code(),
        )
        self.voice_thread.finished.connect(self.on_voiceover_finished)
        self.voice_thread.start()

    def _apply_generated_tts_texts(self, voice_segments):
        source_segments = self.current_translated_segments
        if not source_segments or not voice_segments:
            return False

        updated = False
        grouped_updates = {}
        positional_updates = []
        severe_actions = {"compress_aggressive", "compress_emergency", "keyword_only", "speed_rescue", "speed_balance", "speed_stubborn"}
        for seg in list(voice_segments or []):
            tts_text = ' '.join(str((seg or {}).get("tts_text") or (seg or {}).get("text") or "").split()).strip()
            if not tts_text:
                continue
            subtitle_vi = ' '.join(str((seg or {}).get("subtitle_vi") or (seg or {}).get("text") or "").split()).strip()
            dubbing_vi = ' '.join(str((seg or {}).get("dubbing_vi") or tts_text).split()).strip()
            action_taken = str((seg or {}).get("action_taken") or "").strip().lower()
            ratio = float((seg or {}).get("ratio") or 0.0)
            prefer_dub_match = action_taken in severe_actions or ratio > 1.15
            display_text = dubbing_vi if prefer_dub_match else (subtitle_vi or dubbing_vi)
            group_id = str((seg or {}).get("tts_group_id") or "").strip()
            payload = {
                "tts_text": tts_text,
                "subtitle_vi": subtitle_vi or display_text,
                "dubbing_vi": dubbing_vi,
                "display_text": display_text,
                "action_taken": action_taken,
                "ratio": ratio,
                "attempt_count": int((seg or {}).get("attempt_count") or 1),
            }
            if group_id:
                grouped_updates[group_id] = payload
            else:
                positional_updates.append(payload)

        positional_index = 0
        for seg in source_segments:
            group_id = str((seg or {}).get("tts_group_id") or "").strip()
            if group_id and group_id in grouped_updates:
                next_payload = grouped_updates[group_id]
            elif positional_index < len(positional_updates):
                next_payload = positional_updates[positional_index]
                positional_index += 1
            else:
                continue

            next_tts_text = next_payload["tts_text"]
            current_tts_text = ' '.join(str(seg.get("tts_text") or "").split()).strip()
            current_text = ' '.join(str(seg.get("text") or "").split()).strip()
            if current_tts_text != next_tts_text:
                seg["tts_text"] = next_tts_text
                updated = True
            next_display_text = next_payload["display_text"]
            if next_display_text and current_text != next_display_text:
                seg["text"] = next_display_text
                updated = True
            seg["subtitle_vi"] = next_payload["subtitle_vi"]
            seg["dubbing_vi"] = next_payload["dubbing_vi"]
            seg["action_taken"] = next_payload["action_taken"]
            seg["ratio"] = next_payload["ratio"]
            seg["attempt_count"] = next_payload["attempt_count"]
        return updated

    def on_voiceover_finished(self, voice_track, mixed, voice_segments, error):
        self.voiceover_btn.setEnabled(True)
        self.voiceover_btn.setText("Generate Voice / Mix")
        self.progress_bar.setValue(100)

        if error:
            self._pending_voice_signature = ""
            self.update_project_step("generate_tts", "failed")
            if self.bg_music_edit.text().strip():
                self.update_project_step("mix_audio", "failed")
            QMessageBox.critical(self, "Error", f"Voiceover failed:\n\n{error}")
            self._pipeline_fail("Voiceover failed.")
            self.refresh_ui_state()
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
        if self._apply_generated_tts_texts(voice_segments):
            self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
            self.persist_current_timeline_project_data()
        if self.current_project_state:
            voice_signature = self.build_current_voice_signature(
                segments=self._get_voiceover_segments(),
                background_path=self.resolve_background_audio_path(),
            )
            if voice_signature:
                self.current_project_state.set_setting("voice_signature", voice_signature)
                self.project_service.save_project(self.current_project_state)
        self._pending_voice_signature = ""

        if mixed:
            self.log(f"[Voiceover] Generated Vietnamese voice and mixed audio: Voice={voice_track}, Mixed={mixed}")
        else:
            self.log(f"[Voiceover] Generated Vietnamese voice track: {voice_track} (No background mix created.)")

        self.schedule_timeline_visual_refresh(waveform=True, thumbnails=False)
        self.refresh_ui_state()
        self._pipeline_advance("voiceover")

    def preview_video(self):
        self.preview_controller.preview_video()

    def on_preview_ready(self, preview_path, error, styled_signature=""):
        self.preview_controller.on_preview_ready(preview_path, error, styled_signature)

    def run_all_pipeline(self):
        mode = self.get_output_mode_key()
        include_voice = mode in ("voice", "both")
        if not self.ensure_required_resources("Generate", include_whisper=True, include_voice=include_voice):
            return
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

    def _path_within_root(self, path: str, root: str) -> bool:
        try:
            normalized_path = os.path.normcase(os.path.abspath(path))
            normalized_root = os.path.normcase(os.path.abspath(root))
            return os.path.commonpath([normalized_path, normalized_root]) == normalized_root
        except Exception:
            return False

    def _remove_path_if_safe(self, path: str, *, allowed_roots: list[str], removed: list[str]) -> None:
        normalized = self._normalize_local_file_path(path)
        if not normalized or not os.path.exists(normalized):
            return
        if not any(self._path_within_root(normalized, root) for root in allowed_roots if root):
            return

        def _on_remove_error(func, target, exc_info):
            try:
                os.chmod(target, 0o777)
                func(target)
            except OSError:
                return

        try:
            if os.path.isdir(normalized):
                shutil.rmtree(normalized, onerror=_on_remove_error)
            else:
                os.remove(normalized)
        except OSError:
            return
        if not os.path.exists(normalized):
            removed.append(normalized)

    def _reset_project_runtime_state(self) -> None:
        self.current_project_state = None
        self.current_segment_models = []
        self.current_translated_segment_models = []
        self.current_segments = []
        self.current_translated_segments = []
        self.processed_artifacts = {}
        self.last_extracted_audio = ""
        self.last_vocals_path = ""
        self.last_music_path = ""
        self.last_original_srt_path = ""
        self.last_translated_srt_path = ""
        self.last_voice_vi_path = ""
        self.last_mixed_vi_path = ""
        self.last_preview_video_path = ""
        self.last_styled_preview_path = ""
        self.last_styled_preview_signature = ""
        self.last_exported_video_path = ""
        self.last_exact_preview_5s_path = ""
        self.last_exact_preview_frame_path = ""
        self.live_preview_subtitle_path = ""
        self.live_preview_ass_path = ""
        self.live_preview_segments = []
        self.live_preview_editor_name = ""
        self._live_preview_signature = None
        if hasattr(self, "transcript_text"):
            self.transcript_text.clear()
        if hasattr(self, "translated_text"):
            self.translated_text.clear()
        if hasattr(self, "audio_source_edit"):
            self.audio_source_edit.clear()
        if hasattr(self, "bg_music_edit"):
            self.bg_music_edit.clear()
        if hasattr(self, "mixed_audio_edit"):
            self.mixed_audio_edit.clear()
        if hasattr(self, "timeline"):
            self.timeline.set_segments([])
            self.timeline.set_playing(False)
        self.sync_segment_editor_rows()
        self.refresh_ui_state()

    def _has_cleanable_project_data(self) -> bool:
        project_root = str(getattr(getattr(self, "current_project_state", None), "project_root", "") or "").strip()
        candidates = [
            self.last_extracted_audio,
            self.last_vocals_path,
            self.last_music_path,
            self.last_voice_vi_path,
            self.last_mixed_vi_path,
            self.live_preview_subtitle_path,
            self.live_preview_ass_path,
            self.last_preview_video_path,
            self.last_styled_preview_path,
            self.last_exact_preview_5s_path,
            self.last_exact_preview_frame_path,
            self.get_project_temp_path("tts"),
            self.get_project_temp_path("segment_audio_preview"),
            self.get_project_temp_path("voice_sample_preview"),
            self.get_project_temp_path("htdemucs"),
            self.get_project_temp_path("timeline_video_thumbs"),
            self.get_project_temp_root(),
            project_root,
        ]
        for candidate in candidates:
            normalized = self._normalize_local_file_path(candidate)
            if normalized and os.path.exists(normalized):
                return True
        return False

    def clean_current_project(self):
        project_state = getattr(self, "current_project_state", None)
        if not self._has_cleanable_project_data():
            QMessageBox.information(self, "Clean Project", "There is no generated project data to clean right now.")
            return

        confirmation = QMessageBox.question(
            self,
            "Clean Project",
            "This will remove intermediate project files, temp previews, separated audio, and cached TTS files for the current project.\n\n"
            "It will keep your source video, imported assets, and final exported video.\n\n"
            "Do you want to continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return

        removed_paths = []
        removed_groups = {
            "Project folder": [],
            "Generated voice files": [],
            "Separated audio": [],
            "Preview temp files": [],
            "TTS cache": [],
            "Temp folders": [],
        }
        project_temp_root = self.get_project_temp_root()
        output_root = os.path.join(self.workspace_root, "output")
        project_root = str(getattr(project_state, "project_root", "") or "").strip()
        project_state_path = self.project_service.project_file(project_root) if project_root else ""
        allowed_roots = [root for root in [project_temp_root, output_root, project_root] if root]

        self.cleanup_temp_preview_files()

        file_candidates = [
            ("Separated audio", self.last_extracted_audio),
            ("Separated audio", self.last_vocals_path),
            ("Separated audio", self.last_music_path),
            ("Generated voice files", self.last_voice_vi_path),
            ("Generated voice files", self.last_mixed_vi_path),
            ("Preview temp files", self.live_preview_subtitle_path),
            ("Preview temp files", self.live_preview_ass_path),
            ("Preview temp files", self.last_styled_preview_path),
            ("Project folder", project_state_path),
        ]
        for group_name, candidate in file_candidates:
            before_count = len(removed_paths)
            self._remove_path_if_safe(candidate, allowed_roots=allowed_roots, removed=removed_paths)
            if len(removed_paths) > before_count:
                removed_groups[group_name].append(removed_paths[-1])

        dir_candidates = [
            ("Project folder", project_root),
            ("TTS cache", self.get_project_temp_path("tts")),
            ("Temp folders", self.get_project_temp_path("segment_audio_preview")),
            ("Temp folders", self.get_project_temp_path("voice_sample_preview")),
            ("Temp folders", self.get_project_temp_path("htdemucs")),
            ("Temp folders", self.get_project_temp_path("timeline_video_thumbs")),
            ("Temp folders", project_temp_root),
        ]
        for group_name, candidate in dir_candidates:
            before_count = len(removed_paths)
            self._remove_path_if_safe(candidate, allowed_roots=allowed_roots, removed=removed_paths)
            if len(removed_paths) > before_count:
                removed_groups[group_name].append(removed_paths[-1])

        self._reset_project_runtime_state()

        if removed_paths:
            self.log(f"[Clean Project] Removed {len(removed_paths)} intermediate paths.")
            detail_lines = ["Cleaned these groups:"]
            for group_name, paths in removed_groups.items():
                if paths:
                    detail_lines.append(f"- {group_name}: {len(paths)} item(s)")
            QMessageBox.information(
                self,
                "Clean Project",
                f"Removed {len(removed_paths)} intermediate paths for the current project.\n\n" + "\n".join(detail_lines),
            )
        else:
            QMessageBox.information(
                self,
                "Clean Project",
                "No removable intermediate files were found for the current project.",
            )

    def closeEvent(self, event):
        try:
            if hasattr(self, "video_view"):
                self.video_view.clear_blur_region()
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
        self.schedule_timeline_visual_refresh(waveform=False, thumbnails=True)

    def set_position(self, position):
        set_position_impl(self, position)

    def update_duration_label(self, current, total):
        update_duration_label_impl(self, current, total)

    def _apply_preview_audio_state(self):
        if not hasattr(self, "media_player"):
            return
        try:
            self.media_player.set_volume(getattr(self, "_preview_volume", 100))
        except Exception:
            pass
        try:
            self.media_player.set_muted(getattr(self, "_preview_muted", False))
        except Exception:
            pass
        try:
            self.media_player.set_playback_rate(getattr(self, "_preview_speed", 1.0))
        except Exception:
            pass
        self._refresh_preview_audio_controls()

    def _refresh_preview_audio_controls(self):
        if hasattr(self, "preview_volume_label"):
            label = f"{int(getattr(self, '_preview_volume', 100))}%"
            if getattr(self, "_preview_muted", False):
                label += " muted"
            self.preview_volume_label.setText(label)
        if hasattr(self, "preview_mute_btn"):
            icon_name = "volume_down.svg" if getattr(self, "_preview_muted", False) else "volume_mute.svg"
            icon_path = asset_path("icons", icon_name)
            self.preview_mute_btn.setIcon(load_icon(icon_path, 18))
        if hasattr(self, "play_btn"):
            playing = False
            try:
                playing = bool(self.media_player.is_playing())
            except Exception:
                playing = False
            play_icon = "pause.svg" if playing else "play.svg"
            play_tip = "Pause preview" if playing else "Play preview"
            self.play_btn.setIcon(load_icon(asset_path("icons", play_icon), 18))
            self.play_btn.setToolTip(play_tip)
        if hasattr(self, "blur_area_btn"):
            blur_active = bool(self.blur_area_btn.isChecked())
            self.blur_area_btn.setToolTip("Blur editing on" if blur_active else "Toggle blur area editing")
        if hasattr(self, "preview_speed_combo"):
            target = float(getattr(self, "_preview_speed", 1.0))
            index = self.preview_speed_combo.findData(target)
            if index >= 0 and self.preview_speed_combo.currentIndex() != index:
                self.preview_speed_combo.blockSignals(True)
                self.preview_speed_combo.setCurrentIndex(index)
                self.preview_speed_combo.blockSignals(False)

    def preview_volume_down(self):
        self._preview_volume = max(0, int(getattr(self, "_preview_volume", 100)) - 10)
        self._preview_muted = self._preview_volume == 0
        self._apply_preview_audio_state()

    def preview_volume_up(self):
        self._preview_volume = min(200, int(getattr(self, "_preview_volume", 100)) + 10)
        if self._preview_volume > 0:
            self._preview_muted = False
        self._apply_preview_audio_state()

    def toggle_preview_mute(self):
        self._preview_muted = not bool(getattr(self, "_preview_muted", False))
        self._apply_preview_audio_state()

    def on_preview_speed_changed(self, index: int):
        if not hasattr(self, "preview_speed_combo"):
            return
        rate = self.preview_speed_combo.itemData(index)
        try:
            self._preview_speed = float(rate or 1.0)
        except Exception:
            self._preview_speed = 1.0
        self._apply_preview_audio_state()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoTranslatorGUI()
    window.show()
    sys.exit(app.exec())






