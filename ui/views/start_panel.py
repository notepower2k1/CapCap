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
    gui.workflow_hint_label.setObjectName("helperLabel")
    gui.workflow_hint_label.setWordWrap(True)
    gui.workflow_status_badge = QLabel("Waiting for video")
    gui.workflow_status_badge.setObjectName("statusPill")
    gui.next_step_label = QLabel()
    gui.next_step_label.setObjectName("statusHeadline")
    gui.next_step_label.setWordWrap(True)
    gui.readiness_label = QLabel()
    gui.readiness_label.setObjectName("helperLabel")
    gui.readiness_label.setWordWrap(True)


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
    title_label.setAlignment(Qt.AlignCenter)

    style_key = title.strip().lower()
    preview_frame = QFrame()
    preview_frame.setFixedHeight(88)
    preview_frame.setStyleSheet(
        "QFrame {"
        "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1d2940, stop:1 #0d1522);"
        "border:1px solid #2d425d; border-radius: 12px; }"
    )
    preview_layout = QVBoxLayout(preview_frame)
    preview_layout.setContentsMargins(10, 10, 10, 10)
    preview_layout.setSpacing(2)

    preview_top = QLabel(line_one)
    preview_bottom = QLabel(line_two)
    preview_top.setAlignment(Qt.AlignCenter)
    preview_bottom.setAlignment(Qt.AlignCenter)

    if style_key == "tiktok":
        preview_top.setStyleSheet("font-size: 18px; font-weight: 900; color: #ffffff;")
        preview_bottom.setStyleSheet("font-size: 16px; font-weight: 900; color: #ffd400;")
    elif style_key == "youtube":
        preview_top.setStyleSheet(
            "font-size: 14px; font-weight: 700; color: #ffffff; "
            "background-color: rgba(0, 0, 0, 255); padding: 4px 8px; border-radius: 7px;"
        )
        preview_bottom.setStyleSheet(
            "font-size: 14px; font-weight: 700; color: #ffffff; "
            "background-color: rgba(0, 0, 0, 255); padding: 4px 8px; border-radius: 7px;"
        )
    elif style_key == "short":
        preview_top.setStyleSheet("font-size: 15px; font-weight: 800; color: #ffffff; letter-spacing: 1px;")
        preview_bottom.setStyleSheet("font-size: 13px; color: #dbe5f3;")
    else:
        preview_top.setStyleSheet("font-size: 15px; font-weight: 800; color: #ffffff;")
        preview_bottom.setStyleSheet(
            "font-size: 13px; color: #ffffff; background-color: rgba(0, 0, 0, 120); "
            "padding: 3px 8px; border-radius: 7px;"
        )

    preview_layout.addStretch(1)
    preview_layout.addWidget(preview_top)
    preview_layout.addWidget(preview_bottom)
    preview_layout.addStretch(1)

    subtitle_hint = QLabel()
    subtitle_hint.setObjectName("helperLabel")
    subtitle_hint.setAlignment(Qt.AlignCenter)
    if style_key == "tiktok":
        subtitle_hint.setText("Big text, karaoke, keywords")
    elif style_key == "youtube":
        subtitle_hint.setText("Solid background box")
    elif style_key == "short":
        subtitle_hint.setText("Light and minimal")
    else:
        subtitle_hint.setText("Your own settings")

    layout.addWidget(title_label, 0, Qt.AlignCenter)
    layout.addWidget(preview_frame)
    layout.addWidget(subtitle_hint)
    return card


