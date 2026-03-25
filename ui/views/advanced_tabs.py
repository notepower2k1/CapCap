import os

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def build_advanced_group(gui, left_layout):
    gui.advanced_group = QGroupBox("Advanced Settings")
    gui.advanced_group.setCheckable(True)
    gui.advanced_group.setChecked(False)
    advanced_layout = QVBoxLayout(gui.advanced_group)
    advanced_layout.setSpacing(12)

    gui.enable_ai_polish_cb = QCheckBox("Use AI Translation (better but slower)")
    gui.enable_ai_polish_cb.setChecked(True)
    gui.enable_separate_audio_cb = QCheckBox("Separate voice & background")
    gui.enable_separate_audio_cb.setChecked(True)
    gui.auto_line_break_cb = QCheckBox("Auto line break subtitle")
    gui.auto_line_break_cb.setChecked(True)

    advanced_layout.addWidget(gui.enable_ai_polish_cb)
    advanced_layout.addWidget(gui.enable_separate_audio_cb)
    advanced_layout.addWidget(gui.auto_line_break_cb)
    advanced_layout.addWidget(gui.make_helper_label("Open the manual tools below only when you need troubleshooting or step-by-step control."))

    gui.tabs = QTabWidget()
    advanced_layout.addWidget(gui.tabs, 1)
    left_layout.addWidget(gui.advanced_group, 1)

    tab_prepare = QWidget()
    tab_voice = QWidget()
    tab_tools = QWidget()
    gui.tabs.addTab(tab_prepare, "Prepare")
    gui.tabs.addTab(tab_voice, "Voice")
    gui.tabs.addTab(tab_tools, "Tools")

    prepare_layout = QVBoxLayout(tab_prepare)
    voice_tab_layout = QVBoxLayout(tab_voice)
    tools_layout = QVBoxLayout(tab_tools)

    _build_prepare_tab(gui, prepare_layout)
    _build_voice_tab(gui, voice_tab_layout)
    _build_tools_tab(gui, tools_layout)

    prepare_layout.addStretch()
    voice_tab_layout.addStretch()
    tools_layout.addStretch()


def _build_prepare_tab(gui, layout):
    gui.audio_folder_edit = QLineEdit(os.path.join(os.getcwd(), "temp"))
    browse_folder_btn = QPushButton("Temp Folder")
    browse_folder_btn.clicked.connect(gui.browse_audio_folder)
    folder_layout = QHBoxLayout()
    folder_layout.addWidget(gui.audio_folder_edit)
    folder_layout.addWidget(browse_folder_btn)

    gui.audio_source_edit = QLineEdit()
    gui.audio_source_edit.setPlaceholderText("Optional: choose a custom audio file...")
    browse_audio_src_btn = QPushButton("Choose Audio")
    browse_audio_src_btn.clicked.connect(gui.browse_audio_source)
    audio_src_layout = QHBoxLayout()
    audio_src_layout.addWidget(gui.audio_source_edit)
    audio_src_layout.addWidget(browse_audio_src_btn)

    gui.srt_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
    browse_srt_folder_btn = QPushButton("SRT Folder")
    browse_srt_folder_btn.clicked.connect(gui.browse_srt_output_folder)
    srt_folder_layout = QHBoxLayout()
    srt_folder_layout.addWidget(gui.srt_output_folder_edit)
    srt_folder_layout.addWidget(browse_srt_folder_btn)

    gui.keep_audio_cb = QCheckBox("Keep extracted audio")
    gui.keep_audio_cb.setChecked(True)
    gui.extract_btn = QPushButton("Extract Audio")
    gui.extract_btn.setObjectName("mainActionBtn")
    gui.vocal_sep_btn = QPushButton("Separate Voice and Background")
    gui.vocal_sep_btn.setObjectName("mainActionBtn")
    gui.transcribe_btn = QPushButton("Create Original Subtitle")
    gui.transcribe_btn.setObjectName("mainActionBtn")
    gui.translate_btn = QPushButton("Translate to Vietnamese")
    gui.translate_btn.setObjectName("mainActionBtn")

    layout.addWidget(QLabel("Temporary audio folder"))
    layout.addLayout(folder_layout)
    layout.addWidget(QLabel("Audio source"))
    layout.addLayout(audio_src_layout)
    layout.addWidget(QLabel("Subtitle output folder"))
    layout.addLayout(srt_folder_layout)
    layout.addWidget(gui.keep_audio_cb)
    layout.addWidget(gui.extract_btn)
    layout.addWidget(gui.vocal_sep_btn)
    layout.addWidget(gui.transcribe_btn)
    layout.addWidget(gui.translate_btn)


