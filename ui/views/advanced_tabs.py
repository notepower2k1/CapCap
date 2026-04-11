import os

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
    QPushButton,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


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
    section_layout.addWidget(gui.toggle_advanced_btn)

    gui.advanced_section_content = QWidget()
    gui.advanced_section_content.setVisible(False)
    section_layout.addWidget(gui.advanced_section_content)

    gui.advanced_group = QGroupBox("")
    advanced_layout = QVBoxLayout(gui.advanced_section_content)
    advanced_layout.setSpacing(12)
    advanced_layout.setContentsMargins(0, 0, 0, 0)
    advanced_layout.addWidget(gui.advanced_group)

    group_layout = QVBoxLayout(gui.advanced_group)
    group_layout.setSpacing(12)

    _build_hidden_runtime_widgets(gui)
    _build_audio_mix_controls(gui, group_layout)
    left_layout.addWidget(gui.advanced_section, 1)


def _build_audio_mix_controls(gui, advanced_layout):
    source_title = QLabel("Audio for Preview / Export")
    advanced_layout.addWidget(source_title)

    source_mode_row = QHBoxLayout()
    source_mode_row.addWidget(gui.use_generated_audio_radio)
    source_mode_row.addWidget(gui.use_existing_audio_radio)
    advanced_layout.addLayout(source_mode_row)
    advanced_layout.addWidget(gui.audio_source_hint_label)

    gui.generated_audio_section_label = QLabel("Generate a new Vietnamese voice track")
    advanced_layout.addWidget(gui.generated_audio_section_label)
    gui.generated_audio_section_hint = gui.make_helper_label(
        "Use this when you want CapCap to create a Vietnamese voice track and optionally mix it with your own background audio."
    )
    gui.generated_audio_section_hint.setParent(gui)
    advanced_layout.addWidget(gui.generated_audio_section_hint)

    bg_label = QLabel("Background music / ambient audio")
    gui.bg_music_label = bg_label
    advanced_layout.addWidget(gui.bg_music_label)
    bg_row = QHBoxLayout()
    bg_row.addWidget(gui.bg_music_edit, 1)
    gui.browse_bg_music_btn = QPushButton("Browse")
    gui.browse_bg_music_btn.clicked.connect(gui.browse_background_audio)
    bg_row.addWidget(gui.browse_bg_music_btn)
    advanced_layout.addLayout(bg_row)

    gui.existing_audio_section_label = QLabel("Use your own finished audio")
    advanced_layout.addWidget(gui.existing_audio_section_label)
    gui.existing_audio_section_hint = gui.make_helper_label(
        "Use this when you already have a final audio track and only want preview/export to use that file."
    )
    gui.existing_audio_section_hint.setParent(gui)
    advanced_layout.addWidget(gui.existing_audio_section_hint)

    existing_label = QLabel("Finished audio file")
    gui.mixed_audio_label = existing_label
    advanced_layout.addWidget(gui.mixed_audio_label)
    existing_row = QHBoxLayout()
    existing_row.addWidget(gui.mixed_audio_edit, 1)
    gui.browse_mixed_audio_btn = QPushButton("Browse")
    gui.browse_mixed_audio_btn.clicked.connect(gui.browse_existing_mixed_audio)
    existing_row.addWidget(gui.browse_mixed_audio_btn)
    advanced_layout.addLayout(existing_row)

    voice_gain_row = QHBoxLayout()
    gui.voice_gain_label = QLabel("Voice volume")
    voice_gain_row.addWidget(gui.voice_gain_label)
    voice_gain_row.addWidget(gui.voice_gain_spin)
    advanced_layout.addLayout(voice_gain_row)

    bg_gain_row = QHBoxLayout()
    gui.bg_gain_label = QLabel("BG volume")
    bg_gain_row.addWidget(gui.bg_gain_label)
    bg_gain_row.addWidget(gui.bg_gain_spin)
    advanced_layout.addLayout(bg_gain_row)

    ducking_row = QHBoxLayout()
    gui.ducking_amount_label = QLabel("Lower BG during voice")
    ducking_row.addWidget(gui.ducking_amount_label)
    ducking_row.addWidget(gui.ducking_amount_spin)
    advanced_layout.addLayout(ducking_row)

    preset_row = QHBoxLayout()
    gui.audio_mix_preset_label = QLabel("Mix style")
    preset_row.addWidget(gui.audio_mix_preset_label)
    gui.audio_mix_preset_combo = QComboBox(gui)
    gui.audio_mix_preset_combo.addItem("Custom", "custom")
    gui.audio_mix_preset_combo.addItem("Voice Focus", "voice_focus")
    gui.audio_mix_preset_combo.addItem("Balanced", "balanced")
    gui.audio_mix_preset_combo.addItem("Music Forward", "music_forward")
    gui.audio_mix_preset_combo.currentIndexChanged.connect(gui.on_audio_mix_preset_changed)
    preset_row.addWidget(gui.audio_mix_preset_combo, 1)
    advanced_layout.addLayout(preset_row)

    gui.audio_mix_preset_hint = gui.make_helper_label(
        "Use a preset if you want the voice to stand out more, or keep more of the original background audio."
    )
    gui.audio_mix_preset_hint.setParent(gui)
    advanced_layout.addWidget(gui.audio_mix_preset_hint)

    timing_sync_row = QHBoxLayout()
    gui.voice_timing_sync_label = QLabel("Voice timing sync")
    timing_sync_row.addWidget(gui.voice_timing_sync_label)
    timing_sync_row.addWidget(gui.voice_timing_sync_combo, 1)
    advanced_layout.addLayout(timing_sync_row)
    gui.voice_timing_sync_hint_label = gui.make_helper_label(
        "Smart keeps the voice natural while still trying to fit subtitle timing."
    )
    gui.voice_timing_sync_hint_label.setParent(gui)
    advanced_layout.addWidget(gui.voice_timing_sync_hint_label)

    advanced_layout.addWidget(gui.voiceover_btn)


