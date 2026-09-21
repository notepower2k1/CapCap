#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_preview_transport.py

Unit tests for preview transport, exact/keyframe seeking, timeline clock,
and TimeWarpService synchronization.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root and app are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.services.time_warp_service import TimeWarpService


class TestPreviewTransport(unittest.TestCase):
    """Test transport synchronization, freeze mapping, and seek flags."""

    def test_time_warp_freeze_mapping(self):
        """Verify timeline-to-media time mapping with freeze frame warps."""
        warps = [{"time": 2.0, "duration": 1.0}]
        # Within the freeze interval (2.0s to 3.0s on timeline), media stays locked at anchor 2.0s
        self.assertEqual(TimeWarpService.timeline_to_media_time(2.5, warps), 2.0)
        # After the freeze interval, media resumes (3.5s timeline - 1.0s freeze = 2.5s media)
        self.assertEqual(TimeWarpService.timeline_to_media_time(3.5, warps), 2.5)

    def test_time_warp_multiple_freezes(self):
        """Verify mapping with multiple sequential freeze intervals."""
        warps = [
            {"time": 1.0, "duration": 0.5},  # Timeline [1.0, 1.5] -> Media 1.0
            {"time": 3.0, "duration": 1.0},  # Timeline [3.5, 4.5] -> Media 3.0
        ]
        self.assertEqual(TimeWarpService.timeline_to_media_time(0.5, warps), 0.5)
        self.assertEqual(TimeWarpService.timeline_to_media_time(1.2, warps), 1.0)
        self.assertEqual(TimeWarpService.timeline_to_media_time(2.0, warps), 1.5)
        self.assertEqual(TimeWarpService.timeline_to_media_time(4.0, warps), 3.0)
        self.assertEqual(TimeWarpService.timeline_to_media_time(5.0, warps), 3.5)

    def test_mpv_backend_seek_exact_vs_keyframe_flags(self):
        """Verify MpvMediaPlayerBackend passes exact vs keyframe flags to mpv command."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        # Mock video_view and underlying mpv player
        view = MagicMock()
        view.winId.return_value = 0
        view.video_source_width = 1920
        view.video_source_height = 1080

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "mock_video.mp4"
        backend._position_ms = 0
        backend._video_frozen = False
        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""
        backend._native_audio_active = False
        backend._native_audio_engine = None
        backend._video_time_warps = []
        backend.positionChanged = MagicMock()

        mock_player = MagicMock()
        backend._player = mock_player

        # 1. Seek with exact=True (default)
        backend.setPosition(2500, exact=True)
        mock_player.command.assert_called_with("seek", 2.5, "absolute", "exact")

        # 2. Seek with exact=False (scrubbing fast seek)
        backend.setPosition(3000, exact=False)
        mock_player.command.assert_called_with("seek", 3.0, "absolute", "keyframes")

    def test_seek_burst_throttling_simulation(self):
        """Simulate rapid scrubbing burst where only the release seek is exact."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "mock_video.mp4"
        backend._position_ms = 0
        backend._video_frozen = False
        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""
        backend._native_audio_active = False
        backend._native_audio_engine = None
        backend._video_time_warps = []
        backend.positionChanged = MagicMock()

        mock_player = MagicMock()
        backend._player = mock_player

        # Simulate 10 rapid scrub steps (during mouse drag)
        for t_ms in range(100, 1100, 100):
            backend.setPosition(t_ms, exact=False)
            mock_player.command.assert_called_with("seek", t_ms / 1000.0, "absolute", "keyframes")

        # Final mouse release at 1200ms
        backend.setPosition(1200, exact=True)
        mock_player.command.assert_called_with("seek", 1.2, "absolute", "exact")
        self.assertEqual(backend.position(), 1200)

    def test_set_position_exact_signature_forwarding(self):
        """Verify set_position forwards exact flag to media_player.setPosition."""
        import inspect
        from ui.main_window import VideoTranslatorGUI
        from ui.utils.media_utils import set_position

        # Verify GUI set_position signature accepts exact
        sig = inspect.signature(VideoTranslatorGUI.set_position)
        self.assertIn("exact", sig.parameters)
        self.assertIs(sig.parameters["exact"].default, True)

        # Verify media_utils.set_position forwards exact flag
        gui = MagicMock()
        gui.is_filter_workflow_active.return_value = False
        gui.video_time_warps = []
        gui.media_player.duration.return_value = 10000

        set_position(gui, 3000, exact=False)
        gui.media_player.setPosition.assert_called_with(3000, timeline_pos=3000, exact=False)

        set_position(gui, 4000, exact=True)
        gui.media_player.setPosition.assert_called_with(4000, timeline_pos=4000, exact=True)

    def test_timeline_scrub_finished_attribute(self):
        """Verify timeline mouse release emits scrubFinished with integer playhead ms without AttributeError."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        _ = QApplication.instance() or QApplication([])
        from ui.views.editor.timeline import EditorTimeline

        timeline = EditorTimeline.__new__(EditorTimeline)
        timeline._selection_drag = {"mode": "scrub"}
        timeline._playhead = 2.5
        timeline.scrubFinished = MagicMock()
        event = MagicMock()
        event.button.return_value = Qt.LeftButton

        timeline.mouseReleaseEvent(event)
        timeline.scrubFinished.emit.assert_called_once_with(2500)

    def test_sync_audio_does_not_activate_sidecars_when_native_active(self):
        """Verify legacy audio sidecars are never resumed when native audio is active."""
        from PySide6.QtMultimedia import QMediaPlayer
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "video.mp4"
        backend._video_frozen = False
        backend._player = MagicMock()
        backend._player.time_pos = 2.0
        backend._player.pause = False
        backend._native_audio_active = True
        backend._native_audio_engine = MagicMock()
        backend._native_audio_engine.timeline_position_ms.return_value = 2000

        backend._original_loaded_path = "orig.wav"
        backend._original_player = MagicMock()
        backend._original_player.playbackState.return_value = QMediaPlayer.PlayingState

        backend._dubbed_loaded_path = "dub.wav"
        backend._dubbed_player = MagicMock()
        backend._dubbed_player.playbackState.return_value = QMediaPlayer.PlayingState

        backend._sync_audio_to_video()

        # Legacy players must be paused and NEVER played
        backend._original_player.pause.assert_called_once()
        backend._dubbed_player.pause.assert_called_once()
        backend._original_player.play.assert_not_called()
        backend._dubbed_player.play.assert_not_called()
        # Native engine must receive play
        backend._native_audio_engine.play.assert_called_once()

    def test_freeze_audio_mapping_two_accumulated_freezes(self):
        """Verify freeze detection correctly accumulates multiple warp durations."""
        from ui.utils.preview_audio import _PreviewAudioWorker

        worker = _PreviewAudioWorker.__new__(_PreviewAudioWorker)
        worker._warps = [
            {"time": 2.0, "duration": 1.0},
            {"time": 4.0, "duration": 1.0},
        ]
        # First freeze: [2.0, 3.0]
        self.assertTrue(worker._is_time_frozen(2.5))
        # Normal playback: [3.0, 5.0] (corresponds to media [2.0, 4.0])
        self.assertFalse(worker._is_time_frozen(3.5))
        # Second freeze: anchor 4.0 + 1.0 prior duration = [5.0, 6.0]
        self.assertTrue(worker._is_time_frozen(5.5))
        # Normal playback after second freeze: > 6.0
        self.assertFalse(worker._is_time_frozen(6.5))

    def test_native_sink_ready_and_error_fallback(self):
        """Verify _native_audio_active responds to sinkReady and falls back on error."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = False
        backend.log = MagicMock()

        backend._on_native_audio_sink_ready(True)
        self.assertTrue(backend._native_audio_active)

        backend._on_native_audio_sink_ready(False)
        self.assertFalse(backend._native_audio_active)

        backend._native_audio_active = True
        backend._on_native_audio_error("Sink device disconnected")
        self.assertFalse(backend._native_audio_active)

    def test_apply_preview_audio_track_selection_no_intermediate_wav(self):
        """Verify track selection does not generate intermediate mix WAV when native audio is active."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._preview_audio_track_switching = False
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._resolve_preview_original_video_path.return_value = "video.mp4"
        gui._resolve_preview_original_audio_path.return_value = "orig.wav"
        gui._resolve_preview_voice_only_audio_path.return_value = "voice.wav"
        gui._preferred_preview_audio_track_mode.return_value = "dubbed"
        gui._music_audio_tracks.return_value = []
        gui._compute_audio_track_volume.return_value = 100.0
        gui._get_audio_track_gain_db.return_value = 0.0
        gui._audio_total_duration_ms.return_value = 10000
        gui._is_audio_track_muted.return_value = False

        with patch.object(VideoTranslatorGUI, "_resolve_preview_dubbed_playback_source") as mock_mix:
            VideoTranslatorGUI._apply_preview_audio_track_selection(gui)
            mock_mix.assert_not_called()

    def test_music_audio_tracks_unique_ids_and_gain_calculation(self):
        """Verify unique IDs for music clips and single volume factor."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._normalize_local_file_path = lambda p: p
        layer1 = MagicMock(id="", source="track1.mp3", start=0.0, end=5.0, source_start=0.0)
        layer2 = MagicMock(id="", source="track2.mp3", start=5.0, end=10.0, source_start=0.0)
        track = MagicMock(
            name="A2 Music",
            metadata={"_audio_role": "music", "_volume": 40.0},
            layers=[layer1, layer2],
            muted=False,
            visible=True,
            solo=False,
        )
        gui.timeline = MagicMock()
        gui.timeline._timeline.tracks = [track]
        gui._is_audio_track_muted.return_value = False

        with patch("os.path.exists", return_value=True):
            tracks = VideoTranslatorGUI._music_audio_tracks(gui)
            self.assertEqual(len(tracks), 2)
            self.assertEqual(tracks[0]["id"], "music_layer_0")
            self.assertEqual(tracks[1]["id"], "music_layer_1")
            self.assertNotEqual(tracks[0]["id"], tracks[1]["id"])

        # Verify _apply_audio_track_settings applies linear gain without double multiplication
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._music_audio_tracks.return_value = tracks
        gui._compute_audio_track_volume.return_value = 40.0
        gui._get_audio_track_gain_db.return_value = 0.0

        VideoTranslatorGUI._apply_audio_track_settings(gui, "A2 Music")
        # 40% volume -> gain 0.40
        for t in tracks:
            gui.media_player.set_track_gain.assert_any_call(t["id"], 0.4, False)

    def test_preview_panel_scrub_throttling_and_playback_resume(self):
        """Verify scrub throttling timer and restoring playback state upon release."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        _ = QApplication.instance() or QApplication([])

        gui = MagicMock()
        gui._scrub_pending_pos_ms = None
        gui._scrub_was_playing = False
        gui._scrub_throttle_timer = QTimer()
        gui._scrub_throttle_timer.setSingleShot(True)
        gui._scrub_throttle_timer.setInterval(33)

        def _flush_scrub_seek():
            if gui._scrub_pending_pos_ms is not None:
                pos = gui._scrub_pending_pos_ms
                gui._scrub_pending_pos_ms = None
                gui.set_position(pos, exact=False)

        gui._scrub_throttle_timer.timeout.connect(_flush_scrub_seek)

        def _on_scrub_started():
            gui._is_scrubbing = True
            gui._scrub_was_playing = bool(gui.media_player.is_playing())

        def _on_scrub_seek(pos_ms):
            gui._scrub_pending_pos_ms = pos_ms
            if not gui._scrub_throttle_timer.isActive():
                gui._scrub_throttle_timer.start()

        def _on_scrub_finished(final_pos_ms):
            gui._is_scrubbing = False
            if gui._scrub_throttle_timer.isActive():
                gui._scrub_throttle_timer.stop()
            gui._scrub_pending_pos_ms = None
            gui.set_position(final_pos_ms, exact=True)
            if gui._scrub_was_playing:
                gui._scrub_was_playing = False
                gui.media_player.play()

        gui.media_player.is_playing.return_value = True

        # 1. Scrub started while playing
        _on_scrub_started()
        self.assertTrue(gui._scrub_was_playing)

        # 2. Scrub seek bursts
        _on_scrub_seek(100)
        _on_scrub_seek(200)
        self.assertEqual(gui._scrub_pending_pos_ms, 200)
        self.assertTrue(gui._scrub_throttle_timer.isActive())

        # 3. Scrub finished at 500ms
        _on_scrub_finished(500)
        gui.set_position.assert_called_with(500, exact=True)
        gui.media_player.play.assert_called_once()
        self.assertFalse(gui._scrub_was_playing)

    def test_sync_audio_uses_timeline_coords_after_freeze(self):
        """Verify _sync_audio_to_video maps media time through warps and does not seek backwards after freeze."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "video.mp4"
        backend._video_frozen = False
        backend._player = MagicMock()
        # Video media time is 2500 ms
        backend._player.time_pos = 2.5
        backend._player.pause = False
        backend._native_audio_active = True
        # 1.0s freeze at 2.0s shifts timeline by 1.0s -> media 2.5s corresponds to timeline 3500 ms!
        backend._video_time_warps = [{"time": 2.0, "duration": 1.0}]
        backend._native_audio_engine = MagicMock()
        # Audio is already at timeline 3500 ms
        backend._native_audio_engine.timeline_position_ms.return_value = 3500

        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""

        backend._sync_audio_to_video()

        # Must NOT seek to 2500ms
        backend._native_audio_engine.seek.assert_not_called()

    def test_freeze_unfreeze_never_plays_sidecars_when_native_active(self):
        """Verify freeze and unfreeze do not activate legacy QMediaPlayer sidecars when native audio is active."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "video.mp4"
        backend._video_frozen = False
        backend._position_ms = 0
        backend._native_audio_active = True
        backend._native_audio_engine = MagicMock()

        backend._player = MagicMock()
        backend._original_loaded_path = "orig.wav"
        backend._original_player = MagicMock()
        backend._dubbed_loaded_path = "dub.wav"
        backend._dubbed_player = MagicMock()

        # Freeze frame
        backend.freeze_video_frame(2000)
        backend._dubbed_player.play.assert_not_called()
        backend._original_player.play.assert_not_called()

        # Unfreeze frame
        backend.unfreeze_video_frame(2035)
        backend._dubbed_player.play.assert_not_called()
        backend._original_player.play.assert_not_called()
        backend._native_audio_engine.play.assert_called()

    def test_native_error_fallback_stops_engine(self):
        """Verify that on native engine error, engine is stopped and paused to prevent dual audio."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = True
        backend.log = MagicMock()
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine

        backend._on_native_audio_error("Device unplugged")
        self.assertFalse(backend._native_audio_active)
        mock_engine.pause.assert_called_once()
        mock_engine.stop.assert_called_once()

    def test_apply_audio_fade_updates_native_track_gain(self):
        """Verify _apply_audio_fade updates native track gain during fade window."""
        from ui.utils.media_utils import _apply_audio_fade

        gui = MagicMock()
        gui.media_player.duration.return_value = 10000
        gui.media_player._native_audio_active = True
        gui._is_audio_track_muted.return_value = False

        track = MagicMock(metadata={"_fade_in": 2.0, "_volume": 100.0})
        track.name = "A1 Audio"
        gui.timeline._timeline.tracks = [track]

        # At 1.0s (halfway through 2.0s fade-in), gain should be ~0.50
        _apply_audio_fade(gui, 1000)
        gui.media_player.set_track_gain.assert_called_with("A1 Audio", 0.5, False)

    def test_set_time_warps_propagates_to_native_engine(self):
        """Verify set_time_warps updates backend and forwards warps to native engine."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_engine = MagicMock()
        warps = [{"time": 1.5, "duration": 0.5}]

        backend.set_time_warps(warps)
        self.assertEqual(backend._video_time_warps, warps)
        backend._native_audio_engine.set_warps.assert_called_once_with(warps)

    def test_tracks_snapshot_cached_and_sent_on_sink_ready(self):
        """Verify tracks snapshot is cached if sink is not yet ready, then sent when sink becomes ready."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = False
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine
        backend._video_time_warps = []
        backend.log = MagicMock()

        tracks = [{"id": "t1", "path": "t1.wav"}]
        backend.set_audio_tracks_snapshot(tracks)
        mock_engine.set_tracks.assert_not_called()

        backend._on_native_audio_sink_ready(True)
        self.assertTrue(backend._native_audio_active)
        mock_engine.set_tracks.assert_called_once_with(tracks, [])

    def test_set_source_resets_native_audio_engine(self):
        """Verify setSource stops and resets native audio engine on media change and teardown."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine
        backend._last_tracks_snapshot = ([{"id": "t1"}], [])
        backend._player = MagicMock()
        backend._original_player = MagicMock()
        backend._dubbed_player = MagicMock()
        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""
        backend.stop = MagicMock()
        backend.clear_subtitle = MagicMock()
        backend._apply_blur_filter = MagicMock()
        backend._apply_current_subtitle = MagicMock()

        # Teardown / clear source
        backend.setSource("")
        mock_engine.stop.assert_called_once()
        mock_engine.seek.assert_called_once_with(0)
        mock_engine.set_tracks.assert_called_once_with([])
        self.assertIsNone(backend._last_tracks_snapshot)

        # Load new source
        mock_engine.reset_mock()
        backend._last_tracks_snapshot = ([{"id": "t1"}], [])
        backend._normalize_source = lambda s: "sample.mp4"
        backend.durationChanged = MagicMock()
        backend.setSource("sample.mp4")
        mock_engine.stop.assert_called_once()
        mock_engine.seek.assert_called_once_with(0)
        mock_engine.set_tracks.assert_called_once_with([])
        self.assertIsNone(backend._last_tracks_snapshot)

    def test_on_native_audio_position_changed_emits_position_changed(self):
        """Verify _on_native_audio_position_changed updates position and emits positionChanged."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = True
        backend._video_time_warps = []
        backend.positionChanged = MagicMock()

        backend._on_native_audio_position_changed(1500)
        self.assertEqual(backend._dubbed_position_ms, 1500)
        self.assertEqual(backend._position_ms, 1500)
        backend.positionChanged.emit.assert_called_once_with(1500)

    def test_freeze_timer_tick_uses_native_audio_clock(self):
        """Verify freeze timer uses media_player.timeline_position_ms() when native audio is active."""
        from ui.utils.media_utils import _on_freeze_timer_tick

        gui = MagicMock()
        gui._active_freeze_warp = {"id": "warp_1"}
        gui.timeline._playing = True
        gui.media_player._native_audio_active = True
        gui.media_player.timeline_position_ms.return_value = 2500  # 2.5s
        gui._freeze_anchor_tl_s = 2.0  # anchor at 2.0s
        gui._freeze_duration_s = 1.0  # duration 1.0s (ends at 3.0s)
        gui.video_time_warps = []
        gui.media_player.duration.return_value = 10000

        # At 2.5s, elapsed is 0.5s < 1.0s
        _on_freeze_timer_tick(gui)
        gui.timeline.set_position.assert_called_with(2500)

    def test_terminate_workers_calls_media_player_close(self):
        """Verify _terminate_workers calls media_player.close() to release mpv and timers."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._active_workers = {}
        gui.media_player = MagicMock()

        VideoTranslatorGUI._terminate_workers(gui)
        gui.media_player.close.assert_called_once()

    def test_set_audio_tracks_snapshot_preserves_empty_warps(self):
        """Verify set_audio_tracks_snapshot preserves an explicitly empty warps list."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine
        backend._native_audio_active = True
        backend._video_time_warps = [{"start": 1.0, "end": 2.0, "speed": 0.5}]

        tracks = [{"id": "t1", "path": "test.wav"}]
        backend.set_audio_tracks_snapshot(tracks, warps=[])

        self.assertEqual(backend._last_tracks_snapshot, (tracks, []))
        mock_engine.set_tracks.assert_called_once_with(tracks, [])

    def test_close_stops_sidecars_and_native_engine(self):
        """Verify close() stops sidecar players and releases native engine."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine
        backend._native_audio_active = True
        backend._player = MagicMock()
        backend._original_player = MagicMock()
        backend._dubbed_player = MagicMock()
        backend._poll_timer = MagicMock()
        backend._poll_timer.isActive.return_value = True
        backend._sync_timer = MagicMock()
        backend._sync_timer.isActive.return_value = True

        backend.close()

        backend._poll_timer.stop.assert_called_once()
        backend._sync_timer.stop.assert_called_once()
        backend._original_player.stop.assert_called_once()
        backend._dubbed_player.stop.assert_called_once()
        mock_engine.close.assert_called_once()
        backend._player.terminate.assert_called_once()

    def test_real_thread_set_tracks_unloads_readers(self):
        """Verify PreviewAudioEngine unloads readers in real background QThread when setting empty tracks."""
        import tempfile
        import time
        import numpy as np
        import soundfile as sf
        from PySide6.QtWidgets import QApplication
        from ui.utils.preview_audio import PreviewAudioEngine

        app = QApplication.instance() or QApplication(sys.argv)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        wav_path = tmp.name

        try:
            sf.write(wav_path, np.zeros(1600, dtype=np.float32), 16000)
            engine = PreviewAudioEngine()

            for _ in range(30):
                app.processEvents()
                time.sleep(0.01)

            engine.set_tracks([{"id": "t1", "path": wav_path, "start": 0.0, "end": 1.0}])
            for _ in range(30):
                app.processEvents()
                time.sleep(0.01)
                if len(engine._worker._readers) == 1:
                    break
            self.assertEqual(len(engine._worker._readers), 1)

            # Clear tracks: should close AudioReader in real background thread
            engine.set_tracks([])
            for _ in range(30):
                app.processEvents()
                time.sleep(0.01)
                if len(engine._worker._readers) == 0:
                    break
            self.assertEqual(len(engine._worker._readers), 0)

            engine.close()
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    def test_resolve_preview_mixed_audio_path_bypasses_wav_when_native_active(self):
        """Verify _resolve_preview_mixed_audio_path returns empty string and does not invoke mix_audio_tracks."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui.using_existing_audio_source.return_value = False
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._native_audio_enabled = True

        with patch("app.audio_mixer.mix_audio_tracks") as mock_mixer:
            path = VideoTranslatorGUI._resolve_preview_mixed_audio_path(gui)
            self.assertEqual(path, "")
            mock_mixer.assert_not_called()

    def test_resolve_timeline_audio_visualization_path_native_active(self):
        """Verify resolve_timeline_audio_visualization_path uses voice_only directly without dubbed mix resolution."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui.using_existing_audio_source.return_value = False
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._native_audio_enabled = True
        gui._resolve_preview_voice_only_audio_path.return_value = "C:/fake/voice.wav"

        with patch("os.path.exists", return_value=True), \
             patch.object(VideoTranslatorGUI, "_resolve_preview_dubbed_playback_source") as mock_dubbed:
            path = VideoTranslatorGUI.resolve_timeline_audio_visualization_path(gui)
            self.assertEqual(path, "C:/fake/voice.wav")
            mock_dubbed.assert_not_called()

    def test_is_active_timeline_audio_track_muted_native_active(self):
        """Verify _is_active_timeline_audio_track_muted returns a2_muted directly without invoking dubbed mix resolution."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._preview_audio_track_mode = "dubbed"
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._native_audio_enabled = True
        gui._timeline_audio_track_mutes.return_value = (False, True)

        with patch.object(VideoTranslatorGUI, "_resolve_preview_dubbed_playback_source") as mock_dubbed:
            is_muted = VideoTranslatorGUI._is_active_timeline_audio_track_muted(gui)
            self.assertTrue(is_muted)
            mock_dubbed.assert_not_called()

    def test_native_preview_audio_mode_original_vs_dubbed_snapshot_mutes(self):
        """Verify original vs dubbed audio mode correctly sets mute flags in native tracks snapshot."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._preview_audio_track_switching = False
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._resolve_preview_original_video_path.return_value = "video.mp4"
        gui._resolve_preview_original_audio_path.return_value = "orig.wav"
        gui._resolve_preview_voice_only_audio_path.return_value = "voice.wav"
        gui._compute_audio_track_volume.return_value = 100.0
        gui._get_audio_track_gain_db.return_value = 0.0
        gui._audio_total_duration_ms.return_value = 10000
        gui._is_audio_track_muted.return_value = False
        gui.video_time_warps = []
        music_tracks = [{"id": "m1", "path": "music.mp3", "muted": False, "volume": 100.0}]
        gui._music_audio_tracks.return_value = music_tracks

        with patch("os.path.exists", return_value=True):
            # 1. When mode is "original", TS1 and Music should be muted in snapshot
            gui._preferred_preview_audio_track_mode.return_value = "original"
            gui._preview_audio_track_mode = "original"
            VideoTranslatorGUI._apply_preview_audio_track_selection(gui)

            snapshot, _warps = gui.media_player.set_audio_tracks_snapshot.call_args[0]
            track_by_id = {t["id"]: t for t in snapshot}
            self.assertFalse(track_by_id["A1 Audio"]["muted"])
            self.assertTrue(track_by_id["TS1"]["muted"])
            self.assertTrue(track_by_id["m1"]["muted"])

            # 2. When mode is "dubbed", TS1 and Music should follow their own unmuted state
            gui.media_player.set_audio_tracks_snapshot.reset_mock()
            gui._preferred_preview_audio_track_mode.return_value = "dubbed"
            gui._preview_audio_track_mode = "dubbed"
            VideoTranslatorGUI._apply_preview_audio_track_selection(gui)

            snapshot, _warps = gui.media_player.set_audio_tracks_snapshot.call_args[0]
            track_by_id = {t["id"]: t for t in snapshot}
            self.assertFalse(track_by_id["A1 Audio"]["muted"])
            self.assertFalse(track_by_id["TS1"]["muted"])
            self.assertFalse(track_by_id["m1"]["muted"])

    def test_poll_state_guards_transient_none_and_seeking(self):
        """Verify _poll_state does not emit 0 when time-pos is None or seeking."""
        from ui.utils.media_backend import MpvMediaPlayerBackend
        from PySide6.QtMultimedia import QMediaPlayer

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "video.mp4"
        backend._video_frozen = False
        backend._native_audio_active = False
        backend._state = QMediaPlayer.PausedState
        backend._position_ms = 5000
        backend._duration_ms = 10000
        backend.positionChanged = MagicMock()
        backend.durationChanged = MagicMock()
        backend.stateChanged = MagicMock()

        # 1. When time_pos is None (mpv seeking), positionChanged should NOT be emitted with 0
        backend._read_property = lambda name, fallback=None, default=None: {
            "time-pos": None,
            "duration": 10.0,
            "pause": True,
            "eof-reached": False,
            "core-idle": False,
            "seeking": False,
        }.get(name, default)

        backend._poll_state()
        backend.positionChanged.emit.assert_not_called()
        self.assertEqual(backend._position_ms, 5000)

        # 2. When seeking is True, positionChanged should NOT be emitted
        backend._read_property = lambda name, fallback=None, default=None: {
            "time-pos": 0.0,
            "duration": 10.0,
            "pause": True,
            "eof-reached": False,
            "core-idle": False,
            "seeking": True,
        }.get(name, default)

        backend._poll_state()
        backend.positionChanged.emit.assert_not_called()
        self.assertEqual(backend._position_ms, 5000)

    def test_sync_audio_to_video_guards_none_time_pos(self):
        """Verify _sync_audio_to_video returns early when mpv time_pos is None."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "video.mp4"
        backend._video_frozen = False
        backend._last_seek_mono = 0.0
        backend._player = MagicMock()
        backend._player.time_pos = None  # transient seek state
        backend._player.pause = False
        backend._native_audio_active = True
        backend._native_audio_engine = MagicMock()

        backend._sync_audio_to_video()
        # Native audio engine seek should NOT be called with 0
        backend._native_audio_engine.seek.assert_not_called()

    def test_position_changed_suppressed_during_scrub(self):
        """Verify position_changed in media_utils ignores updates while scrubbing."""
        from ui.utils.media_utils import position_changed

        gui = MagicMock()
        gui._is_scrubbing = True
        gui.timeline = MagicMock()
        gui.media_player = MagicMock()

        position_changed(gui, 0)
        gui.timeline.set_position.assert_not_called()

    def test_timeline_set_position_suppressed_during_scrub_drag(self):
        """Verify EditorTimeline.set_position does not overwrite playhead while dragging."""
        from ui.views.editor.timeline import EditorTimeline

        timeline = EditorTimeline.__new__(EditorTimeline)
        timeline._selection_drag = {"mode": "scrub"}
        timeline._playhead = 5.0
        timeline.set_playhead = MagicMock()

        timeline.set_position(0)
        timeline.set_playhead.assert_not_called()
        self.assertEqual(timeline._playhead, 5.0)

    def test_playback_highlight_no_jump_between_segments(self):
        """Verify update_playback_subtitle_highlight does not select segment 0 when in gap."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._preview_is_playing.return_value = True
        gui.live_preview_segments = []
        gui.get_active_segments.return_value = []
        gui._find_active_segment_index.return_value = -1
        gui._subtitle_track_preview_visible = True
        gui._is_subtitle_inspector_details_visible.return_value = False
        gui.timeline = MagicMock()
        gui.timeline._timeline = MagicMock()
        track = MagicMock()
        track.type.value = "subtitle"
        track.layers = [MagicMock(id="layer_seg_0")]
        gui.timeline._timeline.tracks = [track]
        gui.timeline._selected_layer_id = ""
        gui.timeline._segment_indices = {"layer_seg_0": 0}

        VideoTranslatorGUI.update_playback_subtitle_highlight(gui, 3500)
        # on_timeline_layer_selected should NOT have been called with layer_seg_0
        gui.on_timeline_layer_selected.assert_not_called()

    def test_volume_changed_does_not_schedule_refresh_in_native_mode(self):
        """Verify volume changes do not schedule sidecar refresh when native audio is active."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui.audio_a2_volume_label = MagicMock()
        gui.audio_music_volume_label = MagicMock()
        gui._music_audio_tracks.return_value = [{"id": "m1"}]

        VideoTranslatorGUI.on_audio_a2_volume_changed(gui, 75)
        gui._schedule_preview_audio_refresh.assert_not_called()

        VideoTranslatorGUI.on_audio_music_volume_changed(gui, 50)
        gui._schedule_preview_audio_refresh.assert_not_called()

    def test_mpv_backend_slow_motion_speed_control(self):
        """Verify MpvMediaPlayerBackend dynamically adjusts mpv speed during slow motion."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "mock_video.mp4"
        backend._position_ms = 0
        backend._video_frozen = False
        backend._base_playback_rate = 1.0
        backend._current_applied_speed = 1.0
        mock_player = MagicMock()
        backend._player = mock_player

        warps = [
            {
                "id": "warp_slow_1",
                "type": "slow",
                "time": 3.0,
                "duration": 1.0,
                "speed": 0.8,
                "media_start": 1.0,
                "media_end": 3.0,
            }
        ]
        backend.set_time_warps(warps)

        # At media time 0.5s (before slow): speed should be 1.0
        self.assertEqual(backend._get_warp_speed_for_media_time(0.5), 1.0)
        backend._update_video_speed_for_time_warps(0.5)
        self.assertEqual(backend._current_applied_speed, 1.0)

        # At media time 2.0s (inside slow): speed should be 0.8
        self.assertEqual(backend._get_warp_speed_for_media_time(2.0), 0.8)
        backend._update_video_speed_for_time_warps(2.0)
        self.assertEqual(backend._current_applied_speed, 0.8)
        self.assertEqual(mock_player.speed, 0.8)

        # At media time 3.5s (after slow): speed should restore to 1.0
        self.assertEqual(backend._get_warp_speed_for_media_time(3.5), 1.0)
        backend._update_video_speed_for_time_warps(3.5)
        self.assertEqual(backend._current_applied_speed, 1.0)
        self.assertEqual(mock_player.speed, 1.0)

    def test_preview_audio_slow_motion_not_frozen_and_resamples(self):
        """Verify _PreviewAudioWorker does not treat slow motion as freeze and resamples original audio."""
        import numpy as np
        from ui.utils.preview_audio import _PreviewAudioWorker

        worker = _PreviewAudioWorker.__new__(_PreviewAudioWorker)
        worker.internal_sr = 16000
        worker.block_size = 160
        worker._tracks = []
        worker._readers = {}
        worker._pcm_cache = MagicMock()
        worker._pcm_cache.get.return_value = None
        worker._generation_id = 1

        warps = [
            {
                "id": "warp_slow_1",
                "type": "slow",
                "time": 3.0,
                "duration": 1.0,
                "speed": 0.8,
                "media_start": 1.0,
                "media_end": 3.0,
            }
        ]
        worker._warps = warps

        # Check _is_time_frozen: must be False for slow warps
        self.assertFalse(worker._is_time_frozen(2.0))
        self.assertFalse(worker._is_time_frozen(3.2))

        # Mock reader
        mock_reader = MagicMock()
        # When read is called with 128 samples, return sequential ramp
        mock_reader.read.side_effect = lambda start, count: np.linspace(0.1, 0.9, count, dtype=np.float32)
        worker._readers["video_audio.wav"] = mock_reader

        worker._tracks = [
            {
                "id": "orig_v1",
                "path": "video_audio.wav",
                "is_original_video": True,
                "start_ms": 0,
                "end_ms": 10000,
                "source_start_ms": 0,
                "current_gain": 1.0,
                "target_gain": 1.0,
            }
        ]

        # Read mixed block at 2.0s timeline time: (2.0 * 16000 = 32000 samples)
        # 2.0s is inside the slow warp. TimeWarpService maps timeline to media time with speed 0.8.
        block = worker._read_mixed_block(32000)
        self.assertEqual(len(block), 160)
        # Verify reader was called with count < 160 (stretched by factor 0.8 -> ~128 samples)
        read_args = mock_reader.read.call_args[0]
        self.assertEqual(read_args[1], 128)
        # Block output should have 160 float32 samples with no NaNs
        self.assertFalse(np.isnan(block).any())

    def test_quick_preview_worker_applies_timewarp_in_voice_mode(self):
        """Verify QuickPreviewWorker slices warps, applies timewarp to base clip, and muxes audio."""
        from ui.worker_adapters.preview_workers import QuickPreviewWorker

        warps = [
            {
                "type": "slow",
                "media_start": 2.0,
                "media_end": 4.0,
                "speed": 0.5,
                "time": 2.0,
                "duration": 4.0,
            }
        ]

        worker = QuickPreviewWorker(
            video_path="input_vid.mp4",
            output_path="out_preview.mp4",
            mode="voice",
            start_seconds=2.0,
            duration_seconds=5.0,
            audio_path="timeline_audio.wav",
            video_time_warps=warps,
        )

        with patch("preview_processor.trim_video_clip") as mock_trim, \
             patch("preview_processor.apply_timewarp_to_video_clip") as mock_timewarp, \
             patch("preview_processor.mux_audio_into_video_clip_for_preview") as mock_mux, \
             patch("os.path.exists", return_value=True), \
             patch("shutil.copyfile") as mock_copy:

            worker.run()

            # Window [2.0, 7.0] on timeline:
            # Slow warp is at timeline [2.0, 8.0] at speed 0.5.
            # 5.0s window on timeline running at 0.5x speed consumes 5.0 * 0.5 = 2.5s of media.
            # So media_start = 2.0, media_dur = 2.5 (from 2.0 to 4.5).
            mock_trim.assert_called_once()
            trim_args = mock_trim.call_args[0]
            self.assertEqual(trim_args[0], "input_vid.mp4")
            self.assertAlmostEqual(trim_args[2], 2.0)
            self.assertAlmostEqual(trim_args[3], 2.5)

            mock_timewarp.assert_called_once()
            tw_args = mock_timewarp.call_args[0]
            tw_kwargs = mock_timewarp.call_args[1]
            self.assertAlmostEqual(tw_args[3], 2.5)
            self.assertFalse(tw_kwargs.get("include_audio"))
            # Warp in clip coords should start at 0.0
            clip_warps = tw_args[2]
            self.assertEqual(len(clip_warps), 1)
            self.assertAlmostEqual(clip_warps[0]["media_start"], 0.0)
            self.assertAlmostEqual(clip_warps[0]["media_end"], 2.0)

            # mux_audio_into_video_clip_for_preview should receive warped_clip and video_is_pretrimmed=True
            mock_mux.assert_called_once()
            mux_args = mock_mux.call_args[0]
            mux_kwargs = mock_mux.call_args[1]
            self.assertTrue(mux_kwargs.get("video_is_pretrimmed"))
            self.assertEqual(mux_args[1], "timeline_audio.wav")
            self.assertEqual(mux_args[3], 2.0)
            self.assertEqual(mux_args[4], 5.0)

    def test_quick_preview_worker_applies_timewarp_in_subtitle_mode(self):
        """Verify QuickPreviewWorker warps original audio (include_audio=True) in subtitle mode."""
        from ui.worker_adapters.preview_workers import QuickPreviewWorker

        warps = [{"type": "freeze", "time": 1.0, "duration": 2.0}]

        worker = QuickPreviewWorker(
            video_path="input_vid.mp4",
            output_path="out_preview.mp4",
            mode="subtitle",
            start_seconds=0.5,
            duration_seconds=5.0,
            video_time_warps=warps,
        )

        with patch("preview_processor.trim_video_clip") as mock_trim, \
             patch("preview_processor.apply_timewarp_to_video_clip") as mock_timewarp, \
             patch("os.path.exists", return_value=False), \
             patch("shutil.copyfile") as mock_copy:

            worker.run()

            mock_timewarp.assert_called_once()
            tw_kwargs = mock_timewarp.call_args[1]
            self.assertTrue(tw_kwargs.get("include_audio"))
            mock_copy.assert_called_once()

    def test_find_active_segment_indices_boundary_and_no_stale_stacking(self):
        """Verify _find_active_segment_indices transitions immediately on cue boundaries without multi-segment stacking."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._playback_subtitle_activity_cache = {}
        gui._find_active_segment_indices = lambda pos, segs: VideoTranslatorGUI._find_active_segment_indices(gui, pos, segs)

        segments = [
            {"start": 0.0, "end": 2.0, "text": "Segment 0"},
            {"start": 2.0, "end": 4.0, "text": "Segment 1"},
            {"start": 4.5, "end": 6.0, "text": "Segment 2"},
        ]

        # 1. Inside segment 0
        res_0 = VideoTranslatorGUI._find_active_segment_indices(gui, 1000, segments)
        self.assertEqual(res_0, [0])
        self.assertEqual(VideoTranslatorGUI._find_active_segment_index(gui, 1000, segments), 0)

        # 2. Exactly at boundary 2.0s: segment 1 must be active, segment 0 must NOT be active
        res_boundary = VideoTranslatorGUI._find_active_segment_indices(gui, 2000, segments)
        self.assertEqual(res_boundary, [1])
        self.assertEqual(VideoTranslatorGUI._find_active_segment_index(gui, 2000, segments), 1)

        # 3. Inside segment 1 (e.g. at 2.5s) via cache: must return ONLY segment 1
        res_1 = VideoTranslatorGUI._find_active_segment_indices(gui, 2500, segments)
        self.assertEqual(res_1, [1])
        self.assertEqual(VideoTranslatorGUI._find_active_segment_index(gui, 2500, segments), 1)

        # 4. In gap between segment 1 and segment 2 (e.g. 4.2s): must return empty
        res_gap = VideoTranslatorGUI._find_active_segment_indices(gui, 4200, segments)
        self.assertEqual(res_gap, [])
        self.assertEqual(VideoTranslatorGUI._find_active_segment_index(gui, 4200, segments), -1)

        # 5. Exactly at boundary 4.5s: segment 2 is active
        res_2 = VideoTranslatorGUI._find_active_segment_indices(gui, 4500, segments)
        self.assertEqual(res_2, [2])
        self.assertEqual(VideoTranslatorGUI._find_active_segment_index(gui, 4500, segments), 2)


if __name__ == "__main__":
    unittest.main()
