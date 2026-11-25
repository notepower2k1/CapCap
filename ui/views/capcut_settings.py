import os

from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ui.i18n import t
from utils.capcut_notice import watch_capcut_selection


def _make_capcut_backend_combo(parent, settings):
    combo = QComboBox(parent)
    combo.addItem(t("CapCut Web (default)"), "web")
    combo.addItem(t("CapCut PC (background, experimental)"), "pc")
    selected = str(os.getenv("CAPCUT_BACKEND") or settings.value("capcut_backend", "web")).strip().lower()
    combo.setCurrentIndex(max(0, combo.findData(selected)))
    watch_capcut_selection(combo, connection=True)
    return combo


def build_capcut_stt_settings(parent, settings):
    capcut_stt_widget = QWidget(parent)
    capcut_stt_layout = QVBoxLayout(capcut_stt_widget)
    capcut_stt_layout.setContentsMargins(0, 0, 0, 0)
    capcut_stt_layout.setSpacing(10)

    capcut_stt_title = QLabel(t("CapCut STT Settings"), capcut_stt_widget)
    capcut_stt_title.setObjectName("statusHeadline")
    capcut_stt_layout.addWidget(capcut_stt_title)

    capcut_stt_backend_row = QHBoxLayout()
    capcut_stt_backend_row.addWidget(QLabel(t("CapCut connection:"), capcut_stt_widget))
    capcut_stt_backend_combo = _make_capcut_backend_combo(capcut_stt_widget, settings)
    capcut_stt_backend_combo.setObjectName("capcut_stt_backend_combo")
    capcut_stt_backend_row.addWidget(capcut_stt_backend_combo, 1)
    capcut_stt_layout.addLayout(capcut_stt_backend_row)
    capcut_stt_backend_hint = QLabel(t("Shared with CapCut TTS. The PC installation folder can be set in the CapCut tab."), capcut_stt_widget)
    capcut_stt_backend_hint.setWordWrap(True)
    capcut_stt_backend_hint.setObjectName("helperLabel")
    capcut_stt_layout.addWidget(capcut_stt_backend_hint)

    capcut_chunk_row = QHBoxLayout()
    capcut_chunk_label = QLabel(t("STT Chunk duration:"), capcut_stt_widget)
    capcut_chunk_combo = QComboBox(capcut_stt_widget)
    capcut_chunk_combo.setObjectName("capcut_stt_chunk_combo")
    capcut_chunk_combo.addItem(t("5 minutes"), "300")
    capcut_chunk_combo.addItem(t("10 minutes (default)"), "600")
    capcut_chunk_combo.addItem("15 minutes", "900")
    capcut_chunk_combo.addItem("20 minutes", "1200")
    capcut_chunk_combo.addItem("30 minutes", "1800")
    current_chunk = str(os.getenv("CAPCUT_STT_CHUNK_SECONDS", settings.value("capcut_stt_chunk_seconds", "600"))).strip()
    idx = capcut_chunk_combo.findData(current_chunk)
    capcut_chunk_combo.setCurrentIndex(idx if idx >= 0 else capcut_chunk_combo.findData("600"))
    capcut_chunk_row.addWidget(capcut_chunk_label)
    capcut_chunk_row.addWidget(capcut_chunk_combo, 1)
    capcut_stt_layout.addLayout(capcut_chunk_row)

    capcut_stt_workers_row = QHBoxLayout()
    capcut_stt_workers_label = QLabel(t("STT Workers:"), capcut_stt_widget)
    capcut_stt_workers_combo = QComboBox(capcut_stt_widget)
    for w in ("1", "2", "3", "4", "5", "8", "10"):
        capcut_stt_workers_combo.addItem(f"{w} workers" + (" (default)" if w == "5" else "") if w != "1" else "1 worker", w)
    current_stt_workers = str(os.getenv("CAPCUT_STT_WORKERS", settings.value("capcut_stt_workers", "5"))).strip()
    idx = capcut_stt_workers_combo.findData(current_stt_workers)
    capcut_stt_workers_combo.setCurrentIndex(idx if idx >= 0 else 4)
    capcut_stt_workers_row.addWidget(capcut_stt_workers_label)
    capcut_stt_workers_row.addWidget(capcut_stt_workers_combo, 1)
    capcut_stt_layout.addLayout(capcut_stt_workers_row)

    capcut_stt_hint = QLabel(t("Chunking audio and running concurrent workers speeds up STT processing."), capcut_stt_widget)
    capcut_stt_hint.setObjectName("helperLabel")
    capcut_stt_hint.setWordWrap(True)
    capcut_stt_layout.addWidget(capcut_stt_hint)
    capcut_language_hint = QLabel(t("For Vietnamese, English and other non-Chinese audio, select the source language explicitly. Auto Detect may recognize it incorrectly."), capcut_stt_widget)
    capcut_language_hint.setObjectName("helperLabel")
    capcut_language_hint.setWordWrap(True)
    capcut_stt_layout.addWidget(capcut_language_hint)

    return capcut_stt_widget, capcut_stt_backend_combo, capcut_chunk_combo, capcut_stt_workers_combo


