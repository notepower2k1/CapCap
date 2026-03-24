from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from widgets import TimelineWidget, VideoView


def build_preview_panel(gui):
    right_panel = QWidget()
    right_panel.setObjectName("rightPanel")
    right_layout = QVBoxLayout(right_panel)

    side_info_card = QFrame()
    side_info_card.setObjectName("sideInfoCard")
    side_info_layout = QVBoxLayout(side_info_card)
    side_info_layout.setContentsMargins(14, 12, 14, 12)
    side_info_layout.setSpacing(4)
    preview_title = QLabel("Live preview")
    preview_title.setObjectName("sectionTitle")
    gui.preview_context_label = QLabel("Choose a video to start previewing. Subtitle and voice status will appear here as you work.")
    gui.preview_context_label.setWordWrap(True)
    gui.preview_context_label.setObjectName("previewContextLabel")
    gui.frame_preview_status_label = QLabel("Exact frame preview updates here when available.")
    gui.frame_preview_status_label.setWordWrap(True)
    gui.frame_preview_status_label.setObjectName("helperLabel")
    gui.frame_preview_image_label = QLabel("No frame preview yet")
    gui.frame_preview_image_label.setAlignment(Qt.AlignCenter)
    gui.frame_preview_image_label.setMinimumHeight(170)
    gui.frame_preview_image_label.setStyleSheet(
        "background-color: #0b1220; border: 1px dashed #325173; border-radius: 10px; color: #7f93ad; padding: 12px;"
    )
    side_info_layout.addWidget(preview_title)
    side_info_layout.addWidget(gui.preview_context_label)
    side_info_layout.addWidget(gui.frame_preview_status_label)
    side_info_layout.addWidget(gui.frame_preview_image_label)

    gui.video_view = VideoView()
    gui.video_view.setMinimumHeight(400)
    gui.timeline = TimelineWidget()
    gui.timeline.seekRequested.connect(gui.set_position)
    gui.time_label = QLabel("00:00 / 00:00")
    gui.time_label.setStyleSheet("font-weight: bold; min-width: 100px; color: #6ee7d6;")

    controls_layout = QHBoxLayout()
    gui.play_btn = QPushButton("Play")
    gui.stop_btn = QPushButton("Reset")
    controls_layout.addWidget(gui.play_btn)
    controls_layout.addWidget(gui.stop_btn)
    controls_layout.addStretch()
    controls_layout.addWidget(gui.time_label)

    gui.progress_bar = QProgressBar()
    gui.progress_bar.setFixedHeight(8)
    gui.progress_bar.setTextVisible(False)

    right_layout.addWidget(side_info_card)
    right_layout.addWidget(gui.video_view, 5)
    right_layout.addWidget(gui.timeline)
    right_layout.addLayout(controls_layout)
    right_layout.addWidget(QLabel("Process Status:"))
    right_layout.addWidget(gui.progress_bar)
    return right_panel
