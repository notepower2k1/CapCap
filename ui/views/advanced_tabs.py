import os
from i18n import t

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _advanced_block(title: str):
    card = QFrame()
    card.setObjectName("statusCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(10)

    heading = QLabel(title)
    heading.setObjectName("sectionTitle")
    layout.addWidget(heading)
    return card, layout


def build_advanced_group(gui, left_layout):
    gui.advanced_section = QFrame()
    gui.advanced_section.setObjectName("statusCard")
    section_layout = QVBoxLayout(gui.advanced_section)
    section_layout.setContentsMargins(12, 12, 12, 12)
    section_layout.setSpacing(10)

    gui.toggle_advanced_btn = QToolButton()
    gui.toggle_advanced_btn.setCheckable(True)
    gui.toggle_advanced_btn.setChecked(False)
    gui.toggle_advanced_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    gui.toggle_advanced_btn.setStyleSheet(
        "QToolButton { text-align: left; font-weight: 700; color: #8ad7ff; border: none; padding: 0; }"
    )
    gui.toggle_advanced_btn.toggled.connect(gui.on_advanced_toggled)
    gui.toggle_advanced_btn.hide()

    gui.advanced_section_content = QWidget()
    gui.advanced_section_content.setVisible(True)
    section_layout.addWidget(gui.advanced_section_content)

    gui.advanced_group = QGroupBox("")
    advanced_layout = QVBoxLayout(gui.advanced_section_content)
    advanced_layout.setSpacing(12)
    advanced_layout.setContentsMargins(0, 0, 0, 0)
    advanced_layout.addWidget(gui.advanced_group)

    group_layout = QVBoxLayout(gui.advanced_group)
    group_layout.setSpacing(12)
    group_layout.setContentsMargins(0, 0, 0, 0)

    _build_hidden_runtime_widgets(gui)
    subtitle_card, subtitle_layout = _advanced_block("Subtitle")
    subtitle_layout.addWidget(gui.import_original_srt_btn)
    subtitle_card.hide()
    group_layout.addWidget(subtitle_card)

    logs_card, logs_layout = _advanced_block("Logs")
    logs_layout.addWidget(gui.make_helper_label(
        "Runtime messages are kept here for troubleshooting and bug reports."
    ))
    gui.runtime_log_view = QPlainTextEdit()
    gui.runtime_log_view.setObjectName("runtimeLogView")
    gui.runtime_log_view.setReadOnly(True)
    gui.runtime_log_view.setMaximumBlockCount(10000)
    gui.runtime_log_view.setMinimumHeight(160)
    gui.runtime_log_view.setPlaceholderText(t("Runtime logs will appear here."))
    gui.runtime_log_view.setStyleSheet(
        "QPlainTextEdit#runtimeLogView { background: #0b1220; color: #c9d8e8; "
        "border: 1px solid #30425b; border-radius: 6px; padding: 6px; font-family: Consolas, monospace; }"
    )
    existing_logs = getattr(gui, "_runtime_logs", [])
    if existing_logs:
        gui.runtime_log_view.setPlainText("\n".join(existing_logs))
        gui._runtime_log_view_entry_count = len(existing_logs)
    logs_layout.addWidget(gui.runtime_log_view)
    logs_actions = QHBoxLayout()
    gui.export_logs_btn = QPushButton(t("Export Logs"))
    gui.export_logs_btn.clicked.connect(gui.export_runtime_logs)
    gui.clear_logs_btn = QPushButton(t("Clear Logs"))
    gui.clear_logs_btn.clicked.connect(gui.clear_log)
    logs_actions.addWidget(gui.export_logs_btn)
    logs_actions.addWidget(gui.clear_logs_btn)
    logs_actions.addStretch(1)
    logs_layout.addLayout(logs_actions)
    group_layout.addWidget(logs_card)

    source_card = _build_audio_source_controls(gui)
    audio_layout = getattr(gui, "workflow_audio_layout", None)
    if audio_layout is not None:
        # Place Music Layer controls before Track Volumes, where users can
        # add the source before adjusting its independent level.
        audio_layout.insertWidget(0, source_card)
    else:
        group_layout.addWidget(source_card)
    target_layout = getattr(gui, "workflow_advanced_layout", None) or left_layout
    target_layout.addWidget(gui.advanced_section, 1)


def _build_audio_source_controls(gui):
    # Audio is now composed from independent timeline tracks.  Keep the old
    # hidden source widgets for project compatibility, but do not expose the
    # radio-button/file-source workflow in the Audio tab.
    source_card, source_layout = _advanced_block("Music Layer")
    source_layout.addWidget(
        gui.make_helper_label(
            "Add music as its own timeline track. Its volume is controlled independently in Track Volumes."
        )
    )
    gui.add_music_layer_btn = QPushButton("Add Music Layer")
    gui.add_music_layer_btn.setToolTip("Choose an audio file and add it as an independent music track.")
    gui.add_music_layer_btn.clicked.connect(gui.add_music_layer)
    source_layout.addWidget(gui.add_music_layer_btn)
    gui.music_layers_summary_label = gui.make_helper_label("No music layer added.")
    source_layout.addWidget(gui.music_layers_summary_label)
    return source_card

def _build_hidden_runtime_widgets(gui):
    gui.audio_folder_edit = QLineEdit(os.path.join(gui.workspace_root, "temp"), gui)
    gui.audio_source_edit = QLineEdit(gui)
    gui.srt_output_folder_edit = QLineEdit(os.path.join(gui.workspace_root, "output"), gui)
    gui.keep_audio_cb = QCheckBox("Keep extracted audio", gui)
    gui.keep_audio_cb.setChecked(True)

    gui.extract_btn = QPushButton("Extract Audio", gui)
    gui.vocal_sep_btn = QPushButton("Separate Voice and Background", gui)
    gui.transcribe_btn = QPushButton("Create Original Subtitle", gui)
    gui.import_original_srt_btn = QPushButton("Import Original Subtitle", gui)
    gui.import_original_srt_btn.hide()
    gui.translate_btn = QPushButton("Translate to Vietnamese", gui)

    gui.bg_music_edit = QLineEdit(gui)
    gui.mixed_audio_edit = QLineEdit(gui)
    # These two controls are retained as non-visual compatibility state for
    # projects/settings written by older releases.  Audio source selection is
    # now represented by the A1/TS1/A2 timeline tracks, so the old labels must
    # never be exposed in the active UI.
    gui.use_generated_audio_radio = QRadioButton(gui)
    gui.use_generated_audio_radio.setObjectName("legacyGeneratedAudioSource")
    gui.use_existing_audio_radio = QRadioButton(gui)
    gui.use_existing_audio_radio.setObjectName("legacyExistingAudioSource")
    gui.use_generated_audio_radio.setChecked(True)
    gui.audio_source_hint_label = gui.make_helper_label(
        "Preview and export will use the generated voice or voice+background mix by default."
    )
    gui.audio_source_hint_label.setParent(gui)
    gui.audio_source_hint_label.hide()
    gui.voice_output_folder_edit = QLineEdit(os.path.join(gui.workspace_root, "output"), gui)

    # Subtitle timing is always preserved. Keep this hidden compatibility
    # control for existing editing code, but do not expose it as a setting.
    gui.keep_timeline_cb = QCheckBox(gui)
    gui.keep_timeline_cb.setChecked(True)
    gui.keep_timeline_cb.hide()
    gui.apply_translated_btn = QPushButton("Apply Edited Subtitle To Preview", gui)
    gui.apply_translated_btn.clicked.connect(gui.apply_edited_translation)
    gui.apply_translated_btn.hide()
    gui.auto_preview_frame_cb = QCheckBox("Auto refresh exact frame preview", gui)
    gui.auto_preview_frame_cb.setChecked(False)
    gui.auto_preview_frame_cb.hide()

    gui.show_artifacts_btn = QPushButton("Show Processed Files", gui)
    gui.show_artifacts_btn.clicked.connect(gui.show_processed_files)
    gui.open_temp_btn = QPushButton("Open Temp Folder", gui)
    gui.open_temp_btn.clicked.connect(lambda: gui.open_folder(gui.audio_folder_edit.text()))

    hidden_widgets = [
        gui.audio_folder_edit,
        gui.audio_source_edit,
        gui.srt_output_folder_edit,
        gui.keep_audio_cb,
        gui.extract_btn,
        gui.vocal_sep_btn,
        gui.transcribe_btn,
        gui.translate_btn,
        gui.bg_music_edit,
        gui.mixed_audio_edit,
        gui.use_generated_audio_radio,
        gui.use_existing_audio_radio,
        gui.audio_source_hint_label,
        gui.voice_output_folder_edit,
        gui.apply_translated_btn,
        gui.auto_preview_frame_cb,
        gui.show_artifacts_btn,
        gui.open_temp_btn,
    ]
    for widget in hidden_widgets:
        widget.hide()
        widget.setVisible(False)
        widget.setGeometry(0, 0, 0, 0)
