"""Offscreen Qt coverage for the Whisper ASR cleanup settings."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _folder in (PROJECT_ROOT, PROJECT_ROOT / "app", PROJECT_ROOT / "ui"):
    if str(_folder) not in os.sys.path:
        os.sys.path.insert(0, str(_folder))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QLabel, QMessageBox, QPushButton, QSpinBox, QToolButton, QWidget

from ui import main_window
from ui.i18n import get_language, set_language, t
from ui.main_window import VideoTranslatorGUI
from ui.utils.settings_utils import restore_asr_filter_settings


class TestASRFilterSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app._capcap_test_mode = True

    def setUp(self):
        self.previous_language = get_language()
        set_language("en")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp_dir.name) / "settings.ini"), QSettings.IniFormat)
        self.host = QWidget()
        self.host.settings = self.settings
        self.engine = "whisper"
        self.host.get_transcription_engine = lambda: self.engine
        self.host.get_whisper_model_name = lambda: "base"
        self.host.selected_whisper_model_name = "base"
        self.host.selected_voice_engine = "piper"
        self.host.log = MagicMock()
        self.host.open_resource_manager_dialog = MagicMock()
        self.host.load_voice_preview_catalog = MagicMock()
        self.host.set_project_transcription_engine = MagicMock()
        self.host.save_user_settings = MagicMock()
        self.host._update_ocr_overlay = MagicMock()
        self.env_patch = patch.dict(os.environ, {"CAPCAP_DEVICE": "cpu", "OPENAI_PROVIDER": "google"}, clear=True)
        self.env_patch.start()

    def tearDown(self):
        try:
            self.host.close()
            self.host.deleteLater()
        except RuntimeError:
            pass
        self.app.processEvents()
        self.env_patch.stop()
        self.temp_dir.cleanup()
        set_language(self.previous_language)

    def _controls(self):
        dialog = QApplication.activeModalWidget()
        self.assertIsNotNone(dialog)
        dedup = dialog.findChild(QCheckBox, "asrDedupEnabledCheckBox")
        dedup_threshold = dialog.findChild(QSpinBox, "asrDedupThresholdSpinBox")
        hallucination = dialog.findChild(QCheckBox, "asrHallucinationFilterEnabledCheckBox")
        silence_threshold = dialog.findChild(QDoubleSpinBox, "asrHallucinationSilenceSecondsSpinBox")
        self.assertIsNotNone(dedup)
        self.assertIsNotNone(dedup_threshold)
        self.assertIsNotNone(hallucination)
        self.assertIsNotNone(silence_threshold)
        save = next(button for button in dialog.findChildren(QPushButton) if button.text() == t("Save"))
        cancel = next(button for button in dialog.findChildren(QPushButton) if button.text() == t("Cancel"))
        return dialog, dedup, dedup_threshold, hallucination, silence_threshold, save, cancel

    def _open_dialog(self, callback):
        previous_cwd = os.getcwd()
        callback_errors = []

        def guarded_callback():
            try:
                callback()
            except Exception as exc:
                callback_errors.append(exc)
                dialog = QApplication.activeModalWidget()
                if dialog is not None:
                    dialog.reject()

        try:
            os.chdir(self.temp_dir.name)
            with patch.object(main_window, "is_remote_profile", return_value=False), \
                    patch("app.engines.elevenlabs_tts.read_elevenlabs_voice_cache", return_value={}), \
                    patch.object(QMessageBox, "information"):
                QTimer.singleShot(0, guarded_callback)
                VideoTranslatorGUI.open_model_settings_dialog(self.host)
        finally:
            os.chdir(previous_cwd)
        if callback_errors:
            raise callback_errors[0]

    def test_defaults_ranges_and_independent_enabled_states(self):
        def inspect_dialog():
            dialog, dedup, dedup_threshold, hallucination, silence_threshold, _save, _cancel = self._controls()
            self.assertTrue(dedup.isChecked())
            self.assertEqual(dedup_threshold.value(), 82)
            self.assertTrue(dedup_threshold.isEnabled())
            self.assertTrue(hallucination.isChecked())
            self.assertEqual(silence_threshold.value(), 3.0)
            self.assertTrue(silence_threshold.isEnabled())
            self.assertEqual((dedup_threshold.minimum(), dedup_threshold.maximum()), (50, 100))
            self.assertEqual((silence_threshold.minimum(), silence_threshold.maximum()), (0.5, 10.0))
            self.assertEqual(silence_threshold.singleStep(), 0.5)
            advanced = dialog.findChild(QWidget, "asrFilterAdvancedSettings")
            advanced_button = dialog.findChild(QToolButton, "asrFilterAdvancedButton")
            self.assertTrue(advanced.isHidden())
            self.assertFalse(advanced_button.isChecked())
            advanced_button.click()
            self.assertFalse(advanced.isHidden())
            self.assertEqual(dedup_threshold.value(), 82)
            self.assertEqual(silence_threshold.value(), 3.0)
            dedup.setChecked(False)
            self.assertFalse(dedup_threshold.isEnabled())
            self.assertTrue(silence_threshold.isEnabled())
            hallucination.setChecked(False)
            self.assertFalse(silence_threshold.isEnabled())
            self.assertFalse(dedup_threshold.isEnabled())
            advanced_button.click()
            self.assertTrue(advanced.isHidden())
            dialog.reject()

        self._open_dialog(inspect_dialog)

    def test_controls_are_only_shown_for_whisper_and_localize_to_vietnamese(self):
        set_language("vi")
        self.engine = "sensevoice"

        def inspect_dialog():
            dialog, *_controls = self._controls()
            asr_filter = dialog.findChild(QWidget, "asrFilterSettings")
            self.assertIsNotNone(asr_filter)
            self.assertTrue(asr_filter.isHidden())
            engine_combo = dialog.findChild(QComboBox, "transcription_engine_combo")
            self.assertIsNotNone(engine_combo)
            engine_combo.setCurrentIndex(engine_combo.findData("whisper"))
            self.assertFalse(asr_filter.isHidden())
            self.assertEqual(
                dialog.findChild(QCheckBox, "asrDedupEnabledCheckBox").text(),
                t("Reduce repeated phrases"),
            )
            self.assertNotEqual(t("Reduce repeated phrases"), "Reduce repeated phrases")
            self.assertEqual(
                dialog.findChild(QLabel, "asrDedupThresholdLabel").text(),
                t("Minimum text similarity:"),
            )
            self.assertEqual(
                dialog.findChild(QLabel, "asrDedupHint").text(),
                t("Helps remove duplicate wording where audio sections join, keeping the fuller sentence when possible. Repeated dialogue at other times is kept."),
            )
            self.assertEqual(
                dialog.findChild(QCheckBox, "asrHallucinationFilterEnabledCheckBox").text(),
                t("Reduce made-up subtitles during silence"),
            )
            self.assertEqual(
                dialog.findChild(QLabel, "asrHallucinationSilenceThresholdLabel").text(),
                t("Silence threshold (seconds):"),
            )
            self.assertEqual(
                dialog.findChild(QLabel, "asrHallucinationHint").text(),
                t('Helps reduce subtitles that were not spoken, such as "Thanks for watching" appearing during silence. May make transcription slower or miss very quiet speech.'),
            )
            self.assertEqual(
                dialog.findChild(QLabel, "asrDedupExample").text(),
                t('Example: "Today we are" + "Today we are going to school" → keep the fuller sentence.'),
            )
            self.assertEqual(
                dialog.findChild(QToolButton, "asrFilterAdvancedButton").text(),
                t("Advanced"),
            )
            self.assertEqual(
                dialog.findChild(QLabel, "asrFilterAdvancedHint").text(),
                t("You can keep the defaults if you are unsure: 82% similarity and 3 seconds of silence."),
            )
            dialog.reject()

        self._open_dialog(inspect_dialog)

    def test_cancel_leaves_qsettings_environment_and_dotenv_unchanged(self):
        self.settings.setValue("asr_dedup_enabled", False)
        self.settings.setValue("asr_dedup_similarity", 0.77)
        self.settings.setValue("asr_hallucination_filter_enabled", True)
        self.settings.setValue("asr_hallucination_silence_threshold", 4.0)
        original_env = {
            "CAPCAP_ASR_DEDUP_ENABLED": "0",
            "CAPCAP_ASR_DEDUP_SIMILARITY": "0.77",
            "CAPCAP_WHISPER_HALLUCINATION_FILTER": "1",
            "CAPCAP_WHISPER_HALLUCINATION_SILENCE_SECONDS": "4.0",
        }
        os.environ.update(original_env)
        env_file = Path(self.temp_dir.name) / ".env"
        original_dotenv = "KEEP=untouched\nCAPCAP_ASR_DEDUP_ENABLED=0\n"
        env_file.write_text(original_dotenv, encoding="utf-8")

        def cancel_dialog():
            dialog, dedup, dedup_threshold, hallucination, silence_threshold, _save, cancel = self._controls()
            dedup.setChecked(True)
            dedup_threshold.setValue(95)
            hallucination.setChecked(False)
            silence_threshold.setValue(6.0)
            cancel.click()

        self._open_dialog(cancel_dialog)
        self.assertEqual(env_file.read_text(encoding="utf-8"), original_dotenv)
        self.assertEqual({key: os.environ.get(key) for key in original_env}, original_env)
        self.assertFalse(self.settings.value("asr_dedup_enabled", type=bool))
        self.assertEqual(float(self.settings.value("asr_dedup_similarity")), 0.77)
        self.assertTrue(self.settings.value("asr_hallucination_filter_enabled", type=bool))
        self.assertEqual(float(self.settings.value("asr_hallucination_silence_threshold")), 4.0)

    def test_save_persists_normalized_values_to_qsettings_environment_and_dotenv(self):
        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text("KEEP=old\n", encoding="utf-8")

        def save_dialog():
            _dialog, dedup, dedup_threshold, hallucination, silence_threshold, save, _cancel = self._controls()
            dedup.setChecked(False)
            dedup_threshold.setValue(95)
            hallucination.setChecked(True)
            silence_threshold.setValue(4.5)
            save.click()

        self._open_dialog(save_dialog)

        self.assertFalse(self.settings.value("asr_dedup_enabled", type=bool))
        self.assertEqual(float(self.settings.value("asr_dedup_similarity")), 0.95)
        self.assertTrue(self.settings.value("asr_hallucination_filter_enabled", type=bool))
        self.assertEqual(float(self.settings.value("asr_hallucination_silence_threshold")), 4.5)
        self.assertEqual(os.environ["CAPCAP_ASR_DEDUP_ENABLED"], "0")
        self.assertEqual(os.environ["CAPCAP_ASR_DEDUP_SIMILARITY"], "0.95")
        self.assertEqual(os.environ["CAPCAP_WHISPER_HALLUCINATION_FILTER"], "1")
        self.assertEqual(os.environ["CAPCAP_WHISPER_HALLUCINATION_SILENCE_SECONDS"], "4.5")
        lines = env_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "KEEP=old")
        self.assertIn("CAPCAP_ASR_DEDUP_ENABLED=0", lines)
        self.assertIn("CAPCAP_ASR_DEDUP_SIMILARITY=0.95", lines)
        self.assertIn("CAPCAP_WHISPER_HALLUCINATION_FILTER=1", lines)
        self.assertIn("CAPCAP_WHISPER_HALLUCINATION_SILENCE_SECONDS=4.5", lines)
        self.host.set_project_transcription_engine.assert_called_once_with("whisper")
        self.host.save_user_settings.assert_called_once_with()

    def test_restore_handles_boolean_strings_and_uses_qsettings_fallbacks(self):
        settings = QSettings(str(Path(self.temp_dir.name) / "restore.ini"), QSettings.IniFormat)
        settings.setValue("asr_dedup_enabled", "false")
        settings.setValue("asr_dedup_similarity", "0.91")
        settings.setValue("asr_hallucination_filter_enabled", "0")
        settings.setValue("asr_hallucination_silence_threshold", "2.5")
        env = {}

        options = restore_asr_filter_settings(settings, env)

        self.assertFalse(options["dedup_enabled"])
        self.assertEqual(options["dedup_similarity"], 0.91)
        self.assertFalse(options["hallucination_filter_enabled"])
        self.assertEqual(options["hallucination_silence_threshold"], 2.5)
        self.assertEqual(env["CAPCAP_ASR_DEDUP_ENABLED"], "0")
        self.assertEqual(env["CAPCAP_ASR_DEDUP_SIMILARITY"], "0.91")
        self.assertEqual(env["CAPCAP_WHISPER_HALLUCINATION_FILTER"], "0")
        self.assertEqual(env["CAPCAP_WHISPER_HALLUCINATION_SILENCE_SECONDS"], "2.5")


if __name__ == "__main__":
    unittest.main()
