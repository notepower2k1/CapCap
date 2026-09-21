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

from PySide6.QtWidgets import QApplication, QWidget
from main_window import VideoTranslatorGUI, _relaunch_launcher

app = QApplication.instance() or QApplication([])


class TestProjectSwitch(unittest.TestCase):
    def test_terminate_workers_graceful_interruption(self):
        """Verify _terminate_workers requests interruption instead of force-killing."""
        gui = MagicMock(spec=VideoTranslatorGUI)
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        gui.extraction_thread = mock_worker
        gui._segment_preview_threads = {}

        VideoTranslatorGUI._terminate_workers(gui, close_media=False)

        mock_worker.requestInterruption.assert_called_once()
        mock_worker.quit.assert_called_once()
        # Ensure terminate() was NOT called on the thread
        mock_worker.terminate.assert_not_called()

    def test_terminate_workers_does_not_close_media_when_false(self):
        gui = MagicMock(spec=VideoTranslatorGUI)
        gui.extraction_thread = None
        gui._segment_preview_threads = {}
        gui.media_player = MagicMock()

        VideoTranslatorGUI._terminate_workers(gui, close_media=False)
        gui.media_player.close.assert_not_called()

        VideoTranslatorGUI._terminate_workers(gui, close_media=True)
        gui.media_player.close.assert_called_once()

    def test_relaunch_launcher_reuses_existing_window(self):
        """Verify _relaunch_launcher reuses existing_window instead of instantiating new window."""
        existing_gui = MagicMock(spec=VideoTranslatorGUI)

        with patch("views.launcher.show_launcher", return_value="C:/path/to/video2.mp4"), \
             patch("views.launcher.LauncherWindow.add_recent"):

            res = _relaunch_launcher(existing_window=existing_gui)

            self.assertIs(res, existing_gui)
            existing_gui.load_video_project.assert_called_once_with("C:/path/to/video2.mp4")

    def test_relaunch_launcher_closes_existing_window_on_cancel(self):
        """Verify _relaunch_launcher closes existing_window when launcher is cancelled."""
        existing_gui = MagicMock(spec=VideoTranslatorGUI)

        with patch("views.launcher.show_launcher", return_value=""), \
             patch("PySide6.QtWidgets.QApplication.quit") as mock_quit:

            res = _relaunch_launcher(existing_window=existing_gui)

            self.assertIsNone(res)
            existing_gui.close.assert_called_once()
            mock_quit.assert_called_once()


    def test_terminate_workers_tracks_slow_workers_in_retiring_list(self):
        """Verify still-running workers are safely preserved in _retiring_workers."""
        gui = MagicMock(spec=VideoTranslatorGUI)
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        gui._timeline_thumbnail_worker = mock_worker
        gui._segment_preview_threads = {}
        gui._retiring_workers = []

        VideoTranslatorGUI._terminate_workers(gui, close_media=False)

        self.assertIn(mock_worker, gui._retiring_workers)
        self.assertIsNone(gui._timeline_thumbnail_worker)

    def test_terminate_workers_stops_timers(self):
        """Verify _terminate_workers cancels active visual timers."""
        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._timeline_thumbnail_worker = None
        gui._segment_preview_threads = {}
        gui._timeline_visual_refresh_timer = MagicMock()
        gui.live_subtitle_preview_timer = MagicMock()

        VideoTranslatorGUI._terminate_workers(gui, close_media=False)

        gui._timeline_visual_refresh_timer.stop.assert_called_once()
        gui.live_subtitle_preview_timer.stop.assert_called_once()
        self.assertFalse(gui._pending_timeline_waveform_refresh)
        self.assertFalse(gui._pending_timeline_thumbnail_refresh)


if __name__ == "__main__":
    unittest.main()
