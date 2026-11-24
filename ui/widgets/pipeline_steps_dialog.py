from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from i18n import t


class PipelineStepsDialog(QDialog):
    def __init__(self, parent, *, has_transcript, has_translation, same_language, supports_voice):
        super().__init__(parent)
        self.setWindowTitle(t("Choose processing steps"))
        self._has_transcript = has_transcript
        self._has_translation = has_translation
        self._supports_voice = supports_voice
        layout = QVBoxLayout(self)
        self.transcript = QCheckBox(t("Transcript"))
        self.translate = QCheckBox(t("Translate"))
        self.voice = QCheckBox(t("Generate Voice"))
        self.transcript.setChecked(not has_transcript)
        self.translate.setChecked(not has_translation and not same_language)
        self.voice.setChecked(supports_voice and (self.translate.isChecked() or has_translation))
        self._hints = []
        for check in (self.transcript, self.translate, self.voice):
            layout.addWidget(check)
            hint = QLabel()
            hint.setWordWrap(True)
            hint.setObjectName("helperLabel")
            layout.addWidget(hint)
            self._hints.append(hint)
            check.toggled.connect(self._update_steps)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._start = buttons.button(QDialogButtonBox.Ok)
        self._start.setText(t("Start"))
        buttons.button(QDialogButtonBox.Cancel).setText(t("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(360)
        self._update_steps()

    def _update_steps(self):
        transcribe = self.transcript.isChecked()
        can_translate = transcribe or self._has_transcript
        self.translate.setEnabled(can_translate)
        if not can_translate:
            self.translate.setChecked(False)
        translate = self.translate.isChecked()
        reuse_translation = self._has_translation and not transcribe
        can_generate_voice = self._supports_voice and (translate or reuse_translation)
        self.voice.setEnabled(can_generate_voice)
        if not can_generate_voice:
            self.voice.setChecked(False)
        self._hints[0].setText(t("Reuse the existing transcript.") if self._has_transcript and not transcribe else "")
        self._hints[1].setText(t(
            "Transcript is required." if not can_translate else
            "Reuse existing translated subtitles." if not translate and reuse_translation else
            "Use original subtitles." if not translate else ""
        ))
        self._hints[2].setText(t(
            "Translation is required." if self._supports_voice and not can_generate_voice else
            "Keep original audio." if not self.voice.isChecked() else ""
        ))
        for hint in self._hints:
            hint.setVisible(bool(hint.text()))
        self._start.setEnabled(transcribe or translate or self.voice.isChecked())
