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

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget
from widgets.loading_overlay import MainWindowLoadingOverlay
from main_window import VideoTranslatorGUI
from ui.i18n import t

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

    def test_event_filter_layout_request(self):
        self.overlay.show_for_video("test.mp4")
        self.parent.resize(800, 600)
        ev = QEvent(QEvent.Type.LayoutRequest)
        handled = self.overlay.eventFilter(self.parent, ev)
        self.assertEqual(self.overlay.geometry(), self.parent.rect())

    def test_event_filter_window_event(self):
        win = QWidget()
        win.resize(1000, 700)
        central = QWidget(win)
        central.resize(1000, 700)
        overlay = MainWindowLoadingOverlay(central)
        overlay.show_for_video("test.mp4")
        ev = QEvent(QEvent.Type.Resize)
        overlay.eventFilter(win, ev)
        self.assertEqual(overlay.geometry(), central.rect())
        overlay.dismiss()
        win.deleteLater()

    def test_main_window_integration_methods(self):
        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._loading_overlay = MagicMock(spec=MainWindowLoadingOverlay)
        gui._current_video_path = "test.mp4"

        VideoTranslatorGUI.show_loading_overlay(gui, "test.mp4", 2000)
        gui._loading_overlay.show_for_video.assert_called_once_with("test.mp4", 2000)
        gui._loading_overlay.setGeometry.assert_called_once()
        gui._loading_overlay.raise_.assert_called_once()

        VideoTranslatorGUI.hide_loading_overlay(gui, fade=True)
        gui._loading_overlay.dismiss.assert_called_once_with(fade=True)

    def test_show_completion(self):
        called = []
        def on_click():
            called.append(True)

        self.overlay.show_completion(
            title="✅ Clean Project Completed",
            status="Removed 5 files",
            button_text=t("Back to Launcher"),
            on_action=on_click,
        )
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.overlay.title_label.text(), "✅ Clean Project Completed")
        self.assertEqual(self.overlay.status_label.text(), "Removed 5 files")
        self.assertFalse(self.overlay.subtitle_label.isVisible())
        self.assertFalse(self.overlay.progress_bar.isVisible())
        self.assertTrue(self.overlay.action_btn.isVisible())
        self.assertEqual(self.overlay.action_btn.text(), t("Back to Launcher"))

        self.overlay.action_btn.click()
        self.assertEqual(called, [True])

        # Test show_for_video resets the card state
        self.overlay.show_for_video("sample.mp4")
        self.assertTrue(self.overlay.isVisible())
        self.assertFalse(self.overlay.action_btn.isVisible())
        self.assertTrue(self.overlay.progress_bar.isVisible())
        self.assertEqual(self.overlay.subtitle_label.text(), "sample.mp4")
        self.assertTrue(self.overlay.subtitle_label.isVisible())


if __name__ == "__main__":
    unittest.main()
