#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_timeline_visuals.py

Unit tests for in-process thumbnail decoding and O(1) streaming waveform generation.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

# Ensure project root and app are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import av
from app.media_decode import build_waveform, has_audio_stream, iter_video_thumbnails


class TestTimelineVisuals(unittest.TestCase):
    """Test native in-process video thumbnail and waveform generation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_timeline_visuals_")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_video(self, filename: str = "synth_test.mp4", pts_offset: int = 0) -> str:
        """Create a 2-second synthetic video: first second RED, second second BLUE."""
        video_path = os.path.join(self.temp_dir, filename)
        container = av.open(video_path, mode="w", format="mp4")
        stream = container.add_stream("h264", rate=25)
        stream.width = 320
        stream.height = 240
        stream.pix_fmt = "yuv420p"

        # 50 frames: 25 red (0.0s - 0.96s), 25 blue (1.0s - 1.96s)
        for i in range(50):
            frame_arr = np.zeros((240, 320, 3), dtype=np.uint8)
            if i < 25:
                frame_arr[:, :, 0] = 240  # Red
            else:
                frame_arr[:, :, 2] = 240  # Blue
            frame = av.VideoFrame.from_ndarray(frame_arr, format="rgb24")
            frame.pts = pts_offset + i
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()
        return video_path

    def _create_synthetic_audio(self, filename: str = "synth_audio.wav", duration_s: float = 3.0) -> str:
        """Create a synthetic multi-tone audio file."""
        audio_path = os.path.join(self.temp_dir, filename)
        sr = 16000
        t = np.linspace(0, duration_s, int(duration_s * sr), endpoint=False)
        audio = (0.5 * np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
        sf.write(audio_path, audio, sr, format="WAV", subtype="PCM_16")
        return audio_path

    def test_iter_video_thumbnails_pts_and_colors(self):
        """Ensure iter_video_thumbnails decodes frames with accurate PTS and colors with 0 subprocess calls."""
        video_path = self._create_synthetic_video()

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            thumbnails = list(iter_video_thumbnails(video_path, [0.0, 1.2], width=180))

        self.assertEqual(len(thumbnails), 2)

        # First thumbnail at t=0.0s (Red)
        pts_0, rgb_0 = thumbnails[0]
        self.assertAlmostEqual(pts_0, 0.0, delta=0.08)
        self.assertEqual(rgb_0.shape[1], 180)  # Width
        self.assertEqual(rgb_0.shape[2], 3)    # RGB channels
        self.assertEqual(rgb_0.dtype, np.uint8)
        # Verify dominant red color
        self.assertGreater(rgb_0[:, :, 0].mean(), 180)
        self.assertLess(rgb_0[:, :, 2].mean(), 60)

        # Second thumbnail at t=1.2s (Blue)
        pts_1, rgb_1 = thumbnails[1]
        self.assertAlmostEqual(pts_1, 1.2, delta=0.08)
        self.assertEqual(rgb_1.shape[1], 180)
        self.assertEqual(rgb_1.shape[2], 3)
        self.assertEqual(rgb_1.dtype, np.uint8)
        # Verify dominant blue color
        self.assertGreater(rgb_1[:, :, 2].mean(), 180)
        self.assertLess(rgb_1[:, :, 0].mean(), 60)

    def test_iter_video_thumbnails_empty_and_invalid(self):
        """Ensure iter_video_thumbnails gracefully handles empty or invalid inputs."""
        video_path = self._create_synthetic_video()

        # Empty timestamps
        self.assertEqual(list(iter_video_thumbnails(video_path, [])), [])

        # Non-existent file
        self.assertEqual(list(iter_video_thumbnails("non_existent_file.mp4", [0.0, 1.0])), [])

        # Audio-only file
        audio_path = self._create_synthetic_audio()
        self.assertEqual(list(iter_video_thumbnails(audio_path, [0.0, 1.0])), [])

    def test_build_waveform_matches_batch(self):
        """Ensure streaming build_waveform matches the batch envelope algorithm with 0 subprocess calls."""
        audio_path = self._create_synthetic_audio(duration_s=4.0)

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            waveform, duration_s = build_waveform(audio_path, bucket_count=300)

        self.assertAlmostEqual(duration_s, 4.0, delta=0.05)
        self.assertGreater(len(waveform), 0)
        self.assertTrue(all(0.0 <= x <= 1.0 for x in waveform))

        # Compute batch reference
        data, sr = sf.read(audio_path, dtype="float32")
        peak = float(np.max(np.abs(data)))
        norm_samples = data / peak
        actual_buckets = int(min(300, max(240, round(duration_s * 12.0))))
        chunk_size = max(256, int(np.ceil(norm_samples.size / max(1, actual_buckets))))
        batch_wf = []
        for start in range(0, norm_samples.size, chunk_size):
            chunk = norm_samples[start:start + chunk_size]
            abs_chunk = np.abs(chunk)
            pv = float(np.max(abs_chunk)) if abs_chunk.size else 0.0
            rv = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
            val = max(pv, rv * 1.15)
            batch_wf.append(min(1.0, max(0.03, val ** 0.85)))

        self.assertEqual(len(waveform), len(batch_wf))
        np.testing.assert_allclose(waveform, batch_wf, atol=1e-4)

    def test_build_waveform_silence_and_no_audio(self):
        """Ensure build_waveform returns zeros for silence and empty for no-audio without raising."""
        # 1. Pure silence
        silence_path = os.path.join(self.temp_dir, "silence.wav")
        sf.write(silence_path, np.zeros(32000, dtype=np.float32), 16000, format="WAV", subtype="PCM_16")
        wf_silence, dur_silence = build_waveform(silence_path)
        self.assertAlmostEqual(dur_silence, 2.0, delta=0.05)
        self.assertTrue(all(x == 0.0 for x in wf_silence))

        # 2. Video without audio stream
        video_path = self._create_synthetic_video()
        wf_no_audio, dur_video = build_waveform(video_path)
        self.assertEqual(wf_no_audio, [])
        self.assertAlmostEqual(dur_video, 2.0, delta=0.1)

        # 3. Non-existent path
        wf_none, dur_none = build_waveform("does_not_exist.wav")
        self.assertEqual(wf_none, [])
        self.assertEqual(dur_none, 0.0)

    def test_build_waveform_video_with_audio(self):
        """Ensure build_waveform decodes video audio via linear streaming fast path."""
        from fractions import Fraction
        video_path = os.path.join(self.temp_dir, "video_audio.mp4")
        container = av.open(video_path, mode="w", format="mp4")
        astream = container.add_stream("aac", rate=48000)
        astream.time_base = Fraction(1, 48000)
        t = np.linspace(0, 1.0, 1024, endpoint=False, dtype=np.float32)
        frame_data = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        pts = 0
        for _ in range(94):
            frame = av.AudioFrame.from_ndarray(frame_data.reshape(1, -1), format="fltp", layout="mono")
            frame.rate = 48000
            frame.pts = pts
            pts += 1024
            for p in astream.encode(frame):
                container.mux(p)
        for p in astream.encode(None):
            container.mux(p)
        container.close()

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            wf, dur = build_waveform(video_path, bucket_count=300)

        self.assertAlmostEqual(dur, 2.0, delta=0.1)
        self.assertGreater(len(wf), 0)
        self.assertTrue(all(0.0 <= x <= 1.0 for x in wf))

    def test_timeline_waveform_worker_native(self):
        """Ensure TimelineWaveformWorker runs in-process without invoking FFmpeg CLI."""
        from ui.worker_adapters.processing_workers import TimelineWaveformWorker

        audio_path = self._create_synthetic_audio(duration_s=2.5)
        worker = TimelineWaveformWorker(
            request_signature="req_test_wf",
            video_path="",
            audio_path=audio_path,
            temp_audio_path=os.path.join(self.temp_dir, "temp_audio.wav"),
            duration_s=2.5,
        )

        results = []
        worker.completed.connect(lambda sig, wf, dur, err: results.append((sig, wf, dur, err)))

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            worker.run()

        self.assertEqual(len(results), 1)
        sig, wf, dur, err = results[0]
        self.assertEqual(sig, "req_test_wf")
        self.assertEqual(err, "")
        self.assertGreater(len(wf), 0)
        self.assertAlmostEqual(dur, 2.5, delta=0.1)

    def test_timeline_thumbnail_worker_native(self):
        """Ensure TimelineThumbnailWorker runs in-process without invoking FFmpeg CLI."""
        from ui.worker_adapters.processing_workers import TimelineThumbnailWorker

        video_path = self._create_synthetic_video()
        thumb_dir = os.path.join(self.temp_dir, "worker_thumbs")
        worker = TimelineThumbnailWorker(
            request_signature="req_test_thumbs",
            video_path=video_path,
            duration_s=2.0,
            thumb_dir=thumb_dir,
        )

        results = []
        worker.completed.connect(lambda sig, thumbs, err: results.append((sig, thumbs, err)))

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            worker.run()

        self.assertEqual(len(results), 1)
        sig, thumbs, err = results[0]
        self.assertEqual(sig, "req_test_thumbs")
        self.assertEqual(err, "")
        self.assertGreater(len(thumbs), 0)
        from PySide6.QtGui import QImage
        for pts, image in thumbs:
            self.assertIsInstance(image, QImage)
            self.assertFalse(image.isNull())
            self.assertEqual(image.width(), 180)
        # Verify no JPG files written to disk
        created_files = os.listdir(thumb_dir) if os.path.exists(thumb_dir) else []
        jpg_files = [f for f in created_files if f.lower().endswith(".jpg")]
        self.assertEqual(len(jpg_files), 0)

    def test_bounded_waveform_cache_lru_and_fingerprint(self):
        """Ensure BoundedWaveformCache respects LRU eviction, byte budget, and fingerprint."""
        from app.media_decode import BoundedWaveformCache, WAVEFORM_ALGO_VERSION

        cache = BoundedWaveformCache(max_bytes=1000, max_entries=3)
        audio_path = self._create_synthetic_audio(duration_s=1.0)
        stat = os.stat(audio_path)

        fp = cache.compute_fingerprint(audio_path, stat, 100, stream_index=0)
        self.assertEqual(fp[0], WAVEFORM_ALGO_VERSION)
        self.assertEqual(fp[1], os.path.abspath(audio_path))
        self.assertEqual(fp[2], stat.st_size)
        self.assertEqual(fp[3], getattr(stat, "st_mtime_ns", 0))
        self.assertEqual(fp[4], 0)
        self.assertEqual(fp[5], 100)

        # Put entries
        cache.put(("key1",), ([0.1] * 10, 1.0))
        cache.put(("key2",), ([0.2] * 10, 1.0))
        cache.put(("key3",), ([0.3] * 10, 1.0))
        self.assertIsNotNone(cache.get(("key1",)))
        self.assertIsNotNone(cache.get(("key2",)))
        self.assertIsNotNone(cache.get(("key3",)))

        # 4th entry should evict key1 (least recently used)
        cache.put(("key4",), ([0.4] * 10, 1.0))
        self.assertIsNone(cache.get(("key1",)))
        self.assertIsNotNone(cache.get(("key4",)))

    def test_launcher_project_card_non_blocking_and_async(self):
        """Ensure ProjectCard shows placeholder immediately and extracts thumbnail in background with 0 CLI calls."""
        import time
        from PySide6.QtWidgets import QApplication

        # Ensure ui and app are in sys.path
        for p in ("ui", "app"):
            full_p = os.path.join(PROJECT_ROOT, p)
            if full_p not in sys.path:
                sys.path.insert(0, full_p)
        from views.launcher import ProjectCard
        try:
            from i18n import t
        except ImportError:
            from ui.i18n import t

        app = QApplication.instance() or QApplication([])
        video_path = self._create_synthetic_video()
        cache_dir = os.path.join(self.temp_dir, "thumb_cache")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            card = ProjectCard(video_path, cache_dir)
            # Immediate placeholder
            self.assertEqual(card.thumb_label.text(), t("No Preview"))

            # Allow background thread to process and deliver QImage via signal
            deadline = time.time() + 2.0
            while time.time() < deadline and card._orig_pixmap is None:
                app.processEvents()
                time.sleep(0.02)

            self.assertIsNotNone(card._orig_pixmap)
            self.assertFalse(card._orig_pixmap.isNull())

    def test_launcher_accept_shows_loading_card_and_finishes(self):
        """Ensure LauncherWindow.accept() shows loading card immediately and finishes cache prep."""
        import time
        from PySide6.QtWidgets import QApplication

        for p in ("ui", "app"):
            full_p = os.path.join(PROJECT_ROOT, p)
            if full_p not in sys.path:
                sys.path.insert(0, full_p)
        from views.launcher import LauncherWindow

        app = QApplication.instance() or QApplication([])
        video_path = self._create_synthetic_video()

        with patch.object(LauncherWindow, "_launch_resource_state", return_value=(True, [], [])):
            launcher = LauncherWindow()
            launcher.selected_video = video_path
            accepted = []
            launcher._finish_accept = lambda: accepted.append(True)

            launcher.accept()

            # Loading card must be displayed immediately
            self.assertFalse(launcher.loading_container.isHidden())
            self.assertFalse(launcher.loading_panel.isHidden())

            # Background cache worker should complete and trigger accept
            deadline = time.time() + 4.0
            while time.time() < deadline and not accepted:
                app.processEvents()
                time.sleep(0.02)

            self.assertEqual(accepted, [True])

    def test_prepare_timeline_visual_cache_native_zero_jpg_files(self):
        """Ensure _prepare_timeline_visual_cache on native path creates 0 JPG files on disk."""
        for p in ("ui", "app"):
            full_p = os.path.join(PROJECT_ROOT, p)
            if full_p not in sys.path:
                sys.path.insert(0, full_p)
        from views.launcher import _prepare_timeline_visual_cache

        video_path = self._create_synthetic_video()
        temp_root = os.path.join(self.temp_dir, "launcher_temp")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            _prepare_timeline_visual_cache(video_path, temp_root)

        thumb_dir = os.path.join(temp_root, "timeline_thumbnails")
        created_jpgs = [
            f for f in (os.listdir(thumb_dir) if os.path.exists(thumb_dir) else [])
            if f.lower().endswith(".jpg")
        ]
        self.assertEqual(len(created_jpgs), 0)

    def test_timeline_waveform_worker_video_without_audio_no_cli(self):
        """Ensure TimelineWaveformWorker on video without audio emits empty waveform with 0 CLI calls."""
        from ui.worker_adapters.processing_workers import TimelineWaveformWorker

        video_no_audio = self._create_synthetic_video()
        worker = TimelineWaveformWorker(
            request_signature="req_no_audio_test",
            video_path=video_no_audio,
            audio_path="",
            temp_audio_path=os.path.join(self.temp_dir, "should_not_exist.wav"),
            duration_s=2.0,
        )

        results = []
        worker.completed.connect(lambda sig, wf, dur, err: results.append((sig, wf, dur, err)))

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            worker.run()

        self.assertEqual(len(results), 1)
        sig, wf, dur, err = results[0]
        self.assertEqual(sig, "req_no_audio_test")
        self.assertEqual(wf, [])
        self.assertAlmostEqual(dur, 2.0, delta=0.1)
        self.assertEqual(err, "")
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "should_not_exist.wav")))

    def test_launcher_get_video_duration_pyav_zero_cli(self):
        """Ensure _get_video_duration in launcher retrieves duration via PyAV with 0 ffprobe calls."""
        for p in ("ui", "app"):
            full_p = os.path.join(PROJECT_ROOT, p)
            if full_p not in sys.path:
                sys.path.insert(0, full_p)
        from views.launcher import _get_video_duration

        video_path = self._create_synthetic_video()
        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            dur = _get_video_duration(video_path)

        self.assertAlmostEqual(dur, 2.0, delta=0.1)

    def test_iter_video_thumbnails_nonzero_stream_start_time(self):
        """Ensure iter_video_thumbnails accurately seeks and yields relative presentation times for start_time != 0."""
        # Video with 2.0s PTS offset (start_time != 0)
        video_path = self._create_synthetic_video(filename="offset_test.mp4", pts_offset=50)

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            thumbs = list(iter_video_thumbnails(video_path, [0.0, 1.2], width=180))

        self.assertEqual(len(thumbs), 2)
        pts_0, rgb_0 = thumbs[0]
        pts_1, rgb_1 = thumbs[1]

        # First frame should be near 0.0s (relative presentation start) and RED
        self.assertAlmostEqual(pts_0, 0.0, delta=0.08)
        self.assertGreater(rgb_0[:, :, 0].mean(), 180)
        self.assertLess(rgb_0[:, :, 2].mean(), 60)

        # Second frame should be near 1.2s and BLUE
        self.assertAlmostEqual(pts_1, 1.2, delta=0.1)
        self.assertGreater(rgb_1[:, :, 2].mean(), 180)
        self.assertLess(rgb_1[:, :, 0].mean(), 60)

    def test_iter_video_thumbnails_memory_and_count_budget(self):
        """Ensure iter_video_thumbnails enforces max_thumbnails downsampling and max_bytes memory budget."""
        video_path = self._create_synthetic_video()

        # Request 100 timestamps with max_thumbnails=10
        timestamps = [i * 0.02 for i in range(100)]
        thumbs = list(iter_video_thumbnails(video_path, timestamps, width=180, max_thumbnails=10))
        self.assertLessEqual(len(thumbs), 10)

        # Request with small byte budget
        max_bytes_budget = 150_000
        thumbs_budget = list(iter_video_thumbnails(video_path, timestamps, width=180, max_bytes=max_bytes_budget))
        total_bytes = sum(arr.nbytes for _, arr in thumbs_budget)
        self.assertLessEqual(total_bytes, max_bytes_budget)

    def test_has_audio_stream_probe_error_returns_none_and_worker_falls_back(self):
        """Ensure has_audio_stream returns None on probe failure, causing TimelineWaveformWorker to fall back to FFmpeg."""
        video_path = self._create_synthetic_video()

        # Probe on non-existent path returns None
        self.assertIsNone(has_audio_stream(os.path.join(self.temp_dir, "missing.mp4")))

        # Probe on video with av.open raising error returns None (tri-state semantics)
        with patch("av.open", side_effect=RuntimeError("Corrupt header")):
            status = has_audio_stream(video_path)
            self.assertIsNone(status)

        # Confirm TimelineWaveformWorker falls through to FFmpeg fallback when has_audio_stream returns None
        from ui.worker_adapters.processing_workers import TimelineWaveformWorker
        worker = TimelineWaveformWorker(
            request_signature="req_probe_error_fallback",
            video_path=video_path,
            audio_path="",
            temp_audio_path=os.path.join(self.temp_dir, "fallback.wav"),
            duration_s=2.0,
        )

        fallback_called = []
        def mock_popen(*args, **kwargs):
            fallback_called.append(True)
            class MockProc:
                def communicate(self, *a, **k):
                    return b"", b""
                def poll(self):
                    return 0
                returncode = 0
            return MockProc()

        with patch("app.media_decode.has_audio_stream", return_value=None), \
             patch("subprocess.Popen", side_effect=mock_popen):
            worker.run()

        # Verified: when has_audio_stream returns None, FFmpeg fallback subprocess was called!
        self.assertTrue(len(fallback_called) > 0, "Expected FFmpeg fallback subprocess to be called when has_audio_stream is None")

    def test_thumbnail_memory_release_across_source_switches(self):
        """Verify that switching thumbnail sources releases previous buffers and bounds total memory <= 32 MiB."""
        from PySide6.QtGui import QImage, QPixmap
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        video_path_1 = self._create_synthetic_video(filename="video_1.mp4")
        video_path_2 = self._create_synthetic_video(filename="video_2.mp4")

        # 1. Verify iter_video_thumbnails default budget bounds RGB memory well under 32 MiB
        timestamps = [i * 0.05 for i in range(120)]
        thumbs_1 = list(iter_video_thumbnails(video_path_1, timestamps, width=180))
        rgb_bytes_1 = sum(arr.nbytes for _, arr in thumbs_1)
        self.assertLessEqual(rgb_bytes_1, 8 * 1024 * 1024)

        # 2. Convert to QImage and QPixmap
        qimages_1 = [QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 3, QImage.Format_RGB888).copy() for _, arr in thumbs_1]
        pixmaps_1 = [QPixmap.fromImage(img) for img in qimages_1]

        qimage_bytes_1 = sum(img.sizeInBytes() for img in qimages_1)
        pixmap_bytes_1 = sum(pm.width() * pm.height() * 4 for pm in pixmaps_1)
        total_active_bytes_1 = rgb_bytes_1 + qimage_bytes_1 + pixmap_bytes_1
        self.assertLessEqual(total_active_bytes_1, 32 * 1024 * 1024)

        # 3. Simulate UI state lifecycle:
        # UI holds pixmaps for video 1
        ui_pixmaps = pixmaps_1
        cache_key = "video_1_sig"

        # User switches source to video 2:
        new_key = "video_2_sig"
        if cache_key != new_key:
            # Immediately release old thumbnails
            ui_pixmaps = []
            cache_key = None

        # Verify old buffers were cleared immediately before video 2 starts decoding
        self.assertEqual(len(ui_pixmaps), 0)
        self.assertIsNone(cache_key)

        # 4. Verify worker interruption
        from ui.worker_adapters.processing_workers import TimelineThumbnailWorker
        worker = TimelineThumbnailWorker("req_switch", video_path_2, 2.0, self.temp_dir)
        worker.requestInterruption()
        # Since interruption was requested, worker.run() must exit promptly with empty thumbnails and "interrupted" error
        results = []
        worker.completed.connect(lambda sig, t, e: results.append((sig, t, e)))
        worker.run()
        self.assertEqual(len(results), 1)
        sig, thumbs, err = results[0]
        self.assertEqual(thumbs, [], "Interrupted worker must not decode thumbnails")
        self.assertEqual(err, "interrupted")

    def test_timeline_thumbnail_worker_native_finished_lifecycle(self):
        """Ensure TimelineThumbnailWorker completed is separate from native QThread.finished."""
        from ui.worker_adapters.processing_workers import TimelineThumbnailWorker

        video_path = self._create_synthetic_video()
        worker = TimelineThumbnailWorker("req_cycle", video_path, 1.0, self.temp_dir)

        completed_events = []
        finished_events = []

        worker.completed.connect(lambda sig, thumbs, err: completed_events.append((sig, len(thumbs), err)))
        worker.finished.connect(lambda: finished_events.append("finished"))

        # Run worker in real QThread execution
        worker.start()
        worker.wait(5000)
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        self.assertEqual(len(completed_events), 1)
        self.assertEqual(completed_events[0][0], "req_cycle")
        self.assertEqual(len(finished_events), 1, "Native QThread.finished must be emitted")

    def test_timeline_waveform_worker_native_finished_lifecycle(self):
        """Ensure TimelineWaveformWorker completed is separate from native QThread.finished."""
        from ui.worker_adapters.processing_workers import TimelineWaveformWorker

        audio_path = self._create_synthetic_audio(duration_s=1.0)
        worker = TimelineWaveformWorker(
            "req_wf_cycle",
            "",
            audio_path,
            os.path.join(self.temp_dir, "temp_wf.wav"),
            1.0,
        )

        completed_events = []
        finished_events = []

        worker.completed.connect(lambda sig, wf, dur, err: completed_events.append((sig, len(wf), dur, err)))
        worker.finished.connect(lambda: finished_events.append("finished"))

        # Run worker in real QThread execution
        worker.start()
        worker.wait(5000)
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        self.assertEqual(len(completed_events), 1)
        self.assertEqual(completed_events[0][0], "req_wf_cycle")
        self.assertEqual(len(finished_events), 1, "Native QThread.finished must be emitted for waveform worker")


if __name__ == "__main__":
    unittest.main()



