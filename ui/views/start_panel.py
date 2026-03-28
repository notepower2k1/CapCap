import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
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


def _build_style_preset_card(title: str, line_one: str, line_two: str, radio: QRadioButton):
    card = QFrame()
    card.setObjectName("statusCard")
    card.setStyleSheet(
        "QFrame#statusCard { background-color: #132132; border: 1px solid #35506f; border-radius: 14px; }"
        "QFrame#statusCard:hover { border-color: #5aa6d9; }"
    )
    layout = QVBoxLayout(card)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(4)
    layout.addWidget(radio, 0, Qt.AlignTop)

    title_label = QLabel(title)
    title_label.setObjectName("sectionTitle")
    preview_top = QLabel(line_one)
    preview_top.setStyleSheet("font-size: 14px; font-weight: 800; color: #ffffff;")
    preview_bottom = QLabel(line_two)
    preview_bottom.setStyleSheet("font-size: 13px; color: #dbe5f3;")
    preview_top.setAlignment(Qt.AlignCenter)
    preview_bottom.setAlignment(Qt.AlignCenter)

    layout.addWidget(title_label, 0, Qt.AlignCenter)
    layout.addWidget(preview_top)
    layout.addWidget(preview_bottom)
    return card


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
    gui.voice_tier_group = QButtonGroup(gui)
    gui.use_free_voice_radio = QRadioButton("Use free voice")
    gui.use_premium_voice_radio = QRadioButton("Use premium voice")
    gui.use_free_voice_radio.setChecked(True)
    gui.voice_tier_group.addButton(gui.use_free_voice_radio)
    gui.voice_tier_group.addButton(gui.use_premium_voice_radio)
    gui.free_voice_combo = QComboBox()
    gui.premium_voice_combo = QComboBox()
    gui.voice_gender_combo = QComboBox()
    gui.voice_gender_combo.addItems(["Any", "Male", "Female"])
    gui.voice_gender_combo.currentTextChanged.connect(gui.on_voice_gender_changed)
    gui.voice_speed_spin = QComboBox()
    gui.voice_speed_spin.addItems(["0.9x", "1.0x", "1.1x", "1.2x"])
    gui.voice_speed_spin.setCurrentText("1.0x")
    gui.voice_timing_sync_combo = QComboBox()
    gui.voice_timing_sync_combo.addItems(["Off", "Smart", "Force Fit"])
    gui.voice_timing_sync_combo.setCurrentText("Smart")
    gui.voice_timing_sync_hint_label = QLabel("Auto sync voice to subtitle timing")
    gui.voice_timing_sync_hint_label.setObjectName("helperLabel")
    gui.preview_voice_btn = QPushButton("Preview voice")
    gui.preview_voice_btn.clicked.connect(gui.preview_selected_voice_sample)
    gui.voice_preview_meta_label = QLabel("Premium voice preview uses the configured sample clip.")
    gui.voice_preview_meta_label.setObjectName("helperLabel")
    gui.voice_preview_meta_label.setWordWrap(True)
    voice_layout.addWidget(gui.use_free_voice_radio)
    voice_layout.addWidget(gui.free_voice_combo)
    voice_layout.addWidget(gui.use_premium_voice_radio)
    voice_layout.addWidget(gui.premium_voice_combo)
    voice_layout.addWidget(QLabel("Gender"))
    voice_layout.addWidget(gui.voice_gender_combo)
    voice_layout.addWidget(QLabel("Speed"))
    voice_layout.addWidget(gui.voice_speed_spin)
    voice_layout.addWidget(gui.preview_voice_btn)
    voice_layout.addWidget(gui.voice_preview_meta_label)
    voice_layout.addWidget(gui.voice_timing_sync_hint_label)
    voice_layout.addWidget(gui.voice_timing_sync_combo)
    control_layout.addWidget(voice_card)

    subtitle_card, subtitle_layout = _build_collapsible_section("Section 5: Subtitle Style")
    base_style_label = QLabel("Base style")
    base_style_label.setObjectName("sectionTitle")
    subtitle_layout.addWidget(base_style_label)

    gui.subtitle_preset_tiktok_radio = QRadioButton("TikTok")
    gui.subtitle_preset_youtube_radio = QRadioButton("YouTube")
    gui.subtitle_preset_minimal_radio = QRadioButton("Short")
    gui.subtitle_preset_custom_radio = QRadioButton("Custom")
    gui.subtitle_preset_tiktok_radio.setChecked(True)
    gui.subtitle_preset_group = QButtonGroup(gui)
    gui.subtitle_preset_group.addButton(gui.subtitle_preset_tiktok_radio)
    gui.subtitle_preset_group.addButton(gui.subtitle_preset_youtube_radio)
    gui.subtitle_preset_group.addButton(gui.subtitle_preset_minimal_radio)
    gui.subtitle_preset_group.addButton(gui.subtitle_preset_custom_radio)

    preset_grid = QGridLayout()
    preset_grid.setHorizontalSpacing(8)
    preset_grid.setVerticalSpacing(8)
    preset_grid.addWidget(_build_style_preset_card("TikTok", "HELLO", "WORLD", gui.subtitle_preset_tiktok_radio), 0, 0)
    preset_grid.addWidget(_build_style_preset_card("YouTube", "Hello", "world", gui.subtitle_preset_youtube_radio), 0, 1)
    preset_grid.addWidget(_build_style_preset_card("Short", "HELLO", "world", gui.subtitle_preset_minimal_radio), 0, 2)
    preset_grid.addWidget(_build_style_preset_card("Custom", "Aa Bb", "Your style", gui.subtitle_preset_custom_radio), 1, 0)
    subtitle_layout.addLayout(preset_grid)

    gui.save_subtitle_style_btn = QPushButton("+ Save Current Style")
    gui.save_subtitle_style_btn.clicked.connect(gui.save_current_subtitle_style_preset)
    gui.saved_subtitle_style_combo = QComboBox()
    gui.saved_subtitle_style_combo.addItem("My Presets")
    gui.saved_subtitle_style_combo.currentIndexChanged.connect(gui.load_selected_subtitle_style_preset)
    subtitle_layout.addWidget(gui.save_subtitle_style_btn)
    subtitle_layout.addWidget(gui.saved_subtitle_style_combo)

    highlight_divider = QFrame()
    highlight_divider.setFrameShape(QFrame.HLine)
    highlight_divider.setStyleSheet("color: #30425b;")
    subtitle_layout.addWidget(highlight_divider)

    highlight_title = QLabel("Highlight")
    highlight_title.setObjectName("sectionTitle")
    subtitle_layout.addWidget(highlight_title)

    gui.subtitle_keyword_highlight_cb = QCheckBox("Highlight keyword")
    gui.subtitle_keyword_highlight_cb.setChecked(False)
    gui.subtitle_highlight_color_combo = QComboBox()
    gui.subtitle_highlight_color_combo.addItems(["Yellow", "Cyan", "Green", "Pink"])
    gui.subtitle_highlight_mode_combo = QComboBox()
    gui.subtitle_highlight_mode_combo.addItems(["Auto", "Manual", "Auto + Manual"])
    subtitle_layout.addWidget(gui.subtitle_keyword_highlight_cb)

    highlight_color_row = QHBoxLayout()
    highlight_color_row.addWidget(QLabel("Color:"))
    highlight_color_row.addWidget(gui.subtitle_highlight_color_combo, 1)
    subtitle_layout.addLayout(highlight_color_row)

    highlight_mode_row = QHBoxLayout()
    highlight_mode_row.addWidget(QLabel("Mode:"))
    highlight_mode_row.addWidget(gui.subtitle_highlight_mode_combo, 1)
    subtitle_layout.addLayout(highlight_mode_row)

    custom_wrapper = QFrame()
    custom_wrapper.setObjectName("statusCard")
    custom_wrapper_layout = QVBoxLayout(custom_wrapper)
    custom_wrapper_layout.setContentsMargins(12, 12, 12, 12)
    custom_wrapper_layout.setSpacing(10)
    gui.custom_settings_toggle_btn = QToolButton()
    gui.custom_settings_toggle_btn.setText("▼ Custom Settings")
    gui.custom_settings_toggle_btn.setCheckable(True)
    gui.custom_settings_toggle_btn.setChecked(True)
    gui.custom_settings_toggle_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    gui.custom_settings_toggle_btn.setStyleSheet("QToolButton { text-align: left; font-weight: 700; color: #dbe5f3; border: none; padding: 0; }")
    custom_wrapper_layout.addWidget(gui.custom_settings_toggle_btn)

    gui.custom_settings_content = QWidget()
    custom_controls_layout = QGridLayout(gui.custom_settings_content)
    custom_controls_layout.setContentsMargins(0, 0, 0, 0)
    custom_controls_layout.setHorizontalSpacing(10)
    custom_controls_layout.setVerticalSpacing(8)

    gui.subtitle_font_combo = QComboBox()
    gui.subtitle_font_combo.setEditable(True)
    gui.subtitle_font_combo.addItems(["Montserrat", "Roboto", "Inter", "Poppins", "Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman"])
    gui.subtitle_font_combo.setCurrentText("Segoe UI")
    gui.subtitle_font_size_spin = QSpinBox()
    gui.subtitle_font_size_spin.setRange(12, 72)
    gui.subtitle_font_size_spin.setValue(60)
    gui.subtitle_color_btn = QPushButton("White")
    gui.subtitle_color_hex = "#FFFFFF"
    gui.subtitle_color_btn.clicked.connect(gui.choose_subtitle_color)
    gui.subtitle_align_combo = QComboBox()
    gui.subtitle_align_combo.addItems(["Bottom", "Bottom Left", "Bottom Right", "Center", "Top"])
    gui.subtitle_align_combo.setCurrentText("Bottom")
    gui.subtitle_background_cb = QCheckBox("Background")
    gui.subtitle_background_cb.setChecked(False)
    gui.subtitle_bold_cb = QCheckBox("Bold")
    gui.subtitle_bold_cb.setChecked(True)
    gui.subtitle_animation_combo = QComboBox()
    gui.subtitle_animation_combo.addItems(
        ["Static", "Pop In", "Slide Up", "Fade In", "Fade Out", "Pulse", "Background Appear", "Typewriter", "Word Highlight Karaoke"]
    )
    gui.subtitle_animation_combo.setCurrentText("Pop In")
    gui.subtitle_animation_combo.currentTextChanged.connect(lambda _value: gui.on_subtitle_animation_changed())
    gui.subtitle_animation_time_spin = QDoubleSpinBox()
    gui.subtitle_animation_time_spin.setRange(0.1, 2.5)
    gui.subtitle_animation_time_spin.setSingleStep(0.05)
    gui.subtitle_animation_time_spin.setDecimals(2)
    gui.subtitle_animation_time_spin.setValue(0.22)
    gui.subtitle_animation_time_spin.setSuffix(" s")
    gui.subtitle_animation_time_label = QLabel("Duration")
    gui.subtitle_karaoke_timing_label = QLabel("Text Timing")
    gui.subtitle_karaoke_timing_combo = QComboBox()
    gui.subtitle_karaoke_timing_combo.addItem("Vietnamese pacing", "vietnamese")
    gui.subtitle_karaoke_timing_combo.addItem("Source speech timing", "source")
    gui.subtitle_karaoke_timing_combo.setCurrentIndex(0)
    gui.subtitle_karaoke_timing_combo.currentTextChanged.connect(lambda _value: gui.update_subtitle_preview_style())

    custom_controls_layout.addWidget(QLabel("Font:"), 0, 0)
    custom_controls_layout.addWidget(gui.subtitle_font_combo, 0, 1)
    custom_controls_layout.addWidget(QLabel("Size:"), 1, 0)
    custom_controls_layout.addWidget(gui.subtitle_font_size_spin, 1, 1)
    custom_controls_layout.addWidget(QLabel("Color:"), 2, 0)
    custom_controls_layout.addWidget(gui.subtitle_color_btn, 2, 1)
    custom_controls_layout.addWidget(QLabel("Position:"), 3, 0)
    custom_controls_layout.addWidget(gui.subtitle_align_combo, 3, 1)
    custom_controls_layout.addWidget(QLabel("Animation:"), 4, 0)
    custom_controls_layout.addWidget(gui.subtitle_animation_combo, 4, 1)
    custom_controls_layout.addWidget(gui.subtitle_animation_time_label, 5, 0)
    custom_controls_layout.addWidget(gui.subtitle_animation_time_spin, 5, 1)
    custom_controls_layout.addWidget(gui.subtitle_karaoke_timing_label, 6, 0)
    custom_controls_layout.addWidget(gui.subtitle_karaoke_timing_combo, 6, 1)
    custom_controls_layout.addWidget(gui.subtitle_background_cb, 7, 0)
    custom_controls_layout.addWidget(gui.subtitle_bold_cb, 7, 1)

    custom_wrapper_layout.addWidget(gui.custom_settings_content)
    subtitle_layout.addWidget(custom_wrapper)

    gui.subtitle_preset_summary_label = QLabel()
    gui.subtitle_preset_summary_label.setObjectName("helperLabel")
    gui.subtitle_preset_summary_label.setWordWrap(True)
    subtitle_layout.addWidget(gui.subtitle_preset_summary_label)

    gui.subtitle_x_offset_spin = QSpinBox()
    gui.subtitle_x_offset_spin.setRange(-400, 400)
    gui.subtitle_x_offset_spin.setValue(0)
    gui.subtitle_x_offset_spin.hide()
    gui.subtitle_bottom_offset_spin = QSpinBox()
    gui.subtitle_bottom_offset_spin.setRange(0, 300)
    gui.subtitle_bottom_offset_spin.setValue(30)
    gui.subtitle_bottom_offset_spin.hide()

    def _toggle_custom_section(checked: bool):
        gui.custom_settings_toggle_btn.setText(("▼ " if checked else "▶ ") + "Custom Settings")
        gui.custom_settings_content.setVisible(checked)

    gui.custom_settings_toggle_btn.toggled.connect(_toggle_custom_section)
    control_layout.addWidget(subtitle_card)

    action_card, action_layout = _build_collapsible_section("Section 7: Action + Progress")
    action_layout.addWidget(gui.run_all_btn)
    action_layout.addWidget(QLabel("Progress:"))
    gui.progress_audio_label = QLabel("[ ] Audio analyzed")
    gui.progress_subtitle_label = QLabel("[ ] Subtitle created")
    gui.progress_translate_label = QLabel("[ ] Translating")
    gui.progress_voice_label = QLabel("[ ] Generating voice")
    action_layout.addWidget(gui.progress_audio_label)
    action_layout.addWidget(gui.progress_subtitle_label)
    action_layout.addWidget(gui.progress_translate_label)
    action_layout.addWidget(gui.progress_voice_label)
    control_layout.addWidget(action_card)

    _build_hidden_status_widgets(gui)

    left_layout.addWidget(control_group)


def build_workflow_group(left_layout):
    return None
