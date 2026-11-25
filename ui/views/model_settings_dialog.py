import os
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from language_support import SENSEVOICE_LANGUAGES
from runtime_paths import models_path
from ui.i18n import t
from utils.capcut_notice import watch_capcut_selection
from utils.file_dialog_utils import open_folder as open_folder_impl
from utils.settings_utils import get_asr_filter_settings, persist_asr_filter_settings
from worker_adapters.elevenlabs_workers import ElevenLabsSettingsSyncWorker

from .capcut_settings import build_capcut_stt_settings, build_capcut_tts_settings
from .translation_settings import build_translation_settings


def _update_workers_combo_state(combo, tier: str = "free") -> None:
    """Enable or disable worker options based on ElevenLabs subscription tier.
    Options 3 and 4 (indices 2 and 3) are restricted to paid tiers.
    """
    is_free = str(tier or "free").strip().lower() in ("free", "")
    w_model = combo.model() if hasattr(combo, "model") else None
    if w_model is not None and hasattr(w_model, "item"):
        for idx in (2, 3):  # 3 and 4 workers
            it = w_model.item(idx)
            if it is not None:
                it.setEnabled(not is_free)
    if is_free and hasattr(combo, "currentIndex") and combo.currentIndex() > 1:
        combo.setCurrentIndex(1)


def update_elevenlabs_workers_combo_state(combo, tier: str = "free") -> None:
    return _update_workers_combo_state(combo, tier)


def format_elevenlabs_sub_label(sub_info_dict, api_key_str: str = "") -> str:
    """Format ElevenLabs user subscription information with thousand separators."""
    if isinstance(sub_info_dict, dict) and sub_info_dict.get("tier"):
        tier_str = str(sub_info_dict.get("tier", "")).capitalize()
        raw_count = sub_info_dict.get("character_count", 0) or 0
        raw_limit = sub_info_dict.get("character_limit", 0) or 0
        try:
            c_count = f"{int(raw_count):,}"
        except (ValueError, TypeError):
            c_count = str(raw_count)
        try:
            c_limit = f"{int(raw_limit):,}"
        except (ValueError, TypeError):
            c_limit = str(raw_limit)
        return t("Plan: {tier} | Used: {used} / {limit} characters", tier=tier_str, used=c_count, limit=c_limit)
    else:
        return t("Plan: Free | Used: 0 / 0 characters") if api_key_str else t("Plan: Not connected")


