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
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def build_advanced_group(gui, left_layout):
    gui.advanced_group = QGroupBox("ADVANCED")
    gui.advanced_group.setCheckable(True)
    gui.advanced_group.setChecked(False)
    advanced_layout = QVBoxLayout(gui.advanced_group)
    advanced_layout.addWidget(gui.make_helper_label("Open this section when you want manual controls and troubleshooting tools."))

    gui.tabs = QTabWidget()
    advanced_layout.addWidget(gui.tabs, 1)
    left_layout.addWidget(gui.advanced_group, 1)

    tab_prepare = QWidget()
    tab_subtitles = QWidget()
    tab_voice = QWidget()
    tab_tools = QWidget()
    gui.tabs.addTab(tab_prepare, "1. Prepare")
    gui.tabs.addTab(tab_subtitles, "2. Subtitles")
    gui.tabs.addTab(tab_voice, "3. Voice")
    gui.tabs.addTab(tab_tools, "4. Tools")

    prepare_layout = QVBoxLayout(tab_prepare)
    subtitles_layout = QVBoxLayout(tab_subtitles)
    voice_tab_layout = QVBoxLayout(tab_voice)
    tools_layout = QVBoxLayout(tab_tools)
    prepare_layout.setSpacing(12)
    subtitles_layout.setSpacing(12)
    voice_tab_layout.setSpacing(12)
    tools_layout.setSpacing(12)

    _build_prepare_tab(gui, prepare_layout)
    _build_subtitles_tab(gui, subtitles_layout)
    _build_voice_tab(gui, voice_tab_layout)
    _build_tools_tab(gui, tools_layout)

    prepare_layout.addStretch()
    subtitles_layout.addStretch()
    voice_tab_layout.addStretch()
    tools_layout.addStretch()
    tools_layout.addWidget(QLabel("CapCap guided workflow"))


def _build_prepare_tab(gui, layout):
    group = QGroupBox("STEP 1: PREPARE AUDIO")
    group_layout = QVBoxLayout(group)
    group_layout.addWidget(
        gui.make_helper_label(
            "Use this only when you want to run each step manually. If you are new, the top button 'Create Vietnamese Output' already performs these steps in order."
        )
    )

    gui.audio_folder_edit = QLineEdit(os.path.join(os.getcwd(), "temp"))
    browse_folder_btn = QPushButton("Target Folder")
    browse_folder_btn.clicked.connect(gui.browse_audio_folder)
    folder_layout = QHBoxLayout()
    folder_layout.addWidget(gui.audio_folder_edit)
    folder_layout.addWidget(browse_folder_btn)

    gui.keep_audio_cb = QCheckBox("Keep audio file after completion")
    gui.keep_audio_cb.setChecked(True)
    gui.extract_btn = QPushButton("Extract Audio")
    gui.extract_btn.setObjectName("mainActionBtn")
    gui.vocal_sep_btn = QPushButton("Separate Voice and Background")
    gui.vocal_sep_btn.setObjectName("mainActionBtn")

    group_layout.addWidget(QLabel("Temporary audio folder"))
    group_layout.addLayout(folder_layout)
    group_layout.addWidget(gui.keep_audio_cb)
    group_layout.addWidget(gui.extract_btn)
    group_layout.addWidget(gui.vocal_sep_btn)
    layout.addWidget(group)


