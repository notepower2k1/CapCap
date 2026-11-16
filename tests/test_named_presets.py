import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
for directory in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[1] / "ui", Path(__file__).resolve().parents[1] / "app"):
    sys.path.insert(0, str(directory))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
from widgets.named_presets import NamedPresets


class NamedPresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_saved_library_survives_reopen_and_delete_without_modifying_other_library(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "presets.ini"), QSettings.IniFormat)
            parent = QWidget()
            loaded = []
            value = {"prompt": "Use friendly language", "preset": "general_default"}
            widget = NamedPresets(parent, settings=settings, key="prompts", capture=lambda: value, apply=loaded.append)
            with patch("widgets.named_presets.QInputDialog.getText", return_value=("My prompt", True)):
                widget.save_current()
            reopened = NamedPresets(parent, settings=settings, key="prompts", capture=lambda: value, apply=loaded.append)
            self.assertEqual(loaded, [])  # Opening does not apply a saved preset.
            reopened.combo.setCurrentIndex(reopened.combo.findData("My prompt"))
            reopened.load_selected()
            self.assertEqual(loaded, [value])
            with patch("widgets.named_presets.QMessageBox.question", return_value=QMessageBox.Yes):
                reopened.delete_selected()
            self.assertEqual(reopened.read(), {})
            self.assertIsNone(settings.value("mixes"))
            parent.close()

    def test_custom_prompt_always_gets_output_language_and_numbered_contract(self):
        from app.translation.prompt_builder import build_translation_messages
        system, user = build_translation_messages(source_texts=["a", "b"], translated_texts=None,
            src_lang="en", target_lang="vi", custom_system_prompt="Use friendly language")
        self.assertIn("Use friendly language", system)
        self.assertIn("Output language: vi", system)
        self.assertIn("exactly 2 numbered lines", system)
        self.assertEqual(user, "1. a\n2. b")