def build_capcut_tts_settings(dialog, capcut_content, capcut_layout, settings, capcut_stt_backend_combo):
    capcut_backend_row = QHBoxLayout()
    capcut_backend_row.addWidget(QLabel(t("CapCut connection:"), capcut_content))
    capcut_backend_combo = _make_capcut_backend_combo(capcut_content, settings)
    capcut_backend_combo.setObjectName("capcut_backend_combo")
    capcut_backend_row.addWidget(capcut_backend_combo, 1)
    capcut_layout.addLayout(capcut_backend_row)
    capcut_stt_backend_combo.currentIndexChanged.connect(capcut_backend_combo.setCurrentIndex)
    capcut_backend_combo.currentIndexChanged.connect(capcut_stt_backend_combo.setCurrentIndex)

    capcut_backend_hint = QLabel(t("Applies to both STT and TTS. Web uses the current API. PC uses an installed CapCut in the background without opening its interface. Both modes require internet access."), capcut_content)
    capcut_backend_hint.setWordWrap(True)
    capcut_backend_hint.setObjectName("helperLabel")
    capcut_layout.addWidget(capcut_backend_hint)

    capcut_pc_widget = QWidget(capcut_content)
    capcut_pc_layout = QVBoxLayout(capcut_pc_widget)
    capcut_pc_layout.setContentsMargins(0, 0, 0, 0)
    capcut_pc_layout.addWidget(QLabel(t("CapCut PC folder (optional):"), capcut_pc_widget))
    capcut_pc_row = QHBoxLayout()
    capcut_pc_path_edit = QLineEdit(capcut_pc_widget)
    capcut_pc_path_edit.setObjectName("capcut_pc_path_edit")
    capcut_pc_path_edit.setPlaceholderText(t("Automatically detect the installed CapCut"))
    capcut_pc_path_edit.setText(str(os.getenv("CAPCUT_PC_PATH") or settings.value("capcut_pc_path", "") or ""))
    capcut_pc_browse = QPushButton(t("Browse..."), capcut_pc_widget)
    capcut_pc_row.addWidget(capcut_pc_path_edit, 1)
    capcut_pc_row.addWidget(capcut_pc_browse)
    capcut_pc_layout.addLayout(capcut_pc_row)
    capcut_pc_status = QLabel(capcut_pc_widget)
    capcut_pc_status.setWordWrap(True)
    capcut_pc_status.setObjectName("helperLabel")
    capcut_pc_layout.addWidget(capcut_pc_status)
    capcut_layout.addWidget(capcut_pc_widget)

    def _update_capcut_pc_status():
        selected_pc = capcut_backend_combo.currentData() == "pc"
        capcut_pc_widget.setVisible(selected_pc)
        if not selected_pc:
            return
        try:
            from app.capcut.transport import find_capcut_pc_dll
            dll = find_capcut_pc_dll(capcut_pc_path_edit.text())
            capcut_pc_status.setText(t("CapCut PC detected: {path}").format(path=str(dll.parent)))
        except (OSError, RuntimeError):
            capcut_pc_status.setText(t("CapCut PC was not found. Install CapCut PC or select its installation folder."))

    def _browse_capcut_pc():
        folder = QFileDialog.getExistingDirectory(dialog, t("Select CapCut PC folder"), capcut_pc_path_edit.text())
        if folder:
            capcut_pc_path_edit.setText(folder)

    capcut_pc_browse.clicked.connect(_browse_capcut_pc)
    capcut_backend_combo.currentIndexChanged.connect(_update_capcut_pc_status)
    capcut_pc_path_edit.textChanged.connect(_update_capcut_pc_status)
    _update_capcut_pc_status()

    capcut_tts_widget = QWidget(capcut_content)
    capcut_tts_layout = QVBoxLayout(capcut_tts_widget)
    capcut_tts_layout.setContentsMargins(0, 0, 0, 0)
    capcut_tts_layout.setSpacing(10)

    capcut_tts_title = QLabel(t("CapCut TTS Settings"), capcut_tts_widget)
    capcut_tts_title.setObjectName("statusHeadline")
    capcut_tts_layout.addWidget(capcut_tts_title)

    capcut_tts_batch_row = QHBoxLayout()
    capcut_tts_batch_label = QLabel(t("TTS Batch size:"), capcut_tts_widget)
    capcut_tts_batch_combo = QComboBox(capcut_tts_widget)
    capcut_tts_batch_combo.setObjectName("capcut_tts_batch_combo")
    for b in ("1", "4", "8", "16", "30", "32", "60"):
        capcut_tts_batch_combo.addItem(f"{b} line / req" if b == "1" else (f"{b} lines / req" + (" (default)" if b == "30" else "")), b)
    current_tts_batch = str(os.getenv("CAPCUT_TTS_BATCH_SIZE", settings.value("capcut_tts_batch_size", "30"))).strip()
    idx = capcut_tts_batch_combo.findData(current_tts_batch)
    capcut_tts_batch_combo.setCurrentIndex(idx if idx >= 0 else capcut_tts_batch_combo.findData("30"))
    capcut_tts_batch_row.addWidget(capcut_tts_batch_label)
    capcut_tts_batch_row.addWidget(capcut_tts_batch_combo, 1)
    capcut_tts_layout.addLayout(capcut_tts_batch_row)

    capcut_tts_workers_row = QHBoxLayout()
    capcut_tts_workers_label = QLabel(t("TTS Workers:"), capcut_tts_widget)
    capcut_tts_workers_combo = QComboBox(capcut_tts_widget)
    capcut_tts_workers_combo.setObjectName("capcut_tts_workers_combo")
    for w in ("1", "5", "10", "15", "20", "30", "45", "60"):
        capcut_tts_workers_combo.addItem(f"{w} workers" + (" (default)" if w == "30" else "") if w != "1" else "1 worker", w)
    current_tts_workers = str(os.getenv("CAPCUT_TTS_WORKERS", settings.value("capcut_tts_workers", "30"))).strip()
    idx = capcut_tts_workers_combo.findData(current_tts_workers)
    capcut_tts_workers_combo.setCurrentIndex(idx if idx >= 0 else 5)
    capcut_tts_workers_row.addWidget(capcut_tts_workers_label)
    capcut_tts_workers_row.addWidget(capcut_tts_workers_combo, 1)
    capcut_tts_layout.addLayout(capcut_tts_workers_row)

    capcut_hint = QLabel(t("Batching subtitle lines and running concurrent workers speeds up dubbing by 5-10x and reduces HTTP requests."), capcut_tts_widget)
    capcut_hint.setObjectName("helperLabel")
    capcut_hint.setWordWrap(True)
    capcut_tts_layout.addWidget(capcut_hint)

    capcut_layout.addWidget(capcut_tts_widget)
    capcut_layout.addStretch(1)
    return capcut_backend_combo, capcut_pc_path_edit, capcut_tts_batch_combo, capcut_tts_workers_combo