def _build_subtitles_tab(gui, layout):
    trans_group = QGroupBox("STEP 2: CREATE ORIGINAL SUBTITLES")
    trans_layout = QVBoxLayout(trans_group)
    trans_layout.addWidget(gui.make_helper_label("Turn the source speech into an editable subtitle track."))

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

    gui.transcript_text = QTextEdit()
    gui.transcript_text.setPlaceholderText("The original subtitle transcript will appear here...")
    gui.transcribe_btn = QPushButton("Create Original Subtitle")
    gui.transcribe_btn.setObjectName("mainActionBtn")
    gui.stabilize_button(gui.transcribe_btn, min_width=320)

    trans_layout.addWidget(QLabel("Audio source"))
    trans_layout.addLayout(audio_src_layout)
    trans_layout.addWidget(QLabel("Where to save the original SRT"))
    trans_layout.addLayout(srt_folder_layout)
    trans_layout.addWidget(gui.transcript_text)
    trans_layout.addWidget(gui.transcribe_btn)
    layout.addWidget(trans_group)

    translate_group = QGroupBox("STEP 3: VIETNAMESE TRANSLATION")
    translate_layout = QVBoxLayout(translate_group)
    translate_layout.addWidget(gui.make_helper_label("Review the Vietnamese text here, edit if needed, then push it to the preview player."))

    gui.lang_target_combo = QComboBox()
    gui.lang_target_combo.addItems(["Vietnamese (vie_Latn)", "English (eng_Latn)"])
    gui.lang_target_combo.setCurrentText("Vietnamese (vie_Latn)")
    gui.lang_target_combo.setEnabled(False)
    gui.translated_text = QTextEdit()
    gui.translated_text.setPlaceholderText("Vietnamese subtitle text will appear here. You can edit it before export.")
    gui.translate_btn = QPushButton("Translate to Vietnamese")
    gui.translate_btn.setObjectName("mainActionBtn")
    gui.stabilize_button(gui.translate_btn, min_width=320)
    gui.keep_timeline_cb = QCheckBox("Keep the current timeline when editing Vietnamese text")
    gui.keep_timeline_cb.setChecked(True)
    gui.apply_translated_btn = QPushButton("Apply Edited Subtitle To Preview")
    gui.apply_translated_btn.clicked.connect(gui.apply_edited_translation)
    gui.auto_preview_frame_cb = QCheckBox("Auto refresh exact frame preview")
    gui.auto_preview_frame_cb.setChecked(True)

    style_group = QGroupBox("SUBTITLE LOOK")
    style_layout = QVBoxLayout(style_group)
    gui.subtitle_font_combo = QComboBox()
    gui.subtitle_font_combo.setEditable(True)
    gui.subtitle_font_combo.addItems(["Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman"])
    gui.subtitle_font_combo.setCurrentText("Segoe UI")
    gui.subtitle_font_size_spin = QSpinBox()
    gui.subtitle_font_size_spin.setRange(12, 72)
    gui.subtitle_font_size_spin.setValue(60)
    gui.subtitle_align_combo = QComboBox()
    gui.subtitle_align_combo.addItems(["Bottom Center", "Bottom Left", "Bottom Right", "Center", "Top Center"])
    gui.subtitle_align_combo.setCurrentText("Bottom Center")
    gui.subtitle_x_offset_spin = QSpinBox()
    gui.subtitle_x_offset_spin.setRange(-400, 400)
    gui.subtitle_x_offset_spin.setValue(0)
    gui.subtitle_bottom_offset_spin = QSpinBox()
    gui.subtitle_bottom_offset_spin.setRange(0, 300)
    gui.subtitle_bottom_offset_spin.setValue(30)
    gui.subtitle_color_btn = QPushButton("Text Color: #FFFFFF")
    gui.subtitle_color_btn.clicked.connect(gui.choose_subtitle_color)
    gui.subtitle_color_hex = "#FFFFFF"

    style_row_1 = QHBoxLayout()
    style_row_1.addWidget(QLabel("Font"))
    style_row_1.addWidget(gui.subtitle_font_combo)
    style_row_1.addWidget(QLabel("Size"))
    style_row_1.addWidget(gui.subtitle_font_size_spin)
    style_row_2 = QHBoxLayout()
    style_row_2.addWidget(QLabel("Position"))
    style_row_2.addWidget(gui.subtitle_align_combo)
    style_row_2.addWidget(QLabel("X Offset"))
    style_row_2.addWidget(gui.subtitle_x_offset_spin)
    style_row_2.addWidget(QLabel("Vertical Offset"))
    style_row_2.addWidget(gui.subtitle_bottom_offset_spin)
    style_layout.addLayout(style_row_1)
    style_layout.addLayout(style_row_2)
    style_layout.addWidget(gui.subtitle_color_btn)

    translate_layout.addWidget(QLabel("Output language"))
    translate_layout.addWidget(gui.lang_target_combo)
    translate_layout.addWidget(style_group)
    translate_layout.addWidget(gui.translated_text)
    translate_layout.addWidget(gui.translate_btn)
    translate_layout.addWidget(gui.keep_timeline_cb)
    translate_layout.addWidget(gui.apply_translated_btn)
    translate_layout.addWidget(gui.auto_preview_frame_cb)
    layout.addWidget(translate_group)


