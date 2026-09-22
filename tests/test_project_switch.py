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
        self.assertFalse(gui._pending_timeline_thumbnail_refresh)

    def test_ensure_media_backend_ready_recreates_when_player_closed(self):
        """Verify ensure_media_backend_ready re-initializes media_player if it was closed."""
        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._media_backend_ready = True
        gui.media_player = MagicMock()
        gui.media_player.is_closed.return_value = True

        VideoTranslatorGUI.ensure_media_backend_ready(gui)
        gui.setup_media_player.assert_called_once()

    def test_media_player_backend_tracks_is_closed(self):
        """Verify is_closed returns False initially and True after close()."""
        from ui.utils.media_backend import QtMediaPlayerBackend
        mock_view = QWidget()
        backend = QtMediaPlayerBackend(mock_view)
        self.assertFalse(backend.is_closed())

    def test_sync_segments_empty_clears_subtitle_layers(self):
        """Verify sync_segments_to_dub_subtitle_layers clears TS1 layers when segments list is empty."""
        from app.layers.timeline import Timeline, Track
        from app.layers.base import LayerType
        from app.layers.dub_subtitle import DubSubtitleLayer
        from app.layers.sync_bridge import sync_segments_to_dub_subtitle_layers

        tl = Timeline(duration=10.0)
        track = Track(name="TS1", type=LayerType.DUB_SUBTITLE, height=80)
        layer = DubSubtitleLayer(id="sub-1", start=1.0, end=3.0, text="Hello")
        track.layers.append(layer)
        tl.tracks.append(track)

        self.assertEqual(len(track.layers), 1)

        result = sync_segments_to_dub_subtitle_layers(tl, [])
        self.assertEqual(result, [])
        self.assertEqual(len(track.layers), 0)

    def test_timeline_reset_timeline_clears_all_tracks_to_defaults(self):
        """Verify EditorTimeline.reset_timeline restores only default V1 and A1 tracks with no subtitle layers."""
        from ui.views.editor.timeline import EditorTimeline
        from app.layers.timeline import Track
        from app.layers.base import LayerType
        from app.layers.dub_subtitle import DubSubtitleLayer

        widget = EditorTimeline()
        # Add TS1 track with layers
        ts_track = Track(name="TS1", type=LayerType.DUB_SUBTITLE, height=80)
        ts_track.layers.append(DubSubtitleLayer(id="s1", start=0.0, end=2.0, text="Test"))
        widget._timeline.tracks.append(ts_track)
        widget._segment_indices["s1"] = 0
        widget._duration = 100.0

        widget.reset_timeline()

        self.assertEqual(widget._duration, 0.0)
        self.assertEqual(len(widget._segment_indices), 0)
        track_names = [t.name for t in widget._timeline.tracks]
        self.assertEqual(track_names, ["V1 Video", "A1 Audio"])
        for t in widget._timeline.tracks:
            self.assertEqual(len(t.layers), 0)

    def test_persistence_suppressed_when_cleaning_or_resetting(self):
        """Verify persistence methods return immediately when _is_cleaning_or_resetting is True."""
        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._is_cleaning_or_resetting = True
        gui._pending_timeline_persist = False

        VideoTranslatorGUI.schedule_timeline_project_persist(gui)
        self.assertFalse(gui._pending_timeline_persist)

        gui._pending_timeline_persist = True
        gui._pending_mask_state_persist = True
        gui._pending_blur_state_persist = True
        VideoTranslatorGUI._flush_pending_timeline_persist(gui)
        self.assertFalse(gui._pending_timeline_persist)
        self.assertFalse(gui._pending_mask_state_persist)
        self.assertFalse(gui._pending_blur_state_persist)
        gui.persist_current_timeline_project_data.assert_not_called()

        gui.ensure_current_project = MagicMock()
        VideoTranslatorGUI.persist_current_timeline_project_data(gui)
        gui.ensure_current_project.assert_not_called()

    def test_load_project_context_clears_timeline_when_no_segments(self):
        """Verify load_project_context calls timeline.set_segments([]) and clears rows when project has no segments."""
        gui = MagicMock(spec=VideoTranslatorGUI)
        gui.project_bridge = MagicMock()
        gui.project_bridge.load_context.return_value = {
            "artifacts": {},
            "last_original_srt_path": "",
            "last_translated_srt_path": "",
            "last_extracted_audio": "",
            "last_vocals_path": "",
            "last_music_path": "",
            "last_voice_vi_path": "",
            "last_mixed_vi_path": "",
            "current_segments": [],
            "current_translated_segments": [],
            "current_segment_models": [],
            "current_translated_segment_models": [],
        }
        gui._restore_saved_timeline_model.return_value = False
        gui.timeline = MagicMock()
        gui._segment_editor_rows = ["row1", "row2"]
        gui._selected_segment_index = 0
        gui.isVisible.return_value = False

        mock_state = MagicMock()
        mock_state.settings = {}
        mock_state.artifacts = {}

        VideoTranslatorGUI.load_project_context(gui, mock_state)

        gui.timeline.set_segments.assert_called_with([])
        gui._clear_segment_editor_rows.assert_called_once()
        self.assertEqual(gui._segment_editor_rows, [])
        self.assertEqual(gui._selected_segment_index, -1)
        gui.sync_segment_editor_rows.assert_called_once()
        gui.update_progress_checklist.assert_called_once()
        gui.refresh_ui_state.assert_called_once()


    def test_return_to_launcher_removes_from_recent(self):
        """Verify _return_to_launcher removes target_video_path from recent_projects even if fields were cleared."""
        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._current_video_path = ""
        gui.video_path_edit = MagicMock()
        gui.video_path_edit.text.return_value = ""
        gui.media_player = None

        mock_projects = [
            {"video_path": "D:/CodingTime/media/video1.mp4", "opened_at": 100},
            {"video_path": "D:/CodingTime/media/video2.mp4", "opened_at": 200},
        ]

        saved_projects = []
        with patch("views.launcher._load_recent_projects", return_value=mock_projects), \
             patch("views.launcher._save_recent_projects", side_effect=lambda s, p: saved_projects.extend(p)), \
             patch("PySide6.QtCore.QTimer.singleShot"):

            VideoTranslatorGUI._return_to_launcher(
                gui,
                project_removed_from_recent=True,
                persist_project_data=False,
                target_video_path="D:\\CodingTime\\media\\video1.mp4",
            )

        self.assertEqual(len(saved_projects), 1)
        self.assertEqual(saved_projects[0]["video_path"], "D:/CodingTime/media/video2.mp4")


if __name__ == "__main__":
    unittest.main()



