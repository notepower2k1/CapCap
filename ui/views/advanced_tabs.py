import os

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
)


def build_advanced_group(gui, left_layout):
    gui.advanced_group = QGroupBox("Advanced Settings")
    advanced_layout = QVBoxLayout(gui.advanced_group)
    advanced_layout.setSpacing(12)

    gui.enable_ai_polish_cb = QCheckBox("Use AI Translation (better but slower)", gui.advanced_group)
    gui.enable_ai_polish_cb.setChecked(True)

    advanced_layout.addWidget(gui.enable_ai_polish_cb)

    _build_hidden_runtime_widgets(gui)
    left_layout.addWidget(gui.advanced_group, 1)


def _build_hidden_runtime_widgets(gui):
    gui.audio_folder_edit = QLineEdit(os.path.join(os.getcwd(), "temp"), gui)
    gui.audio_source_edit = QLineEdit(gui)
    gui.srt_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"), gui)
    gui.keep_audio_cb = QCheckBox("Keep extracted audio", gui)
    gui.keep_audio_cb.setChecked(True)

    gui.extract_btn = QPushButton("Extract Audio", gui)
    gui.vocal_sep_btn = QPushButton("Separate Voice and Background", gui)
    gui.transcribe_btn = QPushButton("Create Original Subtitle", gui)
    gui.translate_btn = QPushButton("Translate to Vietnamese", gui)

    gui.bg_music_edit = QLineEdit(gui)
    gui.mixed_audio_edit = QLineEdit(gui)
    gui.use_generated_audio_radio = QRadioButton("Use generated Vietnamese voice", gui)
    gui.use_existing_audio_radio = QRadioButton("Use existing mixed audio", gui)
    gui.use_generated_audio_radio.setChecked(True)
    gui.audio_source_hint_label = gui.make_helper_label(
        "Preview and export will use the generated voice or voice+background mix by default."
    )
    gui.audio_source_hint_label.setParent(gui)
    gui.voice_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"), gui)

    gui.voice_gain_spin = QDoubleSpinBox(gui)
    gui.voice_gain_spin.setRange(-30.0, 30.0)
    gui.voice_gain_spin.setSingleStep(1.0)
    gui.voice_gain_spin.setValue(6.0)
    gui.bg_gain_spin = QDoubleSpinBox(gui)
    gui.bg_gain_spin.setRange(-30.0, 30.0)
    gui.bg_gain_spin.setSingleStep(1.0)
    gui.bg_gain_spin.setValue(-3.0)

    gui.voiceover_btn = QPushButton("Generate Voice / Mix", gui)
    gui.keep_timeline_cb = QCheckBox("Keep the current timeline when editing Vietnamese text", gui)
    gui.keep_timeline_cb.setChecked(True)
    gui.apply_translated_btn = QPushButton("Apply Edited Subtitle To Preview", gui)
    gui.apply_translated_btn.clicked.connect(gui.apply_edited_translation)
    gui.apply_translated_btn.hide()
    gui.auto_preview_frame_cb = QCheckBox("Auto refresh exact frame preview", gui)
    gui.auto_preview_frame_cb.setChecked(False)
    gui.auto_preview_frame_cb.hide()

    gui.show_artifacts_btn = QPushButton("Show Processed Files", gui)
    gui.show_artifacts_btn.clicked.connect(gui.show_processed_files)
    gui.open_temp_btn = QPushButton("Open Temp Folder", gui)
    gui.open_temp_btn.clicked.connect(lambda: gui.open_folder(gui.audio_folder_edit.text()))

    gui.log_view = QTextEdit(gui)
    gui.log_view.setReadOnly(True)
    gui.log_view.setPlaceholderText("Errors and detailed logs will appear here...")
    gui.log_copy_btn = QPushButton("Copy Log", gui)
    gui.log_copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(gui.log_view.toPlainText()))
    gui.log_clear_btn = QPushButton("Clear Log", gui)
    gui.log_clear_btn.clicked.connect(gui.clear_log)

    hidden_widgets = [
        gui.audio_folder_edit,
        gui.audio_source_edit,
        gui.srt_output_folder_edit,
        gui.keep_audio_cb,
        gui.extract_btn,
        gui.vocal_sep_btn,
        gui.transcribe_btn,
        gui.translate_btn,
        gui.bg_music_edit,
        gui.mixed_audio_edit,
        gui.use_generated_audio_radio,
        gui.use_existing_audio_radio,
        gui.audio_source_hint_label,
        gui.voice_output_folder_edit,
        gui.voice_gain_spin,
        gui.bg_gain_spin,
        gui.voiceover_btn,
        gui.apply_translated_btn,
        gui.auto_preview_frame_cb,
        gui.show_artifacts_btn,
        gui.open_temp_btn,
        gui.log_view,
        gui.log_copy_btn,
        gui.log_clear_btn,
    ]
    for widget in hidden_widgets:
        widget.hide()
        widget.setVisible(False)
        widget.setGeometry(0, 0, 0, 0)
