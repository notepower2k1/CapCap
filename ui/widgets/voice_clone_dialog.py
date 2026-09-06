"""Dialog for creating custom cloned voices using VieNeu-TTS."""

from __future__ import annotations

import os
from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from vieneu_tts import save_cloned_voice


class CreateVoiceCloneDialog(QDialog):
    """Modal dialog allowing users to create and save a new clone voice."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.created_voice_entry: dict | None = None

        self.setWindowTitle("Create Voice Clone (VieNeu-TTS)")
        self.setModal(True)
        self.resize(580, 560)
        self.setMinimumSize(500, 480)

        self._setup_player()
        self._init_ui()
        self._apply_styles()

    def _setup_player(self):
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.audio_output.setMuted(False)
        try:
            from PySide6.QtMultimedia import QMediaDevices
            self.audio_output.setDevice(QMediaDevices.defaultAudioOutput())
        except Exception:
            pass
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._winsound_active = False

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Header
        header_title = QLabel("Clone Voice from Audio Sample")
        header_title.setObjectName("dialogTitle")
        layout.addWidget(header_title)

        header_desc = QLabel(
            "Provide a short reference audio sample (3 - 15s) and its matching transcript to create a cloned voice with VieNeu-TTS."
        )
        header_desc.setObjectName("dialogSubtitle")
        header_desc.setWordWrap(True)
        layout.addWidget(header_desc)

        # 1. Voice Name
        name_label = QLabel("Voice Name (*)")
        name_label.setObjectName("fieldLabel")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Storyteller, News Anchor, Podcast Host...")
        layout.addWidget(name_label)
        layout.addWidget(self.name_edit)

        # 2. Gender
        gender_label = QLabel("Gender")
        gender_label.setObjectName("fieldLabel")
        self.gender_combo = QComboBox()
        self.gender_combo.addItem("Male", "male")
        self.gender_combo.addItem("Female", "female")
        layout.addWidget(gender_label)
        layout.addWidget(self.gender_combo)

        # 3. Audio File Picker & Preview
        audio_label = QLabel("Reference Audio Sample (*)")
        audio_label.setObjectName("fieldLabel")
        layout.addWidget(audio_label)

        audio_row = QHBoxLayout()
        audio_row.setSpacing(8)
        self.audio_path_edit = QLineEdit()
        self.audio_path_edit.setReadOnly(True)
        self.audio_path_edit.setPlaceholderText("No audio file selected...")
        audio_row.addWidget(self.audio_path_edit, 1)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setCursor(Qt.PointingHandCursor)
        self.browse_btn.clicked.connect(self._on_browse_audio)
        audio_row.addWidget(self.browse_btn)

        self.play_audio_btn = QPushButton("▶ Play")
        self.play_audio_btn.setCursor(Qt.PointingHandCursor)
        self.play_audio_btn.setEnabled(False)
        self.play_audio_btn.clicked.connect(self._on_toggle_play_audio)
        audio_row.addWidget(self.play_audio_btn)
        layout.addLayout(audio_row)

        audio_hint = QLabel("Recommended: 3 to 15 seconds of clean, clear speech (WAV, MP3, M4A) without background music or noise.")
        audio_hint.setObjectName("fieldHint")
        audio_hint.setWordWrap(True)
        layout.addWidget(audio_hint)

        # 4. Transcript (Reference Text)
        ref_text_label = QLabel("Reference Transcript (*)")
        ref_text_label.setObjectName("fieldLabel")
        layout.addWidget(ref_text_label)

        self.ref_text_edit = QTextEdit()
        self.ref_text_edit.setPlaceholderText(
            "Type the exact words spoken in the reference audio sample (vital for matching cadence and pronunciation)..."
        )
        self.ref_text_edit.setMinimumHeight(75)
        self.ref_text_edit.setMaximumHeight(110)
        layout.addWidget(self.ref_text_edit)

        # 5. Description (Optional)
        desc_label = QLabel("Description (optional)")
        desc_label.setObjectName("fieldLabel")
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("e.g. Warm male narrator, documentary style")
        layout.addWidget(desc_label)
        layout.addWidget(self.desc_edit)

        # Buttons
        layout.addSpacing(6)
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save Voice")
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._on_save_voice)
        btn_row.addWidget(self.save_btn)

        layout.addLayout(btn_row)

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background: #0f1724;
                color: #e8f0fa;
            }
            QLabel#dialogTitle {
                color: #f1f5f9;
                font-size: 16px;
                font-weight: bold;
            }
            QLabel#dialogSubtitle {
                color: #94a3b8;
                font-size: 12px;
                margin-bottom: 4px;
            }
            QLabel#fieldLabel {
                color: #cbd5e1;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#fieldHint {
                color: #64748b;
                font-size: 11px;
            }
            QLineEdit, QTextEdit, QComboBox {
                background: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 13px;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border: 1px solid #38bdf8;
            }
            QPushButton {
                background: #334155;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 7px 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #475569;
            }
            QPushButton#primaryBtn {
                background: #0284c7;
                color: #ffffff;
                border: none;
                font-weight: 600;
                padding: 7px 20px;
            }
            QPushButton#primaryBtn:hover {
                background: #0369a1;
            }
            QPushButton:disabled {
                opacity: 0.5;
                background: #1e293b;
                color: #64748b;
                border-color: #334155;
            }
        """)

    def _on_browse_audio(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Reference Audio File",
            "",
            "Audio Files (*.wav *.mp3 *.m4a *.flac *.ogg);;All Files (*.*)",
        )
        if file_path:
            self.audio_path_edit.setText(file_path)
            self.play_audio_btn.setEnabled(True)
            self._stop_playback()

            if not self.name_edit.text().strip():
                stem = os.path.splitext(os.path.basename(file_path))[0]
                suggested_name = stem.replace("_", " ").replace("-", " ").title()
                self.name_edit.setText(suggested_name)

    def _on_toggle_play_audio(self):
        if getattr(self, "_winsound_active", False) or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_playback()
        else:
            file_path = self.audio_path_edit.text().strip()
            if not file_path or not os.path.exists(file_path):
                return
            abs_path = os.path.abspath(file_path)
            self._stop_playback()
            played_native = False
            if os.name == "nt" and abs_path.lower().endswith(".wav"):
                try:
                    import winsound
                    winsound.PlaySound(abs_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    self._winsound_active = True
                    played_native = True
                    self.play_audio_btn.setText("⏹ Stop")
                except Exception:
                    pass
            if not played_native:
                self.player.setSource(QUrl.fromLocalFile(abs_path))
                self.player.play()
                self.play_audio_btn.setText("⏹ Stop")

    def _stop_playback(self):
        if os.name == "nt":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        self._winsound_active = False
        try:
            self.player.stop()
        except Exception:
            pass
        self.play_audio_btn.setText("▶ Play")

    def _on_playback_state_changed(self, state):
        if state != QMediaPlayer.PlaybackState.PlayingState and not getattr(self, "_winsound_active", False):
            self.play_audio_btn.setText("▶ Play")

    def _on_save_voice(self):
        name = self.name_edit.text().strip()
        audio_path = self.audio_path_edit.text().strip()
        ref_text = self.ref_text_edit.toPlainText().strip()
        gender = str(self.gender_combo.currentData() or "male").strip()
        description = self.desc_edit.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Information", "Please enter a voice name.")
            self.name_edit.setFocus()
            return

        if not audio_path or not os.path.exists(audio_path):
            QMessageBox.warning(self, "Missing Information", "Please select a valid reference audio file.")
            self.browse_btn.setFocus()
            return

        if not ref_text:
            QMessageBox.warning(
                self,
                "Missing Information",
                "Please enter the reference transcript (what is spoken in the audio sample).",
            )
            self.ref_text_edit.setFocus()
            return

        self._stop_playback()

        try:
            saved_meta = save_cloned_voice(
                name=name,
                audio_path=audio_path,
                ref_text=ref_text,
                gender=gender,
                description=description,
            )
            self.created_voice_entry = saved_meta
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Voice Clone Error", f"Could not save cloned voice:\n{exc}")

    def closeEvent(self, event):
        self._stop_playback()
        super().closeEvent(event)

    def reject(self):
        self._stop_playback()
        super().reject()

    def accept(self):
        self._stop_playback()
        super().accept()
