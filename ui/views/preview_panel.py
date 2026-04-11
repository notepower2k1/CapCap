import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from runtime_paths import asset_path
from widgets import MpvVideoView, TimelineWidget, VideoView
from utils.icon_utils import load_icon
from utils.media_backend import is_mpv_backend_available


def _set_preview_icon_button(button: QPushButton, icon_path: str, tooltip: str):
    button.setText("")
    button.setToolTip(tooltip)
    button.setFixedSize(38, 38)
    button.setIcon(load_icon(icon_path, 18))
    button.setIconSize(QSize(18, 18))
    button.setStyleSheet("QPushButton { padding: 0; }")


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

    gui.video_view = MpvVideoView() if is_mpv_backend_available() else VideoView()
    gui.video_view.setMinimumHeight(380)
    gui.timeline = TimelineWidget()
    gui.timeline.seekRequested.connect(gui.set_position)
    gui.time_label = QLabel("00:00 / 00:00")
    gui.time_label.setStyleSheet("font-weight: bold; min-width: 100px; color: #6ee7d6;")

    controls_layout = QHBoxLayout()
    icons_dir = asset_path("icons")
    gui.play_btn = QPushButton()
    gui.stop_btn = QPushButton()
    gui.preview_btn = QPushButton()
    gui.blur_area_btn = QPushButton()
    gui.blur_area_btn.setCheckable(True)
    _set_preview_icon_button(gui.play_btn, os.path.join(icons_dir, "play.svg"), "Play or pause preview")
    _set_preview_icon_button(gui.stop_btn, os.path.join(icons_dir, "reset.svg"), "Reset preview to the beginning")
    _set_preview_icon_button(gui.preview_btn, os.path.join(icons_dir, "preview.svg"), "Render a fresh preview using current subtitle and audio")
    _set_preview_icon_button(gui.blur_area_btn, os.path.join(icons_dir, "blur.svg"), "Toggle blur area editing")
    controls_layout.addWidget(gui.play_btn)
    controls_layout.addWidget(gui.stop_btn)
    controls_layout.addWidget(gui.preview_btn)
    controls_layout.addWidget(gui.blur_area_btn)
    controls_layout.addStretch()
    controls_layout.addWidget(gui.time_label)

    preview_audio_layout = QHBoxLayout()
    gui.preview_volume_down_btn = QPushButton()
    gui.preview_mute_btn = QPushButton()
    gui.preview_volume_up_btn = QPushButton()
    _set_preview_icon_button(gui.preview_volume_down_btn, os.path.join(icons_dir, "volume_down.svg"), "Lower preview volume")
    _set_preview_icon_button(gui.preview_mute_btn, os.path.join(icons_dir, "volume_mute.svg"), "Mute preview")
    _set_preview_icon_button(gui.preview_volume_up_btn, os.path.join(icons_dir, "volume_up.svg"), "Raise preview volume")
    gui.preview_volume_label = QLabel("100%")
    gui.preview_volume_label.setObjectName("helperLabel")
    gui.preview_speed_combo = QComboBox()
    gui.preview_speed_combo.addItem("0.75x", 0.75)
    gui.preview_speed_combo.addItem("1.0x", 1.0)
    gui.preview_speed_combo.addItem("1.25x", 1.25)
    gui.preview_speed_combo.addItem("1.5x", 1.5)
    gui.preview_speed_combo.addItem("2.0x", 2.0)
    gui.preview_speed_combo.setCurrentIndex(1)
    preview_audio_layout.addWidget(gui.preview_volume_down_btn)
    preview_audio_layout.addWidget(gui.preview_mute_btn)
    preview_audio_layout.addWidget(gui.preview_volume_up_btn)
    preview_audio_layout.addWidget(gui.preview_volume_label)
    preview_audio_layout.addStretch()
    preview_audio_layout.addWidget(QLabel("Speed"))
    preview_audio_layout.addWidget(gui.preview_speed_combo)

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
    gui.rewrite_translation_btn = QPushButton("Rewrite")
    gui.import_translation_btn = QPushButton("Import SRT")
    editor_top.addWidget(editor_title)
    editor_top.addStretch()
    editor_top.addWidget(gui.rewrite_translation_btn)
    editor_top.addWidget(gui.import_translation_btn)
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
    right_layout.addLayout(preview_audio_layout)
    right_layout.addWidget(gui.progress_bar)
    right_layout.addWidget(editor_card, 4)
    return right_panel


