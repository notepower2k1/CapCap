from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

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


def _build_header_bar(gui):
    header = QFrame()
    header.setObjectName("statusCard")
    layout = QHBoxLayout(header)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(12)

    gui.project_title_label = QLabel("Project: No video selected")
    gui.project_title_label.setObjectName("statusHeadline")
    layout.addWidget(gui.project_title_label, 1)
    gui.toggle_controls_btn = QPushButton("Hide Controls")
    gui.toggle_controls_btn.clicked.connect(gui.toggle_controls_panel)
    layout.addWidget(gui.toggle_controls_btn)
    gui.download_original_btn = QPushButton("Download Original Script")
    gui.download_original_btn.clicked.connect(gui.download_original_script)
    gui.download_subtitle_btn = QPushButton("Download Subtitle")
    gui.download_subtitle_btn.clicked.connect(gui.download_subtitle)
    layout.addWidget(gui.download_original_btn)
    layout.addWidget(gui.download_subtitle_btn)
    layout.addWidget(gui.export_btn)
    return header


def _build_left_panel(gui):
    scroll_area = QScrollArea()
    gui.left_panel_scroll_area = scroll_area
    scroll_area.setObjectName("leftPanelArea")
    scroll_area.setWidgetResizable(True)
    scroll_area.setFixedWidth(420)
    scroll_area.setFrameShape(QFrame.NoFrame)

    left_panel_container = QWidget()
    left_panel_container.setObjectName("leftPanelContainer")
    left_layout = QVBoxLayout(left_panel_container)
    left_layout.setSpacing(15)
    scroll_area.setWidget(left_panel_container)

    build_start_group(gui, left_layout)
    build_advanced_group(gui, left_layout)
    return scroll_area


def _connect_ui_signals(gui):
    gui.extract_btn.clicked.connect(gui.run_extraction)
    gui.vocal_sep_btn.clicked.connect(gui.run_vocal_separation)
    gui.transcribe_btn.clicked.connect(gui.run_transcription)
    gui.translate_btn.clicked.connect(gui.run_translation)
    gui.voiceover_btn.clicked.connect(gui.run_voiceover)
    gui.output_mode_combo.currentTextChanged.connect(gui.on_output_mode_changed)
    gui.enable_ai_polish_cb.toggled.connect(lambda _: gui.on_output_mode_changed(gui.output_mode_combo.currentText()))
    gui.final_output_folder_edit.textChanged.connect(gui.voice_output_folder_edit.setText)
    gui.final_output_folder_edit.textChanged.connect(gui.srt_output_folder_edit.setText)
    gui.video_path_edit.textChanged.connect(gui.refresh_ui_state)
    gui.video_path_edit.textChanged.connect(gui.update_project_header)
    gui.audio_source_edit.textChanged.connect(gui.refresh_ui_state)
    gui.bg_music_edit.textChanged.connect(gui.refresh_ui_state)
    gui.mixed_audio_edit.textChanged.connect(gui.refresh_ui_state)
    gui.use_generated_audio_radio.toggled.connect(gui.on_audio_source_mode_changed)
    gui.use_existing_audio_radio.toggled.connect(gui.on_audio_source_mode_changed)
    gui.advanced_group.toggled.connect(gui.on_advanced_toggled)
    gui.preview_audio_btn.clicked.connect(gui.preview_current_audio_track)
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
    gui.subtitle_align_combo.currentTextChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_x_offset_spin.valueChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_bottom_offset_spin.valueChanged.connect(gui.update_subtitle_preview_style)
    gui.subtitle_background_cb.toggled.connect(gui.update_subtitle_preview_style)
    gui.on_advanced_toggled(gui.advanced_group.isChecked())


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
    gui.last_extracted_audio = ""
    gui.last_vocals_path = ""
    gui.last_music_path = ""
    gui.last_original_srt_path = ""
    gui.last_translated_srt_path = ""
    gui.last_voice_vi_path = ""
    gui.last_mixed_vi_path = ""
    gui.last_preview_video_path = ""
    gui.last_exported_video_path = ""
    gui.last_exact_preview_5s_path = ""
    gui.last_exact_preview_frame_path = ""
    gui.live_preview_subtitle_path = ""
    gui.live_preview_ass_path = ""
    gui.live_preview_segments = []
    gui.live_preview_editor_name = ""
    gui._syncing_segment_editor = False
    gui._syncing_hidden_editor_text = False
    gui._segment_editor_rows = []
    gui.subtitle_export_font_scale = 1.3
    gui.use_exact_subtitle_preview = True

    gui.update_subtitle_preview_style()
    gui.on_subtitle_preset_changed()
    gui.on_output_mode_changed(gui.output_mode_combo.currentText())
    gui.update_project_header()
    gui.refresh_ui_state()
    gui.sync_segment_editor_rows()
