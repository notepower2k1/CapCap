import os

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .advanced_tabs import build_advanced_group
from .preview_panel import build_preview_panel
from .start_panel import build_start_group


def build_main_window_ui(gui):
    central_widget = QWidget()
    central_widget.setObjectName("centralWidget")
    gui.setCentralWidget(central_widget)
    root_layout = QVBoxLayout(central_widget)
    root_layout.setContentsMargins(15, 15, 15, 15)
    root_layout.setSpacing(15)

    scroll_area = _build_left_panel(gui)
    root_layout.addWidget(_build_header_bar(gui))

    content_layout = QHBoxLayout()
    content_layout.setSpacing(15)
    right_panel = build_preview_panel(gui)

    content_layout.addWidget(scroll_area)
    content_layout.addWidget(right_panel, 1)
    root_layout.addLayout(content_layout, 1)

    _connect_ui_signals(gui)
    _initialize_ui_state(gui)
    QTimer.singleShot(0, gui.sync_left_panel_container_width)


def _build_header_bar(gui):
    header = QFrame()
    header.setObjectName("statusCard")
    layout = QHBoxLayout(header)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(12)

    logo_label = QLabel()
    logo_label.setFixedSize(34, 34)
    logo_label.setAlignment(Qt.AlignCenter)
    if os.path.exists(getattr(gui, "logo_path", "")):
        logo_pixmap = QPixmap(gui.logo_path)
        if not logo_pixmap.isNull():
            white_logo = _tint_pixmap(logo_pixmap, QColor("#FFFFFF"))
            logo_label.setPixmap(white_logo.scaled(30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    layout.addWidget(logo_label)

    brand_label = QLabel("CapCap")
    brand_label.setObjectName("heroTitle")
    brand_label.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 800;")
    layout.addWidget(brand_label)

    gui.project_title_label = QLabel("Project: No video selected")
    gui.project_title_label.setObjectName("statusHeadline")
    layout.addWidget(gui.project_title_label, 1)
    gui.run_all_btn.setMinimumHeight(42)
    layout.addWidget(gui.run_all_btn)
    gui.export_btn.setObjectName("secondaryActionBtn")
    gui.export_btn.setMinimumHeight(42)
    layout.addWidget(gui.export_btn)
    layout.addSpacing(8)

    gui.more_actions_btn = QPushButton("More")
    gui.more_actions_btn.setObjectName("secondaryActionBtn")
    gui.more_actions_btn.setMinimumHeight(42)
    gui.more_actions_btn.setMinimumWidth(180)
    more_menu = QMenu(gui.more_actions_btn)
    more_menu.setObjectName("headerMoreMenu")

    gui.download_subtitle_action = more_menu.addAction("Subtitle")
    gui.download_subtitle_action.triggered.connect(gui.download_subtitle)
    gui.download_original_action = more_menu.addAction("Original Script")
    gui.download_original_action.triggered.connect(gui.download_original_script)
    more_menu.addSeparator()
    gui.clean_project_action = more_menu.addAction("Clean")
    gui.clean_project_action.triggered.connect(gui.clean_current_project)
    gui.toggle_controls_action = more_menu.addAction("Hide Controls")
    gui.toggle_controls_action.triggered.connect(gui.toggle_controls_panel)
    gui.settings_action = more_menu.addAction("Settings")
    gui.settings_action.triggered.connect(gui.open_model_settings_dialog)

    gui.more_actions_btn.setMenu(more_menu)
    layout.addWidget(gui.more_actions_btn)
    return header


def _tint_pixmap(pixmap: QPixmap, color: QColor) -> QPixmap:
    image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    tinted = QImage(image.size(), QImage.Format_ARGB32)
    tinted.fill(Qt.transparent)

    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            alpha = pixel.alpha()
            if alpha <= 0:
                continue
            pixel.setRed(color.red())
            pixel.setGreen(color.green())
            pixel.setBlue(color.blue())
            pixel.setAlpha(alpha)
            tinted.setPixelColor(x, y, pixel)
    return QPixmap.fromImage(tinted)


def _build_left_panel(gui):
    scroll_area = QScrollArea()
    gui.left_panel_scroll_area = scroll_area
    scroll_area.setObjectName("leftPanelArea")
    scroll_area.setWidgetResizable(True)
    scroll_area.setFixedWidth(420)
    scroll_area.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll_area.setFrameShape(QFrame.NoFrame)

    left_panel_container = QWidget()
    left_panel_container.setObjectName("leftPanelContainer")
    gui.left_panel_container = left_panel_container
    left_layout = QVBoxLayout(left_panel_container)
    left_layout.setContentsMargins(10, 0, 10, 0)
    left_layout.setSpacing(12)
    scroll_area.setWidget(left_panel_container)
    scroll_area.installEventFilter(gui)
    scroll_area.viewport().installEventFilter(gui)
    scroll_area.verticalScrollBar().installEventFilter(gui)

    build_start_group(gui, left_layout)
    build_advanced_group(gui, left_layout)
    return scroll_area


def _connect_ui_signals(gui):
    gui.extract_btn.clicked.connect(gui.run_extraction)
    gui.vocal_sep_btn.clicked.connect(gui.run_vocal_separation)
    gui.transcribe_btn.clicked.connect(gui.run_transcription)
    gui.translate_btn.clicked.connect(gui.run_translation)
    gui.rewrite_translation_btn.clicked.connect(gui.run_rewrite_translation)
    gui.import_translation_btn.clicked.connect(gui.import_translated_srt)
    gui.voiceover_btn.clicked.connect(gui.run_voiceover)
    gui.preview_btn.clicked.connect(gui.preview_video)
    if hasattr(gui, "reset_framing_btn"):
        gui.reset_framing_btn.clicked.connect(gui.reset_preview_framing)
    if hasattr(gui, "left_panel_stack"):
        gui.left_panel_stack.currentChanged.connect(gui.on_left_panel_workflow_changed)
    gui.output_mode_combo.currentTextChanged.connect(gui.on_output_mode_changed)
    if hasattr(gui, "output_quality_combo"):
        gui.output_quality_combo.currentIndexChanged.connect(gui.refresh_ui_state)
    gui.audio_handling_combo.currentTextChanged.connect(gui.refresh_ui_state)
    if hasattr(gui, "preview_volume_down_btn"):
        gui.preview_volume_down_btn.clicked.connect(gui.preview_volume_down)
    if hasattr(gui, "preview_volume_up_btn"):
        gui.preview_volume_up_btn.clicked.connect(gui.preview_volume_up)
    if hasattr(gui, "preview_mute_btn"):
        gui.preview_mute_btn.clicked.connect(gui.toggle_preview_mute)
    if hasattr(gui, "preview_speed_combo"):
        gui.preview_speed_combo.currentIndexChanged.connect(gui.on_preview_speed_changed)
    gui.final_output_folder_edit.textChanged.connect(gui.voice_output_folder_edit.setText)
    gui.final_output_folder_edit.textChanged.connect(gui.srt_output_folder_edit.setText)
    gui.video_path_edit.textChanged.connect(gui.refresh_ui_state)
    gui.video_path_edit.textChanged.connect(gui.update_project_header)
    gui.audio_source_edit.textChanged.connect(gui.refresh_ui_state)
    gui.bg_music_edit.textChanged.connect(gui.refresh_ui_state)
    gui.mixed_audio_edit.textChanged.connect(gui.refresh_ui_state)
    gui.use_generated_audio_radio.toggled.connect(gui.on_audio_source_mode_changed)
    gui.use_existing_audio_radio.toggled.connect(gui.on_audio_source_mode_changed)
    gui.blur_area_btn.toggled.connect(gui.toggle_blur_area_editing)
    if hasattr(gui, "video_view") and hasattr(gui.video_view, "framingChanged"):
        gui.video_view.framingChanged.connect(gui.on_preview_framing_changed)
    gui.transcript_text.textChanged.connect(gui.refresh_ui_state)
    gui.transcript_text.textChanged.connect(gui.schedule_live_subtitle_preview_refresh)
    gui.transcript_text.textChanged.connect(gui.sync_segment_editor_from_hidden_text)
    gui.translated_text.textChanged.connect(gui.refresh_ui_state)
    gui.translated_text.textChanged.connect(gui.schedule_live_subtitle_preview_refresh)
    gui.translated_text.textChanged.connect(gui.sync_segment_editor_from_hidden_text)
    gui.show_original_subtitle_cb.toggled.connect(gui.toggle_original_subtitle_visibility)
    gui.subtitle_font_combo.currentTextChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_font_size_spin.valueChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_animation_combo.currentTextChanged.connect(gui.on_subtitle_animation_changed)
    gui.subtitle_bold_cb.toggled.connect(gui.update_subtitle_preview_style)
    gui.subtitle_preset_tiktok_radio.toggled.connect(gui.on_subtitle_preset_changed)
    gui.subtitle_preset_youtube_radio.toggled.connect(gui.on_subtitle_preset_changed)
    gui.subtitle_preset_minimal_radio.toggled.connect(gui.on_subtitle_preset_changed)
    gui.subtitle_preset_custom_radio.toggled.connect(gui.on_subtitle_preset_changed)
    gui.subtitle_keyword_highlight_cb.toggled.connect(gui.update_subtitle_preview_style)
    gui.subtitle_highlight_color_combo.currentTextChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_highlight_mode_combo.currentTextChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_animation_time_spin.valueChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_position_mode_combo.currentTextChanged.connect(gui.on_subtitle_position_mode_changed)
    gui.subtitle_align_combo.currentTextChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_custom_x_spin.valueChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_custom_y_spin.valueChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_x_offset_spin.valueChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_bottom_offset_spin.valueChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_background_cb.toggled.connect(gui.update_subtitle_preview_style)
    if hasattr(gui, "subtitle_outline_cb"):
        gui.subtitle_outline_cb.toggled.connect(gui.update_subtitle_preview_style)
    if hasattr(gui, "subtitle_bg_alpha_spin"):
        gui.subtitle_bg_alpha_spin.valueChanged.connect(gui.update_subtitle_preview_style)

    gui.on_advanced_toggled(bool(getattr(gui, "toggle_advanced_btn", None) and gui.toggle_advanced_btn.isChecked()))


def _initialize_ui_state(gui):
    QTimer.singleShot(100, gui.video_view.reposition_subtitle)

    gui.current_segments = []
    gui.current_translated_segments = []
    gui._frame_preview_running = False
    gui._pending_auto_frame_preview = False
    gui._show_dialog_on_frame_preview = False
    gui.auto_frame_preview_timer = QTimer(gui)
    gui.auto_frame_preview_timer.setSingleShot(True)
    gui.auto_frame_preview_timer.setInterval(700)
    gui.auto_frame_preview_timer.timeout.connect(gui.trigger_auto_frame_preview)
    gui.seek_frame_preview_timer = QTimer(gui)
    gui.seek_frame_preview_timer.setSingleShot(True)
    gui.seek_frame_preview_timer.setInterval(300)
    gui.seek_frame_preview_timer.timeout.connect(gui.trigger_seek_frame_preview)
    gui.live_subtitle_preview_timer = QTimer(gui)
    gui.live_subtitle_preview_timer.setSingleShot(True)
    gui.live_subtitle_preview_timer.setInterval(250)
    gui.live_subtitle_preview_timer.timeout.connect(gui.refresh_live_subtitle_preview)
    gui.video_filter_preview_timer = QTimer(gui)
    gui.video_filter_preview_timer.setSingleShot(True)
    gui.video_filter_preview_timer.setInterval(350)
    gui.video_filter_preview_timer.timeout.connect(gui.run_live_video_filter_preview)
    gui.last_extracted_audio = ""
    gui.last_vocals_path = ""
    gui.last_music_path = ""
    gui.last_original_srt_path = ""
    gui.last_translated_srt_path = ""
    gui.last_voice_vi_path = ""
    gui.last_mixed_vi_path = ""
    gui.last_preview_video_path = ""
    gui.last_styled_preview_path = ""
    gui.last_styled_preview_signature = ""
    gui.last_exported_video_path = ""
    gui.last_exact_preview_5s_path = ""
    gui.last_exact_preview_frame_path = ""
    gui._preview_video_has_burned_subtitles = False
    gui.live_preview_subtitle_path = ""
    gui.live_preview_ass_path = ""
    gui.live_preview_segments = []
    gui.live_preview_editor_name = ""
    gui._live_preview_signature = None
    gui._styled_preview_running = False
    gui._suspend_live_subtitle_sync = False
    gui._syncing_segment_editor = False
    gui._syncing_hidden_editor_text = False
    gui._segment_editor_rows = []
    gui._selected_segment_index = -1
    gui.subtitle_export_font_scale = 1.3
    gui.use_exact_subtitle_preview = True

    gui.update_subtitle_preview_style()
    gui.on_subtitle_preset_changed()
    gui.on_output_mode_changed(gui.output_mode_combo.currentText())
    gui.update_project_header()
    gui.refresh_ui_state()
    gui.sync_segment_editor_rows()