def build_start_group(gui, left_layout):
    _build_hidden_status_widgets(gui)

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

    control_group = QGroupBox("")
    control_layout = QVBoxLayout(control_group)
    control_layout.setSpacing(14)

    upload_card, upload_layout = _build_collapsible_section("1. Choose Video")
    gui.upload_video_btn = QPushButton("Choose Video")
    gui.upload_video_btn.setObjectName("mainActionBtn")
    gui.upload_video_btn.clicked.connect(gui.browse_video)
    gui.upload_video_btn.setMinimumHeight(46)
    gui.upload_hint_label = QLabel("Drag and drop a video here, or browse from your computer.")
    gui.upload_hint_label.setObjectName("helperLabel")
    gui.upload_hint_label.setAlignment(Qt.AlignCenter)
    gui.upload_status_label = QLabel("No video uploaded yet")
    gui.upload_status_label.setObjectName("statusBody")
    gui.upload_status_label.setWordWrap(True)
    upload_layout.addWidget(gui.upload_video_btn)
    upload_layout.addWidget(gui.upload_hint_label)
    upload_layout.addWidget(gui.upload_status_label)
    control_layout.addWidget(upload_card)

    output_card, output_layout = _build_collapsible_section("2. Choose Output")
    output_layout.addWidget(QLabel("Create:"))
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
    gui.output_subtitle_radio = QRadioButton("Subtitles only")
    gui.output_voice_radio = QRadioButton("Voice only")
    gui.output_both_radio = QRadioButton("Subtitles + voice")
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

    output_layout.addSpacing(6)
    output_layout.addWidget(QLabel("Video quality:"))
    gui.output_quality_combo = QComboBox()
    gui.output_quality_combo.addItem("Max (source)", "source")
    gui.output_quality_combo.addItem("720p", "720p")
    gui.output_quality_combo.addItem("1080p (Full HD)", "1080p")
    gui.output_quality_combo.addItem("1440p (2K)", "1440p")
    gui.output_quality_combo.addItem("2160p (4K)", "2160p")
    output_layout.addWidget(gui.output_quality_combo)
    control_layout.addWidget(output_card)

    language_card, language_layout = _build_collapsible_section("3. Language")
    gui.lang_whisper_combo = QComboBox()
    gui.lang_whisper_combo.addItem("Chinese", "zh")
    gui.lang_target_combo = QComboBox()
    gui.lang_target_combo.addItem("Vietnamese", "vi")
    gui.lang_target_combo.setCurrentIndex(0)
    source_row = QVBoxLayout()
    source_row.addWidget(QLabel("Original language"))
    source_row.addWidget(gui.lang_whisper_combo)
    target_row = QVBoxLayout()
    target_row.addWidget(QLabel("Translate to"))
    target_row.addWidget(gui.lang_target_combo)
    language_layout.addLayout(source_row)
    language_layout.addLayout(target_row)
    gui.translator_ai_cb = QCheckBox("Make subtitles easier to read with AI")
    gui.translator_ai_cb.setChecked(True)
    language_layout.addWidget(gui.translator_ai_cb)
    gui.translator_ai_hint_label = QLabel("Keeps the meaning, then lightly cleans up the subtitle so it reads better on screen.")
    gui.translator_ai_hint_label.setObjectName("helperLabel")
    gui.translator_ai_hint_label.setWordWrap(True)
    language_layout.addWidget(gui.translator_ai_hint_label)
 
    gui.translator_style_label = QLabel("Extra tone/style (optional):")
    gui.translator_style_label.setObjectName("helperLabel")
    gui.translator_style_edit = QLineEdit()
    gui.translator_style_edit.setPlaceholderText("e.g. natural, funny, more formal")
    language_layout.addWidget(gui.translator_style_label)
    language_layout.addWidget(gui.translator_style_edit)
 
    # Logic to show/hide style field based on AI checkbox
    def toggle_style_field(checked):
        gui.translator_ai_hint_label.setVisible(checked)
        gui.translator_style_label.setVisible(checked)
        gui.translator_style_edit.setVisible(checked)
 
    gui.translator_ai_cb.toggled.connect(toggle_style_field)
    toggle_style_field(gui.translator_ai_cb.isChecked())
 
    control_layout.addWidget(language_card)

    voice_card, voice_layout = _build_collapsible_section("4. Voice")
    gui.voice_section_card = voice_card
    gui.free_voice_combo = QComboBox()
    gui.voice_gender_combo = QComboBox()
    gui.voice_gender_combo.addItems(["Any", "Male", "Female"])
    gui.voice_gender_combo.currentTextChanged.connect(gui.on_voice_gender_changed)
    gui.voice_speed_spin = QComboBox()
    gui.voice_speed_spin.setEditable(True)
    gui.voice_speed_spin.addItems(["0.8x", "0.9x", "1.0x", "1.1x", "1.2x", "1.3x", "1.4x", "1.5x", "1.6x", "1.8x", "2.0x"])
    gui.voice_speed_spin.setCurrentText("1.0x")
    gui.voice_timing_sync_combo = QComboBox()
    gui.voice_timing_sync_combo.addItems(["Off", "Smart", "Force Fit"])
    gui.voice_timing_sync_combo.setCurrentText("Smart")
    gui.audio_handling_combo = QComboBox()
    gui.audio_handling_combo.addItem("Fast (recommended)", "fast")
    gui.audio_handling_combo.addItem("Cleaner voice (slower)", "clean")
    gui.audio_handling_combo.setCurrentIndex(0)
    gui.audio_handling_hint_label = QLabel("Fast keeps things quick. Cleaner voice removes more background noise before voice generation.")
    gui.audio_handling_hint_label.setObjectName("helperLabel")
    gui.audio_handling_hint_label.setWordWrap(True)
    gui.free_voice_combo.currentIndexChanged.connect(gui.on_selected_voice_changed)
    gui.preview_voice_btn = QPushButton("Preview Selected Voice")
    gui.preview_voice_btn.clicked.connect(gui.preview_selected_voice_sample)
    gui.voice_preview_meta_label = QLabel("Listen to a short sample before generating the full voice track.")
    gui.voice_preview_meta_label.setObjectName("helperLabel")
    gui.voice_preview_meta_label.setWordWrap(True)
    voice_layout.addWidget(gui.free_voice_combo)
    voice_layout.addWidget(QLabel("Voice type"))
    voice_layout.addWidget(gui.voice_gender_combo)
    voice_layout.addWidget(QLabel("Voice speed"))
    voice_layout.addWidget(gui.voice_speed_spin)
    voice_layout.addWidget(QLabel("Audio cleanup"))
    voice_layout.addWidget(gui.audio_handling_combo)
    voice_layout.addWidget(gui.audio_handling_hint_label)
    voice_layout.addWidget(gui.preview_voice_btn)
    voice_layout.addWidget(gui.voice_preview_meta_label)
    control_layout.addWidget(voice_card)

    subtitle_card, subtitle_layout = _build_collapsible_section("5. Subtitle Look")
    base_style_label = QLabel("Choose a style")
    base_style_label.setObjectName("sectionTitle")
    subtitle_layout.addWidget(base_style_label)
    subtitle_style_hint = QLabel("Start with a ready-made look, then fine-tune only if you need to.")
    subtitle_style_hint.setObjectName("helperLabel")
    subtitle_style_hint.setWordWrap(True)
    subtitle_layout.addWidget(subtitle_style_hint)

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
    preset_grid.addWidget(_build_style_preset_card("TikTok", "TRENDING", "WORDS POP", gui.subtitle_preset_tiktok_radio), 0, 0)
    preset_grid.addWidget(_build_style_preset_card("YouTube", "Clean subtitle", "with solid box", gui.subtitle_preset_youtube_radio), 0, 1)
    preset_grid.addWidget(_build_style_preset_card("Short", "HELLO", "world", gui.subtitle_preset_minimal_radio), 0, 2)
    preset_grid.addWidget(_build_style_preset_card("Custom", "Aa Bb", "Your style", gui.subtitle_preset_custom_radio), 1, 0)
    subtitle_layout.addLayout(preset_grid)

    gui.save_subtitle_style_btn = QPushButton("+ Save This Style")
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

    highlight_card, highlight_card_layout = _build_collapsible_section("Keyword Highlight", start_expanded=False)
    highlight_hint = QLabel("Use this when you want important words to stand out on screen.")
    highlight_hint.setObjectName("helperLabel")
    highlight_hint.setWordWrap(True)
    highlight_card_layout.addWidget(highlight_hint)

    gui.subtitle_keyword_highlight_cb = QCheckBox("Highlight key words")
    gui.subtitle_keyword_highlight_cb.setChecked(False)
    gui.subtitle_highlight_color_combo = QComboBox()
    gui.subtitle_highlight_color_combo.addItems(["Yellow", "Cyan", "Green", "Pink"])
    gui.subtitle_highlight_mode_combo = QComboBox()
    gui.subtitle_highlight_mode_combo.addItems(["Auto", "Manual", "Auto + Manual"])
    highlight_card_layout.addWidget(gui.subtitle_keyword_highlight_cb)

    highlight_color_row = QHBoxLayout()
    highlight_color_row.addWidget(QLabel("Color:"))
    highlight_color_row.addWidget(gui.subtitle_highlight_color_combo, 1)
    highlight_card_layout.addLayout(highlight_color_row)

    highlight_mode_row = QHBoxLayout()
    highlight_mode_row.addWidget(QLabel("Source:"))
    highlight_mode_row.addWidget(gui.subtitle_highlight_mode_combo, 1)
    highlight_card_layout.addLayout(highlight_mode_row)
    subtitle_layout.addWidget(highlight_card)

    custom_divider = QFrame()
    custom_divider.setFrameShape(QFrame.HLine)
    custom_divider.setStyleSheet("color: #30425b;")
    subtitle_layout.addWidget(custom_divider)

    custom_title_card, custom_title_layout = _build_collapsible_section("Adjust Details", start_expanded=False)
    custom_hint = QLabel("Adjust font, placement, background, and animation when the ready-made styles are not enough.")
    custom_hint.setObjectName("helperLabel")
    custom_hint.setWordWrap(True)
    custom_title_layout.addWidget(custom_hint)

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
    gui.subtitle_background_color_btn = QPushButton("#000000")
    gui.subtitle_background_color_hex = "#000000"
    gui.subtitle_background_color_btn.clicked.connect(gui.choose_subtitle_background_color)
    gui.subtitle_position_mode_combo = QComboBox()
    gui.subtitle_position_mode_combo.addItem("Quick placement", "anchor")
    gui.subtitle_position_mode_combo.addItem("Custom X/Y", "custom")
    gui.subtitle_align_label = QLabel("Placement:")
    gui.subtitle_align_combo = QComboBox()
    gui.subtitle_align_combo.addItems(["Bottom", "Bottom Left", "Bottom Right", "Center", "Top"])
    gui.subtitle_align_combo.setCurrentText("Bottom")
    gui.subtitle_custom_x_label = QLabel("Custom X:")
    gui.subtitle_custom_x_spin = QSpinBox()
    gui.subtitle_custom_x_spin.setRange(0, 100)
    gui.subtitle_custom_x_spin.setValue(50)
    gui.subtitle_custom_x_spin.setSuffix(" %")
    gui.subtitle_custom_y_label = QLabel("Custom Y:")
    gui.subtitle_custom_y_spin = QSpinBox()
    gui.subtitle_custom_y_spin.setRange(0, 100)
    gui.subtitle_custom_y_spin.setValue(86)
    gui.subtitle_custom_y_spin.setSuffix(" %")
    gui.subtitle_background_cb = QCheckBox("Background Box")
    gui.subtitle_background_cb.setChecked(False)
    gui.subtitle_outline_cb = QCheckBox("Text Outline")
    gui.subtitle_outline_cb.setChecked(True)
    gui.subtitle_bold_cb = QCheckBox("Bold")
    gui.subtitle_bold_cb.setChecked(True)
    gui.subtitle_single_line_cb = QCheckBox("Single-line subtitle (Netflix)")
    gui.subtitle_single_line_cb.setChecked(False)
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
    gui.subtitle_bg_alpha_spin = QDoubleSpinBox()
    gui.subtitle_bg_alpha_spin.setRange(0.0, 1.0)
    gui.subtitle_bg_alpha_spin.setSingleStep(0.05)
    gui.subtitle_bg_alpha_spin.setDecimals(2)
    gui.subtitle_bg_alpha_spin.setValue(0.6)
    gui.subtitle_bg_alpha_spin.setSuffix(" alpha")
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
    custom_controls_layout.addWidget(QLabel("Text Color:"), 2, 0)
    custom_controls_layout.addWidget(gui.subtitle_color_btn, 2, 1)
    custom_controls_layout.addWidget(QLabel("Background color:"), 3, 0)
    custom_controls_layout.addWidget(gui.subtitle_background_color_btn, 3, 1)
    custom_controls_layout.addWidget(QLabel("Placement mode:"), 4, 0)
    custom_controls_layout.addWidget(gui.subtitle_position_mode_combo, 4, 1)
    custom_controls_layout.addWidget(gui.subtitle_align_label, 5, 0)
    custom_controls_layout.addWidget(gui.subtitle_align_combo, 5, 1)
    custom_controls_layout.addWidget(gui.subtitle_custom_x_label, 6, 0)
    custom_controls_layout.addWidget(gui.subtitle_custom_x_spin, 6, 1)
    custom_controls_layout.addWidget(gui.subtitle_custom_y_label, 7, 0)
    custom_controls_layout.addWidget(gui.subtitle_custom_y_spin, 7, 1)
    custom_controls_layout.addWidget(QLabel("Animation:"), 8, 0)
    custom_controls_layout.addWidget(gui.subtitle_animation_combo, 8, 1)
    custom_controls_layout.addWidget(gui.subtitle_animation_time_label, 9, 0)
    custom_controls_layout.addWidget(gui.subtitle_animation_time_spin, 9, 1)
    custom_controls_layout.addWidget(QLabel("Background opacity:"), 10, 0)
    custom_controls_layout.addWidget(gui.subtitle_bg_alpha_spin, 10, 1)
    custom_controls_layout.addWidget(gui.subtitle_karaoke_timing_label, 11, 0)
    custom_controls_layout.addWidget(gui.subtitle_karaoke_timing_combo, 11, 1)
    custom_controls_layout.addWidget(gui.subtitle_background_cb, 12, 0)
    custom_controls_layout.addWidget(gui.subtitle_outline_cb, 12, 1)
    custom_controls_layout.addWidget(gui.subtitle_bold_cb, 13, 0)
    custom_controls_layout.addWidget(gui.subtitle_single_line_cb, 13, 1)
    gui.subtitle_single_line_hint_label = QLabel(
        "Shows one subtitle line at a time by splitting long subtitles into shorter cues."
    )
    gui.subtitle_single_line_hint_label.setObjectName("helperLabel")
    gui.subtitle_single_line_hint_label.setWordWrap(True)
    custom_controls_layout.addWidget(gui.subtitle_single_line_hint_label, 14, 0, 1, 2)

    custom_wrapper_layout.addWidget(gui.custom_settings_content)
    custom_title_layout.addWidget(custom_wrapper)
    subtitle_layout.addWidget(custom_title_card)

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

    action_card, action_layout = _build_collapsible_section("6. Generate")
    action_layout.addWidget(gui.run_all_btn)
    gui.generate_hint_label = QLabel(
        "Generate updates subtitles, voice, and preview using your latest settings."
    )
    gui.generate_hint_label.setObjectName("helperLabel")
    gui.generate_hint_label.setWordWrap(True)
    action_layout.addWidget(gui.generate_hint_label)
    guidance_card = QFrame()
    guidance_card.setObjectName("statusCard")
    guidance_layout = QVBoxLayout(guidance_card)
    guidance_layout.setContentsMargins(12, 12, 12, 12)
    guidance_layout.setSpacing(6)
    guidance_layout.addWidget(gui.workflow_status_badge, 0, Qt.AlignLeft)
    guidance_layout.addWidget(gui.next_step_label)
    guidance_layout.addWidget(gui.readiness_label)
    guidance_layout.addWidget(gui.workflow_hint_label)
    action_layout.addWidget(guidance_card)
    action_layout.addWidget(QLabel("Progress:"))
    gui.progress_audio_label = QLabel("[ ] Audio ready")
    gui.progress_subtitle_label = QLabel("[ ] Original subtitles ready")
    gui.progress_translate_label = QLabel("[ ] Vietnamese subtitles ready")
    gui.progress_voice_label = QLabel("[ ] Voice/audio ready")
    action_layout.addWidget(gui.progress_audio_label)
    action_layout.addWidget(gui.progress_subtitle_label)
    action_layout.addWidget(gui.progress_translate_label)
    action_layout.addWidget(gui.progress_voice_label)
    control_layout.addWidget(action_card)

    left_layout.addWidget(control_group)


def build_workflow_group(left_layout):
    return None







