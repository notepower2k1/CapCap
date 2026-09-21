import os
import sys
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication, QWidget
from widgets.loading_overlay import MainWindowLoadingOverlay
from main_window import VideoTranslatorGUI

app = QApplication.instance() or QApplication([])


class TestMainWindowLoadingOverlay(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.parent.resize(1280, 720)
        self.parent.show()
        self.overlay = MainWindowLoadingOverlay(self.parent)

    def tearDown(self):
        self.overlay.dismiss(fade=False)
        self.overlay.deleteLater()
        self.parent.deleteLater()

    def test_initial_state_hidden(self):
        self.assertFalse(self.overlay.isVisible())

    def test_show_for_video(self):
        self.overlay.show_for_video("C:/path/to/my_video.mp4", max_timeout_ms=1000)
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.overlay.subtitle_label.text(), "my_video.mp4")
        self.assertTrue(self.overlay.subtitle_label.isVisible())

    def test_show_for_empty_video(self):
        self.overlay.show_for_video("", max_timeout_ms=1000)
        self.assertTrue(self.overlay.isVisible())
        self.assertFalse(self.overlay.subtitle_label.isVisible())

    def test_set_status(self):
        self.overlay.set_status("Custom status")
        self.assertEqual(self.overlay.status_label.text(), "Custom status")

    def test_dismiss_instant(self):
        self.overlay.show_for_video("video.mp4", max_timeout_ms=1000)
        self.assertTrue(self.overlay.isVisible())
        self.overlay.dismiss(fade=False)
        self.assertFalse(self.overlay.isVisible())

    def test_main_window_integration_methods(self):
        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._loading_overlay = MagicMock(spec=MainWindowLoadingOverlay)
        gui._current_video_path = "test.mp4"

        VideoTranslatorGUI.show_loading_overlay(gui, "test.mp4", 2000)
        gui._loading_overlay.show_for_video.assert_called_once_with("test.mp4", 2000)

        VideoTranslatorGUI.hide_loading_overlay(gui, fade=True)
        gui._loading_overlay.dismiss.assert_called_once_with(fade=True)


if __name__ == "__main__":
    unittest.main()
