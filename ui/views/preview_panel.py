from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from widgets import MpvVideoView, TimelineWidget


def build_preview_panel(gui):
    right_panel = QWidget()
    right_panel.setObjectName("rightPanel")
    right_layout = QVBoxLayout(right_panel)
    right_layout.setSpacing(12)

    gui.preview_context_label = QLabel("Choose a video to start previewing. Subtitle and voice status will appear here as you work.")
    gui.preview_context_label.setWordWrap(True)
    gui.preview_context_label.setObjectName("previewContextLabel")
    gui.preview_context_label.hide()
    gui.frame_preview_status_label = QLabel("Exact frame preview updates here when available.")
    gui.frame_preview_status_label.setWordWrap(True)
    gui.frame_preview_status_label.setObjectName("helperLabel")
    gui.frame_preview_status_label.hide()
    gui.frame_preview_image_label = QLabel("No frame preview yet")
    gui.frame_preview_image_label.setAlignment(Qt.AlignCenter)
    gui.frame_preview_image_label.setMinimumHeight(170)
    gui.frame_preview_image_label.hide()

    gui.video_view = MpvVideoView()
    gui.video_view.setMinimumHeight(380)
    gui.timeline = TimelineWidget()
    gui.timeline.seekRequested.connect(gui.set_position)
    gui.time_label = QLabel("00:00 / 00:00")
    gui.time_label.setStyleSheet("font-weight: bold; min-width: 100px; color: #6ee7d6;")

    controls_layout = QHBoxLayout()
    gui.play_btn = QPushButton("Play")
    gui.stop_btn = QPushButton("Reset")
    gui.blur_area_btn = QPushButton("Blur Area")
    gui.blur_area_btn.setCheckable(True)
    gui.preview_audio_btn = QPushButton("Preview audio")
    controls_layout.addWidget(gui.play_btn)
    controls_layout.addWidget(gui.stop_btn)
    controls_layout.addWidget(gui.blur_area_btn)
    controls_layout.addWidget(gui.preview_audio_btn)
    controls_layout.addStretch()
    controls_layout.addWidget(gui.time_label)

    gui.progress_bar = QProgressBar()
    gui.progress_bar.setFixedHeight(8)
    gui.progress_bar.setTextVisible(False)

    gui.translated_text = QTextEdit()
    gui.translated_text.setPlaceholderText("Vietnamese subtitle text will appear here. You can edit it before export.")
    gui.translated_text.hide()
    gui.transcript_text = QTextEdit()
    gui.transcript_text.setPlaceholderText("The original subtitle transcript will appear here...")
    gui.transcript_text.hide()

    editor_card = QFrame()
    editor_card.setObjectName("statusCard")
    editor_layout = QVBoxLayout(editor_card)
    editor_layout.setContentsMargins(14, 14, 14, 14)
    editor_layout.setSpacing(10)

    editor_top = QHBoxLayout()
    editor_title = QLabel("Subtitle Editor")
    editor_title.setObjectName("statusHeadline")
    gui.show_original_subtitle_cb = QCheckBox("Show original script")
    gui.show_original_subtitle_cb.setChecked(True)
    gui.rewrite_translation_btn = QPushButton("Rewrite with AI")
    editor_top.addWidget(editor_title)
    editor_top.addStretch()
    editor_top.addWidget(gui.rewrite_translation_btn)
    editor_top.addWidget(gui.keep_timeline_cb)
    editor_top.addWidget(gui.show_original_subtitle_cb)

    editor_hint = QLabel("Edit Vietnamese lines directly below. Original lines can be shown or hidden while you review timing.")
    editor_hint.setObjectName("helperLabel")
    editor_hint.setWordWrap(True)

    gui.segment_editor_scroll = QScrollArea()
    gui.segment_editor_scroll.setWidgetResizable(True)
    gui.segment_editor_scroll.setFrameShape(QFrame.NoFrame)
    gui.segment_editor_container = QWidget()
    gui.segment_editor_layout = QVBoxLayout(gui.segment_editor_container)
    gui.segment_editor_layout.setContentsMargins(0, 0, 0, 0)
    gui.segment_editor_layout.setSpacing(10)
    gui.segment_editor_scroll.setWidget(gui.segment_editor_container)

    editor_layout.addLayout(editor_top)
    editor_layout.addWidget(editor_hint)
    editor_layout.addWidget(gui.segment_editor_scroll, 1)

    right_layout.addWidget(gui.video_view, 5)
    right_layout.addWidget(gui.timeline)
    right_layout.addLayout(controls_layout)
    right_layout.addWidget(gui.progress_bar)
    right_layout.addWidget(editor_card, 4)
    return right_panel