def show_model_settings_dialog(gui, *, remote_mode: bool):
    dialog = QDialog(gui)
    if hasattr(gui, "light_window_icon") and gui.light_window_icon and not gui.light_window_icon.isNull():
        dialog.setWindowIcon(gui.light_window_icon)
    dialog.setWindowTitle(t("Settings"))
    dialog.setModal(True)
    dialog.setMinimumWidth(600)
    dialog.setMinimumHeight(460)
    dialog.resize(650, 560)
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
            QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
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

    tabs = QTabWidget(dialog)
    tabs.setStyleSheet(
        """
            QTabWidget::pane {
                border: 1px solid #2f4868;
                background-color: #0f1724;
                border-radius: 8px;
            }
            QTabBar::tab {
                background-color: #1a2c44;
                color: #9fb3ca;
                padding: 8px 18px;
                border: 1px solid #2f4868;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 3px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background-color: #132033;
                color: #8ad7ff;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: #223755;
                color: #e0edff;
            }
            """
    )
    main_dialog_layout.addWidget(tabs, 1)

    def _make_tab_scroll_widget():
        scroll = QScrollArea(tabs)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #0b1220; width: 8px; border-radius: 4px; }"
            "QScrollBar::handle:vertical { background: #2f4868; border-radius: 4px; min-height: 20px; }"
            "QScrollBar::handle:vertical:hover { background: #456894; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )
        content = QWidget(scroll)
        content.setStyleSheet("background: transparent;")
        tab_layout = QVBoxLayout(content)
        tab_layout.setContentsMargins(12, 12, 12, 12)
        tab_layout.setSpacing(12)
        scroll.setWidget(content)
        return scroll, content, tab_layout

    # =========================================================================
    # Tab 1: Transcription (Audio source, Whisper, OCR, CapCut STT)
    # =========================================================================
    trans_scroll, trans_content, trans_layout = _make_tab_scroll_widget()

    engine_title = QLabel(t("Audio source"), trans_content)
    engine_title.setObjectName("statusHeadline")
    trans_layout.addWidget(engine_title)

    engine_combo = QComboBox(trans_content)
    engine_combo.setObjectName("transcription_engine_combo")
    engine_combo.addItem("Audio (SenseVoice) - Speed", "sensevoice")
    engine_combo.addItem("Audio (Whisper) - Quality", "whisper")
    engine_combo.addItem("CapCut API (Beta)", "capcut")
    engine_combo.addItem("Video (OCR)", "ocr")
    watch_capcut_selection(engine_combo)
    source_language = gui.get_source_language_code() if hasattr(gui, "get_source_language_code") else "auto"
    if source_language not in SENSEVOICE_LANGUAGES:
        engine_combo.removeItem(engine_combo.findData("sensevoice"))
    current_engine = gui.get_transcription_engine()
    idx = engine_combo.findData(current_engine)
    if idx >= 0:
        engine_combo.setCurrentIndex(idx)
    trans_layout.addWidget(engine_combo)

    # Whisper Section
    is_whisper = current_engine == "whisper"
    whisper_title = QLabel(t("Whisper model"), trans_content)
    whisper_title.setObjectName("statusHeadline")
    whisper_title.setVisible(is_whisper)
    trans_layout.addWidget(whisper_title)

    whisper_combo = QComboBox(trans_content)
    whisper_combo.addItem("Base (~1GB VRAM)", "base")
    whisper_combo.addItem(t("Small - Fast (~2GB VRAM)"), "small")
    if os.environ.get("CAPCAP_DEVICE", "cuda").strip().lower() == "cuda":
        whisper_combo.addItem(t("Medium - Balanced (~5GB VRAM)"), "medium")
        whisper_combo.addItem(t("Turbo - High quality + Fast (~6GB VRAM)"), "turbo")
        whisper_combo.addItem(t("Large-v3 - Best quality (~10GB VRAM)"), "large-v3")
    current_whisper = str(getattr(gui, "selected_whisper_model_name", "auto") or "auto").strip().lower()
    if current_whisper == "auto":
        current_whisper = gui.get_whisper_model_name()
    whisper_index = whisper_combo.findData(current_whisper)
    whisper_combo.setCurrentIndex(whisper_index if whisper_index >= 0 else 0)
    whisper_combo.setVisible(is_whisper)
    trans_layout.addWidget(whisper_combo)

    # Whisper ASR cleanup options
    asr_filter_options = get_asr_filter_settings(gui.settings)
    asr_filter_widget = QWidget(trans_content)
    asr_filter_widget.setObjectName("asrFilterSettings")
    asr_filter_layout = QVBoxLayout(asr_filter_widget)
    asr_filter_layout.setContentsMargins(0, 0, 0, 0)
    asr_filter_layout.setSpacing(6)

    dedup_enabled_cb = QCheckBox(t("Reduce repeated phrases"), asr_filter_widget)
    dedup_enabled_cb.setObjectName("asrDedupEnabledCheckBox")
    dedup_enabled_cb.setChecked(asr_filter_options["dedup_enabled"])
    asr_filter_layout.addWidget(dedup_enabled_cb)

    asr_advanced_widget = QWidget(asr_filter_widget)
    asr_advanced_widget.setObjectName("asrFilterAdvancedSettings")
    asr_advanced_layout = QVBoxLayout(asr_advanced_widget)
    asr_advanced_layout.setContentsMargins(12, 0, 0, 0)
    asr_advanced_layout.setSpacing(6)
    asr_advanced_hint = QLabel(
        t("You can keep the defaults if you are unsure: 82% similarity and 3 seconds of silence."),
        asr_advanced_widget,
    )
    asr_advanced_hint.setObjectName("asrFilterAdvancedHint")
    asr_advanced_hint.setStyleSheet("color: #9fb3ca; font-size: 12px;")
    asr_advanced_hint.setWordWrap(True)
    asr_advanced_layout.addWidget(asr_advanced_hint)

    dedup_threshold_row = QHBoxLayout()
    dedup_threshold_label = QLabel(t("Minimum text similarity:"), asr_advanced_widget)
    dedup_threshold_label.setObjectName("asrDedupThresholdLabel")
    dedup_threshold_spin = QSpinBox(asr_advanced_widget)
    dedup_threshold_spin.setObjectName("asrDedupThresholdSpinBox")
    dedup_threshold_spin.setRange(50, 100)
    dedup_threshold_spin.setSuffix("%")
    dedup_threshold_spin.setValue(round(asr_filter_options["dedup_similarity"] * 100))
    dedup_threshold_row.addWidget(dedup_threshold_label, 1)
    dedup_threshold_row.addWidget(dedup_threshold_spin)
    asr_advanced_layout.addLayout(dedup_threshold_row)

    dedup_hint = QLabel(
        t(
            "Helps remove duplicate wording where audio sections join, keeping the fuller sentence when possible. Repeated dialogue at other times is kept."
        ),
        asr_filter_widget,
    )
    dedup_hint.setObjectName("asrDedupHint")
    dedup_hint.setStyleSheet("color: #9fb3ca; font-size: 12px;")
    dedup_hint.setWordWrap(True)
    asr_filter_layout.addWidget(dedup_hint)

    dedup_example = QLabel(
        t('Example: "Today we are" + "Today we are going to school" → keep the fuller sentence.'),
        asr_filter_widget,
    )
    dedup_example.setObjectName("asrDedupExample")
    dedup_example.setStyleSheet("color: #9fb3ca; font-size: 12px;")
    dedup_example.setWordWrap(True)
    asr_filter_layout.addWidget(dedup_example)

    hallucination_filter_cb = QCheckBox(t("Reduce made-up subtitles during silence"), asr_filter_widget)
    hallucination_filter_cb.setObjectName("asrHallucinationFilterEnabledCheckBox")
    hallucination_filter_cb.setChecked(asr_filter_options["hallucination_filter_enabled"])
    asr_filter_layout.addWidget(hallucination_filter_cb)

    hallucination_threshold_row = QHBoxLayout()
    hallucination_threshold_label = QLabel(
        t("Silence threshold (seconds):"), asr_advanced_widget
    )
    hallucination_threshold_label.setObjectName("asrHallucinationSilenceThresholdLabel")
    hallucination_threshold_spin = QDoubleSpinBox(asr_advanced_widget)
    hallucination_threshold_spin.setObjectName("asrHallucinationSilenceSecondsSpinBox")
    hallucination_threshold_spin.setDecimals(1)
    hallucination_threshold_spin.setRange(0.5, 10.0)
    hallucination_threshold_spin.setSingleStep(0.5)
    hallucination_threshold_spin.setValue(asr_filter_options["hallucination_silence_threshold"])
    hallucination_threshold_row.addWidget(hallucination_threshold_label, 1)
    hallucination_threshold_row.addWidget(hallucination_threshold_spin)
    asr_advanced_layout.addLayout(hallucination_threshold_row)

    hallucination_hint = QLabel(
        t(
            'Helps reduce subtitles that were not spoken, such as "Thanks for watching" appearing during silence. May make transcription slower or miss very quiet speech.'
        ),
        asr_filter_widget,
    )
    hallucination_hint.setObjectName("asrHallucinationHint")
    hallucination_hint.setStyleSheet("color: #9fb3ca; font-size: 12px;")
    hallucination_hint.setWordWrap(True)
    asr_filter_layout.addWidget(hallucination_hint)

    asr_advanced_button = QToolButton(asr_filter_widget)
    asr_advanced_button.setObjectName("asrFilterAdvancedButton")
    asr_advanced_button.setText(t("Advanced"))
    asr_advanced_button.setCheckable(True)
    asr_advanced_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    asr_advanced_button.setArrowType(Qt.RightArrow)
    asr_advanced_button.setStyleSheet(
        "QToolButton { color: #8ad7ff; border: none; padding: 4px 0; text-align: left; }"
        "QToolButton:hover { color: #e0edff; }"
    )
    asr_advanced_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    asr_filter_layout.addWidget(asr_advanced_button)
    asr_advanced_widget.setVisible(False)
    asr_filter_layout.addWidget(asr_advanced_widget)

    def update_asr_advanced_visibility(expanded):
        asr_advanced_widget.setVisible(expanded)
        asr_advanced_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

    asr_advanced_button.toggled.connect(update_asr_advanced_visibility)

    def update_asr_filter_controls():
        dedup_enabled = dedup_enabled_cb.isChecked()
        dedup_threshold_label.setEnabled(dedup_enabled)
        dedup_threshold_spin.setEnabled(dedup_enabled)
        hallucination_enabled = hallucination_filter_cb.isChecked()
        hallucination_threshold_label.setEnabled(hallucination_enabled)
        hallucination_threshold_spin.setEnabled(hallucination_enabled)

    dedup_enabled_cb.toggled.connect(update_asr_filter_controls)
    hallucination_filter_cb.toggled.connect(update_asr_filter_controls)
    update_asr_filter_controls()
    asr_filter_widget.setVisible(is_whisper)
    trans_layout.addWidget(asr_filter_widget)

    # OCR Backend combo (RapidOCR vs Windows Media OCR)
    ocr_backend_label = QLabel(t("OCR Engine:"), trans_content)
    ocr_backend_label.setVisible(current_engine == "ocr")
    ocr_backend_combo = QComboBox(trans_content)
    ocr_backend_combo.addItem("RapidOCR (Default)", "rapidocr")
    ocr_backend_combo.addItem("Windows Media OCR (Native)", "winocr")
    current_ocr_backend = str(os.getenv("OCR_BACKEND", gui.settings.value("ocr_backend", "rapidocr")) or "rapidocr").strip().lower()
    idx = ocr_backend_combo.findData(current_ocr_backend)
    if idx >= 0:
        ocr_backend_combo.setCurrentIndex(idx)
    ocr_backend_combo.setVisible(current_engine == "ocr")
    trans_layout.addWidget(ocr_backend_label)
    trans_layout.addWidget(ocr_backend_combo)

    # OCR note for Windows OCR
    ocr_backend_note = QLabel(trans_content)
    ocr_backend_note.setWordWrap(True)
    ocr_backend_note.setStyleSheet("color: #888888; font-size: 11px; margin-top: 2px; margin-bottom: 6px;")

    def update_ocr_backend_note():
        backend = str(ocr_backend_combo.currentData() or "rapidocr").strip().lower()
        is_winocr = backend in ("winocr", "windows", "windows_ocr")
        is_ocr = engine_combo.currentData() == "ocr"
        if is_winocr and is_ocr:
            try:
                from app.ocr_processor import get_windows_ocr_languages
                langs = get_windows_ocr_languages()
            except Exception:
                langs = []
            langs_str = ", ".join(langs) if langs else t("None detected")
            ocr_backend_note.setText(
                t("Note: Windows Media OCR requires language packs installed in Windows Settings (Time & Language → Language → Add Language / OCR).\nCurrently installed: {langs}", langs=langs_str)
            )
            ocr_backend_note.setVisible(True)
        else:
            ocr_backend_note.setText("")
            ocr_backend_note.setVisible(False)

    ocr_backend_combo.currentIndexChanged.connect(update_ocr_backend_note)
    update_ocr_backend_note()
    trans_layout.addWidget(ocr_backend_note)

    # OCR Region combo (only visible when OCR selected)
    region_label = QLabel(t("Subtitle position:"), trans_content)
    region_label.setVisible(current_engine == "ocr")
    region_combo = QComboBox(trans_content)
    region_combo.addItem("Bottom (default)", "bottom")
    region_combo.addItem("Top", "top")
    region_combo.addItem("Full frame", "full")
    current_region = (os.getenv("OCR_SUBTITLE_REGION") or "bottom").strip().lower()
    idx = region_combo.findData(current_region)
    if idx >= 0:
        region_combo.setCurrentIndex(idx)
    region_combo.setVisible(current_engine == "ocr")
    trans_layout.addWidget(region_label)
    trans_layout.addWidget(region_combo)

    sampling_label = QLabel(t("OCR sampling rate:"), trans_content)
    sampling_label.setToolTip("Higher rates catch shorter subtitle flashes but process more video frames.")
    sampling_label.setVisible(current_engine == "ocr")
    sampling_combo = QComboBox(trans_content)
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
    trans_layout.addWidget(sampling_label)
    trans_layout.addWidget(sampling_combo)

    capcut_stt_widget, capcut_stt_backend_combo, capcut_chunk_combo, capcut_stt_workers_combo = (
        build_capcut_stt_settings(trans_content, gui.settings)
    )
    capcut_stt_widget.setVisible(current_engine == "capcut")
    trans_layout.addWidget(capcut_stt_widget)

    def update_engine_fields():
        engine_val = engine_combo.currentData()
        is_ocr = engine_val == "ocr"
        is_whisper = engine_val == "whisper"
        is_capcut_engine = engine_val == "capcut"
        whisper_title.setVisible(is_whisper)
        whisper_combo.setVisible(is_whisper)
        asr_filter_widget.setVisible(is_whisper)
        ocr_backend_label.setVisible(is_ocr)
        ocr_backend_combo.setVisible(is_ocr)
        update_ocr_backend_note()
        region_label.setVisible(is_ocr)
        region_combo.setVisible(is_ocr)
        sampling_label.setVisible(is_ocr)
        sampling_combo.setVisible(is_ocr)
        capcut_stt_widget.setVisible(is_capcut_engine)

    engine_combo.currentIndexChanged.connect(update_engine_fields)
    update_engine_fields()

    trans_layout.addStretch(1)
    tabs.addTab(trans_scroll, t("Transcription"))

    # =========================================================================
    # Tab 2: AI Translation
    # =========================================================================
    trans2_scroll, trans2_content, trans2_layout = _make_tab_scroll_widget()

    translation_fields = build_translation_settings(
        dialog, trans2_content, trans2_layout, gui.settings, lambda message: gui.log(message)
    )
    tabs.addTab(trans2_scroll, t("AI Translation"))

    # =========================================================================
    # Tab 3: ElevenLabs
    # =========================================================================
    elevenlabs_scroll, elevenlabs_content, elevenlabs_layout = _make_tab_scroll_widget()

    # ElevenLabs API Settings Section
    elevenlabs_settings_widget = QWidget(elevenlabs_content)
    elevenlabs_settings_layout = QVBoxLayout(elevenlabs_settings_widget)
    elevenlabs_settings_layout.setContentsMargins(0, 0, 0, 0)
    elevenlabs_settings_layout.setSpacing(10)

    elevenlabs_title = QLabel(t("ElevenLabs API Settings"), elevenlabs_settings_widget)
    elevenlabs_title.setObjectName("statusHeadline")
    elevenlabs_settings_layout.addWidget(elevenlabs_title)

    elevenlabs_key_layout = QVBoxLayout()
    elevenlabs_key_label = QLabel(t("API Key:"), elevenlabs_settings_widget)
    elevenlabs_key_edit = QLineEdit(elevenlabs_settings_widget)
    elevenlabs_key_edit.setObjectName("elevenlabs_api_key")
    elevenlabs_key_edit.setEchoMode(QLineEdit.Password)
    elevenlabs_key_edit.setPlaceholderText("sk-...")
    elevenlabs_key_edit.setText(os.getenv("ELEVENLABS_API_KEY", gui.settings.value("elevenlabs_api_key", "")))
    elevenlabs_key_layout.addWidget(elevenlabs_key_label)
    elevenlabs_key_layout.addWidget(elevenlabs_key_edit)
    elevenlabs_settings_layout.addLayout(elevenlabs_key_layout)

    elevenlabs_model_layout = QVBoxLayout()
    elevenlabs_model_label = QLabel(t("Model:"), elevenlabs_settings_widget)
    elevenlabs_model_combo = QComboBox(elevenlabs_settings_widget)
    try:
        from app.engines.elevenlabs_tts import DEFAULT_ELEVENLABS_MODELS, fetch_elevenlabs_models
    except ImportError:
        from engines.elevenlabs_tts import DEFAULT_ELEVENLABS_MODELS, fetch_elevenlabs_models

    for m in DEFAULT_ELEVENLABS_MODELS:
        elevenlabs_model_combo.addItem(t(m["name"]), m["model_id"])

    current_el_model = os.getenv("ELEVENLABS_MODEL", gui.settings.value("elevenlabs_model", "eleven_turbo_v2_5"))
    if current_el_model == "eleven_multilingual_v2":
        current_el_model = "eleven_turbo_v2_5"
    el_model_idx = elevenlabs_model_combo.findData(current_el_model)
    elevenlabs_model_combo.setCurrentIndex(el_model_idx if el_model_idx >= 0 else 2) # eleven_turbo_v2_5
    elevenlabs_model_layout.addWidget(elevenlabs_model_label)
    elevenlabs_model_layout.addWidget(elevenlabs_model_combo)
    elevenlabs_settings_layout.addLayout(elevenlabs_model_layout)

    elevenlabs_workers_layout = QVBoxLayout()
    elevenlabs_workers_label = QLabel(t("Parallel Workers:"), elevenlabs_settings_widget)
    elevenlabs_workers_combo = QComboBox(elevenlabs_settings_widget)
    elevenlabs_workers_combo.addItem(t("1 worker (Safe / Slow network)"), "1")
    elevenlabs_workers_combo.addItem(t("2 workers (Recommended for Free tier)"), "2")
    elevenlabs_workers_combo.addItem(t("3 workers 🔒 (Paid Plan Required)"), "3")
    elevenlabs_workers_combo.addItem(t("4 workers 🔒 (Paid Plan Required)"), "4")

    saved_workers = str(os.getenv("ELEVENLABS_TTS_WORKERS", gui.settings.value("elevenlabs_tts_workers", "2"))).strip()
    worker_idx = elevenlabs_workers_combo.findData(saved_workers)
    elevenlabs_workers_combo.setCurrentIndex(worker_idx if worker_idx >= 0 else 1)

    def _update_workers_combo_state(tier: str):
        update_elevenlabs_workers_combo_state(elevenlabs_workers_combo, tier)

    elevenlabs_workers_layout.addWidget(elevenlabs_workers_label)
    elevenlabs_workers_layout.addWidget(elevenlabs_workers_combo)
    elevenlabs_settings_layout.addLayout(elevenlabs_workers_layout)

    elevenlabs_sub_label = QLabel(elevenlabs_settings_widget)
    elevenlabs_sub_label.setObjectName("helperLabel")
    saved_key = os.getenv("ELEVENLABS_API_KEY", gui.settings.value("elevenlabs_api_key", "")).strip()

    _format_sub_label = format_elevenlabs_sub_label
    try:
        try:
            from app.engines.elevenlabs_tts import read_elevenlabs_voice_cache
        except ImportError:
            from engines.elevenlabs_tts import read_elevenlabs_voice_cache
        cached_account = read_elevenlabs_voice_cache(saved_key, allow_expired=True) if saved_key else {}
        c_sub = cached_account.get("user_subscription", {}) if isinstance(cached_account, dict) else {}
    except Exception:
        c_sub = {}
    c_tier = str(c_sub.get("tier", "free") or "free").strip().lower() if isinstance(c_sub, dict) else "free"
    elevenlabs_sub_label.setText(_format_sub_label(c_sub, saved_key))
    _update_workers_combo_state(c_tier)

    elevenlabs_sync_btn = QPushButton(t("\U0001f504 Sync voices from ElevenLabs"), elevenlabs_settings_widget)
    elevenlabs_sync_status = QLabel("", elevenlabs_settings_widget)
    elevenlabs_sync_status.setObjectName("helperLabel")
    sync_session = {"open": True, "result": None}
    save_button_ref = []
    dialog.finished.connect(lambda *_args: sync_session.update(open=False))

    def _finish_sync_worker(worker):
        if getattr(gui, "_elevenlabs_sync_worker", None) is worker:
            gui._elevenlabs_sync_worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    def _sync_result_ready(result):
        # QDialog.finished flips this flag before its child widgets are
        # deleted. Late network completions after Cancel are discarded.
        if not sync_session["open"]:
            return
        result = result if isinstance(result, dict) else {}
        snapshot_key = str(result.get("api_key", "") or "").strip()
        current_field_key = elevenlabs_key_edit.text().strip()
        if snapshot_key != current_field_key:
            elevenlabs_sync_status.setText(t("API Key changed during sync; the result was discarded."))
        elif result.get("error"):
            elevenlabs_sync_status.setText(t("Sync failed."))
            QMessageBox.critical(dialog, "ElevenLabs", str(result.get("error")))
        else:
            payload = result.get("payload")
            voices = payload.get("voices", []) if isinstance(payload, dict) else None
            models = result.get("models", [])
            if not isinstance(voices, list):
                elevenlabs_sync_status.setText(t("Sync failed."))
                QMessageBox.critical(dialog, "ElevenLabs", t("ElevenLabs returned an invalid voice catalog."))
            else:
                sync_session["result"] = {"api_key": snapshot_key, "payload": payload}
                valid_models = [
                    (str(item.get("model_id", "") or "").strip(), str(item.get("name", "") or "").strip())
                    for item in models if isinstance(item, dict)
                ] if isinstance(models, list) else []
                valid_models = [(model_id, name or model_id) for model_id, name in valid_models if model_id]
                if valid_models:
                    saved_selected = elevenlabs_model_combo.currentData()
                    elevenlabs_model_combo.clear()
                    for model_id, model_name in valid_models:
                        elevenlabs_model_combo.addItem(t(model_name), model_id)
                    selected_idx = elevenlabs_model_combo.findData(saved_selected)
                    if selected_idx < 0:
                        selected_idx = elevenlabs_model_combo.findData("eleven_turbo_v2_5")
                    elevenlabs_model_combo.setCurrentIndex(max(0, selected_idx))

                sub_info = payload.get("user_subscription", {}) if isinstance(payload, dict) else {}
                elevenlabs_sub_label.setText(_format_sub_label(sub_info, snapshot_key))
                tier = str(sub_info.get("tier", "free") or "free") if isinstance(sub_info, dict) else "free"
                _update_workers_combo_state(tier)
                elevenlabs_sync_status.setText(
                    t("Successfully synced {count} voices. Save to apply.", count=len(voices))
                )

        elevenlabs_sync_btn.setEnabled(True)
        elevenlabs_sync_btn.setText(t("\U0001f504 Sync voices from ElevenLabs"))
        for button in save_button_ref:
            button.setEnabled(True)

    def _sync_elevenlabs_voices():
        key = elevenlabs_key_edit.text().strip()
        if not key:
            QMessageBox.warning(dialog, "ElevenLabs", t("Please enter your API Key first."))
            return
        active_worker = getattr(gui, "_elevenlabs_sync_worker", None)
        if active_worker is not None and active_worker.isRunning():
            elevenlabs_sync_status.setText(t("A voice sync is already running."))
            return
        worker = ElevenLabsSettingsSyncWorker(key, gui)
        gui._elevenlabs_sync_worker = worker
        worker.completed.connect(_sync_result_ready)
        worker.finished.connect(lambda w=worker: _finish_sync_worker(w))
        elevenlabs_sync_btn.setEnabled(False)
        elevenlabs_sync_btn.setText(t("Syncing..."))
        elevenlabs_sync_status.setText(t("Fetching voices, models, and account plan..."))
        for button in save_button_ref:
            button.setEnabled(False)
        worker.start()

    elevenlabs_sync_btn.clicked.connect(_sync_elevenlabs_voices)
    elevenlabs_settings_layout.addWidget(elevenlabs_sync_btn)
    elevenlabs_settings_layout.addWidget(elevenlabs_sync_status)
    elevenlabs_settings_layout.addWidget(elevenlabs_sub_label)

    # ElevenLabs Guide Card
    guide_card = QFrame(elevenlabs_settings_widget)
    guide_card.setStyleSheet(
        """
            QFrame {
                background-color: #132033;
                border: 1px solid #2f4868;
                border-radius: 8px;
            }
            """
    )
    guide_card_layout = QVBoxLayout(guide_card)
    guide_card_layout.setContentsMargins(12, 10, 12, 10)
    guide_card_layout.setSpacing(6)

    guide_title = QLabel(t("🔑 How to get an ElevenLabs API Key:"), guide_card)
    guide_title.setStyleSheet("font-weight: 700; color: #8ad7ff; font-size: 13px; background: transparent;")
    guide_card_layout.addWidget(guide_title)

    guide_text = QLabel(guide_card)
    guide_text.setStyleSheet("color: #d7e3f4; font-size: 12px; background: transparent;")
    guide_text.setWordWrap(True)
    guide_text.setOpenExternalLinks(True)
    guide_text.setText(
        t(
            "1. Visit <a href='https://elevenlabs.io/app/settings/api-keys' style='color: #4da3ff;'>https://elevenlabs.io/app/settings/api-keys</a> and click <b>Create API Key</b>.<br>"
            "2. When creating a <b>Restricted Key</b>, grant these 4 permissions (or choose <b>Full Access</b>):<br>"
            "&nbsp;&nbsp;• <b>Text to Speech:</b> <code>Access</code> (generate voice audio)<br>"
            "&nbsp;&nbsp;• <b>Voices:</b> <code>Read</code> (fetch personal & cloned voices)<br>"
            "&nbsp;&nbsp;• <b>Voice collection:</b> <code>Read</code> (read Voice Library catalog)<br>"
            "&nbsp;&nbsp;• <b>User:</b> <code>Access</code> (sync character count & subscription plan)<br>"
            "3. Paste the API Key above and click <b>🔄 Sync voices from ElevenLabs</b>."
        )
    )
    guide_card_layout.addWidget(guide_text)
    elevenlabs_settings_layout.addWidget(guide_card)

    elevenlabs_hint = QLabel(t("Enter your API Key from https://elevenlabs.io/app/speech-synthesis. Voices will be loaded automatically when a valid key is provided."), elevenlabs_settings_widget)
    elevenlabs_hint.setObjectName("helperLabel")
    elevenlabs_hint.setWordWrap(True)
    elevenlabs_settings_layout.addWidget(elevenlabs_hint)

    elevenlabs_layout.addWidget(elevenlabs_settings_widget)
    elevenlabs_layout.addStretch(1)
    tabs.addTab(elevenlabs_scroll, t("ElevenLabs"))

    # =========================================================================
    # Tab 4: CapCut TTS Settings
    # =========================================================================
    capcut_scroll, capcut_content, capcut_layout = _make_tab_scroll_widget()

    capcut_backend_combo, capcut_pc_path_edit, capcut_tts_batch_combo, capcut_tts_workers_combo = (
        build_capcut_tts_settings(dialog, capcut_content, capcut_layout, gui.settings, capcut_stt_backend_combo)
    )
    tabs.addTab(capcut_scroll, t("CapCut"))

    # Hidden Remote API fields for backward-compatible save logic
    remote_url_edit = QLineEdit(dialog)
    remote_url_edit.setVisible(False)
    remote_url_edit.setText(os.getenv("CAPCAP_REMOTE_API_URL", "http://127.0.0.1:8765"))
    remote_token_edit = QLineEdit(dialog)
    remote_token_edit.setVisible(False)
    remote_token_edit.setText(os.getenv("CAPCAP_REMOTE_API_TOKEN", ""))

    def _piper_models_dir() -> str:
        return models_path("piper")

    def open_voices_folder():
        voices_dir = _piper_models_dir()
        os.makedirs(voices_dir, exist_ok=True)
        open_folder_impl(gui, voices_dir)

    # Buttons
    button_row = QHBoxLayout()
    button_row.setContentsMargins(8, 4, 8, 4)
    manage_resources_btn = QPushButton(t("Manage Resources"), dialog)
    open_voices_folder_btn = QPushButton(t("Open Voices Folder"), dialog)
    button_row.addWidget(manage_resources_btn)
    button_row.addWidget(open_voices_folder_btn)
    button_row.addStretch(1)
    cancel_btn = QPushButton(t("Cancel"), dialog)
    save_btn = QPushButton(t("Save"), dialog)
    save_button_ref.append(save_btn)
    button_row.addWidget(cancel_btn)
    button_row.addWidget(save_btn)
    main_dialog_layout.addLayout(button_row)

    manage_resources_btn.clicked.connect(gui.open_resource_manager_dialog)
    open_voices_folder_btn.clicked.connect(open_voices_folder)
    cancel_btn.clicked.connect(dialog.reject)
    save_btn.clicked.connect(dialog.accept)

    # The subtitle is a top-level overlay above MPV's native surface.
    # Hide it for this modal dialog so it cannot paint over Settings.
    subtitle_item = getattr(getattr(gui, "video_view", None), "subtitle_item", None)
    text_overlay = getattr(getattr(gui, "video_view", None), "text_overlay", None)
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
        if subtitle_was_visible and not getattr(gui, "_preview_video_has_burned_subtitles", False):
            QTimer.singleShot(0, gui.sync_live_subtitle_preview)
        return

    # Save Logic
    new_whisper = str(whisper_combo.currentData() or "small").strip().lower()
    new_engine = str(engine_combo.currentData() or "sensevoice").strip().lower()
    new_ocr_backend = str(ocr_backend_combo.currentData() or "rapidocr").strip().lower()
    new_ocr_region = str(region_combo.currentData() or "bottom").strip().lower()
    new_ocr_sampling_fps = str(sampling_combo.currentData() or "auto").strip().lower()
    asr_env_updates = persist_asr_filter_settings(
        gui.settings,
        {
            "dedup_enabled": dedup_enabled_cb.isChecked(),
            "dedup_similarity": dedup_threshold_spin.value() / 100.0,
            "hallucination_filter_enabled": hallucination_filter_cb.isChecked(),
            "hallucination_silence_threshold": hallucination_threshold_spin.value(),
        },
    )
    new_key = translation_fields.api_key.text().strip()
    new_model = translation_fields.model.text().strip()
    new_provider = str(translation_fields.provider.currentData()).strip()
    new_base_url = translation_fields.base_url.text().strip()

    gui.selected_whisper_model_name = new_whisper
    gui.settings.setValue("default_transcription_engine", new_engine)
    gui.settings.setValue("whisper_model_name", new_whisper)
    gui.settings.setValue("ocr_backend", new_ocr_backend)
    os.environ["OCR_BACKEND"] = new_ocr_backend

    new_capcut_chunk = str(capcut_chunk_combo.currentData() or "600").strip()
    new_capcut_stt_workers = str(capcut_stt_workers_combo.currentData() or "5").strip()
    new_capcut_tts_batch = str(capcut_tts_batch_combo.currentData() or "30").strip()
    new_capcut_tts_workers = str(capcut_tts_workers_combo.currentData() or "30").strip()
    new_capcut_backend = str(capcut_backend_combo.currentData() or "web")
    new_capcut_pc_path = capcut_pc_path_edit.text().strip()

    os.environ["CAPCUT_BACKEND"] = new_capcut_backend
    os.environ["CAPCUT_PC_PATH"] = new_capcut_pc_path
    os.environ["CAPCUT_STT_CHUNK_SECONDS"] = new_capcut_chunk
    os.environ["CAPCUT_STT_WORKERS"] = new_capcut_stt_workers
    os.environ["CAPCUT_TTS_BATCH_SIZE"] = new_capcut_tts_batch
    os.environ["CAPCUT_TTS_WORKERS"] = new_capcut_tts_workers

    gui.settings.setValue("capcut_stt_chunk_seconds", new_capcut_chunk)
    gui.settings.setValue("capcut_backend", new_capcut_backend)
    gui.settings.setValue("capcut_pc_path", new_capcut_pc_path)
    gui.settings.setValue("capcut_stt_workers", new_capcut_stt_workers)
    gui.settings.setValue("capcut_tts_batch_size", new_capcut_tts_batch)
    gui.settings.setValue("capcut_tts_workers", new_capcut_tts_workers)

    # ElevenLabs settings
    new_elevenlabs_key = elevenlabs_key_edit.text().strip()
    new_elevenlabs_model = str(elevenlabs_model_combo.currentData() or "eleven_turbo_v2_5").strip()
    new_elevenlabs_workers = str(elevenlabs_workers_combo.currentData() or "2").strip()
    previous_elevenlabs_key = str(os.getenv("ELEVENLABS_API_KEY", "") or "").strip()
    previous_elevenlabs_model = str(os.getenv("ELEVENLABS_MODEL", "") or "").strip()
    staged_sync = sync_session.get("result") if isinstance(sync_session, dict) else None
    if (
        isinstance(staged_sync, dict)
        and str(staged_sync.get("api_key", "") or "").strip() == new_elevenlabs_key
        and isinstance(staged_sync.get("payload"), dict)
    ):
        try:
            try:
                from app.engines.elevenlabs_tts import write_elevenlabs_voice_cache
            except ImportError:
                from engines.elevenlabs_tts import write_elevenlabs_voice_cache
            cache_written = write_elevenlabs_voice_cache(new_elevenlabs_key, staged_sync["payload"])
            if not cache_written:
                gui.log("[ElevenLabs] Synced voice cache was rejected; a background refresh will retry after Save.")
        except Exception as exc:
            gui.log(f"[ElevenLabs] Could not commit the synced voice cache: {exc}")
    os.environ["ELEVENLABS_API_KEY"] = new_elevenlabs_key
    os.environ["ELEVENLABS_MODEL"] = new_elevenlabs_model
    os.environ["ELEVENLABS_TTS_WORKERS"] = new_elevenlabs_workers
    gui.settings.setValue("elevenlabs_api_key", new_elevenlabs_key)
    gui.settings.setValue("elevenlabs_model", new_elevenlabs_model)
    gui.settings.setValue("elevenlabs_tts_workers", new_elevenlabs_workers)

    # Transcription engine settings (apply to all modes)
    # The subtitle source is project-local.  Do not write it into .env,
    # otherwise opening another project can inherit a stale OCR/Audio
    # choice from an earlier session.
    _engine_updates = {
        "OCR_BACKEND": new_ocr_backend,
        "OCR_SUBTITLE_REGION": new_ocr_region,
        "OCR_SAMPLING_FPS": new_ocr_sampling_fps,
        "CAPCUT_STT_CHUNK_SECONDS": new_capcut_chunk,
        "CAPCUT_BACKEND": new_capcut_backend,
        "CAPCUT_PC_PATH": new_capcut_pc_path,
        "CAPCUT_STT_WORKERS": new_capcut_stt_workers,
        "CAPCUT_TTS_BATCH_SIZE": new_capcut_tts_batch,
        "CAPCUT_TTS_WORKERS": new_capcut_tts_workers,
        "ELEVENLABS_API_KEY": new_elevenlabs_key,
        "ELEVENLABS_MODEL": new_elevenlabs_model,
        "ELEVENLABS_TTS_WORKERS": new_elevenlabs_workers,
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
        new_preset = str(translation_fields.preset.currentData() or "general_default").strip()
        new_browser_platform = str(translation_fields.browser_platform.currentData() or "gemini").strip().lower()
        new_browser_temp_chat = "1" if translation_fields.browser_temp_chat.isChecked() else "0"
        new_browser_workers = translation_fields.browser_workers.currentData() or "1"
        gui.settings.setValue("browser_ai_platform", new_browser_platform)
        gui.settings.setValue("browser_ai_temp_chat", new_browser_temp_chat)
        gui.settings.setValue("browser_ai_workers", new_browser_workers)
        os.environ["CAPCAP_BROWSER_AI_PLATFORM"] = new_browser_platform
        os.environ["CAPCAP_BROWSER_AI_TEMP_CHAT"] = new_browser_temp_chat
        os.environ["CAPCAP_BROWSER_AI_WORKERS"] = new_browser_workers
        gui.settings.setValue("translation_provider", new_provider)
        gui.settings.setValue("translation_preset_id", new_preset)
        os.environ["OPENAI_PROVIDER"] = new_provider
        os.environ["AI_POLISHER_PROVIDER"] = new_provider
        if new_provider in ("google", "bing"):
            updates = {
                "AI_POLISHER_PROVIDER": new_provider,
                "OPENAI_PROVIDER": new_provider,
                "CAPCAP_TRANSLATION_PRESET_ID": new_preset,
            }
        elif new_provider == "browser_ai":
            updates = {
                "AI_POLISHER_PROVIDER": "browser_ai",
                "OPENAI_PROVIDER": "browser_ai",
                "CAPCAP_BROWSER_AI_PLATFORM": new_browser_platform,
                "CAPCAP_BROWSER_AI_TEMP_CHAT": new_browser_temp_chat,
                "CAPCAP_BROWSER_AI_WORKERS": new_browser_workers,
                "CAPCAP_TRANSLATION_PRESET_ID": new_preset,
            }
        elif new_provider == "google_ai_studio":
            gui.settings.setValue("google_ai_studio_api_key", new_key)
            gui.settings.setValue("google_ai_studio_model", new_model or "gemini-3.7-flash")
            gui.settings.setValue("google_ai_studio_base_url", new_base_url or "https://generativelanguage.googleapis.com/v1beta/openai/")
            updates = {
                "AI_POLISHER_PROVIDER": "google_ai_studio",
                "OPENAI_PROVIDER": "google_ai_studio",
                "GOOGLE_AI_STUDIO_API_KEY": new_key,
                "GOOGLE_AI_STUDIO_MODEL": new_model or "gemini-3.7-flash",
                "GOOGLE_AI_STUDIO_BASE_URL": new_base_url or "https://generativelanguage.googleapis.com/v1beta/openai/",
                "CAPCAP_TRANSLATION_PRESET_ID": new_preset,
            }
        elif new_provider == "ollama":
            gui.settings.setValue("ollama_model", new_model)
            gui.settings.setValue("ollama_base_url", new_base_url or "http://localhost:11434/v1")
            updates = {
                "AI_POLISHER_PROVIDER": "ollama",
                "OPENAI_PROVIDER": "ollama",
                "OPENAI_API_KEY": "ollama",
                "OPENAI_MODEL": new_model,
                "OPENAI_BASE_URL": new_base_url or "http://localhost:11434/v1",
                "CAPCAP_TRANSLATION_PRESET_ID": new_preset,
            }
        else:
            gui.settings.setValue("openai_api_key", new_key)
            gui.settings.setValue("openai_model", new_model or "gpt-4o-mini")
            gui.settings.setValue("openai_base_url", new_base_url or "https://api.openai.com/v1/")
            updates = {
                "AI_POLISHER_PROVIDER": "openai",
                "OPENAI_PROVIDER": "openai",
                "OPENAI_API_KEY": new_key,
                "OPENAI_MODEL": new_model or "gpt-4o-mini",
                "OPENAI_BASE_URL": new_base_url or "https://api.openai.com/v1/",
                "CAPCAP_TRANSLATION_PRESET_ID": new_preset,
            }
        new_auto_context = "1" if translation_fields.auto_context.isChecked() else "0"
        gui.settings.setValue("auto_translation_context", new_auto_context)
        os.environ["CAPCAP_AUTO_TRANSLATION_CONTEXT"] = new_auto_context
        updates["CAPCAP_AUTO_TRANSLATION_CONTEXT"] = new_auto_context

    updates.update(_engine_updates)
    updates.update(asr_env_updates)

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
        clean_line = line if line.endswith("\n") else f"{line}\n"
        new_env_lines.append(clean_line)

    # Ensure the last line ends with a newline before appending new keys
    if new_env_lines and not new_env_lines[-1].endswith("\n"):
        new_env_lines[-1] += "\n"

    for k, v in updates.items():
        if k not in handled_keys:
            new_env_lines.append(f"{k}={v}\n")

    with open(".env", "w", encoding="utf-8") as f:
        f.writelines(new_env_lines)

    # Update os.environ so it takes effect immediately in this session
    for k, v in updates.items():
        os.environ[k] = v

    if previous_elevenlabs_key != new_elevenlabs_key or previous_elevenlabs_model != new_elevenlabs_model:
        gui._voiceover_force_refresh = True
    gui.load_voice_preview_catalog()

    gui.set_project_transcription_engine(new_engine)
    gui.save_user_settings()
    gui._update_ocr_overlay()
    QMessageBox.information(gui, t("Success"), t("Settings saved and updated!"))
    if subtitle_item is not None:
        subtitle_item.set_suppressed(False)
    if text_overlay is not None:
        text_overlay.set_suppressed(False)
    if subtitle_was_visible and not getattr(gui, "_preview_video_has_burned_subtitles", False):
        QTimer.singleShot(0, gui.sync_live_subtitle_preview)