def _build_voice_tab(gui, layout):
    gui.bg_music_edit = QLineEdit()
    gui.bg_music_edit.setPlaceholderText("Optional: background/no_vocals audio...")
    browse_bg_btn = QPushButton("Choose Background")
    browse_bg_btn.clicked.connect(gui.browse_background_audio)
    bg_layout = QHBoxLayout()
    bg_layout.addWidget(gui.bg_music_edit)
    bg_layout.addWidget(browse_bg_btn)

    gui.mixed_audio_edit = QLineEdit()
    gui.mixed_audio_edit.setPlaceholderText("Optional: use an existing mixed audio file...")
    browse_mixed_btn = QPushButton("Choose Mixed Audio")
    browse_mixed_btn.clicked.connect(gui.browse_existing_mixed_audio)
    mixed_layout = QHBoxLayout()
    mixed_layout.addWidget(gui.mixed_audio_edit)
    mixed_layout.addWidget(browse_mixed_btn)

    gui.use_generated_audio_radio = QRadioButton("Use generated Vietnamese voice")
    gui.use_existing_audio_radio = QRadioButton("Use existing mixed audio")
    gui.use_generated_audio_radio.setChecked(True)
    gui.audio_source_hint_label = gui.make_helper_label(
        "Preview and export will use the generated voice or voice+background mix by default."
    )

    gui.voice_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
    browse_voice_out_btn = QPushButton("Output Folder")
    browse_voice_out_btn.clicked.connect(gui.browse_voice_output_folder)
    voice_out_layout = QHBoxLayout()
    voice_out_layout.addWidget(gui.voice_output_folder_edit)
    voice_out_layout.addWidget(browse_voice_out_btn)

    gains_layout = QHBoxLayout()
    gui.voice_gain_spin = QDoubleSpinBox()
    gui.voice_gain_spin.setRange(-30.0, 30.0)
    gui.voice_gain_spin.setSingleStep(1.0)
    gui.voice_gain_spin.setValue(6.0)
    gui.bg_gain_spin = QDoubleSpinBox()
    gui.bg_gain_spin.setRange(-30.0, 30.0)
    gui.bg_gain_spin.setSingleStep(1.0)
    gui.bg_gain_spin.setValue(-3.0)
    gains_layout.addWidget(QLabel("Voice Gain (dB)"))
    gains_layout.addWidget(gui.voice_gain_spin)
    gains_layout.addWidget(QLabel("BG Gain (dB)"))
    gains_layout.addWidget(gui.bg_gain_spin)

    gui.voiceover_btn = QPushButton("Generate Voice / Mix")
    gui.voiceover_btn.setObjectName("mainActionBtn")
    gui.preview_btn = QPushButton("Preview With Current Audio")
    gui.preview_btn.clicked.connect(gui.preview_video_with_mixed_audio)

    layout.addWidget(QLabel("Background audio"))
    layout.addLayout(bg_layout)
    layout.addWidget(QLabel("Existing mixed audio"))
    layout.addLayout(mixed_layout)
    layout.addWidget(gui.use_generated_audio_radio)
    layout.addWidget(gui.use_existing_audio_radio)
    layout.addWidget(gui.audio_source_hint_label)
    layout.addLayout(gains_layout)
    layout.addWidget(QLabel("Voice output folder"))
    layout.addLayout(voice_out_layout)
    layout.addWidget(gui.voiceover_btn)
    layout.addWidget(gui.preview_btn)


def _build_tools_tab(gui, layout):
    gui.keep_timeline_cb = QCheckBox("Keep the current timeline when editing Vietnamese text")
    gui.keep_timeline_cb.setChecked(True)
    gui.apply_translated_btn = QPushButton("Apply Edited Subtitle To Preview")
    gui.apply_translated_btn.clicked.connect(gui.apply_edited_translation)
    gui.apply_translated_btn.hide()
    gui.auto_preview_frame_cb = QCheckBox("Auto refresh exact frame preview")
    gui.auto_preview_frame_cb.setChecked(False)
    gui.auto_preview_frame_cb.hide()

    layout.addWidget(gui.keep_timeline_cb)

    gui.show_artifacts_btn = QPushButton("Show Processed Files")
    gui.show_artifacts_btn.clicked.connect(gui.show_processed_files)
    gui.open_temp_btn = QPushButton("Open Temp Folder")
    gui.open_temp_btn.clicked.connect(lambda: gui.open_folder(gui.audio_folder_edit.text()))
    layout.addWidget(gui.show_artifacts_btn)
    layout.addWidget(gui.open_temp_btn)
    layout.addWidget(gui.open_output_btn)

    gui.log_view = QTextEdit()
    gui.log_view.setReadOnly(True)
    gui.log_view.setPlaceholderText("Errors and detailed logs will appear here...")
    log_btns = QHBoxLayout()
    gui.log_copy_btn = QPushButton("Copy Log")
    gui.log_copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(gui.log_view.toPlainText()))
    gui.log_clear_btn = QPushButton("Clear Log")
    gui.log_clear_btn.clicked.connect(gui.clear_log)
    log_btns.addWidget(gui.log_copy_btn)
    log_btns.addWidget(gui.log_clear_btn)
    layout.addWidget(gui.log_view)
    layout.addLayout(log_btns)