def _build_hidden_runtime_widgets(gui):
    gui.audio_folder_edit = QLineEdit(os.path.join(gui.workspace_root, "temp"), gui)
    gui.audio_source_edit = QLineEdit(gui)
    gui.srt_output_folder_edit = QLineEdit(os.path.join(gui.workspace_root, "output"), gui)
    gui.keep_audio_cb = QCheckBox("Keep extracted audio", gui)
    gui.keep_audio_cb.setChecked(True)

    gui.extract_btn = QPushButton("Extract Audio", gui)
    gui.vocal_sep_btn = QPushButton("Separate Voice and Background", gui)
    gui.transcribe_btn = QPushButton("Create Original Subtitle", gui)
    gui.translate_btn = QPushButton("Translate to Vietnamese", gui)

    gui.bg_music_edit = QLineEdit(gui)
    gui.mixed_audio_edit = QLineEdit(gui)
    gui.use_generated_audio_radio = QRadioButton("Use generated Vietnamese voice", gui)
    gui.use_existing_audio_radio = QRadioButton("Use existing mixed audio", gui)
    gui.use_generated_audio_radio.setChecked(True)
    gui.audio_source_hint_label = gui.make_helper_label(
        "Preview and export will use the generated voice or voice+background mix by default."
    )
    gui.audio_source_hint_label.setParent(gui)
    gui.voice_output_folder_edit = QLineEdit(os.path.join(gui.workspace_root, "output"), gui)

    gui.voice_gain_spin = QDoubleSpinBox(gui)
    gui.voice_gain_spin.setRange(-30.0, 30.0)
    gui.voice_gain_spin.setSingleStep(1.0)
    gui.voice_gain_spin.setValue(6.0)
    gui.bg_gain_spin = QDoubleSpinBox(gui)
    gui.bg_gain_spin.setRange(-30.0, 30.0)
    gui.bg_gain_spin.setSingleStep(1.0)
    gui.bg_gain_spin.setValue(0.0)
    gui.ducking_amount_spin = QDoubleSpinBox(gui)
    gui.ducking_amount_spin.setRange(-24.0, 0.0)
    gui.ducking_amount_spin.setSingleStep(1.0)
    gui.ducking_amount_spin.setValue(-6.0)

    gui.voiceover_btn = QPushButton("Generate Voice / Mix", gui)
    gui.keep_timeline_cb = QCheckBox("Keep the current timeline when editing Vietnamese text", gui)
    gui.keep_timeline_cb.setChecked(True)
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