def _build_voice_tab(gui, layout):
    group = QGroupBox("STEP 4: VIETNAMESE VOICE")
    group_layout = QVBoxLayout(group)
    group_layout.addWidget(gui.make_helper_label("Only needed for voice output modes. You can skip this entire card when exporting subtitles only."))

    gui.voice_name_combo = QComboBox()
    gui.voice_name_combo.addItems(["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"])
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
        "Preview and export will use the generated voice or voice+background mix by default. Switch to existing mixed audio when you want to override it."
    )

    gui.voice_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
    browse_voice_out_btn = QPushButton("Output Folder")
    browse_voice_out_btn.clicked.connect(gui.browse_voice_output_folder)
    voice_out_layout = QHBoxLayout()
    voice_out_layout.addWidget(gui.voice_output_folder_edit)
    voice_out_layout.addWidget(browse_voice_out_btn)

    adv_group = QGroupBox("Advanced Voice Settings")
    adv_group.setCheckable(True)
    adv_group.setChecked(False)
    adv_layout = QVBoxLayout(adv_group)
    gains_layout = QHBoxLayout()
    gui.voice_gain_spin = QDoubleSpinBox()
    gui.voice_gain_spin.setRange(-30.0, 30.0)
    gui.voice_gain_spin.setSingleStep(1.0)
    gui.voice_gain_spin.setValue(6.0)
    gui.bg_gain_spin = QDoubleSpinBox()
    gui.bg_gain_spin.setRange(-30.0, 30.0)
    gui.bg_gain_spin.setSingleStep(1.0)
    gui.bg_gain_spin.setValue(-3.0)
    gains_layout.addWidget(QLabel("Voice Gain (dB):"))
    gains_layout.addWidget(gui.voice_gain_spin)
    gains_layout.addWidget(QLabel("BG Gain (dB):"))
    gains_layout.addWidget(gui.bg_gain_spin)
    adv_layout.addLayout(gains_layout)
    adv_layout.addWidget(QLabel("Where to save intermediate voice files"))
    adv_layout.addLayout(voice_out_layout)

    gui.voiceover_btn = QPushButton("Generate Voice / Mix")
    gui.voiceover_btn.setObjectName("mainActionBtn")
    gui.stabilize_button(gui.voiceover_btn, min_width=320)
    gui.preview_btn = QPushButton("Open Video Preview With Selected Audio")
    gui.preview_btn.clicked.connect(gui.preview_video_with_mixed_audio)
    gui.stabilize_button(gui.preview_btn, min_width=320)

    group_layout.addWidget(QLabel("Voice"))
    group_layout.addWidget(gui.voice_name_combo)
    group_layout.addWidget(QLabel("Audio source for preview/export"))
    group_layout.addWidget(gui.use_generated_audio_radio)
    group_layout.addWidget(gui.use_existing_audio_radio)
    group_layout.addWidget(gui.audio_source_hint_label)
    group_layout.addWidget(QLabel("Background audio"))
    group_layout.addLayout(bg_layout)
    group_layout.addWidget(QLabel("Existing mixed audio"))
    group_layout.addLayout(mixed_layout)
    group_layout.addWidget(adv_group)
    group_layout.addWidget(gui.voiceover_btn)
    group_layout.addWidget(gui.preview_btn)
    layout.addWidget(group)


def _build_tools_tab(gui, layout):
    artifacts_group = QGroupBox("RESULTS AND FILES")
    artifacts_layout = QVBoxLayout(artifacts_group)
    artifacts_layout.addWidget(gui.make_helper_label("Open generated files quickly when you want to inspect or reuse outputs."))
    gui.show_artifacts_btn = QPushButton("Show Processed Files")
    gui.show_artifacts_btn.clicked.connect(gui.show_processed_files)
    gui.open_temp_btn = QPushButton("Open Temp Folder")
    gui.open_temp_btn.clicked.connect(lambda: gui.open_folder(gui.audio_folder_edit.text()))
    artifacts_layout.addWidget(gui.show_artifacts_btn)
    artifacts_layout.addWidget(gui.open_temp_btn)
    artifacts_layout.addWidget(gui.open_output_btn)
    layout.addWidget(artifacts_group)

    log_group = QGroupBox("LOG")
    log_layout = QVBoxLayout(log_group)
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
    log_layout.addWidget(gui.log_view)
    log_layout.addLayout(log_btns)
    layout.addWidget(log_group)
