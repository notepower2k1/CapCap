"""Exercise synchronized STT/TTS selectors in the real Qt settings dialog."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_asr_filter_settings as settings_harness
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton
from ui.i18n import t


class CapCutBackendSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings_harness.TestASRFilterSettings.setUpClass()

    def setUp(self):
        self.host = settings_harness.TestASRFilterSettings()
        self.host.setUp()

    def tearDown(self):
        self.host.tearDown()

    def test_pc_selection_is_shared_saved_and_restored_and_can_return_to_web(self):
        def select_pc():
            dialog = QApplication.activeModalWidget()
            stt = dialog.findChild(QComboBox, "capcut_stt_backend_combo")
            backend = dialog.findChild(QComboBox, "capcut_backend_combo")
            self.assertEqual(backend.currentData(), "web")
            stt.setCurrentIndex(stt.findData("pc"))
            self.assertEqual(backend.currentData(), "pc")
            dialog.findChild(QLineEdit, "capcut_pc_path_edit").setText(r"D:\Apps\CapCut")
            next(button for button in dialog.findChildren(QPushButton) if button.text() == t("Save")).click()

        with patch("app.capcut.transport.find_capcut_pc_dll", return_value=Path(r"D:\Apps\CapCut\sscronet.dll")):
            self.host._open_dialog(select_pc)
        self.assertEqual(os.environ["CAPCUT_BACKEND"], "pc")
        self.assertEqual(self.host.settings.value("capcut_backend"), "pc")
        self.assertEqual(self.host.settings.value("capcut_pc_path"), r"D:\Apps\CapCut")
        saved_env = (Path(self.host.temp_dir.name) / ".env").read_text(encoding="utf-8")
        self.assertIn("CAPCUT_BACKEND=pc", saved_env)
        self.assertIn(r"CAPCUT_PC_PATH=D:\Apps\CapCut", saved_env)
        os.environ.pop("CAPCUT_BACKEND")
        os.environ.pop("CAPCUT_PC_PATH")

        def restore_then_select_web():
            dialog = QApplication.activeModalWidget()
            stt = dialog.findChild(QComboBox, "capcut_stt_backend_combo")
            backend = dialog.findChild(QComboBox, "capcut_backend_combo")
            self.assertEqual(backend.currentData(), "pc")
            self.assertEqual(stt.currentData(), "pc")
            backend.setCurrentIndex(backend.findData("web"))
            self.assertEqual(stt.currentData(), "web")
            next(button for button in dialog.findChildren(QPushButton) if button.text() == t("Save")).click()

        with patch("app.capcut.transport.find_capcut_pc_dll", return_value=Path("sscronet.dll")):
            self.host._open_dialog(restore_then_select_web)
        self.assertEqual(os.environ["CAPCUT_BACKEND"], "web")
        self.assertEqual(self.host.settings.value("capcut_backend"), "web")

    def test_cancel_does_not_change_backend_or_path(self):
        def cancel():
            dialog = QApplication.activeModalWidget()
            backend = dialog.findChild(QComboBox, "capcut_backend_combo")
            backend.setCurrentIndex(backend.findData("pc"))
            dialog.findChild(QLineEdit, "capcut_pc_path_edit").setText("unused")
            dialog.reject()

        self.host._open_dialog(cancel)
        self.assertNotIn("CAPCUT_BACKEND", os.environ)
        self.assertIsNone(self.host.settings.value("capcut_backend"))


if __name__ == "__main__":
    unittest.main()
