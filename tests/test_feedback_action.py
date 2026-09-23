import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMenu, QPushButton
from ui.views.main_window import _build_header_bar


class TestFeedbackAction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_header_bar_has_feedback_action(self):
        gui = MagicMock()
        gui.run_all_btn = QPushButton("Run All")
        gui.export_btn = QPushButton("Export")
        gui.preview_5s_btn = QPushButton("Preview 5s")
        gui.toggle_controls_panel = MagicMock()
        gui.clean_current_project = MagicMock()
        gui.exit_to_launcher = MagicMock()
        gui.open_model_settings_dialog = MagicMock()
        gui.open_feedback_dialog = MagicMock()

        header = _build_header_bar(gui)
        self.assertIsNotNone(gui.more_actions_btn)
        menu = gui.more_actions_btn.menu()
        self.assertIsNotNone(menu)

        actions = [a.text() for a in menu.actions()]
        self.assertIn("Feedback", actions)
        self.assertIn("Settings", actions)
        self.assertIn("Clean", actions)
        self.assertIn("Exit", actions)

        # Trigger feedback action
        gui.feedback_action.trigger()
        gui.open_feedback_dialog.assert_called_once()

    @patch.object(QDialog, "exec")
    def test_open_feedback_dialog_structure(self, mock_exec):
        from ui.main_window import VideoTranslatorGUI

        win = VideoTranslatorGUI.__new__(VideoTranslatorGUI)
        win.open_feedback_dialog()
        mock_exec.assert_called_once()


if __name__ == "__main__":
    unittest.main()
