import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout


def build_start_group(gui, left_layout):
    gui.video_path_edit = QLineEdit()
    gui.video_path_edit.setPlaceholderText("Choose one Chinese video to process...")
    browse_btn = QPushButton("Browse")
    browse_btn.clicked.connect(gui.browse_video)

    file_layout = QHBoxLayout()
    file_layout.addWidget(gui.video_path_edit)
    file_layout.addWidget(browse_btn)

    start_group = QGroupBox("START HERE")
    start_layout = QVBoxLayout(start_group)
    hero_card = QFrame()
    hero_card.setObjectName("heroCard")
    hero_layout = QVBoxLayout(hero_card)
    hero_layout.setContentsMargins(14, 14, 14, 14)
    hero_layout.setSpacing(6)
    hero_title = QLabel("Make CapCap easier for first-time users")
    hero_title.setObjectName("heroTitle")
    hero_body = QLabel(
        "Pick one video, choose the output you want, then let the guided pipeline handle the heavy lifting. "
        "The detailed tabs below are there when you want to review or fine-tune."
    )
    hero_body.setWordWrap(True)
    hero_body.setObjectName("heroBody")
    hero_layout.addWidget(hero_title)
    hero_layout.addWidget(hero_body)
    start_layout.addWidget(hero_card)
    start_layout.addWidget(QLabel("1. Target Video"))
    start_layout.addLayout(file_layout)

    gui.output_mode_combo = QComboBox()
    gui.output_mode_combo.addItems(
        [
            "Vietnamese subtitles only",
            "Vietnamese voice only",
            "Vietnamese subtitles + voice",
        ]
    )
    gui.output_mode_combo.setCurrentText("Vietnamese subtitles + voice")

    gui.lang_whisper_combo = QComboBox()
    gui.lang_whisper_combo.addItems(["zh", "auto", "ko", "ja", "en", "vi"])

    gui.enable_ai_polish_cb = QCheckBox("Use AI polish after Microsoft translation")
    gui.enable_ai_polish_cb.setChecked(True)

    gui.final_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
    gui.final_output_folder_edit.setPlaceholderText("Folder to save final results...")
    browse_final_out_btn = QPushButton("Save To")
    browse_final_out_btn.clicked.connect(gui.browse_voice_output_folder)
    final_out_layout = QHBoxLayout()
    final_out_layout.addWidget(gui.final_output_folder_edit)
    final_out_layout.addWidget(browse_final_out_btn)

    gui.workflow_hint_label = QLabel()
    gui.workflow_hint_label.setWordWrap(True)
    gui.workflow_hint_label.setObjectName("statusBody")
    gui.workflow_status_badge = QLabel("Waiting for video")
    gui.workflow_status_badge.setObjectName("statusPill")
    gui.next_step_label = QLabel()
    gui.next_step_label.setWordWrap(True)
    gui.next_step_label.setObjectName("statusHeadline")
    gui.readiness_label = QLabel()
    gui.readiness_label.setWordWrap(True)
    gui.readiness_label.setObjectName("statusBody")

    status_card = QFrame()
    status_card.setObjectName("statusCard")
    status_layout = QVBoxLayout(status_card)
    status_layout.setContentsMargins(14, 14, 14, 14)
    status_layout.setSpacing(8)
    status_title = QLabel("Guided status")
    status_title.setObjectName("sectionTitle")
    status_layout.addWidget(status_title)
    status_layout.addWidget(gui.workflow_status_badge, 0, Qt.AlignLeft)
    status_layout.addWidget(gui.next_step_label)
    status_layout.addWidget(gui.workflow_hint_label)
    status_layout.addWidget(gui.readiness_label)

    gui.run_all_btn = QPushButton("Create Vietnamese Output")
    gui.run_all_btn.setObjectName("mainActionBtn")
    gui.run_all_btn.clicked.connect(gui.run_all_pipeline)

    gui.export_btn = QPushButton("Export Final Video")
    gui.export_btn.setObjectName("mainActionBtn")
    gui.export_btn.clicked.connect(gui.export_final_video)

    gui.preview_5s_btn = QPushButton("Open 5-Second Preview")
    gui.preview_5s_btn.clicked.connect(gui.preview_five_seconds)

    gui.preview_frame_btn = QPushButton("Open Large Frame Preview")
    gui.preview_frame_btn.clicked.connect(gui.preview_exact_frame)

    gui.open_output_btn = QPushButton("Open Results Folder")
    gui.open_output_btn.clicked.connect(lambda: gui.open_folder(gui.final_output_folder_edit.text()))

    gui.stabilize_button(gui.run_all_btn, min_width=260)
    gui.stabilize_button(gui.preview_frame_btn, min_width=260)
    gui.stabilize_button(gui.preview_5s_btn, min_width=260)
    gui.stabilize_button(gui.export_btn, min_width=260)
    gui.stabilize_button(gui.open_output_btn, min_width=260)

    actions_layout = QVBoxLayout()
    actions_row_1 = QHBoxLayout()
    actions_row_1.addWidget(gui.run_all_btn)
    actions_row_1.addWidget(gui.export_btn)
    actions_row_2 = QHBoxLayout()
    actions_row_2.addWidget(gui.preview_frame_btn)
    actions_row_2.addWidget(gui.preview_5s_btn)
    actions_layout.addLayout(actions_row_1)
    actions_layout.addLayout(actions_row_2)

    start_layout.addWidget(QLabel("2. What do you want back?"))
    start_layout.addWidget(gui.output_mode_combo)
    start_layout.addWidget(QLabel("3. Source speech language"))
    start_layout.addWidget(gui.lang_whisper_combo)
    start_layout.addWidget(QLabel("4. Translation quality"))
    start_layout.addWidget(gui.enable_ai_polish_cb)
    start_layout.addWidget(QLabel("5. Save results to"))
    start_layout.addLayout(final_out_layout)
    start_layout.addWidget(status_card)
    start_layout.addLayout(actions_layout)
    start_layout.addWidget(gui.open_output_btn)
    left_layout.addWidget(start_group)


def build_workflow_group(left_layout):
    workflow_group = QGroupBox("WORKFLOW")
    workflow_layout = QVBoxLayout(workflow_group)
    workflow_text = QLabel(
        "Recommended flow:\n"
        "1. Choose the video.\n"
        "2. Pick the output mode.\n"
        "3. Click 'Create Vietnamese Output'.\n"
        "4. Use 'Open Large Frame Preview' or 'Open 5-Second Preview' to review the real subtitle render.\n"
        "5. Click 'Export Final Video' when you are happy."
    )
    workflow_text.setWordWrap(True)
    workflow_layout.addWidget(workflow_text)
    left_layout.addWidget(workflow_group)
