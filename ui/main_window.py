import sys
import os
import re
import json
import copy
import glob
import hashlib
import shutil
import threading
from uuid import uuid4
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QToolButton, QLabel, QLineEdit,
                             QFileDialog, QTextEdit, QComboBox,
                             QFrame, QProgressBar, QMessageBox,
                             QScrollArea,
                             QColorDialog, QTabWidget, QDialog, QSizePolicy, QInputDialog, QLayout,
                             QSpinBox)
from PySide6.QtCore import Qt, QUrl, QTimer, QSettings, QEvent, Signal, QPoint, QRect
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QIcon, QKeySequence, QPixmap, QTextCursor
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
from new_highlight_selector import auto_select_matches
from video_processor import srt_to_ass
from audio_mixer import ffprobe_wav_duration
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
from widgets.progress_dialog import BackgroundableProgressDialog
from widgets.spin_boxes import ReliableDoubleSpinBox
from widgets.subtitle_editor_dialog import SubtitleEditorDialog
from runtime_paths import app_path, asset_path, bundle_root, models_path, workspace_root
from runtime_profile import is_remote_profile
from worker_adapters import (
    ExtractionWorker,
    ResourceDownloadWorker,
    OcrTranslatorCaptureWorker,
    OcrTranslatorTranslationWorker,
    SegmentAudioPreviewWorker,
    VoiceSamplePreviewWorker,
    VocalSeparationWorker,
    VoiceOverWorker,
    TimelineThumbnailWorker,
    TimelineWaveformWorker,
    AlternateRangeTranscriptionWorker,
)

# Import our backend modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))
from video_processor import get_video_dimensions
from workflows.voice_workflow import predict_speed_ratios
from audio_mixer import mix_voice_with_background


def _default_asr_engine() -> str:
    return "sensevoice"


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

    def set_original_audio_file(self, audio_path):
        return None

    def _clear_original_audio(self):
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

    def set_mute_original(self, muted):
        return None

    def set_mute_dubbed(self, muted):
        return None

    def is_original_muted(self):
        return False

    def is_dubbed_muted(self):
        return False

    def set_original_volume(self, percent):
        return None

    def set_dubbed_volume(self, percent):
        return None

    def original_volume(self):
        return 100

    def dubbed_volume(self):
        return 100

    def set_playback_rate(self, rate):
        return None

    def playback_rate(self):
        return 1.0

class VideoTranslatorGUI(QMainWindow):
    VOICE_ENTRY_ID_ROLE = Qt.UserRole + 1
    runtime_log_received = Signal(str)
    subtitle_ass_ready = Signal(int, str, str, object)

    def __init__(self):
        super().__init__()
        self._current_video_path = ""
        title = "CapCap Video Translator"
        if is_remote_profile():
            title += " (Remote)"
        self.setWindowTitle(title)
        self.settings = QSettings("CapCap", "VideoTranslatorGUI")
        self.setAcceptDrops(True)
        self.logo_path = asset_path("capcap.png")
        if os.path.exists(self.logo_path):
            self.setWindowIcon(QIcon(self.logo_path))
        self.setWindowFlag(Qt.FramelessWindowHint)
        
        # Start maximized, but keep the window genuinely resizable.  Locking
        # it to the first monitor's pixel size prevented Qt from adapting the
        # layout when users moved between laptop/desktop displays or changed
        # DPI scaling.
        self.setWindowState(Qt.WindowMaximized)
        self.setMinimumSize(1024, 640)
        self._responsive_layout_pending = False
        self._responsive_layout_mode = "desktop"
        self._initial_layout_finalized = False
        
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
            QFrame#audioSourcePanel {
                background-color: #101d2d;
                border: 1px solid #2a455f;
                border-radius: 10px;
            }
            QLabel#audioSourceTitle {
                color: #edf7ff;
                font-weight: 700;
            }
            QFrame#subtitleInspectorHandle {
                background-color: #0d1624;
                border: 1px solid #24384f;
                border-left: none;
                border-top-right-radius: 14px;
                border-bottom-right-radius: 14px;
            }
            QPushButton#subtitleInspectorHandleBtn {
                background-color: #162638;
                color: #8ad7ff;
                border: 1px solid #31506d;
                border-right: none;
                border-top-left-radius: 999px;
                border-bottom-left-radius: 999px;
                border-top-right-radius: 0px;
                border-bottom-right-radius: 0px;
                font-size: 20px;
                font-weight: 900;
                padding: 0px;
            }
            QPushButton#subtitleInspectorHandleBtn:hover {
                background-color: #1d3047;
                border-color: #4d82b5;
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
            QPushButton#mainActionBtn, QToolButton#mainActionBtn {
                background-color: #4ed0b3;
                color: #0b1620;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: bold;
                border-bottom: 2px solid #258971;
                padding: 8px 14px;
            }
            QPushButton#mainActionBtn:hover, QToolButton#mainActionBtn:hover {
                background-color: #66ddc2;
            }
            QToolButton#mainActionBtn::menu-indicator {
                image: none;
                width: 0px;
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
            QMenu#headerMoreMenu, QMenu#generateMenu, QMenu#generateStepMenu {
                background-color: #0f1724;
                color: #e6eef9;
                border: 1px solid #30425b;
                padding: 6px;
            }
            QMenu#headerMoreMenu::item, QMenu#generateMenu::item, QMenu#generateStepMenu::item {
                background-color: transparent;
                color: #e6eef9;
                padding: 8px 14px;
                border-radius: 8px;
            }
            QMenu#generateStepMenu::item:enabled {
                background-color: #17324a;
                color: #e8f7ff;
                border: 1px solid #39749e;
                font-weight: 700;
            }
            QMenu#generateStepMenu::item:disabled {
                background-color: #111b29;
                color: rgba(151, 169, 190, 110);
                border: 1px solid #1d2a3a;
                font-weight: 400;
            }
            QMenu#headerMoreMenu::item:selected, QMenu#generateMenu::item:selected, QMenu#generateStepMenu::item:selected {
                background-color: #213248;
                color: #ffffff;
            }
            QMenu#generateStepMenu::item:disabled:selected {
                background-color: #111b29;
                color: rgba(151, 169, 190, 110);
            }
            QMenu#headerMoreMenu::separator, QMenu#generateMenu::separator, QMenu#generateStepMenu::separator {
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
        self._runtime_logs = []
        self._pending_runtime_log_entries = []
        self._runtime_log_view_entry_count = 0
        self._runtime_log_flush_timer = QTimer(self)
        self._runtime_log_flush_timer.setSingleShot(True)
        self._runtime_log_flush_timer.setInterval(100)
        self._runtime_log_flush_timer.timeout.connect(self._flush_runtime_log_entries)
        self._editor_highlight_chunks = {}
        self._editor_highlight_state = {}
        self.runtime_log_received.connect(self._append_runtime_log_entry)
        self.workspace_root = workspace_root()
        self._cleanup_temp_root()
        self.project_service = ProjectService(self.workspace_root)
        self.project_bridge = GUIProjectBridge(self.project_service)
        self.voice_catalog_service = VoiceCatalogService(self.workspace_root)
        self.subtitle_controller = SubtitleController(self)
        self.pipeline_controller = PipelineController(self)
        self.preview_controller = PreviewController(self)
        self.current_project_state = None
        # Do not inherit a legacy global Subtitle Source from .env.  Opening
        # a project below will replace this with that project's own setting.
        os.environ["TRANSCRIPTION_ENGINE"] = _default_asr_engine()
        self.current_segment_models = []
        self.current_translated_segment_models = []
        self.selected_whisper_model_name = "auto"
        self._last_audio_preview_path = ""
        self._segment_preview_threads = {}
        self._voice_sample_preview_thread = None
        self._voiceover_force_refresh = False
        self.voice_catalog_entries_all = []
        self.voice_catalog_entries = []

        self.voice_catalog_map = {}
        self._voice_signals_bound = False
        self._media_backend_ready = False
        self._blur_region_signal_bound = False
        self._blur_edit_finished_signal_bound = False
        self._preview_audio_signals_bound = False
        self.media_player = _BootstrapMediaBackend()
        self.voice_preview_dialog = None
        self._voice_preview_row_buttons = {}
        self._tracked_progress_dialogs = []
        self._timeline_timing_undo_stack = []
        self._timeline_timing_redo_stack = []
        self._suspend_timeline_undo = False
        self._timeline_waveform_cache_key = None
        self._timeline_waveform_samples = []
        self._timeline_waveform_duration_s = 0.0
        self._timeline_waveform_worker = None
        self._desired_timeline_waveform_request = None
        self._timeline_video_thumb_cache_key = None
        self._timeline_video_thumbnails = []
        self._timeline_thumbnail_worker = None
        self._desired_timeline_thumbnail_request = None
        self._pending_timeline_waveform_refresh = False
        self._pending_timeline_thumbnail_refresh = False
        self._allow_post_pipeline_preview_assets = False
        self._subtitle_custom_style_state = None
        self._subtitle_preset_apply_in_progress = False
        # Exact full-block subtitle backgrounds are measured by libass.  Keep
        # that expensive work out of the GUI thread; the active ASS track is
        # intentionally retained until the newest debounced result is ready.
        self._subtitle_ass_request_token = 0
        self._subtitle_ass_worker_running = False
        self._subtitle_ass_worker_threads = []
        self._subtitle_ass_pending_snapshot = None
        self.subtitle_ass_ready.connect(self._on_async_subtitle_ass_ready)
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
        self._filter_preview_blur_was_checked = False
        self._filter_preview_ocr_was_editable = False
        self._suspend_ocr_overlay = False
        self._ocr_overlay_visible = True
        self._ocr_translator_active = False
        self._ocr_translator_rect = (0.2, 0.2, 0.6, 0.25)
        self._ocr_translator_capture_worker = None
        self._ocr_translator_translation_worker = None
        self._play_video_filter_preview_when_ready = False
        self._filter_thumbnail_target_height = 320
        self._video_filter_preview_dirty = False
        self._video_filter_apply_requested = False
        self._blur_edit_finish_syncing = False
        self._blur_region_preview_dirty = False
        # Blur/Mask are MPV filter effects. During a paused geometry edit we
        # suppress only the active layer from the filter graph so an old,
        # stale effect is never left behind the lightweight edit overlay.
        self._deferred_effect_edit_type = ""
        self._deferred_effect_edit_layer_id = ""
        # A selected layer becomes editable only after an explicit paused
        # selection.  Playback and its pause transition never implicitly
        # restore edit chrome for the previously selected layer.
        self._preview_edit_layer_id = ""
        self._review_mode_active = False
        # Overlay drags can emit dozens of events per second.  Persisting the
        # full project/timeline for each one causes synchronous JSON and
        # project-file writes on the UI thread, so collect rapid edits and
        # save their final state shortly after interaction settles.
        self._pending_timeline_persist = False
        self._pending_mask_state_persist = False
        self._pending_blur_state_persist = False
        self._timeline_persist_timer = QTimer(self)
        self._timeline_persist_timer.setSingleShot(True)
        self._timeline_persist_timer.setInterval(180)
        self._timeline_persist_timer.timeout.connect(self._flush_pending_timeline_persist)
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
        if getattr(self, "subtitle_preset_tiktok_radio", None) and self.subtitle_preset_tiktok_radio.isChecked():
            return "tiktok"
        if getattr(self, "subtitle_preset_youtube_radio", None) and self.subtitle_preset_youtube_radio.isChecked():
            return "youtube"
        if getattr(self, "subtitle_preset_minimal_radio", None) and self.subtitle_preset_minimal_radio.isChecked():
            return "minimal"
        return "youtube"

    def get_subtitle_preset_config(self, preset_key: str | None = None) -> dict:
        preset = (preset_key or self.get_selected_subtitle_preset()).lower()
        presets = {
            "tiktok": {
                "label": "TikTok",
                "font_name": "Montserrat",
                "font_size": 30,
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
                "font_size": 30,
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
                "font_size": 30,
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
                "font_name": "Segoe UI",
                "font_size": 30,
                "font_color": "#FFFFFF",
                "highlight_color": "#FFD400",
                "outline_color": "#000000",
                "outline_width": 3,
                "shadow_color": "#000000",
                "shadow_depth": 1,
                "shadow_alpha": 0.3,
                "background_box": False,
                "background_color": "#000000",
                "background_alpha": 0.6,
                "animation": "Pop In",
                "bold": True,
                "auto_keyword_highlight": False,
                "highlight_mode": "Auto",
                "summary": "Your editable working preset. Manual style changes can switch here automatically.",
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

    def prepare_responsive_layout(self):
        """Apply only the target-screen responsive profile while hidden."""
        screen = self.screen() or QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen is not None else None
        if geometry is None:
            self.apply_responsive_layout()
            return
        self.apply_responsive_layout(geometry.width(), geometry.height())

    def prepare_initial_editor_layout(self):
        """Resolve the complete first editor layout while the window is hidden.

        Unlike the old post-show timers, this gives the central widget and
        splitter their final screen-sized geometry first, activates Qt's
        layouts, and then applies the initial 45/55 workspace allocation.
        The first visible paint is therefore already the settled layout.
        """
        if getattr(self, "_initial_layout_finalized", False):
            return
        screen = self.screen() or QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen is not None else None
        if geometry is not None:
            self.setGeometry(geometry)
        self.ensurePolished()
        central = self.centralWidget()
        if central is not None:
            central.setGeometry(self.contentsRect())
            layout = central.layout()
            if layout is not None:
                layout.activate()
        self.prepare_responsive_layout()
        if central is not None and central.layout() is not None:
            central.layout().activate()
        set_default_splitter = getattr(self, "_set_default_preview_timeline_sizes", None)
        if callable(set_default_splitter):
            set_default_splitter()
        self._initial_layout_finalized = True

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_initial_layout_finalized", False):
            return
        # Fallback for non-launcher entry points. The normal Launcher flow
        # calls prepare_initial_editor_layout() before show().
        self.prepare_initial_editor_layout()

    def resizeEvent(self, event):
        """Apply responsive layout changes after Qt settles a resize/DPI move."""
        super().resizeEvent(event)
        if not getattr(self, "_initial_layout_finalized", False):
            return
        if not getattr(self, "_responsive_layout_pending", False):
            self._responsive_layout_pending = True
            QTimer.singleShot(0, self.apply_responsive_layout)

    def apply_responsive_layout(self, available_width=None, available_height=None):
        """Keep the editor usable from 1280x720 upward without altering
        the normal desktop composition.

        Width controls the two side panels; available height controls the
        Preview/Timeline minimums.  Content remains reachable through the
        existing inspector and timeline scroll areas instead of being clipped.
        """
        self._responsive_layout_pending = False
        central = self.centralWidget()
        width = int(available_width or (central.width() if central is not None else self.width()) or self.width())
        height = int(available_height or (central.height() if central is not None else self.height()) or self.height())
        compact_width = width < 1500
        compact_height = height < 850
        tight_height = height < 760
        mode = "compact" if (compact_width or compact_height) else "desktop"
        self._responsive_layout_mode = mode

        root_layout = getattr(self, "root_layout", None)
        content_layout = getattr(self, "content_layout", None)
        header_layout = getattr(self, "header_layout", None)
        if root_layout is not None:
            margin = 8 if compact_height else 15
            root_layout.setContentsMargins(margin, margin, margin, margin)
            root_layout.setSpacing(8 if compact_height else 15)
        if content_layout is not None:
            content_layout.setSpacing(8 if compact_width else 15)
        if header_layout is not None:
            header_layout.setContentsMargins(10 if compact_width else 18, 8 if compact_height else 14,
                                             10 if compact_width else 18, 8 if compact_height else 14)
            header_layout.setSpacing(6 if compact_width else 12)

        # Narrower side panels leave a meaningful preview width on 1366/1280
        # laptops while the cards themselves remain scrollable.
        left_panel = getattr(self, "left_panel_scroll_area", None)
        if left_panel is not None:
            left_panel.setFixedWidth(320 if compact_width else 420)
        inspector_width = 320 if compact_width else 400
        inspector_max = 440 if compact_width else 560
        self._responsive_inspector_width = inspector_width
        for attr in (
            "subtitle_inspector_card", "audio_inspector_card", "blur_inspector_card",
            "logo_inspector_card", "mask_inspector_card", "text_inspector_card",
            "default_inspector_card", "video_inspector_card",
        ):
            card = getattr(self, attr, None)
            if card is not None:
                card.setMinimumWidth(inspector_width)
                card.setMaximumWidth(inspector_max)
        self._sync_subtitle_inspector_shell_width()
        stack = getattr(self, "inspector_stack", None)
        if stack is not None:
            for index in range(stack.count()):
                scroll = stack.widget(index)
                if isinstance(scroll, QScrollArea):
                    scroll.setHorizontalScrollBarPolicy(
                        Qt.ScrollBarAsNeeded if compact_width else Qt.ScrollBarAlwaysOff
                    )

        # The fixed minimums are intentionally reduced only on short displays.
        # Timeline tracks remain available through their own scrollbars.
        workspace_min = 350
        timeline_min = 360
        video_min = 270
        if compact_height:
            workspace_min, timeline_min, video_min = 260, 255, 190
        if tight_height:
            workspace_min, timeline_min, video_min = 220, 210, 170
        self._responsive_workspace_minimum_height = workspace_min
        self._responsive_timeline_minimum_height = timeline_min
        workspace = getattr(self, "preview_workspace_widget", None)
        timeline_card = getattr(self, "timeline_card", None)
        video_view = getattr(self, "video_view", None)
        if workspace is not None:
            workspace.setMinimumHeight(workspace_min)
        if timeline_card is not None:
            timeline_card.setMinimumHeight(timeline_min)
        if video_view is not None:
            video_view.setMinimumHeight(video_min)

        # Reduce header chrome in compact mode without removing actions.
        for button in (getattr(self, "run_all_btn", None), getattr(self, "export_btn", None),
                       getattr(self, "more_actions_btn", None)):
            if button is not None:
                button.setMinimumHeight(34 if compact_height else 42)
        preview_button = getattr(self, "preview_5s_btn", None)
        if preview_button is not None:
            preview_button.setVisible(not compact_width)
        more_button = getattr(self, "more_actions_btn", None)
        if more_button is not None:
            more_button.setMinimumWidth(84 if compact_width else 180)
        logo = getattr(self, "header_logo_label", None)
        if logo is not None:
            logo.setVisible(not compact_width)
        brand = getattr(self, "header_brand_label", None)
        if brand is not None:
            brand.setVisible(not compact_width)

        # Ensure altered minimums are immediately reflected in splitter bounds
        # and native preview overlay geometry.
        splitter = getattr(self, "preview_timeline_splitter", None)
        if splitter is not None:
            sizes = splitter.sizes()
            if len(sizes) == 2 and sum(sizes) > timeline_min and sizes[1] < timeline_min:
                splitter.setSizes([max(1, sum(sizes) - timeline_min), timeline_min])
        self.sync_left_panel_container_width()
        QTimer.singleShot(0, self._resync_preview_region_overlays)

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

    def ensure_media_backend_ready(self):
        if getattr(self, "_media_backend_ready", False):
            return
        self.setup_media_player()
        if hasattr(self, "video_view") and hasattr(self.video_view, "blurRegionChanged"):
            if getattr(self, "_blur_region_signal_bound", False):
                try:
                    self.video_view.blurRegionChanged.disconnect(self.on_preview_blur_region_changed)
                except Exception:
                    pass
            self.video_view.blurRegionChanged.connect(self.on_preview_blur_region_changed)
            self._blur_region_signal_bound = True
        if hasattr(self, "video_view") and hasattr(self.video_view, "blurEditFinished"):
            if getattr(self, "_blur_edit_finished_signal_bound", False):
                try:
                    self.video_view.blurEditFinished.disconnect(self.on_blur_edit_finished)
                except Exception:
                    pass
            self.video_view.blurEditFinished.connect(self.on_blur_edit_finished)
            self._blur_edit_finished_signal_bound = True
        if hasattr(self, "video_view") and hasattr(self.video_view, "subtitlePositionChanged"):
            if not getattr(self, "_subtitle_position_drag_signal_bound", False):
                self.video_view.subtitlePositionChanged.connect(self.on_subtitle_position_dragged)
                self._subtitle_position_drag_signal_bound = True
        if hasattr(self, "video_view") and hasattr(self.video_view, "textLayerSelected"):
            if not getattr(self, "_text_layer_signal_bound", False):
                self.video_view.textLayerSelected.connect(self._on_text_layer_selected_from_preview)
                self.video_view.textLayerMoved.connect(self._on_text_layer_moved)
                self._text_layer_signal_bound = True

    def _on_text_layer_selected_from_preview(self, layer_id):
        if hasattr(self, "timeline"):
            self.timeline._selected_layer_id = str(layer_id)
            self.timeline._redraw()
        self.on_timeline_layer_selected(str(layer_id))

    def _on_text_layer_moved(self, layer_id, x, y):
        if self._preview_is_playing():
            return
        layer = next((item for item in self._text_layers() if item.id == layer_id), None)
        if layer is None:
            return
        layer.transform.x, layer.transform.y = float(x), float(y)
        self.schedule_timeline_project_persist()

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
        self.audio_preview_output.setVolume(1.0)
        self.audio_preview_output.setMuted(False)
        try:
            from PySide6.QtMultimedia import QMediaDevices
            self.audio_preview_output.setDevice(QMediaDevices.defaultAudioOutput())
        except Exception:
            pass
        self.audio_preview_player.setAudioOutput(self.audio_preview_output)
        self.audio_preview_player.errorOccurred.connect(
            lambda err, msg: self.log(f"[Audio Preview] player error: {msg} ({err})")
        )

        self.voice_preview_library_player = QMediaPlayer(self)
        self.voice_preview_library_output = QAudioOutput(self)
        self.voice_preview_library_output.setVolume(1.0)
        self.voice_preview_library_output.setMuted(False)
        try:
            from PySide6.QtMultimedia import QMediaDevices
            self.voice_preview_library_output.setDevice(QMediaDevices.defaultAudioOutput())
        except Exception:
            pass
        self.voice_preview_library_player.setAudioOutput(self.voice_preview_library_output)
        self.voice_preview_library_player.playbackStateChanged.connect(
            lambda state: self._stop_voice_library_preview() if state != QMediaPlayer.PlaybackState.PlayingState else None
        )
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
        if provider in ("vieneu", "capcut"):
            return entry_id
        return ""

    def _voice_provider_label(self, provider: str) -> str:
        provider_key = str(provider or "").strip().lower()
        if provider_key == "piper":
            return "Piper"
        if provider_key == "vieneu":
            return "VieNeu"
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

    def _current_voice_engine_key(self) -> str:
        combo = getattr(self, "voice_engine_combo", None)
        if combo is None:
            return "piper"
        key = str(combo.currentData() or "piper").strip().lower() or "piper"
        if key in ("fast", "piper"):
            return "piper"
        return key

    def get_transcription_engine(self) -> str:
        """Return the recognition source for the open project, never a stale global preference."""
        state = getattr(self, "current_project_state", None)
        settings = getattr(state, "settings", {}) if state is not None else {}
        value = str(settings.get("transcription_engine", "") or "").strip().lower()
        return value if value in {"whisper", "sensevoice", "ocr", "capcut"} else _default_asr_engine()

    def set_project_transcription_engine(self, engine: str) -> None:
        """Apply a project-local source choice and clear incompatible range state."""
        engine = str(engine or "").strip().lower()
        if engine not in {"whisper", "sensevoice", "ocr", "capcut"}:
            engine = _default_asr_engine()
        previous = self.get_transcription_engine()
        os.environ["TRANSCRIPTION_ENGINE"] = engine
        state = getattr(self, "current_project_state", None)
        if state is not None:
            state.set_setting("transcription_engine", engine)
            self.project_service.save_project(state)
        if hasattr(self, "audio_source_combo"):
            idx = self.audio_source_combo.findData(engine)
            if idx >= 0 and self.audio_source_combo.currentIndex() != idx:
                self.audio_source_combo.blockSignals(True)
                self.audio_source_combo.setCurrentIndex(idx)
                self.audio_source_combo.blockSignals(False)
        if engine != previous:
            # A new OCR project needs the crop editor immediately. Existing
            # projects with completed OCR remain unobstructed until the user
            # explicitly reopens the region tool.
            if engine == "ocr" and not self.current_segments and not self.transcript_text.toPlainText().strip():
                self._ocr_overlay_visible = True
            timeline = getattr(self, "timeline", None)
            if timeline is not None:
                timeline.clear_selection_range()
            self._alternate_ocr_range_pending = None
            overlay = getattr(self, "ocr_region_overlay", None)
            if overlay is not None:
                overlay.set_editable(False)
                overlay.hide()
            button = getattr(self, "timeline_alt_transcribe_btn", None)
            if button is not None:
                self._update_alt_transcribe_button_label()
            self.log(f"[Subtitle Source] Changed to {engine}; cleared Selection Range.")
        self.update_speaker_diarization_availability()
        self._update_ocr_overlay()

    def _alternate_transcription_engine(self) -> str:
        return "whisper" if self.get_transcription_engine() == "ocr" else "ocr"

    def _update_alt_transcribe_button_label(self) -> None:
        button = getattr(self, "timeline_alt_transcribe_btn", None)
        if button is None or getattr(self, "_alternate_range_transcription_worker", None) is not None:
            return
        if bool(getattr(self, "_alternate_ocr_range_pending", None)):
            button.setText("Run OCR")
            return
        button.setText("Alt Transcribe")
        button.setToolTip("Transcribe the Selection Range with custom Whisper or OCR settings")

    def _resolve_active_voice_name(self, *, persist_new_clone: bool = False) -> str:
        free_value = str(self.free_voice_combo.currentData() or "").strip() if hasattr(self, "free_voice_combo") else ""
        if free_value and free_value.startswith("edge:"):
            return free_value
        if free_value and (free_value.startswith("vieneu:") or free_value.startswith("vieneu_clone:")):
            return free_value
        if free_value and free_value.startswith("capcut:"):
            return free_value
        if free_value and free_value in getattr(self, "voice_catalog_map", {}):
            return free_value
        # A gender filter can legitimately leave the selector empty. Do not
        # silently fall back to a voice outside the user's selected filter.
        combo_has_items = bool(hasattr(self, "free_voice_combo") and self.free_voice_combo.count())
        target_language = self.get_target_language_code()
        current_engine = self._current_voice_engine_key()
        if current_engine == "vieneu":
            if hasattr(self, "free_voice_combo") and self.free_voice_combo.count() > 0:
                fallback_value = str(self.free_voice_combo.itemData(0) or "").strip()
                if fallback_value:
                    return fallback_value
                fallback_entry_id = str(self.free_voice_combo.itemData(0, self.VOICE_ENTRY_ID_ROLE) or "").strip()
                if fallback_entry_id:
                    return fallback_entry_id
            return "vieneu:Minh Đức"
        if current_engine == "capcut":
            if hasattr(self, "free_voice_combo") and self.free_voice_combo.count() > 0:
                fallback_value = str(self.free_voice_combo.itemData(0) or "").strip()
                if fallback_value:
                    return fallback_value
                fallback_entry_id = str(self.free_voice_combo.itemData(0, self.VOICE_ENTRY_ID_ROLE) or "").strip()
                if fallback_entry_id:
                    return fallback_entry_id
            return "capcut:BV421_vivn_streaming"
        if (
            combo_has_items
            and target_language == "vi"
            and "ngochuyen" in getattr(self, "voice_catalog_map", {})
            and self.free_voice_combo.findData("ngochuyen") >= 0
        ):
            return "ngochuyen"
        if (
            combo_has_items
            and target_language == "vi"
            and "vi_VN-vais1000-medium" in getattr(self, "voice_catalog_map", {})
            and self.free_voice_combo.findData("vi_VN-vais1000-medium") >= 0
        ):
            return "vi_VN-vais1000-medium"
        if hasattr(self, "free_voice_combo") and self.free_voice_combo.count() > 0:
            fallback_value = str(self.free_voice_combo.itemData(0) or "").strip()
            if fallback_value:
                return fallback_value
            fallback_entry_id = str(self.free_voice_combo.itemData(0, self.VOICE_ENTRY_ID_ROLE) or "").strip()
            if fallback_entry_id:
                return fallback_entry_id
        return ""

    def on_voice_engine_changed(self, *args):
        self._voiceover_force_refresh = True
        if not getattr(self, "voice_catalog_entries_all", None):
            self.load_voice_preview_catalog()
            return
        engine = self._current_voice_engine_key()
        if hasattr(self, "voice_selector_label"):
            self.voice_selector_label.setText("Voice model")
        if hasattr(self, "create_voice_clone_btn"):
            self.create_voice_clone_btn.setVisible(engine == "vieneu")
        self.refresh_voice_catalog_combos()
        self._update_voice_preview_meta()

    def load_voice_preview_catalog(self):
        self._auto_sync_piper_voices_to_catalog()
        self.voice_catalog_entries_all = self.voice_catalog_service.load_catalog()
        # A packaged build keeps its catalog under _internal (read-only), so
        # newly downloaded piper-new voices may not be persisted there. Merge
        # the local manifest in memory as well, ensuring their names/genders
        # are immediately available to the selector and gender filter.
        self._merge_piper_manifest_voices()
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

    def _load_piper_voice_manifest(self) -> dict[str, dict]:
        """Load piper-new's shared voice metadata keyed by model stem."""
        manifest_path = models_path("piper", "voices.json")
        if not os.path.isfile(manifest_path):
            return {}
        try:
            with open(manifest_path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        entries: dict[str, dict] = {}
        if not isinstance(payload, list):
            return entries
        for item in payload:
            if not isinstance(item, dict):
                continue
            audio_path = str(item.get("audio_path", "") or "").replace("\\", "/").strip()
            voice_id = os.path.splitext(os.path.basename(audio_path))[0]
            if voice_id:
                entries[voice_id] = dict(item)
        return entries

    def _merge_piper_manifest_voices(self) -> None:
        """Merge local piper-new models/metadata into the in-memory catalog."""
        manifest = self._load_piper_voice_manifest()
        if not manifest:
            return
        existing = {
            str(entry.get("id", "")).strip(): entry
            for entry in (self.voice_catalog_entries_all or [])
            if isinstance(entry, dict) and str(entry.get("id", "")).strip()
        }
        for voice_id, meta in manifest.items():
            model_path = models_path("piper", f"{voice_id}.onnx")
            if not os.path.isfile(model_path) or os.path.getsize(model_path) <= 0:
                continue
            name = str(meta.get("name", "") or "").strip() or voice_id
            gender = self._normalize_gender_value(str(meta.get("gender", "") or ""))
            entry = existing.get(voice_id)
            if entry is None:
                entry = {
                    "id": voice_id,
                    "name": name,
                    "provider": "piper",
                    "provider_voice": f"models/piper/{voice_id}.onnx",
                    "language": "vi",
                    "gender": gender,
                    "tier": "free",
                    "preview_video_url": "",
                    "preview_video_path": "",
                    "preview_audio_url": "",
                    "preview_audio_path": "",
                    "enabled": True,
                    "tags": ["local", "piper"],
                }
                if str(meta.get("description", "") or "").strip():
                    entry["description"] = str(meta.get("description")).strip()
                self.voice_catalog_entries_all.append(entry)
                existing[voice_id] = entry
                continue
            # Manifest metadata is authoritative for piper-new display data,
            # but keep project/catalog-specific fields such as preview media.
            if name and str(entry.get("name", "")).strip() != name:
                entry["name"] = name
            if gender and self._normalize_gender_value(str(entry.get("gender", ""))) != gender:
                entry["gender"] = gender
            if str(meta.get("description", "") or "").strip():
                entry["description"] = str(meta.get("description")).strip()

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
        # Include both writable and bundled roots.  In a packaged build the
        # writable models/piper directory can exist as an empty download
        # target, and must not hide voices shipped under _internal/models.
        model_directories = []
        seen_directories = set()
        for root in (self.workspace_root, bundle_root()):
            for folder, relative_dir in (("piper", "models/piper"), ("piper-en", "models/piper-en")):
                directory = os.path.join(root, "models", folder)
                if directory in seen_directories:
                    continue
                seen_directories.add(directory)
                model_directories.append((directory, relative_dir))
        model_directories = tuple(model_directories)
        if not any(os.path.isdir(path) for path, _relative_path in model_directories):
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
            # Legacy packs have one config beside each model.  The current
            # piper-new Vietnamese pack shares models/piper/config.json, so
            # fall back to the containing directory when the per-model file
            # is absent.
            folder_name = os.path.basename(os.path.dirname(model_path))
            shared_config = os.path.join(os.path.dirname(model_path), "config.json")
            candidates = (
                [shared_config, f"{model_path}.json"]
                if folder_name.lower() == "piper"
                else [f"{model_path}.json", shared_config]
            )
            if folder_name:
                candidates.append(models_path(folder_name, "config.json"))
            head = ""
            for cfg_path in candidates:
                if not os.path.isfile(cfg_path):
                    continue
                try:
                    with open(cfg_path, "r", encoding="utf-8", errors="ignore") as handle:
                        head = handle.read(16384)
                    if head:
                        break
                except Exception:
                    continue
            if not head:
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

        def provider_voice_for_model(model_path: str, relative_dir: str) -> str:
            return f"{relative_dir}/{os.path.basename(model_path)}"

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

        model_paths = []
        for models_dir, relative_dir in model_directories:
            if not os.path.isdir(models_dir):
                continue
            model_paths.extend(
                (os.path.join(models_dir, name), relative_dir)
                for name in os.listdir(models_dir)
                if name.lower().endswith(".onnx")
            )
        model_paths.sort(key=lambda item: (item[1], os.path.basename(item[0]).lower()))
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

        for model_path, relative_dir in model_paths:
            voice_id = os.path.splitext(os.path.basename(model_path))[0]
            model_ids.add(voice_id)
            pv = provider_voice_for_model(model_path, relative_dir)
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
        target_language = self.get_target_language_code()
        current_engine = self._current_voice_engine_key()
        for entry in (self.voice_catalog_entries_all or []):
            if not entry or not isinstance(entry, dict):
                continue
            if not entry.get("enabled", True):
                continue
            provider = str(entry.get("provider", "")).strip().lower()
            if current_engine == "vieneu":
                if provider != "vieneu":
                    continue
            elif current_engine == "capcut":
                if provider != "capcut":
                    continue
            else:
                if provider not in {"piper", "edge"}:
                    continue
            entry_language = str(entry.get("language", "")).strip().lower().split("-", 1)[0]
            if entry_language and entry_language != target_language:
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
        if previous_free and self.free_voice_combo.findData(previous_free) >= 0:
            self.set_voice_combo_value(self.free_voice_combo, previous_free)
        elif current_engine == "vieneu":
            if self.free_voice_combo.findData("vieneu:Minh Đức") >= 0:
                self.set_voice_combo_value(self.free_voice_combo, "vieneu:Minh Đức")
            elif self.free_voice_combo.count() > 0:
                self.free_voice_combo.setCurrentIndex(0)
        elif current_engine == "capcut":
            if self.free_voice_combo.findData("capcut:BV421_vivn_streaming") >= 0:
                self.set_voice_combo_value(self.free_voice_combo, "capcut:BV421_vivn_streaming")
            elif self.free_voice_combo.count() > 0:
                self.free_voice_combo.setCurrentIndex(0)
        elif target_language == "vi" and "ngochuyen" in self.voice_catalog_map:
            self.set_voice_combo_value(self.free_voice_combo, "ngochuyen")
        elif target_language == "vi" and "vi_VN-vais1000-medium" in self.voice_catalog_map:
            self.set_voice_combo_value(self.free_voice_combo, "vi_VN-vais1000-medium")
        if not self._voice_signals_bound:
            self._voice_signals_bound = True
        self.on_voice_tier_changed()
        self._update_voice_preview_meta()
        self.refresh_detected_speakers_section()

    def on_voice_gender_changed(self):
        self.refresh_voice_catalog_combos()

    def on_target_language_changed(self, _index: int = -1):
        """Show and select only local voices that match the output language."""
        self._voiceover_force_refresh = True
        if getattr(self, "voice_catalog_entries_all", None):
            self.refresh_voice_catalog_combos()

    def on_selected_voice_changed(self):
        self._update_voice_preview_meta()
        self._preload_active_voice_if_needed()

    def _preload_active_voice_if_needed(self):
        voice_name = self.get_active_voice_name()
        if not voice_name:
            return
        if str(voice_name).startswith("f5:"):
            return
        entry_id = str(self.free_voice_combo.currentData(self.VOICE_ENTRY_ID_ROLE) or '').strip() if hasattr(self, 'free_voice_combo') else ''
        entry = self.voice_catalog_map.get(entry_id) if hasattr(self, 'voice_catalog_map') else None
        provider = str((entry or {}).get('provider', '')).strip().lower()
        if provider not in ('piper', 'vieneu'):
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
        return self._resolve_active_voice_name(persist_new_clone=False)

    @staticmethod
    def _speaker_sort_key(speaker: str) -> tuple[int, str]:
        value = str(speaker or "").strip()
        try:
            return (int(value.rsplit("_", 1)[-1]), value)
        except (TypeError, ValueError):
            return (9999, value)

    @staticmethod
    def _speaker_color_hex(speaker: str) -> str:
        value = str(speaker or "").strip()
        try:
            index = int(value.rsplit("_", 1)[-1])
        except (TypeError, ValueError):
            index = sum(ord(char) for char in value)
        return QColor.fromHsv((index * 137 + 20) % 360, 155, 205).name()

    def _uses_speaker_subtitle_colors(self) -> bool:
        checkbox = getattr(self, "subtitle_speaker_colors_cb", None)
        return bool(checkbox and checkbox.isChecked())

    def _subtitle_color_for_segment(self, segment: dict | None) -> QColor:
        speaker = str((segment or {}).get("speaker", "") or "").strip()
        if self._uses_speaker_subtitle_colors() and speaker:
            return QColor(self._speaker_color_hex(speaker))
        return QColor(self.subtitle_color_hex)

    def _apply_live_subtitle_segment_color(self, segment: dict | None) -> None:
        item = getattr(getattr(self, "video_view", None), "subtitle_item", None)
        if item is None:
            return
        color = self._subtitle_color_for_segment(segment)
        if color != getattr(item, "font_color", None):
            item.font_color = color
            item.update()

    def _refresh_speaker_subtitle_colors_if_needed(self) -> None:
        """Rebuild the ASS preview only when speaker IDs affect its colors."""
        if self._uses_speaker_subtitle_colors():
            self.update_subtitle_preview_style()

    def _detected_speaker_ids(self) -> list[str]:
        segments = list(
            getattr(self, "current_translated_segments", None)
            or getattr(self, "current_segments", None)
            or []
        )
        return sorted(
            {
                str(segment.get("speaker", "") or "").strip()
                for segment in segments
                if str(segment.get("speaker", "") or "").strip()
            },
            key=self._speaker_sort_key,
        )

    @staticmethod
    def _speaker_display_name(speaker: str, position: int) -> str:
        """Use stable automatic labels; diarization IDs remain internal."""
        if 0 <= position < 26:
            return f"Speaker {chr(ord('A') + position)}"
        return f"Speaker {position + 1}"

    def _speaker_voice_assignments(self) -> dict:
        state = getattr(self, "current_project_state", None)
        raw = state.settings.get("speaker_voice_assignments", {}) if state is not None else {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _save_speaker_voice_assignment(
        self,
        speaker: str,
        *,
        name: str | None = None,
        voice: str | None = None,
        voice_gender_filter: str | None = None,
    ) -> None:
        state = getattr(self, "current_project_state", None)
        speaker = str(speaker or "").strip()
        if state is None or not speaker:
            return
        assignments = self._speaker_voice_assignments()
        entry = dict(assignments.get(speaker, {}) or {})
        if name is not None:
            entry["name"] = str(name or "").strip()
        if voice is not None:
            entry["voice"] = str(voice or "").strip()
        if voice_gender_filter is not None:
            entry["voice_gender_filter"] = str(voice_gender_filter or "Any").strip() or "Any"
        assignments[speaker] = entry
        state.set_setting("speaker_voice_assignments", assignments)
        self.project_service.save_project(state)
        self._voiceover_force_refresh = True

    def _voice_display_entries(
        self,
        *,
        gender: str = "any",
        include_voice: str = "",
    ) -> list[tuple[str, str]]:
        """Return gender-filtered voices for a speaker row independently.

        ``free_voice_combo`` intentionally represents only Voice Setup.  A
        speaker's filter/search must never depend on that combo's contents.
        Keep an already assigned voice visible while filtering so changing a
        filter cannot silently replace the speaker's mapping.
        """
        wanted_gender = self._normalize_gender_value(gender)
        assigned = str(include_voice or "").strip()
        entries: list[tuple[str, str]] = []
        for entry in sorted(list(getattr(self, "voice_catalog_entries", []) or []), key=self._voice_entry_sort_key):
            value = self._voice_catalog_data_value(entry)
            if not value:
                continue
            entry_gender = self._normalize_gender_value(str(entry.get("gender", "")))
            label = str(entry.get("name", entry.get("id", "Voice")) or "Voice")
            gender_match = wanted_gender not in {"male", "female"} or entry_gender in {wanted_gender, "", "any"}
            if gender_match or value == assigned:
                entries.append((label, value))
        return entries

    def refresh_detected_speakers_section(self) -> None:
        card = getattr(self, "detected_speakers_card", None)
        layout = getattr(self, "detected_speakers_list_layout", None)
        if card is None or layout is None:
            return
        # Voice catalog initialization happens during UI construction, before
        # a project (and therefore the segment lists) necessarily exists.
        segments = list(
            getattr(self, "current_translated_segments", None)
            or getattr(self, "current_segments", None)
            or []
        )
        speakers = self._detected_speaker_ids()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        card.setVisible(bool(speakers))
        if not speakers:
            if hasattr(self, "timeline"):
                self.timeline.set_highlighted_speaker("")
            return
        assignments = self._speaker_voice_assignments()
        for position, speaker in enumerate(speakers):
            entry = dict(assignments.get(speaker, {}) or {})
            display_name = self._speaker_display_name(speaker, position)
            segment_count = sum(
                1 for segment in segments
                if str(segment.get("speaker", "") or "").strip() == speaker
            )
            row = QFrame()
            row.setObjectName("statusCard")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(9, 8, 9, 8)
            row_layout.setSpacing(6)
            header = QHBoxLayout()
            indicator = QLabel()
            indicator.setFixedSize(12, 12)
            indicator.setStyleSheet(
                f"background: {self._speaker_color_hex(speaker)}; border-radius: 6px; border: 1px solid #dcecff;"
            )
            header.addWidget(indicator)
            speaker_label = QLabel(f"{display_name}  ·  {segment_count} segment{'s' if segment_count != 1 else ''}")
            speaker_label.setToolTip(f"Timeline ID: {speaker}")
            header.addWidget(speaker_label, 1)
            row_layout.addLayout(header)
            row_layout.addWidget(QLabel("Voice type"))
            gender_combo = QComboBox()
            gender_combo.addItems(["Any", "Male", "Female"])
            saved_gender = str(entry.get("voice_gender_filter", "Any") or "Any").strip().title()
            gender_combo.setCurrentText(saved_gender if saved_gender in {"Any", "Male", "Female"} else "Any")
            row_layout.addWidget(gender_combo)
            row_layout.addWidget(QLabel("Voice"))
            voice_combo = QComboBox()
            assigned_voice = str(entry.get("voice", "") or "")
            row_layout.addWidget(voice_combo)

            def _refresh_speaker_voice_combo(
                *,
                combo=voice_combo,
                filter_combo=gender_combo,
                assigned=assigned_voice,
            ):
                # ``"Use default voice"`` intentionally has an empty value;
                # do not fall back to the original assignment in that case.
                current_assigned = (
                    str(combo.currentData() or "")
                    if combo.count()
                    else str(assigned or "")
                )
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("Use default voice", "")
                for label, value in self._voice_display_entries(
                    gender=filter_combo.currentText(),
                    include_voice=current_assigned,
                ):
                    combo.addItem(label, value)
                voice_index = combo.findData(current_assigned)
                combo.setCurrentIndex(voice_index if voice_index >= 0 else 0)
                combo.blockSignals(False)

            _refresh_speaker_voice_combo()
            voice_combo.currentIndexChanged.connect(
                lambda _index, sp=speaker, combo=voice_combo: self._save_speaker_voice_assignment(
                    sp, voice=str(combo.currentData() or "")
                )
            )
            gender_combo.currentTextChanged.connect(
                lambda value, sp=speaker, refresh=_refresh_speaker_voice_combo: (
                    self._save_speaker_voice_assignment(sp, voice_gender_filter=value),
                    refresh(),
                )
            )
            voice_combo.activated.connect(
                lambda _index, sp=speaker: self.highlight_timeline_speaker(sp)
            )
            reassign_row = QHBoxLayout()
            reassign_row.setContentsMargins(0, 0, 0, 0)
            reassign_row.setSpacing(6)
            reassign_row.addWidget(QLabel("Move all to"))
            reassign_combo = QComboBox()
            for target_position, target_speaker in enumerate(speakers):
                if target_speaker != speaker:
                    reassign_combo.addItem(
                        self._speaker_display_name(target_speaker, target_position),
                        target_speaker,
                    )
            reassign_button = QPushButton("Apply")
            reassign_button.setToolTip(
                f"Reassign every {display_name} subtitle segment to the selected speaker."
            )
            has_target = reassign_combo.count() > 0
            reassign_combo.setEnabled(has_target)
            reassign_button.setEnabled(has_target)
            reassign_button.clicked.connect(
                lambda _checked=False, source=speaker, combo=reassign_combo: self.reassign_all_speaker_segments(
                    source, str(combo.currentData() or "")
                )
            )
            reassign_row.addWidget(reassign_combo, 1)
            reassign_row.addWidget(reassign_button)
            row_layout.addLayout(reassign_row)
            row.mousePressEvent = lambda event, sp=speaker, original=row.mousePressEvent: (
                self.toggle_timeline_speaker_highlight(sp), original(event)
            )[-1]
            layout.addWidget(row)
        layout.addStretch()

    def highlight_timeline_speaker(self, speaker: str) -> None:
        if hasattr(self, "timeline"):
            self.timeline.set_highlighted_speaker(speaker)

    def toggle_timeline_speaker_highlight(self, speaker: str) -> None:
        """Toggle the presentation-only speaker highlight from its card."""
        timeline = getattr(self, "timeline", None)
        if timeline is None:
            return
        selected = str(speaker or "").strip()
        current = str(getattr(timeline, "_highlighted_speaker", "") or "").strip()
        timeline.set_highlighted_speaker("" if selected and selected == current else selected)

    def _apply_speaker_voice_assignments(self, segments: list[dict]) -> list[dict]:
        assignments = self._speaker_voice_assignments()
        if not assignments:
            return [dict(segment) for segment in segments or []]
        resolved = []
        for segment in segments or []:
            item = dict(segment)
            speaker = str(item.get("speaker", "") or "").strip()
            voice = str((assignments.get(speaker, {}) or {}).get("voice", "") or "").strip()
            if voice:
                item["voice_name"] = voice
            resolved.append(item)
        return resolved

    def on_segment_speaker_changed(self, index: int, speaker: str) -> None:
        """Apply a manual diarization correction without rerunning analysis."""
        if getattr(self, "_syncing_segment_editor", False):
            return
        speaker = str(speaker or "").strip()
        updated = False
        for segments_list in (
            getattr(self, "current_segments", None),
            getattr(self, "current_translated_segments", None),
        ):
            if segments_list and 0 <= index < len(segments_list):
                if speaker:
                    segments_list[index]["speaker"] = speaker
                else:
                    segments_list[index].pop("speaker", None)
                updated = True
        if not updated:
            return
        self._sync_segment_models_from_current_segments()
        self._voiceover_force_refresh = True
        # Speaker identity changes the TS1 color and future voice selection,
        # not subtitle text, timing, or visual style.  Avoid the full
        # apply_segments_to_timeline() path here because it refreshes the
        # live subtitle preview assets and rewrites its SRT unnecessarily.
        if hasattr(self, "timeline"):
            self.timeline.set_segments(self.get_active_segments())
            self.timeline.set_active_segment_index(index)
        self._refresh_speaker_subtitle_colors_if_needed()
        self.refresh_detected_speakers_section()
        self.persist_current_timeline_project_data()

    def reassign_all_speaker_segments(self, source_speaker: str, target_speaker: str) -> None:
        """Move every cue from one diarized speaker to another."""
        source_speaker = str(source_speaker or "").strip()
        target_speaker = str(target_speaker or "").strip()
        if not source_speaker or not target_speaker or source_speaker == target_speaker:
            return
        changed_indexes: set[int] = set()
        for segments_list in (
            getattr(self, "current_segments", None),
            getattr(self, "current_translated_segments", None),
        ):
            if not segments_list:
                continue
            for index, segment in enumerate(segments_list):
                if str(segment.get("speaker", "") or "").strip() == source_speaker:
                    segment["speaker"] = target_speaker
                    changed_indexes.add(index)
        if not changed_indexes:
            return
        self._sync_segment_models_from_current_segments()
        self._voiceover_force_refresh = True
        if hasattr(self, "timeline"):
            self.timeline.set_segments(self.get_active_segments())
            self.timeline.set_highlighted_speaker(target_speaker)
        self._refresh_speaker_subtitle_colors_if_needed()
        self.refresh_detected_speakers_section()
        self.persist_current_timeline_project_data()
        self.log(
            f"[Diarization] Reassigned {len(changed_indexes)} segment(s) "
            f"from {source_speaker} to {target_speaker}."
        )

    def on_voice_tier_changed(self):
        mode = self.get_output_mode_key() if hasattr(self, "output_mode_combo") else "both"
        if hasattr(self, "free_voice_combo"):
            self.free_voice_combo.setEnabled(True)
        if hasattr(self, "preview_voice_btn"):
            # Preview Voice belongs to Voice Setup and should remain available
            # whenever the Voice panel is open, independent of output-mode
            # compatibility state.
            self.preview_voice_btn.setVisible(True)
        self._update_voice_preview_meta()

    def _parse_voice_speed_value(self) -> float:
        raw = str(getattr(self, "voice_speed_spin", None).currentText() if getattr(self, "voice_speed_spin", None) else "1.0x").strip().lower()
        raw = raw.replace("x", "")
        try:
            return float(raw or "1.0")
        except ValueError:
            return 1.0

    def _percent_to_db(self, percent: int) -> float:
        """Convert volume percentage (0-200) to dB gain."""
        if percent <= 0:
            return -60.0
        import math
        return 20.0 * math.log10(percent / 100.0)

    # -----------------------------
    # Logging + error helpers
    # -----------------------------
    def log(self, message: str):
        log_message_impl(self, message)

    def _append_runtime_log_entry(self, message: str):
        from datetime import datetime

        text = str(message or "").strip()
        if not text:
            return
        entry = f"{datetime.now().strftime('%H:%M:%S')}  {text}"
        self._pending_runtime_log_entries.append(entry)
        if not self._runtime_log_flush_timer.isActive():
            self._runtime_log_flush_timer.start()

    def _flush_runtime_log_entries(self):
        entries = self._pending_runtime_log_entries
        self._pending_runtime_log_entries = []
        if not entries:
            return
        self._runtime_logs.extend(entries)
        if len(self._runtime_logs) > 10000:
            del self._runtime_logs[:-10000]
        view = getattr(self, "runtime_log_view", None)
        # The Logs view belongs to the Advanced workflow page. Keep the
        # in-memory log complete, but defer text layout/repaint work until
        # the user actually opens that page.
        if view is not None and view.isVisible():
            already_rendered = int(getattr(self, "_runtime_log_view_entry_count", 0))
            if already_rendered != len(self._runtime_logs) - len(entries):
                view.setPlainText("\n".join(self._runtime_logs))
            else:
                view.appendPlainText("\n".join(entries))
            self._runtime_log_view_entry_count = len(self._runtime_logs)

    def sync_runtime_log_view(self, *_args):
        """Populate deferred runtime logs when the Advanced page is shown."""
        view = getattr(self, "runtime_log_view", None)
        if view is None or not view.isVisible():
            return
        logs = getattr(self, "_runtime_logs", [])
        if int(getattr(self, "_runtime_log_view_entry_count", 0)) == len(logs):
            return
        view.setPlainText("\n".join(logs))
        self._runtime_log_view_entry_count = len(logs)

    def clear_log(self):
        clear_log_impl(self)

    def export_runtime_logs(self):
        default_name = f"capcap_logs_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        default_path = os.path.join(self.workspace_root, default_name)
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Runtime Logs",
            default_path,
            "Text Files (*.txt);;All Files (*)",
        )
        if not file_path:
            return
        try:
            self._flush_runtime_log_entries()
            with open(file_path, "w", encoding="utf-8") as handle:
                entries = getattr(self, "_runtime_logs", [])
                handle.write("\n".join(entries))
                handle.write("\n" if entries else "")
            self.log(f"[Logs] Exported runtime logs to {file_path}")
        except OSError as exc:
            QMessageBox.warning(self, "Export Logs", f"Could not export logs:\n{exc}")

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
        try:
            button.setVisible(bool(tracked))
            button.setEnabled(bool(tracked))
        except RuntimeError:
            # Qt can destroy the toolbar button before a tracked progress
            # dialog emits its destroyed signal during application shutdown.
            pass

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

    def _create_vietdict_template(self, resource_id: str):
        # Kept as a compatibility no-op for old callers.  Dictionaries are
        # now stored in project state and are edited in Subtitle Inspector.
        return
        """

        acronyms_path = os.path.join("", "acronyms.csv")
        if not os.path.exists(acronyms_path):
            with open(acronyms_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["acronym", "transliteration"])
                w.writerow(["vtv", "vô tuyến truyền hình"])
                w.writerow(["CLB", "câu lạc bộ"])
            print(f"[VietDict] Created template: {acronyms_path}")

        nonvn_path = os.path.join(dir_path, "non-vietnamese-words.csv")
        if not os.path.exists(nonvn_path):
            with open(nonvn_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["original", "transliteration"])
                w.writerow(["iPhone", "ai phôn"])
            print(f"[VietDict] Created template: {nonvn_path}")

        os.startfile(dir_path)

        """

    def _vietdict_add_row(self, table):
        from PySide6.QtWidgets import QTableWidgetItem
        r = table.rowCount()
        table.insertRow(r)
        table.setItem(r, 0, QTableWidgetItem(""))
        table.setItem(r, 1, QTableWidgetItem(""))
        table.scrollToBottom()

    def _vietdict_remove_row(self, table):
        rows = sorted({idx.row() for idx in table.selectedIndexes()}, reverse=True)
        for r in rows:
            table.removeRow(r)

    def open_normalizer_dict_dialog(self):
        """Edit the pronunciation dictionary stored in this project.

        This is deliberately project state rather than a global CSV resource:
        the same word may need different pronunciation in different videos.
        The dictionary is passed to Piper only; subtitle text is untouched.
        """
        if not str(self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""):
            QMessageBox.information(self, "Normalizer Dictionary", "Open a project before editing its pronunciation dictionary.")
            return
        state = self.ensure_current_project()
        if state is None:
            QMessageBox.information(self, "Normalizer Dictionary", "Open a project before editing its pronunciation dictionary.")
            return
        if not self._translation_phase_complete():
            QMessageBox.information(self, "Normalizer Dictionary", "Complete the Translation phase before editing the project dictionary.")
            return

        DICT_DEFS = [
            {"label": "Acronyms", "key": "acronyms", "col_a": "acronym", "col_b": "transliteration"},
            {"label": "Non-Vietnamese Words", "key": "non_vietnamese_words", "col_a": "original", "col_b": "transliteration"},
        ]

        dialog = QDialog(self)
        dialog.setWindowTitle("Normalizer Dictionary")
        dialog.setModal(True)
        dialog.resize(700, 520)
        dialog.setStyleSheet("""
            QDialog { background-color: #0f1724; }
            QLabel { color: #d7e3f4; background-color: transparent; }
            QTableWidget { background-color: #132033; color: #d7e3f4; gridline-color: #2f4868;
                border: 1px solid #2f4868; border-radius: 8px; font-size: 13px; }
            QTableWidget::item:selected { background-color: #29405d; color: #f8fbff; }
            QHeaderView::section { background-color: #1a2c44; color: #8ad7ff; border: none;
                padding: 6px 8px; font-weight: 700; font-size: 12px; }
            QPushButton { background-color: #22344d; color: #f8fbff; border: 1px solid #34506f;
                border-radius: 8px; padding: 6px 16px; font-weight: 600; }
            QPushButton:hover { background-color: #29405d; }
            QPushButton#dangerBtn { background-color: #5a1a1a; border-color: #8b2a2a; }
            QPushButton#dangerBtn:hover { background-color: #7a2828; }
            QPushButton#primaryBtn { background-color: #1a4a5a; border-color: #2a6a8b; }
            QPushButton#primaryBtn:hover { background-color: #1e5a6e; }
            QTabWidget::pane { border: 1px solid #2f4868; background-color: #0f1724; border-radius: 8px; }
            QTabBar::tab { background-color: #1a2c44; color: #9fb3ca; padding: 8px 20px; border: 1px solid #2f4868;
                border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }
            QTabBar::tab:selected { background-color: #132033; color: #8ad7ff; }
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Manage Normalizer Dictionary", dialog)
        title.setStyleSheet("color: #f8fbff; font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        project_name = str(getattr(state, "project_id", "") or "current project")
        hint = QLabel(
            f"Project: {project_name}\n"
            "Entries affect Piper pronunciation only; translated subtitle text is not changed.",
            dialog,
        )
        hint.setStyleSheet("color: #9fb3ca; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        from PySide6.QtWidgets import QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView
        tabs = QTabWidget(dialog)
        layout.addWidget(tabs, 1)

        tables = {}

        for defn in DICT_DEFS:
            tab = QWidget(dialog)
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(8, 8, 8, 8)
            tab_layout.setSpacing(8)

            table = QTableWidget(0, 2, dialog)
            table.setHorizontalHeaderLabels([defn["col_a"].title(), defn["col_b"].title()])
            table.horizontalHeader().setStretchLastSection(True)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.verticalHeader().setVisible(False)
            tab_layout.addWidget(table, 1)

            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)
            add_btn = QPushButton("+ Add Row", dialog)
            remove_btn = QPushButton("Remove Selected", dialog)
            remove_btn.setObjectName("dangerBtn")
            btn_row.addWidget(add_btn)
            btn_row.addWidget(remove_btn)
            btn_row.addStretch()
            tab_layout.addLayout(btn_row)

            add_btn.clicked.connect(lambda _checked=False, t=table: self._vietdict_add_row(t))
            remove_btn.clicked.connect(lambda _checked=False, t=table: self._vietdict_remove_row(t))

            tables[defn["key"]] = {"table": table, "defn": defn}
            tabs.addTab(tab, defn["label"])

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        bottom_row.addStretch()

        save_btn = QPushButton("Save All", dialog)
        save_btn.setObjectName("primaryBtn")
        close_btn = QPushButton("Close", dialog)
        close_btn.clicked.connect(dialog.accept)
        bottom_row.addWidget(save_btn)
        bottom_row.addWidget(close_btn)
        layout.addLayout(bottom_row)

        def _load_all():
            settings = getattr(state, "settings", {}) or {}
            dictionary = settings.get("normalizer_dictionary", {}) or {}
            for key, meta in tables.items():
                rows = dictionary.get(key, []) if isinstance(dictionary, dict) else []
                table = meta["table"]
                table.setRowCount(0)
                if isinstance(rows, dict):
                    rows = [rows]
                for row in rows if isinstance(rows, (list, tuple)) else []:
                    if not isinstance(row, dict):
                        continue
                    a = str(row.get(meta["defn"]["col_a"], row.get("key", "")) or "").strip()
                    b = str(row.get(meta["defn"]["col_b"], row.get("value", "")) or "").strip()
                    if a or b:
                        r = table.rowCount()
                        table.insertRow(r)
                        table.setItem(r, 0, QTableWidgetItem(a))
                        table.setItem(r, 1, QTableWidgetItem(b))

        def _save_all():
            dictionary = {}
            for key, meta in tables.items():
                table = meta["table"]
                rows = []
                for r in range(table.rowCount()):
                    a = (table.item(r, 0).text() if table.item(r, 0) else "").strip()
                    b = (table.item(r, 1).text() if table.item(r, 1) else "").strip()
                    if a or b:
                        rows.append({meta["defn"]["col_a"]: a, meta["defn"]["col_b"]: b})
                dictionary[key] = rows
            try:
                state.set_setting("normalizer_dictionary", dictionary)
                self.current_project_state = state
                self.project_service.save_project(state)
                try:
                    from tts_processor import reset_vietnamese_normalizer_cache
                    reset_vietnamese_normalizer_cache()
                except Exception as exc:
                    self.log(f"[VietDict] Cache refresh warning: {exc}")
                self._voiceover_force_refresh = True
                self._pending_voice_signature = ""
                self.log(f"[VietDict] Saved project pronunciation dictionary ({project_name}).")
                self.refresh_ui_state()
                QMessageBox.information(dialog, "Normalizer Dictionary", "Project dictionary saved. New Preview Voice, Regenerate Voice, and TTS runs will use it immediately.")
            except Exception as exc:
                self.log(f"[VietDict] Save failed: {exc}")
                QMessageBox.critical(dialog, "Normalizer Dictionary", "Could not save the project dictionary. No pronunciation changes were applied.\n\n" + str(exc))

        save_btn.clicked.connect(_save_all)

        _load_all()
        dialog.exec()

    def _missing_resource_entries(self, *, include_whisper: bool = False, include_voice: bool = False, include_ocr: bool = False, validate_pipeline_runtime: bool = False) -> list[tuple[str, str]]:
        service = self._resource_service()
        missing: list[tuple[str, str]] = []

        if include_whisper and not is_remote_profile():
            engine = self.get_transcription_engine()
            if engine == "capcut":
                pass
            elif engine == "sensevoice":
                missing.extend(service.validate_sensevoice_runtime())
            else:
                model_name = self.get_whisper_model_name()
                resource_id = f"whisper:{model_name}"
                if not service.is_resource_installed(resource_id):
                    missing.append((resource_id, f"Whisper {model_name.title()} model"))

        if include_voice and not is_remote_profile():
            voice_name = self.get_active_voice_name()
            if (
                voice_name
                and not str(voice_name).startswith("edge:")
                and not str(voice_name).startswith("f5:")
                and not str(voice_name).startswith("vieneu:")
                and not str(voice_name).startswith("vieneu_clone:")
                and not str(voice_name).startswith("capcut:")
            ):
                resource_id = f"voice:{voice_name}"
                if not service.is_resource_installed(resource_id):
                    voice_label = voice_name
                    voice_entry = self.voice_catalog_map.get(voice_name) if hasattr(self, "voice_catalog_map") else None
                    if isinstance(voice_entry, dict):
                        voice_label = str(voice_entry.get("name", voice_name)).strip() or voice_name
                    missing.append((resource_id, f"Local voice: {voice_label}"))

        if include_ocr:
            missing.extend(service.validate_ocr_runtime())

        if include_voice and not is_remote_profile():
            active_v = self.get_active_voice_name()
            if (
                active_v
                and not str(active_v).startswith("edge:")
                and not str(active_v).startswith("f5:")
                and not str(active_v).startswith("vieneu:")
                and not str(active_v).startswith("vieneu_clone:")
                and not str(active_v).startswith("capcut:")
            ):
                missing.extend(service.validate_piper_voice_runtime(active_v))

        if validate_pipeline_runtime and not is_remote_profile():
            missing.extend(service.validate_pipeline_runtime())

        deduped: list[tuple[str, str]] = []
        seen = set()
        for item in missing:
            if item[0] in seen:
                continue
            seen.add(item[0])
            deduped.append(item)
        return deduped

    def ensure_required_resources(self, action_label: str, *, include_whisper: bool = False, include_voice: bool = False, include_ocr: bool = False, validate_pipeline_runtime: bool = False) -> bool:
        missing = self._missing_resource_entries(
            include_whisper=include_whisper,
            include_voice=include_voice,
            include_ocr=include_ocr,
            validate_pipeline_runtime=validate_pipeline_runtime,
        )
        if not missing:
            return True

        missing_lines = "\n".join(f"- {label}" for _resource_id, label in missing)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("CapCap Cannot Start This Step")
        box.setText(f"{action_label} cannot start because a required local component is unavailable.")
        box.setInformativeText(
            "The exact cause is listed below. Use Manage Resources for downloadable "
            "models, or fix the shown folder/permission problem before trying again:\n\n"
            f"{missing_lines}"
        )
        open_btn = box.addButton("Manage Resources", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self.open_resource_manager_dialog()
        return False

    def open_resource_manager_dialog(self):
        from views.resource_manager import open_resource_manager
        open_resource_manager(
            self.workspace_root,
            parent=self,
            on_finished=lambda: self._on_resource_download_complete(),
        )

    def _on_resource_download_complete(self):
        try:
            self.load_voice_preview_catalog()
        except Exception:
            pass
        self.refresh_ui_state()

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
        # The Audio Source selector was replaced by timeline audio tracks.
        # Legacy radio controls may still exist in older project/settings
        # data, but they are not part of the active UI and must not bypass
        # A1/TS1/Music track composition.
        if not hasattr(self, "existing_audio_source_panel"):
            return False
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
        music_tracks = self._music_audio_tracks()
        try:
            voice_path = self._resolve_preview_voice_only_audio_path()
        except Exception:
            voice_path = ""
        tts_state = self._tts_audio_track_state()
        if voice_path and not bool(tts_state.get("muted", False)) and float(tts_state.get("volume", 100.0)) > 0.0:
            return voice_path
        # A Music Layer is also a valid audio source for composed export when
        # TTS has not been generated (or has been muted). Respect its actual
        # volume/mute state so a zeroed track cannot re-enter export through a
        # stale raw-path fallback.
        for item in music_tracks:
            if bool(item.get("muted", False)) or float(item.get("volume", 0.0)) <= 0.0:
                continue
            candidate = self._normalize_local_file_path(item.get("path", ""))
            if candidate and os.path.exists(candidate):
                return candidate
        # Legacy projects without a usable timeline voice/music state may
        # still have a cached mixed_vi artifact. Only use it when no active
        # independent tracks are present, preventing old audio from bypassing
        # the new per-track volume controls.
        if not music_tracks and not voice_path:
            for candidate in (
                self.processed_artifacts.get("mixed_vi"),
                self.last_mixed_vi_path,
            ):
                normalized = self._normalize_local_file_path(candidate)
                if normalized and os.path.exists(normalized):
                    return normalized
        return ""

    def _music_audio_tracks(self) -> list[dict]:
        """Return active Music Layer entries in the timeline.

        Music is intentionally modeled separately from the source video's A1
        audio and the generated TS1 voice.  The returned dictionaries are the
        common input format used by preview and export mixing.
        """
        timeline = getattr(self, "timeline", None)
        model = getattr(timeline, "_timeline", None) if timeline is not None else None
        if model is None:
            return []
        tracks = []
        for track in getattr(model, "tracks", []) or []:
            metadata = getattr(track, "metadata", {}) or {}
            name = str(getattr(track, "name", "") or "")
            role = str(metadata.get("_audio_role", "") or "").strip().lower()
            if role != "music" and name.lower() not in {"a2 music", "music"}:
                continue
            try:
                track_volume = float(metadata.get("_volume", 30.0))
            except (TypeError, ValueError):
                track_volume = 30.0
            track_muted = bool(
                getattr(track, "muted", False)
                or metadata.get("_muted", False)
                or self._is_audio_track_muted(name)
            )
            track_visible = bool(getattr(track, "visible", True))
            track_solo = bool(getattr(track, "solo", False) or metadata.get("_solo", False))
            for layer in getattr(track, "layers", []) or []:
                source = self._normalize_local_file_path(str(getattr(layer, "source", "") or ""))
                if not source or not os.path.exists(source):
                    continue
                start = max(0.0, float(getattr(layer, "start", 0.0) or 0.0))
                end = max(start, float(getattr(layer, "end", 0.0) or 0.0))
                tracks.append({
                    "path": source,
                    "start": start,
                    "end": end,
                    "source_start": float(getattr(layer, "source_start", 0.0) or 0.0),
                    "volume": track_volume,
                    "muted": (
                        track_muted
                        or not track_visible
                        or not bool(getattr(layer, "visible", True))
                        or bool(getattr(layer, "muted", False))
                    ),
                    "solo": track_solo,
                    "loop": True,
                    "track_name": name or "A2 Music",
                    "legacy": False,
                })
        return tracks

    def _tts_audio_track_state(self) -> dict:
        """Return the generated voice track's timeline volume/mute state."""
        timeline = getattr(self, "timeline", None)
        model = getattr(timeline, "_timeline", None) if timeline is not None else None
        state = {"volume": 100.0, "muted": False, "solo": False, "track_name": "TS1"}
        for track in getattr(model, "tracks", []) or [] if model is not None else []:
            name = str(getattr(track, "name", "") or "")
            if name not in ("TS1", "A2 Dub"):
                continue
            metadata = getattr(track, "metadata", {}) or {}
            try:
                state["volume"] = float(metadata.get("_volume", 100.0))
            except (TypeError, ValueError):
                state["volume"] = 100.0
            state["muted"] = bool(
                getattr(track, "muted", False)
                or metadata.get("_muted", False)
                or self._is_audio_track_muted(name)
            )
            state["solo"] = bool(getattr(track, "solo", False) or metadata.get("_solo", False))
            state["track_name"] = name
            break
        return state

    def _audio_track_is_soloed(self, track_name: str) -> bool:
        timeline = getattr(self, "timeline", None)
        model = getattr(timeline, "_timeline", None) if timeline is not None else None
        if model is None:
            return False
        for track in getattr(model, "tracks", []) or []:
            name = str(getattr(track, "name", "") or "")
            metadata = getattr(track, "metadata", {}) or {}
            if name == track_name:
                return bool(getattr(track, "solo", False) or metadata.get("_solo", False))
        return False

    def _audio_total_duration_ms(self) -> int:
        try:
            duration = int(getattr(self.media_player, "duration", lambda: 0)() or 0)
        except Exception:
            duration = 0
        if duration <= 0:
            try:
                duration = int(getattr(self.timeline, "duration", 0) or 0)
            except Exception:
                duration = 0
        return max(0, duration)

    def _refresh_music_layer_summary(self):
        label = getattr(self, "music_layers_summary_label", None)
        entries = self._music_audio_tracks()
        # The optional volume row is shown only for a real timeline Music
        # Layer.  Legacy project-level music artifacts remain usable for
        # playback/export but should not make the optional control appear as
        # if a new layer had been added.
        volume_row = getattr(self, "audio_music_volume_row", None)
        if volume_row is not None:
            volume_row.setVisible(any(not bool(item.get("legacy", False)) for item in entries))
        if label is None:
            return
        if not entries:
            label.setText("No music layer added.")
            return
        names = []
        for entry in entries:
            names.append(os.path.basename(str(entry.get("path", ""))) or "Music")
        label.setText(f"{len(names)} music layer(s): " + ", ".join(names))

    def add_music_layer(self):
        """Choose an audio file and add it as an independent timeline track."""
        if self._preview_is_playing():
            return
        video_path = str(getattr(self, "video_path_edit", None).text() if hasattr(self, "video_path_edit") else "").strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.information(self, "Add Music Layer", "Select a video before adding music.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Background Music",
            "",
            "Audio Files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg);;All Files (*)",
        )
        if not path:
            return
        normalized = self._normalize_local_file_path(path)
        if not normalized or not os.path.exists(normalized):
            return
        timeline = getattr(self, "timeline", None)
        model = getattr(timeline, "_timeline", None) if timeline is not None else None
        if model is None:
            return
        from app.layers.audio import AudioLayer
        from app.layers.base import LayerType
        from app.layers.sync_bridge import find_or_create_track

        duration = max(0.1, float(getattr(model, "duration", 0.0) or 0.0))
        track = find_or_create_track(model, "A2 Music", LayerType.AUDIO, 80)
        if not isinstance(track.metadata, dict):
            track.metadata = {}
        track.metadata["_audio_role"] = "music"
        track.metadata.setdefault("_volume", 30.0)
        track.metadata.setdefault("_muted", False)
        layer = AudioLayer(
            name=f"Music {len(track.layers) + 1}",
            source=normalized,
            start=0.0,
            end=duration,
            volume=float(track.metadata.get("_volume", 30.0)) / 100.0,
        )
        layer.metadata["_audio_role"] = "music"
        track.layers.append(layer)
        if hasattr(timeline, "_track_heights"):
            timeline._track_heights[track.id] = int(track.height or 80)
        try:
            self.bg_music_edit.setText(normalized)
        except Exception:
            pass
        self.last_music_path = normalized
        self.processed_artifacts["music"] = normalized
        if hasattr(self, "update_project_artifact"):
            self.update_project_artifact("music", normalized)
        timeline._redraw()
        self._sync_audio_mix_controls_from_tracks()
        self._refresh_music_layer_summary()
        try:
            timeline.select_layer(layer.id)
            self.on_timeline_layer_selected(layer.id)
        except Exception:
            pass
        self.persist_current_timeline_project_data()
        self._schedule_preview_audio_refresh(force=True)

    def _schedule_preview_audio_refresh(self, *, force: bool = False):
        if getattr(self, "_preview_audio_track_switching", False):
            return
        timer = getattr(self, "_music_preview_refresh_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(120)
            timer.timeout.connect(lambda: self.sync_preview_audio_track_to_output(apply_to_player=True, force=True))
            self._music_preview_refresh_timer = timer
        timer.start(0 if force else 120)

    def _resolve_preview_voice_only_audio_path(self) -> str:
        if self.using_existing_audio_source():
            return ""
        candidates = [
            self.processed_artifacts.get("voice_vi"),
            self.last_voice_vi_path,
        ]
        for candidate in candidates:
            normalized = self._normalize_local_file_path(candidate)
            if normalized and os.path.exists(normalized):
                return normalized
        # Older projects may have persisted the generated WAV only on the
        # TS1 DubSubtitle layers, without a top-level voice_vi artifact.  Use
        # that canonical layer reference as a recovery path after reopening.
        timeline = getattr(self, "timeline", None)
        model = getattr(timeline, "_timeline", None) if timeline is not None else None
        for track in getattr(model, "tracks", []) or [] if model is not None else []:
            if str(getattr(track, "name", "")) not in ("TS1", "A2 Dub"):
                continue
            for layer in getattr(track, "layers", []) or []:
                metadata = getattr(layer, "metadata", {}) or {}
                for candidate in (
                    getattr(layer, "audio_path", ""),
                    metadata.get("audio_path", ""),
                    metadata.get("_audio_path", ""),
                ):
                    normalized = self._normalize_local_file_path(candidate)
                    if normalized and os.path.exists(normalized):
                        return normalized
        # Legacy releases sometimes stored only mixed_vi.  It is still a
        # valid dubbed sidecar when there is no separate Music Layer.
        if not self._music_audio_tracks():
            for candidate in (
                self.processed_artifacts.get("mixed_vi"),
                self.last_mixed_vi_path,
            ):
                normalized = self._normalize_local_file_path(candidate)
                if normalized and os.path.exists(normalized):
                    return normalized
        return ""

    def _resolve_preview_background_audio_path(self) -> str:
        # A Music Layer on the timeline is the only source that should be mixed with TTS.
        # Never silently substitute the source video's extracted audio or separated stems:
        # A1 remains an independent track and can be muted separately.
        music_tracks = self._music_audio_tracks()
        if music_tracks:
            return str(music_tracks[0].get("path", "") or "")
        return ""

    def _resolve_preview_mixed_audio_path(self) -> str:
        if self.using_existing_audio_source():
            audio_path = self._normalize_local_file_path(self.mixed_audio_edit.text().strip())
            return audio_path if audio_path and os.path.exists(audio_path) else ""
        voice_only = self._resolve_preview_voice_only_audio_path()
        music_tracks = self._music_audio_tracks()
        # The dubbed sidecar may contain TTS, music, or both.  Do not require
        # a voice file here: a project with a muted/skipped TTS track and an
        # active Music Layer must still be previewable and exportable.
        if not voice_only and not music_tracks:
            return ""

        tts_state = self._tts_audio_track_state()
        voice_stat = None
        if voice_only:
            try:
                voice_stat = os.stat(voice_only)
            except OSError:
                voice_only = ""
        signature_payload = {
            "kind": "tts-plus-music-v1",
            "voice": os.path.abspath(voice_only) if voice_only else "",
            "voice_size": int(voice_stat.st_size) if voice_stat is not None else 0,
            "voice_mtime_ns": int(getattr(voice_stat, "st_mtime_ns", int(voice_stat.st_mtime * 1_000_000_000))) if voice_stat is not None else 0,
            "tts_volume": round(float(tts_state.get("volume", 100.0)), 3),
            "tts_muted": bool(tts_state.get("muted", False)),
            "music": [
                {
                    "path": os.path.abspath(str(item.get("path", ""))),
                    "size": int(os.path.getsize(str(item.get("path", "")))),
                    "mtime_ns": int(os.stat(str(item.get("path", ""))).st_mtime_ns),
                    "start": round(float(item.get("start", 0.0)), 3),
                    "end": round(float(item.get("end", 0.0)), 3),
                    "source_start": round(float(item.get("source_start", 0.0)), 3),
                    "volume": round(float(item.get("volume", 30.0)), 3),
                    "muted": bool(item.get("muted", False)),
                }
                for item in music_tracks
            ],
        }
        mix_hash = hashlib.sha1(json.dumps(signature_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        output_path = os.path.join(self.get_workspace_temp_root(create=True), f"timeline_preview_mix_{mix_hash}.wav")
        if os.path.exists(output_path):
            return output_path

        try:
            from audio_mixer import mix_audio_tracks
            tracks = []
            if voice_only:
                tracks.append({
                    "path": voice_only,
                    "start": 0.0,
                    "end": self._audio_total_duration_ms() / 1000.0,
                    "volume": float(tts_state.get("volume", 100.0)),
                    "muted": bool(tts_state.get("muted", False)),
                })
            tracks.extend(music_tracks)
            mix_audio_tracks(
                tracks=tracks,
                output_wav_path=output_path,
                total_duration_ms=self._audio_total_duration_ms(),
            )
        except Exception as exc:
            # A project may legitimately have no active dubbed/music track
            # yet (for example before TTS generation, or when both tracks
            # are muted).  Treat that as a silent sidecar rather than an
            # error emitted on every preview refresh; unexpected mixer
            # failures remain visible in the runtime log.
            if not (isinstance(exc, ValueError) and "No active audio tracks" in str(exc)):
                self.log(f"[Preview] timeline audio mix unavailable: {exc}")
            return ""
        return output_path

    def resolve_timeline_audio_visualization_path(self) -> str:
        preview_mode = str(getattr(self, "_preview_audio_track_mode", "original") or "original").strip().lower()
        if preview_mode == "original":
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

        dubbed_audio_kind, dubbed_audio = self._resolve_preview_dubbed_playback_source()
        if dubbed_audio_kind in ("mixed", "voice") and dubbed_audio and os.path.exists(dubbed_audio):
            return dubbed_audio

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

    def _resolve_preview_original_video_path(self) -> str:
        video_path = self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        normalized = self._normalize_local_file_path(video_path)
        return normalized if normalized and os.path.exists(normalized) else ""

    def _resolve_preview_dubbed_audio_path(self) -> str:
        mixed_audio = self._resolve_preview_mixed_audio_path()
        if mixed_audio:
            return mixed_audio
        return self._resolve_preview_voice_only_audio_path()

    def _has_preview_dubbed_audio_source(self) -> bool:
        if self.using_existing_audio_source():
            audio_path = self._normalize_local_file_path(self.mixed_audio_edit.text().strip())
            return bool(audio_path and os.path.exists(audio_path))
        return bool(self._resolve_preview_voice_only_audio_path() or self._music_audio_tracks())

    def _timeline_audio_track_mutes(self) -> tuple[bool, bool] | None:
        if not hasattr(self, "timeline") or not getattr(self.timeline, "_timeline", None):
            return None
        a1_muted = None
        a2_muted = None
        for track in self.timeline._timeline.tracks:
            metadata = getattr(track, "metadata", {}) or {}
            muted = bool(getattr(track, "muted", False) or metadata.get("_muted", False))
            if track.name == "A1 Audio":
                a1_muted = muted
            elif track.name in ("A2 Dub", "TS1"):
                a2_muted = muted
        if a1_muted is None and a2_muted is None:
            return None
        return bool(a1_muted), bool(a2_muted)

    def _resolve_preview_dubbed_playback_source(self) -> tuple[str, str]:
        """Resolve which audio file represents the dubbed track in preview.

        With a Music Layer, preview uses the cached TTS+music render so its
        track volumes match export. Without one, it falls back to pure TTS.

        Returns ("voice", path) | ("mixed", path) | ("original", "").
        """
        track_mutes = self._timeline_audio_track_mutes()
        voice_only = self._resolve_preview_voice_only_audio_path()
        mixed_audio = self._resolve_preview_mixed_audio_path()

        if not track_mutes:
            if mixed_audio:
                return "mixed", mixed_audio
            if voice_only:
                return "voice", voice_only
            return "original", ""

        a1_muted, a2_muted = track_mutes
        # A muted TS1 track does not imply the Music Layer is muted.  When a
        # composed sidecar exists, it is rebuilt without TTS and returned so
        # music remains audible independently.
        if mixed_audio:
            return "mixed", mixed_audio
        if a2_muted:
            return "original", ""
        # With no Music Layer the generated voice can be played directly.
        if voice_only:
            return "voice", voice_only
        return "original", ""

    def _preview_audio_track_choices(self) -> list[tuple[str, str]]:
        choices = [("Original Audio", "original")]
        if self._has_preview_dubbed_audio_source():
            choices.append(("Dub Voice", "dubbed"))
        return choices

    def _preferred_preview_audio_track_mode(self) -> str:
        track_mutes = self._timeline_audio_track_mutes()
        if track_mutes:
            _a1_muted, a2_muted = track_mutes
            # A2 mute applies only to TTS.  Music is composed into the
            # dubbed sidecar independently, so keep that sidecar selected
            # when a Music Layer exists.
            if a2_muted and not self._music_audio_tracks():
                return "original"
        mode = str(self.get_output_mode_key() or "subtitle").strip().lower()
        if mode in ("voice", "both"):
            if self._has_preview_dubbed_audio_source():
                return "dubbed"
        return "original"

    def sync_preview_audio_track_to_output(self, *, apply_to_player: bool = True, force: bool = False):
        target_mode = self._preferred_preview_audio_track_mode()
        self._preview_audio_track_mode = target_mode

        if not apply_to_player or not getattr(self, "media_player", None):
            return

        source_video = self._resolve_preview_original_video_path()
        current_source = self._normalize_local_file_path(str(getattr(self.media_player, "_source_path", "") or ""))
        should_apply = bool(force) or not current_source
        if source_video and current_source:
            should_apply = bool(force) or os.path.abspath(current_source) == os.path.abspath(source_video)

        if should_apply:
            self._apply_preview_audio_track_selection()
            return

    def _apply_preview_audio_track_selection(self):
        if (
            getattr(self, "_preview_audio_track_switching", False)
            or not hasattr(self, "media_player")
            or not getattr(self, "media_player", None)
        ):
            return
        source_video = self._resolve_preview_original_video_path()
        if not source_video:
            return

        # Always load the original audio sidecar. Resolve/build the composed
        # dubbed sidecar only when the current output mode actually selects
        # it; otherwise a subtitle-only/original preview would needlessly
        # attempt a TTS+Music mix and report "No active audio tracks".
        preferred_mode = self._preferred_preview_audio_track_mode()
        selected_mode = str(getattr(self, "_preview_audio_track_mode", "") or preferred_mode).strip().lower()
        if selected_mode == "dubbed":
            dubbed_audio_kind, dubbed_audio = self._resolve_preview_dubbed_playback_source()
        else:
            dubbed_audio_kind, dubbed_audio = "original", ""
        if not dubbed_audio or dubbed_audio_kind == "original":
            dubbed_audio = ""
        original_audio = self._resolve_preview_original_audio_path()

        try:
            current_position = int(self.media_player.position())
        except Exception:
            current_position = 0
        try:
            was_playing = bool(self.media_player.is_playing())
        except Exception:
            was_playing = False

        current_source = str(getattr(self.media_player, "_source_path", "") or "")
        should_reset_source = not current_source or os.path.abspath(current_source) != os.path.abspath(source_video)

        self._preview_audio_track_switching = True
        try:
            if should_reset_source:
                try:
                    self.media_player.pause()
                except Exception:
                    pass
                self.media_player.setSource(QUrl.fromLocalFile(source_video))
                self.refresh_video_dimensions(source_video)
                self._preview_video_has_burned_subtitles = False
                self.sync_live_subtitle_preview()
            # Always load the original audio sidecar when available
            if hasattr(self.media_player, "set_original_audio_file"):
                if original_audio:
                    self.media_player.set_original_audio_file(original_audio)
                else:
                    try:
                        self.media_player._clear_original_audio()
                    except Exception:
                        pass
            # Re-apply persisted A1 level/mute after replacing the sidecar.
            # QMediaPlayer resets the output volume when a new audio source
            # is opened, which otherwise makes a restored 0%/custom level
            # appear to be ignored until the slider is touched.
            try:
                original_volume = self._compute_audio_track_volume("A1 Audio", base=100.0)
                original_gain = self._get_audio_track_gain_db("A1 Audio")
                original_volume *= 10 ** (original_gain / 20.0)
                if hasattr(self.media_player, "set_original_volume"):
                    self.media_player.set_original_volume(max(0.0, min(200.0, original_volume)))
                if hasattr(self.media_player, "set_mute_original"):
                    self.media_player.set_mute_original(self._is_audio_track_muted("A1 Audio"))
            except Exception:
                pass
            if dubbed_audio:
                self.media_player.set_audio_file(dubbed_audio)
                try:
                    if dubbed_audio_kind == "mixed":
                        # TTS and Music volumes are already baked into the
                        # composed WAV; never apply TS1's level to the whole
                        # sidecar a second time.
                        if hasattr(self.media_player, "set_dubbed_volume"):
                            self.media_player.set_dubbed_volume(100.0)
                        if hasattr(self.media_player, "set_mute_dubbed"):
                            self.media_player.set_mute_dubbed(False)
                    else:
                        dubbed_volume = self._compute_audio_track_volume("TS1", base=100.0)
                        dubbed_gain = self._get_audio_track_gain_db("TS1")
                        dubbed_volume *= 10 ** (dubbed_gain / 20.0)
                        if hasattr(self.media_player, "set_dubbed_volume"):
                            self.media_player.set_dubbed_volume(max(0.0, min(200.0, dubbed_volume)))
                        if hasattr(self.media_player, "set_mute_dubbed"):
                            self.media_player.set_mute_dubbed(self._is_audio_track_muted("TS1"))
                except Exception:
                    pass
            else:
                self.media_player.clear_audio()
            if current_position > 0:
                try:
                    self.media_player.setPosition(current_position)
                except Exception:
                    pass
            if was_playing:
                try:
                    self.media_player.play()
                    if hasattr(self, "timeline"):
                        self.timeline.set_playing(True)
                except Exception:
                    pass
            else:
                if hasattr(self, "timeline"):
                    self.timeline.set_playing(False)
            # Only log the preview audio state when at least one audio sidecar
            # was actually applied. Logging "silent" on a freshly opened
            # video (no generate/voice done yet) is misleading noise —
            # Bug 3.
            if original_audio or dubbed_audio:
                active_label = "both" if (original_audio and dubbed_audio) else (
                    "dubbed" if dubbed_audio else "original"
                )
                self.log(f"[Preview] audio: {active_label}")
        finally:
            self._preview_audio_track_switching = False
        self.schedule_timeline_visual_refresh(waveform=True, thumbnails=True)

    def _resolve_preview_original_audio_path(self) -> str:
        """Resolve the original audio file path (separate from source video).

        A1 always represents the source video's original audio.  Audio
        Processing/Clean mode no longer changes this track; separated stems
        are only used when the user explicitly adds a Music Layer.
        """
        candidates: list[str] = []
        candidates.extend([
            self.processed_artifacts.get("extracted_audio"),
            self.last_extracted_audio,
            self.audio_source_edit.text().strip() if hasattr(self, "audio_source_edit") else "",
        ])
        for candidate in candidates:
            if not candidate:
                continue
            normalized = self._normalize_local_file_path(candidate)
            if normalized and os.path.exists(normalized):
                return normalized
        # Final fallback: the source video file itself. mpv runs with
        # `ao=null` (video-only) and audio is routed through the A1
        # QMediaPlayer sidecar, so on a freshly opened video (no Generate
        # run yet, no extracted audio artifact) the sidecar would be empty
        # and the user hears nothing. QMediaPlayer decodes the audio
        # track straight out of a video container, so loading the source
        # video into the A1 sidecar restores the original audio. Once the
        # pipeline extracts a dedicated audio file, that takes priority
        # via the candidates above.
        source_video = self._resolve_preview_original_video_path()
        if source_video:
            return source_video
        return ""

    def on_preview_audio_track_changed(self, index: int):
        if getattr(self, "_preview_audio_track_switching", False) or not hasattr(self, "preview_audio_track_combo"):
            return
        mode = str(self.preview_audio_track_combo.itemData(index) or "original").strip().lower()
        self._preview_audio_track_mode = mode if mode in ("original", "dubbed") else "original"
        self._apply_preview_audio_track_selection()

    def _waveform_temp_path(self) -> str:
        video_path = self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        if not video_path:
            return ""
        video_hash = hashlib.md5(video_path.encode("utf-8")).hexdigest()[:12]
        return os.path.join(self.get_workspace_temp_root(create=True), f"waveform_{video_hash}.wav")

    def _timeline_waveform_request_signature(self):
        # A1 represents the source video's original audio. It must remain
        # stable as Transcript/Translate/TTS change project artifacts.
        video_path = self._normalize_local_file_path(self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else "")
        if video_path and os.path.exists(video_path):
            try:
                stat = os.stat(video_path)
                return (
                    "v4-source-video-envelope",
                    os.path.abspath(video_path),
                    int(stat.st_size),
                    int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
                )
            except Exception:
                return ("v4-source-video-envelope", os.path.abspath(video_path), 0, 0)
        return None

    def _timeline_thumbnail_request_signature(self):
        video_path = self._normalize_local_file_path(self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else "")
        duration_s = max(0.0, float(getattr(self.timeline, "duration", 0) or 0) / 1000.0) if hasattr(self, "timeline") else 0.0
        if not video_path or not os.path.exists(video_path) or duration_s <= 0.0:
            return None
        try:
            stat = os.stat(video_path)
            return (
                "v5-timeline-thumbnails",
                os.path.abspath(video_path),
                int(stat.st_size),
                int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
                int(round(duration_s)),
            )
        except Exception:
            return ("v5-timeline-thumbnails", os.path.abspath(video_path), 0, 0, int(round(duration_s)))

    def _load_launcher_timeline_visual_cache(self):
        """Return static V1/A1 data prepared by the launcher, if valid."""
        video_path = self._normalize_local_file_path(
            self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        )
        if not video_path or not os.path.exists(video_path):
            return None
        try:
            stat = os.stat(video_path)
            source_abs = os.path.abspath(video_path)
            cache_dir = os.path.join(self.get_workspace_temp_root(create=True), "timeline_visuals")
            digest = hashlib.md5(source_abs.encode("utf-8")).hexdigest()[:12]
            manifest_path = os.path.join(cache_dir, f"{digest}.json")
            candidates = [manifest_path]
            # A packaged build can be launched with a copied video (for
            # example, the same file moved from media/ to Downloads).  The
            # launcher cache is still reusable in that case, but its
            # path-based digest no longer matches.  Fall back to a manifest
            # with the same filename and byte size, while retaining the
            # normal exact path/mtime validation as the first choice.
            if os.path.isdir(cache_dir):
                candidates.extend(
                    path for path in glob.glob(os.path.join(cache_dir, "*.json"))
                    if path != manifest_path
                )
            for candidate in candidates:
                try:
                    with open(candidate, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                except (OSError, ValueError, TypeError):
                    continue
                exact = (
                    data.get("source") == source_abs
                    and int(data.get("size", -1)) == int(stat.st_size)
                    and int(data.get("mtime_ns", -1)) == int(getattr(stat, "st_mtime_ns", 0))
                )
                compatible_copy = (
                    os.path.basename(str(data.get("source", ""))).lower()
                    == os.path.basename(source_abs).lower()
                    and int(data.get("size", -1)) == int(stat.st_size)
                )
                if exact or compatible_copy:
                    return data
            return None
        except (OSError, ValueError, TypeError):
            return None

    def refresh_timeline_waveform(self):
        if not hasattr(self, "timeline"):
            return
        request_signature = self._timeline_waveform_request_signature()
        if not request_signature:
            self._desired_timeline_waveform_request = None
            self._timeline_waveform_cache_key = None
            self._timeline_waveform_samples = []
            self._timeline_waveform_duration_s = 0.0
            self.timeline.set_waveform_data([], 0.0)
            return
        launcher_cache = self._load_launcher_timeline_visual_cache()
        if launcher_cache and launcher_cache.get("waveform"):
            self._desired_timeline_waveform_request = request_signature
            self._timeline_waveform_cache_key = request_signature
            self._timeline_waveform_samples = list(launcher_cache.get("waveform") or [])
            self._timeline_waveform_duration_s = max(0.0, float(launcher_cache.get("duration_s") or 0.0))
            self.timeline.set_waveform_data(
                self._timeline_waveform_samples, self._timeline_waveform_duration_s
            )
            return
        self._desired_timeline_waveform_request = request_signature
        if self._timeline_waveform_cache_key == request_signature:
            self.timeline.set_waveform_data(
                self._timeline_waveform_samples, self._timeline_waveform_duration_s
            )
            return
        worker = self._timeline_waveform_worker
        if worker is not None and worker.isRunning():
            return
        video_path = self._normalize_local_file_path(
            self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        )
        worker = TimelineWaveformWorker(
            request_signature, video_path, "", self._waveform_temp_path()
        )
        worker.finished.connect(self._on_timeline_waveform_ready)
        self._timeline_waveform_worker = worker
        worker.start()

    def _on_timeline_waveform_ready(self, request_signature, waveform, duration_s, error):
        self._timeline_waveform_worker = None
        if request_signature != self._desired_timeline_waveform_request:
            self.refresh_timeline_waveform()
            return
        if error:
            print(f"[Timeline] waveform generation failed: {error}")
            self._timeline_waveform_cache_key = request_signature
            self._timeline_waveform_samples = []
            self._timeline_waveform_duration_s = 0.0
        else:
            self._timeline_waveform_cache_key = request_signature
            self._timeline_waveform_samples = list(waveform or [])
            self._timeline_waveform_duration_s = max(0.0, float(duration_s or 0.0))
            print(
                f"[Timeline] waveform generated: samples={len(self._timeline_waveform_samples)} "
                f"duration={self._timeline_waveform_duration_s:.1f}s"
            )
        if hasattr(self, "timeline"):
            self.timeline.set_waveform_data(self._timeline_waveform_samples, self._timeline_waveform_duration_s)

    def schedule_timeline_visual_refresh(self, *, waveform: bool = True, thumbnails: bool = True, delay_ms: int = 40):
        # V1/A1 visuals are tied only to the source media, not to a pipeline
        # stage. They are static cached assets and may be prepared as soon as
        # a video is opened.
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
        request_signature = self._timeline_thumbnail_request_signature()
        if not request_signature:
            self._timeline_video_thumb_cache_key = None
            self._timeline_video_thumbnails = []
            self._desired_timeline_thumbnail_request = None
            self.timeline.set_video_thumbnails([])
            return
        launcher_cache = self._load_launcher_timeline_visual_cache()
        if launcher_cache and launcher_cache.get("thumbnails"):
            pixmaps = []
            for timestamp_s, output_path in launcher_cache.get("thumbnails"):
                pixmap = QPixmap(str(output_path or ""))
                if not pixmap.isNull():
                    pixmaps.append((float(timestamp_s), pixmap))
            if pixmaps:
                self._desired_timeline_thumbnail_request = request_signature
                self._timeline_video_thumb_cache_key = request_signature
                self._timeline_video_thumbnails = pixmaps
                self.timeline.set_video_thumbnails(pixmaps)
                return
        self._desired_timeline_thumbnail_request = request_signature
        if self._timeline_video_thumb_cache_key == request_signature:
            self.timeline.set_video_thumbnails(self._timeline_video_thumbnails)
            return
        worker = self._timeline_thumbnail_worker
        if worker is not None and worker.isRunning():
            return
        video_path = self._normalize_local_file_path(self.video_path_edit.text().strip())
        duration_s = max(0.0, float(self.timeline.duration or 0) / 1000.0)
        thumb_dir = os.path.join(self.get_workspace_temp_root(create=True), "timeline_thumbnails")
        worker = TimelineThumbnailWorker(request_signature, video_path, duration_s, thumb_dir)
        worker.finished.connect(self._on_timeline_video_thumbnails_ready)
        self._timeline_thumbnail_worker = worker
        worker.start()

    def _on_timeline_video_thumbnails_ready(self, request_signature, thumbnails, error):
        self._timeline_thumbnail_worker = None
        if request_signature != self._desired_timeline_thumbnail_request:
            self.refresh_timeline_video_thumbnails()
            return
        if error:
            print(f"[Timeline] thumbnail generation failed: {error}")
            self._timeline_video_thumb_cache_key = request_signature
            self._timeline_video_thumbnails = []
        else:
            pixmaps = []
            for timestamp_s, output_path in list(thumbnails or []):
                pixmap = QPixmap(str(output_path or ""))
                if not pixmap.isNull():
                    pixmaps.append((float(timestamp_s), pixmap))
            self._timeline_video_thumb_cache_key = request_signature
            self._timeline_video_thumbnails = pixmaps
        if hasattr(self, "timeline"):
            self.timeline.set_video_thumbnails(self._timeline_video_thumbnails)

    def _hide_legacy_audio_source_controls(self):
        """Keep pre-timeline Audio Source widgets out of the active UI.

        Older projects still load a handful of compatibility widgets because
        runtime code and settings migration reference their values.  They are
        deliberately not part of the current Audio tab, which is based on
        independent A1/TS1/Music tracks.  Centralising the hide operation also
        prevents a legacy refresh path from making the old radio buttons
        visible again after output-mode or project-state updates.
        """
        legacy_widget_names = (
            "use_generated_audio_radio",
            "use_existing_audio_radio",
            "audio_source_hint_label",
            "bg_music_label",
            "bg_music_edit",
            "browse_bg_music_btn",
            "mixed_audio_label",
            "mixed_audio_edit",
            "browse_mixed_audio_btn",
            "generated_audio_section_label",
            "generated_audio_section_hint",
            "existing_audio_section_label",
            "existing_audio_section_hint",
        )
        for name in legacy_widget_names:
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                widget.hide()
                widget.setVisible(False)
            except RuntimeError:
                # A Qt object may have been deleted during a UI rebuild.
                continue
        for name in ("generated_audio_source_panel", "existing_audio_source_panel"):
            panel = getattr(self, name, None)
            if panel is None:
                continue
            try:
                panel.hide()
                panel.setVisible(False)
            except RuntimeError:
                continue

    def on_audio_source_mode_changed(self):
        # The old source selector is compatibility-only.  Always force it
        # hidden before handling any legacy state refresh.
        self._hide_legacy_audio_source_controls()
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
        if self._is_realtime_color_filter_state():
            self._pending_video_filter_preview = False
            self._apply_realtime_color_filter_preview()
            return
        if not hasattr(self, "video_filter_preview_timer"):
            return
        self._pending_video_filter_preview = True
        if getattr(self, "_styled_preview_running", False):
            return
        self.video_filter_preview_timer.start()

    def _is_realtime_color_filter_state(self) -> bool:
        """Return whether the current state is safe for MPV live preview."""
        try:
            state = self.get_video_filter_state()
            # LUT preview is realtime only when the active MPV backend has
            # native gpu-next/libplacebo LUT support. The stable gpu backend
            # continues using the debounced FFmpeg preview path.
            if (
                state.get("lut_path")
                and float(state.get("lut_strength", 0) or 0) > 0.001
                and not bool(getattr(self.media_player, "supports_native_lut", False))
            ):
                return False
            return bool(getattr(self.media_player, "backend_name", "") == "libmpv") and hasattr(
                self.media_player, "set_color_filter_state"
            )
        except Exception:
            return False

    def _apply_realtime_color_filter_preview(self) -> bool:
        if not self._is_realtime_color_filter_state():
            return False
        try:
            source_path = self.video_path_edit.text().strip()
            if not source_path or not os.path.exists(source_path):
                return False
            current_source = str(getattr(self.media_player, "_source_path", "") or "")
            was_playing = bool(self.media_player.is_playing())
            position = int(self.media_player.position() or 0)
            if os.path.abspath(current_source) != os.path.abspath(source_path):
                self.media_player.setSource(QUrl.fromLocalFile(source_path))
                if position > 0:
                    self.media_player.setPosition(position)
                if was_playing:
                    self.media_player.play()
            self.media_player.set_color_filter_state(self.get_video_filter_state())
            self._video_filter_preview_dirty = False
            self._video_filter_apply_requested = False
            self._play_video_filter_preview_when_ready = False
            return True
        except Exception as exc:
            self.log(f"[Filter] MPV realtime preview failed: {exc}")
            return False

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
        if self._is_realtime_color_filter_state():
            self._video_filter_preview_dirty = False
            self._video_filter_apply_requested = False
            self._apply_realtime_color_filter_preview()
            return
        self._video_filter_preview_dirty = self.has_active_video_filters()
        self._video_filter_apply_requested = False
        self.refresh_ui_state()

    def apply_current_video_filter(self):
        self.log(f"[Filter] apply_current_video_filter called, has_active={self.has_active_video_filters()}")
        if self._is_realtime_color_filter_state():
            self.log("[Filter] Applying Brightness/Contrast/Saturation through MPV realtime preview")
            self._apply_realtime_color_filter_preview()
            self.refresh_ui_state()
            return
        if not self.has_active_video_filters():
            self.log("[Filter] No active filters, returning early")
            self._video_filter_preview_dirty = False
            self._video_filter_apply_requested = False
            self.hide_filter_thumbnail_preview()
            self.refresh_ui_state()
            return
        self._video_filter_apply_requested = True
        self.refresh_ui_state()
        self.log("[Filter] Calling preview_controller.preview_video()")
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
        if self._is_realtime_color_filter_state():
            self._pending_video_filter_preview = False
            return
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
        if hasattr(self, "subtitle_bottom_offset_label"):
            self.subtitle_bottom_offset_label.setVisible(not is_custom)
        if hasattr(self, "subtitle_bottom_offset_spin"):
            self.subtitle_bottom_offset_spin.setVisible(not is_custom)
        self.update_subtitle_preview_style()
        if getattr(self, "current_project_state", None) is not None:
            self.schedule_timeline_project_persist()

    def _set_subtitle_item_text_rendering(self, enabled: bool) -> None:
        video_view = getattr(self, "video_view", None)
        subtitle_item = getattr(video_view, "subtitle_item", None)
        setter = getattr(subtitle_item, "set_text_rendering", None)
        if callable(setter):
            try:
                setter(bool(enabled))
            except Exception:
                pass

    def on_subtitle_drag_started(self):
        """Swap to the Qt layer only while dragging for immediate feedback."""
        if self._preview_is_playing():
            return
        if getattr(self, "_preview_video_has_burned_subtitles", False):
            return
        if hasattr(self, "media_player"):
            self.media_player.clear_subtitle()
        self._set_subtitle_item_text_rendering(True)

    def on_subtitle_position_dragged(self, x_percent: int, y_percent: int):
        """Commit a drag from the live subtitle overlay to style controls."""
        if self._preview_is_playing():
            return
        x_percent = max(0, min(100, int(x_percent)))
        y_percent = max(0, min(100, int(y_percent)))
        if hasattr(self, "subtitle_position_mode_combo"):
            self.subtitle_position_mode_combo.blockSignals(True)
            index = self.subtitle_position_mode_combo.findData("custom")
            if index >= 0:
                self.subtitle_position_mode_combo.setCurrentIndex(index)
            self.subtitle_position_mode_combo.blockSignals(False)
        for widget, value in (
            (getattr(self, "subtitle_custom_x_spin", None), x_percent),
            (getattr(self, "subtitle_custom_y_spin", None), y_percent),
        ):
            if widget is not None:
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)
        self.on_subtitle_position_mode_changed()

    def get_subtitle_position_config(self) -> dict:
        alignment_map = {
            "Bottom Left": 1,
            "Bottom Center": 2,
            "Bottom": 2,
            "Bottom Right": 3,
            "Center": 5,
            "Top Center": 8,
            "Top": 8,
        }
        return {
            "position_mode": "custom" if self.is_custom_subtitle_position_mode() else "anchor",
            "alignment_label": self.subtitle_align_combo.currentText().strip(),
            "alignment": alignment_map.get(self.subtitle_align_combo.currentText(), 2),
            "margin_v": int(self.subtitle_bottom_offset_spin.value()),
            "x_offset": int(self.subtitle_x_offset_spin.value()),
            "custom_position_enabled": self.is_custom_subtitle_position_mode(),
            "custom_position_x": int(self.subtitle_custom_x_spin.value()),
            "custom_position_y": int(self.subtitle_custom_y_spin.value()),
        }

    def _saved_subtitle_style_payload(self) -> dict:
        return {
            "preset": self.get_selected_subtitle_preset(),
            "font": self.subtitle_font_combo.currentText().strip(),
            "size": int(self.subtitle_font_size_spin.value()),
            "color": self.subtitle_color_hex,
            "background_color": getattr(self, "subtitle_background_color_hex", "#000000"),
            "animation": self.subtitle_animation_combo.currentText().strip(),
            "animation_time": float(self.subtitle_animation_time_spin.value()),
            "karaoke_timing_mode": str(self.subtitle_karaoke_timing_combo.currentData() or "vietnamese"),
            "background": bool(self.subtitle_background_cb.isChecked()),
            "background_width": str(self.subtitle_background_width_combo.currentData() if hasattr(self, "subtitle_background_width_combo") else "fit_text"),
            "background_shape": str(self.subtitle_background_shape_combo.currentData() if hasattr(self, "subtitle_background_shape_combo") else "rectangle"),
            "outline": bool(getattr(self, "subtitle_outline_cb", None) and self.subtitle_outline_cb.isChecked()),
            "background_alpha": float(self.subtitle_bg_alpha_spin.value()) if hasattr(self, "subtitle_bg_alpha_spin") else 0.6,
            "background_padding": int(self.subtitle_background_padding_spin.value()) if hasattr(self, "subtitle_background_padding_spin") else 6,
            "background_radius": int(self.subtitle_background_radius_spin.value()) if hasattr(self, "subtitle_background_radius_spin") else 0,
            "bold": bool(self.subtitle_bold_cb.isChecked()),
            "speaker_colors": self._uses_speaker_subtitle_colors(),
            "auto_keyword_highlight": bool(self.subtitle_keyword_highlight_cb.isChecked()),
            "highlight_color": self.subtitle_highlight_color_combo.currentText().strip(),
            "highlight_mode": self.subtitle_highlight_mode_combo.currentText().strip(),
        }

    def _current_subtitle_style_controls_state(self) -> dict:
        return {
            "preset": self.get_selected_subtitle_preset(),
            "font": self.subtitle_font_combo.currentText().strip(),
            "size": int(self.subtitle_font_size_spin.value()),
            "color": self.subtitle_color_hex,
            "background_color": getattr(self, "subtitle_background_color_hex", "#000000"),
            "animation": self.subtitle_animation_combo.currentText().strip(),
            "animation_time": float(self.subtitle_animation_time_spin.value()),
            "karaoke_timing_mode": str(self.subtitle_karaoke_timing_combo.currentData() or "vietnamese"),
            "background": bool(self.subtitle_background_cb.isChecked()),
            "background_width": str(self.subtitle_background_width_combo.currentData() if hasattr(self, "subtitle_background_width_combo") else "fit_text"),
            "background_shape": str(self.subtitle_background_shape_combo.currentData() if hasattr(self, "subtitle_background_shape_combo") else "rectangle"),
            "outline": bool(getattr(self, "subtitle_outline_cb", None) and self.subtitle_outline_cb.isChecked()),
            "background_alpha": float(self.subtitle_bg_alpha_spin.value()) if hasattr(self, "subtitle_bg_alpha_spin") else 0.6,
            "background_padding": int(self.subtitle_background_padding_spin.value()) if hasattr(self, "subtitle_background_padding_spin") else 6,
            "background_radius": int(self.subtitle_background_radius_spin.value()) if hasattr(self, "subtitle_background_radius_spin") else 0,
            "bold": bool(self.subtitle_bold_cb.isChecked()),
            "speaker_colors": self._uses_speaker_subtitle_colors(),
            "auto_keyword_highlight": bool(self.subtitle_keyword_highlight_cb.isChecked()),
            "highlight_color": self.subtitle_highlight_color_combo.currentText().strip(),
            "highlight_mode": self.subtitle_highlight_mode_combo.currentText().strip(),
            "single_line": bool(getattr(self, "subtitle_single_line_cb", None) and self.subtitle_single_line_cb.isChecked()),
            "position": self.get_subtitle_position_config(),
        }

    def _apply_subtitle_style_controls_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        self.subtitle_font_combo.setCurrentText(str(state.get("font", self.subtitle_font_combo.currentText())))
        self.subtitle_font_size_spin.setValue(int(state.get("size", self.subtitle_font_size_spin.value())))
        self.subtitle_color_hex = str(state.get("color", self.subtitle_color_hex)).upper()
        self.subtitle_color_btn.setText(self.subtitle_color_hex)
        self.subtitle_background_color_hex = str(
            state.get("background_color", getattr(self, "subtitle_background_color_hex", "#000000"))
        ).upper()
        if hasattr(self, "subtitle_background_color_btn"):
            self.subtitle_background_color_btn.setText(self.subtitle_background_color_hex)
        self.subtitle_animation_combo.setCurrentText(str(state.get("animation", self.subtitle_animation_combo.currentText())))
        self.subtitle_animation_time_spin.setValue(float(state.get("animation_time", self.subtitle_animation_time_spin.value())))
        karaoke_mode = str(state.get("karaoke_timing_mode", self.subtitle_karaoke_timing_combo.currentData() or "vietnamese"))
        karaoke_index = self.subtitle_karaoke_timing_combo.findData(karaoke_mode)
        if karaoke_index >= 0:
            self.subtitle_karaoke_timing_combo.setCurrentIndex(karaoke_index)
        self.subtitle_background_cb.setChecked(bool(state.get("background", self.subtitle_background_cb.isChecked())))
        if hasattr(self, "subtitle_background_width_combo"):
            index = self.subtitle_background_width_combo.findData(str(state.get("background_width", "fit_text")))
            self.subtitle_background_width_combo.setCurrentIndex(max(0, index))
            shape_index = self.subtitle_background_shape_combo.findData(str(state.get("background_shape", "rectangle")))
            self.subtitle_background_shape_combo.setCurrentIndex(max(0, shape_index))
            self.on_subtitle_background_width_changed()
            if hasattr(self, "subtitle_background_radius_spin"):
                self.subtitle_background_radius_spin.setValue(int(state.get("background_radius", 0)))
        if hasattr(self, "subtitle_outline_cb"):
            self.subtitle_outline_cb.setChecked(bool(state.get("outline", self.subtitle_outline_cb.isChecked())))
        if hasattr(self, "subtitle_bg_alpha_spin"):
            self.subtitle_bg_alpha_spin.setValue(float(state.get("background_alpha", self.subtitle_bg_alpha_spin.value())))
        if hasattr(self, "subtitle_background_padding_spin"):
            self.subtitle_background_padding_spin.setValue(
                int(state.get("background_padding", self.subtitle_background_padding_spin.value()))
            )
        self.subtitle_bold_cb.setChecked(bool(state.get("bold", self.subtitle_bold_cb.isChecked())))
        if hasattr(self, "subtitle_speaker_colors_cb"):
            self.subtitle_speaker_colors_cb.setChecked(
                bool(state.get("speaker_colors", self.subtitle_speaker_colors_cb.isChecked()))
            )
        self.subtitle_keyword_highlight_cb.setChecked(
            bool(state.get("auto_keyword_highlight", self.subtitle_keyword_highlight_cb.isChecked()))
        )
        self.subtitle_highlight_color_combo.setCurrentText(
            str(state.get("highlight_color", self.subtitle_highlight_color_combo.currentText()))
        )
        self.subtitle_highlight_mode_combo.setCurrentText(
            str(state.get("highlight_mode", self.subtitle_highlight_mode_combo.currentText()))
        )
        if hasattr(self, "subtitle_single_line_cb"):
            self.subtitle_single_line_cb.setChecked(bool(state.get("single_line", self.subtitle_single_line_cb.isChecked())))
        position = dict(state.get("position") or {})
        if position:
            mode_combo = getattr(self, "subtitle_position_mode_combo", None)
            if mode_combo is not None:
                index = mode_combo.findData(str(position.get("position_mode", "anchor")))
                if index >= 0:
                    mode_combo.setCurrentIndex(index)
            align_combo = getattr(self, "subtitle_align_combo", None)
            if align_combo is not None:
                align_combo.setCurrentText(str(position.get("alignment_label", align_combo.currentText())))
            for widget_name, value_key in (
                ("subtitle_bottom_offset_spin", "margin_v"),
                ("subtitle_x_offset_spin", "x_offset"),
                ("subtitle_custom_x_spin", "custom_position_x"),
                ("subtitle_custom_y_spin", "custom_position_y"),
            ):
                widget = getattr(self, widget_name, None)
                if widget is not None and value_key in position:
                    widget.setValue(int(position[value_key]))
            self.on_subtitle_position_mode_changed()

    def _capture_subtitle_custom_style_state(self) -> None:
        self._subtitle_custom_style_state = self._current_subtitle_style_controls_state()

    def on_subtitle_style_control_edited(self, *_args):
        if getattr(self, "_subtitle_preset_apply_in_progress", False):
            return
        self._capture_subtitle_custom_style_state()
        custom_radio = getattr(self, "subtitle_preset_custom_radio", None)
        if custom_radio is not None and not custom_radio.isChecked():
            custom_radio.blockSignals(True)
            custom_radio.setChecked(True)
            custom_radio.blockSignals(False)
            self.on_subtitle_preset_changed()
        if getattr(self, "current_project_state", None) is not None:
            self.schedule_timeline_project_persist()

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
        elif key == "custom" and getattr(self, "subtitle_preset_custom_radio", None):
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
        self._capture_subtitle_custom_style_state()
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
            optimize_subtitles=False,
            style_instruction=self.get_ai_style_instruction(),
        )

    def build_current_voice_signature(self, segments=None, background_path=""):
        voice_segments = list(segments or [])
        if not voice_segments:
            voice_segments = self._get_voiceover_segments()
        if not voice_segments:
            return ""
        normalizer_signature = ""
        try:
            from tts_processor import normalizer_dictionary_fingerprint
            settings = getattr(self.current_project_state, "settings", {}) or {}
            normalizer_signature = normalizer_dictionary_fingerprint(
                settings.get("normalizer_dictionary", {})
            )
        except Exception:
            # Signature generation must not prevent a project from opening;
            # the TTS cache still remains usable when the optional normalizer
            # package is unavailable.
            normalizer_signature = ""
        return self.project_service.build_voice_signature(
            voice_segments,
            audio_handling_mode=self.get_audio_handling_mode(),
            voice_name=self.get_active_voice_name(),
            voice_speed=self._parse_voice_speed_value(),
            timing_sync_mode=str(self.voice_timing_sync_combo.currentText()).strip(),
            background_path=background_path,
            original_volume=int(self.audio_a1_volume_slider.value()) if hasattr(self, "audio_a1_volume_slider") else 50,
            dub_volume=int(self.audio_a2_volume_slider.value()) if hasattr(self, "audio_a2_volume_slider") else 100,
            normalizer_signature=normalizer_signature,
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

        # These states belong to the project editor view.  The track-label
        # widget itself is rebuilt on startup, so it cannot be their source
        # of truth.
        state.set_setting("preview_track_visibility", {
            "TS1": bool(getattr(self, "_subtitle_track_preview_visible", True)),
            "T1 Text": bool(getattr(self, "_text_track_preview_visible", True)),
            "L1 Logo": bool(getattr(self, "_logo_track_preview_visible", True)),
            "M1": bool(getattr(self, "_mask_track_preview_visible", True)),
            "B1": bool(self._blur_effect_enabled()),
        })
        state.set_setting("subtitle_style_controls", self._current_subtitle_style_controls_state())
        
        # Save timeline data (includes mask and logo layers)
        if hasattr(self, "timeline") and self.timeline._timeline:
            import json
            timeline_data = self.timeline._timeline.to_dict()
            # Save timeline to a file in the project directory
            timeline_path = os.path.join(state.project_root, "timeline", "timeline.json")
            os.makedirs(os.path.dirname(timeline_path), exist_ok=True)
            with open(timeline_path, "w", encoding="utf-8") as f:
                json.dump(timeline_data, f, ensure_ascii=False, indent=2)
            state.set_artifact("timeline", timeline_path)
            # Selection Range is an ephemeral editing aid. Never restore it
            # when reopening a project, where an old range can be confusing.
            state.settings.pop("timeline_selection_range", None)
        
        self.project_service.save_project(state)

    def schedule_timeline_project_persist(self, *, mask_state=False, blur_state=False):
        """Coalesce persistence requested by high-frequency editor events.

        Preview geometry is updated by the callers immediately.  Only the
        disk-backed project/timeline write is delayed, which prevents drag
        operations and text typing from blocking the Qt event loop.
        """
        self._pending_timeline_persist = True
        self._pending_mask_state_persist = self._pending_mask_state_persist or bool(mask_state)
        self._pending_blur_state_persist = self._pending_blur_state_persist or bool(blur_state)
        timer = getattr(self, "_timeline_persist_timer", None)
        if timer is not None:
            timer.start()
        else:
            self._flush_pending_timeline_persist()

    def _flush_pending_timeline_persist(self):
        """Write coalesced editor changes once after an edit burst ends."""
        if not getattr(self, "_pending_timeline_persist", False):
            return
        save_mask = self._pending_mask_state_persist
        save_blur = self._pending_blur_state_persist
        self._pending_timeline_persist = False
        self._pending_mask_state_persist = False
        self._pending_blur_state_persist = False
        try:
            if save_mask:
                self.persist_project_mask_state()
            if save_blur:
                self.persist_project_blur_state()
            self.persist_current_timeline_project_data()
        except Exception:
            # Preserve existing best-effort persistence behavior: a save
            # failure must not interrupt editing or leave the timer running.
            pass

    def _cache_core_timeline_tracks_only(self):
        """Keep only V1, A1, and TS1 when a video session is closed.

        Optional editing tracks remain fully usable (and exportable) during
        the active session. They are deliberately not retained in the
        reopen cache, preventing Blur/Logo/Mask/Text tracks from following
        a video into its next editing session.
        """
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        # Music is a first-class project audio track, so retain it alongside
        # the source and generated-voice tracks when the editor session is
        # cached/reopened.
        core_track_names = {"V1 Video", "A1 Audio", "TS1", "A2 Music"}
        timeline = self.timeline._timeline
        removed = [track for track in timeline.tracks if track.name not in core_track_names]
        if removed:
            timeline.tracks = [track for track in timeline.tracks if track.name in core_track_names]
            for track in removed:
                self.timeline._track_heights.pop(track.id, None)
            selected_id = str(getattr(self.timeline, "_selected_layer_id", "") or "")
            retained_ids = {layer.id for track in timeline.tracks for layer in track.layers}
            if selected_id not in retained_ids:
                self.timeline._selected_layer_id = ""
            self.timeline._redraw()
            self.persist_current_timeline_project_data()
        state = getattr(self, "current_project_state", None)
        if state is not None:
            # Blur and mask also have legacy settings fallbacks. Clear those
            # cache entries so they cannot recreate optional tracks on load.
            state.set_setting("blur_state", {"enabled": False, "regions": []})
            state.set_setting("mask_state", {"enabled": False, "regions": []})
            self.project_service.save_project(state)
        if removed:
            self.log(f"[Timeline Cache] Retained core tracks only; discarded {len(removed)} optional track(s).")

    def _restore_saved_timeline_model(self, state) -> bool:
        """Restore the complete editor timeline, including optional layers.

        The project bridge restores transcript artifacts, but those artifacts
        only describe the core subtitle data.  Text/Logo/Blur/Mask tracks are
        persisted separately in ``timeline/timeline.json`` and must be loaded
        before the subtitle sync rebuilds TS1; otherwise reopening a project
        silently drops the optional tracks from the in-memory model.
        """
        timeline = getattr(self, "timeline", None)
        if timeline is None:
            return False
        artifacts = getattr(state, "artifacts", {}) or {}
        timeline_path = str(artifacts.get("timeline", "") or "").strip()
        if not timeline_path:
            timeline_path = os.path.join(state.project_root, "timeline", "timeline.json")
        timeline_path = self._normalize_local_file_path(timeline_path)
        if not timeline_path or not os.path.isfile(timeline_path):
            return False
        try:
            import json
            from app.layers.timeline import Timeline

            with open(timeline_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            loaded = Timeline.from_dict(saved)
            if not loaded.tracks:
                return False

            timeline._timeline = loaded
            # Track visibility toggles are an editor-view concern, not part
            # of the serialized project model.  Do not let hidden IDs from a
            # previous project affect the restored tracks.
            timeline._timeline_hidden_track_ids.clear()
            # The source/video duration is refreshed later by set_video_source;
            # retain the saved duration until then so optional layer geometry
            # can be drawn immediately during project restoration.
            timeline._duration = max(
                float(getattr(timeline, "_duration", 0.0) or 0.0),
                float(getattr(loaded, "duration", 0.0) or 0.0),
            )
            timeline._track_heights = {
                str(track.id): int(getattr(track, "height", timeline.TRACK_DEFAULT_H))
                for track in loaded.tracks
            }
            timeline._segment_indices.clear()
            timeline._overlap_layout_cache.clear()
            timeline._overlap_row_assignments.clear()
            timeline._redraw()
            self.log(
                f"[Timeline] Restored {len(loaded.tracks)} saved track(s) "
                f"from {timeline_path}"
            )
            return True
        except Exception as exc:
            self.log(f"[Timeline] Could not restore saved timeline: {exc}")
            return False

    def load_project_context(self, state):
        if not state:
            return
        self._allow_post_pipeline_preview_assets = False
        # Subtitle Source is stored only with this project.  Old projects
        # without a value start from the normal default instead of inheriting
        # the last global .env selection.
        project_engine = str(getattr(state, "settings", {}).get("transcription_engine", "") or "").strip().lower()
        os.environ["TRANSCRIPTION_ENGINE"] = project_engine if project_engine in {"whisper", "sensevoice", "ocr"} else _default_asr_engine()
        audio_handling_mode = str(getattr(state, "settings", {}).get("audio_handling_mode", "") or "").strip().lower()
        if audio_handling_mode and hasattr(self, "audio_handling_combo"):
            combo_index = self.audio_handling_combo.findData(audio_handling_mode)
            if combo_index >= 0:
                self.audio_handling_combo.setCurrentIndex(combo_index)
        context = self.project_bridge.load_context(state)
        self.processed_artifacts = {}
        # Restore project-scoped visibility. Old projects remain visible by
        # default because they have no saved value yet.
        preview_visibility = dict(getattr(state, "settings", {}).get("preview_track_visibility") or {})
        self._subtitle_track_preview_visible = bool(preview_visibility.get("TS1", True))
        self._text_track_preview_visible = bool(preview_visibility.get("T1 Text", True))
        self._logo_track_preview_visible = bool(preview_visibility.get("L1 Logo", True))
        self._mask_track_preview_visible = bool(preview_visibility.get("M1", True))
        saved_subtitle_style = dict(getattr(state, "settings", {}).get("subtitle_style_controls") or {})
        if saved_subtitle_style:
            self._apply_subtitle_style_controls_state(saved_subtitle_style)
            self._subtitle_custom_style_state = dict(saved_subtitle_style)
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_subtitle_track_visible"):
            self.video_view.set_subtitle_track_visible(self._subtitle_track_preview_visible)
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_logo_track_visible"):
            self.video_view.set_logo_track_visible(self._logo_track_preview_visible)
        self.last_original_srt_path = ""
        self.last_translated_srt_path = ""
        self.last_extracted_audio = ""
        self.last_vocals_path = ""
        # Sync timeline track mute -> GUI per-track mute state
        self._sync_timeline_mute_to_gui()
        self._sync_audio_mix_controls_from_tracks()
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
        self._saved_timeline_model_restored = False
        if hasattr(self, "timeline"):
            self.timeline.set_segments([])
            self.timeline.set_video_thumbnails([])
            self.timeline.set_playing(False)
            # Restore optional tracks before apply_segments_to_timeline().
            # That method refreshes TS1, while preserving the restored Text,
            # Logo, Blur, and Mask tracks.
            self._saved_timeline_model_restored = bool(
                self._restore_saved_timeline_model(state)
            )
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
        self.refresh_detected_speakers_section()
        if self.current_translated_segments:
            self.refresh_auto_keyword_highlights(force=True)
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
            self._enable_post_pipeline_preview_assets(refresh=True)
            self.apply_segments_to_timeline()
            self.set_selected_segment_index(0, sync_ui=True)
        # Restore A2 Dub track if TTS was generated
        voice_path = context.get("artifacts", {}).get("voice_vi", "")
        if voice_path and os.path.exists(voice_path) and hasattr(self, "timeline"):
            self.timeline.sync_tts_track(voice_path, segments=self.current_translated_segments or self.current_segments)
            # Enable Audio tab since voice generation was completed
            if hasattr(self, "audio_tab_btn"):
                self.audio_tab_btn.setEnabled(True)
        self._sync_timeline_mute_to_gui()
        self._sync_audio_mix_controls_from_tracks()
        # OCR geometry is project-scoped.  For a reopened OCR project that has
        # not produced a transcript yet, keep the crop editor visible so the
        # user can configure the region before running Transcript.  Completed
        # projects return to an unobstructed preview until OCR is explicitly
        # reopened with the toolbar button.
        has_transcript = bool(
            self.current_segments
            or self.current_segment_models
            or (hasattr(self, "transcript_text") and self.transcript_text.toPlainText().strip())
        )
        if not bool(getattr(self, "_alternate_ocr_range_pending", None)):
            self._ocr_overlay_visible = project_engine == "ocr" and not has_transcript
        self._update_ocr_overlay()
        # Clear any stale layer selection from the previous project so
        # the inspector does not stay pinned to a track that no longer
        # exists (e.g. a BlurLayer from a previous project that was
        # removed by _restore_project_blur_state).
        if hasattr(self, "timeline"):
            try:
                self.timeline.select_layer("")
            except Exception:
                pass
        self._show_default_inspector()
        self._restore_project_blur_state(state)
        if hasattr(self, "_restore_project_mask_state"):
            try:
                self._restore_project_mask_state(state)
            except Exception:
                pass
        # Always start a reopened project focused on the source video layer,
        # never on an optional overlay that happened to be restored first.
        try:
            video_layer = None
            for track in self.timeline._timeline.tracks:
                if str(getattr(track, "name", "")) == "V1 Video" and track.layers:
                    video_layer = track.layers[0]
                    break
            if video_layer is not None:
                self.timeline.select_layer(video_layer.id)
                self.on_timeline_layer_selected(video_layer.id)
        except Exception:
            pass
        # Reconnect optional timeline layers to their preview overlays after
        # restoring the serialized model.  Timeline restoration alone is not
        # enough because these overlays are maintained by the video view.
        try:
            self._refresh_text_layer_preview(getattr(self.timeline, "_selected_layer_id", ""))
        except Exception:
            pass
        try:
            logo_track = next(
                (
                    track for track in self.timeline._timeline.tracks
                    if str(getattr(track, "name", "")) == "L1 Logo"
                    and getattr(track, "layers", None)
                ),
                None,
            )
            if logo_track is not None:
                logo_layer = next(
                    (candidate for candidate in logo_track.layers
                     if self._layer_is_active_at_preview_time(candidate)),
                    logo_track.layers[0],
                )
                self._show_logo_overlay(logo_track, logo_layer)
        except Exception:
            pass
        # Force the dual-track sidecar player to re-initialize for this
        # project. Without this, reopening a project would leave the
        # original/dubbed QMediaPlayer sidecars pointing at the previous
        # project's audio files (or empty), so the user hears nothing
        # until they press Generate.
        try:
            if hasattr(self, "sync_preview_audio_track_to_output"):
                self.sync_preview_audio_track_to_output(apply_to_player=True, force=True)
        except Exception:
            pass
        # Stop any active playback so the user re-presses Play after
        # reopening. Otherwise mpv / QMediaPlayer may keep playing the
        # previous source.
        try:
            if hasattr(self, "media_player") and self.media_player is not None:
                self.media_player.pause()
        except Exception:
            pass

    def _enable_post_pipeline_preview_assets(self, *, refresh: bool = True):
        self._allow_post_pipeline_preview_assets = True
        if refresh:
            self.refresh_timeline_waveform()
            self.refresh_timeline_video_thumbnails()

    def resolve_background_audio_path(self) -> str:
        # Voice generation receives only an explicitly added Music Layer from the timeline.
        # The source video's extracted A1 audio and separated stems are never implicitly treated as music.
        music_tracks = self._music_audio_tracks()
        if music_tracks:
            return str(music_tracks[0].get("path", "") or "")
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
        if not bool(getattr(self, "_allow_post_pipeline_preview_assets", False)):
            return
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
        if not bool(getattr(self, "_allow_post_pipeline_preview_assets", False)):
            return
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
        already_visible = bool(getattr(self, "_filter_thumbnail_visible", False))
        self._filter_thumbnail_visible = True
        if already_visible:
            self.update_frame_preview_thumbnail(image_path)
            if hasattr(self, "frame_preview_badge_label"):
                self._position_frame_preview_badge()
                self.frame_preview_badge_label.show()
            return
        self._suspend_preview_region_tools_for_filter()
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
            self._filter_thumbnail_target_height = target_height
            if hasattr(self.frame_preview_image_label, "setMinimumHeight"):
                self.frame_preview_image_label.setMinimumHeight(target_height)
            if hasattr(self.frame_preview_image_label, "setMaximumHeight"):
                self.frame_preview_image_label.setMaximumHeight(target_height)
            self.frame_preview_image_label.show()
        if hasattr(self, "video_view"):
            self.video_view.hide()
        self._force_hide_ocr_overlay_for_filter()
        self.update_frame_preview_thumbnail(image_path)
        if hasattr(self, "frame_preview_badge_label"):
            self._position_frame_preview_badge()
            self.frame_preview_badge_label.show()
        QTimer.singleShot(0, self._force_hide_ocr_overlay_for_filter)

    def hide_filter_thumbnail_preview(self):
        self._filter_thumbnail_visible = False
        if hasattr(self, "frame_preview_badge_label"):
            self.frame_preview_badge_label.hide()
        if hasattr(self, "frame_preview_image_label"):
            if hasattr(self.frame_preview_image_label, "setMinimumHeight"):
                self.frame_preview_image_label.setMinimumHeight(0)
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
        self._restore_preview_region_tools_after_filter()

    def _suspend_preview_region_tools_for_filter(self):
        self._suspend_ocr_overlay = True
        self._filter_preview_blur_was_checked = bool(
            hasattr(self, "blur_area_btn") and self.blur_area_btn.isChecked()
        )
        overlay = getattr(self, "ocr_region_overlay", None)
        self._filter_preview_ocr_was_editable = bool(getattr(overlay, "_editable", False)) if overlay is not None else False

        if hasattr(self, "blur_area_btn"):
            self.blur_area_btn.setEnabled(False)
        if hasattr(self, "video_view"):
            self.video_view.set_blur_edit_enabled(False)
        if overlay is not None:
            overlay.set_editable(False)
            overlay.hide()

    def _force_hide_ocr_overlay_for_filter(self):
        if not bool(getattr(self, "_filter_thumbnail_visible", False)):
            return
        overlay = getattr(self, "ocr_region_overlay", None)
        if overlay is not None:
            overlay.set_editable(False)
            overlay.hide()

    def _restore_preview_region_tools_after_filter(self):
        self._suspend_ocr_overlay = False
        if hasattr(self, "blur_area_btn"):
            self.blur_area_btn.setEnabled(True)
        self._sync_blur_controls()

        overlay = getattr(self, "ocr_region_overlay", None)
        if overlay is not None:
            self._update_ocr_overlay()
            if (
                bool(getattr(self, "_filter_preview_ocr_was_editable", False))
                and os.getenv("TRANSCRIPTION_ENGINE", _default_asr_engine()).strip().lower() == "ocr"
            ):
                overlay.set_editable(True)
                overlay.sync_to_view()

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

    def _update_ocr_overlay(self):
        overlay = getattr(self, "ocr_region_overlay", None)
        if overlay is None:
            return
        is_ocr = os.getenv("TRANSCRIPTION_ENGINE", _default_asr_engine()).strip().lower() == "ocr"
        alternate_ocr_active = bool(getattr(self, "_alternate_ocr_range_pending", None))
        btn = getattr(self, "ocr_region_btn", None)
        if btn:
            btn.setVisible(is_ocr)
            btn.blockSignals(True)
            btn.setChecked(bool(getattr(self, "_ocr_overlay_visible", True)))
            btn.blockSignals(False)
        if not is_ocr and not alternate_ocr_active:
            overlay._requested_visible = False
            overlay.hide()
            overlay.set_editable(False)
        else:
            overlay._requested_visible = bool(alternate_ocr_active or getattr(self, "_ocr_overlay_visible", True))
            if bool(alternate_ocr_active or getattr(self, "_ocr_overlay_visible", True)):
                overlay.set_editable(True)
                overlay.sync_to_view()
            else:
                overlay.set_editable(False)
                overlay.hide()

    def toggle_ocr_overlay_visibility(self, checked: bool):
        self._ocr_overlay_visible = bool(checked)
        overlay = getattr(self, "ocr_region_overlay", None)
        if overlay is not None:
            overlay._requested_visible = bool(checked)
            overlay.set_editable(bool(checked))
            if checked:
                overlay.sync_to_view()
                overlay.raise_()
                QTimer.singleShot(0, overlay.sync_to_view)
            else:
                overlay.hide()
        self._update_ocr_overlay()

    def cleanup_file_if_exists(self, path: str):
        cleanup_file_if_exists_impl(path)

    def get_workspace_temp_root(self, create: bool = False) -> str:
        root = os.path.normpath(os.path.join(self.workspace_root, "temp"))
        if create:
            os.makedirs(root, exist_ok=True)
        return root

    def _cleanup_temp_root(self) -> None:
        root = self.get_workspace_temp_root()
        if not os.path.isdir(root):
            return
        for entry in os.listdir(root):
            fpath = os.path.join(root, entry)
            if not os.path.isfile(fpath):
                continue
            try:
                os.remove(fpath)
            except OSError:
                pass

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
        return "both"

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
        return ("brightness", "contrast", "saturation", "gamma", "hue", "temperature", "highlights", "shadows")

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
        # Keep the inspector controls in sync as well.  The inspector has a
        # separate set of sliders from the legacy filter panel; previously a
        # Reset changed the underlying state but left those visible sliders at
        # their old values until the inspector was reopened.
        try:
            self._sync_video_inspector_ui()
        except Exception:
            pass
        if hasattr(self, "media_player") and self._is_realtime_color_filter_state():
            self._apply_realtime_color_filter_preview()
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
        self._persist_video_filter_settings()

    def on_video_filter_intensity_changed(self, value):
        if self._video_filter_ui_sync:
            return
        self._video_filter_intensity = max(0, min(100, int(value)))
        self._refresh_video_filter_ui()
        self.refresh_ui_state()
        self._mark_video_filter_preview_dirty()
        if not self._is_video_filter_slider_interacting():
            self.schedule_live_video_filter_preview()
        self._persist_video_filter_settings()

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
        self._persist_video_filter_settings()

    def reset_video_filters(self):
        self.set_video_filter_state(
            "original",
            75,
            self._default_video_filter_overrides(),
            self._default_video_filter_modified_flags(),
        )
        if self._is_realtime_color_filter_state():
            self._apply_realtime_color_filter_preview()
        self._video_filter_preview_dirty = False
        self._video_filter_apply_requested = False
        if not self._is_realtime_color_filter_state():
            self.schedule_live_video_filter_preview()
        self._persist_video_filter_settings()

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
            lut_strength = max(0.0, min(1.0, float(self._video_filter_intensity) / 100.0))
        active = any(abs(int(value)) > 0 for value in effective_values.values()) or bool(
            lut_path and lut_strength > 0.001
        )
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
        state = self.get_video_filter_state()
        active = bool(state.get("active"))
        return active

    def on_output_ratio_changed(self, *_args):
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_preview_aspect_ratio"):
            self.video_view.set_preview_aspect_ratio(self.get_output_ratio_key())
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_preview_scale_mode"):
            self.video_view.set_preview_scale_mode(self.get_output_scale_mode_key())
        self._sync_preview_framing_to_player()
        self._sync_preview_output_canvas_dimensions()
        self.update_subtitle_preview_style()
        # update_subtitle_preview_style establishes the new output canvas
        # render dimensions. Refresh Text afterwards so it cannot reuse the
        # previous Ratio's height/scale payload.
        self._refresh_text_layer_preview(getattr(getattr(self, "timeline", None), "_selected_layer_id", ""))
        self.apply_preview_blur_region()
        self.refresh_ui_state()

    def on_output_scale_mode_changed(self, *_args):
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_preview_scale_mode"):
            self.video_view.set_preview_scale_mode(self.get_output_scale_mode_key())
        self._sync_preview_framing_to_player()
        self._sync_preview_output_canvas_dimensions()
        self.update_subtitle_preview_style()
        self._refresh_text_layer_preview(getattr(getattr(self, "timeline", None), "_selected_layer_id", ""))
        self.apply_preview_blur_region()
        self.refresh_ui_state()

    def on_preview_framing_changed(self, *_args):
        self._sync_preview_framing_to_player()
        self._refresh_text_layer_preview(getattr(getattr(self, "timeline", None), "_selected_layer_id", ""))
        self.apply_preview_blur_region()
        self.refresh_ui_state()

    def reset_preview_framing(self):
        if hasattr(self, "video_view") and hasattr(self.video_view, "reset_preview_fill_focus"):
            self.video_view.reset_preview_fill_focus()
        self._sync_preview_framing_to_player()
        self.apply_preview_blur_region()
        self.refresh_ui_state()

    def get_audio_handling_mode(self):
        if not hasattr(self, "audio_handling_combo"):
            return "fast"
        value = self.audio_handling_combo.currentData()
        if value:
            return str(value).strip().lower()
        return "fast"

    def is_speaker_diarization_enabled(self) -> bool:
        engine = self.get_transcription_engine()
        return bool(
            engine != "ocr"
            and hasattr(self, "speaker_diarization_cb")
            and self.speaker_diarization_cb.isChecked()
        )

    def get_speaker_diarization_num_speakers(self) -> int:
        combo = getattr(self, "speaker_diarization_speakers_combo", None)
        if combo is None:
            return -1
        try:
            value = int(combo.currentData())
            return value if value >= 2 else -1
        except (TypeError, ValueError):
            return -1

    def update_speaker_diarization_availability(self) -> None:
        checkbox = getattr(self, "speaker_diarization_cb", None)
        hint = getattr(self, "speaker_diarization_hint_label", None)
        card = getattr(self, "speaker_diarization_card", None)
        speakers_combo = getattr(self, "speaker_diarization_speakers_combo", None)
        if checkbox is None:
            return
        engine = self.get_transcription_engine()
        available = engine != "ocr"
        if not available:
            checkbox.setChecked(False)
        checkbox.setEnabled(available)
        checkbox.setVisible(available)
        if card is not None:
            card.setVisible(available)
        if speakers_combo is not None:
            speakers_combo.setEnabled(available)
        if hint is not None:
            hint.setVisible(available)
        checkbox.setToolTip(
            "Detect speakers offline with Sherpa-ONNX and color TS1 segments."
            if available else "Speaker diarization is unavailable when Video (OCR) is selected."
        )

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
        # The old "Use AI translation" checkbox was removed when provider
        # selection moved into Settings.  A configured cloud/API provider is
        # now the explicit request to use AI; only selecting Google Translate
        # bypasses the AI branch.  Keeping the legacy checkbox fallback makes
        # older embedded UI layouts harmless.
        provider = str(os.getenv("OPENAI_PROVIDER") or "google").strip().lower()
        if provider in {"gemini", "google_ai_studio", "openai", "ollama"}:
            return True
        legacy_checkbox = getattr(self, "translator_ai_cb", None)
        return bool(legacy_checkbox and legacy_checkbox.isChecked())

    def is_skip_translation(self):
        # Translation is always part of the fixed Subtitle + Voice workflow.
        return False

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
        return " | ".join(part for part in style_parts if part).strip()

    def on_output_mode_changed(self, value: str):
        mode = "both"
        if getattr(self, "_filter_thumbnail_visible", False):
            self.hide_filter_thumbnail_preview()
        self.workflow_hint_label.setText(build_workflow_hint(mode, self.is_ai_polish_enabled()))

        show_voice = mode in ("voice", "both")
        if hasattr(self, "voice_section_card"):
            self.voice_section_card.setVisible(show_voice)
        if hasattr(self, "quick_preview_btn"):
            self.quick_preview_btn.setVisible(show_voice)
        if hasattr(self, "styled_preview_btn"):
            self.styled_preview_btn.setVisible(show_voice)
        self.mixed_audio_edit.setEnabled(show_voice)
        # Do not resurrect the pre-timeline Audio Source controls when the
        # output mode changes.  Music is added through the visible Music Layer
        # card and mixed via independent track volumes.
        self._hide_legacy_audio_source_controls()
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
        self.update_preview_context_label(guidance["has_subtitles"], guidance["has_voice_audio"])

    def update_project_header(self):
        video_path = self.video_path_edit.text().strip()
        if video_path:
            video_name = os.path.basename(video_path)
            self.project_title_label.setText(f"Project: {video_name}")
            if hasattr(self, "upload_status_label"):
                self.upload_status_label.setText(f"[OK] {video_name} uploaded")
        else:
            self.project_title_label.setText("Project: No video selected")
            if hasattr(self, "upload_status_label"):
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
        # Hide-controls is disabled - the workflow panel is always visible.
        self.set_controls_panel_visible(True)

    def set_controls_panel_visible(self, visible: bool):
        # The workflow panel is always visible. Hide-controls is disabled.
        if hasattr(self, "left_panel_scroll_area"):
            self.left_panel_scroll_area.setVisible(True)
        QTimer.singleShot(0, self._resync_preview_region_overlays)

    def _resync_preview_region_overlays(self):
        try:
            self._sync_blur_controls()
        except Exception:
            pass
        try:
            self._update_ocr_overlay()
        except Exception:
            pass

    def update_progress_checklist(self):
        self.update_workflow_stage_badges()

    def _completed_translation_provider_label(self) -> str:
        """Return the provider recorded in completed translation segments.

        This intentionally reads the result metadata rather than Settings:
        an unavailable AI provider can finish a run through Google Translate.
        """
        models = list(getattr(self, "current_translated_segment_models", []) or [])
        provider_counts = {}
        for model in models:
            provider = str(getattr(model, "metadata", {}).get("translation_provider", "") or "").strip().lower()
            if provider:
                provider_counts[provider] = provider_counts.get(provider, 0) + 1
        if not provider_counts:
            return ""
        provider = max(provider_counts, key=provider_counts.get)
        names = {
            "google-web": "Google Translate",
            "google": "Google Translate",
            "gemini": "Google AI Studio",
            "google_ai_studio": "Google AI Studio",
            "openai": "OpenAI",
            "ollama": "Ollama",
        }
        return names.get(provider, provider.replace("-", " ").title())

    def update_workflow_stage_badges(self):
        """Reflect persisted workflow artifacts in the left-side milestones."""
        badges = getattr(self, "workflow_stage_badges", {}) or {}
        labels = getattr(self, "workflow_stage_labels", {}) or {}
        if not badges:
            return
        video_path = str(self.video_path_edit.text() if hasattr(self, "video_path_edit") else "").strip()
        state = getattr(self, "current_project_state", None)
        artifacts = getattr(state, "artifacts", {}) or {}
        steps = getattr(state, "steps", {}) or {}
        has_video = bool(video_path and os.path.exists(video_path))
        transcript = bool(self.current_segments) or bool(artifacts.get("transcript_segments"))
        # PrepareWorkflow writes a compatibility SRT even when translation is
        # intentionally skipped. Only a completed translation artifact/step
        # unlocks the next Step-by-Step action.
        translation_status = str(steps.get("translate_raw", "")).lower()
        # A retained translation artifact is useful for legacy projects, but
        # it must not make the phase look completed while a new translation
        # is running or after the user stopped it.
        translated = (
            False if translation_status in {"running", "failed"}
            else translation_status == "done" or bool(artifacts.get("translation_final"))
        )
        tts_skipped = bool(state and state.settings.get("tts_skipped", False))
        voice = not tts_skipped and bool(
            artifacts.get("voice_vi") or artifacts.get("mixed_vi") or self.last_voice_vi_path or self.last_mixed_vi_path
        )
        exported = bool(artifacts.get("final_video"))
        running = str(getattr(self, "_pipeline_step", "") or "") if getattr(self, "_pipeline_active", False) else ""
        values = {
            "prepare": (has_video, "prepare"),
            "transcript": (transcript, "prepare"),
            "translate": (translated, "translation"),
            "tts": (voice, "voiceover"),
            "export": (exported, "export"),
        }
        for key, (complete, running_step) in values.items():
            label = labels.get(key)
            if label is not None and key == "translate":
                provider = self._completed_translation_provider_label() if complete else ""
                label.setText(f"Translate — {provider}" if provider else "Translate")
            badge = badges.get(key)
            if badge is None:
                continue
            is_running = running == running_step or (key == "transcript" and running == "prepare")
            if is_running:
                text, color = "Processing…", "#f6c453"
            elif complete:
                text, color = "✓ Completed", "#6ee7d6"
            elif key == "tts" and translated:
                # A translated subtitle track is exportable without a dub.
                # Keep TTS available for later regeneration, but make its
                # optional nature obvious in the workflow sidebar.
                text, color = "Optional", "#8394aa"
            else:
                text, color = "Not started", "#8394aa"
            badge.setText(text)
            badge.setStyleSheet(f"color: {color}; font-weight: 700;")

        # Step-by-Step is deliberately linear until translation is complete.
        # Translation remains repeatable, like TTS: users often adjust the
        # provider, prompt, or source subtitles and need to run it again
        # without transcribing the video a second time.
        if hasattr(self, "_generate_transcript_action"):
            self._generate_transcript_action.setEnabled(has_video and not transcript and not self._pipeline_active)
        if hasattr(self, "_generate_translate_action"):
            self._generate_translate_action.setEnabled(transcript and not self._pipeline_active)
            self._generate_translate_action.setText("Re-translate" if translated else "Auto Translate")
        if hasattr(self, "_generate_import_translated_srt_action"):
            self._generate_import_translated_srt_action.setEnabled(transcript and not self._pipeline_active)
        if hasattr(self, "_generate_tts_action"):
            self._generate_tts_action.setEnabled(
                # TTS is intentionally repeatable: subtitle/voice edits may
                # require regenerating audio after this stage was completed.
                translated and not self._pipeline_active
                and self.get_output_mode_key() in ("voice", "both")
            )
        if hasattr(self, "_generate_tts_skip_action"):
            self._generate_tts_skip_action.setEnabled(translated and not self._pipeline_active)

    def update_preview_context_label(self, has_subtitles: bool, has_voice_audio: bool):
        subtitle_source = "Vietnamese review track" if self.current_translated_segments else ("original subtitle track" if self.current_segments else "no subtitle track yet")
        # Audio is composed from independent timeline tracks now; do not
        # surface the removed "Use generated..." / "Use existing..." source
        # selector terminology in the preview context text.
        audio_parts = []
        if has_voice_audio:
            audio_parts.append("generated voice")
        if self._music_audio_tracks():
            audio_parts.append("music")
        audio_source = " + ".join(audio_parts) if audio_parts else "original audio"
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
        self.on_subtitle_style_control_edited()
        self.update_subtitle_preview_style()

    def choose_subtitle_background_color(self):
        current = getattr(self, "subtitle_background_color_hex", "#000000")
        color = QColorDialog.getColor(QColor(current), self, "Choose Subtitle Background Color")
        if not color.isValid():
            return
        self.subtitle_background_color_hex = color.name().upper()
        if hasattr(self, "subtitle_background_color_btn"):
            self.subtitle_background_color_btn.setText(self.subtitle_background_color_hex)
        self.on_subtitle_style_control_edited()
        self.update_subtitle_preview_style()

    def on_subtitle_background_width_changed(self, *_args):
        is_full_area = bool(
            hasattr(self, "subtitle_background_width_combo")
            and self.subtitle_background_width_combo.currentData() == "full_area"
        )
        hint = getattr(self, "subtitle_background_exact_hint", None)
        if hint is not None:
            hint.setVisible(is_full_area)
        # Shape selection was removed in favor of a consistent rounded
        # rectangle controlled by Corner radius.
        for name in ("subtitle_background_shape_label", "subtitle_background_shape_combo"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(False)
        self.update_subtitle_preview_style()

    def on_subtitle_font_scale_changed(self, _index: int = -1):
        """Translate the friendly percentage picker into the stored font size."""
        combo = getattr(self, "subtitle_font_scale_combo", None)
        spin = getattr(self, "subtitle_font_size_spin", None)
        if combo is None or spin is None:
            return
        percent = int(combo.currentData() or 100)
        spin.setValue(max(spin.minimum(), min(spin.maximum(), round(60 * percent / 100.0))))

    def sync_subtitle_font_scale_control(self, size: int | None = None):
        """Keep the visible selector honest when a preset/project sets a size."""
        combo = getattr(self, "subtitle_font_scale_combo", None)
        spin = getattr(self, "subtitle_font_size_spin", None)
        if combo is None or spin is None:
            return
        size = int(spin.value() if size is None else size)
        choices = [int(combo.itemData(index)) for index in range(combo.count())]
        if not choices:
            return
        nearest = min(choices, key=lambda percent: abs((60 * percent / 100.0) - size))
        index = combo.findData(nearest)
        if index >= 0 and index != combo.currentIndex():
            was_blocked = combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(was_blocked)

    def _subtitle_render_dimensions(self) -> tuple[int, int]:
        """Return the canvas dimensions the export ASS file is authored for."""
        source_w = max(1, int(getattr(self.video_view, "video_source_width", 0) or 1920))
        source_h = max(1, int(getattr(self.video_view, "video_source_height", 0) or 1080))
        video_path = self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        controller = getattr(self, "preview_controller", None)
        if controller is not None and video_path:
            try:
                target_w, target_h = controller._resolve_output_canvas_dimensions(video_path)
                if target_w and target_h:
                    return int(target_w), int(target_h)
            except Exception:
                pass
        return source_w, source_h

    def _sync_preview_output_canvas_dimensions(self):
        """Set the current output canvas before any preview-layer refresh.

        Text can exist without TS1 subtitles, so it must not depend on the
        subtitle-style path to learn that Ratio/Quality changed.
        """
        view = getattr(self, "video_view", None)
        if view is None or not hasattr(view, "set_subtitle_render_dimensions"):
            return
        width, height = self._subtitle_render_dimensions()
        view.set_subtitle_render_dimensions(width, height)

    def _resolved_subtitle_font_name(self, requested_font: str) -> str:
        """Use Qt's actual font fallback for both preview and ASS export.

        Preset fonts such as Montserrat are not installed on every Windows
        system. Qt and libass otherwise pick different fallbacks, causing
        identical text and widths to wrap on different words.
        """
        requested_font = str(requested_font or "Segoe UI").strip() or "Segoe UI"
        try:
            bundled_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets", "fonts"))
            if not getattr(self, "_bundled_subtitle_fonts_registered", False) and os.path.isdir(bundled_dir):
                for filename in os.listdir(bundled_dir):
                    if filename.lower().endswith((".ttf", ".otf")):
                        QFontDatabase.addApplicationFont(os.path.join(bundled_dir, filename))
                self._bundled_subtitle_fonts_registered = True
            resolved = QFontInfo(QFont(requested_font)).family().strip()
            return resolved or requested_font
        except Exception:
            return requested_font

    def update_subtitle_preview_style(self):
        if not hasattr(self, "video_view"):
            return
        item = self.video_view.subtitle_item
        has_video = bool(self.video_path_edit.text().strip())
        has_segments = bool(self.get_active_segments())
        if not has_video or not has_segments:
            item.set_text("")
            item.hide()
            self.sync_live_subtitle_preview()
            return
        render_w, render_h = self._subtitle_render_dimensions()
        self.video_view.set_subtitle_render_dimensions(render_w, render_h)
        source_h = max(1, render_h)
        preview_rect = self.video_view.get_preview_canvas_rect() if hasattr(self.video_view, "get_preview_canvas_rect") else self.video_view.get_video_content_rect()
        preview_h = max(1.0, preview_rect.height() or float(self.video_view.height()) or 1.0)
        preset = self.get_subtitle_preset_config()
        export_font_size = int(self.subtitle_font_size_spin.value())
        preview_scale = preview_h / source_h
        preview_text_scale = preview_scale * 0.85
        # The preview is a scaled view of the source video. Do not impose a
        # 10px floor here: it made several user-selected sizes render as the
        # same size and therefore looked as though the control had stopped
        # updating.
        # Qt's QFont and libass use different font metric engines. At the
        # small sizes used by this live preview, QFont advances the bundled
        # Montserrat glyphs about 15% wider than libass, causing earlier line
        # wraps and a visibly larger preview. Calibrate the editable layer to
        # the ASS renderer, while keeping the exported source size unchanged.
        preview_font_size = max(1, int(round(export_font_size * preview_text_scale)))
        font_name = self._resolved_subtitle_font_name(
            self.subtitle_font_combo.currentText().strip() or preset.get("font_name", "Segoe UI")
        )
        bg_alpha = float(self.subtitle_bg_alpha_spin.value()) if hasattr(self, "subtitle_bg_alpha_spin") else float(preset.get("background_alpha", 0.0))
        bg_color = QColor(getattr(self, "subtitle_background_color_hex", preset.get("background_color", "#000000")))
        bg_color.setAlpha(max(0, min(255, int(round(bg_alpha * 255.0)))))
        item.set_style(
            font_name=font_name or preset.get("font_name", "Segoe UI"),
            font_size=preview_font_size,
            font_color=self._subtitle_color_for_segment(
                (self.live_preview_segments or self.get_active_segments() or [None])[0]
            ),
            # Stroke/shadow values are authored for the source video. Scale
            # them for the smaller Qt preview too; otherwise TikTok's 7px
            # export outline overwhelms its preview-sized glyphs.
            outline_width=(
                float(preset.get("outline_width", 2)) * preview_text_scale
                if not hasattr(self, "subtitle_outline_cb") or self.subtitle_outline_cb.isChecked()
                else 0.0
            ),
            outline_color=QColor(preset.get("outline_color", "#000000")),
            background_box=bool(self.subtitle_background_cb.isChecked()),
            background_color=bg_color,
            single_line=bool(getattr(self, "subtitle_single_line_cb", None) and self.subtitle_single_line_cb.isChecked()),
            bold=bool(self.subtitle_bold_cb.isChecked()),
            shadow_color=QColor(preset.get("shadow_color", "#000000")),
            shadow_depth=float(preset.get("shadow_depth", 0)) * preview_text_scale,
        )
        position = self.get_subtitle_position_config()
        item.set_alignment(position.get("alignment_label", "Bottom"))
        item.set_positioning(
            x_offset=int(position.get("x_offset", 0)),
            bottom_offset=int(position.get("margin_v", 30)),
            custom_position_enabled=bool(position.get("custom_position_enabled", False)),
            custom_x_percent=int(position.get("custom_position_x", 50)),
            custom_y_percent=int(position.get("custom_position_y", 86)),
        )
        segments = self.live_preview_segments or self.get_active_segments()
        selected = int(getattr(self, "_selected_segment_index", -1))
        style_segment = segments[selected] if 0 <= selected < len(segments) else (segments[0] if segments else None)
        self._apply_live_subtitle_segment_color(style_segment)
        self._set_live_subtitle_effects(style_segment)
        self.video_view.reposition_subtitle()
        self.sync_live_subtitle_preview()
        self.schedule_auto_frame_preview()

    def _set_live_subtitle_effects(self, segment: dict | None, position_ms: int = 0):
        """Feed the editable preview layer the same cue effects used at export."""
        if not hasattr(self, "video_view"):
            return
        item = self.video_view.subtitle_item
        segment = segment or {}
        preset = self.get_subtitle_preset_config()
        text = str(segment.get("text", "") or "")
        mode = self.subtitle_highlight_mode_combo.currentText().strip() if hasattr(self, "subtitle_highlight_mode_combo") else "Auto"
        phrases = []
        if mode in ("Auto", "Auto + Manual"):
            phrases.extend(segment.get("auto_highlights", []) or [])
        if mode in ("Manual", "Auto + Manual"):
            phrases.extend(segment.get("manual_highlights", []) or [])
        animation = self.subtitle_animation_combo.currentText().strip().lower() if hasattr(self, "subtitle_animation_combo") else ""
        animation_duration = max(0.01, float(self.subtitle_animation_time_spin.value())) if hasattr(self, "subtitle_animation_time_spin") else 0.22
        start = float(segment.get("start", 0.0) or 0.0)
        end = max(start + 0.01, float(segment.get("end", start + 0.01) or start + 0.01))
        elapsed = max(0.0, float(position_ms) / 1000.0 - start)
        animation_progress = min(1.0, elapsed / animation_duration)
        if animation == "fade out":
            animation_progress = min(1.0, max(0.0, float(position_ms) / 1000.0 - (end - animation_duration)) / animation_duration)
        karaoke_index = -1
        if animation == "word highlight karaoke" and text:
            words = [word for word in text.split() if word]
            progress = max(0.0, min(0.999, (float(position_ms) / 1000.0 - start) / (end - start)))
            karaoke_index = min(len(words) - 1, int(progress * len(words))) if words else -1
        item.set_effects(
            highlight_color=self._highlight_color_hex() or preset.get("highlight_color", "#FFD400"),
            highlight_phrases=phrases,
            karaoke_word_index=karaoke_index,
            auto_keyword_highlight=bool(self.subtitle_keyword_highlight_cb.isChecked()) if hasattr(self, "subtitle_keyword_highlight_cb") else False,
            animation_style=animation,
            animation_progress=animation_progress,
        )

    def on_single_line_toggled(self, checked: bool):
        self.update_subtitle_preview_style()
        if not self.current_translated_segments:
            return
        if checked:
            self._split_segments_for_single_line()
        else:
            self._single_line_split_cache = None
        self.apply_segments_to_timeline()
        self.schedule_live_subtitle_preview_refresh()

    def _split_segments_for_single_line(self):
        from translation import TranslationOrchestrator
        source = list(self.current_translated_segments or [])
        if not source:
            return
        orchestrator = TranslationOrchestrator()
        provider_type, polisher = orchestrator._resolve_ai_provider()
        if not polisher or not polisher.is_configured():
            polisher = None
        split = orchestrator._split_segments_for_single_line(
            source, polisher=polisher, provider_type=provider_type, target_lang=self.get_target_language_code(),
            words_per_segment=int(self.subtitle_words_per_segment_spin.value()) if hasattr(self, "subtitle_words_per_segment_spin") else 4,
        )
        if split and split != source:
            self._single_line_split_cache = split

    def get_subtitle_export_style(self, segments=None):
        preset = self.get_subtitle_preset_config()
        # Export-only glyph calibration. ASS ScaleX/ScaleY enlarges glyphs
        # without changing font-size-derived line spacing or row placement.
        export_font_scale = max(0.1, float(getattr(self, "subtitle_export_font_scale", 1.0)))
        export_font_size = max(1, int(self.subtitle_font_size_spin.value()))
        style_segments = segments if segments is not None else self.get_active_segments()
        position = self.get_subtitle_position_config()
        # Preview and export both use the subtitle's centre anchor.  Do not
        # convert it to ASS's bottom anchor: that conversion includes the Qt
        # widget's font-metric padding and caused a vertical offset whenever
        # the output canvas ratio changed.
        custom_bottom_y = None
        return {
            "font_name": self._resolved_subtitle_font_name(
                self.subtitle_font_combo.currentText().strip() or preset.get("font_name", "Arial")
            ),
            "font_size": export_font_size,
            "font_scale": export_font_scale,
            "font_color": self._hex_to_ass_color(self.subtitle_color_hex),
            "speaker_colors": (
                [
                    self._hex_to_ass_color(self._speaker_color_hex(str(segment.get("speaker", "") or "")))
                    if str(segment.get("speaker", "") or "").strip() else ""
                    for segment in (style_segments or [])
                ]
                if self._uses_speaker_subtitle_colors() else []
            ),
            "highlight_color": self._hex_to_ass_color(self._highlight_color_hex()),
            "outline_color": self._hex_to_ass_color(preset.get("outline_color", "#000000")),
            "outline_width": (
                float(preset.get("outline_width", 2))
                if not hasattr(self, "subtitle_outline_cb") or self.subtitle_outline_cb.isChecked()
                else 0.0
            ),
            "shadow_color": self._hex_to_ass_color(preset.get("shadow_color", "#000000")),
            "shadow_depth": float(preset.get("shadow_depth", 1)),
            "shadow_alpha": float(preset.get("shadow_alpha", 0.0)),
            "background_color": self._hex_to_ass_color(
                getattr(self, "subtitle_background_color_hex", preset.get("background_color", "#000000"))
            ),
            "background_alpha": float(self.subtitle_bg_alpha_spin.value()) if hasattr(self, "subtitle_bg_alpha_spin") else float(preset.get("background_alpha", 0.0)),
            "background_padding": int(self.subtitle_background_padding_spin.value()) if hasattr(self, "subtitle_background_padding_spin") else 6,
            "background_radius": int(self.subtitle_background_radius_spin.value()) if hasattr(self, "subtitle_background_radius_spin") else 0,
            "background_width": str(self.subtitle_background_width_combo.currentData() if hasattr(self, "subtitle_background_width_combo") else "fit_text"),
            "background_shape": str(self.subtitle_background_shape_combo.currentData() if hasattr(self, "subtitle_background_shape_combo") else "rectangle"),
            "animation": self.subtitle_animation_combo.currentText().strip() or preset.get("animation", "Static"),
            "animation_duration": float(self.subtitle_animation_time_spin.value()),
            "karaoke_timing_mode": str(self.subtitle_karaoke_timing_combo.currentData() or "vietnamese"),
            "position_mode": str(position.get("position_mode", "anchor")),
            "alignment": int(position.get("alignment", 2)),
            "margin_v": int(position.get("margin_v", 30)),
            "custom_position_enabled": bool(position.get("custom_position_enabled", False)),
            "custom_position_x": int(position.get("custom_position_x", 50)),
            "custom_position_y": int(position.get("custom_position_y", 86)),
            "custom_position_bottom_y": custom_bottom_y,
            "background_box": bool(self.subtitle_background_cb.isChecked()),
            "bold": bool(self.subtitle_bold_cb.isChecked()),
            "preset_key": self.get_selected_subtitle_preset(),
            "auto_keyword_highlight": bool(self.subtitle_keyword_highlight_cb.isChecked())
            and self.subtitle_highlight_mode_combo.currentText().strip() in ("Auto", "Auto + Manual")
            and not any(seg.get("auto_highlights") for seg in (style_segments or [])),
            "manual_highlights": self._build_render_highlight_lists(style_segments or []),
            "word_timings": [list(seg.get("words", [])) for seg in (style_segments or [])],
            "blur_region": (
                self.video_view.get_blur_region_normalized()
                if hasattr(self, "video_view") and self._blur_effect_enabled()
                else None
            ),
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
        selected = self.get_selected_subtitle_preset()
        self._subtitle_preset_apply_in_progress = True
        try:
            if selected == "custom":
                if self._subtitle_custom_style_state:
                    self._apply_subtitle_style_controls_state(self._subtitle_custom_style_state)
            else:
                self.subtitle_font_combo.setCurrentText(preset.get("font_name", "Arial"))
                self.subtitle_font_size_spin.setValue(int(preset.get("font_size", self.subtitle_font_size_spin.value())))
                self.subtitle_animation_combo.setCurrentText(preset.get("animation", "Static"))
                self.subtitle_background_cb.setChecked(bool(preset.get("background_box", False)))
                self.subtitle_background_color_hex = str(
                    preset.get("background_color", getattr(self, "subtitle_background_color_hex", "#000000"))
                ).upper()
                if hasattr(self, "subtitle_background_color_btn"):
                    self.subtitle_background_color_btn.setText(self.subtitle_background_color_hex)
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
        finally:
            self._subtitle_preset_apply_in_progress = False
        if hasattr(self, "style_library_card"):
            self.style_library_card.setVisible(True)
        if hasattr(self, "highlight_card"):
            self.highlight_card.setVisible(True)
        if hasattr(self, "custom_title_card"):
            self.custom_title_card.setVisible(True)
        if hasattr(self, "subtitle_preset_summary_label"):
            self.subtitle_preset_summary_label.setText(
                f"{preset.get('label', 'Preset')}: {preset.get('summary', '')}"
            )
        self._update_animation_time_visibility()
        self.on_subtitle_background_width_changed()
        if selected == "custom":
            self._capture_subtitle_custom_style_state()
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
        self._sync_preview_framing_to_player()

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
        source_models = self.current_segment_models or []
        # Segment lists normally remain index-aligned, but imported SRTs and
        # manual timing edits may add/remove cues on only one side.  Retain a
        # fast timing lookup so the inspector can still show the matching
        # source transcript instead of an empty "Original" field.
        base_by_time = {
            (
                round(float(segment.get("start", 0.0)), 3),
                round(float(segment.get("end", 0.0)), 3),
            ): segment
            for segment in base_segments
            if isinstance(segment, dict)
        }
        # Timeline subtitle layers retain their visible `text` separately
        # from `_seg_dict`.  Keep this as the final recovery source for a
        # cue whose in-memory dictionary was rebuilt from SRT timing data.
        timeline_text_by_index = {}
        timeline_model = getattr(getattr(self, "timeline", None), "_timeline", None)
        for track in list(getattr(timeline_model, "tracks", []) or []):
            if str(getattr(getattr(track, "type", ""), "value", getattr(track, "type", ""))).lower() not in {"subtitle", "dub_subtitle"}:
                continue
            for layer in list(getattr(track, "layers", []) or []):
                metadata = getattr(layer, "metadata", {}) or {}
                try:
                    segment_index = int(metadata.get("_seg_index", -1))
                except (TypeError, ValueError):
                    continue
                if segment_index >= 0:
                    timeline_text_by_index[segment_index] = str(
                        getattr(layer, "text", "") or getattr(layer, "dub_text", "") or ""
                    )
        row_count = max(len(base_segments), len(translated_segments))
        rows = []
        for idx in range(row_count):
            base = base_segments[idx] if idx < len(base_segments) else {}
            translated = translated_segments[idx] if idx < len(translated_segments) else {}
            if translated:
                time_key = (
                    round(float(translated.get("start", 0.0)), 3),
                    round(float(translated.get("end", 0.0)), 3),
                )
                timed_base = base_by_time.get(time_key)
                if timed_base is not None:
                    base = timed_base
            reference = translated or base
            # Imported/manual translated segments do not always retain a
            # parallel original item at the same index.  Prefer the actual
            # transcript, then the source-text metadata retained by the
            # translation workflow, so the inspector never loses the source
            # text while the preview/timeline still has a visible cue.
            model_original = ""
            if idx < len(source_models):
                model_original = str(getattr(source_models[idx], "original_text", "") or "")
            original_text = str(
                translated.get("source_text", "")
                or translated.get("original_text", "")
                or base.get("original_text", "")
                or base.get("text", "")
                or model_original
                or timeline_text_by_index.get(idx, "")
                or ""
            )
            shown_text = str(translated.get("text", "") or original_text)
            rows.append(
                {
                    "segment_index": idx,
                    "start": float(reference.get("start", 0.0)),
                    "end": float(reference.get("end", 0.0)),
                    "original": original_text,
                    # Before translation this is the original transcript,
                    # which is also the text currently shown on screen.
                    "translated": shown_text,
                    "spoken": str(translated.get("tts_text") or translated.get("dubbing_vi") or translated.get("text", "")),
                    "subtitle_vi": str(translated.get("subtitle_vi") or translated.get("text", "")),
                    "dubbing_vi": str(translated.get("dubbing_vi") or translated.get("tts_text") or translated.get("text", "")),
                    "ratio": float(translated.get("ratio", 0.0) or 0.0),
                    "attempt_count": int(translated.get("attempt_count", 0) or 0),
                    "action_taken": str(translated.get("action_taken", "")),
                    "voice_speed": float(reference.get("voice_speed", 1.0)),
                    "manual_highlights": list(translated.get("manual_highlights", [])),
                }
            )
        return rows

    def _update_segment_spoken_status(self, index: int):
        row = self._find_segment_editor_row(index)
        if not row:
            return
        segment = {}
        if 0 <= index < len(self.current_translated_segments or []):
            segment = self.current_translated_segments[index] or {}
        subtitle_text = " ".join(str(segment.get("text", "") or "").split()).strip()
        spoken_text = " ".join(str(segment.get("tts_text") or segment.get("dubbing_vi") or segment.get("text", "")).split()).strip()
        # The per-segment status label was moved to the A2 Dub
        # Track Inspector. Update it there so the inspector reflects
        # whether the spoken text matches the subtitle.
        status_label = getattr(self, "audio_inspector_spoken_status_label", None)
        if status_label is not None:
            if spoken_text and subtitle_text and spoken_text != subtitle_text:
                status_label.setText("Spoken text differs from subtitle.")
            elif spoken_text:
                status_label.setText("Spoken text matches subtitle.")
            else:
                status_label.setText("")

    def _resolve_segment_voice_text(self, segment: dict) -> str:
        current = dict(segment or {})
        subtitle_text = " ".join(str(current.get("text", "") or "").split()).strip()
        if bool(current.get("voice_edited")):
            edited_text = " ".join(str(current.get("tts_text") or current.get("dubbing_vi") or "").split()).strip()
            if edited_text:
                return edited_text
        return subtitle_text

    def on_segment_spoken_text_edited(self, index: int, editor: QTextEdit):
        if getattr(self, "_syncing_segment_editor", False):
            return
        if not (0 <= index < len(self.current_translated_segments or [])):
            return
        value = " ".join(editor.toPlainText().split()).strip()
        segment = self.current_translated_segments[index]
        segment["tts_text"] = value
        segment["dubbing_vi"] = value
        segment["voice_edited"] = True
        self._voiceover_force_refresh = True
        self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
        self.persist_current_timeline_project_data()
        self._update_segment_spoken_status(index)
        self.refresh_ui_state()

    def use_spoken_text_for_subtitle(self, index: int):
        if not (0 <= index < len(self.current_translated_segments or [])):
            return
        segment = self.current_translated_segments[index]
        spoken_text = " ".join(str(segment.get("tts_text") or segment.get("dubbing_vi") or "").split()).strip()
        if not spoken_text:
            QMessageBox.information(self, "Nothing To Match", "This line does not have voice text yet.")
            return
        segment["text"] = spoken_text
        segment["subtitle_vi"] = spoken_text
        segment["voice_edited"] = True
        self._voiceover_force_refresh = True
        self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
        self._sync_hidden_translated_text_from_segments()
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.sync_segment_editor_rows()
        self.refresh_ui_state()

    def _normalize_manual_highlight(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").replace("\u2029", " ").replace("\n", " ")).strip()

    def refresh_auto_keyword_highlights(self, force: bool = False):
        if not getattr(self, "current_translated_segments", None):
            return
        if not getattr(self, "subtitle_keyword_highlight_cb", None) or not self.subtitle_keyword_highlight_cb.isChecked():
            return
        if not hasattr(self, "subtitle_highlight_mode_combo") or self.subtitle_highlight_mode_combo.currentText().strip() not in ("Auto", "Auto + Manual"):
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

        self.log(f"[Auto Highlight] Generating highlight phrases for {len(pending_texts)} subtitle lines...")
        resolved_batches = [
            [candidate.text for candidate in auto_select_matches(text, max_keywords=2)]
            for text in pending_texts
        ]

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
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif child_layout:
                while child_layout.count():
                    child_item = child_layout.takeAt(0)
                    child_widget = child_item.widget()
                    if child_widget:
                        child_widget.hide()
                        child_widget.setParent(None)
                        child_widget.deleteLater()




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
        if hasattr(self, "_refresh_audio_inspector_dub_voice_buttons"):
            try:
                self._refresh_audio_inspector_dub_voice_buttons()
            except Exception:
                pass

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

    def _set_layer_timing_controls(self, prefix: str, layer) -> None:
        """Populate an overlay inspector's Start/End controls without edits."""
        for suffix, value in (("start", float(layer.start)), ("end", float(layer.end))):
            control = getattr(self, f"{prefix}_inspector_{suffix}_spin", None)
            if control is None:
                continue
            control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(False)

    def _layer_is_active_at_preview_time(self, layer, time_seconds=None) -> bool:
        """Return whether a layer should be visible at the current playhead."""
        if not bool(getattr(layer, "visible", True)):
            return False
        if time_seconds is None:
            try:
                time_seconds = float(self.media_player.position()) / 1000.0
            except Exception:
                time_seconds = 0.0
        start = max(0.0, float(getattr(layer, "start", 0.0) or 0.0))
        end = float(getattr(layer, "end", 0.0) or 0.0)
        # Legacy layers without a valid duration continue to be visible.
        return end <= start or (start <= float(time_seconds) < end)

    def _preview_is_playing(self) -> bool:
        # The timeline keeps an explicit review/edit state synchronized from
        # the media-state callback. Prefer it here: native backends can report
        # a transient PlayingState while a pause/seek event is still being
        # delivered, which used to disable range actions immediately after a
        # range was created.
        timeline = getattr(self, "timeline", None)
        if timeline is not None and hasattr(timeline, "_playing"):
            return bool(getattr(timeline, "_playing", False))
        try:
            return bool(self.media_player.is_playing())
        except Exception:
            return False

    def _deferred_effect_layer_id_for(self, layer_type: str) -> str:
        """Return the one effect clip temporarily hidden during an edit."""
        if self._preview_is_playing():
            return ""
        if str(getattr(self, "_deferred_effect_edit_type", "") or "") != str(layer_type):
            return ""
        return str(getattr(self, "_deferred_effect_edit_layer_id", "") or "")

    def _set_deferred_effect_edit_target(self, track=None, layer=None) -> bool:
        """Suppress exactly one selected Blur/Mask effect while paused.

        Overlay geometry continues to update normally; only the MPV filter
        contribution of the selected clip is deferred until it is committed.
        """
        next_type = ""
        next_id = ""
        if layer is not None and not self._preview_is_playing():
            layer_type = str(getattr(getattr(layer, "type", ""), "value", getattr(layer, "type", ""))).lower()
            track_name = str(getattr(track, "name", "") or "")
            if layer_type == "blur" and track_name == "B1":
                next_type, next_id = "blur", str(getattr(layer, "id", "") or "")
            elif layer_type == "mask" and track_name == "M1":
                next_type, next_id = "mask", str(getattr(layer, "id", "") or "")
        changed = (
            next_type != str(getattr(self, "_deferred_effect_edit_type", "") or "")
            or next_id != str(getattr(self, "_deferred_effect_edit_layer_id", "") or "")
        )
        if changed:
            previous_id = str(getattr(self, "_deferred_effect_edit_layer_id", "") or "")
            # Restore the previously edited layer before changing the shared
            # target. This prevents a stale suppression from surviving a
            # Blur A -> Blur B or Mask A -> Mask B selection switch.
            if previous_id and not self._preview_is_playing():
                self._deferred_effect_edit_type = ""
                self._deferred_effect_edit_layer_id = ""
                self._timed_layer_preview_signature = None
                self.refresh_timed_layer_preview()
            self._deferred_effect_edit_type = next_type
            self._deferred_effect_edit_layer_id = next_id
            self._timed_layer_preview_signature = None
        return changed

    def commit_deferred_effect_editing(self, *, refresh: bool = True) -> bool:
        """Restore a deferred Blur/Mask effect using its final geometry."""
        if not getattr(self, "_deferred_effect_edit_layer_id", ""):
            return False
        self._deferred_effect_edit_type = ""
        self._deferred_effect_edit_layer_id = ""
        self._timed_layer_preview_signature = None
        if refresh:
            self.refresh_timed_layer_preview()
        return True

    def prepare_preview_for_review_mode(self) -> None:
        """Commit paused edits and remove every preview editing affordance.

        Called immediately before playback starts so the frame entering
        Review Mode already contains the final Blur/Mask graph, rather than
        waiting for the asynchronous media-player state notification.
        """
        self._preview_edit_layer_id = ""
        self.commit_deferred_effect_editing(refresh=False)
        if hasattr(self, "video_view"):
            if hasattr(self.video_view, "subtitle_item"):
                self.video_view.subtitle_item.set_editable(False)
            if hasattr(self.video_view, "set_blur_edit_enabled"):
                self.video_view.set_blur_edit_enabled(False)
            mask_overlay = getattr(self.video_view, "mask_overlay", None)
            if mask_overlay is not None:
                mask_overlay.set_editable(False)
            logo_overlay = getattr(self.video_view, "logo_overlay", None)
            if logo_overlay is not None:
                logo_overlay.set_editable(False)
        self._timed_layer_preview_signature = None
        self._refresh_text_layer_preview("")
        self.refresh_timed_layer_preview()

    def refresh_timed_layer_preview(self, position_ms=None) -> None:
        """Show only overlay layers whose timeline interval contains the playhead."""
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        time_seconds = float(position_ms if position_ms is not None else self.media_player.position()) / 1000.0
        tracked = []
        for track in self.timeline._timeline.tracks:
            for layer in track.layers:
                layer_type = str(getattr(getattr(layer, "type", ""), "value", getattr(layer, "type", ""))).lower()
                is_logo = layer_type == "image" and str(getattr(track, "name", "")) == "L1 Logo"
                if layer_type in {"blur", "mask", "text"} or is_logo:
                    tracked.append((layer.id, self._layer_is_active_at_preview_time(layer, time_seconds)))
        signature = tuple(tracked)
        if signature == getattr(self, "_timed_layer_preview_signature", None):
            return
        self._timed_layer_preview_signature = signature
        selected_id = str(getattr(self.timeline, "_selected_layer_id", "") or "")

        # Text layers are rendered independently, so filtering their payload
        # makes them disappear/reappear without changing their saved state.
        self._refresh_text_layer_preview(selected_id)

        for track in self.timeline._timeline.tracks:
            if str(getattr(track, "name", "")) == "L1 Logo":
                # The L1 header Hide/Show state is independent from playback
                # and timing.  Do not let a timed refresh recreate a hidden
                # logo when playback advances or resumes.
                if not bool(getattr(self, "_logo_track_preview_visible", True)):
                    if hasattr(self.video_view, "clear_logo"):
                        self.video_view.clear_logo()
                    continue
                active = [l for l in track.layers if self._layer_is_active_at_preview_time(l, time_seconds)]
                if active:
                    is_selected_logo = (
                        selected_id in {l.id for l in active}
                        and selected_id == str(getattr(self, "_preview_edit_layer_id", "") or "")
                        and not self._preview_is_playing()
                    )
                    target = next((l for l in active if l.id == selected_id), active[0])
                    self._show_logo_overlay(track, target, editable=is_selected_logo)
                elif hasattr(self.video_view, "clear_logo"):
                    self.video_view.clear_logo()
            elif str(getattr(track, "name", "")) == "M1":
                active_layers = [l for l in track.layers if self._layer_is_active_at_preview_time(l, time_seconds)]
                # Keep the selected region in the editor overlay, but remove
                # only that one layer from the expensive rendered effect
                # while it is being edited on a paused frame.
                overlay_regions = self._current_mask_regions_payload(time_seconds=time_seconds)
                suppressed_id = self._deferred_effect_layer_id_for("mask")
                effect_regions = self._current_mask_regions_payload(
                    time_seconds=time_seconds, exclude_layer_id=suppressed_id,
                )
                if hasattr(self.video_view, "set_mask_regions"):
                    active_index = next((i for i, l in enumerate(active_layers) if l.id == selected_id), 0)
                    self.video_view.set_mask_regions(
                        overlay_regions,
                        active_index=active_index,
                        editable=bool(
                            active_layers
                            and selected_id in {l.id for l in active_layers}
                            and selected_id == str(getattr(self, "_preview_edit_layer_id", "") or "")
                            and self._deferred_effect_layer_id_for("mask") == selected_id
                            and not self._preview_is_playing()
                        ),
                    )
                # The mask effect is independent of selection/edit handles.
                # Keep it applied when the preview is paused as well.
                self._apply_mask_to_preview(regions=effect_regions)
            elif str(getattr(track, "name", "")) == "B1":
                overlay_regions = []
                active_layers = [l for l in track.layers if self._layer_is_active_at_preview_time(l, time_seconds)]
                for layer in active_layers:
                    overlay_regions.append({
                        "x": float(getattr(layer, "position_x", 0.0)), "y": float(getattr(layer, "position_y", 0.0)),
                        "width": float(getattr(layer, "width", 0.0)), "height": float(getattr(layer, "height", 0.0)),
                        "blur_strength": float(getattr(layer, "blur_strength", 20.0)),
                        "blur_opacity": float(getattr(layer, "blur_opacity", 1.0)),
                        "pixelate": bool(getattr(layer, "pixelate", False)),
                        "pixelate_size": int(getattr(layer, "pixelate_size", 12)),
                    })
                if hasattr(self.video_view, "set_blur_regions_normalized"):
                    self.video_view.set_blur_regions_normalized(overlay_regions)
                if hasattr(self.video_view, "set_blur_edit_enabled"):
                    # B1's effect remains rendered, but its border/handles
                    # are shown only while a B1 layer is selected and the
                    # preview is paused.
                    is_selected_blur = selected_id in {l.id for l in active_layers}
                    try:
                        is_playing = bool(self.media_player.is_playing())
                    except Exception:
                        is_playing = False
                    self.video_view.set_blur_edit_enabled(bool(
                        is_selected_blur
                        and selected_id == str(getattr(self, "_preview_edit_layer_id", "") or "")
                        and self._deferred_effect_layer_id_for("blur") == selected_id
                        and not is_playing
                        and self._blur_effect_enabled()
                    ))
                # Blur has a separate MPV filter in addition to its editable
                # outline. Update that filter with the same time-filtered
                # regions; otherwise a filter applied at playback start
                # continues blurring after the outline has disappeared.
                suppressed_id = self._deferred_effect_layer_id_for("blur")
                effect_regions = [
                    region for region, layer in zip(overlay_regions, active_layers)
                    if str(getattr(layer, "id", "")) != suppressed_id
                ]
                self.apply_preview_blur_region(regions=effect_regions)

        # Rebuild both managed effect payloads once from the complete active
        # timeline after all overlay bookkeeping. This is the authoritative
        # multi-layer path: one selected layer may be suppressed, but every
        # other active Blur/Mask layer is always included.
        suppressed_mask_id = self._deferred_effect_layer_id_for("mask")
        self._apply_mask_to_preview(
            regions=self._current_mask_regions_payload(
                time_seconds=time_seconds,
                exclude_layer_id=suppressed_mask_id,
            )
        )
        blur_effect_regions = []
        suppressed_blur_id = self._deferred_effect_layer_id_for("blur")
        for track in self.timeline._timeline.tracks:
            if str(getattr(track, "name", "")) != "B1":
                continue
            for layer in track.layers:
                if not self._layer_is_active_at_preview_time(layer, time_seconds):
                    continue
                if str(getattr(layer, "id", "") or "") == suppressed_blur_id:
                    continue
                blur_effect_regions.append({
                    "x": float(getattr(layer, "position_x", 0.0)),
                    "y": float(getattr(layer, "position_y", 0.0)),
                    "width": float(getattr(layer, "width", 0.0)),
                    "height": float(getattr(layer, "height", 0.0)),
                    "blur_strength": float(getattr(layer, "blur_strength", 20.0)),
                    "blur_opacity": float(getattr(layer, "blur_opacity", 1.0)),
                    "pixelate": bool(getattr(layer, "pixelate", False)),
                    "pixelate_size": int(getattr(layer, "pixelate_size", 12)),
                })
        self.apply_preview_blur_region(regions=blur_effect_regions)

    def _wire_layer_timing_controls(self, prefix: str) -> None:
        """Wire one inspector's common Start/End controls once."""
        wired_name = f"_{prefix}_layer_timing_wired"
        if getattr(self, wired_name, False):
            return
        setattr(self, wired_name, True)
        start_control = getattr(self, f"{prefix}_inspector_start_spin", None)
        end_control = getattr(self, f"{prefix}_inspector_end_spin", None)
        if start_control is None or end_control is None:
            return

        def _selected_layer():
            selected_id = str(getattr(self.timeline, "_selected_layer_id", "") or "")
            for track in getattr(getattr(self.timeline, "_timeline", None), "tracks", []):
                for layer in track.layers:
                    if layer.id == selected_id:
                        return track, layer
            return None, None

        def _apply_timing(_value=None):
            track, layer = _selected_layer()
            if layer is None:
                return
            start = max(0.0, float(start_control.value()))
            end = max(start + float(getattr(self.timeline, "MIN_DUR", 0.1)), float(end_control.value()))
            duration = float(getattr(self.timeline, "_duration", 0.0) or 0.0)
            if duration > 0:
                start = min(start, max(0.0, duration - float(getattr(self.timeline, "MIN_DUR", 0.1))))
                end = min(end, duration)
                end = max(end, start + float(getattr(self.timeline, "MIN_DUR", 0.1)))
            layer.start, layer.end = start, end
            self._set_layer_timing_controls(prefix, layer)
            self.timeline._redraw()
            self.persist_current_timeline_project_data()
            self._timed_layer_preview_signature = None
            self.refresh_timed_layer_preview()
            if prefix == "mask":
                self._apply_mask_to_preview(
                    regions=self._current_mask_regions_payload(include_inactive=True)
                )

        start_control.valueChanged.connect(_apply_timing)
        end_control.valueChanged.connect(_apply_timing)

    def on_timeline_layer_timing_changed(self, layer_id: str, start: float, end: float):
        """Persist timeline-handle duration edits for all non-subtitle layers."""
        if self._preview_is_playing():
            return
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        for track in self.timeline._timeline.tracks:
            for layer in track.layers:
                if layer.id != layer_id:
                    continue
                if bool(getattr(layer, "locked", False)):
                    return
                layer_type = str(getattr(getattr(layer, "type", ""), "value", getattr(layer, "type", ""))).lower()
                is_logo = layer_type == "image" and str(getattr(track, "name", "")) == "L1 Logo"
                if layer_type not in {"blur", "mask", "text"} and not is_logo:
                    return
                layer.start = max(0.0, float(start))
                layer.end = max(layer.start + float(getattr(self.timeline, "MIN_DUR", 0.1)), float(end))
                self.persist_current_timeline_project_data()
                self._timed_layer_preview_signature = None
                self.refresh_timed_layer_preview()
                if layer_type == "mask":
                    self._apply_mask_to_preview(
                        regions=self._current_mask_regions_payload(include_inactive=True)
                    )
                # Refresh the visible inspector values while keeping its
                # layer-specific visual controls and preview selection intact.
                self.on_timeline_layer_selected(layer_id)
                return

    def on_timeline_layer_selected(self, layer_id: str):
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        track = None
        layer = None
        for t in self.timeline._timeline.tracks:
            for l in t.layers:
                if l.id == layer_id:
                    layer = l
                    track = t
                    break
            if layer:
                break
        # The subtitle overlay should only capture the mouse when a concrete
        # subtitle segment (TS1/S1) is selected in the timeline.  Otherwise
        # it stays click-through, preventing accidental moves while editing
        # other video layers.
        is_review_mode = self._preview_is_playing()
        if hasattr(self, "video_view") and hasattr(self.video_view, "subtitle_item"):
            layer_type = str(getattr(getattr(layer, "type", ""), "value", getattr(layer, "type", ""))).lower() if layer else ""
            self.video_view.subtitle_item.set_editable(
                not is_review_mode and layer_type in {"subtitle", "dub_subtitle"}
            )
        # During review mode a layer may still be inspected/focused, but it
        # must never acquire preview drag handles. A real paused selection is
        # the only entry point into preview editing.
        self._preview_edit_layer_id = "" if is_review_mode else str(layer_id or "")
        # Selection changes are the commit boundary for deferred Blur/Mask
        # geometry. Selecting a Blur/Mask while paused starts a new light-
        # weight edit session; selecting anything else restores the old one.
        self._set_deferred_effect_edit_target(track, layer)
        if not layer:
            self._show_default_inspector()
            # Deselecting a layer only removes edit chrome. Effects and
            # rendered layer content remain visible in the preview.
            self._timed_layer_preview_signature = None
            self.refresh_timed_layer_preview()
            return
        # A selection must respect timing immediately, including before the
        # next playback positionChanged signal is emitted.
        self._timed_layer_preview_signature = None
        self.refresh_timed_layer_preview()
        layer_type = str(getattr(layer.type, "value", layer.type)).lower()
        can_modify_layer = not bool(getattr(track, "locked", False)) and not bool(getattr(layer, "locked", False))
        if hasattr(self, "timeline_split_btn"):
            self.timeline_split_btn.setEnabled(
                not is_review_mode and can_modify_layer and (layer_type in {"subtitle", "dub_subtitle", "blur", "mask", "text"}
                or (layer_type == "image" and str(getattr(track, "name", "")) == "L1 Logo")
                )
            )
        if hasattr(self, "timeline_delete_btn"):
            self.timeline_delete_btn.setEnabled(not is_review_mode and can_modify_layer)
        if layer_type == "subtitle":
            self._show_subtitle_inspector_for_layer(layer_id)
        elif layer_type == "dub_subtitle":
            self._show_dub_subtitle_inspector_for_layer(layer_id, layer)
        elif layer_type == "audio":
            if str(getattr(track, "name", "")) != "A1 Audio":
                self._show_audio_inspector_for_track(track, layer)
        elif layer_type == "blur":
            self._show_blur_inspector_for_track(track, layer)
        elif layer_type == "video":
            self._show_video_inspector_for_track(track, layer)
        elif layer_type == "image" and str(getattr(track, "name", "")) == "L1 Logo":
            self._show_logo_overlay(track, layer)
            self._show_logo_inspector_for_track(track, layer)
        elif layer_type == "mask" or str(getattr(track, "name", "")) == "M1":
            self._show_mask_overlay(track, layer)
            self._show_mask_inspector_for_track(track, layer)
        elif layer_type == "text":
            self._show_text_inspector_for_track(track, layer)
            self._refresh_text_layer_preview(layer.id)
        else:
            # Image, sticker: show default with info
            self._show_default_inspector_for_layer(track, layer)
            # Do not clear unrelated visual layers when changing inspector
            # panels. Their effects are independent of selection.
            self._timed_layer_preview_signature = None
            self.refresh_timed_layer_preview()

    def _show_logo_overlay(self, track, layer, *, editable=True):
        """Show the draggable logo overlay for the selected logo layer."""
        if not hasattr(self, "video_view"):
            return
        # This method is also called by selection/project-restoration paths,
        # so guard it here as well as in the timed playback refresh.
        if not bool(getattr(self, "_logo_track_preview_visible", True)):
            if hasattr(self.video_view, "clear_logo"):
                self.video_view.clear_logo()
            return
        path = str(getattr(layer, "source", "") or "")
        if not path:
            return
        try:
            from app.layers.transform import Transform
            transform = getattr(layer, "transform", None) or Transform()
        except Exception:
            transform = None
        # Get position/size from the layer (use transform or defaults)
        if transform is not None and hasattr(transform, "x"):
            x = float(getattr(transform, "x", 0.1)) / 100.0
            y = float(getattr(transform, "y", 0.1)) / 100.0
            scale_x = float(getattr(transform, "scale_x", 1.0))
            scale_y = float(getattr(transform, "scale_y", 1.0))
            w = 0.2 * scale_x
            h = 0.2 * scale_y
        else:
            x, y, w, h = 0.1, 0.1, 0.2, 0.2

        # Store the handler lambdas as attributes so we can disconnect
        # them by reference. This avoids the libpyside RuntimeWarning
        # that occurs when calling disconnect() with no args or with
        # a lambda that was never connected.
        prev_moved = getattr(self, "_logo_moved_handler", None)
        if prev_moved is not None:
            try:
                self.video_view.logoMoved.disconnect(prev_moved)
            except (RuntimeError, TypeError, Exception):
                pass
        prev_deleted = getattr(self, "_logo_deleted_handler", None)
        if prev_deleted is not None:
            try:
                self.video_view.logoDeleted.disconnect(prev_deleted)
            except (RuntimeError, TypeError, Exception):
                pass

        self._logo_overlay_layer = layer

        def _moved_handler(nx, ny, nw, nh, l=layer):
            self._on_logo_moved(l, nx, ny, nw, nh)

        def _deleted_handler(l=layer):
            self._delete_logo_layer(l)

        self._logo_moved_handler = _moved_handler
        self._logo_deleted_handler = _deleted_handler

        self.video_view.logoMoved.connect(_moved_handler)
        self.video_view.logoDeleted.connect(_deleted_handler)

        logos = []
        active_index = 0
        for index, candidate in enumerate(track.layers):
            if not self._layer_is_active_at_preview_time(candidate):
                continue
            source = str(getattr(candidate, "source", "") or "")
            candidate_transform = getattr(candidate, "transform", None)
            if candidate_transform is not None and hasattr(candidate_transform, "x"):
                logo_x = float(getattr(candidate_transform, "x", 0.1)) / 100.0
                logo_y = float(getattr(candidate_transform, "y", 0.1)) / 100.0
                logo_w = 0.2 * float(getattr(candidate_transform, "scale_x", 1.0))
                logo_h = 0.2 * float(getattr(candidate_transform, "scale_y", 1.0))
                logo_rotation = float(getattr(candidate_transform, "rotation", 0.0) or 0.0)
            else:
                logo_x, logo_y, logo_w, logo_h, logo_rotation = 0.1, 0.1, 0.2, 0.2, 0.0
            logos.append({
                "source": source, "x": logo_x, "y": logo_y,
                "width": logo_w, "height": logo_h,
                "opacity": float(getattr(candidate, "opacity", 1.0) or 1.0),
                "rotation": logo_rotation,
            })
            if candidate is layer:
                active_index = len(logos) - 1
        if not logos:
            if hasattr(self.video_view, "clear_logo"):
                self.video_view.clear_logo()
            return
        self.video_view.set_logos(
            logos,
            active_index=active_index,
            editable=bool(editable and not self._preview_is_playing()
                          and str(getattr(layer, "id", "") or "") == str(getattr(self, "_preview_edit_layer_id", "") or "")),
        )

        # Push opacity + rotation from the layer to the overlay. We
        # default to fully opaque + 0° for a freshly created logo.
        opacity = float(getattr(layer, "opacity", 1.0) or 1.0)
        rotation = 0.0
        if transform is not None and hasattr(transform, "rotation"):
            try:
                rotation = float(getattr(transform, "rotation", 0.0) or 0.0)
            except (TypeError, ValueError):
                rotation = 0.0
        self.video_view.set_logo_opacity(opacity)
        self.video_view.set_logo_rotation(rotation)

    def _delete_logo_layer(self, layer):
        """Remove the logo layer from the L1 track and clean up."""
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        remaining_track = None
        remaining_layer = None
        for track in self.timeline._timeline.tracks:
            if layer in track.layers:
                track.layers.remove(layer)
                if not track.layers:
                    try:
                        self.timeline._timeline.tracks.remove(track)
                    except ValueError:
                        pass
                    if hasattr(self.timeline, "_track_heights") and track.id in self.timeline._track_heights:
                        del self.timeline._track_heights[track.id]
                else:
                    remaining_track = track
                    remaining_layer = track.layers[0]
                break
        try:
            self.timeline._selected_layer_id = remaining_layer.id if remaining_layer else ""
        except Exception:
            pass
        if hasattr(self.timeline, "_redraw"):
            self.timeline._redraw()
        if hasattr(self.timeline, "viewport"):
            self.timeline.viewport().update()
        if remaining_layer is not None:
            self._show_logo_overlay(remaining_track, remaining_layer)
        elif hasattr(self, "video_view") and hasattr(self.video_view, "clear_logo"):
            self.video_view.clear_logo()
        try:
            self.persist_current_timeline_project_data()
        except Exception:
            pass
        if hasattr(self, "_show_default_inspector"):
            self._show_default_inspector()

    def _on_logo_moved(self, layer, x, y, w, h):
        """Update the ImageLayer's transform from the logo overlay drag."""
        if self._preview_is_playing():
            return
        try:
            from app.layers.transform import Transform
            transform = Transform(
                x=float(x) * 100.0,
                y=float(y) * 100.0,
                scale_x=float(w) / 0.2 if 0.2 > 0 else 1.0,
                scale_y=float(h) / 0.2 if 0.2 > 0 else 1.0,
            )
            layer.transform = transform
        except Exception:
            pass
        # Coalesce the disk write while the overlay emits drag events.
        self.schedule_timeline_project_persist()

    def _show_mask_overlay(self, track, layer):
        """Show the draggable mask overlay for the selected mask layer."""
        if not hasattr(self, "video_view"):
            return
        if not bool(getattr(self, "_mask_track_preview_visible", True)):
            # Hide/Show controls both the visual effect and its edit chrome.
            # A later focus/selection event must not resurrect either one.
            if hasattr(self.video_view, "clear_mask_region"):
                self.video_view.clear_mask_region()
            return
        # Disconnect any previous handlers to avoid the libpyside
        # RuntimeWarning that occurs when calling disconnect() with no
        # args or a lambda that was never connected.
        prev_moved = getattr(self, "_mask_moved_handler", None)
        if prev_moved is not None:
            try:
                self.video_view.maskMoved.disconnect(prev_moved)
            except (RuntimeError, TypeError, Exception):
                pass
        prev_changed = getattr(self, "_mask_region_changed_handler", None)
        if prev_changed is not None:
            try:
                self.video_view.maskRegionChanged.disconnect(prev_changed)
            except (RuntimeError, TypeError, Exception):
                pass
        prev_deleted = getattr(self, "_mask_deleted_handler", None)
        if prev_deleted is not None:
            try:
                self.video_view.maskDeleted.disconnect(prev_deleted)
            except (RuntimeError, TypeError, Exception):
                pass

        self._mask_overlay_layer = layer

        def _moved_handler(nx, ny, nw, nh, l=layer):
            self._on_mask_moved(l, nx, ny, nw, nh)

        def _region_changed_handler(t=track, l=layer):
            # Fired continuously while the user drags the overlay. Push
            # the new region back to the layer + mpv filter so the
            # green mask follows the overlay in real time.
            self._on_mask_overlay_changed(t, l)

        def _deleted_handler(l=layer):
            self._delete_mask_layer(l)

        self._mask_moved_handler = _moved_handler
        self._mask_region_changed_handler = _region_changed_handler
        self._mask_deleted_handler = _deleted_handler

        self.video_view.maskMoved.connect(_moved_handler)
        self.video_view.maskRegionChanged.connect(_region_changed_handler)
        self.video_view.maskDeleted.connect(_deleted_handler)

        visible_layers = [candidate for candidate in track.layers
                          if self._layer_is_active_at_preview_time(candidate)]
        regions = self._current_mask_regions_payload()
        try:
            active_index = visible_layers.index(layer)
        except ValueError:
            active_index = 0
        # The overlay is always shown so the user can move / resize
        # the region regardless of the M1 track toggle. The toggle
        # only controls whether the mpv filter is applied (see
        # on_track_mask_toggled). Without this, the overlay would
        # only appear after the user clicked the mask layer track
        # to re-select it, even though the layer already exists.
        is_playing = False
        try:
            is_playing = bool(self.media_player.is_playing())
        except Exception:
            is_playing = False
        if hasattr(self, "track_label_bar"):
            self.track_label_bar.set_controls_enabled(not is_playing)
        self.video_view.set_mask_regions(
            regions, active_index=active_index, editable=not is_playing,
        )
        # Re-apply the complete active M1 payload after rebinding the
        # editable overlay. set_mask_regions only updates editor chrome; it
        # must never leave the other mask effects cleared when selection
        # changes between multiple layers.
        try:
            self._apply_mask_to_preview(
                regions=self._current_mask_regions_payload(
                    exclude_layer_id=self._deferred_effect_layer_id_for("mask")
                )
            )
        except Exception:
            pass

    def _on_mask_moved(self, layer, x, y, w, h):
        """Update Mask geometry without rebuilding its MPV effect per move."""
        if self._preview_is_playing():
            return
        try:
            layer.position_x = float(x)
            layer.position_y = float(y)
            layer.width = float(w)
            layer.height = float(h)
        except Exception:
            return
        # Persist coalesced geometry only. The selected M1 effect has already
        # been removed from the filter graph for this paused edit session.
        self.schedule_timeline_project_persist(mask_state=True)
        try:
            if (hasattr(self, "mask_inspector_x_spin")
                    and self.timeline._selected_layer_id == layer.id):
                for control, value in (
                    (self.mask_inspector_x_spin, x),
                    (self.mask_inspector_y_spin, y),
                    (self.mask_inspector_w_spin, w),
                    (self.mask_inspector_h_spin, h),
                ):
                    control.blockSignals(True)
                    control.setValue(float(value))
                    control.blockSignals(False)
        except Exception:
            pass

    def _resync_visual_layers_after_state(self):
        """Finalize overlay effects/handles after a playback state change."""
        try:
            self._timed_layer_preview_signature = None
            self.refresh_timed_layer_preview()
            self._apply_mask_to_preview()
            self._sync_blur_controls()
        except Exception:
            pass
        # Both project settings and timeline JSON are disk-backed.  Defer
        # those writes during the drag while keeping all preview state live.
        self.schedule_timeline_project_persist(mask_state=True)

    def _on_mask_overlay_changed(self, track, layer):
        """Read the current overlay region and update the layer
        position. The mpv filter is NOT re-applied here — it is
        only applied while the video is playing, to avoid lag
        during the drag. When the user presses play, the latest
        layer position is pushed to mpv via `_apply_mask_to_preview`
        (called from `toggle_play` and the stateChanged handler).
        """
        if self._preview_is_playing() or not hasattr(self, "video_view"):
            return
        overlay = getattr(self.video_view, "mask_overlay", None)
        if overlay is None or not overlay._regions:
            return
        try:
            active_index = int(getattr(overlay, "_active_index", -1))
            rect = overlay._regions[active_index]
            x = float(rect.x())
            y = float(rect.y())
            w = float(rect.width())
            h = float(rect.height())
        except Exception:
            return
        try:
            layer.position_x = x
            layer.position_y = y
            layer.width = w
            layer.height = h
        except Exception:
            return
        self.schedule_timeline_project_persist(mask_state=True)

    def _delete_mask_layer(self, layer):
        """Remove the mask layer from the M1 track and clean up."""
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        for track in self.timeline._timeline.tracks:
            if layer in track.layers:
                track.layers.remove(layer)
                if not track.layers:
                    try:
                        self.timeline._timeline.tracks.remove(track)
                    except ValueError:
                        pass
                    if hasattr(self.timeline, "_track_heights") and track.id in self.timeline._track_heights:
                        del self.timeline._track_heights[track.id]
                break
        if hasattr(self.timeline, "_redraw"):
            self.timeline._redraw()
        if hasattr(self.timeline, "viewport"):
            self.timeline.viewport().update()
        try:
            if hasattr(self, "_apply_mask_to_preview"):
                self._apply_mask_to_preview()
        except Exception:
            pass
        try:
            if hasattr(self, "persist_project_mask_state"):
                self.persist_project_mask_state()
        except Exception:
            pass
        # The mask-specific delete path returns before the shared Delete
        # handler can persist the serialized timeline.  Write timeline.json
        # here as well so a deleted M1 layer cannot reappear on reopen.
        try:
            self.persist_current_timeline_project_data()
        except Exception:
            pass
        if hasattr(self, "_show_default_inspector"):
            self._show_default_inspector()
        self._clear_effect_selection_after_delete()

    def _clear_effect_selection_after_delete(self):
        """Leave deleted Blur/Mask layers in a neutral, non-editing state."""
        self._deferred_effect_edit_type = ""
        self._deferred_effect_edit_layer_id = ""
        self._preview_edit_layer_id = ""
        self._timed_layer_preview_signature = None
        timeline = getattr(self, "timeline", None)
        model = getattr(timeline, "_timeline", None) if timeline is not None else None
        video_layer_id = ""
        video_track = None
        video_layer = None
        for track in getattr(model, "tracks", []) or []:
            track_type = str(getattr(getattr(track, "type", ""), "value", getattr(track, "type", ""))).lower()
            if track_type != "video" and str(getattr(track, "name", "")) != "V1 Video":
                continue
            if track.layers:
                video_track, video_layer = track, track.layers[0]
                video_layer_id = str(getattr(video_layer, "id", "") or "")
                break
        # If the M1 track was removed with its final layer, clear the
        # independent top-level overlay as well as the MPV effect. The
        # overlay is not owned by the timeline scene and can otherwise keep
        # painting its last region after the model is empty.
        has_mask_layers = any(
            str(getattr(track, "name", "")) == "M1" and bool(getattr(track, "layers", []))
            for track in getattr(model, "tracks", []) or []
        )
        if not has_mask_layers and hasattr(self, "video_view"):
            try:
                if hasattr(self.video_view, "clear_mask_region"):
                    self.video_view.clear_mask_region()
                elif getattr(self.video_view, "mask_overlay", None) is not None:
                    self.video_view.mask_overlay.clear_region()
            except Exception:
                pass
        if video_layer_id:
            timeline._selected_layer_id = video_layer_id
            self.on_timeline_layer_selected(video_layer_id)
        else:
            timeline._selected_layer_id = ""
            self.refresh_timed_layer_preview()

    def _show_subtitle_inspector_for_layer(self, layer_id: str):
        """Show subtitle inspector and select the matching segment."""
        self._switch_inspector("subtitle")
        if hasattr(self, "timeline") and self.timeline:
            idx = self.timeline.segment_index_for_layer_id(layer_id)
            if idx >= 0:
                self.set_selected_segment_index(idx, sync_ui=True)

    def _show_dub_subtitle_inspector_for_layer(self, layer_id: str, layer=None):
        """Show the inspector for a dub subtitle layer."""
        self._switch_inspector("subtitle")
        if hasattr(self, "timeline") and self.timeline:
            idx = self.timeline.segment_index_for_layer_id(layer_id)
            if idx >= 0:
                self.set_selected_segment_index(idx, sync_ui=True)

    def _show_audio_inspector_for_track(self, track, layer=None):
        """Show audio inspector populated with the selected track's settings."""
        self._switch_inspector("audio")
        # The Dub Voice section is only for A2 Dub/TS1. Hide it for
        # A1 Audio (or any other audio track).
        track_name = str(getattr(track, "name", "") or "")
        dub_section = getattr(self, "audio_inspector_dub_section", None)
        if dub_section is not None:
            dub_section.setVisible(track_name in ("A2 Dub", "TS1"))
        if track is None:
            return
        track_name = str(getattr(track, "name", "Audio"))
        if hasattr(self, "audio_inspector_track_name_label"):
            self.audio_inspector_track_name_label.setText(track_name)
        if hasattr(self, "audio_inspector_layer_count_label"):
            count = len(list(getattr(track, "layers", [])))
            if layer is not None:
                layer_label = f"Selected: {layer.name}"
            else:
                layer_label = "No layer selected"
            self.audio_inspector_layer_count_label.setText(
                f"{layer_label}    •    {count} layer(s) in track"
            )
        if hasattr(self, "audio_inspector_summary_label"):
            self.audio_inspector_summary_label.setText(
                f"Audio settings for {track_name}. Adjust volume, gain, "
                "speed or mute the track for preview."
            )
        # Load current track metadata into the controls
        meta = getattr(track, "metadata", None) or {}
        try:
            volume = float(meta.get("_volume", 100.0))
        except (TypeError, ValueError):
            volume = 100.0
        try:
            gain = float(meta.get("_gain_db", 0.0))
        except (TypeError, ValueError):
            gain = 0.0
        try:
            speed = float(meta.get("_speed", 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        muted = bool(meta.get("_muted", False))
        solo = bool(meta.get("_solo", False))
        try:
            fade_in = float(meta.get("_fade_in", 0.0))
        except (TypeError, ValueError):
            fade_in = 0.0
        try:
            fade_out = float(meta.get("_fade_out", 0.0))
        except (TypeError, ValueError):
            fade_out = 0.0
        if hasattr(self, "audio_inspector_gain_spin"):
            self.audio_inspector_gain_spin.blockSignals(True)
            self.audio_inspector_gain_spin.setValue(gain)
            self.audio_inspector_gain_spin.blockSignals(False)
        if hasattr(self, "audio_inspector_speed_spin"):
            self.audio_inspector_speed_spin.blockSignals(True)
            self.audio_inspector_speed_spin.setValue(speed)
            self.audio_inspector_speed_spin.blockSignals(False)
        if hasattr(self, "audio_inspector_mute_btn"):
            self.audio_inspector_mute_btn.blockSignals(True)
            self.audio_inspector_mute_btn.setChecked(muted)
            self.audio_inspector_mute_btn.setText("Unmute Track" if muted else "Mute Track")
            self.audio_inspector_mute_btn.blockSignals(False)
        if hasattr(self, "audio_inspector_solo_btn"):
            self.audio_inspector_solo_btn.blockSignals(True)
            self.audio_inspector_solo_btn.setChecked(solo)
            self.audio_inspector_solo_btn.blockSignals(False)
        if hasattr(self, "audio_inspector_fade_in_spin"):
            self.audio_inspector_fade_in_spin.blockSignals(True)
            self.audio_inspector_fade_in_spin.setValue(fade_in)
            self.audio_inspector_fade_in_spin.blockSignals(False)
        if hasattr(self, "audio_inspector_fade_out_spin"):
            self.audio_inspector_fade_out_spin.blockSignals(True)
            self.audio_inspector_fade_out_spin.setValue(fade_out)
            self.audio_inspector_fade_out_spin.blockSignals(False)
        if hasattr(self, "_refresh_audio_inspector_dub_voice_buttons"):
            try:
                self._refresh_audio_inspector_dub_voice_buttons()
            except Exception:
                pass
        if track_name not in ("A2 Dub", "TS1"):
            for attr in ("audio_inspector_use_voice_btn", "audio_inspector_regenerate_voice_btn"):
                button = getattr(self, attr, None)
                if button is not None:
                    button.setVisible(False)
                    button.setEnabled(False)

    def _show_default_inspector_for_layer(self, track, layer):
        self._switch_inspector("default")
        if hasattr(self, "default_inspector_summary_label"):
            tname = getattr(track, "name", "Track") if track else "Track"
            lname = getattr(layer, "name", "Layer") if layer else "Layer"
            ltype = str(getattr(layer.type, "value", layer.type)) if layer else "?"
            self.default_inspector_summary_label.setText(
                f"Selected: {tname} → {lname} ({ltype}).\n"
                "No per-layer settings available for this track type yet."
            )

    def _show_blur_inspector_for_track(self, track, layer=None):
        """Show the Blur Track Inspector populated with the selected track."""
        self._switch_inspector("blur")
        self._wire_blur_inspector_controls()
        self._wire_layer_timing_controls("blur")
        if track is None:
            return
        # B1 mirrors M1 interaction: all regions remain visible in the
        # preview, but only the layer selected in the timeline is editable.
        if layer is not None:
            self._set_layer_timing_controls("blur", layer)
            try:
                visible_layers = [candidate for candidate in track.layers
                                  if self._layer_is_active_at_preview_time(candidate)]
                active_index = visible_layers.index(layer) if layer in visible_layers else 0
                self.video_view.set_blur_active_index(active_index)
            except (AttributeError, ValueError):
                pass
        track_name = str(getattr(track, "name", "Blur"))
        if hasattr(self, "blur_inspector_track_name_label"):
            self.blur_inspector_track_name_label.setText(track_name)
        if hasattr(self, "blur_inspector_layer_count_label"):
            count = len(list(getattr(track, "layers", [])))
            if layer is not None:
                self.blur_inspector_layer_count_label.setText(
                    f"Selected: {layer.name}    •    {count} blur region(s) in track"
                )
            else:
                self.blur_inspector_layer_count_label.setText(
                    f"{count} blur region(s) in track"
                )
        # Load radius / opacity / pixelate from the selected layer
        # (fall back to defaults when no layer is selected).
        if layer is not None:
            try:
                strength = int(round(float(getattr(layer, "blur_strength", 20.0))))
            except (TypeError, ValueError):
                strength = 20
            strength = max(1, min(20, strength))
            try:
                opacity = float(getattr(layer, "blur_opacity", 1.0))
            except (TypeError, ValueError):
                opacity = 1.0
            opacity = max(0.0, min(1.0, opacity))
            pixelate = bool(getattr(layer, "pixelate", False))
            try:
                pixel_size = int(getattr(layer, "pixelate_size", 12))
            except (TypeError, ValueError):
                pixel_size = 12
            pixel_size = max(2, min(60, pixel_size))
        else:
            strength, opacity, pixelate, pixel_size = 20, 1.0, False, 12

        if hasattr(self, "blur_inspector_radius_slider"):
            self.blur_inspector_radius_slider.blockSignals(True)
            self.blur_inspector_radius_slider.setValue(strength)
            self.blur_inspector_radius_slider.blockSignals(False)
        if hasattr(self, "blur_inspector_radius_value_label"):
            self.blur_inspector_radius_value_label.setText(str(strength))
        if hasattr(self, "blur_inspector_opacity_slider"):
            self.blur_inspector_opacity_slider.blockSignals(True)
            self.blur_inspector_opacity_slider.setValue(int(round(opacity * 100)))
            self.blur_inspector_opacity_slider.blockSignals(False)
        if hasattr(self, "blur_inspector_opacity_value_label"):
            self.blur_inspector_opacity_value_label.setText(
                f"{int(round(opacity * 100))}%"
            )
        if hasattr(self, "blur_inspector_pixelate_cb"):
            self.blur_inspector_pixelate_cb.blockSignals(True)
            self.blur_inspector_pixelate_cb.setChecked(pixelate)
            self.blur_inspector_pixelate_cb.blockSignals(False)
        if hasattr(self, "blur_inspector_pixel_size_slider"):
            self.blur_inspector_pixel_size_slider.blockSignals(True)
            self.blur_inspector_pixel_size_slider.setValue(pixel_size)
            self.blur_inspector_pixel_size_slider.blockSignals(False)
        if hasattr(self, "blur_inspector_pixel_size_value_label"):
            self.blur_inspector_pixel_size_value_label.setText(str(pixel_size))

        if hasattr(self, "blur_inspector_summary_label"):
            self.blur_inspector_summary_label.setText(
                f"Blur regions in '{track_name}'. Use the B1 layer "
                "visibility control in the timeline to show or hide it."
            )

    def _wire_blur_inspector_controls(self):
        """One-time wiring of the Blur Inspector's per-region controls."""
        if getattr(self, "_blur_inspector_wired", False):
            return
        self._blur_inspector_wired = True

        def _selected_blur_layer():
            """Return the currently selected BlurLayer (or None)."""
            if not hasattr(self, "timeline") or not self.timeline._timeline:
                return None, None
            sid = getattr(self.timeline, "_selected_layer_id", "") or ""
            for tr in self.timeline._timeline.tracks:
                for l in tr.layers:
                    if l.id == sid:
                        return l, tr
            return None, None

        def _on_radius_changed(value):
            layer, _ = _selected_blur_layer()
            if layer is None:
                return
            try:
                layer.blur_strength = int(value)
            except Exception:
                return
            if hasattr(self, "blur_inspector_radius_value_label"):
                self.blur_inspector_radius_value_label.setText(str(int(value)))
            self._sync_blur_layer_to_preview(layer)

        def _on_opacity_changed(value):
            layer, _ = _selected_blur_layer()
            if layer is None:
                return
            opacity = max(0.0, min(1.0, float(value) / 100.0))
            try:
                layer.blur_opacity = opacity
            except Exception:
                return
            if hasattr(self, "blur_inspector_opacity_value_label"):
                self.blur_inspector_opacity_value_label.setText(f"{int(value)}%")
            self._sync_blur_layer_to_preview(layer)

        def _on_pixelate_toggled(checked):
            layer, _ = _selected_blur_layer()
            if layer is None:
                return
            try:
                layer.pixelate = bool(checked)
            except Exception:
                return
            self._sync_blur_layer_to_preview(layer)

        def _on_pixel_size_changed(value):
            layer, _ = _selected_blur_layer()
            if layer is None:
                return
            try:
                layer.pixelate_size = int(value)
            except Exception:
                return
            if hasattr(self, "blur_inspector_pixel_size_value_label"):
                self.blur_inspector_pixel_size_value_label.setText(str(int(value)))
            self._sync_blur_layer_to_preview(layer)

        self._blur_radius_handler = _on_radius_changed
        self._blur_opacity_handler = _on_opacity_changed
        self._blur_pixelate_handler = _on_pixelate_toggled
        self._blur_pixel_size_handler = _on_pixel_size_changed

        if hasattr(self, "blur_inspector_radius_slider"):
            self.blur_inspector_radius_slider.valueChanged.connect(_on_radius_changed)
        if hasattr(self, "blur_inspector_opacity_slider"):
            self.blur_inspector_opacity_slider.valueChanged.connect(_on_opacity_changed)
        if hasattr(self, "blur_inspector_pixelate_cb"):
            self.blur_inspector_pixelate_cb.toggled.connect(_on_pixelate_toggled)
        if hasattr(self, "blur_inspector_pixel_size_slider"):
            self.blur_inspector_pixel_size_slider.valueChanged.connect(_on_pixel_size_changed)

    def _sync_blur_layer_to_preview(self, layer):
        """Push a BlurLayer's per-region style back to the video preview
        + persisted state + B1 timeline regions (so the export matches).
        """
        if not hasattr(self, "video_view") or not hasattr(self.video_view, "blur_overlay"):
            return
        try:
            from app.layers.blur import BlurLayer
            regions = self.video_view.blur_overlay._regions or []
        except Exception:
            return
        # Find the index of this layer in the B1 track to map it to
        # the corresponding region in the video overlay.
        idx = -1
        if hasattr(self, "timeline") and self.timeline._timeline:
            for tr in self.timeline._timeline.tracks:
                if tr.id == layer.id or layer in tr.layers:
                    try:
                        idx = list(tr.layers).index(layer)
                    except ValueError:
                        idx = -1
                    break
        if idx < 0 or idx >= len(regions):
            return
        rect = regions[idx]
        try:
            x = float(rect.x())
            y = float(rect.y())
            w = float(rect.width())
            h = float(rect.height())
        except Exception:
            return
        # Build a single-region payload using this layer's style and
        # write it through the normal persist + preview path.
        payload = [{
            "x": x, "y": y, "width": w, "height": h,
            "blur_strength": int(getattr(layer, "blur_strength", 20)),
            "blur_opacity": float(getattr(layer, "blur_opacity", 1.0)),
            "pixelate": bool(getattr(layer, "pixelate", False)),
            "pixelate_size": int(getattr(layer, "pixelate_size", 12)),
        }]
        try:
            if hasattr(self.video_view, "set_blur_regions_normalized"):
                self.video_view.set_blur_regions_normalized(payload)
        except Exception:
            pass
        # Persist and re-apply the filter (so the export matches).
        if hasattr(self, "persist_project_blur_state"):
            try:
                self.persist_project_blur_state(regions=payload)
            except Exception:
                pass
        if hasattr(self, "apply_preview_blur_region"):
            try:
                self.apply_preview_blur_region(regions=payload, force=True)
            except Exception:
                pass
        # Push the new style onto the B1 track layers (one payload
        # entry per BlurLayer).
        if hasattr(self, "timeline") and self.timeline._timeline:
            from app.layers.blur import BlurLayer as _BL
            for tr in self.timeline._timeline.tracks:
                if tr.name == "B1":
                    for i, l in enumerate(tr.layers):
                        if i < len(payload):
                            l.blur_strength = int(payload[i].get("blur_strength", 20))
                            l.blur_opacity = float(payload[i].get("blur_opacity", 1.0))
                            l.pixelate = bool(payload[i].get("pixelate", False))
                            l.pixelate_size = int(payload[i].get("pixelate_size", 12))

    def _show_default_inspector(self):
        self._switch_inspector("default")

    def _text_layers(self):
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return []
        return [layer for track in self.timeline._timeline.tracks for layer in track.layers
                if str(getattr(getattr(layer, "type", ""), "value", getattr(layer, "type", ""))).lower() == "text"]

    def _refresh_text_layer_preview(self, active_id=""):
        if not hasattr(self, "video_view") or not hasattr(self.video_view, "set_text_layers"):
            return
        from app.layers.text import TEXT_LAYER_EXPORT_SCALE
        # Use the same source-to-preview calibration as the editable
        # subtitle overlay. TextLayer.font_size is authored at source-video
        # scale (60 px at 100%), while QFont draws in preview pixels.
        render_h = max(1, int(getattr(self.video_view, "subtitle_render_height", 0) or 0))
        if render_h <= 1:
            _render_w, render_h = self._subtitle_render_dimensions()
        preview_rect = self.video_view.get_preview_canvas_rect()
        # Preview canvases can be smaller than the output canvas on laptops
        # or after moving the Preview/Timeline splitter. Preserve the real
        # source-to-preview scale in both directions; the old 1.0 floor kept
        # Text at export size instead of matching the visible canvas.
        preview_scale = max(
            0.01,
            float(preview_rect.height() or self.video_view.height() or 1.0) / max(1, render_h),
        )
        preview_text_scale = preview_scale * TEXT_LAYER_EXPORT_SCALE
        items = []
        is_editable = (
            not self._preview_is_playing()
            and str(active_id or "")
            and str(active_id or "") == str(getattr(self, "_preview_edit_layer_id", "") or "")
        )
        effective_active_id = str(active_id or "") if is_editable else ""
        if not bool(getattr(self, "_text_track_preview_visible", True)):
            self.video_view.set_text_layers([], active_id or getattr(self.timeline, "_selected_layer_id", ""))
            return
        for layer in self._text_layers():
            if not self._layer_is_active_at_preview_time(layer):
                continue
            transform = getattr(layer, "transform", None)
            items.append({
                "id": layer.id, "text": getattr(layer, "text", ""),
                "font_name": getattr(layer, "font_name", "Arial"),
                "font_size": max(1, int(round(float(getattr(layer, "font_size", 60)) * preview_text_scale))),
                "font_color": getattr(layer, "font_color", "#FFFFFF"),
                "background_color": getattr(layer, "background_color", ""),
                "background_opacity": max(0.0, min(1.0, float(getattr(layer, "background_opacity", 0.5) or 0.0))),
                "font_bold": getattr(layer, "font_bold", False),
                "font_italic": getattr(layer, "font_italic", False),
                "font_underline": getattr(layer, "font_underline", False),
                # The shared Qt renderer scales its source-space padding with
                # the preview canvas just like glyph size, so export and the
                # editor use the same physical box geometry.
                "padding_scale": preview_scale,
                "x": getattr(transform, "x", .5) if transform else .5,
                "y": getattr(transform, "y", .5) if transform else .5,
            })
        self.video_view.set_text_layers(items, effective_active_id)
        if getattr(self.video_view, "text_overlay", None) is not None:
            self.video_view.text_overlay.set_editable(bool(is_editable))

    def _show_text_inspector_for_track(self, track, layer):
        self._switch_inspector("text")
        self._wire_text_inspector_controls()
        self._wire_layer_timing_controls("text")
        self._set_layer_timing_controls("text", layer)
        self.text_inspector_content.blockSignals(True)
        self.text_inspector_content.setPlainText(str(getattr(layer, "text", "")))
        self.text_inspector_content.blockSignals(False)
        self.text_inspector_font_combo.blockSignals(True)
        font_name = str(getattr(layer, "font_name", "Arial"))
        if self.text_inspector_font_combo.findText(font_name) < 0:
            self.text_inspector_font_combo.addItem(font_name)
        self.text_inspector_font_combo.setCurrentText(font_name)
        self.text_inspector_font_combo.blockSignals(False)
        size = int(getattr(layer, "font_size", 60))
        choices = [int(self.text_inspector_size_combo.itemData(i)) for i in range(self.text_inspector_size_combo.count())]
        nearest = min(choices, key=lambda percent: abs(60 * percent / 100.0 - size))
        self.text_inspector_size_combo.blockSignals(True)
        self.text_inspector_size_combo.setCurrentIndex(self.text_inspector_size_combo.findData(nearest))
        self.text_inspector_size_combo.blockSignals(False)
        color = str(getattr(layer, "font_color", "#FFFFFF"))
        self.text_inspector_color_btn.setText(color)
        self.text_inspector_color_btn.setStyleSheet(f"background-color: {color}; color: #fff;")
        bg = str(getattr(layer, "background_color", "") or "")
        self.text_inspector_background_btn.setText(bg or "None")
        self.text_inspector_background_btn.setStyleSheet(f"background-color: {bg or '#26364a'}; color: #fff;")
        opacity = max(0, min(100, int(round(float(getattr(layer, "background_opacity", 0.5) or 0.0) * 100))))
        self.text_inspector_background_opacity_slider.blockSignals(True)
        self.text_inspector_background_opacity_slider.setValue(opacity)
        self.text_inspector_background_opacity_slider.blockSignals(False)
        self.text_inspector_background_opacity_value.setText(f"{opacity}%")
        self.text_inspector_summary_label.setText(f"Selected: {getattr(track, 'name', 'T1 Text')} → {getattr(layer, 'name', 'Text')}. Drag it on the preview to move it.")

    def _wire_text_inspector_controls(self):
        if getattr(self, "_text_inspector_wired", False):
            return
        self._text_inspector_wired = True
        def selected():
            sid = getattr(self.timeline, "_selected_layer_id", "")
            return next((layer for layer in self._text_layers() if layer.id == sid), None)
        def changed():
            layer = selected()
            if layer:
                self._refresh_text_layer_preview(layer.id)
                self.schedule_timeline_project_persist()
        def content_changed():
            layer = selected()
            if layer:
                text = self.text_inspector_content.toPlainText()
                if not text.strip():
                    text = "Text"
                    self.text_inspector_content.blockSignals(True)
                    self.text_inspector_content.setPlainText(text)
                    self.text_inspector_content.blockSignals(False)
                layer.text = text
                first_line = next((line.strip() for line in text.splitlines() if line.strip()), "Text")
                layer.name = first_line[:24] or "Text"
                self.timeline._redraw(); changed()
        def size_changed(_index):
            layer = selected()
            if layer:
                percent = int(self.text_inspector_size_combo.currentData() or 100)
                layer.font_size = int(round(60 * percent / 100.0)); changed()
        def font_changed(value):
            layer = selected()
            if layer: layer.font_name = str(value); changed()
        def color_changed():
            from PySide6.QtWidgets import QColorDialog
            from PySide6.QtGui import QColor
            layer = selected()
            chosen = QColorDialog.getColor(QColor(getattr(layer, "font_color", "#FFFFFF")), self, "Pick text color")
            if layer and chosen.isValid():
                layer.font_color = chosen.name(); self.text_inspector_color_btn.setText(layer.font_color)
                self.text_inspector_color_btn.setStyleSheet(f"background-color: {layer.font_color}; color: #fff;"); changed()
        def background_changed():
            layer = selected()
            if layer is None: return
            current = QColor(str(getattr(layer, "background_color", "") or "#000000"))
            chosen = QColorDialog.getColor(current, self, "Choose Text Background Color")
            if chosen.isValid():
                layer.background_color = chosen.name()
                self.text_inspector_background_btn.setText(layer.background_color)
                self.text_inspector_background_btn.setStyleSheet(f"background-color: {layer.background_color}; color: #fff;"); changed()
        def background_opacity_changed(value):
            layer = selected()
            if layer is not None:
                value = max(0, min(100, int(value)))
                layer.background_opacity = value / 100.0
                self.text_inspector_background_opacity_value.setText(f"{value}%")
                changed()
        self.text_inspector_content.textChanged.connect(content_changed)
        self.text_inspector_size_combo.currentIndexChanged.connect(size_changed)
        self.text_inspector_font_combo.currentTextChanged.connect(font_changed)
        self.text_inspector_color_btn.clicked.connect(color_changed)
        self.text_inspector_background_btn.clicked.connect(background_changed)
        self.text_inspector_background_opacity_slider.valueChanged.connect(background_opacity_changed)

    def _show_logo_inspector_for_track(self, track, layer=None):
        """Show the Logo Track Inspector populated with the selected L1 layer."""
        self._switch_inspector("logo")
        self._wire_logo_inspector_controls()
        self._wire_layer_timing_controls("logo")
        if layer is None:
            return
        self._set_layer_timing_controls("logo", layer)
        # Read current opacity/rotation from the layer and apply to the
        # inspector controls.
        opacity = float(getattr(layer, "opacity", 1.0) or 1.0)
        rotation = 0.0
        try:
            transform = getattr(layer, "transform", None)
            if transform is not None and hasattr(transform, "rotation"):
                rotation = float(getattr(transform, "rotation", 0.0) or 0.0)
        except Exception:
            rotation = 0.0
        if hasattr(self, "logo_inspector_opacity_slider"):
            self.logo_inspector_opacity_slider.blockSignals(True)
            self.logo_inspector_opacity_slider.setValue(int(round(opacity * 100)))
            self.logo_inspector_opacity_slider.blockSignals(False)
        if hasattr(self, "logo_inspector_opacity_value_label"):
            self.logo_inspector_opacity_value_label.setText(f"{int(round(opacity * 100))}%")
        if hasattr(self, "logo_inspector_rotation_slider"):
            self.logo_inspector_rotation_slider.blockSignals(True)
            self.logo_inspector_rotation_slider.setValue(int(round(rotation)))
            self.logo_inspector_rotation_slider.blockSignals(False)
        if hasattr(self, "logo_inspector_rotation_value_label"):
            self.logo_inspector_rotation_value_label.setText(f"{int(round(rotation))}°")
        if hasattr(self, "logo_inspector_summary_label"):
            tname = getattr(track, "name", "L1 Logo")
            lname = getattr(layer, "name", "Logo")
            self.logo_inspector_summary_label.setText(
                f"Selected: {tname} → {lname}. "
                "Adjust opacity and rotation below; drag the logo on the "
                "preview to reposition."
            )

    def _wire_logo_inspector_controls(self):
        """One-time wiring of the Logo Inspector's opacity/rotation controls."""
        if getattr(self, "_logo_inspector_wired", False):
            return
        self._logo_inspector_wired = True

        def _on_opacity_changed(value, l=None):
            if l is None:
                l = getattr(self, "_logo_overlay_layer", None)
            if l is None:
                return
            opacity = max(0.0, min(1.0, float(value) / 100.0))
            try:
                l.opacity = opacity
            except Exception:
                pass
            if hasattr(self, "logo_inspector_opacity_value_label"):
                self.logo_inspector_opacity_value_label.setText(f"{int(value)}%")
            if hasattr(self, "video_view") and hasattr(self.video_view, "set_logo_opacity"):
                self.video_view.set_logo_opacity(opacity)

        def _on_rotation_changed(value, l=None):
            if l is None:
                l = getattr(self, "_logo_overlay_layer", None)
            if l is None:
                return
            rotation = float(value)
            try:
                from app.layers.transform import Transform
                transform = getattr(l, "transform", None) or Transform()
                transform.rotation = rotation
                l.transform = transform
            except Exception:
                pass
            if hasattr(self, "logo_inspector_rotation_value_label"):
                self.logo_inspector_rotation_value_label.setText(f"{int(value)}°")
            if hasattr(self, "video_view") and hasattr(self.video_view, "set_logo_rotation"):
                self.video_view.set_logo_rotation(rotation)

        # Store handlers so we can disconnect on re-wire.
        self._logo_opacity_handler = _on_opacity_changed
        self._logo_rotation_handler = _on_rotation_changed

        if hasattr(self, "logo_inspector_opacity_slider"):
            self.logo_inspector_opacity_slider.valueChanged.connect(_on_opacity_changed)
        if hasattr(self, "logo_inspector_rotation_slider"):
            self.logo_inspector_rotation_slider.valueChanged.connect(_on_rotation_changed)

    def _show_video_inspector_for_track(self, track, layer=None):
        """Show the Video Track Inspector (V1 Video)."""
        if not self._video_filter_inspector_available():
            self._switch_inspector("default")
            if hasattr(self, "default_inspector_summary_label"):
                self.default_inspector_summary_label.setText(
                    "Video Filter Inspector requires the gpu-next preview backend."
                )
            return
        self._switch_inspector("video")
        if track is None:
            return
        if hasattr(self, "video_inspector_summary_label"):
            self.video_inspector_summary_label.setText(
                "Adjust the preset, intensity and fine-tune each channel below."
            )
        # Populate the inline filter controls
        self._wire_video_inspector_controls()
        self._refresh_video_inspector_status()

    def _wire_video_inspector_controls(self):
        """One-time wiring of the inline video filter controls."""
        if getattr(self, "_video_inspector_wired", False):
            return
        # Preset combo
        if hasattr(self, "video_inspector_preset_combo"):
            preset_keys = (
                list(self._video_filter_presets().keys())
                if hasattr(self, "_video_filter_presets")
                else ["original", "bright", "warm", "vivid", "cool", "soft"]
            )
            preset_labels = {
                "original": "Original",
                "bright": "Bright",
                "warm": "Warm",
                "vivid": "Vivid",
                "cool": "Cool",
                "soft": "Soft",
            }
            for key in preset_keys:
                label = preset_labels.get(str(key), str(key).title())
                self.video_inspector_preset_combo.addItem(label, str(key))
            self.video_inspector_preset_combo.currentIndexChanged.connect(
                self._on_video_inspector_preset_changed
            )
        # Intensity
        if hasattr(self, "video_inspector_intensity_slider"):
            self.video_inspector_intensity_slider.valueChanged.connect(
                self._on_video_inspector_intensity_changed
            )
            self.video_inspector_intensity_slider.sliderReleased.connect(
                self._on_video_inspector_intensity_released
            )
        # Adjust sliders
        if hasattr(self, "video_inspector_adjust_sliders"):
            for field_key, (slider, value_lbl) in self.video_inspector_adjust_sliders.items():
                slider.valueChanged.connect(
                    lambda v, lbl=value_lbl, fk=field_key: self._on_video_inspector_adjust_changed(fk, v, lbl)
                )
                slider.sliderReleased.connect(
                    lambda fk=field_key: self._on_video_inspector_adjust_released(fk)
                )
        # Reset
        if hasattr(self, "video_inspector_reset_btn"):
            self.video_inspector_reset_btn.clicked.connect(self._on_video_inspector_reset)
        self._video_inspector_wired = True
        # Initial UI sync
        self._sync_video_inspector_ui()

    def _sync_video_inspector_ui(self):
        if hasattr(self, "video_inspector_preset_combo"):
            try:
                key = self._normalize_video_filter_preset_key(
                    getattr(self, "_video_filter_preset_key", "original")
                )
                for i in range(self.video_inspector_preset_combo.count()):
                    if self.video_inspector_preset_combo.itemData(i) == key:
                        self.video_inspector_preset_combo.blockSignals(True)
                        self.video_inspector_preset_combo.setCurrentIndex(i)
                        self.video_inspector_preset_combo.blockSignals(False)
                        break
            except Exception:
                pass
        if hasattr(self, "video_inspector_intensity_slider"):
            try:
                self.video_inspector_intensity_slider.blockSignals(True)
                self.video_inspector_intensity_slider.setValue(int(self._video_filter_intensity))
                self.video_inspector_intensity_slider.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "video_inspector_intensity_value_label"):
            try:
                self.video_inspector_intensity_value_label.setText(str(int(self._video_filter_intensity)))
            except Exception:
                pass
        if hasattr(self, "video_inspector_adjust_sliders"):
            overrides = getattr(self, "_video_filter_adjust_overrides", {}) or {}
            for field_key, (slider, value_lbl) in self.video_inspector_adjust_sliders.items():
                try:
                    val = int(overrides.get(field_key, 0))
                except Exception:
                    val = 0
                try:
                    slider.blockSignals(True)
                    slider.setValue(val)
                    slider.blockSignals(False)
                except Exception:
                    pass
                value_lbl.setText(str(val))

    def _refresh_video_inspector_status(self):
        try:
            if not hasattr(self, "video_inspector_status_label"):
                return
            try:
                active = bool(self.has_active_video_filters())
            except Exception:
                active = False
            realtime = self._is_realtime_color_filter_state()
            
            if active and realtime:
                self.video_inspector_status_label.setText("✓ Realtime preview")
                self.video_inspector_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
            elif active:
                self.video_inspector_status_label.setText("✓ Filter applied")
                self.video_inspector_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
            else:
                self.video_inspector_status_label.setText("No filter applied")
                self.video_inspector_status_label.setStyleSheet("color: #888; font-weight: normal;")
            if hasattr(self, "video_inspector_reset_btn"):
                self.video_inspector_reset_btn.setEnabled(self._video_filter_inspector_available())
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"[Filter] Status refresh error: {e}")

    def _on_video_inspector_preset_changed(self, index: int):
        if not hasattr(self, "video_inspector_preset_combo"):
            return
        try:
            key = self.video_inspector_preset_combo.itemData(index)
            if not key:
                return
            self.on_video_filter_preset_selected(str(key))
        except Exception:
            pass
        # When the preset changes, the base values for each adjust
        # field change too. Refresh the slider UI so the user can see
        # what the new preset looks like at the current intensity.
        self._sync_video_inspector_ui()
        self._refresh_video_inspector_status()
        if hasattr(self, "refresh_ui_state"):
            self.refresh_ui_state()

    def _on_video_inspector_intensity_changed(self, value: int):
        if hasattr(self, "video_inspector_intensity_value_label"):
            self.video_inspector_intensity_value_label.setText(str(int(value)))
        try:
            self.on_video_filter_intensity_changed(int(value))
        except Exception:
            pass
        self._refresh_video_inspector_status()

    def _on_video_inspector_intensity_released(self):
        if hasattr(self, "on_video_filter_slider_released"):
            try:
                self.on_video_filter_slider_released()
            except Exception:
                pass
        self._refresh_video_inspector_status()

    def _on_video_inspector_adjust_changed(self, field_key: str, value: int, value_lbl):
        value_lbl.setText(str(int(value)))
        self.on_video_filter_adjust_changed(field_key, int(value))
        self._refresh_video_inspector_status()

    def _video_filter_inspector_available(self) -> bool:
        """Return whether the initialized preview backend supports realtime filters."""
        backend = getattr(self, "media_player", None)
        if backend is None or str(getattr(backend, "backend_name", "")) != "libmpv":
            return False
        if not bool(getattr(backend, "_gpu_next_enabled", False)):
            return False
        try:
            vo = backend._player.vo
            return any(
                str(item.get("name", "")) == "gpu-next"
                for item in (vo or [])
                if isinstance(item, dict)
            )
        except Exception:
            return False

    def _on_video_inspector_adjust_released(self, field_key: str):
        if hasattr(self, "on_video_filter_slider_released"):
            try:
                self.on_video_filter_slider_released()
            except Exception:
                pass
        self._refresh_video_inspector_status()

    def _on_video_inspector_reset(self):
        self.reset_video_filters()
        self._refresh_video_inspector_status()
        if hasattr(self, "refresh_ui_state"):
            self.refresh_ui_state()

    def _current_blur_track_for_inspector(self):
        """Return the Blur Track currently displayed in the Blur inspector."""
        if not hasattr(self, "blur_inspector_track_name_label"):
            return None, None
        target = self.blur_inspector_track_name_label.text().strip()
        if not target or target == "-":
            return None, None
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return None, None
        for t in self.timeline._timeline.tracks:
            if t.name == target:
                return t, target
        return None, None

    def on_blur_inspector_show_toggled(self, checked: bool):
        """Toggle whether the blur is rendered on the video preview.

        The blur layers remain in the timeline; only the visual mpv vf
        filter is toggled on/off via the media player's blur region.
        """
        track, _track_name = self._current_blur_track_for_inspector()
        if track is None:
            return
        if not isinstance(track.metadata, dict):
            track.metadata = {}
        track.metadata["_show_on_preview"] = bool(checked)
        if hasattr(self, "media_player") and self.media_player is not None:
            if checked:
                # Re-apply the blur region to the media player.
                if hasattr(self, "apply_preview_blur_region"):
                    try:
                        self.apply_preview_blur_region(force=True)
                    except Exception:
                        pass
            else:
                # Clear the blur vf filter but keep the layer data.
                try:
                    self.media_player.clear_blur_region()
                except Exception:
                    pass
        if hasattr(self, "blur_inspector_summary_label"):
            state = "shown" if checked else "hidden"
            self.blur_inspector_summary_label.setText(
                f"The visual blur is currently {state} on the video preview."
            )

    def _switch_inspector(self, kind: str):
        if not hasattr(self, "inspector_stack"):
            return
        idx_map = {
            "subtitle": 0,
            "audio": 1,
            "blur": 2,
            "video": 3,
            "default": 4,
            "logo": 5,
            "mask": 6,
            "text": 7,
        }
        target = idx_map.get(kind, 4)
        if self.inspector_stack.currentIndex() != target:
            self.inspector_stack.setCurrentIndex(target)
        # The handle/toggle button is always visible so the user can
        # The handle/toggle UI was removed - the track inspector is
        # always expanded. No need to show/hide a handle.
        # Clicking a track layer opens the inspector (auto-expand shell).
        if kind in ("subtitle", "audio", "blur", "video", "logo", "mask", "text"):
            self.set_inspector_collapsed(False)

    def _current_audio_track_for_inspector(self):
        """Return the Track object currently displayed in the audio inspector."""
        if not hasattr(self, "audio_inspector_card") or not hasattr(self, "timeline"):
            return None, None
        if not self.timeline._timeline:
            return None, None
        if not hasattr(self, "audio_inspector_track_name_label"):
            return None, None
        target = self.audio_inspector_track_name_label.text().strip()
        if not target or target == "-":
            return None, None
        for t in self.timeline._timeline.tracks:
            if t.name == target:
                return t, target
        return None, None

    def on_audio_inspector_gain_changed(self, value: float):
        track, track_name = self._current_audio_track_for_inspector()
        if track is None:
            return
        if not isinstance(track.metadata, dict):
            track.metadata = {}
        track.metadata["_gain_db"] = float(value)
        self._apply_audio_track_settings(track_name)

    def on_audio_inspector_speed_changed(self, value: float):
        track, track_name = self._current_audio_track_for_inspector()
        if track is None:
            return
        if not isinstance(track.metadata, dict):
            track.metadata = {}
        track.metadata["_speed"] = float(value)
        self._apply_audio_track_settings(track_name)

    def on_audio_inspector_fade_in_changed(self, value: float):
        track, track_name = self._current_audio_track_for_inspector()
        if track is None:
            return
        if not isinstance(track.metadata, dict):
            track.metadata = {}
        track.metadata["_fade_in"] = float(value)
        self._apply_audio_track_settings(track_name)

    def on_audio_inspector_fade_out_changed(self, value: float):
        track, track_name = self._current_audio_track_for_inspector()
        if track is None:
            return
        if not isinstance(track.metadata, dict):
            track.metadata = {}
        track.metadata["_fade_out"] = float(value)
        self._apply_audio_track_settings(track_name)

    def on_audio_inspector_mute_toggled(self, checked: bool):
        track, track_name = self._current_audio_track_for_inspector()
        if track is None:
            return
        if not isinstance(track.metadata, dict):
            track.metadata = {}
        track.metadata["_muted"] = bool(checked)
        track.muted = bool(checked)
        if hasattr(self, "audio_inspector_mute_btn"):
            self.audio_inspector_mute_btn.setText(
                "Unmute Track" if checked else "Mute Track"
            )
        self._apply_audio_track_settings(track_name)

    def on_audio_inspector_solo_toggled(self, checked: bool):
        track, track_name = self._current_audio_track_for_inspector()
        if track is None:
            return
        if not isinstance(track.metadata, dict):
            track.metadata = {}
        track.metadata["_solo"] = bool(checked)
        self._apply_audio_track_settings(track_name)
        # Solo changes affect every other audio track.  Re-apply the two
        # preview sidecars (and rebuild the composed Music/TTS sidecar when
        # present) so preview follows the same routing used by export.
        if track_name != "A1 Audio":
            self._apply_audio_track_settings("A1 Audio")
        if track_name not in ("A2 Dub", "TS1"):
            self._apply_audio_track_settings("TS1")
        if self._music_audio_tracks():
            self._schedule_preview_audio_refresh(force=True)

    def _refresh_audio_inspector_dub_voice_buttons(self):
        """Enable/disable Dub Voice buttons and populate shared/tabs."""
        idx = int(getattr(self, "_selected_segment_index", -1))
        segments = self.get_active_segments() or []
        valid = 0 <= idx < len(segments)
        seg = segments[idx] if valid and isinstance(segments[idx], dict) else {}
        translation_ready = self._translation_phase_complete()
        for attr in (
            "audio_inspector_use_voice_btn",
            "audio_inspector_regenerate_voice_btn",
        ):
            btn = getattr(self, attr, None)
            if btn is not None:
                # These actions only make sense once translated subtitle
                # data exists.  Keep them out of the inspector until the
                # Translation phase has completed successfully.
                btn.setVisible(translation_ready)
                btn.setEnabled(valid and translation_ready)
        # Shared section: Original text
        orig_lbl = getattr(self, "inspector_original_text_label", None)
        orig_widget = getattr(self, "inspector_shared_original_label", None)
        if orig_lbl is not None:
            orig_text = ""
            if valid:
                row = self._find_segment_editor_row(idx)
                if row is not None:
                    orig_text = str(row.get("original", "") or "")
                if not orig_text:
                    orig_text = str(
                        seg.get("source_text", "")
                        or seg.get("original_text", "")
                        or seg.get("text", "")
                        or ""
                    )
            orig_lbl.setText(orig_text if orig_text else "")
            if orig_widget is not None:
                orig_widget.setVisible(bool(orig_text))

    def on_audio_inspector_regenerate_voice_clicked(self):
        if not self._translation_phase_complete():
            QMessageBox.information(self, "Voice Unavailable", "Complete the Translation phase before generating subtitle voice audio.")
            return
        idx = int(getattr(self, "_selected_segment_index", -1))
        segments = self.get_active_segments() or []
        if not (0 <= idx < len(segments)):
            return
        self.preview_segment_audio(idx)

    AUDIO_MIX_PRESETS = {
        "original_only": (100, 0),
        "prefer_original": (80, 20),
        "balanced": (100, 100),
        "prefer_dub": (20, 80),
        "dub_only": (0, 100),
    }

    def on_audio_mix_preset_changed(self):
        if not hasattr(self, "audio_mix_preset_combo"):
            return
        preset_key = str(self.audio_mix_preset_combo.currentData() or "").strip().lower()
        if preset_key in self.AUDIO_MIX_PRESETS:
            a1_val, a2_val = self.AUDIO_MIX_PRESETS[preset_key]
            if hasattr(self, "audio_a1_volume_slider"):
                self.audio_a1_volume_slider.blockSignals(True)
                self.audio_a1_volume_slider.setValue(a1_val)
                self.audio_a1_volume_slider.blockSignals(False)
            if hasattr(self, "audio_a1_volume_label"):
                self.audio_a1_volume_label.setText(f"{int(a1_val)}%")
            if hasattr(self, "audio_a2_volume_slider"):
                self.audio_a2_volume_slider.blockSignals(True)
                self.audio_a2_volume_slider.setValue(a2_val)
                self.audio_a2_volume_slider.blockSignals(False)
            if hasattr(self, "audio_a2_volume_label"):
                self.audio_a2_volume_label.setText(f"{int(a2_val)}%")
            self._apply_audio_mix_to_tracks(a1_val, a2_val)

    def on_audio_a1_volume_changed(self, value: int):
        if hasattr(self, "audio_a1_volume_label"):
            self.audio_a1_volume_label.setText(f"{int(value)}%")
        self._sync_audio_track_volume("A1 Audio", int(value))
        self._set_audio_mix_preset_custom()

    def on_audio_a2_volume_changed(self, value: int):
        if hasattr(self, "audio_a2_volume_label"):
            self.audio_a2_volume_label.setText(f"{int(value)}%")
        self._sync_audio_track_volume("TS1", int(value))
        # Music is composed into the dubbed sidecar.  Changing the TTS level
        # must rebuild that sidecar even when the TTS level becomes 0%, so the
        # music-only result is loaded instead of leaving the previous render.
        if self._music_audio_tracks():
            self._schedule_preview_audio_refresh(force=True)
        self._set_audio_mix_preset_custom()

    def on_audio_music_volume_changed(self, value: int):
        if hasattr(self, "audio_music_volume_label"):
            self.audio_music_volume_label.setText(f"{int(value)}%")
        self._sync_audio_track_volume("A2 Music", int(value))
        # The music level is baked into the composed dubbed sidecar. Refresh
        # immediately so a prior original-only sidecar cannot remain audible
        # after the user enables/raises Music.
        self._schedule_preview_audio_refresh(force=True)

    def _apply_audio_mix_to_tracks(self, a1_val: int, a2_val: int):
        self._sync_audio_track_volume("A1 Audio", a1_val)
        self._sync_audio_track_volume("TS1", a2_val)

    def _sync_audio_track_volume(self, track_name: str, volume: int):
        if not hasattr(self, "timeline") or self.timeline is None:
            return
        # TS1 is the current dubbed/subtitle track name; A2 Dub is retained
        # for projects created by older releases.  Treat both as the same
        # logical TTS volume control so a restored legacy timeline cannot
        # silently ignore a slider update.
        names = {str(track_name)}
        if str(track_name) in {"TS1", "A2 Dub"}:
            names.update({"TS1", "A2 Dub"})
        for t in self.timeline._timeline.tracks:
            if str(getattr(t, "name", "")) in names:
                if not isinstance(t.metadata, dict):
                    t.metadata = {}
                t.metadata["_volume"] = float(volume)
                self._apply_audio_track_settings(str(getattr(t, "name", track_name)))
                self.schedule_timeline_project_persist()

    def _sync_audio_mix_controls_from_tracks(self):
        """Load timeline track volumes into the Audio tab controls."""
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        values = {
            "A1 Audio": 50,
            "TS1": 100,
            "A2 Dub": 100,
            "A2 Music": 30,
        }
        for track in self.timeline._timeline.tracks:
            if track.name not in values:
                continue
            meta = getattr(track, "metadata", {}) or {}
            try:
                values[track.name] = int(round(float(meta.get("_volume", values[track.name]))))
            except (TypeError, ValueError):
                pass
        if hasattr(self, "audio_a1_volume_slider"):
            self.audio_a1_volume_slider.blockSignals(True)
            self.audio_a1_volume_slider.setValue(max(0, min(200, values["A1 Audio"])))
            self.audio_a1_volume_slider.blockSignals(False)
            if hasattr(self, "audio_a1_volume_label"):
                self.audio_a1_volume_label.setText(f"{values['A1 Audio']}%")
        tts_value = values["TS1"] if any(t.name == "TS1" for t in self.timeline._timeline.tracks) else values["A2 Dub"]
        if hasattr(self, "audio_a2_volume_slider"):
            self.audio_a2_volume_slider.blockSignals(True)
            self.audio_a2_volume_slider.setValue(max(0, min(200, tts_value)))
            self.audio_a2_volume_slider.blockSignals(False)
            if hasattr(self, "audio_a2_volume_label"):
                self.audio_a2_volume_label.setText(f"{tts_value}%")
        if hasattr(self, "audio_music_volume_slider"):
            self.audio_music_volume_slider.blockSignals(True)
            self.audio_music_volume_slider.setValue(max(0, min(200, values["A2 Music"])))
            self.audio_music_volume_slider.blockSignals(False)
            if hasattr(self, "audio_music_volume_label"):
                self.audio_music_volume_label.setText(f"{values['A2 Music']}%")
        self._refresh_music_layer_summary()

    def _set_audio_mix_preset_custom(self):
        if not hasattr(self, "audio_mix_preset_combo"):
            return
        idx = self.audio_mix_preset_combo.findData("custom")
        if idx >= 0 and self.audio_mix_preset_combo.currentIndex() != idx:
            self.audio_mix_preset_combo.setCurrentIndex(idx)

    def _apply_audio_track_settings(self, track_name: str):
        """Apply per-track volume/gain/mute to the underlying media player.

        Maps the timeline track name to the media player:
          "A1 Audio" -> QMediaPlayer #1 (original sidecar)
          "A2 Dub" / "TS1" -> QMediaPlayer #2 (dubbed sidecar)
        """
        if not hasattr(self, "media_player") or self.media_player is None:
            return
        try:
            if track_name == "A1 Audio":
                vol = self._compute_audio_track_volume(track_name, base=100.0)
                gain_db = self._get_audio_track_gain_db(track_name)
                effective = vol * (10 ** (gain_db / 20.0))
                effective = max(0.0, min(200.0, effective))
                if hasattr(self.media_player, "set_original_volume"):
                    self.media_player.set_original_volume(effective)
                muted = self._is_audio_track_muted(track_name)
                if hasattr(self.media_player, "set_mute_original"):
                    self.media_player.set_mute_original(muted)
            elif track_name in ("A2 Dub", "TS1"):
                # When Music is present the dubbed sidecar is a composed
                # TTS+Music render.  TTS volume/mute must be baked into that
                # render; applying the TS1 volume to the whole sidecar would
                # incorrectly attenuate the music as well.
                # Presence of a Music Layer is enough to choose the composed
                # sidecar path; avoid resolving/building that mix
                # synchronously from a slider callback.
                if bool(self._music_audio_tracks()):
                    if hasattr(self.media_player, "set_dubbed_volume"):
                        self.media_player.set_dubbed_volume(100.0)
                    if hasattr(self.media_player, "set_mute_dubbed"):
                        self.media_player.set_mute_dubbed(False)
                    self._schedule_preview_audio_refresh()
                else:
                    vol = self._compute_audio_track_volume(track_name, base=100.0)
                    gain_db = self._get_audio_track_gain_db(track_name)
                    effective = vol * (10 ** (gain_db / 20.0))
                    effective = max(0.0, min(200.0, effective))
                    if hasattr(self.media_player, "set_dubbed_volume"):
                        self.media_player.set_dubbed_volume(effective)
                    muted = self._is_audio_track_muted(track_name)
                    if hasattr(self.media_player, "set_mute_dubbed"):
                        self.media_player.set_mute_dubbed(muted)
            elif track_name == "A2 Music":
                # Music is composed into the dubbed sidecar alongside TS1;
                # rebuild that lightweight cached mix after a volume change.
                self._schedule_preview_audio_refresh()
        except Exception:
            pass

    def _get_audio_track_meta(self, track_name: str) -> dict:
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return {}
        for t in self.timeline._timeline.tracks:
            if t.name == track_name:
                if not isinstance(t.metadata, dict):
                    t.metadata = {}
                return t.metadata
        return {}

    def _get_audio_track_volume(self, track_name: str) -> float:
        meta = self._get_audio_track_meta(track_name)
        default_vol = 50.0 if track_name.startswith("A1") else 100.0
        try:
            return float(meta.get("_volume", default_vol))
        except (TypeError, ValueError):
            return default_vol

    def _get_audio_track_gain_db(self, track_name: str) -> float:
        meta = self._get_audio_track_meta(track_name)
        try:
            return float(meta.get("_gain_db", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _is_audio_track_muted(self, track_name: str) -> bool:
        meta = self._get_audio_track_meta(track_name)
        track_obj = None
        if hasattr(self, "timeline") and self.timeline._timeline:
            track_obj = next(
                (t for t in self.timeline._timeline.tracks if t.name == track_name),
                None,
            )
        if bool(meta.get("_muted", False)) or bool(getattr(track_obj, "muted", False)):
            return True
        # A soloed track is never muted by another track's solo. If
        # multiple tracks are soloed, all of them play; the rest are muted.
        if bool(meta.get("_solo", False)) or bool(getattr(track_obj, "solo", False)):
            return False
        # If any OTHER audio track is soloed, this one is muted.
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return False
        for t in self.timeline._timeline.tracks:
            if t.name == track_name:
                continue
            other_name = str(getattr(t, "name", "") or "")
            is_audio = other_name.startswith(("A1", "A2")) or other_name in ("TS1", "A2 Dub")
            if is_audio:
                if bool(getattr(t, "solo", False)) or (
                    isinstance(t.metadata, dict) and bool(t.metadata.get("_solo", False))
                ):
                    return True
        return False

    def _compute_audio_track_volume(self, track_name: str, base: float = 100.0) -> float:
        meta = self._get_audio_track_meta(track_name)
        default_base = 50.0 if track_name.startswith("A1") else base
        try:
            v = float(meta.get("_volume", default_base))
        except (TypeError, ValueError):
            v = default_base
        return max(0.0, min(200.0, v))

    def on_track_mute_toggled(self, track_name: str, is_muted: bool):
        """Handle timeline audio track mute toggling.
        Maps timeline mute to per-track mute on the dual-track player.
        """
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        for t in self.timeline._timeline.tracks:
            if t.name == track_name:
                t.muted = is_muted
                if not isinstance(t.metadata, dict):
                    t.metadata = {}
                t.metadata["_muted"] = bool(is_muted)

        muted = bool(is_muted)
        if track_name == "A1 Audio":
            self._mute_original = muted
            if hasattr(self, "media_player"):
                try:
                    self.media_player.set_mute_original(muted)
                except Exception:
                    pass
        elif track_name in ("A2 Dub", "TS1"):
            self._mute_dubbed = muted
            if hasattr(self, "media_player"):
                try:
                    # A composed dubbed sidecar may still contain Music;
                    # rebuild it with TS1 muted instead of muting the whole
                    # sidecar (which would incorrectly silence Music too).
                    if bool(self._music_audio_tracks()):
                        self.media_player.set_mute_dubbed(False)
                        self._schedule_preview_audio_refresh(force=True)
                    else:
                        self.media_player.set_mute_dubbed(muted)
                except Exception:
                    pass

        if track_name == "A2 Music":
            # Music shares the generated-audio sidecar, so its mute state is
            # reflected by rebuilding the TTS+music preview mix.  The track
            # itself remains independent in the timeline/export model.
            self._schedule_preview_audio_refresh(force=True)

        if hasattr(self, "track_label_bar"):
            self.track_label_bar.set_muted(track_name, muted)

        self.schedule_timeline_visual_refresh(waveform=True, thumbnails=False)
        self.schedule_timeline_project_persist()

    def on_track_blur_toggled(self, track_name: str, is_on: bool):
        """Handle B1 track label click - toggle blur effect."""
        if not hasattr(self, "blur_area_btn"):
            return
        self.blur_area_btn.blockSignals(True)
        self.blur_area_btn.setChecked(bool(is_on))
        self.blur_area_btn.blockSignals(False)
        try:
            self.toggle_blur_effect_enabled(bool(is_on))
        except Exception:
            pass
        self.schedule_timeline_project_persist(blur_state=True)

    def on_track_logo_toggled(self, track_name: str, is_shown: bool):
        """Handle L1 track label click - hide or show the logo overlay."""
        self._logo_track_preview_visible = bool(is_shown)
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_logo_track_visible"):
            self.video_view.set_logo_track_visible(self._logo_track_preview_visible)
        # Force the next timed refresh to respect the new track state even if
        # the playhead remains at the same timestamp.
        self._timed_layer_preview_signature = None
        self.schedule_timeline_project_persist()
        if not hasattr(self, "video_view"):
            return
        if is_shown:
            # Restore only logos active at the current playhead.  This keeps
            # Hide/Show consistent with timed logo segments.
            self.refresh_timed_layer_preview()
        else:
            # Hide the logo overlay
            if hasattr(self.video_view, "clear_logo"):
                self.video_view.clear_logo()

    def on_track_mask_toggled(self, track_name: str, is_shown: bool):
        """Handle M1 track label click - show or hide the mask filter."""
        self._mask_track_preview_visible = bool(is_shown)
        self.schedule_timeline_project_persist(mask_state=True)
        if not hasattr(self, "media_player"):
            return
        if is_shown:
            # Re-apply the M1 mask filter from the timeline.
            try:
                self._apply_mask_to_preview()
            except Exception:
                pass
            # Re-show the mask overlay. A label click should restore M1 even
            # when another layer is selected (or the selection was cleared).
            try:
                if hasattr(self, "timeline") and self.timeline._timeline:
                    sid = getattr(self.timeline, "_selected_layer_id", "")
                    for tr in self.timeline._timeline.tracks:
                        if tr.name != "M1" or not tr.layers:
                            continue
                        layer = next((item for item in tr.layers if item.id == sid), tr.layers[0])
                        self._show_mask_overlay(tr, layer)
                        return
            except Exception:
                pass
        else:
            try:
                self.media_player.clear_mask_region()
            except Exception:
                pass
            if hasattr(self, "video_view") and hasattr(self.video_view, "clear_mask_region"):
                try:
                    self.video_view.clear_mask_region()
                except Exception:
                    pass

    def on_track_text_toggled(self, track_name: str, is_shown: bool):
        """Show or hide every Text layer in the T1 track without changing export data."""
        timeline = getattr(self, "timeline", None)
        if timeline is not None and timeline._timeline:
            for track in timeline._timeline.tracks:
                if track.name == track_name:
                    self._text_track_preview_visible = bool(is_shown)
                    timeline._redraw()
                    self._refresh_text_layer_preview(getattr(timeline, "_selected_layer_id", ""))
                    if hasattr(self, "track_label_bar"):
                        self.track_label_bar.set_text_shown(track_name, bool(is_shown))
                    self.log(f"[Timeline] {'Shown' if is_shown else 'Hidden'} text track: {track_name}")
                    self.schedule_timeline_project_persist()
                    return

    def on_track_subtitle_toggled(self, track_name: str, is_shown: bool):
        """Temporarily show or hide TS1 subtitle output without deleting data."""
        self._subtitle_track_preview_visible = bool(is_shown)
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_subtitle_track_visible"):
            self.video_view.set_subtitle_track_visible(self._subtitle_track_preview_visible)
        if not is_shown:
            try:
                self.media_player.clear_subtitle()
            except Exception:
                pass
            # clear_subtitle() removes MPV's external ASS track.  Its source
            # path may still be the same on Show, so invalidate the UI-level
            # cache as well; otherwise sync_live_subtitle_preview() believes
            # the removed track is already loaded and never restores it.
            self._loaded_live_ass_path = ""
            self._loaded_live_ass_signature = None
            if hasattr(self, "video_view"):
                try:
                    self.video_view.subtitle_item.hide()
                except Exception:
                    pass
        else:
            try:
                self.sync_live_subtitle_preview()
            except Exception:
                pass
        if hasattr(self, "track_label_bar"):
            self.track_label_bar.set_subtitle_shown(track_name, bool(is_shown))
        self.log(f"[Timeline] {'Shown' if is_shown else 'Hidden'} subtitle track: {track_name}")
        self.schedule_timeline_project_persist()

    def on_track_label_selected(self, track_name: str):
        """Select the first layer in a track; label clicks never toggle state."""
        timeline = getattr(self, "timeline", None)
        if timeline is None or not timeline._timeline:
            return
        for track in timeline._timeline.tracks:
            if track.name == track_name and track.layers:
                timeline.select_layer(track.layers[0].id)
                return

    def _sync_timeline_mute_to_gui(self):
        """Pull the current timeline track mute state into the GUI and backend."""
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return
        a1_muted = False
        a2_muted = False
        music_muted = False
        for t in self.timeline._timeline.tracks:
            metadata = getattr(t, "metadata", {}) or {}
            muted = bool(getattr(t, "muted", False) or metadata.get("_muted", False))
            if t.name == "A1 Audio":
                a1_muted = muted
            elif t.name in ("A2 Dub", "TS1"):
                a2_muted = muted
            elif t.name == "A2 Music":
                music_muted = muted
        self._mute_original = a1_muted
        self._mute_dubbed = a2_muted
        if hasattr(self, "media_player"):
            try:
                self.media_player.set_mute_original(a1_muted)
            except Exception:
                pass
            try:
                # The dubbed sidecar may contain an independent Music Layer.
                # A muted TTS track must not mute that whole sidecar; the
                # compositor removes TTS while keeping Music audible.
                self.media_player.set_mute_dubbed(
                    False if self._music_audio_tracks() else a2_muted
                )
            except Exception:
                pass
        if hasattr(self, "track_label_bar"):
            self.track_label_bar.set_muted("A1 Audio", a1_muted)
            self.track_label_bar.set_muted("TS1", a2_muted)
            self.track_label_bar.set_muted("A2 Music", music_muted)

    def _is_active_timeline_audio_track_muted(self) -> bool:
        track_mutes = self._timeline_audio_track_mutes()
        if not track_mutes:
            return False
        a1_muted, a2_muted = track_mutes
        mode = str(getattr(self, "_preview_audio_track_mode", "original") or "original").strip().lower()
        if mode != "dubbed":
            return a1_muted
        dubbed_audio_kind, _dubbed_path = self._resolve_preview_dubbed_playback_source()
        if dubbed_audio_kind == "voice":
            return a2_muted
        if dubbed_audio_kind == "mixed":
            # The mixed sidecar contains TTS + Music. A1 is loaded as its
            # own sidecar, so only the TTS-side mute controls this stream.
            return a2_muted
        return a1_muted

    def on_add_timeline_layer(self, layer_type: str = "subtitle"):
        if not hasattr(self, "timeline"):
            return
        if self._preview_is_playing():
            return

        if layer_type in {"blur", "logo", "mask", "text", "image", "sticker"} and not bool(
            getattr(self, "_optional_layer_controls_ready", False)
        ):
            QMessageBox.information(
                self,
                "Generate Video First",
                "Complete video generation before adding Blur, Logo, Mask, Text, or other overlay layers.",
            )
            return

        tl = self.timeline._timeline
        if not tl:
            return

        from app.layers.base import LayerType
        from app.layers.sync_bridge import find_or_create_track

        if layer_type == "subtitle":
            # TS1 is driven from the segment lists, not the legacy Subtitle
            # track. Insert at the playhead so the normal timeline, preview,
            # editor, export and project persistence paths all stay aligned.
            try:
                start = max(0.0, float(self.media_player.position()) / 1000.0)
            except Exception:
                start = 0.0
            duration = max(0.0, float(getattr(tl, "duration", 0.0) or 0.0))
            end = start + 2.0
            if duration > 0.0:
                end = min(end, duration)
                if end - start < 0.20:
                    start = max(0.0, end - 2.0)
            if end - start < 0.05:
                end = start + 2.0

            translated_exists = bool(getattr(self, "current_translated_segments", None))
            if not hasattr(self, "current_segments") or self.current_segments is None:
                self.current_segments = []
            if not hasattr(self, "current_translated_segments") or self.current_translated_segments is None:
                self.current_translated_segments = []

            active_segments = self.current_translated_segments if translated_exists else self.current_segments
            index = next(
                (idx for idx, segment in enumerate(active_segments)
                 if float(segment.get("start", 0.0) or 0.0) > start),
                len(active_segments),
            )
            source_segment = {"start": start, "end": end, "text": "New subtitle", "words": []}
            translated_segment = {
                "start": start,
                "end": end,
                "text": "New subtitle",
                "source_text": "New subtitle",
                "tts_text": "New subtitle",
                "provider": "manual",
            }
            history_entry = {
                "type": "insert",
                "index": int(index),
                "selected_before": int(getattr(self, "_selected_segment_index", -1)),
                "selected_after": int(index),
                "current_before": [],
                "current_after": [copy.deepcopy(source_segment)],
                "translated_before": [],
                "translated_after": [copy.deepcopy(translated_segment)] if translated_exists else [],
            }
            self.current_segments.insert(min(index, len(self.current_segments)), source_segment)
            if translated_exists:
                self.current_translated_segments.insert(min(index, len(self.current_translated_segments)), translated_segment)
            # Segment insertion changes the source index mapping used by the
            # optional one-line display cache, so always rebuild it from the
            # current project data.
            self._single_line_split_cache = None
            self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
            if translated_exists:
                self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
                self._sync_hidden_translated_text_from_segments()
            self._sync_hidden_transcript_text_from_segments()
            self._timeline_timing_undo_stack.append(history_entry)
            self._timeline_timing_redo_stack = []
            if len(self._timeline_timing_undo_stack) > 100:
                self._timeline_timing_undo_stack = self._timeline_timing_undo_stack[-100:]
            self._refresh_timeline_history_buttons()
            self.set_selected_segment_index(index, sync_ui=True)
            self.timeline.set_active_segment_index(index)
            self.apply_segments_to_timeline()
            self.persist_current_timeline_project_data()
            self.schedule_live_subtitle_preview_refresh()
            self.refresh_ui_state()
            self.show_subtitle_inspector_details()
            self.log(f"[Subtitle] Added manual TS1 segment at {start:.2f}s.")
            return

        elif layer_type == "text":
            from app.layers.text import TextLayer
            text_track = find_or_create_track(tl, "T1 Text", LayerType.TEXT, 80)
            idx = len(text_track.layers)
            layer = TextLayer(
                name=f"Text {idx + 1}",
                text="New text layer",
                start=0.0,
                end=tl.duration if tl.duration > 0 else 10.0,
            )
            layer.font_size = 60
            # Match the subtitle defaults so identical Text/Subtitles size
            # values use the same family and weight out of the box.
            layer.font_name = "Segoe UI"
            layer.font_bold = True
            layer.transform.x = 0.5
            layer.transform.y = 0.5
            layer.z_index = idx
            text_track.layers.append(layer)
            if hasattr(self.timeline, "_track_heights"):
                self.timeline._track_heights[text_track.id] = text_track.height or 80
            self.timeline._redraw()
            self.timeline._selected_layer_id = layer.id
            self._show_text_inspector_for_track(text_track, layer)
            self._refresh_text_layer_preview(layer.id)

        elif layer_type == "image":
            from app.layers.image import ImageLayer
            img_track = find_or_create_track(tl, "I1 Image", LayerType.IMAGE, 80)
            idx = len(img_track.layers)
            layer = ImageLayer(
                name=f"Image {idx + 1}",
                source="",
                start=0.0,
                end=min(tl.duration, 10.0) if tl.duration > 0 else 10.0,
            )
            layer.z_index = idx
            img_track.layers.append(layer)
            self.timeline._redraw()

        elif layer_type == "logo":
            from app.layers.image import ImageLayer
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Logo / Watermark Image", "",
                "Images (*.png *.jpg *.jpeg *.bmp *.gif *.svg);;All Files (*)"
            )
            if not path:
                return
            img_track = find_or_create_track(tl, "L1 Logo", LayerType.IMAGE, 80)
            # L1 supports multiple independent logo layers.  Keep existing
            # layers intact; selecting a timeline layer determines which
            # logo is currently editable in the preview overlay.
            idx = len(img_track.layers)
            dur = tl.duration if tl.duration > 0 else 10.0
            layer = ImageLayer(
                name=f"Logo {idx + 1}",
                source=path,
                start=0.0,
                end=dur,
            )
            layer.z_index = idx
            # Mark as watermark so the preview positions it correctly
            layer.metadata["_is_watermark"] = True
            img_track.layers.append(layer)
            # Register the new track's height in the timeline so it gets
            # a real draw slot.
            if hasattr(self.timeline, "_track_heights"):
                self.timeline._track_heights[img_track.id] = (
                    img_track.height or 80
                )
            self.timeline._redraw()
            # Show the logo overlay immediately (no need to click the
            # layer first) and persist the logo state.
            try:
                self._show_logo_overlay(img_track, layer)
            except Exception:
                pass

        elif layer_type == "blur":
            from app.layers.blur import BlurLayer
            blur_track = find_or_create_track(tl, "B1", LayerType.BLUR, 60)
            # Register the new track's height in the timeline so it gets a
            # real draw slot (otherwise the track silently uses the default
            # height and may not be visible).
            if hasattr(self.timeline, "_track_heights"):
                self.timeline._track_heights[blur_track.id] = (
                    blur_track.height or 60
                )
            idx = len(blur_track.layers)
            # Stagger each new blur layer slightly so all layers are
            # visible in the timeline (otherwise overlapping layers at
            # the same position hide each other).
            stagger = idx % 4
            base_y = 0.75 - stagger * 0.06
            base_x = 0.30 + (stagger % 2) * 0.08
            # New Blur layers are global by default.  Their geometry can be
            # narrowed later in the inspector/timeline, but creating one must
            # not unexpectedly limit it to a five-second window at the
            # current playhead.
            blur_start = 0.0
            blur_end = float(tl.duration) if float(getattr(tl, "duration", 0.0) or 0.0) > 0.0 else 10.0
            layer = BlurLayer(
                name=f"Blur {idx + 1}",
                position_x=float(base_x),
                position_y=float(base_y),
                width=0.4,
                height=0.1,
                blur_strength=20.0,
                start=blur_start,
                end=blur_end,
            )
            layer.z_index = idx
            blur_track.layers.append(layer)
            # Force a redraw so the new track + layer are visible.
            self.timeline._redraw()
            # Auto-scroll the timeline vertically so the new B1
            # track is in view (it sits below V1 + A1 by default).
            try:
                if hasattr(self.timeline, "verticalScrollBar"):
                    y_offset = 0
                    if hasattr(self.timeline, "RULER_HEIGHT"):
                        y_offset = int(self.timeline.RULER_HEIGHT)
                    for tr in tl.tracks:
                        if tr.id == blur_track.id:
                            break
                        y_offset += int(
                            self.timeline._track_heights.get(
                                tr.id, self.timeline.TRACK_DEFAULT_H
                            )
                        )
                    bar = self.timeline.verticalScrollBar()
                    # Make sure the scroll bar range reflects the new scene
                    # size (it is normally auto-sized by the QGraphicsView,
                    # but the range can lag on first update).
                    viewport_h = int(self.timeline.viewport().height())
                    scene_h = int(self.timeline._scene.height())
                    bar.setRange(0, max(0, scene_h - viewport_h))
                    # Center the B1 track in the viewport
                    target = max(0, y_offset - max(0, (viewport_h - 80) // 2))
                    bar.setValue(target)
                    # Make sure the new layer is fully visible too.
                    self.timeline.ensureVisible(
                        0,
                        y_offset,
                        1,
                        int(self.timeline._track_heights.get(
                            blur_track.id, 60
                        )),
                    )
            except Exception:
                pass
            # Auto-enable the blur effect so the visual blur shows on the
            # video preview the moment the layer is added.
            if hasattr(self, "blur_area_btn"):
                self.blur_area_btn.blockSignals(True)
                self.blur_area_btn.setChecked(True)
                self.blur_area_btn.blockSignals(False)
            # Push the new region's normalized data into the video view
            # and force the mpv vf filter to be applied immediately.
            try:
                regions = []
                for ll in blur_track.layers:
                    if not getattr(ll, "visible", True):
                        continue
                    regions.append({
                        "x": float(getattr(ll, "position_x", 0.3)),
                        "y": float(getattr(ll, "position_y", 0.8)),
                        "width": float(getattr(ll, "width", 0.4)),
                        "height": float(getattr(ll, "height", 0.1)),
                        "blur_strength": float(getattr(ll, "blur_strength", 20.0)),
                    })
                if hasattr(self.video_view, "set_blur_regions_normalized"):
                    self.video_view.set_blur_regions_normalized(regions)
                # The Add Layer menu bypasses the legacy Blur button, so it
                # must explicitly enable the editable overlay.  Merely
                # checking blur_area_btn does not emit its toggled signal.
                if hasattr(self.video_view, "set_blur_edit_enabled"):
                    self.video_view.set_blur_edit_enabled(True)
                if hasattr(self.video_view, "set_blur_active_index"):
                    self.video_view.set_blur_active_index(idx)
                if hasattr(self, "apply_preview_blur_region"):
                    self.apply_preview_blur_region(force=True)
            except Exception:
                pass
            # Persist the new region(s) to the project state so they
            # survive a close/reopen. Without this, the blur_state is
            # only saved on the legacy blur add/edit handlers, and a
            # region added via the new "Blur" button would be lost.
            try:
                if hasattr(self, "persist_project_blur_state"):
                    self.persist_project_blur_state()
            except Exception:
                pass
            try:
                self.timeline._selected_layer_id = layer.id
                # Route newly-created layers through the same paused
                # selection path as existing B1 layers. This starts the
                # deferred edit session and removes only this layer's
                # rendered effect while its geometry is edited.
                self.on_timeline_layer_selected(layer.id)
            except Exception:
                pass

        elif layer_type == "mask":
            from app.layers.mask import MaskLayer
            mask_track = find_or_create_track(tl, "M1", LayerType.MASK, 60)
            if hasattr(self.timeline, "_track_heights"):
                self.timeline._track_heights[mask_track.id] = (
                    mask_track.height or 60
                )
            idx = len(mask_track.layers)
            # Offset new regions slightly so their draggable overlays do
            # not start perfectly on top of an existing mask.
            stagger = idx % 4
            layer = MaskLayer(
                name=f"Mask {idx + 1}",
                position_x=0.3 + (stagger % 2) * 0.08,
                position_y=0.4 + (stagger // 2) * 0.08,
                width=0.4,
                height=0.2,
                color="#000000",
                mode="solid",
                pixelate_size=12,
                blur_strength=20,
                start=0.0,
                # Span the full timeline so the mask track is visible
                # across the whole video (like the audio track layers),
                # not a short 5-second segment.
                end=tl.duration if tl.duration > 0 else 5.0,
            )
            layer.z_index = idx
            # Visibility is gated by the play state in
            # _apply_mask_to_preview: the mask filter is only pushed
            # to mpv while the video is playing, so a freshly added
            # mask does not draw on the paused preview.
            mask_track.layers.append(layer)
            self.timeline._redraw()
            # Push the new mask into the mpv filter chain and persist
            # it so the export matches the preview.
            try:
                self._apply_mask_to_preview()
            except Exception:
                pass
            try:
                if hasattr(self, "persist_project_mask_state"):
                    self.persist_project_mask_state()
            except Exception:
                pass
            # Select the new mask layer so the inspector opens with
            # the right settings loaded.
            try:
                self.timeline._selected_layer_id = layer.id
                self.timeline._redraw()
                # Use the normal paused-layer selection path so the new M1
                # layer gets the same deferred effect/edit-handle behavior
                # as a layer selected after reopening a project.
                self.on_timeline_layer_selected(layer.id)
            except Exception:
                pass
        
        # Save timeline data (includes mask and logo layers)
        try:
            self.persist_current_timeline_project_data()
        except Exception:
            pass

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

        # A derived single-line cache is indexed against the pre-edit
        # subtitle list.  Reusing it after a delete can redraw a segment
        # which has just been removed, especially when a new segment is
        # inserted at the same point in the timeline.
        self._single_line_split_cache = None

        self._timeline_timing_undo_stack.append(history_entry)
        self._timeline_timing_redo_stack = []
        if len(self._timeline_timing_undo_stack) > 100:
            self._timeline_timing_undo_stack = self._timeline_timing_undo_stack[-100:]
        self._refresh_timeline_history_buttons()

        self.set_selected_segment_index(index, sync_ui=True)
        if hasattr(self, "timeline"):
            self.timeline.set_active_segment_index(index)
        self.apply_segments_to_timeline()
        # Write the edited lists even if a deletion leaves one of them
        # empty.  The generic persistence method deliberately avoids empty
        # lists during initial project setup, so explicitly replace the
        # existing segment artifacts here to prevent a deleted segment from
        # being restored from disk or project cache later in this session.
        state = getattr(self, "current_project_state", None)
        if state is not None:
            try:
                self.current_segment_models = self.project_bridge.persist_transcription(
                    state, self.current_segments or [], self.last_original_srt_path
                )
                if self.current_translated_segments is not None:
                    self.current_translated_segment_models = self.project_bridge.persist_translation(
                        state,
                        self.current_segment_models,
                        self.current_translated_segments or [],
                        self.last_translated_srt_path,
                    )
            except Exception as exc:
                self.log(f"[Subtitle] Could not update deleted segment cache: {exc}")
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
        if self._preview_is_playing():
            return
        selection = getattr(self.timeline, "selection_range", lambda: None)() if hasattr(self, "timeline") else None
        # A selected overlay always owns Split. The selection supplies cut
        # times only; it must never redirect the command to TS1.
        if self._split_selected_overlay_layer(selection):
            return
        if selection:
            start, end = selection
            if self._split_selected_subtitle_by_range(start, end):
                return
        # Overlay layers use the same Split action as subtitle/audio blocks.
        # Copying the layer preserves its style, transform and visibility;
        # only its identity and timing are changed.
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

    def _sync_preview_framing_to_player(self):
        """Keep native MPV crop framing consistent with the preview canvas."""
        view = getattr(self, "video_view", None)
        player = getattr(self, "media_player", None)
        if view is None or player is None or not hasattr(player, "set_preview_framing"):
            return
        try:
            source_w = float(getattr(view, "video_source_width", 0) or 0)
            source_h = float(getattr(view, "video_source_height", 0) or 0)
            canvas = view.get_preview_canvas_rect()
            source_ratio = source_w / source_h if source_w > 0 and source_h > 0 else 0.0
            canvas_ratio = canvas.width() / canvas.height() if canvas.height() > 0 else source_ratio
            focus_x, focus_y = self.get_output_fill_focus()
            player.set_preview_framing(
                source_ratio,
                canvas_ratio,
                self.get_output_scale_mode_key(),
                focus_x,
                focus_y,
            )
        except Exception as exc:
            self.log(f"[Preview] Could not sync canvas framing: {exc}")

    def _persist_video_filter_settings(self):
        """Persist realtime filter controls without requiring an Apply action."""
        try:
            self.save_user_settings()
        except Exception as exc:
            self.log(f"[Filter] Could not persist filter settings: {exc}")

    def transcribe_selected_range_alternate(self):
        timeline = getattr(self, "timeline", None)
        selection = timeline.selection_range() if timeline else None
        if not selection:
            QMessageBox.information(self, "Transcribe Selected Range", "Please create a Selection Range first.")
            return
        if getattr(self, "_alternate_range_transcription_worker", None) is not None:
            return
        video_path = self.video_path_edit.text().strip()
        if not video_path or not os.path.isfile(video_path):
            QMessageBox.warning(self, "Transcribe Selected Range", "Please load a video first.")
            return
        pending = getattr(self, "_alternate_ocr_range_pending", None)
        pending_overlay = getattr(self, "ocr_region_overlay", None)
        # A hidden range-OCR editor is not actionable. It can happen after a
        # window switch or an older pending state, and must not bypass the
        # configuration dialog on the next Alt Transcribe click.
        if pending and (pending_overlay is None or not pending_overlay.isVisible()):
            self.log("[Range OCR] Cleared a stale pending OCR region request.")
            self._alternate_ocr_range_pending = None
            pending = None
            self._update_alt_transcribe_button_label()
        if pending:
            config = dict(pending)
            self._alternate_ocr_range_pending = None
            overlay = getattr(self, "ocr_region_overlay", None)
            if overlay is not None:
                overlay.set_editable(False)
                overlay.hide()
            self._update_alt_transcribe_button_label()
        else:
            config = self._show_range_transcription_dialog(selection)
            if config is None:
                return

        start, end = float(config["start"]), float(config["end"])
        engine_name = str(config["engine"])
        mode = str(config["mode"])
        if engine_name == "whisper" and not self.ensure_required_resources("Range Transcription", include_whisper=True):
            return
        if engine_name == "ocr" and not self.ensure_required_resources("Range Transcription", include_ocr=True):
            return
        if engine_name == "ocr" and not pending:
            overlay = getattr(self, "ocr_region_overlay", None)
            if overlay is None:
                QMessageBox.warning(self, "Range OCR", "The OCR region editor is unavailable.")
                return
            self._alternate_ocr_range_pending = dict(config)
            overlay._requested_visible = True
            overlay.set_editable(True)

            def _show_range_ocr_editor():
                view = getattr(overlay, "_target_view", None)
                if view is None:
                    return
                overlay.setGeometry(QRect(view.mapToGlobal(QPoint(0, 0)), view.size()))
                overlay.show()
                overlay.raise_()
                overlay.update()

            _show_range_ocr_editor()
            QTimer.singleShot(0, _show_range_ocr_editor)
            self._update_alt_transcribe_button_label()
            self.log(
                f"[Range OCR] Region editor opened for {start:.3f}s–{end:.3f}s; "
                f"fps={config['ocr_fps'] or 'Settings default'}. Adjust it, then click Run OCR."
            )
            return

        model = str(config.get("whisper_model", "")) if engine_name == "whisper" else ""
        language = str(config.get("language", "auto"))
        ocr_fps = config.get("ocr_fps") if engine_name == "ocr" else None
        ocr_region = str(config.get("ocr_region", "bottom"))
        settings_summary = (
            f"model={model}, language={language}" if engine_name == "whisper"
            else f"region={ocr_region}, fps={ocr_fps or 'Settings default'}"
        )
        self.log(
            f"[Range Transcription] Running {engine_name} for {start:.3f}s–{end:.3f}s "
            f"({mode}; {settings_summary})."
        )
        worker = AlternateRangeTranscriptionWorker(
            video_path, start, end, engine_name, model, language,
            ocr_region=ocr_region, ocr_fps=ocr_fps,
        )
        # Keep the QThread parented and referenced until its native finished
        # signal fires.  Clearing the only reference from the worker's custom
        # result signal could destroy the QThread while run() was unwinding.
        worker.setParent(self)
        self._alternate_range_transcription_worker = worker
        action_button = getattr(self, "timeline_alt_transcribe_btn", None)
        if action_button is not None:
            action_button.setEnabled(False)
            action_button.setText("Running…")
        def finished(segments, error):
            if action_button is not None:
                action_button.setEnabled(True)
                self._update_alt_transcribe_button_label()
            if error:
                QMessageBox.warning(self, "Transcribe Selected Range", f"{engine_name.title()} failed.\n\n{error}")
                return
            if not segments:
                QMessageBox.information(
                    self, "Transcribe Selected Range",
                    "No subtitle text was detected in this range. Existing subtitle segments were not changed.",
                )
                return
            self._apply_alternate_range_transcript(segments, start, end, mode)
        def cleanup_worker():
            if getattr(self, "_alternate_range_transcription_worker", None) is worker:
                self._alternate_range_transcription_worker = None
            # finished() runs while the worker reference is still retained,
            # so its label refresh intentionally does nothing. Refresh once
            # the native QThread has actually finished and been released.
            self._update_alt_transcribe_button_label()
            worker.deleteLater()
        worker.completed.connect(finished)
        worker.finished.connect(cleanup_worker)
        worker.start()

    def _show_range_transcription_dialog(self, selection):
        """Collect recognition options without changing the project's main source."""
        start, end = (float(selection[0]), float(selection[1]))
        overlaps = any(
            float(seg.get("end", 0.0)) > start and float(seg.get("start", 0.0)) < end
            for seg in list(self.current_segments or [])
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("Transcribe Selected Range")
        dialog.setMinimumWidth(420)
        dialog.setStyleSheet(
            "QDialog { background: #101b2d; color: #e6eef9; } "
            "QLabel { color: #d7e4f5; } QComboBox { min-height: 28px; }"
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(QLabel(f"Range: {start:.3f}s – {end:.3f}s", dialog))

        engine_label = QLabel("Engine", dialog)
        engine_combo = QComboBox(dialog)
        engine_combo.addItem("Whisper", "whisper")
        engine_combo.addItem("OCR", "ocr")
        default_engine = self._alternate_transcription_engine()
        engine_index = engine_combo.findData(default_engine)
        engine_combo.setCurrentIndex(engine_index if engine_index >= 0 else 0)
        layout.addWidget(engine_label)
        layout.addWidget(engine_combo)

        mode_label = QLabel("Existing subtitle segments", dialog)
        mode_combo = QComboBox(dialog)
        mode_combo.addItem("Replace overlapping segments (recommended)", "replace")
        mode_combo.addItem("Append new segments", "append")
        mode_combo.setCurrentIndex(0 if overlaps else 1)
        layout.addWidget(mode_label)
        layout.addWidget(mode_combo)

        whisper_box = QWidget(dialog)
        whisper_layout = QVBoxLayout(whisper_box)
        whisper_layout.setContentsMargins(0, 0, 0, 0)
        whisper_layout.setSpacing(6)
        whisper_layout.addWidget(QLabel("Whisper model", whisper_box))
        whisper_model_combo = QComboBox(whisper_box)
        whisper_model_combo.addItem("Base", "base")
        whisper_model_combo.addItem("Small (Fast)", "small")
        if os.environ.get("CAPCAP_DEVICE", "cuda").strip().lower() == "cuda":
            whisper_model_combo.addItem("Medium (Quality)", "medium")
        current_model = str(self.get_whisper_model_name() or "small").strip().lower()
        model_index = whisper_model_combo.findData(current_model)
        whisper_model_combo.setCurrentIndex(model_index if model_index >= 0 else 0)
        whisper_layout.addWidget(whisper_model_combo)
        whisper_layout.addWidget(QLabel("Language", whisper_box))
        language_combo = QComboBox(whisper_box)
        source_language = str(self.get_source_language_code() or "auto")
        language_combo.addItem(f"Project language ({source_language})", source_language)
        if source_language != "auto":
            language_combo.addItem("Auto detect", "auto")
        for label, code in (("Chinese", "zh"), ("English", "en"), ("Vietnamese", "vi"), ("Japanese", "ja"), ("Korean", "ko")):
            if code != source_language:
                language_combo.addItem(label, code)
        whisper_layout.addWidget(language_combo)
        layout.addWidget(whisper_box)

        ocr_box = QWidget(dialog)
        ocr_layout = QVBoxLayout(ocr_box)
        ocr_layout.setContentsMargins(0, 0, 0, 0)
        ocr_layout.setSpacing(6)
        ocr_layout.addWidget(QLabel("OCR sampling rate", ocr_box))
        ocr_fps_combo = QComboBox(ocr_box)
        ocr_fps_combo.addItem("Use Settings default", "settings")
        ocr_fps_combo.addItem("1 FPS (lighter)", "1")
        ocr_fps_combo.addItem("1.5 FPS", "1.5")
        ocr_fps_combo.addItem("2 FPS", "2")
        ocr_fps_combo.addItem("3 FPS", "3")
        ocr_fps_combo.addItem("4 FPS (short flashes)", "4")
        current_fps = str(os.getenv("OCR_SAMPLING_FPS") or "auto").strip().lower()
        fps_index = ocr_fps_combo.findData(current_fps)
        ocr_fps_combo.setCurrentIndex(fps_index if fps_index >= 0 else 0)
        ocr_layout.addWidget(ocr_fps_combo)
        ocr_hint = QLabel("After continuing, adjust the current OCR region on the preview, then click Run OCR.", ocr_box)
        ocr_hint.setWordWrap(True)
        ocr_hint.setObjectName("helperLabel")
        ocr_layout.addWidget(ocr_hint)
        layout.addWidget(ocr_box)

        def update_engine_options():
            is_whisper = engine_combo.currentData() == "whisper"
            whisper_box.setVisible(is_whisper)
            ocr_box.setVisible(not is_whisper)
            dialog.adjustSize()

        engine_combo.currentIndexChanged.connect(update_engine_options)
        update_engine_options()

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_button = QPushButton("Cancel", dialog)
        run_button = QPushButton("Continue", dialog)
        buttons.addWidget(cancel_button)
        buttons.addWidget(run_button)
        layout.addLayout(buttons)
        cancel_button.clicked.connect(dialog.reject)
        run_button.clicked.connect(dialog.accept)
        if dialog.exec() != QDialog.Accepted:
            return None

        engine_name = str(engine_combo.currentData() or "whisper")
        fps_value = str(ocr_fps_combo.currentData() or "settings")
        return {
            "start": start,
            "end": end,
            "engine": engine_name,
            "mode": str(mode_combo.currentData() or "replace"),
            "whisper_model": str(whisper_model_combo.currentData() or "small"),
            "language": str(language_combo.currentData() or "auto"),
            "ocr_region": str(os.getenv("OCR_SUBTITLE_REGION") or "bottom").strip().lower(),
            "ocr_fps": None if fps_value == "settings" else float(fps_value),
        }

    def _apply_alternate_range_transcript(self, segments, start, end, mode):
        fresh = [
            {"start": max(float(start), float(seg.get("start", start))), "end": min(float(end), float(seg.get("end", end))), "text": str(seg.get("text", "")).strip()}
            for seg in list(segments or []) if str(seg.get("text", "")).strip()
        ]
        if mode == "replace":
            self.current_segments = self._replace_subtitle_segments_in_range(
                self.current_segments, fresh, start, end,
            )
            if self.current_translated_segments:
                self.current_translated_segments = self._replace_subtitle_segments_in_range(
                    self.current_translated_segments, [dict(seg) for seg in fresh], start, end,
                )
        else:
            self.current_segments = sorted(self.current_segments + fresh, key=lambda seg: float(seg.get("start", 0.0)))
            if self.current_translated_segments:
                self.current_translated_segments = sorted(
                    self.current_translated_segments + [dict(seg) for seg in fresh],
                    key=lambda seg: float(seg.get("start", 0.0)),
                )
        self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
        self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
        self._sync_hidden_transcript_text_from_segments()
        self._sync_hidden_translated_text_from_segments()
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()
        self.log(f"[Range Transcription] Added {len(fresh)} alternate-engine segment(s).")

    @staticmethod
    def _replace_subtitle_segments_in_range(existing, replacement, start, end):
        """Replace only the selected interval, retaining cue portions outside it."""
        retained = []
        for source in list(existing or []):
            segment = dict(source)
            segment_start = float(segment.get("start", 0.0))
            segment_end = float(segment.get("end", 0.0))
            overlaps = segment_end > start and segment_start < end
            if not overlaps:
                retained.append(segment)
                continue
            # A cue can cross either selection boundary. Keep the unaffected
            # temporal portion instead of deleting the entire cue.
            if segment_start < start:
                before = dict(segment)
                before["end"] = float(start)
                if float(before["end"]) > float(before["start"]):
                    retained.append(before)
            if segment_end > end:
                after = dict(segment)
                after["start"] = float(end)
                if float(after["end"]) > float(after["start"]):
                    retained.append(after)
        return sorted(retained + [dict(item) for item in list(replacement or [])], key=lambda seg: float(seg.get("start", 0.0)))

    def _split_selected_subtitle_by_range(self, range_start: float, range_end: float) -> bool:
        """Split only the selected TS1 cue at the selection boundaries."""
        selected_id = str(getattr(getattr(self, "timeline", None), "_selected_layer_id", "") or "")
        if selected_id:
            for track in self.timeline._timeline.tracks:
                if any(layer.id == selected_id for layer in track.layers) and bool(getattr(track, "locked", False)):
                    QMessageBox.information(self, "Layer Locked", "Unlock this timeline layer before splitting it.")
                    return True
        index = int(getattr(self, "_selected_segment_index", -1))
        if not (0 <= index < len(self.get_active_segments() or [])):
            QMessageBox.information(self, "Split by Selection", "Select a subtitle segment before splitting it.")
            return True
        boundaries = (float(range_start), float(range_end))
        history = {"type": "range_split", "current_before": copy.deepcopy(self.current_segments), "translated_before": copy.deepcopy(self.current_translated_segments)}
        changed = False
        for attr, translated in (("current_segments", False), ("current_translated_segments", True)):
            source = list(getattr(self, attr, []) or [])
            if not (0 <= index < len(source)):
                continue
            rebuilt = list(source)
            pieces = [source[index]]
            for boundary in boundaries:
                next_pieces = []
                for piece in pieces:
                    start = float(piece.get("start", 0.0)); end = float(piece.get("end", 0.0))
                    if start + 0.01 < boundary < end - 0.01:
                        first, second = self._build_split_segment_pair(piece, boundary)
                        next_pieces.extend((first, second)); changed = True
                    else:
                        next_pieces.append(piece)
                pieces = next_pieces
            if changed:
                rebuilt[index:index + 1] = pieces
                setattr(self, attr, rebuilt)
                if translated:
                    self.current_translated_segment_models = self._dict_segments_to_models(rebuilt, translated=True)
                    self._sync_hidden_translated_text_from_segments()
                else:
                    self.current_segment_models = self._dict_segments_to_models(rebuilt, translated=False)
                    self._sync_hidden_transcript_text_from_segments()
        if not changed:
            QMessageBox.information(self, "Split by Selection", "No subtitle segment crosses the selection boundaries.")
            return False
        history["current_after"] = copy.deepcopy(self.current_segments)
        history["translated_after"] = copy.deepcopy(self.current_translated_segments)
        self._timeline_timing_undo_stack.append(history)
        self._timeline_timing_redo_stack = []
        self._refresh_timeline_history_buttons()
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()
        self.log(f"[Timeline] Split subtitle segments at selection {range_start:.3f}s–{range_end:.3f}s.")
        return True

    def _split_selected_overlay_layer(self, selection=None) -> bool:
        """Split a selected Blur, Logo, Mask, or Text layer at the playhead."""
        timeline = getattr(self, "timeline", None)
        if timeline is None or not getattr(timeline, "_timeline", None):
            return False
        selected_id = str(getattr(timeline, "_selected_layer_id", "") or "")
        if not selected_id:
            return False
        selected_track = selected_layer = None
        for track in timeline._timeline.tracks:
            for layer in track.layers:
                if layer.id == selected_id:
                    selected_track, selected_layer = track, layer
                    break
            if selected_layer is not None:
                break
        if selected_layer is None:
            return False
        if bool(getattr(selected_track, "locked", False)):
            QMessageBox.information(self, "Layer Locked", "Unlock this timeline layer before splitting it.")
            return True
        if bool(getattr(selected_layer, "locked", False)):
            QMessageBox.information(self, "Layer Locked", "Unlock this layer before splitting it.")
            return True
        layer_type = str(getattr(getattr(selected_layer, "type", ""), "value", getattr(selected_layer, "type", ""))).lower()
        is_logo = layer_type == "image" and str(getattr(selected_track, "name", "")) == "L1 Logo"
        if layer_type not in {"blur", "mask", "text"} and not is_logo:
            return False
        split_times = list(selection or (float(self.media_player.position()) / 1000.0,))
        start, end = float(selected_layer.start), float(selected_layer.end)
        min_duration = max(0.1, float(getattr(timeline, "MIN_DUR", 0.1)))
        split_times = sorted({float(t) for t in split_times if start + min_duration < float(t) < end - min_duration})
        if not split_times:
            QMessageBox.information(
                self,
                "Split Layer",
                "Place the playhead or selection boundary inside the selected layer before splitting.",
            )
            return True
        index = selected_track.layers.index(selected_layer)
        before_layers = copy.deepcopy(selected_track.layers)
        pieces = []
        piece_start = start
        for part_index, split_time in enumerate(split_times + [end]):
            piece = copy.deepcopy(selected_layer)
            piece.id = selected_layer.id if part_index == 0 else uuid4().hex[:12]
            piece.name = str(getattr(selected_layer, "name", "Layer") or "Layer") if part_index == 0 else f"{str(getattr(selected_layer, 'name', 'Layer') or 'Layer')} {part_index + 1}"
            piece.start, piece.end = piece_start, split_time
            piece_start = split_time
            pieces.append(piece)
        selected_track.layers[index:index + 1] = pieces
        new_layer = pieces[-1]
        timeline._selected_layer_id = new_layer.id
        self._timeline_timing_undo_stack.append({"type": "overlay_split", "track_id": selected_track.id, "before_layers": before_layers, "after_layers": copy.deepcopy(selected_track.layers)})
        self._timeline_timing_redo_stack = []
        self._refresh_timeline_history_buttons()
        timeline._redraw()
        self.persist_current_timeline_project_data()
        self.on_timeline_layer_selected(new_layer.id)
        self.refresh_ui_state()
        return True

    def populate_timeline_layers_menu(self):
        """Build the Layers menu without touching project/preview visibility."""
        menu = getattr(self, "timeline_layers_menu", None)
        timeline = getattr(self, "timeline", None)
        if menu is None:
            return
        menu.clear()
        if timeline is None or not timeline._timeline:
            empty = menu.addAction("No layers")
            empty.setEnabled(False)
            return
        has_tracks = False
        for track in timeline._timeline.tracks:
            # Do not show empty/default tracks. The menu reflects only
            # tracks that currently contain project layers.
            if not track.layers:
                continue
            has_tracks = True
            action = menu.addAction(str(track.name or "Layer Track"))
            action.setCheckable(True)
            action.setChecked(timeline.is_track_shown_on_timeline(track))
            action.setToolTip("Only changes whether this entire track is displayed on the timeline.")
            action.toggled.connect(
                lambda shown, track_id=track.id: timeline.set_track_shown_on_timeline(track_id, shown)
            )
        if not has_tracks:
            empty = menu.addAction("No layer tracks")
            empty.setEnabled(False)

    def on_timeline_track_lock_toggled(self, track_name: str, locked: bool):
        timeline = getattr(self, "timeline", None)
        if timeline is None or not timeline._timeline:
            return
        for track in timeline._timeline.tracks:
            if track.name == track_name:
                track.locked = bool(locked)
                timeline._redraw()
                self.persist_current_timeline_project_data()
                self.refresh_ui_state()
                self.log(f"[Timeline] {'Locked' if locked else 'Unlocked'} track: {track.name}")
                return

    def toggle_selected_timeline_layer_lock(self, layer_id: str = ""):
        timeline = getattr(self, "timeline", None)
        selected_id = str(layer_id or getattr(timeline, "_selected_layer_id", "") or "") if timeline else ""
        if not timeline or not timeline._timeline or not selected_id:
            return
        for track in timeline._timeline.tracks:
            for layer in track.layers:
                if layer.id == selected_id:
                    layer.locked = not bool(getattr(layer, "locked", False))
                    timeline._redraw()
                    self.persist_current_timeline_project_data()
                    self.log(f"[Timeline] {'Locked' if layer.locked else 'Unlocked'} layer: {layer.name or layer.id}")
                    return

    def delete_selected_timeline_segment(self):
        if self._preview_is_playing():
            return
        # If a layer is currently selected in the timeline, remove it
        # from its track. Handles blur (with overlay sync), image/logo,
        # text, and any other layer type.
        if hasattr(self, "timeline") and self.timeline._timeline:
            selected_id = str(getattr(self.timeline, "_selected_layer_id", "") or "")
            if selected_id:
                for track in self.timeline._timeline.tracks:
                    layer = None
                    layer_idx = -1
                    for li, l in enumerate(track.layers):
                        if l.id == selected_id:
                            layer = l
                            layer_idx = li
                            break
                    if layer is None:
                        continue
                    if bool(getattr(track, "locked", False)):
                        QMessageBox.information(self, "Layer Locked", "Unlock this timeline layer before deleting it.")
                        return
                    if bool(getattr(layer, "locked", False)):
                        QMessageBox.information(self, "Layer Locked", "Unlock this layer before deleting it.")
                        return
                    layer_type = str(
                        getattr(getattr(layer, "type", ""), "value", getattr(layer, "type", ""))
                    ).lower()
                    # TS1 is a projection of the canonical subtitle lists.
                    # Removing only its visual DubSubtitleLayer leaves the
                    # source segment intact; a later resize calls
                    # apply_segments_to_timeline() and recreates that "deleted"
                    # cue. Route it through the canonical segment deletion
                    # branch below instead.
                    is_subtitle_layer = (
                        layer_type == "dub_subtitle"
                        or str(getattr(getattr(track, "type", ""), "value", getattr(track, "type", ""))).lower() == "dub_subtitle"
                    )
                    if is_subtitle_layer:
                        if bool(getattr(track, "locked", False)) or bool(getattr(layer, "locked", False)):
                            QMessageBox.information(self, "Layer Locked", "Unlock this timeline layer before deleting it.")
                            return
                        segment_index = int(getattr(self.timeline, "_segment_indices", {}).get(layer.id, -1))
                        if segment_index < 0 and isinstance(getattr(layer, "metadata", None), dict):
                            try:
                                segment_index = int(layer.metadata.get("_seg_index", -1))
                            except (TypeError, ValueError):
                                segment_index = -1
                        if segment_index < 0:
                            QMessageBox.warning(self, "Delete Segment", "Could not identify the selected subtitle segment.")
                            return
                        # Carry the selected layer's timing/text into the
                        # canonical deletion path.  Segment indices can be
                        # renumbered after an insertion/deletion, so relying
                        # on a stale index can remove the adjacent cue.
                        self._pending_delete_segment_key = (
                            round(float(getattr(layer, "start", 0.0) or 0.0), 6),
                            round(float(getattr(layer, "end", 0.0) or 0.0), 6),
                            str(getattr(layer, "text", "") or ""),
                        )
                        self.timeline._selected_layer_id = ""
                        self._selected_segment_index = segment_index
                        return self.delete_selected_timeline_segment()
                    # Use the layer-specific removal paths where they own
                    # preview state.  The Delete timeline button therefore
                    # removes the selected layer rather than merely deleting
                    # a timeline bar and leaving a stale overlay behind.
                    if layer_type == "image" and str(getattr(track, "name", "")) == "L1 Logo":
                        self._delete_logo_layer(layer)
                        return
                    if layer_type == "mask" or str(getattr(track, "name", "")) == "M1":
                        self._delete_mask_layer(layer)
                        return
                    # Blur: pop the corresponding overlay region first
                    if layer_type == "blur":
                        try:
                            overlay = getattr(self.video_view, "blur_overlay", None)
                            # A split BlurLayer can share one preview region
                            # with its sibling pieces. Only remove by index
                            # when preview regions and timeline layers are
                            # still one-to-one; otherwise deleting one split
                            # piece would remove the surviving blur region.
                            if (
                                overlay is not None
                                and len(overlay._regions) == len(track.layers)
                                and 0 <= layer_idx < len(overlay._regions)
                            ):
                                overlay._regions.pop(layer_idx)
                                overlay._active_index = min(
                                    layer_idx, len(overlay._regions) - 1
                                )
                                overlay.update()
                                if hasattr(overlay, "sync_to_view"):
                                    overlay.sync_to_view()
                        except Exception:
                            pass
                    # Remove the layer from the track
                    try:
                        if layer in track.layers:
                            track.layers.remove(layer)
                    except ValueError:
                        pass
                    # If the track is now empty, remove it (B1, L1, etc.)
                    if not track.layers:
                        try:
                            self.timeline._timeline.tracks.remove(track)
                        except ValueError:
                            pass
                        if hasattr(self.timeline, "_track_heights") and track.id in self.timeline._track_heights:
                            del self.timeline._track_heights[track.id]
                    # Sync blur overlay if needed
                    if layer_type == "blur":
                        try:
                            regions = self._current_blur_regions_payload() if hasattr(self, "_current_blur_regions_payload") else []
                            if hasattr(self.video_view, "set_blur_regions_normalized"):
                                self.video_view.set_blur_regions_normalized(regions)
                            if hasattr(self.timeline, "sync_blur_regions"):
                                self.timeline.sync_blur_regions(regions)
                            if hasattr(self, "apply_preview_blur_region"):
                                self.apply_preview_blur_region(force=True)
                            if hasattr(self, "persist_project_blur_state"):
                                self.persist_project_blur_state()
                        except Exception:
                            pass
                    # Clear selection and redraw
                    try:
                        self.timeline._selected_layer_id = ""
                    except Exception:
                        pass
                    if hasattr(self.timeline, "_redraw"):
                        self.timeline._redraw()
                    if hasattr(self.timeline, "viewport"):
                        self.timeline.viewport().update()
                    # Show default inspector
                    if hasattr(self, "_show_default_inspector"):
                        self._show_default_inspector()
                    # Keep remaining Logo / Mask layers visible. Clearing
                    # the whole overlay here used to hide every surviving
                    # layer until the user clicked one in the timeline.
                    if str(getattr(track, "name", "")) == "L1 Logo":
                        if track.layers:
                            next_layer = track.layers[min(layer_idx, len(track.layers) - 1)]
                            self.timeline._selected_layer_id = next_layer.id
                            self._show_logo_overlay(track, next_layer)
                        elif hasattr(self, "video_view") and hasattr(self.video_view, "clear_logo"):
                            self.video_view.clear_logo()
                    if str(getattr(track, "name", "")) == "M1":
                        if not track.layers and hasattr(self, "video_view") and hasattr(self.video_view, "clear_mask_region"):
                            self.video_view.clear_mask_region()
                        try:
                            if hasattr(self, "_apply_mask_to_preview"):
                                self._apply_mask_to_preview()
                        except Exception:
                            pass
                        try:
                            if hasattr(self, "persist_project_mask_state"):
                                self.persist_project_mask_state()
                        except Exception:
                            pass
                    if layer_type == "blur":
                        # Do not auto-select a surviving B1 layer. Leave the
                        # editor focused on V1 so the remaining effect is
                        # visible but not implicitly put into edit mode.
                        self._clear_effect_selection_after_delete()
                    if layer_type == "text":
                        # The preview overlay owns a list of all text layers;
                        # refresh it after deletion so only the selected
                        # layer is removed and surviving text stays visible.
                        self._refresh_text_layer_preview("")
                    if layer_type == "audio" and str(getattr(track, "name", "")) == "A2 Music":
                        # Removing a Music Layer must also remove it from the
                        # composed preview sidecar.  Do not leave the hidden
                        # legacy path/artifact as a fallback after the last
                        # music layer has been deleted.
                        if not track.layers:
                            try:
                                self.bg_music_edit.clear()
                            except Exception:
                                pass
                            self.last_music_path = ""
                            self.processed_artifacts.pop("music", None)
                            state = getattr(self, "current_project_state", None)
                            if state is not None:
                                state.artifacts.pop("music", None)
                                self.project_service.save_project(state)
                        self._refresh_music_layer_summary()
                        self._schedule_preview_audio_refresh(force=True)
                    try:
                        self.persist_current_timeline_project_data()
                    except Exception:
                        pass
                    return
        segments = list(self.get_active_segments() or [])
        if not segments:
            return
        index = int(getattr(self, "_selected_segment_index", -1))
        pending_key = getattr(self, "_pending_delete_segment_key", None)
        if pending_key:
            target_start, target_end, target_text = pending_key
            matching = [
                idx for idx, segment in enumerate(segments)
                if abs(float(segment.get("start", 0.0) or 0.0) - target_start) < 0.01
                and abs(float(segment.get("end", 0.0) or 0.0) - target_end) < 0.01
                and (not target_text or str(segment.get("text", "") or "") == target_text)
            ]
            if not matching:
                matching = [
                    idx for idx, segment in enumerate(segments)
                    if abs(float(segment.get("start", 0.0) or 0.0) - target_start) < 0.01
                    and abs(float(segment.get("end", 0.0) or 0.0) - target_end) < 0.01
                ]
            if matching:
                index = matching[0]
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

        def _matching_index(items):
            if not pending_key:
                return index if 0 <= index < len(items) else -1
            target_start, target_end, target_text = pending_key
            for item_index, segment in enumerate(items):
                if abs(float(segment.get("start", 0.0) or 0.0) - target_start) < 0.01 \
                        and abs(float(segment.get("end", 0.0) or 0.0) - target_end) < 0.01 \
                        and (not target_text or str(segment.get("text", "") or "") == target_text):
                    return item_index
            for item_index, segment in enumerate(items):
                if abs(float(segment.get("start", 0.0) or 0.0) - target_start) < 0.01 \
                        and abs(float(segment.get("end", 0.0) or 0.0) - target_end) < 0.01:
                    return item_index
            return index if 0 <= index < len(items) else -1

        current_index = _matching_index(self.current_segments or [])
        if 0 <= current_index < len(self.current_segments or []):
            delete_history_entry["current_before"] = [copy.deepcopy(self.current_segments[current_index])]
            self.current_segments[current_index:current_index + 1] = []
            self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
            self._sync_hidden_transcript_text_from_segments()

        translated_index = _matching_index(self.current_translated_segments or [])
        if 0 <= translated_index < len(self.current_translated_segments or []):
            delete_history_entry["translated_before"] = [copy.deepcopy(self.current_translated_segments[translated_index])]
            self.current_translated_segments[translated_index:translated_index + 1] = []
            self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
            self._sync_hidden_translated_text_from_segments()

        # The optional one-line display cache is indexed against the old
        # subtitle list. It must never survive a deletion, otherwise a later
        # timing edit can redraw a stale cue from that cache.
        self._single_line_split_cache = None
        self._pending_delete_segment_key = None

        self._timeline_timing_undo_stack.append(delete_history_entry)
        self._timeline_timing_redo_stack = []
        if len(self._timeline_timing_undo_stack) > 100:
            self._timeline_timing_undo_stack = self._timeline_timing_undo_stack[-100:]
        self._refresh_timeline_history_buttons()

        self.set_selected_segment_index(target_selection, sync_ui=True)
        if hasattr(self, "timeline"):
            self.timeline.set_active_segment_index(target_selection)
        self.apply_segments_to_timeline()
        # `persist_current_timeline_project_data()` intentionally skips empty
        # lists during project initialization. A user deletion is different:
        # write the exact post-delete lists, including [] so a removed final
        # cue can never be restored from the project artifacts.
        state = getattr(self, "current_project_state", None)
        if state is not None:
            try:
                self.current_segment_models = self.project_bridge.persist_transcription(
                    state, self.current_segments or [], self.last_original_srt_path,
                )
                if self.current_translated_segments is not None:
                    self.current_translated_segment_models = self.project_bridge.persist_translation(
                        state,
                        self.current_segment_models,
                        self.current_translated_segments or [],
                        self.last_translated_srt_path,
                    )
            except Exception as exc:
                self.log(f"[Subtitle] Could not update deleted segment cache: {exc}")
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
        # Timing changes invalidate the optional derived one-line display
        # list. Otherwise it can retain deleted cues and overwrite the fresh
        # canonical subtitle state on the next redraw.
        self._single_line_split_cache = None
        self.apply_segments_to_timeline()
        # Rebuilding TS1 rehydrates its layers and can briefly select the
        # first cue while the preview refreshes. Restore the cue that was
        # actually edited only after the rebuild has completed.
        self.set_selected_segment_index(index, sync_ui=True)
        if hasattr(self, "timeline"):
            self.timeline.set_active_segment_index(index)
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

    def _refresh_timeline_history_buttons(self):
        if hasattr(self, "timeline_undo_btn"):
            self.timeline_undo_btn.setEnabled(bool(self._timeline_timing_undo_stack))
        if hasattr(self, "timeline_redo_btn"):
            self.timeline_redo_btn.setEnabled(bool(self._timeline_timing_redo_stack))

    def undo_last_timeline_timing_edit(self):
        if self._preview_is_playing():
            return False
        if not self._timeline_timing_undo_stack:
            return False
        entry = self._timeline_timing_undo_stack.pop()
        if str(entry.get("type", "")) == "range_split":
            self._apply_range_split_history(entry, use_after=False)
            self._timeline_timing_redo_stack.append(entry)
            self._refresh_timeline_history_buttons()
            return True
        if str(entry.get("type", "")) == "overlay_split":
            self._apply_overlay_split_history(entry, use_after=False)
            self._timeline_timing_redo_stack.append(entry)
            self._refresh_timeline_history_buttons()
            return True
        if str(entry.get("type", "timing")) in {"insert", "split", "delete", "batch_timing"}:
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
        if self._preview_is_playing():
            return False
        if not self._timeline_timing_redo_stack:
            return False
        entry = self._timeline_timing_redo_stack.pop()
        if str(entry.get("type", "")) == "range_split":
            self._apply_range_split_history(entry, use_after=True)
            self._timeline_timing_undo_stack.append(entry)
            self._refresh_timeline_history_buttons()
            return True
        if str(entry.get("type", "")) == "overlay_split":
            self._apply_overlay_split_history(entry, use_after=True)
            self._timeline_timing_undo_stack.append(entry)
            self._refresh_timeline_history_buttons()
            return True
        if str(entry.get("type", "timing")) in {"insert", "split", "delete", "batch_timing"}:
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

    def _apply_overlay_split_history(self, entry, *, use_after: bool):
        timeline = getattr(self, "timeline", None)
        if timeline is None or not timeline._timeline:
            return
        track_id = str(entry.get("track_id", ""))
        for track in timeline._timeline.tracks:
            if track.id == track_id:
                track.layers = copy.deepcopy(entry.get("after_layers" if use_after else "before_layers", []))
                timeline._selected_layer_id = track.layers[-1].id if track.layers else ""
                timeline._redraw()
                self.persist_current_timeline_project_data()
                self.refresh_ui_state()
                return

    def _apply_range_split_history(self, entry, *, use_after: bool):
        suffix = "after" if use_after else "before"
        self.current_segments = copy.deepcopy(entry.get(f"current_{suffix}", []))
        self.current_translated_segments = copy.deepcopy(entry.get(f"translated_{suffix}", []))
        self.current_segment_models = self._dict_segments_to_models(self.current_segments, translated=False)
        self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
        self._sync_hidden_transcript_text_from_segments()
        self._sync_hidden_translated_text_from_segments()
        self.apply_segments_to_timeline()
        self.persist_current_timeline_project_data()
        self.schedule_live_subtitle_preview_refresh()
        self.refresh_ui_state()

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

    def _is_subtitle_inspector_details_visible(self) -> bool:
        stack = getattr(self, "inspector_stack", None)
        if not stack or stack.currentIndex() != 0:
            return False
        card = getattr(self, "subtitle_inspector_card", None)
        return bool(card and card.isVisible())

    def is_subtitle_inspector_anchored(self) -> bool:
        # Backwards-compatible alias - the anchor now applies to the
        # entire track inspector (subtitle, audio, blur, default).
        return self.is_inspector_anchored()

    def is_inspector_anchored(self) -> bool:
        checkbox = getattr(self, "anchor_inspector_cb", None)
        return bool(checkbox and checkbox.isChecked())

    def _sync_subtitle_inspector_shell_width(self, visible: bool = None):
        """Width of the inspector shell.

        The shell hosts a QStackedWidget that can show a subtitle, audio or
        default card. Width is driven by the `_inspector_collapsed` state:
        - collapsed=True  -> handle only
        - collapsed=False -> wide enough for the widest card

        The `visible` parameter is ignored (kept for API compatibility).
        """
        shell = getattr(self, "subtitle_inspector_shell", None)
        if shell is None:
            return
        # The handle was removed - no extra handle width to add.
        handle_width = 0

        if bool(getattr(self, "_inspector_collapsed", False)):
            target_width = handle_width
        else:
            responsive_width = int(getattr(self, "_responsive_inspector_width", 0) or 0)
            widest = responsive_width if responsive_width > 0 else 400
            for attr in ("subtitle_inspector_card", "audio_inspector_card", "default_inspector_card"):
                card = getattr(self, attr, None)
                if card is None:
                    continue
                try:
                    raw_max = int(card.maximumWidth() or 0)
                    if raw_max > 5000 or raw_max <= 0:
                        raw_max = 0
                    raw_min = int(card.minimumWidth() or 0)
                    if raw_min > 5000 or raw_min < 0:
                        raw_min = 0
                    raw_hint = int(card.sizeHint().width() or 0)
                    candidate = raw_max or raw_hint or raw_min or widest
                    widest = max(widest, candidate)
                except Exception:
                    pass
            if responsive_width > 0:
                widest = max(responsive_width, min(widest, 440))
            else:
                widest = max(400, min(widest, 560))
            target_width = handle_width + widest
        shell.setMinimumWidth(target_width)
        shell.setMaximumWidth(target_width)
        shell.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def _update_subtitle_inspector_summary(self, rows=None):
        rows = rows if rows is not None else self._segment_editor_display_rows()
        count = len(rows or [])
        translation_ready = self._translation_phase_complete()
        if not count:
            self._selected_segment_index = -1
            if hasattr(self, "subtitle_inspector_summary_label"):
                self.subtitle_inspector_summary_label.setText("Selected subtitle: none")
            if hasattr(self, "rewrite_selected_segment_btn"):
                self.rewrite_selected_segment_btn.setEnabled(False)
            return

        selected_index = self._get_effective_selected_segment_index(rows)
        if selected_index < 0 or selected_index >= count:
            selected_index = int(rows[0].get("segment_index", 0))
        self._selected_segment_index = selected_index
        if hasattr(self, "subtitle_inspector_summary_label"):
            self.subtitle_inspector_summary_label.setText(f"Selected subtitle: Block {selected_index + 1} / {count}")
        if hasattr(self, "rewrite_selected_segment_btn"):
            self.rewrite_selected_segment_btn.setEnabled(translation_ready)

    def _translation_phase_complete(self) -> bool:
        """Return whether translated subtitle data is a completed artifact."""
        state = getattr(self, "current_project_state", None)
        steps = getattr(state, "steps", {}) or {}
        status = str(steps.get("translate_raw", "") or "").strip().lower()
        if status in {"running", "failed", "cancelled", "pending"}:
            return False
        segments_ready = bool(getattr(self, "current_translated_segments", None))
        artifacts = getattr(state, "artifacts", {}) or {}
        artifact_path = str(artifacts.get("translation_final", "") or "").strip()
        artifact_ready = bool(artifact_path and os.path.exists(artifact_path))
        if not (segments_ready or artifact_ready):
            return False
        # Legacy projects may have the artifact but no explicit done status.
        return status == "done" or artifact_ready

    def set_subtitle_inspector_details_visible(self, visible: bool, *, sync: bool = True):
        if not visible and self.is_inspector_anchored():
            visible = True
        # The subtitle details widget (segment editor) visibility is
        # independent from the audio/default cards. The shell collapse
        # state is managed via `set_inspector_collapsed` (called from the
        # toggle button handler), not by this function.
        widget = getattr(self, "subtitle_inspector_details_widget", None)
        if widget is not None:
            widget.setVisible(bool(visible))
        toggle_btn = getattr(self, "subtitle_inspector_toggle_btn", None)
        if toggle_btn is not None:
            toggle_btn.blockSignals(True)
            toggle_btn.setChecked(bool(visible))
            if str(toggle_btn.objectName() or "") == "subtitleInspectorHandleBtn":
                toggle_btn.setText("▶" if visible else "◀")
                toggle_btn.setToolTip("Hide subtitle editor" if visible else "Show subtitle editor")
            else:
                toggle_btn.setText("Hide details" if visible else "Show details")
            toggle_btn.blockSignals(False)
        anchor_cb = getattr(self, "anchor_inspector_cb", None)
        if anchor_cb is not None:
            toggle_btn = getattr(self, "subtitle_inspector_toggle_btn", None)
            if toggle_btn is not None:
                toggle_btn.setEnabled(not self.is_inspector_anchored())
        if not visible:
            self._clear_segment_editor_rows()
            self._segment_editor_rows = []
            self._update_subtitle_inspector_summary()
        else:
            self._sync_selected_segment_to_playback_position()
            if sync:
                self.sync_segment_editor_rows()
        # Do NOT change the inspector collapsed state from here; the
        # toggle button drives the collapse. Other callers (e.g. media_utils
        # on Play) just hide the details without collapsing the shell.

    def set_inspector_collapsed(self, collapsed: bool):
        """Collapse or expand the inspector shell. The track layer
        inspector is always expanded - collapse is disabled.
        """
        collapsed = False
        self._inspector_collapsed = False
        # Sync shell width
        try:
            self._sync_subtitle_inspector_shell_width(visible=not bool(collapsed))
        except Exception:
            pass
        # Hide the entire stack so no card content is visible when collapsed
        stack = getattr(self, "inspector_stack", None)
        if stack is not None:
            stack.setVisible(not bool(collapsed))
        # Sync subtitle details widget visibility to match
        widget = getattr(self, "subtitle_inspector_details_widget", None)
        if widget is not None:
            widget.setVisible(not bool(collapsed))
        # Sync toggle button
        toggle_btn = getattr(self, "subtitle_inspector_toggle_btn", None)
        if toggle_btn is not None:
            toggle_btn.blockSignals(True)
            toggle_btn.setChecked(not bool(collapsed))
            toggle_btn.setText("▶" if collapsed else "◀")
            toggle_btn.setToolTip(
                "Show track inspector" if collapsed else "Hide track inspector"
            )
            toggle_btn.blockSignals(False)

    def show_subtitle_inspector_details(self):
        self.set_subtitle_inspector_details_visible(True, sync=True)

    def toggle_subtitle_inspector_details(self, checked: bool):
        # checked=True means "show details" (expand the inspector shell).
        # checked=False means "hide details" (collapse to handle only).
        self.set_inspector_collapsed(not bool(checked))
        # Also update the subtitle details widget visibility (so the
        # segment editor appears/disappears).
        widget = getattr(self, "subtitle_inspector_details_widget", None)
        if widget is not None:
            widget.setVisible(bool(checked))

    def on_anchor_inspector_toggled(self, checked: bool):
        if checked:
            # Anchor means: keep the track inspector shell expanded
            # (whichever card is currently shown: subtitle, audio, blur
            # or default).
            self.set_inspector_collapsed(False)
        toggle_btn = getattr(self, "subtitle_inspector_toggle_btn", None)
        if toggle_btn is not None:
            toggle_btn.setEnabled(not checked)
        self.save_user_settings()

    def _sync_selected_segment_to_playback_position(self):
        if not hasattr(self, "media_player"):
            return
        segments = self.live_preview_segments or self.get_active_segments()
        if not segments:
            return
        try:
            position_ms = int(self.media_player.position())
        except Exception:
            return
        active_index = self._find_active_segment_index(position_ms, segments)
        if active_index >= 0:
            self.set_selected_segment_index(active_index, sync_ui=False)

    def sync_segment_editor_rows(self):
        if not hasattr(self, "segment_editor_layout") or getattr(self, "_syncing_segment_editor", False):
            return
        if not self._is_subtitle_inspector_details_visible():
            self._update_subtitle_inspector_summary()
            return

        self._syncing_segment_editor = True
        try:
            self._clear_segment_editor_rows()
            self._segment_editor_rows = []
            rows = self._segment_editor_display_rows()
            self._update_subtitle_inspector_summary(rows)
            if not rows:
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
            self._update_subtitle_inspector_summary(rows)

            show_original = True
            for row in visible_rows:
                idx = int(row.get("segment_index", 0))
                card = QFrame(self.segment_editor_container if hasattr(self, "segment_editor_container") else None)
                # No border on the subtitle display frame - blends into
                # the inspector shell.
                card.setFrameShape(QFrame.NoFrame)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(4, 4, 4, 4)
                card_layout.setSpacing(6)

                # Start/End timing chips
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

                card_layout.addLayout(timing_meta_layout)

                # Speaker assignment is intentionally local to the selected
                # cue.  It lets users correct diarization mistakes without
                # rerunning the entire audio analysis pass.
                speaker_row = QHBoxLayout()
                speaker_row.setContentsMargins(0, 0, 0, 0)
                speaker_row.setSpacing(8)
                speaker_ids = self._detected_speaker_ids()
                segment_source = self.current_translated_segments or self.current_segments or []
                selected_speaker = ""
                if 0 <= idx < len(segment_source):
                    selected_speaker = str(segment_source[idx].get("speaker", "") or "").strip()
                try:
                    speaker_position = speaker_ids.index(selected_speaker)
                except ValueError:
                    speaker_position = -1
                speaker_indicator = QLabel()
                speaker_indicator.setFixedSize(10, 10)
                speaker_indicator.setStyleSheet(
                    "background: %s; border-radius: 5px; border: 1px solid #dcecff;"
                    % (self._speaker_color_hex(selected_speaker) if selected_speaker else "#53657d")
                )
                speaker_row.addWidget(speaker_indicator)
                speaker_row.addWidget(QLabel("Speaker:"))
                speaker_combo = QComboBox()
                for position, speaker_id in enumerate(speaker_ids):
                    speaker_combo.addItem(self._speaker_display_name(speaker_id, position), speaker_id)
                combo_index = speaker_combo.findData(selected_speaker)
                if combo_index >= 0:
                    speaker_combo.setCurrentIndex(combo_index)
                speaker_combo.setEnabled(bool(speaker_ids))
                speaker_combo.setToolTip(
                    "Assign this subtitle segment to a detected speaker."
                    if speaker_ids else "Run Speaker Diarization first to assign a speaker."
                )
                speaker_combo.currentIndexChanged.connect(
                    lambda _value, segment_index=idx, combo=speaker_combo: self.on_segment_speaker_changed(
                        segment_index, str(combo.currentData() or "")
                    )
                )
                speaker_row.addWidget(speaker_combo, 1)
                speaker_row.addStretch()
                card_layout.addLayout(speaker_row)

                speed_row = QHBoxLayout()
                speed_row.setContentsMargins(0, 0, 0, 0)
                speed_row.setSpacing(8)
                speed_label = QLabel("Voice Speed:")
                speed_label.setObjectName("helperLabel")
                speed_spin = ReliableDoubleSpinBox()
                speed_spin.setRange(0.5, 3.0)
                speed_spin.setSingleStep(0.1)
                speed_spin.setDecimals(1)
                speed_spin.setValue(float(row.get("voice_speed", 1.0)))
                speed_spin.setSuffix("x")
                speed_spin.setFixedWidth(90)
                speed_spin.valueChanged.connect(
                    lambda val, idx=idx: self.on_segment_voice_speed_changed(idx, val)
                )
                speed_row.addWidget(speed_label)
                speed_row.addWidget(speed_spin)
                speed_row.addStretch()

                card_layout.addLayout(speed_row)
                card_layout.addWidget(original_label)

                # The QTabWidget wrapper (with the "Subtitle" tab label
                # and the horizontal tab bar / "hr" beneath it) has been
                # removed. The translated editor + highlight actions are
                # placed directly in the card layout.
                translated_editor = QTextEdit()
                translated_editor.setObjectName("segmentInspectorEditor")
                translated_editor.setAcceptRichText(False)
                translated_editor.setPlainText(row["translated"])
                translated_editor.setMinimumHeight(96)
                translated_editor.setMaximumHeight(96)
                translated_editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                translated_editor.setPlaceholderText("Text shown on screen.")
                translated_editor.textChanged.connect(
                    lambda idx=idx, editor=translated_editor: self.on_segment_translation_edited(idx, editor)
                )
                translated_editor.selectionChanged.connect(
                    lambda idx=idx, editor=translated_editor: self._update_segment_highlight_button_state(idx, editor)
                )
                highlight_btn = QPushButton("Add highlight from selection")
                highlight_btn.setEnabled(False)
                highlight_btn.clicked.connect(
                    lambda _=False, idx=idx, editor=translated_editor: self.add_segment_manual_highlight(idx, editor)
                )

                highlight_action_layout = QHBoxLayout()
                highlight_action_layout.setContentsMargins(0, 0, 0, 0)
                highlight_action_layout.setSpacing(8)
                highlight_action_layout.addWidget(highlight_btn)
                highlight_action_layout.addStretch()

                highlight_meta_layout = QHBoxLayout()
                highlight_meta_layout.setContentsMargins(0, 0, 0, 0)
                highlight_meta_layout.setSpacing(6)
                highlight_placeholder = QLabel("")
                highlight_placeholder.setObjectName("helperLabel")
                highlight_chip_container = QWidget()
                highlight_chip_layout = QHBoxLayout(highlight_chip_container)
                highlight_chip_layout.setContentsMargins(0, 0, 0, 0)
                highlight_chip_layout.setSpacing(6)
                highlight_meta_layout.addWidget(highlight_placeholder)
                highlight_meta_layout.addWidget(highlight_chip_container, 1)

                card_layout.addWidget(translated_editor, 0)
                card_layout.addLayout(highlight_action_layout)
                card_layout.addLayout(highlight_meta_layout)

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
                self._update_segment_spoken_status(idx)

            self._set_segment_editor_highlight(selected_index)
        finally:
            self._syncing_segment_editor = False

    def sync_segment_editor_from_hidden_text(self):
        if getattr(self, "_syncing_hidden_editor_text", False):
            return

        transcript_text = self.transcript_text.toPlainText().strip()
        if transcript_text and not transcript_text.lower().startswith("transcribing..."):
            # Preserve non-SRT metadata (notably diarization speaker IDs)
            # when the hidden SRT editor is populated during project load.
            parsed_transcript = self._segments_from_editor_text(
                transcript_text, self.current_segments
            )
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
                    "speaker": str(base.get("speaker", "") or ""),
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

    def on_segment_voice_speed_changed(self, index: int, value: float):
        if getattr(self, "_syncing_segment_editor", False):
            return
        for segments_list in (self.current_translated_segments, self.current_segments):
            if segments_list and 0 <= index < len(segments_list):
                segments_list[index]["voice_speed"] = round(float(value), 1)
                self._voiceover_force_refresh = True
        self.persist_current_timeline_project_data()

    def _set_segment_editor_highlight(self, active_index: int):
        rows = getattr(self, "_segment_editor_rows", [])
        target_frame = None
        for row in rows:
            row_index = int(row.get("segment_index", -1))
            if row_index == active_index:
                row["frame"].setStyleSheet("QFrame#statusCard { background-color: #153149; border: 1px solid #5fb9ff; border-radius: 14px; }")
                target_frame = row["frame"]
            else:
                row["frame"].setStyleSheet("")
        # Scroll the outer inspector card so the highlighted segment
        # is visible. The inner segment_editor_scroll was flattened;
        # the QScrollArea wrapping the subtitle card is at stack index 0.
        if target_frame is not None and hasattr(self, "inspector_stack"):
            try:
                scroll = self.inspector_stack.widget(0)
                if scroll is not None and hasattr(scroll, "ensureWidgetVisible"):
                    scroll.ensureWidgetVisible(target_frame, 0, 36)
            except Exception:
                pass

    def stop_audio_preview(self):
        if os.name == "nt":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if hasattr(self, "audio_preview_player") and self.audio_preview_player is not None:
            try:
                self.audio_preview_player.stop()
            except Exception:
                pass

    def play_audio_preview_file(self, audio_path: str):
        if not audio_path or not os.path.exists(audio_path):
            raise FileNotFoundError("Audio preview file was not found.")
        if os.path.getsize(audio_path) <= 44:
            raise RuntimeError("Audio preview file is empty or invalid.")
        if hasattr(self, "media_player") and self.media_player.is_playing():
            self.media_player.pause()
            if hasattr(self, "timeline"):
                self.timeline.set_playing(False)

        abs_path = os.path.abspath(audio_path)
        self._last_audio_preview_path = abs_path
        self.stop_audio_preview()

        # On Windows, try native winsound for WAV files (direct to Windows audio device, highly reliable)
        played_native = False
        if os.name == "nt" and abs_path.lower().endswith(".wav"):
            try:
                import winsound
                winsound.PlaySound(abs_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                played_native = True
            except Exception as exc:
                self.log(f"[Voice Preview] native winsound failed: {exc}")

        if not played_native:
            if not hasattr(self, "audio_preview_player") or self.audio_preview_player is None:
                self.setup_audio_preview_player()
            if hasattr(self, "audio_preview_output") and self.audio_preview_output is not None:
                try:
                    from PySide6.QtMultimedia import QMediaDevices
                    self.audio_preview_output.setDevice(QMediaDevices.defaultAudioOutput())
                    self.audio_preview_output.setVolume(1.0)
                    self.audio_preview_output.setMuted(False)
                except Exception:
                    pass
            self.audio_preview_player.stop()
            self.audio_preview_player.setSource(QUrl.fromLocalFile(abs_path))
            self.audio_preview_player.play()

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

    def _blur_effect_enabled(self) -> bool:
        return bool(hasattr(self, "blur_area_btn") and self.blur_area_btn.isChecked())

    def _sync_blur_controls(self):
        video_view = getattr(self, "video_view", None)
        blur_btn = getattr(self, "blur_area_btn", None)
        blur_add_btn = getattr(self, "blur_add_btn", None)
        if video_view is None or blur_btn is None:
            return
        has_video = bool(self.video_path_edit.text().strip()) and os.path.exists(self.video_path_edit.text().strip())
        blur_enabled = self._blur_effect_enabled()
        is_playing = False
        media_player = getattr(self, "media_player", None)
        if media_player is not None:
            try:
                is_playing = bool(media_player.is_playing())
            except Exception:
                is_playing = False
        # The blur overlay (the draggable rectangle) is only shown
        # when the blur effect is ON. Turning the effect OFF hides
        # the rectangle; turning it ON shows it again for drag.
        has_regions = bool(self._current_blur_regions_payload())
        selected_id = str(getattr(getattr(self, "timeline", None), "_selected_layer_id", "") or "")
        selected_blur = False
        timeline_model = getattr(getattr(self, "timeline", None), "_timeline", None)
        for track in getattr(timeline_model, "tracks", []):
            if str(getattr(track, "name", "")) != "B1":
                continue
            selected_blur = any(str(getattr(layer, "id", "")) == selected_id for layer in getattr(track, "layers", []))
            break
        editing_allowed = (
            blur_enabled
            and has_video
            and has_regions
            and selected_blur
            and not is_playing
            and not bool(getattr(self, "_filter_thumbnail_visible", False))
        )
        video_view.set_blur_edit_enabled(editing_allowed)
        if blur_add_btn is not None:
            # The "+" button must be clickable even when the blur effect
            # toggle is OFF: pressing it should both enable the effect
            # AND add a region. Requiring the user to toggle first is
            # unnecessary friction.
            blur_add_btn.setEnabled(
                bool(getattr(self, "_optional_layer_controls_ready", False))
                and has_video
                and not is_playing
                and not bool(getattr(self, "_filter_thumbnail_visible", False))
            )

    def toggle_blur_effect_enabled(self, checked: bool):
        if not hasattr(self, "video_view") or not hasattr(self, "blur_area_btn"):
            return
        has_video = bool(self.video_path_edit.text().strip()) and os.path.exists(self.video_path_edit.text().strip())
        if checked and not has_video:
            self.blur_area_btn.blockSignals(True)
            self.blur_area_btn.setChecked(False)
            self.blur_area_btn.blockSignals(False)
            QMessageBox.warning(self, "Blur Area", "Please load a video before adding a blur area.")
            return
        # The B1 header visibility control is the single visibility source.
        # Update the managed MPV effect immediately, including while paused.
        self._sync_blur_controls()
        if hasattr(self, "media_player"):
            if checked:
                self.apply_preview_blur_region(force=True)
            else:
                self.media_player.clear_blur_region()
        self.persist_project_blur_state()
        # Sync the B1 track label so the ON/OFF indicator matches
        if hasattr(self, "track_label_bar"):
            try:
                self.track_label_bar.set_blur_on("B1", bool(checked))
            except Exception:
                pass
        if checked:
            self.log("[Blur Area] blur effect enabled.")

    def add_blur_region(self):
        if not hasattr(self, "video_view"):
            return
        has_video = bool(self.video_path_edit.text().strip()) and os.path.exists(self.video_path_edit.text().strip())
        if not has_video:
            QMessageBox.warning(self, "Blur Area", "Please load a video before adding a blur area.")
            return
        if hasattr(self, "blur_area_btn") and not self.blur_area_btn.isChecked():
            self.blur_area_btn.blockSignals(True)
            self.blur_area_btn.setChecked(True)
            self.blur_area_btn.blockSignals(False)
        if hasattr(self.video_view, "add_blur_region"):
            self.video_view.add_blur_region()
        # Do NOT call on_add_timeline_layer("blur") here. The
        # blurRegionChanged signal emitted by add_blur_region() will
        # trigger on_preview_blur_region_changed() which (with the
        # recent fix) syncs the B1 track from the overlay regions
        # even when the blur effect is on. Adding a BlurLayer here too
        # would create a duplicate.
        self._sync_blur_controls()
        self._blur_region_preview_dirty = True
        if hasattr(self, "media_player"):
            self.media_player.clear_blur_region()
        self.persist_project_blur_state()

    def on_blur_edit_finished(self):
        if getattr(self, "_blur_edit_finish_syncing", False):
            return
        if not self._blur_effect_enabled():
            return
        self._blur_region_preview_dirty = True
        self.schedule_timeline_project_persist(blur_state=True)

    def toggle_ocr_region_editing(self, checked: bool):
        overlay = getattr(self, "ocr_region_overlay", None)
        if overlay is None:
            return
        engine = os.getenv("TRANSCRIPTION_ENGINE", _default_asr_engine())
        if not checked or engine != "ocr":
            overlay.hide()
            overlay.set_editable(False)
            self.ocr_region_btn.setStyleSheet("QPushButton { color: #6ee7d6; font-weight: bold; font-size: 10px; padding: 0; }")
            self._sync_blur_controls()
            return
        if self._blur_effect_enabled():
            self.video_view.set_blur_edit_enabled(False)
        overlay.set_editable(True)
        overlay.sync_to_view()
        self.apply_preview_blur_region()
        self.log("[OCR Region] drag inside the video preview to move or resize the OCR crop.")

    def toggle_ocr_translator(self, checked: bool):
        """Show the independent, on-demand OCR Translator selection."""
        overlay = getattr(self, "ocr_translator_overlay", None)
        self._ocr_translator_active = bool(checked)
        if overlay is None:
            return
        if not self._ocr_translator_active:
            overlay.hide()
            return
        video_path = self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        if not video_path or not os.path.isfile(video_path):
            self._ocr_translator_active = False
            button = getattr(self, "ocr_translator_btn", None)
            if button is not None:
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
            QMessageBox.warning(self, "OCR Translator", "Please load a video before capturing visual text.")
            return
        overlay.set_normalized_rect(getattr(self, "_ocr_translator_rect", (0.2, 0.2, 0.6, 0.25)))
        overlay.sync_to_view()
        # The preview's native mpv surface can finish its layout after the
        # toolbar signal. Re-sync on the next event-loop pass so the tool
        # always receives the final visible video geometry.
        QTimer.singleShot(0, overlay.sync_to_view)
        self.log("[OCR Translator] Selection active. Drag or resize it, then click Capture.")

    def _on_ocr_translator_rect_changed(self, rect):
        self._ocr_translator_rect = tuple(rect)

    def capture_ocr_translator_region(self):
        if getattr(self, "_ocr_translator_capture_worker", None) is not None:
            return
        video_path = self.video_path_edit.text().strip()
        overlay = getattr(self, "ocr_translator_overlay", None)
        if not video_path or overlay is None:
            return
        position_ms = int(self.media_player.position()) if hasattr(self, "media_player") else 0
        self._ocr_translator_rect = overlay.normalized_rect()
        overlay.set_capturing(True)
        worker = OcrTranslatorCaptureWorker(video_path, position_ms / 1000.0, self._ocr_translator_rect)
        self._ocr_translator_capture_worker = worker
        worker.finished.connect(self._on_ocr_translator_capture_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self.log(f"[OCR Translator] Capturing visual text at {position_ms / 1000.0:.2f}s.")

    def _on_ocr_translator_capture_finished(self, text, error):
        self._ocr_translator_capture_worker = None
        overlay = getattr(self, "ocr_translator_overlay", None)
        if overlay is not None:
            overlay.set_capturing(False)
        if error:
            QMessageBox.warning(self, "OCR Translator", f"Could not capture text.\n\n{error}")
            return
        if not str(text or "").strip():
            QMessageBox.information(self, "OCR Translator", "No text was detected in the selected region.")
            return
        self.log("[OCR Translator] Capture complete.")
        self._show_ocr_translator_dialog(str(text).strip())

    def _show_ocr_translator_dialog(self, original_text):
        overlay = getattr(self, "ocr_translator_overlay", None)
        if overlay is not None:
            overlay.hide()
        dialog = QDialog(self)
        dialog.setWindowTitle("OCR Translator")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumSize(520, 390)
        dialog.setStyleSheet(
            "QDialog { background: #101826; color: #e6eef9; }"
            "QLabel { color: #b9c8dc; font-weight: 700; }"
            "QTextEdit { background: #0b1220; color: #edf4ff; border: 1px solid #2b3b52; border-radius: 7px; padding: 7px; }"
            "QPushButton { background: #24364f; color: #ffffff; border: 1px solid #355271; border-radius: 7px; padding: 7px 12px; font-weight: 700; }"
            "QPushButton:hover { background: #315070; } QPushButton:disabled { color: #718198; background: #182334; }"
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Original OCR Text"))
        original_edit = QTextEdit(); original_edit.setPlainText(original_text); original_edit.setReadOnly(True)
        layout.addWidget(original_edit, 1)
        layout.addWidget(QLabel("Translated Text"))
        translated_edit = QTextEdit(); translated_edit.setReadOnly(True); translated_edit.setPlaceholderText("Click Translate to translate the captured text.")
        layout.addWidget(translated_edit, 1)
        actions = QHBoxLayout()
        translate_btn = QPushButton("Translate")
        copy_original_btn = QPushButton("Copy Original")
        copy_translation_btn = QPushButton("Copy Translation")
        close_btn = QPushButton("Close")
        actions.addWidget(translate_btn); actions.addWidget(copy_original_btn); actions.addWidget(copy_translation_btn); actions.addStretch(1); actions.addWidget(close_btn)
        layout.addLayout(actions)

        def copy_text(edit):
            QApplication.clipboard().setText(edit.toPlainText())

        def translate():
            if getattr(self, "_ocr_translator_translation_worker", None) is not None:
                return
            translate_btn.setEnabled(False); translate_btn.setText("Translating...")
            worker = OcrTranslatorTranslationWorker(
                original_text, self.get_source_language_code(), self.get_target_language_code()
            )
            self._ocr_translator_translation_worker = worker
            def finished(translated, error):
                self._ocr_translator_translation_worker = None
                translate_btn.setEnabled(True); translate_btn.setText("Translate")
                if error:
                    QMessageBox.warning(dialog, "OCR Translator", f"Translation failed.\n\n{error}")
                    return
                translated_edit.setPlainText(translated)
                self.log("[OCR Translator] Translation complete.")
            worker.finished.connect(finished)
            worker.finished.connect(worker.deleteLater)
            worker.start()

        translate_btn.clicked.connect(translate)
        copy_original_btn.clicked.connect(lambda: copy_text(original_edit))
        copy_translation_btn.clicked.connect(lambda: copy_text(translated_edit))
        close_btn.clicked.connect(dialog.accept)
        dialog.finished.connect(lambda _result: overlay.sync_to_view() if overlay is not None and self._ocr_translator_active else None)
        dialog.exec()

    def on_preview_blur_region_changed(self):
        if self._preview_is_playing():
            return
        if self._blur_effect_enabled():
            self._blur_region_preview_dirty = True
            # Even when the blur effect is on, the B1 track in the
            # timeline must stay in sync with the overlay regions. Without
            # this, deleting a region from the overlay leaves a stale
            # BlurLayer behind in the timeline. The actual mpv blur
            # effect is only updated when the video plays, to keep
            # editing fast.
            if hasattr(self, "timeline"):
                try:
                    regions = self._current_blur_regions_payload() if hasattr(self, "_current_blur_regions_payload") else []
                    self.timeline.sync_blur_regions(regions)
                    self.schedule_timeline_project_persist(blur_state=True)
                except Exception:
                    pass
            return
        self.apply_preview_blur_region()
        self.schedule_timeline_project_persist(blur_state=True)
        if hasattr(self, "timeline"):
            regions = self._current_blur_regions_payload() if hasattr(self, "_current_blur_regions_payload") else []
            self.timeline.sync_blur_regions(regions)

    def apply_preview_blur_region(self, *, regions=None, force: bool = False):
        if not hasattr(self, "media_player") or not hasattr(self, "video_view"):
            return
        self._blur_region_preview_dirty = False
        blur_enabled = self._blur_effect_enabled()
        if regions is not None:
            blur_region = regions
        else:
            blur_region = self._current_blur_regions_payload()
            suppressed_id = self._deferred_effect_layer_id_for("blur")
            if suppressed_id and isinstance(blur_region, list):
                blur_layers = []
                timeline_model = getattr(getattr(self, "timeline", None), "_timeline", None)
                for track in list(getattr(timeline_model, "tracks", []) or []):
                    if str(getattr(track, "name", "") or "") == "B1":
                        blur_layers = list(getattr(track, "layers", []) or [])
                        break
                if len(blur_layers) == len(blur_region):
                    blur_region = [
                        region for region, layer in zip(blur_region, blur_layers)
                        if str(getattr(layer, "id", "") or "") != suppressed_id
                    ]
        # Always apply the blur when enabled and regions exist, even
        # when the video is paused, so the user can see the cached
        # blur effect on the video preview.
        if blur_enabled and blur_region:
            self.media_player.set_blur_region(blur_region)
        else:
            self.media_player.clear_blur_region()

    def _current_blur_regions_payload(self):
        if not hasattr(self, "video_view") or not hasattr(self.video_view, "get_blur_region_normalized"):
            return []
        raw_regions = self.video_view.get_blur_region_normalized()
        if isinstance(raw_regions, dict):
            raw_regions = [raw_regions]
        if not isinstance(raw_regions, list):
            return []
        regions = []
        for region in raw_regions:
            if not isinstance(region, dict):
                continue
            try:
                x = max(0.0, min(1.0, float(region.get("x", 0.0))))
                y = max(0.0, min(1.0, float(region.get("y", 0.0))))
                width = max(0.0, min(1.0 - x, float(region.get("width", 0.0))))
                height = max(0.0, min(1.0 - y, float(region.get("height", 0.0))))
            except (TypeError, ValueError):
                continue
            if width <= 0.0 or height <= 0.0:
                continue
            entry = {
                "x": round(x, 6),
                "y": round(y, 6),
                "width": round(width, 6),
                "height": round(height, 6),
            }
            # Per-region style (radius, opacity, pixelate). Defaults
            # are chosen so an existing region without these keys
            # behaves the same as before the inspector was added.
            try:
                strength = region.get("blur_strength", region.get("strength"))
                if strength is not None:
                    entry["blur_strength"] = int(round(float(strength)))
            except (TypeError, ValueError):
                pass
            try:
                opacity = region.get("blur_opacity", region.get("opacity"))
                if opacity is not None:
                    entry["blur_opacity"] = round(float(opacity), 4)
            except (TypeError, ValueError):
                pass
            if bool(region.get("pixelate", False)):
                entry["pixelate"] = True
                try:
                    entry["pixelate_size"] = int(region.get("pixelate_size", 12))
                except (TypeError, ValueError):
                    entry["pixelate_size"] = 12
            regions.append(entry)
        # Preview rectangles store geometry only. Preserve timing and per-layer
        # style from B1 whenever its layers correspond one-to-one with those
        # rectangles. This is essential after splitting: a blur piece remains
        # an independent timed layer instead of being rebuilt as a full-video
        # region by the preview-to-timeline sync.
        blur_layers = []
        timeline_model = getattr(getattr(self, "timeline", None), "_timeline", None)
        for track in list(getattr(timeline_model, "tracks", []) or []):
            if str(getattr(track, "name", "") or "") == "B1":
                blur_layers = list(getattr(track, "layers", []) or [])
                break
        if blur_layers and len(blur_layers) != len(regions):
            # A split layer can produce several timeline clips that share
            # one original preview rectangle. In that case the timeline is
            # authoritative: expand its independent clips back into payload
            # entries rather than collapsing them to the single rectangle.
            regions = []
            for layer in blur_layers:
                try:
                    regions.append({
                        "x": round(float(getattr(layer, "position_x", 0.0) or 0.0), 6),
                        "y": round(float(getattr(layer, "position_y", 0.0) or 0.0), 6),
                        "width": round(float(getattr(layer, "width", 0.0) or 0.0), 6),
                        "height": round(float(getattr(layer, "height", 0.0) or 0.0), 6),
                        "start": float(getattr(layer, "start", 0.0) or 0.0),
                        "end": float(getattr(layer, "end", 0.0) or 0.0),
                        "blur_strength": float(getattr(layer, "blur_strength", 20.0) or 20.0),
                        "blur_opacity": float(getattr(layer, "blur_opacity", 1.0) or 1.0),
                        "pixelate": bool(getattr(layer, "pixelate", False)),
                        "pixelate_size": int(getattr(layer, "pixelate_size", 12) or 12),
                    })
                except (TypeError, ValueError):
                    continue
        elif len(blur_layers) == len(regions):
            for entry, layer in zip(regions, blur_layers):
                try:
                    entry["start"] = float(getattr(layer, "start", 0.0) or 0.0)
                    entry["end"] = float(getattr(layer, "end", 0.0) or 0.0)
                    entry["blur_strength"] = float(getattr(layer, "blur_strength", entry.get("blur_strength", 20)) or 20)
                    entry["blur_opacity"] = float(getattr(layer, "blur_opacity", entry.get("blur_opacity", 1.0)) or 1.0)
                    entry["pixelate"] = bool(getattr(layer, "pixelate", entry.get("pixelate", False)))
                    entry["pixelate_size"] = int(getattr(layer, "pixelate_size", entry.get("pixelate_size", 12)) or 12)
                except (TypeError, ValueError):
                    continue
        return regions

    def persist_project_blur_state(self, *, regions=None, enabled=None):
        state = getattr(self, "current_project_state", None)
        if not state:
            return
        if regions is None:
            regions = self._current_blur_regions_payload()
        if enabled is None:
            enabled = self._blur_effect_enabled()
        blur_state = {
            "enabled": bool(enabled),
            "regions": list(regions or []),
        }
        if state.settings.get("blur_state") == blur_state:
            return
        state.set_setting("blur_state", blur_state)
        self.project_service.save_project(state)



    def _restore_project_blur_state(self, state):
        blur_state = dict(getattr(state, "settings", {}).get("blur_state") or {})
        regions = blur_state.get("regions", [])
        # A serialized timeline is authoritative for optional layers.  The
        # legacy blur_state setting can be stale after deleting the last B1
        # layer, so never use it to recreate a deleted layer on reopen.
        if getattr(self, "_saved_timeline_model_restored", False):
            timeline_model = getattr(getattr(self, "timeline", None), "_timeline", None)
            timeline_layers = []
            for track in getattr(timeline_model, "tracks", []) or []:
                if str(getattr(track, "name", "") or "") == "B1":
                    timeline_layers = list(getattr(track, "layers", []) or [])
                    break
            regions = []
            for layer in timeline_layers:
                try:
                    regions.append({
                        "x": float(getattr(layer, "position_x", 0.0) or 0.0),
                        "y": float(getattr(layer, "position_y", 0.0) or 0.0),
                        "width": float(getattr(layer, "width", 0.0) or 0.0),
                        "height": float(getattr(layer, "height", 0.0) or 0.0),
                        "start": float(getattr(layer, "start", 0.0) or 0.0),
                        "end": float(getattr(layer, "end", 0.0) or 0.0),
                        "blur_strength": float(getattr(layer, "blur_strength", 20.0) or 20.0),
                        "blur_opacity": float(getattr(layer, "blur_opacity", 1.0) or 1.0),
                        "pixelate": bool(getattr(layer, "pixelate", False)),
                        "pixelate_size": int(getattr(layer, "pixelate_size", 12) or 12),
                    })
                except (TypeError, ValueError):
                    continue
        if hasattr(self, "video_view") and hasattr(self.video_view, "set_blur_regions_normalized"):
            self.video_view.set_blur_regions_normalized(regions)
        # Restore the B1 track visibility instead of forcing Blur back on.
        # Older projects did not save this value and therefore default to ON.
        blur_enabled = bool(blur_state.get("enabled", True))
        if hasattr(self, "blur_area_btn"):
            self.blur_area_btn.blockSignals(True)
            self.blur_area_btn.setChecked(blur_enabled)
            self.blur_area_btn.blockSignals(False)
        self._sync_blur_controls()
        if hasattr(self, "track_label_bar"):
            try:
                self.track_label_bar.set_blur_on("B1", blur_enabled)
            except Exception:
                pass
        if hasattr(self, "timeline") and not getattr(self, "_saved_timeline_model_restored", False):
            self.timeline.sync_blur_regions(regions)
        if hasattr(self, "media_player"):
            try:
                if blur_enabled:
                    self.apply_preview_blur_region(force=True)
                else:
                    self.media_player.clear_blur_region()
            except Exception:
                self.media_player.clear_blur_region()

    # ---- Mask layer (M1) ----
    def _current_mask_regions_payload(self, *, time_seconds=None, include_inactive=False,
                                      exclude_layer_id: str = ""):
        """Build the mask payload from the M1 track's MaskLayers.

        Visibility is NOT checked here — the play-state gate in
        _apply_mask_to_preview is the single source of truth for
        whether the mask is shown on the video. The payload always
        includes every M1 layer so the mask is ready the moment the
        user presses play.
        """
        if not hasattr(self, "timeline") or not self.timeline._timeline:
            return []
        items: list[dict] = []
        for tr in self.timeline._timeline.tracks:
            if tr.name != "M1":
                continue
            for layer in tr.layers:
                if exclude_layer_id and str(getattr(layer, "id", "") or "") == str(exclude_layer_id):
                    continue
                if not include_inactive and not self._layer_is_active_at_preview_time(layer, time_seconds):
                    continue
                try:
                    items.append({
                        "x": float(getattr(layer, "position_x", 0.3)),
                        "y": float(getattr(layer, "position_y", 0.4)),
                        "width": float(getattr(layer, "width", 0.4)),
                        "height": float(getattr(layer, "height", 0.2)),
                        "color": str(getattr(layer, "color", "#000000")),
                        "mode": str(getattr(layer, "mode", "solid")),
                        "opacity": float(getattr(layer, "opacity", 1.0)),
                        "pixelate_size": int(getattr(layer, "pixelate_size", 12)),
                        "blur_strength": int(getattr(layer, "blur_strength", 20)),
                        "start": float(getattr(layer, "start", 0.0) or 0.0),
                        "end": float(getattr(layer, "end", 0.0) or 0.0),
                    })
                except (TypeError, ValueError):
                    continue
        return items

    def _apply_mask_to_preview(self, *, regions=None, force: bool = False):
        """Push the M1 mask track into the mpv filter chain.

        The mask effect is independent of timeline selection and playback
        state.  Pausing or selecting another layer must not remove it from
        the preview; only the dedicated M1 visibility control (or an empty
        mask track) may clear the effect.  The editable outline/handles are
        managed separately by the timeline selection.

        `force=True` bypasses the play-state gate (used by direct
        calls from `toggle_play` so the mask is applied/cleared in
        the same code path as the play/pause).
        """
        if not hasattr(self, "media_player"):
            return
        # M1 Hide/Show must survive play/pause, project restoration, and
        # native-window focus changes.  Never rebuild a hidden mask graph.
        if not bool(getattr(self, "_mask_track_preview_visible", True)):
            self.media_player.clear_mask_region()
            return
        if regions is None:
            regions = self._current_mask_regions_payload(
                include_inactive=True,
                exclude_layer_id=self._deferred_effect_layer_id_for("mask"),
            )
        if force:
            if regions:
                self.media_player.set_mask_region(regions)
            else:
                self.media_player.clear_mask_region()
            return
        if regions:
            self.media_player.set_mask_region(regions)
        else:
            self.media_player.clear_mask_region()

    def _on_preview_state_changed(self, _state: int):
        """Re-apply the M1 mask filter when the player state changes.

        The mask is only applied to the video while the player is
        playing. Hooked from `media_player.stateChanged` in
        `setup_media_player` so the mpv filter chain is updated on
        play / pause / stop. The mask overlay is also locked
        (`set_editable(False)`) while the video is playing so the
        user cannot accidentally drag or resize the region during
        playback. Also sync the timeline play state so the timeline
        stops running when the video ends (Bug 2).
        """
        try:
            is_playing = bool(self.media_player.is_playing())
        except Exception:
            is_playing = False
        was_review_mode = bool(getattr(self, "_review_mode_active", False))
        self._review_mode_active = is_playing
        if is_playing:
            # Entering review mode is an explicit commit boundary. This
            # restores a deferred Blur/Mask at its final geometry before the
            # next frame is shown, then removes every preview edit target.
            self._preview_edit_layer_id = ""
        if is_playing or was_review_mode:
            # Borders are selection chrome, not rendered layer content.
            # Clear their active state on both Review entry and its pause
            # transition; a subsequent explicit paused selection re-enables
            # only the requested layer.
            if hasattr(self, "video_view") and hasattr(self.video_view, "subtitle_item"):
                self.video_view.subtitle_item.set_editable(False)
            self._refresh_text_layer_preview("")
        if hasattr(self, "track_label_bar"):
            self.track_label_bar.set_controls_enabled(not is_playing)
        splitter = getattr(self, "preview_timeline_splitter", None)
        if splitter is not None:
            try:
                # Review Mode keeps the preview geometry stable so native
                # overlays and MPV effects cannot be disturbed mid-playback.
                # Disable only the handle; never disable the child widgets.
                splitter.handle(1).setEnabled(not is_playing)
            except Exception:
                pass
        # Sync the timeline's "playing" flag to the real player state.
        # Without this the timeline keeps animating past the end of the
        # video because the player auto-pauses (keep_open="always") but
        # nothing tells the timeline to stop.
        try:
            if hasattr(self, "timeline") and self.timeline is not None:
                self.timeline.set_playing(is_playing)
        except Exception:
            pass
        # Lock / unlock editing based on both play state and timeline
        # selection. Pausing must not make every region editable.
        try:
            selected_id = str(getattr(getattr(self, "timeline", None), "_selected_layer_id", "") or "")
            selected_type = ""
            selected_track_name = ""
            selected_track = None
            selected_layer = None
            for track in getattr(getattr(self.timeline, "_timeline", None), "tracks", []) if hasattr(self, "timeline") else []:
                for layer in getattr(track, "layers", []):
                    if str(getattr(layer, "id", "")) == selected_id:
                        selected_type = str(getattr(getattr(layer, "type", ""), "value", getattr(layer, "type", ""))).lower()
                        selected_track_name = str(getattr(track, "name", ""))
                        selected_track, selected_layer = track, layer
                        break
                if selected_type:
                    break
            if is_playing:
                effect_edit_changed = self.commit_deferred_effect_editing(refresh=False)
            else:
                # Pausing enters Edit Mode, but does not automatically start
                # editing the layer selected before playback. Effects remain
                # rendered and handles remain hidden until the user selects
                # a layer again.
                effect_edit_changed = False
            if effect_edit_changed:
                self.refresh_timed_layer_preview()
            mask_overlay = getattr(self.video_view, "mask_overlay", None)
            if mask_overlay is not None and mask_overlay._regions:
                mask_overlay.set_editable(bool(
                    not is_playing
                    and selected_type == "mask"
                    and selected_track_name == "M1"
                    and selected_id == str(getattr(self, "_preview_edit_layer_id", "") or "")
                    and self._deferred_effect_layer_id_for("mask") == selected_id
                ))
            if hasattr(self, "video_view") and hasattr(self.video_view, "set_blur_edit_enabled"):
                self.video_view.set_blur_edit_enabled(
                    bool(
                        not is_playing
                        and selected_type == "blur"
                        and selected_id == str(getattr(self, "_preview_edit_layer_id", "") or "")
                        and self._deferred_effect_layer_id_for("blur") == selected_id
                        and self._blur_effect_enabled()
                    )
                )
            logo_overlay = getattr(self.video_view, "logo_overlay", None)
            if logo_overlay is not None and getattr(logo_overlay, "_regions", None):
                logo_overlay.set_editable(bool(
                    not is_playing
                    and selected_type == "image"
                    and selected_track_name == "L1 Logo"
                    and selected_id == str(getattr(self, "_preview_edit_layer_id", "") or "")
                ))
            if hasattr(self, "video_view") and getattr(self.video_view, "text_overlay", None) is not None:
                # Keep text content visible but make its top-level overlay
                # click-through in review mode and immediately after pause.
                self.video_view.text_overlay.set_editable(False if (is_playing or was_review_mode) else bool(
                    selected_type == "text" and selected_id == str(getattr(self, "_preview_edit_layer_id", "") or "")
                ))
        except Exception:
            pass
        # When playback just ended, pause both audio sidecars so they
        # don't drift ahead of the held last frame.
        if not is_playing and hasattr(self, "media_player"):
            try:
                if hasattr(self.media_player, "_original_loaded_path") and getattr(self.media_player, "_original_loaded_path", ""):
                    self.media_player._original_player.pause()
            except Exception:
                pass
            try:
                if hasattr(self.media_player, "_dubbed_loaded_path") and getattr(self.media_player, "_dubbed_loaded_path", ""):
                    self.media_player._dubbed_player.pause()
            except Exception:
                pass
        try:
            self._apply_mask_to_preview()
        except Exception:
            pass
        QTimer.singleShot(0, self.refresh_ui_state)

    def persist_project_mask_state(self, *, regions=None):
        state = getattr(self, "current_project_state", None)
        if not state:
            return
        if regions is None:
            regions = self._current_mask_regions_payload()
        mask_state = {
            "enabled": bool(getattr(self, "_mask_track_preview_visible", True)),
            "regions": list(regions or []),
        }
        if state.settings.get("mask_state") == mask_state:
            return
        state.set_setting("mask_state", mask_state)
        self.project_service.save_project(state)

    def _restore_project_mask_state(self, state):
        mask_state = dict(getattr(state, "settings", {}).get("mask_state") or {})
        regions = mask_state.get("regions", [])
        timeline_model_restored = bool(getattr(self, "_saved_timeline_model_restored", False))
        if timeline_model_restored:
            # The saved M1 track is authoritative.  In particular, an empty
            # M1 track means the user deleted the final mask and must not be
            # reconstructed from the legacy mask_state setting.
            regions = self._current_mask_regions_payload(include_inactive=True)
        if hasattr(self, "media_player"):
            self._apply_mask_to_preview(regions=regions, force=True)
        if hasattr(self, "track_label_bar"):
            try:
                self.track_label_bar.set_mask_shown(
                    "M1", bool(getattr(self, "_mask_track_preview_visible", True))
                )
            except Exception:
                pass
        # Sync the M1 track from legacy settings only when no serialized
        # timeline was available.  Otherwise this method must not recreate
        # deleted layers or overwrite their timing/style properties.
        if hasattr(self, "timeline") and regions and not timeline_model_restored:
            try:
                from app.layers.mask import MaskLayer
                from app.layers.sync_bridge import find_or_create_track
                from app.layers.base import LayerType
                tl = self.timeline._timeline
                track = find_or_create_track(tl, "M1", LayerType.MASK, 60)
                track.layers.clear()
                # Mask layers span the full video duration (like the
                # audio track) so the M1 row matches the video length
                # rather than collapsing to a zero-width clip (Bug 1).
                mask_end = tl.duration if tl.duration > 0 else (
                    self.timeline._duration if hasattr(self.timeline, "_duration") else 0.0
                )
                if mask_end <= 0:
                    mask_end = 5.0
                for i, r in enumerate(regions):
                    layer = MaskLayer(
                        name=f"Mask {i + 1}",
                        position_x=float(r.get("x", 0.3)),
                        position_y=float(r.get("y", 0.4)),
                        width=float(r.get("width", 0.4)),
                        height=float(r.get("height", 0.2)),
                        color=str(r.get("color", "#000000")),
                        mode=str(r.get("mode", "solid")),
                        pixelate_size=int(r.get("pixelate_size", 12)),
                        blur_strength=int(r.get("blur_strength", 20)),
                        start=0.0,
                        end=float(mask_end),
                    )
                    layer.z_index = i
                    track.layers.append(layer)
                if hasattr(self.timeline, "_track_heights"):
                    self.timeline._track_heights[track.id] = 60
                self.timeline._redraw()
                # Keep the restored mask available for preview/editing. The
                # caller selects V1 after all tracks are restored, so this
                # does not steal the default project focus.
                if track.layers:
                    try:
                        first_layer = track.layers[0]
                        self.timeline._selected_layer_id = first_layer.id
                        self._show_mask_overlay(track, first_layer)
                    except Exception:
                        pass
            except Exception:
                pass

    def _show_mask_inspector_for_track(self, track, layer=None):
        """Show the Mask Track Inspector populated with the selected M1 layer.

        The inspector only exposes the mask's colour + opacity. Position,
        size and mode are not configurable here — the user positions /
        resizes the region via the draggable overlay on the video. The
        mask is only applied to the video while the player is playing.
        """
        self._switch_inspector("mask")
        self._wire_mask_inspector_controls()
        self._wire_layer_timing_controls("mask")
        if layer is None:
            return
        self._set_layer_timing_controls("mask", layer)
        color = str(getattr(layer, "color", "#000000"))
        if hasattr(self, "mask_inspector_color_btn"):
            self.mask_inspector_color_btn.blockSignals(True)
            self.mask_inspector_color_btn.setText(color)
            self.mask_inspector_color_btn.setStyleSheet(
                f"background-color: {color}; color: #fff;"
            )
            self.mask_inspector_color_btn.blockSignals(False)
        try:
            opacity = float(getattr(layer, "opacity", 1.0))
        except (TypeError, ValueError):
            opacity = 1.0
        opacity = max(0.0, min(1.0, opacity))
        if hasattr(self, "mask_inspector_opacity_slider"):
            self.mask_inspector_opacity_slider.blockSignals(True)
            self.mask_inspector_opacity_slider.setValue(int(round(opacity * 100)))
            self.mask_inspector_opacity_slider.blockSignals(False)
        if hasattr(self, "mask_inspector_opacity_value_label"):
            self.mask_inspector_opacity_value_label.setText(f"{int(round(opacity * 100))}%")
        if hasattr(self, "mask_inspector_summary_label"):
            tname = getattr(track, "name", "M1")
            lname = getattr(layer, "name", "Mask")
            self.mask_inspector_summary_label.setText(
                f"Selected: {tname} → {lname}. Drag the mask on the video "
                "to move it. Drag a corner to resize. The X button deletes "
                "the mask. The mask is applied while the video is playing."
            )

    def _wire_mask_inspector_controls(self):
        """One-time wiring of the Mask Inspector controls.

        Only colour + opacity are wired here. Position / size / mode
        are not configurable in the inspector; the user positions and
        resizes the mask via the draggable overlay on the video.
        """
        if getattr(self, "_mask_inspector_wired", False):
            return
        self._mask_inspector_wired = True

        def _selected_mask_layer():
            if not hasattr(self, "timeline") or not self.timeline._timeline:
                return None, None
            sid = getattr(self.timeline, "_selected_layer_id", "") or ""
            for tr in self.timeline._timeline.tracks:
                for l in tr.layers:
                    if l.id == sid:
                        return l, tr
            return None, None

        def _sync_preview(l):
            try:
                self._apply_mask_to_preview()
            except Exception:
                pass
            try:
                if hasattr(self, "persist_project_mask_state"):
                    self.persist_project_mask_state()
            except Exception:
                pass

        def _on_opacity_changed(v):
            layer, _ = _selected_mask_layer()
            if layer is None:
                return
            opacity = max(0.0, min(1.0, float(v) / 100.0))
            try:
                layer.opacity = opacity
            except Exception:
                pass
            if hasattr(self, "mask_inspector_opacity_value_label"):
                self.mask_inspector_opacity_value_label.setText(f"{int(v)}%")
            _sync_preview(layer)

        self._mask_opacity_handler = _on_opacity_changed
        if hasattr(self, "mask_inspector_opacity_slider"):
            self.mask_inspector_opacity_slider.valueChanged.connect(_on_opacity_changed)

        # Color picker
        from PySide6.QtWidgets import QColorDialog
        def _on_color_clicked():
            from PySide6.QtGui import QColor
            layer, _ = _selected_mask_layer()
            current = QColor(str(getattr(layer, "color", "#000000")))
            chosen = QColorDialog.getColor(current, self, "Pick mask colour")
            if not chosen.isValid():
                return
            hex_str = chosen.name()
            if hasattr(self, "mask_inspector_color_btn"):
                self.mask_inspector_color_btn.setText(hex_str)
                self.mask_inspector_color_btn.setStyleSheet(
                    f"background-color: {hex_str}; color: #fff;"
                )
            if layer is not None:
                try:
                    layer.color = hex_str
                except Exception:
                    pass
                _sync_preview(layer)

        self._mask_color_handler = _on_color_clicked
        if hasattr(self, "mask_inspector_color_btn"):
            self.mask_inspector_color_btn.clicked.connect(_on_color_clicked)

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
        if os.name == "nt":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
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
            played_native = False
            if source.isLocalFile():
                abs_path = os.path.abspath(source.toLocalFile())
                if os.name == "nt" and abs_path.lower().endswith(".wav"):
                    try:
                        import winsound
                        winsound.PlaySound(abs_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                        played_native = True
                    except Exception as exc:
                        self.log(f"[Voice Preview] native winsound failed: {exc}")
            if not played_native:
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
        if not voice_name:
            QMessageBox.warning(self, "Preview voice", "Choose a voice first.")
            return
        voice_speed = self._parse_voice_speed_value()
        text = "Chào bạn, đây là bản xem trước giọng nói của mẫu được chọn."  # "Hello, this is a preview of the selected voice sample." in Vietnamese

        if hasattr(self, "preview_voice_btn"):
            self.preview_voice_btn.setEnabled(False)
            self.preview_voice_btn.setText("...")

        project_state = getattr(self, "current_project_state", None) or self.ensure_current_project()
        project_settings = getattr(project_state, "settings", {}) or {}
        normalizer_dictionary = dict(project_settings.get("normalizer_dictionary", {}) or {})
        worker = VoiceSamplePreviewWorker(
            self.workspace_root,
            text,
            voice_name,
            voice_speed,
            temp_dir=self.get_project_temp_dir("voice_sample_preview"),
            normalizer_dictionary=normalizer_dictionary,
        )
        worker.progress.connect(self.log)
        worker.finished.connect(self.on_voice_sample_preview_ready)
        self._voice_sample_preview_thread = worker
        worker.start()

    def on_voice_sample_preview_ready(self, audio_path: str, error: str):
        if hasattr(self, "preview_voice_btn"):
            self.preview_voice_btn.setEnabled(True)
            self.preview_voice_btn.setText("Preview Selected Voice")
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

    def open_create_voice_clone_dialog(self):
        from widgets.voice_clone_dialog import CreateVoiceCloneDialog

        dialog = CreateVoiceCloneDialog(self)
        if dialog.exec():
            entry = dialog.created_voice_entry
            if entry and isinstance(entry, dict):
                voice_id = entry.get("id")
                self.load_voice_preview_catalog()
                if voice_id and hasattr(self, "free_voice_combo"):
                    self.set_voice_combo_value(self.free_voice_combo, voice_id)
                self.log(f"[VieNeu] Added new cloned voice: {entry.get('name', voice_id)}")

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
        if not voice_name:
            QMessageBox.warning(self, "Missing Voice", "Choose a voice first before generating subtitle audio preview.")
            return
        voice_speed = self._parse_voice_speed_value()
        project_state = getattr(self, "current_project_state", None) or self.ensure_current_project()
        project_settings = getattr(project_state, "settings", {}) or {}
        normalizer_dictionary = dict(project_settings.get("normalizer_dictionary", {}) or {})
        row = self._find_segment_editor_row(index)
        # The per-segment "Regenerate voice" button was moved to the
        # A2 Dub Track Inspector. Disable that one instead.
        if getattr(self, "audio_inspector_regenerate_voice_btn", None) is not None:
            self.audio_inspector_regenerate_voice_btn.setEnabled(False)
            self.audio_inspector_regenerate_voice_btn.setText("...")

        existing = self._segment_preview_threads.get(index)
        if existing and existing.isRunning():
            existing.quit()
            existing.wait(2000)
        worker = SegmentAudioPreviewWorker(
            self.workspace_root,
            index,
            text,
            voice_name,
            voice_speed,
            temp_dir=self.get_project_temp_dir("segment_audio_preview"),
            cache_temp_dir=self.get_project_temp_dir("tts"),
            normalizer_dictionary=normalizer_dictionary,
        )
        worker.finished.connect(self.on_segment_audio_preview_ready)
        self._segment_preview_threads[index] = worker
        worker.start()

    def on_segment_audio_preview_ready(self, index: int, audio_path: str, error: str):
        btn = getattr(self, "audio_inspector_regenerate_voice_btn", None)

        self._segment_preview_threads.pop(index, None)

        if error:
            if btn is not None:
                btn.setEnabled(True)
                btn.setText("Regenerate voice")
            self.show_error("Audio Preview Failed", "Could not generate preview audio for this subtitle.", error)
            return

        self._voiceover_force_refresh = True
        if btn is not None:
            btn.setEnabled(True)
            btn.setText("Regenerate voice")

        if getattr(self, "last_voice_vi_path", "") and os.path.exists(self.last_voice_vi_path):
            self.run_voiceover()
        else:
            self._apply_segment_audio_end_to_timeline(index=index, audio_path=audio_path)
            try:
                self.play_audio_preview_file(audio_path)
            except Exception as exc:
                self.show_error("Audio Preview Failed", "Could not play the generated preview audio.", str(exc))

    def _apply_segment_audio_end_to_timeline(self, *, index: int, audio_path: str) -> None:
        if not audio_path or not os.path.exists(audio_path):
            return
        actual_d = ffprobe_wav_duration(audio_path)
        if actual_d <= 0.0:
            return
        segs = self.current_translated_segments or self.current_segments
        if not segs or index < 0 or index >= len(segs):
            return
        seg = segs[index]
        try:
            start_s = float(seg.get("start", 0.0))
        except (TypeError, ValueError):
            return
        audio_end = start_s + actual_d
        try:
            cur_end = float(seg.get("end", audio_end))
        except (TypeError, ValueError):
            cur_end = audio_end
        if audio_end > cur_end + 0.01:
            seg["_audio_end"] = audio_end
        else:
            seg.pop("_audio_end", None)
        timeline = getattr(self, "timeline", None)
        if timeline is None:
            return
        timeline_model = getattr(timeline, "_timeline", None)
        if timeline_model is None:
            return
        from app.layers.sync_bridge import DUB_SUBTITLE_TRACK_NAME
        target_track = None
        for t in timeline_model.tracks:
            if t.name == DUB_SUBTITLE_TRACK_NAME:
                target_track = t
                break
        if target_track is None:
            return
        for layer in target_track.layers:
            meta = getattr(layer, "metadata", None) or {}
            if not isinstance(meta, dict):
                continue
            try:
                if int(meta.get("_seg_index", -1)) == index:
                    if audio_end > cur_end + 0.01:
                        meta["_audio_end"] = audio_end
                    else:
                        meta.pop("_audio_end", None)
            except (TypeError, ValueError):
                continue
        timeline._redraw()

    def download_subtitle(self):
        srt_text = self.translated_text.toPlainText().strip()
        if not srt_text:
            QMessageBox.warning(self, "Missing Subtitle", "No translated subtitle is ready yet.")
            return
        target_lang = str(self.get_target_language_code() or "translated").lower()
        suggested_name = os.path.splitext(os.path.basename(self.video_path_edit.text().strip() or "subtitle"))[0] + f"_{target_lang}.srt"
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Translated Subtitle", suggested_name, "Subtitle Files (*.srt)")
        if not file_path:
            return
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(srt_text)
        QMessageBox.information(self, "Saved", f"Translated subtitle exported to:\n\n{file_path}")

    def import_original_srt(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Original Subtitle",
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

        self.current_segments = imported_segments
        self.transcript_text.setText(srt_text)
        self.last_original_srt_path = file_path
        self.persist_transcription_project_data(imported_segments, srt_path=file_path)
        state = self.ensure_current_project()
        if state:
            state.set_setting("transcription_signature", "")
            self.project_service.save_project(state)
        self._sync_segment_models_from_current_segments()
        if hasattr(self, "timeline"):
            self.timeline.set_segments(self.current_segments)
            self.schedule_timeline_visual_refresh(waveform=True, thumbnails=True)
        self.log(f"[Import] Original subtitle loaded: {file_path} ({len(imported_segments)} segments)")
        QMessageBox.information(self, "Import Success", f"Loaded {len(imported_segments)} segments from original subtitle.")
        self.refresh_ui_state()

    def import_translated_srt(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Translated Subtitle",
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

        # An SRT only stores text/timestamps. Keep diarization metadata from
        # the current translated transcript first (manual speaker corrections
        # are stored there), falling back to the original transcript. Matching
        # by overlap also works when an imported file has slightly different
        # cue boundaries or a different number of cues.
        base_segments = self.current_translated_segments or self.current_segments
        if base_segments:
            for imported in imported_segments:
                try:
                    start = float(imported.get("start", 0.0))
                    end = float(imported.get("end", start))
                except (TypeError, ValueError):
                    continue
                best = None
                best_score = -1.0
                midpoint = (start + end) / 2.0
                for base in base_segments:
                    speaker = str(base.get("speaker", "") or "").strip()
                    if not speaker:
                        continue
                    try:
                        base_start = float(base.get("start", 0.0))
                        base_end = float(base.get("end", base_start))
                    except (TypeError, ValueError):
                        continue
                    overlap = max(0.0, min(end, base_end) - max(start, base_start))
                    distance = abs(midpoint - ((base_start + base_end) / 2.0))
                    score = overlap * 1000.0 - distance
                    if score > best_score:
                        best_score = score
                        best = base
                if best is not None:
                    speaker = str(best.get("speaker", "") or "").strip()
                    if speaker:
                        imported["speaker"] = speaker
        if self.keep_timeline_cb.isChecked() and base_segments and len(base_segments) == len(imported_segments):
            merged_segments = []
            for idx, base in enumerate(base_segments):
                merged = dict(imported_segments[idx])
                merged["start"] = float(base.get("start", 0.0))
                merged["end"] = float(base.get("end", 0.0))
                merged["words"] = list(base.get("words", []))
                if base.get("speaker"):
                    merged["speaker"] = str(base.get("speaker", "") or "")
                if "manual_highlights" in imported_segments[idx]:
                    merged["manual_highlights"] = imported_segments[idx]["manual_highlights"]
                elif base.get("manual_highlights"):
                    merged["manual_highlights"] = list(base.get("manual_highlights", []))
                merged_segments.append(merged)
            imported_segments = merged_segments
            srt_text = self.format_to_srt(imported_segments)

        self.translated_text.setText(srt_text)
        self.apply_edited_translation(show_message=False, force_apply=True)
        # ``apply_edited_translation`` rebuilds dictionaries from SRT (which
        # has no speaker field). Re-apply the metadata-bearing imported list
        # after that conversion so the speaker assignments survive both the
        # editor update and the timeline rebuild.
        if imported_segments and self.current_translated_segments:
            if len(imported_segments) == len(self.current_translated_segments):
                for idx, imported in enumerate(imported_segments):
                    speaker = str(imported.get("speaker", "") or "").strip()
                    if speaker:
                        self.current_translated_segments[idx]["speaker"] = speaker
            else:
                for target in self.current_translated_segments:
                    try:
                        start = float(target.get("start", 0.0))
                        end = float(target.get("end", start))
                    except (TypeError, ValueError):
                        continue
                    best = None
                    best_score = -1.0
                    midpoint = (start + end) / 2.0
                    for imported in imported_segments:
                        speaker = str(imported.get("speaker", "") or "").strip()
                        if not speaker:
                            continue
                        try:
                            imported_start = float(imported.get("start", 0.0))
                            imported_end = float(imported.get("end", imported_start))
                        except (TypeError, ValueError):
                            continue
                        overlap = max(0.0, min(end, imported_end) - max(start, imported_start))
                        distance = abs(midpoint - ((imported_start + imported_end) / 2.0))
                        score = overlap * 1000.0 - distance
                        if score > best_score:
                            best_score = score
                            best = imported
                    if best is not None:
                        target["speaker"] = str(best.get("speaker", "") or "").strip()
            self.current_translated_segment_models = self._dict_segments_to_models(
                self.current_translated_segments,
                translated=True,
            )
            self.apply_segments_to_timeline()
        self.last_translated_srt_path = file_path
        self.processed_artifacts["srt_translated"] = file_path
        self.persist_translation_project_data(self.current_translated_segments, file_path)
        # Rebuild speaker UI/colors after replacing the translated cues.  The
        # imported SRT itself cannot contain speaker metadata, so the merge
        # above is the source of truth for these project-only fields.
        self.refresh_detected_speakers_section()
        self._refresh_speaker_subtitle_colors_if_needed()
        self.refresh_ui_state()
        QMessageBox.information(
            self,
            "Imported",
            "Translated subtitle loaded. You can now run Generate Voice / TTS.\n\n" + file_path,
        )

    def download_original_script(self):
        script_text = self.transcript_text.toPlainText().strip()
        if not script_text:
            QMessageBox.warning(self, "Missing Script", "No original script is ready yet.")
            return
        base_name = os.path.splitext(os.path.basename(self.video_path_edit.text().strip() or "original"))[0] + "_original"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Source Subtitle",
            base_name + ".srt",
            "Subtitle Files (*.srt)",
        )
        if not file_path:
            return
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(script_text)
        QMessageBox.information(self, "Saved", f"Source subtitle exported to:\n\n{file_path}")

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
        base = self.current_translated_segments or self.current_segments or []
        if base and bool(getattr(self, "subtitle_single_line_cb", None) and self.subtitle_single_line_cb.isChecked()):
            split = getattr(self, "_single_line_split_cache", None)
            if split is not None:
                return split
        return base

    def apply_segments_to_timeline(self):
        segs = self.get_active_segments()
        if segs:
            settings = getattr(self.current_project_state, "settings", {}) or {}
            predict_speed_ratios(
                segs,
                normalizer_dictionary=dict(settings.get("normalizer_dictionary", {}) or {}),
            )
        self.timeline.set_segments(segs if segs else [])
        self.schedule_timeline_visual_refresh(waveform=True, thumbnails=True)
        # Configure the Qt subtitle overlay before showing its drag target.
        # Otherwise it can briefly use the default size until the first drag.
        self.update_subtitle_preview_style()
        self._show_subtitle_drag_layer()
        self.sync_live_subtitle_preview()

    def _segments_from_editor_text(self, srt_text: str, base_segments):
        srt_text = (srt_text or "").strip()
        if not srt_text:
            return []

        if self.keep_timeline_cb.isChecked() and base_segments:
            edited_texts = self.extract_subtitle_text_entries(srt_text)
            if edited_texts and len(edited_texts) == len(base_segments):
                out = []
                for idx, base in enumerate(base_segments):
                    d = {
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
                    if base.get("speaker"):
                        d["speaker"] = str(base.get("speaker", "") or "")
                    raw = base.get("_audio_end")
                    if raw is not None:
                        try:
                            d["_audio_end"] = float(raw)
                        except (TypeError, ValueError):
                            pass
                    out.append(d)
                return out

        parsed_segments = self.parse_srt_to_segments(srt_text)
        if base_segments and len(parsed_segments) == len(base_segments):
            for idx, segment in enumerate(parsed_segments):
                base = base_segments[idx]
                segment["words"] = list(base.get("words", []))
                segment["manual_highlights"] = list(base.get("manual_highlights", []))
                if base.get("speaker"):
                    segment["speaker"] = str(base.get("speaker", "") or "")
                if base.get("tts_text"):
                    segment["tts_text"] = str(base.get("tts_text", "") or "")
                    segment["tts_group_id"] = base.get("tts_group_id", "")
                    segment["tts_group_start"] = float(base.get("tts_group_start", base.get("start", 0.0)) or 0.0)
                    segment["tts_group_end"] = float(base.get("tts_group_end", base.get("end", 0.0)) or 0.0)
        return parsed_segments

    def _uses_exact_full_block_subtitle_background(self) -> bool:
        """Whether the live ASS path needs exact, measured vector geometry."""
        try:
            return bool(
                getattr(self, "subtitle_background_cb", None)
                and self.subtitle_background_cb.isChecked()
                and getattr(self, "subtitle_background_width_combo", None)
                and str(self.subtitle_background_width_combo.currentData() or self.subtitle_background_width_combo.currentText()).strip().lower()
                in {"full_area", "full subtitle area", "full block"}
            )
        except Exception:
            return False

    def _build_live_subtitle_ass_snapshot(self, segments):
        """Capture all Qt-owned state before an ASS worker is started."""
        video_path = self.video_path_edit.text().strip()
        source_width = max(1, int(getattr(self.video_view, "video_source_width", 0) or 1920))
        source_height = max(1, int(getattr(self.video_view, "video_source_height", 0) or 1080))
        canvas_width, canvas_height = self._subtitle_render_dimensions()
        subtitle_style = copy.deepcopy(self.get_subtitle_export_style(segments=segments))
        if subtitle_style.get("custom_position_enabled") and (canvas_width != source_width or canvas_height != source_height):
            try:
                scale_mode = self.get_output_scale_mode_key()
                focus_x, focus_y = self.get_output_fill_focus()
                scale = max(canvas_width / source_width, canvas_height / source_height) if scale_mode == "fill" else min(canvas_width / source_width, canvas_height / source_height)
                displayed_w, displayed_h = source_width * scale, source_height * scale
                offset_x = (canvas_width - displayed_w) * (focus_x if scale_mode == "fill" else 0.5)
                offset_y = (canvas_height - displayed_h) * (focus_y if scale_mode == "fill" else 0.5)
                x_canvas = float(subtitle_style.get("custom_position_x", 50.0)) * canvas_width / 100.0
                y_canvas = float(subtitle_style.get("custom_position_y", 86.0)) * canvas_height / 100.0
                subtitle_style["custom_position_x"] = max(0.0, min(100.0, (x_canvas - offset_x) * 100.0 / displayed_w))
                subtitle_style["custom_position_y"] = max(0.0, min(100.0, (y_canvas - offset_y) * 100.0 / displayed_h))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        signature = (video_path, source_width, source_height, repr(segments), repr(subtitle_style))
        return {
            "segments": copy.deepcopy(list(segments or [])),
            "video_width": source_width,
            "video_height": source_height,
            "style": subtitle_style,
            "signature": signature,
            "preview_dir": self.get_project_temp_dir("preview"),
        }

    @staticmethod
    def _write_subtitle_ass_from_snapshot(snapshot: dict, srt_path: str) -> str:
        """Worker-safe ASS generation.  It intentionally touches no Qt state."""
        from subtitle_builder import generate_srt
        generate_srt(snapshot["segments"], srt_path)
        style = snapshot["style"]
        return srt_to_ass(
            srt_path,
            video_width=snapshot["video_width"], video_height=snapshot["video_height"],
            alignment=style.get("alignment", 2), margin_v=style.get("margin_v", 30),
            font_name=style.get("font_name", "Arial"), font_size=style.get("font_size", 18),
            font_color=style.get("font_color", "&H00FFFFFF"), background_box=style.get("background_box", False),
            animation_style=style.get("animation", "Static"), highlight_color=style.get("highlight_color", "&H00FFFFFF"),
            outline_color=style.get("outline_color", "&H00000000"), outline_width=style.get("outline_width", 2.0),
            shadow_color=style.get("shadow_color", "&H80000000"), shadow_depth=style.get("shadow_depth", 1.0),
            background_color=style.get("background_color", "&H80000000"), background_alpha=style.get("background_alpha", 0.5),
            background_width=style.get("background_width", "fit_text"), background_shape=style.get("background_shape", "rectangle"),
            background_padding=style.get("background_padding", 6), background_radius=style.get("background_radius", 0),
            bold=style.get("bold", False), preset_key=style.get("preset_key", ""),
            auto_keyword_highlight=style.get("auto_keyword_highlight", False), animation_duration=style.get("animation_duration", 0.22),
            manual_highlights=style.get("manual_highlights", []), word_timings=style.get("word_timings", []),
            speaker_colors=style.get("speaker_colors", []), custom_position_enabled=style.get("custom_position_enabled", False),
            custom_position_x=style.get("custom_position_x", 50), custom_position_y=style.get("custom_position_y", 86),
            custom_position_bottom_y=style.get("custom_position_bottom_y"), single_line=style.get("single_line", False),
            font_scale=style.get("font_scale", 1.0), log_generation=False,
        )

    def _schedule_deferred_subtitle_ass_build(self, segments) -> bool:
        """Queue exact libass measurement after editing settles; never block UI."""
        if not self._uses_exact_full_block_subtitle_background() or not segments:
            return False
        snapshot = self._build_live_subtitle_ass_snapshot(segments)
        if snapshot["signature"] == getattr(self, "_live_preview_signature", None):
            return True
        self._subtitle_ass_request_token = int(getattr(self, "_subtitle_ass_request_token", 0)) + 1
        snapshot["token"] = self._subtitle_ass_request_token
        self._subtitle_ass_pending_snapshot = snapshot
        timer = getattr(self, "subtitle_ass_debounce_timer", None)
        if timer:
            timer.start()
        else:
            self._start_deferred_subtitle_ass_build()
        return True

    def _start_deferred_subtitle_ass_build(self):
        snapshot = getattr(self, "_subtitle_ass_pending_snapshot", None)
        if not snapshot:
            return
        self._subtitle_ass_pending_snapshot = None
        token = int(snapshot["token"])
        preview_dir = snapshot["preview_dir"]
        os.makedirs(preview_dir, exist_ok=True)
        srt_path = os.path.join(preview_dir, f"live_preview_subtitle_{token}.srt")

        def _worker():
            try:
                ass_path = self._write_subtitle_ass_from_snapshot(snapshot, srt_path)
                self.subtitle_ass_ready.emit(token, srt_path, ass_path, snapshot["signature"])
            except Exception as exc:
                self.runtime_log_received.emit(f"[Subtitle Background] Exact libass layout failed: {exc}")
                self.subtitle_ass_ready.emit(token, "", "", snapshot["signature"])

        worker = threading.Thread(target=_worker, name=f"subtitle-ass-{token}", daemon=True)
        self._subtitle_ass_worker_threads = [thread for thread in getattr(self, "_subtitle_ass_worker_threads", []) if thread.is_alive()]
        self._subtitle_ass_worker_threads.append(worker)
        worker.start()

    def _on_async_subtitle_ass_ready(self, token: int, srt_path: str, ass_path: str, signature):
        """Apply only the newest completed exact layout on the Qt thread."""
        if int(token) != int(getattr(self, "_subtitle_ass_request_token", 0)):
            return
        if not ass_path or not os.path.exists(ass_path):
            return
        self.live_preview_subtitle_path = srt_path
        self.live_preview_ass_path = ass_path
        self._live_preview_signature = signature
        self.processed_artifacts["subtitle_preview_srt"] = srt_path
        self.processed_artifacts["subtitle_preview_ass"] = ass_path
        try:
            self.media_player.set_subtitle_file(ass_path)
            self._loaded_live_ass_path = ass_path
            self._loaded_live_ass_signature = signature
            self._set_subtitle_item_text_rendering(False)
            self.update_playback_subtitle_highlight(int(self.media_player.position() or 0))
        except Exception as exc:
            self.runtime_log_received.emit(f"[Subtitle Background] Could not apply exact layout: {exc}")

    def _write_live_preview_assets(self, segments):
        if not segments:
            self.live_preview_subtitle_path = ""
            self.live_preview_ass_path = ""
            self._live_preview_signature = None
            self._loaded_live_ass_path = ""
            self._loaded_live_ass_signature = None
            return "", ""

        # Full-block geometry is measured from libass itself.  Scheduling it
        # here keeps all callers (style controls, playback callbacks, project
        # load) non-blocking.  The previous exact track stays visible while a
        # newer style is being measured after the debounce interval.
        if self._uses_exact_full_block_subtitle_background():
            self._schedule_deferred_subtitle_ass_build(segments)
            return self.live_preview_subtitle_path, self.live_preview_ass_path

        # A pending Full Block worker must never re-apply an older ASS file
        # after the user switches the background off (or back to Fit Text).
        self._subtitle_ass_request_token = int(getattr(self, "_subtitle_ass_request_token", 0)) + 1

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
        source_width = max(1, int(getattr(self.video_view, "video_source_width", 0) or 1920))
        source_height = max(1, int(getattr(self.video_view, "video_source_height", 0) or 1080))
        canvas_width, canvas_height = self._subtitle_render_dimensions()
        subtitle_style = self.get_subtitle_export_style(segments=segments)
        # MPV/libass renders subtitles on the source frame before MPV applies
        # its Fit/Fill presentation transform. Convert custom canvas-relative
        # anchors back into source coordinates so the visible result follows
        # the same point after framing.
        if subtitle_style.get("custom_position_enabled") and (canvas_width != source_width or canvas_height != source_height):
            try:
                scale_mode = self.get_output_scale_mode_key()
                focus_x, focus_y = self.get_output_fill_focus()
                scale = max(canvas_width / source_width, canvas_height / source_height) if scale_mode == "fill" else min(canvas_width / source_width, canvas_height / source_height)
                displayed_w, displayed_h = source_width * scale, source_height * scale
                offset_x = (canvas_width - displayed_w) * (focus_x if scale_mode == "fill" else 0.5)
                offset_y = (canvas_height - displayed_h) * (focus_y if scale_mode == "fill" else 0.5)
                x_canvas = float(subtitle_style.get("custom_position_x", 50.0)) * canvas_width / 100.0
                y_canvas = float(subtitle_style.get("custom_position_y", 86.0)) * canvas_height / 100.0
                subtitle_style["custom_position_x"] = max(0.0, min(100.0, (x_canvas - offset_x) * 100.0 / displayed_w))
                subtitle_style["custom_position_y"] = max(0.0, min(100.0, (y_canvas - offset_y) * 100.0 / displayed_h))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        video_width, video_height = source_width, source_height
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
            background_width=subtitle_style.get("background_width", "fit_text"),
            background_shape=subtitle_style.get("background_shape", "rectangle"),
            background_padding=subtitle_style.get("background_padding", 6),
            background_radius=subtitle_style.get("background_radius", 0),
            bold=subtitle_style.get("bold", False),
            preset_key=subtitle_style.get("preset_key", ""),
            auto_keyword_highlight=subtitle_style.get("auto_keyword_highlight", False),
            animation_duration=subtitle_style.get("animation_duration", 0.22),
            manual_highlights=subtitle_style.get("manual_highlights", []),
            word_timings=subtitle_style.get("word_timings", []),
            speaker_colors=subtitle_style.get("speaker_colors", []),
            custom_position_enabled=subtitle_style.get("custom_position_enabled", False),
            custom_position_x=subtitle_style.get("custom_position_x", 50),
            custom_position_y=subtitle_style.get("custom_position_y", 86),
            custom_position_bottom_y=subtitle_style.get("custom_position_bottom_y"),
            single_line=subtitle_style.get("single_line", False),
            font_scale=subtitle_style.get("font_scale", 1.0),
            log_generation=False,
        )
        self._live_preview_signature = preview_signature
        self.processed_artifacts["subtitle_preview_srt"] = self.live_preview_subtitle_path
        self.processed_artifacts["subtitle_preview_ass"] = self.live_preview_ass_path
        return self.live_preview_subtitle_path, self.live_preview_ass_path

    def _resolve_live_preview_segments(self):
        single_line = bool(getattr(self, "subtitle_single_line_cb", None) and self.subtitle_single_line_cb.isChecked())
        if single_line and self.current_translated_segments:
            return self.get_active_segments(), "translated"

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
        active = self._find_active_segment_indices(position_ms, segments)
        return active[0] if active else -1

    def _find_active_segment_indices(self, position_ms: int, segments) -> list[int]:
        """Return the indices of every segment whose [start, end] contains
        position_ms. Multiple entries are returned when segments overlap in
        time, so the live overlay can stack them on separate lines.
        """
        position_seconds = max(0.0, float(position_ms) / 1000.0)
        # ``segments`` is already the editor's indexed list. Avoid copying a
        # long TS1 list on each 200 ms playback tick; the cache below only
        # needs its identity and length to detect a replacement.
        source = segments or []
        cache = getattr(self, "_playback_subtitle_activity_cache", None)
        source_key = (id(segments), len(source))
        if cache and cache.get("source_key") == source_key:
            # The end is exclusive so a tick landing exactly on the next cue
            # boundary recalculates the active set immediately.
            if cache["stable_start"] <= position_seconds < cache["stable_end"]:
                return list(cache["active_indices"])

        # This full scan happens only when playback crosses a subtitle/gap
        # boundary. The cached stable interval handles the several position
        # updates that occur while a cue remains unchanged.
        result: list[int] = []
        previous_boundary = 0.0
        next_boundary = None
        for idx, seg in enumerate(source):
            if not isinstance(seg, dict):
                continue
            try:
                start_s = float(seg.get("start", 0.0))
                end_s = float(seg.get("end", 0.0))
            except (TypeError, ValueError):
                continue
            for boundary in (start_s, end_s):
                if boundary <= position_seconds:
                    previous_boundary = max(previous_boundary, boundary)
                else:
                    next_boundary = boundary if next_boundary is None else min(next_boundary, boundary)
            if start_s <= position_seconds <= end_s:
                result.append(idx)
        stable_start = previous_boundary
        stable_end = next_boundary if next_boundary is not None else float("inf")
        self._playback_subtitle_activity_cache = {
            "source_key": source_key,
            "stable_start": stable_start,
            "stable_end": stable_end,
            "active_indices": list(result),
        }
        return result

    def _set_editor_highlight(self, editor, active_index: int):
        if not editor:
            return

        document = editor.document()
        revision = int(document.revision())
        editor_key = id(editor)
        state = (revision, active_index)
        if self._editor_highlight_state.get(editor_key) == state:
            return
        self._editor_highlight_state[editor_key] = state

        selections = []
        cached = self._editor_highlight_chunks.get(editor_key)
        if cached and cached[0] == revision:
            chunks = cached[1]
        else:
            text = editor.toPlainText()
            block_pattern = re.compile(
                r"(^|\n\n)(\d+\n\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}\n.*?)(?=\n\n\d+\n|\Z)",
                re.DOTALL,
            )
            chunks = [(match.start(2), match.end(2)) for match in block_pattern.finditer(text)]
            self._editor_highlight_chunks[editor_key] = (revision, chunks)

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
            if not bool(getattr(self, "_subtitle_track_preview_visible", True)):
                self.timeline.set_active_segment_index(-1)
                if hasattr(self, "video_view"):
                    self.video_view.subtitle_item.hide()
                return
            segments = self.live_preview_segments or self.get_active_segments()
            active_index = self._find_active_segment_index(position_ms, segments)
            self.timeline.set_active_segment_index(active_index)
            inspector_visible = self._is_subtitle_inspector_details_visible()
            # Playback may update its last position even while paused.  Do
            # not let that stale position overwrite a subtitle the user has
            # just selected on TS1 (which commonly reset the inspector to
            # segment 1 at position 0).  Follow playback only while it is
            # actually running.
            is_playing = self._preview_is_playing()
            if is_playing and hasattr(self.timeline, "_timeline") and self.timeline._timeline:
                # Review Mode follows the active subtitle track so the
                # Subtitle Inspector can refresh with the cue under the
                # playhead. This intentionally overrides a paused edit
                # selection only while playback is running.
                subtitle_layer_id = ""
                if active_index >= 0:
                    for layer_id, segment_index in getattr(self.timeline, "_segment_indices", {}).items():
                        if int(segment_index) == int(active_index):
                            subtitle_layer_id = str(layer_id)
                            break
                if not subtitle_layer_id:
                    for track in self.timeline._timeline.tracks:
                        track_type = str(getattr(getattr(track, "type", ""), "value", getattr(track, "type", ""))).lower()
                        if track_type not in {"subtitle", "dub_subtitle"} and str(getattr(track, "name", "")) != "TS1":
                            continue
                        if track.layers:
                            subtitle_layer_id = str(getattr(track.layers[0], "id", "") or "")
                            break
                if subtitle_layer_id and str(getattr(self.timeline, "_selected_layer_id", "") or "") != subtitle_layer_id:
                    self.timeline._selected_layer_id = subtitle_layer_id
                    self.on_timeline_layer_selected(subtitle_layer_id)
            # Keep the selected TS1 cue synchronized with the playhead during
            # review playback even when the inspector was previously focused
            # on another panel.  set_selected_segment_index() updates the
            # inspector when it is visible and remains harmless otherwise.
            if is_playing and active_index >= 0 and active_index != getattr(self, "_selected_segment_index", -1):
                self.set_selected_segment_index(active_index, sync_ui=True)

            if inspector_visible:
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
                    if self.video_view.subtitle_item.isVisible():
                        self.video_view.subtitle_item.hide()
                else:
                    active_indices = self._find_active_segment_indices(position_ms, segments)
                    if active_indices:
                        active_lines = [segments[i].get("text", "") for i in active_indices]
                        if len(active_lines) == 1:
                            self.video_view.subtitle_item.set_text(active_lines[0])
                        else:
                            self.video_view.subtitle_item.set_lines(active_lines)
                        self._apply_live_subtitle_segment_color(segments[active_indices[0]])
                        self._set_live_subtitle_effects(segments[active_indices[0]], position_ms)
                        if not self.video_view.subtitle_item.isVisible():
                            self.video_view.subtitle_item.show()
                    else:
                        # Keep a real subtitle visible while paused so it
                        # remains a draggable editing layer after subtitle
                        # generation, even if the playhead is between cues.
                        if not self.media_player.is_playing():
                            self._show_subtitle_drag_layer(segments)
                        else:
                             self.video_view.subtitle_item.set_text("")
                             if self.video_view.subtitle_item.isVisible():
                                 self.video_view.subtitle_item.hide()
                self.video_view.reposition_subtitle()
        except Exception as exc:
            self.log(f"[Preview] subtitle highlight skipped: {exc}")

    def _show_subtitle_drag_layer(self, segments=None):
        """Show a representative live subtitle as the paused drag target."""
        if not hasattr(self, "video_view") or getattr(self, "_preview_video_has_burned_subtitles", False):
            return
        items = list(segments or self.live_preview_segments or self.get_active_segments() or [])
        if not items:
            return
        index = int(getattr(self, "_selected_segment_index", -1))
        if not (0 <= index < len(items)):
            index = 0
        text = str(items[index].get("text", "") or "").strip()
        if not text:
            return
        self.video_view.subtitle_item.set_text(text)
        self._apply_live_subtitle_segment_color(items[index])
        self._set_live_subtitle_effects(items[index])
        self.video_view.subtitle_item.show()
        self.video_view.reposition_subtitle()

    def sync_live_subtitle_preview(self):
        """Synchronize the live subtitle renderer and draggable Qt target."""
        if not hasattr(self, "media_player"):
            return
        if not bool(getattr(self, "_subtitle_track_preview_visible", True)):
            self.media_player.clear_subtitle()
            if hasattr(self, "video_view"):
                self.video_view.subtitle_item.set_text("")
                self.video_view.subtitle_item.hide()
            return
        if getattr(self, "_preview_video_has_burned_subtitles", False):
            self.media_player.clear_subtitle()
            if hasattr(self, "video_view"):
                self.video_view.subtitle_item.set_text("")
                self.video_view.subtitle_item.hide()
            return
        can_render_libass = bool(
            getattr(self, "_use_libass_live_preview", False)
            and hasattr(self.media_player, "set_subtitle_file")
            and hasattr(self.media_player, "_sub_track_id")
        )
        if can_render_libass:
            segments, editor_name = self._resolve_live_preview_segments()
            if segments:
                self.live_preview_segments = list(segments)
                self.live_preview_editor_name = editor_name
                _srt_path, ass_path = self._write_live_preview_assets(segments)
                if ass_path and os.path.exists(ass_path):
                    # Re-adding an unchanged MPV subtitle track can briefly
                    # stall playback.  Only reload it once the final ASS path
                    # actually changes.
                    live_track_id = getattr(self.media_player, "_sub_track_id", -1)
                    has_live_mpv_track = isinstance(live_track_id, int) and live_track_id >= 0
                    if (
                        ass_path != getattr(self, "_loaded_live_ass_path", "")
                        or getattr(self, "_loaded_live_ass_signature", None) != getattr(self, "_live_preview_signature", None)
                        or not has_live_mpv_track
                    ):
                        self.media_player.set_subtitle_file(ass_path)
                        self._loaded_live_ass_path = ass_path
                        self._loaded_live_ass_signature = getattr(self, "_live_preview_signature", None)
                    self._set_subtitle_item_text_rendering(False)
                    position = int(self.media_player.position() or 0)
                    self.update_playback_subtitle_highlight(position)
                    return
            self.media_player.clear_subtitle()
            if hasattr(self, "video_view"):
                self.video_view.subtitle_item.set_text("")
                self.video_view.subtitle_item.hide()
            return
        self.media_player.clear_subtitle()
        self._set_subtitle_item_text_rendering(True)
        position = 0
        try:
            position = int(self.media_player.position())
        except Exception:
            pass
        self.update_playback_subtitle_highlight(position)

    def refresh_ui_state(self):
        """Basic enable/disable rules to guide user flow."""
        # Legacy Audio Source controls are kept only for compatibility with
        # older project/settings data.  Re-assert their hidden state on every
        # UI refresh so playback, project loading, or mode changes cannot
        # accidentally make the obsolete radio buttons appear.
        self._hide_legacy_audio_source_controls()
        review_mode = self._preview_is_playing()
        v_ok = bool(self.video_path_edit.text().strip()) and os.path.exists(self.video_path_edit.text().strip())
        a_ok = bool(self.audio_source_edit.text().strip()) and os.path.exists(self.audio_source_edit.text().strip())
        has_translated_text = bool(self.translated_text.toPlainText().strip())
        translation_ready = self._translation_phase_complete()
        selected_audio_path = self.resolve_selected_audio_path()
        has_voice_audio = bool(selected_audio_path and os.path.exists(selected_audio_path))
        has_subtitle_track = bool(self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path))
        mode = self.get_output_mode_key()
        steps = getattr(getattr(self, "current_project_state", None), "steps", {}) or {}
        voice_running = steps.get("generate_tts") == "running" or steps.get("mix_audio") == "running"
        # Translation is sufficient for a final subtitle-only export.  TTS
        # remains optional: if it has not been generated, Export and Fast
        # Preview retain the source audio and burn the translated subtitles.
        # Voice-only projects without subtitles keep their historical rule.
        can_export = v_ok and (
            has_subtitle_track
            or (mode == "voice" and has_voice_audio)
        )

        self.extract_btn.setEnabled(v_ok)
        self.vocal_sep_btn.setEnabled(a_ok)
        if hasattr(self, "voice_timing_sync_combo") and hasattr(self, "voice_speed_spin"):
            sync_mode = self.voice_timing_sync_combo.currentText().strip().lower()
            self.voice_speed_spin.setEnabled(sync_mode != "off")
        self.transcribe_btn.setEnabled(a_ok)
        self.translate_btn.setEnabled(bool(self.transcript_text.toPlainText().strip()))
        self.apply_translated_btn.setEnabled(translation_ready and has_translated_text)
        if hasattr(self, "rewrite_translation_btn"):
            self.rewrite_translation_btn.setVisible(translation_ready)
            self.rewrite_translation_btn.setEnabled(
                translation_ready and bool(self.transcript_text.toPlainText().strip()) and has_translated_text
            )
        if hasattr(self, "subtitle_editor_btn"):
            self.subtitle_editor_btn.setVisible(translation_ready)
            self.subtitle_editor_btn.setEnabled(translation_ready and not review_mode)
        if hasattr(self, "normalizer_dict_btn"):
            self.normalizer_dict_btn.setVisible(translation_ready)
            self.normalizer_dict_btn.setEnabled(translation_ready and not review_mode)
        if hasattr(self, "rewrite_selected_segment_btn"):
            has_selected_segment = 0 <= int(getattr(self, "_selected_segment_index", -1)) < len(self.current_translated_segments or [])
            self.rewrite_selected_segment_btn.setEnabled(
                translation_ready and bool(self.transcript_text.toPlainText().strip()) and has_translated_text and has_selected_segment
            )
        if hasattr(self, "_refresh_audio_inspector_dub_voice_buttons"):
            self._refresh_audio_inspector_dub_voice_buttons()
        generated_mode = not self.using_existing_audio_source()
        if hasattr(self, "voiceover_btn"):
            self.voiceover_btn.setEnabled(translation_ready and has_translated_text and generated_mode and mode in ("voice", "both"))
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
            elif self._is_realtime_color_filter_state():
                status_text = "Realtime MPV preview active."
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
            self.blur_area_btn.setEnabled(can_export and not review_mode)
        if hasattr(self, "add_music_layer_btn"):
            # Music is an audio track, not a visual overlay; it can be added
            # as soon as a source video is selected, but never while Review
            # Mode is active or a voice/render worker is running.
            source_video = self._resolve_preview_original_video_path()
            self.add_music_layer_btn.setEnabled(bool(source_video) and not review_mode and not voice_running)
        # Overlay tracks are only meaningful once the generated output is
        # ready. Keep their controls disabled before that point so users
        # cannot create layers against an incomplete video workflow.
        self._optional_layer_controls_ready = bool(can_export and not voice_running and not review_mode)
        for button_name in ("blur_add_btn", "add_logo_btn", "add_mask_btn", "add_text_btn"):
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(self._optional_layer_controls_ready)
        # Subtitle segments are valid as soon as a transcript/translation is
        # available. Keep the shared + Layer menu usable for manual fixes
        # without unlocking the unrelated overlay-layer actions early.
        if hasattr(self, "add_layer_btn"):
            has_subtitle_segments = bool(self.current_segments or self.current_translated_segments)
            self.add_layer_btn.setEnabled((self._optional_layer_controls_ready or has_subtitle_segments) and not review_mode)
        if hasattr(self, "blur_add_btn"):
            self.blur_add_btn.setEnabled(
                self._optional_layer_controls_ready
                and not bool(getattr(self, "_filter_thumbnail_visible", False))
            )
        if hasattr(self, "ocr_region_btn"):
            self.ocr_region_btn.setEnabled(v_ok)
        if hasattr(self, "ocr_translator_btn"):
            self.ocr_translator_btn.setEnabled(v_ok)
        self._sync_blur_controls()
        voice_controls_enabled = generated_mode and (mode in ("voice", "both") or not hasattr(self, "output_mode_combo"))
        if hasattr(self, "free_voice_combo"):
            self.free_voice_combo.setEnabled(voice_controls_enabled)
        if hasattr(self, "voice_engine_combo"):
            self.voice_engine_combo.setEnabled(voice_controls_enabled)
        if hasattr(self, "premium_voice_combo"):
            self.premium_voice_combo.setEnabled(False)
        if hasattr(self, "bg_music_edit"):
            self.bg_music_edit.setEnabled(generated_mode and mode in ("voice", "both"))
        if hasattr(self, "mixed_audio_edit"):
            self.mixed_audio_edit.setEnabled(mode in ("voice", "both") and bool(hasattr(self, "use_existing_audio_radio") and self.use_existing_audio_radio.isChecked()))
        if hasattr(self, "preview_voice_btn"):
            self.preview_voice_btn.setVisible(True)
            self.preview_voice_btn.setEnabled(bool(self.voice_catalog_entries_all))
        has_timeline_segments = bool(self.get_active_segments())
        selected_overlay_is_splittable = False
        selected_layer_locked = False
        has_selected_timeline_layer = False
        selected_layer_id = str(getattr(getattr(self, "timeline", None), "_selected_layer_id", "") or "")
        if selected_layer_id and getattr(getattr(self, "timeline", None), "_timeline", None):
            for track in self.timeline._timeline.tracks:
                for layer in track.layers:
                    if layer.id != selected_layer_id:
                        continue
                    has_selected_timeline_layer = True
                    selected_layer_locked = bool(getattr(track, "locked", False)) or bool(getattr(layer, "locked", False))
                    layer_type = str(getattr(getattr(layer, "type", ""), "value", getattr(layer, "type", ""))).lower()
                    selected_overlay_is_splittable = layer_type in {"blur", "mask", "text"} or (
                        layer_type == "image" and str(getattr(track, "name", "")) == "L1 Logo"
                    )
                    break
                if selected_overlay_is_splittable:
                    break
        if hasattr(self, "timeline_split_btn"):
            self.timeline_split_btn.setEnabled(
                (has_timeline_segments or selected_overlay_is_splittable)
                and (not has_selected_timeline_layer or not selected_layer_locked)
                and not review_mode
            )
        if hasattr(self, "timeline_delete_btn"):
            self.timeline_delete_btn.setEnabled(
                has_timeline_segments and (not has_selected_timeline_layer or not selected_layer_locked)
                and not review_mode
            )

        # Keep the timeline readable and fully seekable during playback, but
        # make every state-changing control unavailable in Review Mode.
        if review_mode:
            for button_name in (
                "timeline_undo_btn", "timeline_redo_btn", "timeline_selection_mode_btn",
                "timeline_clear_selection_btn", "timeline_alt_transcribe_btn",
            ):
                button = getattr(self, button_name, None)
                if button is not None:
                    button.setEnabled(False)
        else:
            self._refresh_timeline_history_buttons()
            selection_exists = bool(getattr(self.timeline, "selection_range", lambda: None)()) if hasattr(self, "timeline") else False
            if hasattr(self, "timeline_selection_mode_btn"):
                self.timeline_selection_mode_btn.setEnabled(bool(v_ok))
            if hasattr(self, "timeline_clear_selection_btn"):
                self.timeline_clear_selection_btn.setEnabled(selection_exists)
            if hasattr(self, "timeline_alt_transcribe_btn"):
                self.timeline_alt_transcribe_btn.setVisible(selection_exists)
                self.timeline_alt_transcribe_btn.setEnabled(selection_exists and not bool(getattr(self, "_alternate_range_transcription_worker", None)))
        if hasattr(self, "inspector_stack"):
            self.inspector_stack.setEnabled(not review_mode)
        # Lock only the layout handle while playing.  The splitter's child
        # widgets remain enabled so playback/seek controls still work.
        splitter = getattr(self, "preview_timeline_splitter", None)
        if splitter is not None:
            try:
                splitter.handle(1).setEnabled(not review_mode)
            except Exception:
                pass

        self._update_generate_button_menu(has_data=has_translated_text or has_timeline_segments)
        self.update_workflow_stage_badges()

        if hasattr(self, "clean_project_action"):
            self.clean_project_action.setEnabled(self._has_cleanable_project_data())
        self.run_all_btn.setEnabled(v_ok and not self._pipeline_active)
        self.preview_frame_btn.setEnabled(v_ok and bool(self.get_active_segments()))
        self.preview_5s_btn.setEnabled(v_ok)
        if hasattr(self, "preview_5s_action"):
            self.preview_5s_action.setEnabled(v_ok)
        self.export_btn.setEnabled(can_export)
        if hasattr(self, "download_subtitle_action"):
            self.download_subtitle_action.setEnabled(bool(self.translated_text.toPlainText().strip()))
        if hasattr(self, "download_original_action"):
            self.download_original_action.setEnabled(bool(self.transcript_text.toPlainText().strip()))
        if hasattr(self, "tabs"):
            self.tabs.setTabEnabled(1, v_ok)
            self.tabs.setTabEnabled(2, v_ok and mode in ("voice", "both"))
        # Audio Source and related controls are needed before Transcript, so
        # the workflow Audio tab must be available as soon as a video is
        # selected—not only after the optional TTS stage completes.
        if hasattr(self, "audio_tab_btn"):
            self.audio_tab_btn.setEnabled(v_ok)
        self.update_workflow_availability()
        self.update_guidance_panel()
        self._update_ocr_overlay()

    def _update_generate_button_menu(self, has_data: bool):
        if not hasattr(self, "run_all_btn"):
            return
        btn = self.run_all_btn
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction
        if btn.menu() is None:
            menu = QMenu(btn)
            menu.setObjectName("generateMenu")
            # The parent menu renders the Step-by-Step / Full Pipeline
            # submenu titles, so it needs its own width—not just the child
            # popup menus.
            menu.setMinimumWidth(220)
            step_menu = menu.addMenu("Step-by-Step")
            step_menu.setObjectName("generateStepMenu")
            step_menu.setMinimumWidth(220)
            transcript_action = QAction("Run to Transcript", step_menu)
            transcript_action.triggered.connect(lambda: self.run_pipeline_to_stage("transcript"))
            translate_menu = step_menu.addMenu("Run to Translate")
            translate_menu.setObjectName("generateStepMenu")
            translate_menu.setMinimumWidth(220)
            translate_action = QAction("Auto Translate", translate_menu)
            translate_action.triggered.connect(lambda: self.run_pipeline_to_stage("translate"))
            import_translation_action = QAction("Import Translated File…", translate_menu)
            import_translation_action.triggered.connect(self.import_translated_srt)
            translate_menu.addActions([translate_action, import_translation_action])
            tts_menu = step_menu.addMenu("Generate Voice / TTS")
            tts_menu.setObjectName("generateStepMenu")
            tts_menu.setMinimumWidth(220)
            tts_action = QAction("TTS", tts_menu)
            tts_action.triggered.connect(lambda: self.run_pipeline_to_stage("tts"))
            tts_skip_action = QAction("Skip", tts_menu)
            tts_skip_action.triggered.connect(self.skip_tts_stage)
            tts_menu.addActions([tts_action, tts_skip_action])
            step_menu.insertAction(translate_menu.menuAction(), transcript_action)
            step_menu.addAction(tts_menu.menuAction())
            full_menu = menu.addMenu("Full Pipeline")
            full_menu.setObjectName("generateStepMenu")
            full_menu.setMinimumWidth(220)
            full_action = QAction("Run full pipeline", full_menu)
            full_action.triggered.connect(self.run_all_pipeline)
            full_menu.addAction(full_action)
            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.InstantPopup)
            btn.setText("Generate")
            self._generate_transcript_action = transcript_action
            self._generate_translate_action = translate_action
            self._generate_import_translated_srt_action = import_translation_action
            self._generate_tts_action = tts_action
            self._generate_tts_skip_action = tts_skip_action
        self.update_workflow_stage_badges()

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
                self._allow_post_pipeline_preview_assets = False
                self.load_project_context(self.current_project_state)
                self.media_player.pause()
                self.media_player.setPosition(0)
                self.refresh_ui_state()
                self.sync_live_subtitle_preview()
                event.acceptProposedAction()
                return
        event.ignore()

    def run_extraction(self):
        v_path = self.video_path_edit.text()
        if not v_path: return
        
        target_dir = self.audio_folder_edit.text()
        file_basename = os.path.splitext(os.path.basename(v_path))[0]
        a_path = os.path.join(target_dir, file_basename + ".wav")
        
        print(f"[Extraction] start: video={v_path} audio={a_path}")
        self.progress_bar.setValue(10)
        self.update_project_step("extract_audio", "running")
        self.extraction_thread = ExtractionWorker(v_path, a_path)
        self.extraction_thread.finished.connect(self.on_extraction_finished)
        self.extraction_thread.start()

    def on_extraction_finished(self, success, path):
        print(f"[Extraction] finished: success={success} path={path}")
        self.progress_bar.setValue(30)
        self.extract_btn.setEnabled(True)
        if success:
            self.last_extracted_audio = path
            self.audio_source_edit.setText(path)
            self.processed_artifacts["audio_extracted"] = path
            self.update_project_artifact("extracted_audio", path)
            self.update_project_step("extract_audio", "done")
            self.log(f"[Audio] Original audio extracted: {path}")
            self.schedule_timeline_visual_refresh(waveform=True, thumbnails=False)
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
            self.last_no_vocals_path = music
            self.processed_artifacts["vocals"] = vocal
            self.update_project_artifact("vocals", vocal)
            if music:
                self.processed_artifacts["no_vocals"] = music
                self.update_project_artifact("no_vocals", music)
            self.update_project_step("separate_audio", "done")
            QMessageBox.information(self, "Success", 
                f"Audio stems separated!\n\nVocals: {os.path.basename(vocal)}\nBackground: {os.path.basename(music)}\n\nVocals are now selected for transcription.")
            self._pipeline_advance("separation")
        else:
            self.update_project_step("separate_audio", "failed")
            self._pipeline_fail("Separation did not produce output.")
        self.refresh_ui_state()

    def run_transcription(self):
        is_ocr = self.get_transcription_engine() == "ocr"
        if not self.ensure_required_resources(
            "Transcription",
            include_whisper=not is_ocr,
            include_ocr=is_ocr,
            validate_pipeline_runtime=True,
        ):
            return
        self.subtitle_controller.run_transcription()

    def on_transcription_finished(self, segments, error=""):
        self.subtitle_controller.on_transcription_finished(segments, error)

    def run_translation(self):
        self.subtitle_controller.run_translation()

    def on_translation_finished(self, translated_srt, error, fallback_notice=""):
        self.subtitle_controller.on_translation_finished(translated_srt, error, fallback_notice)

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
        selected = str(getattr(self, "selected_whisper_model_name", "auto") or "auto").strip().lower()
        is_gpu_mode = os.environ.get("CAPCAP_DEVICE", "cuda").strip().lower() == "cuda"
        if not is_gpu_mode and selected == "medium":
            selected = "auto"
        if selected and selected != "auto":
            return selected
        model_root = os.path.join(self.workspace_root, "models", "faster_whisper")
        preferred_models = ("medium", "small", "base", "tiny") if is_gpu_mode else ("small", "base", "tiny")
        for candidate in preferred_models:
            model_dir = os.path.join(model_root, candidate)
            if os.path.isdir(model_dir) and any(
                name.endswith(".bin") for name in os.listdir(model_dir)
            ):
                return candidate
            snapshots_dir = os.path.join(
                model_root,
                f"models--Systran--faster-whisper-{candidate}",
                "snapshots",
            )
            if os.path.isdir(snapshots_dir):
                for snapshot_name in os.listdir(snapshots_dir):
                    if os.path.isfile(os.path.join(snapshots_dir, snapshot_name, "model.bin")):
                        return candidate
        return "medium"

    def get_whisper_model_path(self) -> str:
        return os.path.join(self.workspace_root, "models", "ggml-medium.bin")

    def open_model_settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.setModal(True)
        dialog.setMinimumWidth(580)
        parent_height = self.height() if (hasattr(self, "height") and self.height() > 200) else 850
        dialog.setMinimumHeight(int(parent_height * 0.80))
        dialog.resize(600, int(parent_height * 0.85))
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
            QComboBox, QLineEdit, QSpinBox {
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
        main_dialog_layout = QVBoxLayout(dialog)
        main_dialog_layout.setContentsMargins(14, 16, 14, 12)
        main_dialog_layout.setSpacing(10)

        scroll_area = QScrollArea(dialog)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #0b1220; width: 8px; border-radius: 4px; }"
            "QScrollBar::handle:vertical { background: #2f4868; border-radius: 4px; min-height: 20px; }"
            "QScrollBar::handle:vertical:hover { background: #456894; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )

        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(4, 2, 8, 2)
        layout.setSpacing(14)
        scroll_area.setWidget(content_widget)
        main_dialog_layout.addWidget(scroll_area, 1)

        remote_mode = is_remote_profile()
        # Transcription Engine Section
        engine_title = QLabel("Audio source")
        engine_title.setObjectName("statusHeadline")
        layout.addWidget(engine_title)

        engine_combo = QComboBox(dialog)
        engine_combo.addItem("Audio (SenseVoice) - Speed", "sensevoice")
        engine_combo.addItem("Audio (Whisper) - Quality", "whisper")
        engine_combo.addItem("CapCut API (Beta)", "capcut")
        engine_combo.addItem("Video (OCR)", "ocr")
        current_engine = self.get_transcription_engine()
        idx = engine_combo.findData(current_engine)
        if idx >= 0:
            engine_combo.setCurrentIndex(idx)
        layout.addWidget(engine_combo)

        # OCR Region combo (only visible when OCR selected)
        region_label = QLabel("Subtitle position:")
        region_label.setVisible(current_engine == "ocr")
        region_combo = QComboBox(dialog)
        region_combo.addItem("Bottom (default)", "bottom")
        region_combo.addItem("Top", "top")
        region_combo.addItem("Full frame", "full")
        current_region = (os.getenv("OCR_SUBTITLE_REGION") or "bottom").strip().lower()
        idx = region_combo.findData(current_region)
        if idx >= 0:
            region_combo.setCurrentIndex(idx)
        region_combo.setVisible(current_engine == "ocr")
        layout.addWidget(region_label)
        layout.addWidget(region_combo)

        sampling_label = QLabel("OCR sampling rate:")
        sampling_label.setToolTip("Higher rates catch shorter subtitle flashes but process more video frames.")
        sampling_label.setVisible(current_engine == "ocr")
        sampling_combo = QComboBox(dialog)
        sampling_combo.addItem("Auto (recommended)", "auto")
        sampling_combo.addItem("1 FPS (lighter)", "1")
        sampling_combo.addItem("1.5 FPS", "1.5")
        sampling_combo.addItem("2 FPS", "2")
        sampling_combo.addItem("3 FPS", "3")
        sampling_combo.addItem("4 FPS (short flashes)", "4")
        current_sampling_fps = str(os.getenv("OCR_SAMPLING_FPS") or "auto").strip().lower()
        idx = sampling_combo.findData(current_sampling_fps)
        sampling_combo.setCurrentIndex(idx if idx >= 0 else 0)
        sampling_combo.setVisible(current_engine == "ocr")
        layout.addWidget(sampling_label)
        layout.addWidget(sampling_combo)

        # Whisper Section
        is_whisper = current_engine == "whisper"
        whisper_title = QLabel("Whisper model")
        whisper_title.setObjectName("statusHeadline")
        whisper_title.setVisible(is_whisper)
        layout.addWidget(whisper_title)
        
        whisper_combo = QComboBox(dialog)
        whisper_combo.addItem("Base", "base")
        whisper_combo.addItem("Small (Fast)", "small")
        if os.environ.get("CAPCAP_DEVICE", "cuda").strip().lower() == "cuda":
            whisper_combo.addItem("Medium (Auto)", "medium")
        current_whisper = str(getattr(self, "selected_whisper_model_name", "auto") or "auto").strip().lower()
        if current_whisper == "auto":
            current_whisper = self.get_whisper_model_name()
        whisper_index = whisper_combo.findData(current_whisper)
        whisper_combo.setCurrentIndex(whisper_index if whisper_index >= 0 else 0)
        whisper_combo.setVisible(is_whisper)
        layout.addWidget(whisper_combo)

        # CapCut API Settings Section
        is_capcut = (current_engine == "capcut") or (str(getattr(self, "selected_voice_engine", "") or (self.voice_engine_combo.currentData() if hasattr(self, "voice_engine_combo") else "")).strip().lower() == "capcut")
        capcut_settings_widget = QWidget(dialog)
        capcut_settings_layout = QVBoxLayout(capcut_settings_widget)
        capcut_settings_layout.setContentsMargins(0, 0, 0, 0)
        capcut_settings_layout.setSpacing(10)

        capcut_title = QLabel("CapCut API Settings")
        capcut_title.setObjectName("statusHeadline")
        capcut_settings_layout.addWidget(capcut_title)

        capcut_chunk_row = QHBoxLayout()
        capcut_chunk_label = QLabel("STT Chunk duration:")
        capcut_chunk_combo = QComboBox(dialog)
        capcut_chunk_combo.addItem("5 minutes (default)", "300")
        capcut_chunk_combo.addItem("10 minutes", "600")
        capcut_chunk_combo.addItem("15 minutes", "900")
        capcut_chunk_combo.addItem("20 minutes", "1200")
        capcut_chunk_combo.addItem("30 minutes", "1800")
        current_chunk = str(os.getenv("CAPCUT_STT_CHUNK_SECONDS", self.settings.value("capcut_stt_chunk_seconds", "300"))).strip()
        idx = capcut_chunk_combo.findData(current_chunk)
        capcut_chunk_combo.setCurrentIndex(idx if idx >= 0 else 0)
        capcut_chunk_row.addWidget(capcut_chunk_label)
        capcut_chunk_row.addWidget(capcut_chunk_combo, 1)
        capcut_settings_layout.addLayout(capcut_chunk_row)

        capcut_stt_workers_row = QHBoxLayout()
        capcut_stt_workers_label = QLabel("STT Workers:")
        capcut_stt_workers_combo = QComboBox(dialog)
        for w in ("1", "2", "3", "4", "5", "8", "10"):
            capcut_stt_workers_combo.addItem(f"{w} workers" + (" (default)" if w == "5" else "") if w != "1" else "1 worker", w)
        current_stt_workers = str(os.getenv("CAPCUT_STT_WORKERS", self.settings.value("capcut_stt_workers", "5"))).strip()
        idx = capcut_stt_workers_combo.findData(current_stt_workers)
        capcut_stt_workers_combo.setCurrentIndex(idx if idx >= 0 else 4)
        capcut_stt_workers_row.addWidget(capcut_stt_workers_label)
        capcut_stt_workers_row.addWidget(capcut_stt_workers_combo, 1)
        capcut_settings_layout.addLayout(capcut_stt_workers_row)

        capcut_tts_batch_row = QHBoxLayout()
        capcut_tts_batch_label = QLabel("TTS Batch size:")
        capcut_tts_batch_combo = QComboBox(dialog)
        for b in ("1", "4", "8", "16", "30", "32", "60"):
            capcut_tts_batch_combo.addItem(f"{b} line / req" if b == "1" else (f"{b} lines / req" + (" (default)" if b == "60" else "")), b)
        current_tts_batch = str(os.getenv("CAPCUT_TTS_BATCH_SIZE", self.settings.value("capcut_tts_batch_size", "60"))).strip()
        idx = capcut_tts_batch_combo.findData(current_tts_batch)
        capcut_tts_batch_combo.setCurrentIndex(idx if idx >= 0 else 6)
        capcut_tts_batch_row.addWidget(capcut_tts_batch_label)
        capcut_tts_batch_row.addWidget(capcut_tts_batch_combo, 1)
        capcut_settings_layout.addLayout(capcut_tts_batch_row)

        capcut_tts_workers_row = QHBoxLayout()
        capcut_tts_workers_label = QLabel("TTS Workers:")
        capcut_tts_workers_combo = QComboBox(dialog)
        for w in ("1", "5", "10", "15", "20", "30", "45", "60"):
            capcut_tts_workers_combo.addItem(f"{w} workers" + (" (default)" if w == "30" else "") if w != "1" else "1 worker", w)
        current_tts_workers = str(os.getenv("CAPCUT_TTS_WORKERS", self.settings.value("capcut_tts_workers", "30"))).strip()
        idx = capcut_tts_workers_combo.findData(current_tts_workers)
        capcut_tts_workers_combo.setCurrentIndex(idx if idx >= 0 else 5)
        capcut_tts_workers_row.addWidget(capcut_tts_workers_label)
        capcut_tts_workers_row.addWidget(capcut_tts_workers_combo, 1)
        capcut_settings_layout.addLayout(capcut_tts_workers_row)

        capcut_hint = QLabel("Batching subtitle lines and running concurrent workers speeds up dubbing by 5-10x and reduces HTTP requests.")
        capcut_hint.setObjectName("helperLabel")
        capcut_hint.setWordWrap(True)
        capcut_settings_layout.addWidget(capcut_hint)

        capcut_settings_widget.setVisible(is_capcut)
        layout.addWidget(capcut_settings_widget)

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
        ai_title = QLabel("AI Translation")
        ai_title.setObjectName("statusHeadline")
        ai_title.setVisible(not remote_mode)
        layout.addWidget(ai_title)

        provider_layout = QHBoxLayout()
        provider_label = QLabel("Translator Provider:")
        provider_label.setVisible(not remote_mode)
        provider_layout.addWidget(provider_label)
        provider_combo = QComboBox(dialog)
        provider_combo.addItem("Google Translate (free, no key)", "google")
        provider_combo.addItem("Google AI Studio", "google_ai_studio")
        provider_combo.addItem("OpenAI", "openai")
        provider_combo.addItem("Ollama (Local)", "ollama")
        current_provider = (os.getenv("OPENAI_PROVIDER") or "google").strip().lower()
        if current_provider == "gemini":
            current_provider = "google_ai_studio"
        if current_provider not in {"google", "google_ai_studio", "openai", "ollama"}:
            current_provider = "google"
        idx = provider_combo.findData(current_provider)
        if idx >= 0:
            provider_combo.setCurrentIndex(idx)
        provider_combo.setVisible(not remote_mode)
        provider_layout.addWidget(provider_combo, 1)
        layout.addLayout(provider_layout)

        def _provider_values(provider):
            if provider == "google_ai_studio":
                legacy = str(os.getenv("OPENAI_PROVIDER") or "").strip().lower() == "gemini"
                return (
                    os.getenv("GOOGLE_AI_STUDIO_API_KEY", "") or (os.getenv("OPENAI_API_KEY", "") if legacy else ""),
                    os.getenv("GOOGLE_AI_STUDIO_MODEL", "") or (os.getenv("OPENAI_MODEL", "") if legacy else ""),
                    os.getenv("GOOGLE_AI_STUDIO_BASE_URL", "") or (os.getenv("OPENAI_BASE_URL", "") if legacy else ""),
                )
            return (os.getenv("OPENAI_API_KEY", ""), os.getenv("OPENAI_MODEL", ""), os.getenv("OPENAI_BASE_URL", ""))

        initial_key, initial_model, initial_base_url = _provider_values(current_provider)

        key_section_widget = QWidget(dialog)
        key_layout = QVBoxLayout(key_section_widget)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_label = QLabel("API Key:")
        key_edit = QLineEdit(dialog)
        key_edit.setEchoMode(QLineEdit.Password)
        key_edit.setText(initial_key)
        key_layout.addWidget(key_label)
        key_layout.addWidget(key_edit)
        key_section_widget.setVisible(not remote_mode)
        layout.addWidget(key_section_widget)

        model_layout = QVBoxLayout()
        model_label = QLabel("AI Model:")
        model_edit = QLineEdit(dialog)
        model_edit.setText(initial_model)
        model_layout.addWidget(model_label)
        model_layout.addWidget(model_edit)
        model_label.setVisible(not remote_mode)
        model_edit.setVisible(not remote_mode)
        layout.addLayout(model_layout)

        base_url_layout = QVBoxLayout()
        base_url_label = QLabel("API URL:")
        base_url_edit = QLineEdit(dialog)
        base_url_edit.setText(initial_base_url)
        base_url_layout.addWidget(base_url_label)
        base_url_layout.addWidget(base_url_edit)
        base_url_label.setVisible(not remote_mode)
        base_url_edit.setVisible(not remote_mode)
        layout.addLayout(base_url_layout)

        batch_size_layout = QHBoxLayout()
        batch_size_label = QLabel("Batch Size (Lines/req):")
        batch_size_spin = QSpinBox(dialog)
        batch_size_spin.setRange(5, 200)
        batch_size_spin.setSingleStep(5)
        env_batch = str(os.getenv("CAPCAP_AI_TRANSLATION_MAX_SEGMENTS", "")).strip()
        if env_batch.isdigit() and int(env_batch) > 0:
            batch_size_spin.setValue(int(env_batch))
        else:
            batch_size_spin.setValue(40 if current_provider == "ollama" else 80)
        batch_size_layout.addWidget(batch_size_label)
        batch_size_layout.addWidget(batch_size_spin, 1)
        batch_size_label.setVisible(not remote_mode)
        batch_size_spin.setVisible(not remote_mode)
        layout.addLayout(batch_size_layout)

        batch_size_hint = QLabel("Subtitle lines sent per AI request (Recommended: 80 for Gemini/OpenAI, 20-40 for Ollama).")
        batch_size_hint.setObjectName("helperLabel")
        batch_size_hint.setWordWrap(True)
        batch_size_hint.setVisible(not remote_mode)
        layout.addWidget(batch_size_hint)

        provider_hint = QLabel("Get an API key at https://aistudio.google.com/apikey")
        provider_hint.setObjectName("helperLabel")
        provider_hint.setWordWrap(True)
        provider_hint.setVisible(not remote_mode)
        layout.addWidget(provider_hint)

        def _toggle_visible(widget, visible):
            widget.setVisible(visible)

        def update_provider_fields():
            p = provider_combo.currentData()
            model_edit.setPlaceholderText("")
            is_ai = p != "google"
            is_google_ai_studio = p == "google_ai_studio"
            is_openai = p == "openai"
            is_ollama = p == "ollama"
            is_google = p == "google"
            _toggle_visible(key_section_widget, is_google_ai_studio or is_openai)
            _toggle_visible(base_url_label, not remote_mode and is_ai)
            _toggle_visible(base_url_edit, not remote_mode and is_ai)
            _toggle_visible(batch_size_label, not remote_mode and is_ai)
            _toggle_visible(batch_size_spin, not remote_mode and is_ai)
            _toggle_visible(batch_size_hint, not remote_mode and is_ai)
            _toggle_visible(test_btn, not remote_mode and is_ai)
            _toggle_visible(test_status, not remote_mode and is_ai)
            _toggle_visible(model_label, not remote_mode and is_ai)
            _toggle_visible(model_edit, not remote_mode and is_ai)
            if is_google:
                provider_hint.setText("Free Google web translate, no API key needed. Lower quality than AI translation.")
                key_edit.clear()
                model_edit.clear()
                base_url_edit.clear()
            elif is_google_ai_studio:
                model_label.setText("AI Model:")
                key, model, base_url = _provider_values(p)
                key_edit.setText(key)
                model_edit.setText(model)
                base_url_edit.setText(base_url or "https://generativelanguage.googleapis.com/v1beta/openai/")
                if not model_edit.text().strip():
                    model_edit.setText("gemini-3.7-flash")
                if batch_size_spin.value() == 40:
                    batch_size_spin.setValue(80)
                provider_hint.setText("Use a Google AI Studio Gemini API key: https://aistudio.google.com/apikey")
            elif is_openai:
                model_label.setText("AI Model:")
                key, model, base_url = _provider_values(p)
                key_edit.setText(key)
                model_edit.setText(model)
                base_url_edit.setText(base_url or "https://api.openai.com/v1/")
                if not model_edit.text().strip():
                    model_edit.setText("gpt-4o-mini")
                if batch_size_spin.value() == 40:
                    batch_size_spin.setValue(80)
                provider_hint.setText("Get an API key at https://platform.openai.com/api-keys")
            elif p == "ollama":
                model_label.setText("AI Model:")
                base_url_edit.setText("http://localhost:11434/v1")
                key_edit.clear()
                model_edit.setText("gemma4:31b-cloud")
                if batch_size_spin.value() == 80:
                    batch_size_spin.setValue(40)
                provider_hint.setText("Requires a running Ollama server. Default model: gemma4:31b-cloud")
            model_edit.setReadOnly(False)
            dialog.layout().invalidate()
            dialog.adjustSize()

        test_btn = QPushButton("Test Connection", dialog)
        test_btn.setVisible(not remote_mode)
        test_status = QLabel("")
        test_status.setObjectName("helperLabel")
        test_status.setVisible(not remote_mode)
        test_row = QHBoxLayout()
        test_row.addWidget(test_btn)
        test_row.addWidget(test_status, 1)
        layout.addLayout(test_row)

        def test_ai_connection():
            url = base_url_edit.text().strip()
            provider = provider_combo.currentData()
            key = key_edit.text().strip() or ("ollama" if provider == "ollama" else "")
            model = model_edit.text().strip()
            if not url:
                test_status.setText("Enter a server URL first.")
                return
            if not key:
                test_status.setText("Enter an API key first.")
                return
            if not model:
                test_status.setText("Enter a model name first.")
                return
            test_status.setText("Testing...")
            test_status.repaint()
            try:
                from openai import OpenAI
                client = OpenAI(api_key=key, base_url=url, timeout=15.0)
                # A tiny completion validates the endpoint, credential, and the
                # selected model.  Some compatible APIs do not expose /models.
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Reply with OK."}],
                    max_tokens=8,
                )
                test_status.setText(f"Connected: {model}")
            except Exception as e:
                if provider == "ollama":
                    self.log(f"[Ollama] Connection test failed: {e}")
                    test_status.setText("Unable to connect to Ollama. Please check your connection and settings.")
                else:
                    test_status.setText(f"Failed: {e}")

        test_btn.clicked.connect(test_ai_connection)

        provider_combo.currentIndexChanged.connect(update_provider_fields)
        update_provider_fields()

        def update_engine_fields():
            engine_val = engine_combo.currentData()
            is_ocr = engine_val == "ocr"
            is_whisper = engine_val == "whisper"
            is_capcut_engine = engine_val == "capcut"
            is_capcut_voice = str(getattr(self, "selected_voice_engine", "") or (self.voice_engine_combo.currentData() if hasattr(self, "voice_engine_combo") else "")).strip().lower() == "capcut"
            _toggle_visible(whisper_title, is_whisper)
            _toggle_visible(whisper_combo, is_whisper)
            _toggle_visible(region_label, is_ocr)
            _toggle_visible(region_combo, is_ocr)
            _toggle_visible(sampling_label, is_ocr)
            _toggle_visible(sampling_combo, is_ocr)
            _toggle_visible(capcut_settings_widget, is_capcut_engine or is_capcut_voice)
            content_widget.layout().invalidate()

        engine_combo.currentIndexChanged.connect(update_engine_fields)
        update_engine_fields()

        local_download_layout = QHBoxLayout()
        manage_resources_btn = QPushButton("Manage Resources", dialog)
        open_voices_folder_btn = QPushButton("Open Voices Folder", dialog)
        local_download_layout.addWidget(manage_resources_btn)
        local_download_layout.addWidget(open_voices_folder_btn)
        manage_resources_btn.setVisible(not remote_mode)
        open_voices_folder_btn.setVisible(not remote_mode)
        layout.addLayout(local_download_layout)

        def _piper_models_dir() -> str:
            return models_path("piper")

        def open_voices_folder():
            voices_dir = _piper_models_dir()
            os.makedirs(voices_dir, exist_ok=True)
            open_folder_impl(self, voices_dir)

        open_voices_folder_btn.clicked.connect(open_voices_folder)
        manage_resources_btn.clicked.connect(self.open_resource_manager_dialog)
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

        # Buttons
        button_row = QHBoxLayout()
        button_row.setContentsMargins(8, 4, 8, 4)
        button_row.addStretch()
        cancel_btn = QPushButton("Cancel", dialog)
        save_btn = QPushButton("Save", dialog)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)
        main_dialog_layout.addLayout(button_row)

        cancel_btn.clicked.connect(dialog.reject)
        save_btn.clicked.connect(dialog.accept)

        # The subtitle is a top-level overlay above MPV's native surface.
        # Hide it for this modal dialog so it cannot paint over Settings.
        subtitle_item = getattr(getattr(self, "video_view", None), "subtitle_item", None)
        text_overlay = getattr(getattr(self, "video_view", None), "text_overlay", None)
        subtitle_was_visible = bool(subtitle_item is not None and subtitle_item.isVisible())
        if subtitle_item is not None:
            subtitle_item.set_suppressed(True)
        if text_overlay is not None:
            text_overlay.set_suppressed(True)
        dialog_result = dialog.exec()
        if dialog_result != QDialog.Accepted:
            if subtitle_item is not None:
                subtitle_item.set_suppressed(False)
            if text_overlay is not None:
                text_overlay.set_suppressed(False)
            if subtitle_was_visible and not getattr(self, "_preview_video_has_burned_subtitles", False):
                QTimer.singleShot(0, self.sync_live_subtitle_preview)
            return

        # Save Logic
        new_whisper = str(whisper_combo.currentData() or "small").strip().lower()
        new_engine = str(engine_combo.currentData() or "sensevoice").strip().lower()
        new_ocr_region = str(region_combo.currentData() or "bottom").strip().lower()
        new_ocr_sampling_fps = str(sampling_combo.currentData() or "auto").strip().lower()
        new_key = key_edit.text().strip()
        new_model = model_edit.text().strip()
        new_provider = str(provider_combo.currentData()).strip()
        new_base_url = base_url_edit.text().strip()

        self.selected_whisper_model_name = new_whisper

        new_capcut_chunk = str(capcut_chunk_combo.currentData() or "300").strip()
        new_capcut_stt_workers = str(capcut_stt_workers_combo.currentData() or "5").strip()
        new_capcut_tts_batch = str(capcut_tts_batch_combo.currentData() or "60").strip()
        new_capcut_tts_workers = str(capcut_tts_workers_combo.currentData() or "30").strip()

        os.environ["CAPCUT_STT_CHUNK_SECONDS"] = new_capcut_chunk
        os.environ["CAPCUT_STT_WORKERS"] = new_capcut_stt_workers
        os.environ["CAPCUT_TTS_BATCH_SIZE"] = new_capcut_tts_batch
        os.environ["CAPCUT_TTS_WORKERS"] = new_capcut_tts_workers

        self.settings.setValue("capcut_stt_chunk_seconds", new_capcut_chunk)
        self.settings.setValue("capcut_stt_workers", new_capcut_stt_workers)
        self.settings.setValue("capcut_tts_batch_size", new_capcut_tts_batch)
        self.settings.setValue("capcut_tts_workers", new_capcut_tts_workers)

        # Transcription engine settings (apply to all modes)
        # The subtitle source is project-local.  Do not write it into .env,
        # otherwise opening another project can inherit a stale OCR/Audio
        # choice from an earlier session.
        _engine_updates = {
            "OCR_SUBTITLE_REGION": new_ocr_region,
            "OCR_SAMPLING_FPS": new_ocr_sampling_fps,
            "CAPCUT_STT_CHUNK_SECONDS": new_capcut_chunk,
            "CAPCUT_STT_WORKERS": new_capcut_stt_workers,
            "CAPCUT_TTS_BATCH_SIZE": new_capcut_tts_batch,
            "CAPCUT_TTS_WORKERS": new_capcut_tts_workers,
        }
        
        # Write back to .env
        env_lines = []
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                env_lines = f.readlines()
        
        if remote_mode:
            updates = {
                "CAPCAP_REMOTE_API_URL": remote_url_edit.text().strip() or "http://127.0.0.1:8765",
                "CAPCAP_REMOTE_API_TOKEN": remote_token_edit.text().strip(),
            }
        else:
            new_batch_size = str(batch_size_spin.value())
            if new_provider == "google":
                updates = {
                    "AI_POLISHER_PROVIDER": "google",
                    "OPENAI_PROVIDER": "google",
                }
            elif new_provider == "google_ai_studio":
                updates = {
                    "AI_POLISHER_PROVIDER": "google_ai_studio",
                    "OPENAI_PROVIDER": "google_ai_studio",
                    "GOOGLE_AI_STUDIO_API_KEY": new_key,
                    "GOOGLE_AI_STUDIO_MODEL": new_model or "gemini-3.7-flash",
                    "GOOGLE_AI_STUDIO_BASE_URL": new_base_url or "https://generativelanguage.googleapis.com/v1beta/openai/",
                    "CAPCAP_AI_TRANSLATION_MAX_SEGMENTS": new_batch_size,
                }
            elif new_provider == "ollama":
                updates = {
                    "AI_POLISHER_PROVIDER": "ollama",
                    "OPENAI_PROVIDER": "ollama",
                    "OPENAI_API_KEY": "ollama",
                    "OPENAI_MODEL": new_model,
                    "OPENAI_BASE_URL": new_base_url or "http://localhost:11434/v1",
                    "CAPCAP_AI_TRANSLATION_MAX_SEGMENTS": new_batch_size,
                }
            else:
                updates = {
                    "AI_POLISHER_PROVIDER": "openai",
                    "OPENAI_PROVIDER": "openai",
                    "OPENAI_API_KEY": new_key,
                    "OPENAI_MODEL": new_model or "gpt-4o-mini",
                    "OPENAI_BASE_URL": new_base_url or "https://api.openai.com/v1/",
                    "CAPCAP_AI_TRANSLATION_MAX_SEGMENTS": new_batch_size,
                }
        
        updates.update(_engine_updates)

        new_env_lines = []
        handled_keys = set()
        for line in env_lines:
            match = re.match(r"^([^=]+)=.*", line)
            if match:
                k = match.group(1).strip()
                if k == "TRANSCRIPTION_ENGINE":
                    # Legacy global cache: source selection now belongs to
                    # the active project and must not survive here.
                    continue
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

        self.set_project_transcription_engine(new_engine)
        self.save_user_settings()
        self._update_ocr_overlay()
        QMessageBox.information(self, "Success", "Settings saved and updated!")
        if subtitle_item is not None:
            subtitle_item.set_suppressed(False)
        if text_overlay is not None:
            text_overlay.set_suppressed(False)
        if subtitle_was_visible and not getattr(self, "_preview_video_has_burned_subtitles", False):
            QTimer.singleShot(0, self.sync_live_subtitle_preview)

    def apply_edited_translation(self, show_message=True, force_apply=True):
        result = self.subtitle_controller.apply_edited_translation(show_message=show_message, force_apply=force_apply)
        if result:
            self.refresh_auto_keyword_highlights()
            self.sync_segment_editor_rows()
            return result

    def open_subtitle_editor(self):
        """Open the staged, bulk translated-subtitle editor.

        Unlike the small inspector editor this does not alter the project on
        every keystroke.  It makes text-only changes explicit via Update.
        """
        if not self._translation_phase_complete():
            QMessageBox.information(self, "Subtitle Editor", "Complete the Translation phase before editing translated subtitles.")
            return
        segments = list(self.current_translated_segments or [])
        if not segments:
            QMessageBox.information(
                self,
                "Subtitle Editor",
                "Translated subtitles are not available yet. Run Translate or import a translated SRT first.",
            )
            return
        editor_segments = copy.deepcopy(segments)
        # Translation artifacts can intentionally omit source_text.  The
        # source transcript remains index/timing aligned, so enrich only the
        # editor copy for display without changing project metadata.
        source_segments = list(self.current_segments or [])
        source_by_time = {
            (round(float(item.get("start", 0.0) or 0.0), 3), round(float(item.get("end", 0.0) or 0.0), 3)): item
            for item in source_segments
            if isinstance(item, dict)
        }
        for index, segment in enumerate(editor_segments):
            if not str(segment.get("source_text") or segment.get("original_text") or "").strip():
                key = (
                    round(float(segment.get("start", 0.0) or 0.0), 3),
                    round(float(segment.get("end", 0.0) or 0.0), 3),
                )
                source = source_by_time.get(key) or (source_segments[index] if index < len(source_segments) else {})
                source_text = str(source.get("text", "") or "").strip()
                if source_text:
                    segment["source_text"] = source_text
        dialog = SubtitleEditorDialog(
            self,
            editor_segments,
            self._apply_subtitle_editor_changes,
            self.run_rewrite_translation,
        )
        self._subtitle_editor_dialog = dialog
        try:
            dialog.exec()
        finally:
            if getattr(self, "_subtitle_editor_dialog", None) is dialog:
                self._subtitle_editor_dialog = None

    def _invalidate_dubbed_output_after_subtitle_edit(self):
        """Stop old TTS/mix output from being used after subtitle edits.

        Per-cue cache files deliberately remain: VoiceWorkflow keys those
        files by text, voice and speed, so unchanged cues are cache hits on
        the next TTS run.  The old assembled voice/mix is invalid because it
        still contains deleted or changed cues and must never be exported.
        """
        state = self.ensure_current_project()
        had_dubbed_output = bool(
            getattr(self, "last_voice_vi_path", "")
            or getattr(self, "last_mixed_vi_path", "")
            or self.processed_artifacts.get("voice_vi")
            or self.processed_artifacts.get("mixed_vi")
        )
        for key in ("voice_vi", "mixed_vi", "voice_segments"):
            self.processed_artifacts.pop(key, None)
            if state is not None:
                state.artifacts.pop(key, None)
        # The TS1 layer objects may still contain paths to the old assembled
        # voice track.  Clear them too so neither preview nor export can use
        # a deleted/obsolete cue before the next TTS run.
        timeline_model = getattr(getattr(self, "timeline", None), "_timeline", None)
        for track in list(getattr(timeline_model, "tracks", []) or []):
            track_type = str(getattr(getattr(track, "type", ""), "value", getattr(track, "type", ""))).lower()
            if track_type not in {"dub_subtitle", "subtitle"}:
                continue
            for layer in list(getattr(track, "layers", []) or []):
                if hasattr(layer, "audio_path"):
                    layer.audio_path = ""
                if hasattr(layer, "tts_settings"):
                    layer.tts_settings = {}
                metadata = getattr(layer, "metadata", None)
                if isinstance(metadata, dict):
                    metadata.pop("_audio_end", None)
        self.last_voice_vi_path = ""
        self.last_mixed_vi_path = ""
        if state is not None:
            state.set_step_status("generate_tts", "pending")
            state.set_step_status("mix_audio", "pending")
            state.settings.pop("voice_signature", None)
            self.project_service.save_project(state)
        if had_dubbed_output:
            self.log("[Subtitle Editor] Existing dubbed output invalidated; unchanged cues remain in the TTS cache.")
            self.sync_preview_audio_track_to_output(apply_to_player=True, force=True)

    def _apply_subtitle_editor_changes(self, rows) -> bool:
        """Apply staged content/deletion changes without rewriting cue metadata."""
        source = list(self.current_translated_segments or [])
        if len(rows or []) != len(source):
            QMessageBox.warning(self, "Subtitle Editor", "The subtitle list changed while the editor was open. Reopen it and try again.")
            return False

        updated = []
        changed_count = 0
        deleted_count = 0
        for row, original in zip(rows, source):
            if bool(row.get("deleted")):
                deleted_count += 1
                continue
            text = str(row.get("text", "") or "").strip()
            if not text:
                QMessageBox.warning(self, "Subtitle Editor", "Use Delete for an unnecessary segment instead of leaving translated text empty.")
                return False
            segment = copy.deepcopy(original)
            old_text = str(segment.get("text", "") or "").strip()
            if text != old_text:
                changed_count += 1
                segment["text"] = text
                segment["subtitle_vi"] = text
                # A changed subtitle must speak the changed text.  Do not
                # retain a manual voice override from the old sentence.
                segment["tts_text"] = ""
                segment["dubbing_vi"] = ""
                segment["voice_edited"] = False
                self._reconcile_manual_highlights(segment)
            updated.append(segment)

        if not changed_count and not deleted_count:
            return True

        self.current_translated_segments = updated
        self.current_translated_segment_models = self._dict_segments_to_models(updated, translated=True)
        self._single_line_split_cache = None
        self._voiceover_force_refresh = bool(changed_count or deleted_count)
        self._sync_hidden_translated_text_from_segments()
        self.refresh_auto_keyword_highlights(force=True)
        self.apply_segments_to_timeline()
        self._invalidate_dubbed_output_after_subtitle_edit()
        self.persist_current_timeline_project_data()
        # Keep the project-facing translated SRT in sync as well as the
        # JSON/timeline state. Export can then use the edited result without
        # relying on a later preview refresh to rewrite it incidentally.
        self._regenerate_translated_srt_from_segments()
        if not updated:
            # persist_current_timeline_project_data intentionally skips an
            # empty list. Persist an explicit empty translation artifact so
            # a project reopened after "Delete All" cannot resurrect its
            # former subtitles from translation_final.json.
            self.persist_translation_project_data([], self.last_translated_srt_path)
        self.schedule_live_subtitle_preview_refresh()
        self.schedule_auto_frame_preview()
        self.sync_segment_editor_rows()
        self.refresh_ui_state()
        self.log(
            f"[Subtitle Editor] Updated translated subtitles: changed={changed_count}, deleted={deleted_count}, unchanged={len(updated) - changed_count}."
        )
        QMessageBox.information(
            self,
            "Subtitle Editor Updated",
            f"Updated {changed_count} subtitle segment(s); deleted {deleted_count}.\n"
            "Timeline timing, speaker assignments, and styles were preserved.\n"
            "Run TTS again only if you need dubbed audio; unchanged lines reuse their cache.",
        )
        return True



    def setup_media_player(self):
        if getattr(self, "_media_backend_ready", False):
            return
        previous_speed = getattr(self, "_preview_speed", 1.0)
        setup_media_player_impl(self)
        self._preview_speed = previous_speed
        self._media_backend_ready = True
        if hasattr(self, "media_player"):
            try:
                self.media_player.set_playback_rate(previous_speed)
            except Exception:
                pass

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
            return self._apply_speaker_voice_assignments(self.parse_srt_to_segments(translated_srt)) if translated_srt else []

        grouped_segments = []
        idx = 0
        while idx < len(source_segments):
            segment = dict(source_segments[idx])
            group_id = str(segment.get('tts_group_id', '') or '').strip()
            tts_text = self._resolve_segment_voice_text(segment)
            if not group_id:
                segment['text'] = tts_text
                segment['tts_text'] = str(segment.get('tts_text') or '').strip() if bool(segment.get('voice_edited')) else ''
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

            voice_text = ""
            voice_edited = False
            for item in group_items:
                if bool(item.get('voice_edited')):
                    candidate_text = " ".join(str(item.get('tts_text') or item.get('dubbing_vi') or '').split()).strip()
                    if candidate_text:
                        voice_text = candidate_text
                        voice_edited = True
                        break
            if not voice_text:
                voice_text = ' '.join(
                    ' '.join(str(item.get('text') or '').split()).strip()
                    for item in group_items
                ).strip()

            grouped_segments.append({
                'start': float(group_items[0].get('tts_group_start', group_items[0].get('start', 0.0)) or group_items[0].get('start', 0.0)),
                'end': float(group_items[-1].get('tts_group_end', group_items[-1].get('end', 0.0)) or group_items[-1].get('end', 0.0)),
                'text': voice_text,
                'tts_text': voice_text if voice_edited else '',
                'tts_group_id': group_id,
                'voice_edited': voice_edited,
                'source_text': ' '.join(
                    ' '.join(str(item.get('source_text') or item.get('text') or '').split()).strip()
                    for item in group_items
                ).strip(),
                'speaker': str(group_items[0].get('speaker', '') or ''),
            })
            idx = cursor
        return self._apply_speaker_voice_assignments(grouped_segments)

    def run_voiceover(self):
        if not self.ensure_required_resources("Voice generation", include_voice=True):
            if getattr(self, "_pipeline_active", False):
                self._pipeline_fail("Missing resources")
            return
        state = self.ensure_current_project()
        if state and not self.translated_text.toPlainText().strip():
            self.load_project_context(state)

        translated_srt = self.translated_text.toPlainText().strip()
        if not translated_srt:
            if getattr(self, "_pipeline_active", False):
                self._pipeline_fail("No translated SRT available")
            QMessageBox.warning(self, "Error", "No translated SRT available. Please run translation first (STEP 3).")
            return

        segments = self._get_voiceover_segments()
        if not segments:
            if getattr(self, "_pipeline_active", False):
                self._pipeline_fail("Translated SRT could not be parsed to segments")
            QMessageBox.warning(self, "Error", "Translated SRT could not be parsed to segments.")
            return

        out_dir = self.voice_output_folder_edit.text().strip() or os.path.join(self.workspace_root, "output")
        bg_path = self.resolve_background_audio_path()
        audio_handling_mode = self.get_audio_handling_mode()
        voice_name = self._resolve_active_voice_name(persist_new_clone=True)
        if not voice_name:
            if getattr(self, "_pipeline_active", False):
                self._pipeline_fail("Missing voice")
            QMessageBox.warning(self, "Missing Voice", "Choose a voice first.")
            return
        if state is not None and state.settings.get("tts_skipped", False):
            # Starting TTS explicitly re-enables the generated voice path.
            state.set_setting("tts_skipped", False)
            self.project_service.save_project(state)
        voice_speed = self._parse_voice_speed_value()
        timing_sync_mode = str(self.voice_timing_sync_combo.currentText()).strip()
        original_volume = int(self.audio_a1_volume_slider.value()) if hasattr(self, "audio_a1_volume_slider") else 50
        dub_volume = int(self.audio_a2_volume_slider.value()) if hasattr(self, "audio_a2_volume_slider") else 100
        voice_signature = self.build_current_voice_signature(segments=segments, background_path=bg_path)
        if state and voice_signature:
            force_refresh = bool(getattr(self, "_voiceover_force_refresh", False))
            cached_voice_signature = str(state.settings.get("voice_signature", "") or "").strip()
            cached_voice_track = self._normalize_local_file_path(state.artifacts.get("voice_vi", "") or self.last_voice_vi_path)
            cached_mixed_track = self._normalize_local_file_path(state.artifacts.get("mixed_vi", "") or self.last_mixed_vi_path)
            # TTS generation caches the standalone voice track.  Music and
            # volume are composed later for preview/export, so a Music Layer
            # must not force a second TTS synthesis just because no legacy
            # mixed_vi artifact exists.
            required_output = cached_voice_track
            self.log(
                f"[Voiceover] Cache check: force={force_refresh}, "
                f"cached_sig={'<empty>' if not cached_voice_signature else cached_voice_signature[:16]+'...'}, "
                f"new_sig={'<empty>' if not voice_signature else voice_signature[:16]+'...'}, "
                f"match={cached_voice_signature == voice_signature}, "
                f"required_output={required_output}, exists={os.path.exists(required_output) if required_output else False}"
            )
            if not force_refresh and cached_voice_signature == voice_signature and required_output and os.path.exists(required_output):
                self.last_voice_vi_path = cached_voice_track if cached_voice_track and os.path.exists(cached_voice_track) else self.last_voice_vi_path
                self.last_mixed_vi_path = cached_mixed_track if cached_mixed_track and os.path.exists(cached_mixed_track) else ""
                if self.last_voice_vi_path:
                    self.processed_artifacts["voice_vi"] = self.last_voice_vi_path
                    self.update_project_artifact("voice_vi", self.last_voice_vi_path)
                    self.update_project_step("generate_tts", "done")
                if bg_path:
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
        except Exception:
            pass

        if hasattr(self, "voiceover_btn"):
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
            original_volume,
            dub_volume,
            project_state_path,
            self.get_project_temp_dir("tts"),
            self.is_ai_dubbing_rewrite_enabled() and self.get_output_mode_key() in ("voice", "both"),
            self.get_ai_dubbing_style_instruction(),
            self.get_source_language_code(),
        )
        self.voice_thread.progress.connect(self.log)
        self.voice_thread.finished.connect(self.on_voiceover_finished)
        self.voice_thread.start()

    def _apply_generated_tts_texts(self, voice_segments):
        source_segments = self.current_translated_segments
        if not source_segments or not voice_segments:
            return False

        updated = False
        grouped_updates = {}
        positional_updates = []
        for seg in list(voice_segments or []):
            tts_text = ' '.join(str((seg or {}).get("tts_text") or (seg or {}).get("text") or "").split()).strip()
            if not tts_text:
                continue
            subtitle_vi = ' '.join(str((seg or {}).get("subtitle_vi") or (seg or {}).get("text") or "").split()).strip()
            dubbing_vi = ' '.join(str((seg or {}).get("dubbing_vi") or tts_text).split()).strip()
            action_taken = str((seg or {}).get("action_taken") or "").strip().lower()
            ratio = float((seg or {}).get("ratio") or 0.0)
            group_id = str((seg or {}).get("tts_group_id") or "").strip()
            try:
                new_start = float((seg or {}).get("start", 0.0))
                new_end = float((seg or {}).get("end", 0.0))
            except (TypeError, ValueError):
                new_start = new_end = None
            try:
                new_original_end = float((seg or {}).get("_original_end")) if (seg or {}).get("_original_end") is not None else None
            except (TypeError, ValueError):
                new_original_end = None
            try:
                new_audio_end = float((seg or {}).get("_audio_end")) if (seg or {}).get("_audio_end") is not None else None
            except (TypeError, ValueError):
                new_audio_end = None
            payload = {
                "tts_text": tts_text,
                "subtitle_vi": subtitle_vi,
                "dubbing_vi": dubbing_vi,
                "action_taken": action_taken,
                "ratio": ratio,
                "attempt_count": int((seg or {}).get("attempt_count") or 1),
                "start": new_start,
                "end": new_end,
                "_original_end": new_original_end,
                "_audio_end": new_audio_end,
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
            if current_tts_text != next_tts_text:
                seg["tts_text"] = next_tts_text
                updated = True
            seg["subtitle_vi"] = next_payload["subtitle_vi"]
            seg["dubbing_vi"] = next_payload["dubbing_vi"]
            seg["action_taken"] = next_payload["action_taken"]
            seg["ratio"] = next_payload["ratio"]
            seg["attempt_count"] = next_payload["attempt_count"]
            # Sync start/end from the voice workflow so the SRT reflects the
            # actual TTS audio duration (see _extend_segment_ends_to_audio).
            new_start = next_payload.get("start")
            new_end = next_payload.get("end")
            if new_start is not None and new_end is not None and new_end > new_start:
                try:
                    old_start = float(seg.get("start", 0.0))
                    old_end = float(seg.get("end", 0.0))
                except (TypeError, ValueError):
                    old_start = old_end = None
                if old_start is not None and old_end is not None:
                    if abs(new_start - old_start) > 0.01 or abs(new_end - old_end) > 0.01:
                        seg["start"] = new_start
                        seg["end"] = new_end
                        updated = True
            new_original_end = next_payload.get("_original_end")
            if new_original_end is not None:
                seg["_original_end"] = new_original_end
            new_audio_end = next_payload.get("_audio_end")
            if new_audio_end is not None:
                seg["_audio_end"] = new_audio_end
            else:
                seg.pop("_audio_end", None)
        return updated

    def _regenerate_translated_srt_from_segments(self):
        """Regenerate the project SRT from current_translated_segments.
        Called after the voice workflow extends a segment's end time to
        match the actual TTS audio duration, so the burned-in subtitle and
        the rendered audio stay in sync.
        """
        out_path = str(getattr(self, "last_translated_srt_path", "") or "").strip()
        if not out_path:
            return
        try:
            from subtitle_builder import generate_srt
            generate_srt(self.current_translated_segments, out_path)
        except Exception as exc:
            print(f"[Voice] SRT regen failed: {exc}")
            return
        self.processed_artifacts["srt_translated"] = out_path
        self.persist_translation_project_data(self.current_translated_segments, out_path)

    def on_voiceover_finished(self, voice_track, mixed, voice_segments, error):
        if hasattr(self, "voiceover_btn"):
            self.voiceover_btn.setEnabled(True)
            self.voiceover_btn.setText("Generate Voice / Mix")
        self.progress_bar.setValue(100)

        if error:
            self._voiceover_force_refresh = False
            self._pending_voice_signature = ""
            self.update_project_step("generate_tts", "failed")
            if self.bg_music_edit.text().strip():
                self.update_project_step("mix_audio", "failed")
            QMessageBox.critical(self, "Error", f"Voiceover failed:\n\n{error}")
            self._pipeline_fail("Voiceover failed.")
            self.refresh_ui_state()
            return

        if hasattr(self, "audio_tab_btn"):
            self.audio_tab_btn.setEnabled(True)

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
        elif self._music_audio_tracks():
            self.update_project_step("mix_audio", "skipped")
        if self._apply_generated_tts_texts(voice_segments):
            self._single_line_split_cache = None
            self.current_translated_segment_models = self._dict_segments_to_models(self.current_translated_segments, translated=True)
            self._sync_hidden_translated_text_from_segments()
            self.apply_segments_to_timeline()
            if hasattr(self, "timeline") and voice_track:
                self.timeline.sync_tts_track(
                    voice_track,
                    segments=self.current_translated_segments or self.current_segments,
                )
                if hasattr(self, "voice_timing_sync_combo"):
                    self.timeline.set_voice_sync_mode(self.voice_timing_sync_combo.currentText())
            self._sync_timeline_mute_to_gui()
            self._sync_audio_mix_controls_from_tracks()
            self.persist_current_timeline_project_data()
            # Regenerate the project SRT from the updated segments so it
            # reflects the actual TTS audio duration (e.g. when a segment
            # was extended in voice_workflow._extend_segment_ends_to_audio).
            self._regenerate_translated_srt_from_segments()
            self.schedule_live_subtitle_preview_refresh()
            self.sync_segment_editor_rows()
        if self.current_project_state:
            voice_signature = self.build_current_voice_signature(
                segments=self._get_voiceover_segments(),
                background_path=self.resolve_background_audio_path(),
            )
            if voice_signature:
                self.current_project_state.set_setting("voice_signature", voice_signature)
                self.project_service.save_project(self.current_project_state)
        self._voiceover_force_refresh = False
        self._pending_voice_signature = ""

        pipeline_advanced = False
        try:
            if getattr(self, "_pipeline_active", False):
                self._pipeline_advance("voiceover")
                pipeline_advanced = True
        except Exception as exc:
            self.log(f"[Voiceover] pipeline_advance failed: {exc}")
            self.refresh_ui_state()

        if mixed:
            self.log(f"[Voiceover] Generated Vietnamese voice and mixed audio: Voice={voice_track}, Mixed={mixed}")
        else:
            self.log(f"[Voiceover] Generated Vietnamese voice track: {voice_track} (No background mix created.)")

        self.schedule_timeline_visual_refresh(waveform=True, thumbnails=False)
        self.refresh_ui_state()
        self.sync_preview_audio_track_to_output()

        if not getattr(self, "_pipeline_active", False) and not pipeline_advanced:
            QMessageBox.information(
                self,
                "Success",
                "AI Voiceover generation finished successfully!\n\nThe new voice track is loaded and ready on the timeline.",
            )

    def preview_video(self):
        self.preview_controller.preview_video()

    def on_preview_ready(self, preview_path, error, styled_signature=""):
        self.preview_controller.on_preview_ready(preview_path, error, styled_signature)

    def smart_generate(self):
        if getattr(self, "_pipeline_active", False):
            return
        has_subtitles = bool(self.current_segments)
        has_translated = bool(self.current_translated_segments and self.translated_text.toPlainText().strip())
        mode = self.get_output_mode_key()
        need_voice = mode in ("voice", "both")

        if not has_subtitles or (not has_translated and mode != "voice"):
            self.run_all_pipeline()
        elif need_voice:
            self.run_voiceover_with_progress()
        else:
            self.preview_video()

    def run_voiceover_with_progress(self, target_stage="full"):
        existing = getattr(self, "voice_thread", None)
        if existing and existing.isRunning():
            try:
                if hasattr(existing, "stop"):
                    existing.stop()
                if hasattr(existing, "finished"):
                    existing.finished.disconnect()
                if hasattr(existing, "progress"):
                    existing.progress.disconnect()
            except Exception:
                pass
            try:
                existing.quit()
                existing.wait(300)
                if existing.isRunning():
                    existing.terminate()
                    existing.wait(200)
            except Exception:
                pass
            self.voice_thread = None
        self._pipeline_active = True
        self._pipeline_step = "voiceover"
        self.pipeline_controller.target_stage = str(target_stage or "full")
        if hasattr(self, "run_all_btn"):
            self.run_all_btn.setEnabled(False)
            self.run_all_btn.setText("Processing...")
        self.pipeline_controller._setup_progress_dialog(includes_separation=False)
        self.pipeline_controller.progress_dialog.skip_step("ai_process")
        self.pipeline_controller.progress_dialog.start_step("voiceover")
        self._voiceover_force_refresh = True
        self.run_voiceover()

    def run_pipeline_to_stage(self, target_stage: str):
        target_stage = str(target_stage or "full").strip().lower()
        if target_stage not in {"transcript", "translate", "tts"}:
            self.run_all_pipeline()
            return
        has_transcript = bool(self.current_segments or self.transcript_text.toPlainText().strip())
        has_translation = bool(self.current_translated_segments or self.translated_text.toPlainText().strip())
        if target_stage == "translate" and not has_transcript:
            QMessageBox.information(self, "Step-by-Step", "Complete Transcript before running Translate.")
            return
        if target_stage == "translate" and has_translation:
            # A deliberate re-translate must bypass the finished-translation
            # cache.  Keep the transcript cache intact, so this does not
            # repeat audio extraction or transcription.
            state = self.ensure_current_project()
            if state is not None:
                state.set_setting("translation_signature", "")
                state.set_step_status("translate_raw", "pending")
                state.set_step_status("refine_translation", "pending")
                self.project_service.save_project(state)
            self.log("[Translation] Re-translate requested; reusing the existing transcript.")
        if target_stage == "translate":
            # Translate is independent once TS1 exists.  Do not send this
            # request through PrepareWorkflow: that workflow begins at audio
            # extraction/transcription and can rerun OCR/ASR when a cache
            # signature changes.
            if not self.transcript_text.toPlainText().strip() and self.current_segments:
                self.transcript_text.setText(self.format_to_srt(self.current_segments))
            self.log("[Pipeline] Translate requested; using the completed transcript only.")
            self.run_translation()
            return
        if target_stage == "tts" and not has_translation:
            QMessageBox.information(self, "Step-by-Step", "Complete Translate before running Generate Voice / TTS.")
            return
        if target_stage == "tts" and has_translation:
            self.run_voiceover_with_progress(target_stage="tts")
            return
        mode = self.get_output_mode_key()
        include_voice = target_stage == "tts" and mode in ("voice", "both")
        is_ocr = self.get_transcription_engine() == "ocr"
        if not self.ensure_required_resources(
            "Generate",
            include_whisper=not is_ocr,
            include_voice=include_voice,
            include_ocr=is_ocr,
            validate_pipeline_runtime=True,
        ):
            return
        self.pipeline_controller.run_all_pipeline(target_stage=target_stage)

    def skip_tts_stage(self):
        """Explicitly finish the optional TTS phase after translation."""
        has_translation = bool(self.current_translated_segments or self.translated_text.toPlainText().strip())
        if not has_translation:
            QMessageBox.information(self, "Skip TTS", "Complete Translate before skipping Generate Voice / TTS.")
            return
        state = self.ensure_current_project()
        if state is not None:
            state.set_setting("tts_skipped", True)
            state.set_step_status("generate_tts", "skipped")
            state.set_step_status("mix_audio", "skipped")
            self.project_service.save_project(state)
        self._voiceover_force_refresh = True
        self.log("[Pipeline] Generate Voice / TTS skipped. The translated subtitle video is ready to export.")
        self.refresh_ui_state()

    def run_all_pipeline(self):
        mode = self.get_output_mode_key()
        include_voice = mode in ("voice", "both")
        is_ocr = self.get_transcription_engine() == "ocr"
        if not self.ensure_required_resources(
            "Generate",
            include_whisper=not is_ocr,
            include_voice=include_voice,
            include_ocr=is_ocr,
            validate_pipeline_runtime=True,
        ):
            return
        self.pipeline_controller.run_all_pipeline(target_stage="full")

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
        self._timeline_waveform_cache_key = None
        self._timeline_waveform_samples = []
        self._timeline_waveform_duration_s = 0.0
        self._desired_timeline_waveform_request = None
        self._timeline_video_thumb_cache_key = None
        self._timeline_video_thumbnails = []
        self._desired_timeline_thumbnail_request = None
        self._allow_post_pipeline_preview_assets = False
        self._pending_timeline_waveform_refresh = False
        self._pending_timeline_thumbnail_refresh = False
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
        if hasattr(self, "video_path_edit"):
            self.video_path_edit.clear()
        if hasattr(self, "timeline"):
            self.timeline.set_segments([])
            self.timeline.set_duration(0)
            self.timeline.set_waveform_data([], 0.0)
            self.timeline.set_video_thumbnails([])
            self.timeline.set_playing(False)
        if hasattr(self, "media_player"):
            try:
                self.media_player.clear_subtitle()
                self.media_player.stop()
                from PySide6.QtCore import QUrl
                self.media_player.setSource(QUrl())
            except Exception:
                pass
        if hasattr(self, "video_view"):
            try:
                self.video_view.clear_blur_region()
            except Exception:
                pass
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(0)
        # Force-clear segment editor directly
        self._clear_segment_editor_rows()
        self._segment_editor_rows = []
        self._selected_segment_index = -1
        self.sync_segment_editor_rows()
        self.update_progress_checklist()
        self.refresh_ui_state()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

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

    def exit_to_launcher(self):
        self._return_to_launcher(project_removed_from_recent=False)

    def clean_current_project(self):
        project_state = getattr(self, "current_project_state", None)
        if not self._has_cleanable_project_data():
            QMessageBox.information(self, "Clean Project", "There is no generated project data to clean right now.")
            return

        confirmation = QMessageBox.question(
            self,
            "Clean Project",
            "This will remove intermediate project files, temp previews, separated audio, cached TTS files, and this video's timeline media cache.\n\n"
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
            "Timeline media cache": [],
            "Launcher media cache": [],
        }
        project_temp_root = self.get_project_temp_root()
        output_root = os.path.join(self.workspace_root, "output")
        project_root = str(getattr(project_state, "project_root", "") or "").strip()
        project_id = str(getattr(project_state, "project_id", "") or "").strip()
        if not project_id and project_root:
            project_id = os.path.basename(os.path.normpath(project_root))
        project_state_path = self.project_service.project_file(project_root) if project_root else ""
        allowed_roots = [root for root in [project_temp_root, output_root, project_root] if root]

        # Stop active workers and pending persistence before deleting files.
        # Otherwise a late worker/timer can recreate the selected project's
        # cache or timeline after it has just been removed.
        self._terminate_workers()
        # Release MPV/QMediaPlayer handles before deleting extracted audio or
        # project files. Windows keeps a stopped sidecar source locked until
        # it is explicitly unloaded, which previously made the first cleanup
        # attempt report WinError 32.
        media_player = getattr(self, "media_player", None)
        if media_player is not None:
            try:
                media_player.clear_audio()
            except Exception:
                pass
            try:
                media_player._clear_original_audio()
            except Exception:
                pass
            try:
                from PySide6.QtCore import QUrl
                media_player.setSource(QUrl())
            except Exception:
                pass
            try:
                QApplication.processEvents()
            except Exception:
                pass
        persist_timer = getattr(self, "_timeline_persist_timer", None)
        if persist_timer is not None:
            persist_timer.stop()
        self._pending_timeline_persist = False
        self._pending_mask_state_persist = False
        self._pending_blur_state_persist = False

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

        # Older builds stored a few per-project temporary files directly in
        # temp/<project_id>.  Remove that exact legacy directory only; never
        # remove the shared temp root or another project's folder.
        if project_id:
            legacy_project_temp = os.path.join(self.get_workspace_temp_root(), project_id)
            before_count = len(removed_paths)
            self._remove_path_if_safe(
                legacy_project_temp,
                allowed_roots=[self.get_workspace_temp_root()],
                removed=removed_paths,
            )
            if len(removed_paths) > before_count:
                removed_groups["Temp folders"].append(removed_paths[-1])

        # V1/A1 visual assets live in the shared temp root because they are
        # prepared in the launcher before a project context exists. Remove
        # only files whose digest belongs to this source video; caches for
        # other projects remain untouched.
        source_video = self._normalize_local_file_path(
            self.video_path_edit.text().strip() if hasattr(self, "video_path_edit") else ""
        )
        if source_video:
            source = os.path.abspath(source_video)
            # Some older code paths keyed cache files by the user-provided
            # path while newer ones use the normalized absolute path. Remove
            # both keys for this one selected source video.
            digest_sources = {source, source_video}
            digests = {
                hashlib.md5(cache_source.encode("utf-8")).hexdigest()[:12]
                for cache_source in digest_sources
            }
            full_digests = {
                hashlib.md5(cache_source.encode("utf-8")).hexdigest()
                for cache_source in digest_sources
            }
            temp_root = self.get_workspace_temp_root()
            timeline_cache_paths = []
            for digest in digests:
                timeline_cache_paths.extend([
                    os.path.join(temp_root, f"waveform_{digest}.wav"),
                    os.path.join(temp_root, "timeline_visuals", f"{digest}.json"),
                ])
            thumb_dir = os.path.join(temp_root, "timeline_thumbnails")
            if os.path.isdir(thumb_dir):
                for digest in digests:
                    timeline_cache_paths.extend(
                        glob.glob(os.path.join(thumb_dir, f"launcher_{digest}_*.jpg"))
                    )
            for candidate in timeline_cache_paths:
                before_count = len(removed_paths)
                self._remove_path_if_safe(candidate, allowed_roots=[temp_root], removed=removed_paths)
                if len(removed_paths) > before_count:
                    removed_groups["Timeline media cache"].append(removed_paths[-1])

            # The launcher card thumbnail has its own full-MD5 filename.
            # It belongs solely to this source video, so cleaning this project
            # can safely remove it without touching other recent projects.
            launcher_thumb_root = os.path.join(temp_root, "launcher_thumbs")
            for digest in full_digests:
                before_count = len(removed_paths)
                self._remove_path_if_safe(
                    os.path.join(launcher_thumb_root, f"{digest}.jpg"),
                    allowed_roots=[temp_root],
                    removed=removed_paths,
                )
                if len(removed_paths) > before_count:
                    removed_groups["Launcher media cache"].append(removed_paths[-1])

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
        # The project directory above has intentionally been deleted. Do not
        # persist the in-memory timeline while returning to the launcher,
        # because that would recreate projects/<project_id>/timeline.json.
        self._return_to_launcher(
            project_removed_from_recent=True,
            persist_project_data=False,
        )

    def _return_to_launcher(self, project_removed_from_recent=True, *, persist_project_data=True):
        # Keep the complete saved timeline when returning to the launcher.
        # Optional tracks (Text, Logo, Blur, Mask) are part of the project
        # state and must be available when that project is reopened.
        if persist_project_data:
            try:
                self.persist_current_timeline_project_data()
            except Exception:
                pass
        video_path = getattr(self, "_current_video_path", "")
        if not video_path:
            video_path = os.path.normpath(self.video_path_edit.text().strip())
        self.log(f"[Clean] _return_to_launcher: video_path={video_path}")
        if video_path and project_removed_from_recent:
            try:
                from views.launcher import _load_recent_projects, _save_recent_projects
                projects = _load_recent_projects()
                projects = [p for p in projects if os.path.normpath(p.get("video_path", "")) != os.path.normpath(video_path)]
                _save_recent_projects(None, projects)
                self.log(f"[Clean] Removed from recent: {video_path} -> {len(projects)} remaining")
            except Exception as e:
                self.log(f"[Clean] Failed: {e}")
        self._current_video_path = ""
        self._terminate_workers()
        self.hide()
        QApplication.setQuitOnLastWindowClosed(False)
        QTimer.singleShot(100, _relaunch_launcher)

    def _terminate_workers(self):
        attrs = [
            "extraction_thread",
            "vocal_thread",
            "voice_thread",
            "_voice_sample_preview_thread",
            "transcription_thread",
            "_alternate_range_transcription_worker",
            "translation_thread",
            "rewrite_translation_thread",
            "prepare_workflow_thread",
            "export_thread",
            "quick_preview_thread",
            "frame_preview_thread",
            "preview_thread",
        ]
        for name in attrs:
            worker = getattr(self, name, None)
            if worker is not None and getattr(worker, "isRunning", lambda: False)():
                print(f"[Cleanup] Terminating worker: {name}")
                try:
                    worker.quit()
                    worker.wait(3000)
                    if worker.isRunning():
                        worker.terminate()
                        worker.wait(2000)
                        print(f"[Cleanup] Force-terminated {name}")
                    else:
                        print(f"[Cleanup] Graceful quit {name}")
                except Exception as e:
                    print(f"[Cleanup] Failed to terminate {name}: {e}")
        threads_dict = getattr(self, "_segment_preview_threads", None)
        if threads_dict:
            for idx, worker in list(threads_dict.items()):
                try:
                    if getattr(worker, "isRunning", lambda: False)():
                        print(f"[Cleanup] Terminating segment preview thread idx={idx}")
                        worker.quit()
                        worker.wait(3000)
                        if worker.isRunning():
                            worker.terminate()
                            worker.wait(2000)
                except Exception as e:
                    print(f"[Cleanup] Failed to terminate segment thread {idx}: {e}")
            threads_dict.clear()
        print("[Cleanup] Worker termination complete.")

    def closeEvent(self, event):
        try:
            # A drag may have ended less than one debounce interval ago.
            # Flush it before teardown so the final overlay position is not
            # lost when the window is closed immediately.
            self._flush_pending_timeline_persist()
            # Persist the current blur state BEFORE clearing the overlay.
            # Block the blurRegionChanged signal during the clear so the
            # signal handler does not overwrite the saved state with an
            # empty regions list.
            if hasattr(self, "video_view"):
                try:
                    self.video_view.blurRegionChanged.disconnect(self.on_preview_blur_region_changed)
                except Exception:
                    pass
            if hasattr(self, "persist_project_blur_state"):
                try:
                    self.persist_project_blur_state()
                except Exception:
                    pass
            if hasattr(self, "persist_project_mask_state"):
                try:
                    self.persist_project_mask_state()
                except Exception:
                    pass
            # Preserve the complete project timeline on a normal application
            # close, including Text and Logo tracks. Optional layers are
            # removed only by the explicit clean/return workflow.
            if hasattr(self, "video_view"):
                self.video_view.clear_blur_region()
            if hasattr(self, "media_player") and hasattr(self.media_player, "clear_mask_region"):
                try:
                    self.media_player.clear_mask_region()
                except Exception:
                    pass
            self.save_user_settings()
            self.cleanup_temp_preview_files()
            self._terminate_workers()
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

    def refresh_play_button_icon(self):
        """Update the play button icon + tooltip to reflect the current
        media player state (playing vs paused). Called from
        position_changed when playback ends naturally so the button
        switches from the pause icon back to the play icon."""
        if not hasattr(self, "play_btn"):
            return
        playing = False
        try:
            playing = bool(self.media_player.is_playing())
        except Exception:
            playing = False
        play_icon = "pause.svg" if playing else "play.svg"
        play_tip = "Pause preview" if playing else "Play preview"
        try:
            self.play_btn.setIcon(load_icon(asset_path("icons", play_icon), 18))
            self.play_btn.setToolTip(play_tip)
        except Exception:
            pass
        if hasattr(self, "blur_area_btn"):
            blur_active = bool(self.blur_area_btn.isChecked())
            self.blur_area_btn.setToolTip("Blur effect on" if blur_active else "Turn blur effect on or off")
        if hasattr(self, "preview_speed_combo"):
            target = float(getattr(self, "_preview_speed", 1.0))
            index = self.preview_speed_combo.findData(target)
            if index >= 0 and self.preview_speed_combo.currentIndex() != index:
                self.preview_speed_combo.blockSignals(True)
                self.preview_speed_combo.setCurrentIndex(index)
                self.preview_speed_combo.blockSignals(False)
        if hasattr(self, "preview_audio_track_combo"):
            combo = self.preview_audio_track_combo
            entries = self._preview_audio_track_choices()
            current_mode = str(getattr(self, "_preview_audio_track_mode", "both") or "both").strip().lower()
            if current_mode == "dubbed" and not any(value == "dubbed" for _label, value in entries):
                current_mode = "both"
                self._preview_audio_track_mode = "both"
            existing = [(combo.itemText(i), str(combo.itemData(i) or "")) for i in range(combo.count())]
            if existing != entries:
                combo.blockSignals(True)
                combo.clear()
                for label, value in entries:
                    combo.addItem(label, value)
                combo.blockSignals(False)
            target_index = combo.findData(current_mode)
            if target_index < 0:
                target_index = 0
            if combo.currentIndex() != target_index:
                combo.blockSignals(True)
                combo.setCurrentIndex(target_index)
                combo.blockSignals(False)
            combo.setEnabled(combo.count() > 1 and getattr(self, "media_player", None) is not None and getattr(self.media_player, "backend_name", "") == "libmpv")

    def on_preview_speed_changed(self, index: int):
        if not hasattr(self, "preview_speed_combo"):
            return
        rate = self.preview_speed_combo.itemData(index)
        try:
            new_rate = float(rate or 1.0)
        except Exception:
            new_rate = 1.0
        self._preview_speed = new_rate
        if hasattr(self, "media_player"):
            try:
                self.media_player.set_playback_rate(new_rate)
            except Exception:
                pass


def _relaunch_launcher():
    from views.launcher import show_launcher, LauncherWindow
    video_path = show_launcher(None)
    QApplication.setQuitOnLastWindowClosed(True)
    if not video_path:
        QApplication.quit()
        return
    LauncherWindow.add_recent(None, video_path)
    new_window = VideoTranslatorGUI()
    new_window.prepare_initial_editor_layout()
    new_window.show()
    def _init():
        new_window._current_video_path = os.path.abspath(video_path)
        new_window.ensure_media_backend_ready()
        new_window.video_path_edit.setText(video_path)
        new_window.media_player.setSource(QUrl.fromLocalFile(video_path))
        if hasattr(new_window, "refresh_video_dimensions"):
            new_window.refresh_video_dimensions(video_path)
        new_window.current_project_state = new_window.ensure_current_project()
        new_window.load_project_context(new_window.current_project_state)
        if hasattr(new_window, "timeline") and hasattr(new_window.timeline, "set_video_source"):
            try:
                dur = new_window.media_player.duration() / 1000.0
            except Exception:
                dur = 60.0
            new_window.timeline.set_video_source(new_window._current_video_path, dur)
        new_window.schedule_timeline_visual_refresh(waveform=True, thumbnails=True)
    QTimer.singleShot(100, _init)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoTranslatorGUI()
    window.show()
    sys.exit(app.exec())






