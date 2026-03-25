import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _section_title(text):
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def _section_card():
    card = QFrame()
    card.setObjectName("statusCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(10)
    return card, layout


def _build_collapsible_section(title: str, start_expanded: bool = True):
    wrapper = QFrame()
    wrapper.setObjectName("statusCard")
    wrapper_layout = QVBoxLayout(wrapper)
    wrapper_layout.setContentsMargins(12, 12, 12, 12)
    wrapper_layout.setSpacing(10)

    toggle_btn = QToolButton()
    toggle_btn.setText(("▼ " if start_expanded else "▶ ") + title)
    toggle_btn.setCheckable(True)
    toggle_btn.setChecked(start_expanded)
    toggle_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    toggle_btn.setStyleSheet("QToolButton { text-align: left; font-weight: 700; color: #8ad7ff; border: none; padding: 0; }")

    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(10)
    content.setVisible(start_expanded)

    def _toggle_section(checked: bool):
        toggle_btn.setText(("▼ " if checked else "▶ ") + title)
        content.setVisible(checked)

    toggle_btn.toggled.connect(_toggle_section)
    wrapper_layout.addWidget(toggle_btn)
    wrapper_layout.addWidget(content)
    return wrapper, content_layout


def _build_hidden_status_widgets(gui):
    gui.workflow_hint_label = QLabel()
    gui.workflow_hint_label.hide()
    gui.workflow_status_badge = QLabel("Waiting for video")
    gui.workflow_status_badge.hide()
    gui.next_step_label = QLabel()
    gui.next_step_label.hide()
    gui.readiness_label = QLabel()
    gui.readiness_label.hide()


def build_start_group(gui, left_layout):
    gui.video_path_edit = QLineEdit()
    gui.video_path_edit.setPlaceholderText("Choose one video to process...")
    gui.video_path_edit.hide()

    gui.final_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
    gui.final_output_folder_edit.setPlaceholderText("Folder to save final results...")
    gui.final_output_folder_edit.hide()

    gui.run_all_btn = QPushButton("Generate")
    gui.run_all_btn.setObjectName("mainActionBtn")
    gui.run_all_btn.clicked.connect(gui.run_all_pipeline)

    gui.export_btn = QPushButton("Export")
    gui.export_btn.setObjectName("mainActionBtn")
    gui.export_btn.clicked.connect(gui.export_final_video)

    gui.preview_5s_btn = QPushButton("Open 5-Second Preview")
    gui.preview_5s_btn.clicked.connect(gui.preview_five_seconds)
    gui.preview_frame_btn = QPushButton("Open Large Frame Preview")
    gui.preview_frame_btn.clicked.connect(gui.preview_exact_frame)
    gui.open_output_btn = QPushButton("Open Results Folder")
    gui.open_output_btn.clicked.connect(lambda: gui.open_folder(gui.final_output_folder_edit.text()))

    gui.stabilize_button(gui.run_all_btn, min_width=240)
    gui.stabilize_button(gui.export_btn, min_width=180)

    control_group = QGroupBox("Controls")
    control_layout = QVBoxLayout(control_group)
    control_layout.setSpacing(14)

    upload_card, upload_layout = _build_collapsible_section("Section 1: Input")
    gui.upload_video_btn = QPushButton("Upload Video")
    gui.upload_video_btn.setObjectName("mainActionBtn")
    gui.upload_video_btn.clicked.connect(gui.browse_video)
    gui.upload_video_btn.setMinimumHeight(46)
    gui.upload_hint_label = QLabel("Drag & drop or browse")
    gui.upload_hint_label.setObjectName("helperLabel")
    gui.upload_hint_label.setAlignment(Qt.AlignCenter)
    gui.upload_status_label = QLabel("No video uploaded yet")
    gui.upload_status_label.setObjectName("statusBody")
    gui.upload_status_label.setWordWrap(True)
    upload_layout.addWidget(gui.upload_video_btn)
    upload_layout.addWidget(gui.upload_hint_label)
    upload_layout.addWidget(gui.upload_status_label)
    control_layout.addWidget(upload_card)

    output_card, output_layout = _build_collapsible_section("Section 2: Output Type")
    output_layout.addWidget(QLabel("Output:"))
    gui.output_mode_combo = QComboBox()
    gui.output_mode_combo.addItems(
        [
            "Vietnamese subtitles only",
            "Vietnamese voice only",
            "Vietnamese subtitles + voice",
        ]
    )
    gui.output_mode_combo.setCurrentText("Vietnamese subtitles + voice")
    gui.output_mode_combo.hide()
    gui.output_subtitle_radio = QRadioButton("Subtitle only")
    gui.output_voice_radio = QRadioButton("Voice only")
    gui.output_both_radio = QRadioButton("Subtitle + Voice")
    gui.output_both_radio.setChecked(True)
    gui.output_mode_group = QButtonGroup(gui)
    gui.output_mode_group.addButton(gui.output_subtitle_radio)
    gui.output_mode_group.addButton(gui.output_voice_radio)
    gui.output_mode_group.addButton(gui.output_both_radio)
    gui.output_subtitle_radio.toggled.connect(
        lambda checked: checked and gui.output_mode_combo.setCurrentText("Vietnamese subtitles only")
    )
    gui.output_voice_radio.toggled.connect(
        lambda checked: checked and gui.output_mode_combo.setCurrentText("Vietnamese voice only")
    )
    gui.output_both_radio.toggled.connect(
        lambda checked: checked and gui.output_mode_combo.setCurrentText("Vietnamese subtitles + voice")
    )
    output_layout.addWidget(gui.output_subtitle_radio)
    output_layout.addWidget(gui.output_voice_radio)
    output_layout.addWidget(gui.output_both_radio)
    control_layout.addWidget(output_card)

    language_card, language_layout = _build_collapsible_section("Section 3: Language")
    gui.lang_whisper_combo = QComboBox()
    gui.lang_whisper_combo.addItem("Auto detect", "auto")
    gui.lang_whisper_combo.addItem("Chinese", "zh")
    gui.lang_whisper_combo.addItem("Korean", "ko")
    gui.lang_whisper_combo.addItem("Japanese", "ja")
    gui.lang_whisper_combo.addItem("English", "en")
    gui.lang_whisper_combo.addItem("Vietnamese", "vi")
    gui.lang_target_combo = QComboBox()
    gui.lang_target_combo.addItem("Vietnamese", "vi")
    gui.lang_target_combo.setCurrentIndex(0)
    source_row = QVBoxLayout()
    source_row.addWidget(QLabel("Source Language"))
    source_row.addWidget(gui.lang_whisper_combo)
    target_row = QVBoxLayout()
    target_row.addWidget(QLabel("Target Language"))
    target_row.addWidget(gui.lang_target_combo)
    language_layout.addLayout(source_row)
    language_layout.addLayout(target_row)
    control_layout.addWidget(language_card)

    voice_card, voice_layout = _build_collapsible_section("Section 4: Voice")
    gui.voice_section_card = voice_card
    gui.voice_name_combo = QComboBox()
    gui.voice_name_combo.addItem("Female AI voice", "vi-VN-HoaiMyNeural")
    gui.voice_name_combo.addItem("Male AI voice", "vi-VN-NamMinhNeural")
    gui.voice_name_combo.setCurrentIndex(0)
    gui.voice_speed_spin = QComboBox()
    gui.voice_speed_spin.addItems(["0.9x", "1.0x", "1.1x", "1.2x"])
    gui.voice_speed_spin.setCurrentText("1.0x")
    gui.voice_tone_combo = QComboBox()
    gui.voice_tone_combo.addItems(["Natural", "Warm", "Bright"])
    gui.voice_tone_combo.setCurrentText("Natural")
    voice_layout.addWidget(QLabel("Voice"))
    voice_layout.addWidget(gui.voice_name_combo)
    voice_layout.addWidget(QLabel("Speed"))
    voice_layout.addWidget(gui.voice_speed_spin)
    voice_layout.addWidget(QLabel("Tone"))
    voice_layout.addWidget(gui.voice_tone_combo)
    control_layout.addWidget(voice_card)

    subtitle_card, subtitle_layout = _build_collapsible_section("Section 5: Subtitle Style")
    gui.subtitle_font_combo = QComboBox()
    gui.subtitle_font_combo.setEditable(True)
    gui.subtitle_font_combo.addItems(["Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman"])
    gui.subtitle_font_combo.setCurrentText("Segoe UI")
    gui.subtitle_font_size_spin = QSpinBox()
    gui.subtitle_font_size_spin.setRange(12, 72)
    gui.subtitle_font_size_spin.setValue(60)
    gui.subtitle_color_btn = QPushButton("Color: #FFFFFF")
    gui.subtitle_color_hex = "#FFFFFF"
    gui.subtitle_color_btn.clicked.connect(gui.choose_subtitle_color)
    gui.subtitle_background_cb = QCheckBox("Background")
    gui.subtitle_background_cb.setChecked(False)
    subtitle_layout.addWidget(QLabel("Font"))
    subtitle_layout.addWidget(gui.subtitle_font_combo)
    subtitle_layout.addWidget(QLabel("Size"))
    subtitle_layout.addWidget(gui.subtitle_font_size_spin)
    subtitle_layout.addWidget(QLabel("Color"))
    subtitle_layout.addWidget(gui.subtitle_color_btn)
    subtitle_layout.addWidget(gui.subtitle_background_cb)
    gui.subtitle_align_combo = QComboBox()
    gui.subtitle_align_combo.addItems(["Bottom Center", "Bottom Left", "Bottom Right", "Center", "Top Center"])
    gui.subtitle_align_combo.setCurrentText("Bottom Center")
    gui.subtitle_align_combo.hide()
    gui.subtitle_x_offset_spin = QSpinBox()
    gui.subtitle_x_offset_spin.setRange(-400, 400)
    gui.subtitle_x_offset_spin.setValue(0)
    gui.subtitle_x_offset_spin.hide()
    gui.subtitle_bottom_offset_spin = QSpinBox()
    gui.subtitle_bottom_offset_spin.setRange(0, 300)
    gui.subtitle_bottom_offset_spin.setValue(30)
    gui.subtitle_bottom_offset_spin.hide()
    control_layout.addWidget(subtitle_card)

    action_card, action_layout = _build_collapsible_section("Section 7: Action + Progress")
    action_layout.addWidget(gui.run_all_btn)
    action_layout.addWidget(QLabel("Progress:"))
    gui.progress_audio_label = QLabel("⬜ Audio analyzed")
    gui.progress_subtitle_label = QLabel("⬜ Subtitle created")
    gui.progress_translate_label = QLabel("⬜ Translating")
    gui.progress_voice_label = QLabel("⬜ Generating voice")
    action_layout.addWidget(gui.progress_audio_label)
    action_layout.addWidget(gui.progress_subtitle_label)
    action_layout.addWidget(gui.progress_translate_label)
    action_layout.addWidget(gui.progress_voice_label)
    control_layout.addWidget(action_card)

    _build_hidden_status_widgets(gui)

    left_layout.addWidget(control_group)


def build_workflow_group(left_layout):
    return None
