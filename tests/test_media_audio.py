#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_media_audio.py

Unit tests for native audio decoding and windowed audio reading without subprocess CLI.
"""

from __future__ import annotations

import io
import os
import sys
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

from app.media_decode import AudioReader, decode_audio


class TestMediaAudioDecode(unittest.TestCase):
    """Test native audio decoding from in-memory bytes and short sources."""

    def test_decode_wav_bytes_no_subprocess(self):
        """Ensure decode_audio decodes WAV bytes in-memory with zero subprocess calls."""
        source = io.BytesIO()
        test_samples = np.full(1600, 0.25, dtype=np.float32)
        sf.write(source, test_samples, 16000, format="WAV", subtype="FLOAT")
        wav_bytes = source.getvalue()

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
            pcm, rate = decode_audio(wav_bytes, sample_rate=16000)

        self.assertEqual(rate, 16000)
        self.assertEqual(pcm.shape, (1600,))
        self.assertEqual(pcm.dtype, np.float32)
        np.testing.assert_allclose(pcm, 0.25, atol=1e-6)

    def test_decode_stereo_to_mono(self):
        """Ensure stereo audio is correctly downmixed to mono."""
        source = io.BytesIO()
        left = np.full((1000, 1), 0.4, dtype=np.float32)
        right = np.full((1000, 1), 0.2, dtype=np.float32)
        stereo = np.hstack([left, right])
        sf.write(source, stereo, 16000, format="WAV", subtype="FLOAT")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
            pcm, rate = decode_audio(source.getvalue(), sample_rate=16000)

        self.assertEqual(rate, 16000)
        self.assertEqual(pcm.shape, (1000,))
        # Expected mono is (0.4 + 0.2) / 2 = 0.3
        np.testing.assert_allclose(pcm, 0.3, atol=1e-5)

    def test_decode_resampling(self):
        """Ensure audio resamples accurately from 44100 Hz to 16000 Hz."""
        source = io.BytesIO()
        # 1 second of 44100 Hz audio
        samples_44k = (np.sin(2 * np.pi * 440 * np.linspace(0, 1.0, 44100, endpoint=False))).astype(np.float32)
        sf.write(source, samples_44k, 44100, format="WAV", subtype="FLOAT")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
            pcm, rate = decode_audio(source.getvalue(), sample_rate=16000)

        self.assertEqual(rate, 16000)
        # Should be roughly 16000 samples (+/- small filter edge)
        self.assertTrue(abs(len(pcm) - 16000) <= 20)

    def test_decode_corrupted_input(self):
        """Ensure corrupted audio raises ValueError or RuntimeError without crashing."""
        corrupted = b"NOT_A_VALID_AUDIO_FILE_HEADER_12345"
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            decode_audio(corrupted)

    def test_decode_empty_input(self):
        """Ensure empty input raises ValueError."""
        with self.assertRaises(ValueError):
            decode_audio(b"")

    def test_decode_mp3_no_subprocess(self):
        """Ensure MP3 audio can be decoded without subprocess calls."""
        source = io.BytesIO()
        test_samples = (np.sin(2 * np.pi * 440 * np.linspace(0, 0.5, 8000, endpoint=False))).astype(np.float32)
        sf.write(source, test_samples, 16000, format="MP3")
        mp3_bytes = source.getvalue()

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
            pcm, rate = decode_audio(mp3_bytes, sample_rate=16000)

        self.assertEqual(rate, 16000)
        self.assertTrue(len(pcm) >= 7900)  # codec padding / delay allowance
        self.assertEqual(pcm.dtype, np.float32)

    def test_decode_packed_integer_pcm16_matroska(self):
        """Ensure decode_audio decodes packed integer s16 stereo Matroska to float32 mono."""
        import av
        import fractions

        mkv_path = os.path.join(PROJECT_ROOT, "temp", "test_s16_decode.mkv")
        os.makedirs(os.path.dirname(mkv_path), exist_ok=True)
        container = av.open(mkv_path, mode="w", format="matroska")
        stream = container.add_stream("pcm_s16le", rate=16000)
        stream.time_base = fractions.Fraction(1, 16000)

        left = np.full(1600, 0.25 * 32767, dtype=np.int16)
        right = np.full(1600, 0.25 * 32767, dtype=np.int16)
        interleaved = np.empty(3200, dtype=np.int16)
        interleaved[0::2] = left
        interleaved[1::2] = right
        frame = av.AudioFrame.from_ndarray(interleaved.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = 16000
        frame.time_base = stream.time_base
        for p in stream.encode(frame):
            container.mux(p)
        for p in stream.encode(None):
            container.mux(p)
        container.close()

        try:
            with open(mkv_path, "rb") as f:
                raw_bytes = f.read()
            pcm, rate = decode_audio(raw_bytes, sample_rate=16000)
            self.assertEqual(rate, 16000)
            self.assertEqual(len(pcm), 1600)
            self.assertEqual(pcm.dtype, np.float32)
            np.testing.assert_allclose(pcm, 0.25, atol=1e-3)
        finally:
            if os.path.exists(mkv_path):
                try:
                    os.remove(mkv_path)
                except OSError:
                    pass


class TestAudioReader(unittest.TestCase):
    """Test bounded windowed AudioReader."""

    def setUp(self):
        self.temp_dir = os.path.join(PROJECT_ROOT, "temp", "test_audio_reader")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.wav_path = os.path.join(self.temp_dir, "test_tone.wav")

        # Create a 5-second 16kHz test sine wave with continuous index ramp
        self.sr = 16000
        self.total_samples = 5 * self.sr  # 80,000 samples
        # Float ramp values from 0.0 to 1.0 for exact index checking
        self.ground_truth = (np.linspace(0.0, 1.0, self.total_samples, endpoint=False)).astype(np.float32)
        sf.write(self.wav_path, self.ground_truth, self.sr, format="WAV", subtype="FLOAT")

    def tearDown(self):
        if os.path.exists(self.wav_path):
            try:
                os.remove(self.wav_path)
            except OSError:
                pass

    def test_sequential_window_reading(self):
        """Test reading contiguous blocks sequentially."""
        block_size = 1600  # 100ms
        with AudioReader(self.wav_path, sample_rate=self.sr) as reader:
            for i in range(10):
                start = i * block_size
                block = reader.read(start, block_size)
                self.assertEqual(len(block), block_size)
                expected = self.ground_truth[start : start + block_size]
                np.testing.assert_allclose(block, expected, atol=1e-6)

    def test_random_seek_and_alignment(self):
        """Test random forward and backward seek and exact sample alignment."""
        with AudioReader(self.wav_path, sample_rate=self.sr) as reader:
            # Seek far forward
            start = 32000
            block = reader.read(start, 800)
            self.assertEqual(len(block), 800)
            np.testing.assert_allclose(block, self.ground_truth[start : start + 800], atol=1e-6)

            # Seek backward
            start_back = 4800
            block_back = reader.read(start_back, 1600)
            self.assertEqual(len(block_back), 1600)
            np.testing.assert_allclose(block_back, self.ground_truth[start_back : start_back + 1600], atol=1e-6)

    def test_eof_padding(self):
        """Test that reading past EOF pads with zeros."""
        with AudioReader(self.wav_path, sample_rate=self.sr) as reader:
            # Start 500 samples before EOF, request 1500 samples
            start = self.total_samples - 500
            block = reader.read(start, 1500)
            self.assertEqual(len(block), 1500)

            # First 500 should match ground truth
            np.testing.assert_allclose(block[:500], self.ground_truth[start:], atol=1e-6)
            # Remaining 1000 must be zero
            np.testing.assert_allclose(block[500:], 0.0, atol=1e-7)

    def test_negative_start_sample(self):
        """Test that start_sample < 0 pads beginning with zeros."""
        with AudioReader(self.wav_path, sample_rate=self.sr) as reader:
            # Start at -500, read 1000 samples
            block = reader.read(-500, 1000)
            self.assertEqual(len(block), 1000)
            # First 500 are zero
            np.testing.assert_allclose(block[:500], 0.0, atol=1e-7)
            # Next 500 match ground truth [0:500]
            np.testing.assert_allclose(block[500:], self.ground_truth[:500], atol=1e-6)

    def test_multiple_open_close_cycles(self):
        """Ensure repeated opening and closing does not fail or leak file handles."""
        for _ in range(50):
            reader = AudioReader(self.wav_path, sample_rate=self.sr)
            block = reader.read(0, 320)
            self.assertEqual(len(block), 320)
            reader.close()

    def test_audio_reader_video_container(self):
        """Ensure AudioReader can open video container (MP4) with audio and read blocks."""
        import av
        import fractions

        mp4_path = os.path.join(self.temp_dir, "test_container.mp4")
        container = av.open(mp4_path, mode="w", format="mp4")
        stream = container.add_stream("aac", rate=self.sr)
        stream.time_base = fractions.Fraction(1, self.sr)

        tone = (np.sin(2 * np.pi * 440 * np.linspace(0, 2.0, 2 * self.sr))).astype(np.float32)
        frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = self.sr
        frame.time_base = stream.time_base
        frame.pts = 0

        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()

        try:
            with AudioReader(mp4_path, sample_rate=self.sr) as reader:
                block = reader.read(0, 1600)
                self.assertEqual(len(block), 1600)
                self.assertEqual(block.dtype, np.float32)
                # Verify non-zero content
                self.assertTrue(np.max(np.abs(block)) > 0.01)
        finally:
            if os.path.exists(mp4_path):
                try:
                    os.remove(mp4_path)
                except OSError:
                    pass

    def test_audio_reader_packed_integer_pcm16_matroska(self):
        """Ensure AudioReader decodes packed s16 stereo Matroska accurately."""
        import av
        import fractions

        mkv_path = os.path.join(self.temp_dir, "test_s16_reader.mkv")
        container = av.open(mkv_path, mode="w", format="matroska")
        stream = container.add_stream("pcm_s16le", rate=self.sr)
        stream.time_base = fractions.Fraction(1, self.sr)

        left = np.full(1600, 0.25 * 32767, dtype=np.int16)
        right = np.full(1600, 0.25 * 32767, dtype=np.int16)
        interleaved = np.empty(3200, dtype=np.int16)
        interleaved[0::2] = left
        interleaved[1::2] = right
        frame = av.AudioFrame.from_ndarray(interleaved.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = self.sr
        frame.time_base = stream.time_base
        for p in stream.encode(frame):
            container.mux(p)
        for p in stream.encode(None):
            container.mux(p)
        container.close()

        try:
            with AudioReader(mkv_path, sample_rate=self.sr) as reader:
                block = reader.read(0, 1600)
                self.assertEqual(len(block), 1600)
                self.assertEqual(block.dtype, np.float32)
                np.testing.assert_allclose(block, 0.25, atol=1e-3)
        finally:
            if os.path.exists(mkv_path):
                try:
                    os.remove(mkv_path)
                except OSError:
                    pass

    def test_pyav_audio_reader_seek_pts_alignment(self):
        """Ensure seeking in PyAV container precisely aligns PTS timestamps with samples."""
        import av
        import fractions

        mkv_path = os.path.join(self.temp_dir, "test_pts_alignment.mkv")
        container = av.open(mkv_path, mode="w", format="matroska")
        stream = container.add_stream("pcm_s16le", rate=16000, layout="mono")
        full_pcm = np.linspace(0, 1.0, 16000, dtype=np.float32)
        frame_size = 1000
        for i in range(16):
            chunk = full_pcm[i * frame_size : (i + 1) * frame_size]
            int_chunk = (chunk * 32767).astype(np.int16)
            frame = av.AudioFrame.from_ndarray(int_chunk.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = 16000
            frame.pts = i * frame_size
            frame.time_base = fractions.Fraction(1, 16000)
            for p in stream.encode(frame):
                container.mux(p)
        for p in stream.encode(None):
            container.mux(p)
        container.close()

        try:
            with AudioReader(mkv_path, sample_rate=16000) as reader:
                block = reader.read(4800, 160)
                expected = full_pcm[4800:4960]
                self.assertEqual(len(block), 160)
                np.testing.assert_allclose(block, expected, atol=1e-3)
        finally:
            if os.path.exists(mkv_path):
                try:
                    os.remove(mkv_path)
                except OSError:
                    pass

    def test_resample_continuous_dc_window(self):
        """Ensure reading 10ms windows across sample rates preserves filter continuity without edge drop."""
        wav_44k = os.path.join(self.temp_dir, "test_dc_44k.wav")
        dc_val = 0.5
        samples_44k = np.full(44100, dc_val, dtype=np.float32)
        sf.write(wav_44k, samples_44k, 44100, format="WAV", subtype="FLOAT")

        try:
            with AudioReader(wav_44k, sample_rate=16000) as reader:
                block_size = 160  # 10ms at 16kHz
                blocks = []
                # Read consecutive windows across 100ms to 400ms
                for i in range(10, 40):
                    b = reader.read(i * block_size, block_size)
                    blocks.append(b)
                concatenated = np.concatenate(blocks)
                # Max difference from 0.5 must be < 0.01 (Issue 5)
                max_diff = float(np.max(np.abs(concatenated - dc_val)))
                self.assertLess(max_diff, 0.01)
        finally:
            if os.path.exists(wav_44k):
                try:
                    os.remove(wav_44k)
                except OSError:
                    pass


class TestAudioMixerPCM(unittest.TestCase):
    """Test block-based PCM mixing and linear gain clipping."""

    def test_mix_pcm_block_linear_and_clip(self):
        """Test linear gain combination and clipping to [-1.0, 1.0]."""
        from app.audio_mixer import mix_pcm_block

        a = np.array([0.2, 0.9], dtype=np.float32)
        b = np.array([0.4, 0.9], dtype=np.float32)
        original = a.copy()
        mixed = mix_pcm_block([a, b], [0.5, 1.0])
        np.testing.assert_allclose(mixed, [0.5, 1.0], atol=1e-6)
        np.testing.assert_array_equal(a, original)

    def test_mix_pcm_block_negative_clipping(self):
        """Test negative overload clips to -1.0."""
        from app.audio_mixer import mix_pcm_block

        a = np.array([-0.8, -0.6], dtype=np.float32)
        b = np.array([-0.5, -0.7], dtype=np.float32)
        mixed = mix_pcm_block([a, b], [1.0, 1.0])
        # -0.8 + -0.5 = -1.3 -> -1.0; -0.6 + -0.7 = -1.3 -> -1.0
        np.testing.assert_allclose(mixed, [-1.0, -1.0], atol=1e-6)

    def test_mix_pcm_block_length_mismatch(self):
        """Ensure blocks with different lengths raise ValueError."""
        from app.audio_mixer import mix_pcm_block

        a = np.array([0.1, 0.2], dtype=np.float32)
        b = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        with self.assertRaises(ValueError):
            mix_pcm_block([a, b], [1.0, 1.0])

    def test_mix_pcm_block_empty(self):
        """Ensure empty input returns empty array."""
        from app.audio_mixer import mix_pcm_block

        mixed = mix_pcm_block([], [])
        self.assertEqual(len(mixed), 0)
        self.assertEqual(mixed.dtype, np.float32)

    def test_mix_pcm_block_does_not_mutate_nan_inputs(self):
        """Ensure blocks containing NaN or inf are not mutated in-place."""
        from app.audio_mixer import mix_pcm_block

        input_arr = np.array([0.5, np.nan, np.inf, -np.inf], dtype=np.float32)
        mixed = mix_pcm_block([input_arr], [1.0])
        # Output must be cleaned and clamped
        np.testing.assert_allclose(mixed, [0.5, 0.0, 1.0, -1.0], atol=1e-6)
        # Original input MUST still contain NaN and inf (never mutated in-place)
        self.assertTrue(np.isnan(input_arr[1]))
        self.assertTrue(np.isposinf(input_arr[2]))
        self.assertTrue(np.isneginf(input_arr[3]))

    def test_mix_pcm_block_gains_count_mismatch(self):
        """Ensure mismatch between number of blocks and number of gains raises ValueError."""
        from app.audio_mixer import mix_pcm_block

        b1 = np.array([0.1, 0.2], dtype=np.float32)
        b2 = np.array([0.3, 0.4], dtype=np.float32)
        with self.assertRaises(ValueError):
            # 2 blocks but only 1 gain
            mix_pcm_block([b1, b2], [1.0])


class TestPreviewAudioEngine(unittest.TestCase):
    """Test PreviewAudioEngine lifecycle and asynchronous controls."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def setUp(self):
        self.temp_dir = os.path.join(PROJECT_ROOT, "temp", "test_preview_engine")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.wav_path = os.path.join(self.temp_dir, "test_track.wav")
        tone = (np.sin(2 * np.pi * 440 * np.linspace(0, 3.0, 3 * 16000))).astype(np.float32) * 0.5
        sf.write(self.wav_path, tone, 16000, format="WAV", subtype="FLOAT")

    def tearDown(self):
        if os.path.exists(self.wav_path):
            try:
                os.remove(self.wav_path)
            except OSError:
                pass

    def test_engine_lifecycle(self):
        """Ensure PreviewAudioEngine initializes, sets tracks, seeks, and closes cleanly."""
        from ui.utils.preview_audio import PreviewAudioEngine

        engine = PreviewAudioEngine()
        try:
            tracks = [
                {
                    "id": "track_1",
                    "path": self.wav_path,
                    "start": 0.0,
                    "end": 3.0,
                    "volume": 80.0,
                    "muted": False,
                }
            ]
            engine.set_tracks(tracks)
            engine.set_track_gain("track_1", 0.5, muted=False)
            engine.seek(1200)
            self.assertEqual(engine.timeline_position_ms(), 1200)

            engine.play()
            self.app.processEvents()
            engine.pause()
            self.app.processEvents()
            engine.stop()
            self.assertEqual(engine.timeline_position_ms(), 0)
        finally:
            engine.close()

    def test_music_clip_seamless_looping(self):
        """Ensure looping music track wraps seamlessly across reader boundaries."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        ramp_wav = os.path.join(self.temp_dir, "test_ramp.wav")
        ramp_samples = (np.linspace(0.0, 1.0, 1000, endpoint=False)).astype(np.float32)
        sf.write(ramp_wav, ramp_samples, 16000, format="WAV", subtype="FLOAT")

        worker = _PreviewAudioWorker()
        try:
            worker.block_size = 320
            worker._sink_sr = 16000
            worker._sink_channels = 1
            worker._sink_is_float = True
            worker._is_playing = True
            worker._tracks = [
                {
                    "id": "loop_music",
                    "path": ramp_wav,
                    "start_ms": 0,
                    "end_ms": 10000,
                    "source_start_ms": 0,
                    "volume": 100.0,
                    "muted": False,
                    "target_gain": 1.0,
                    "current_gain": 1.0,
                    "loop": True,
                    "is_original_video": False,
                }
            ]
            worker._readers = {ramp_wav: AudioReader(ramp_wav, sample_rate=16000)}
            mock_io = MagicMock()
            mock_io.write.return_value = 1280
            mock_sink = MagicMock()
            mock_sink.bytesFree.return_value = 100000
            worker._sink = mock_sink
            worker._io_device = mock_io

            # Set timeline position near loop boundary (sample 960 -> 960/16 = 60ms exactly)
            # Sample 960..1280 spans across boundary: 960..999 is [960:1000] (40 samples), 1000..1279 is [0:280] (280 samples)
            worker._timeline_pos_ms = 60
            worker._on_timer_tick()

            self.assertTrue(mock_io.write.called)
            written_bytes = mock_io.write.call_args[0][0]
            written_samples = np.frombuffer(written_bytes, dtype=np.float32)
            self.assertEqual(len(written_samples), 320)
            # Verify continuity: first 40 samples match ramp[960:], next 280 match ramp[:280]
            np.testing.assert_allclose(written_samples[:40], ramp_samples[960:1000], atol=1e-4)
            np.testing.assert_allclose(written_samples[40:], ramp_samples[:280], atol=1e-4)
        finally:
            worker.close()
            if os.path.exists(ramp_wav):
                try:
                    os.remove(ramp_wav)
                except OSError:
                    pass

    def test_audio_worker_clock_advances_only_on_written_bytes(self):
        """Ensure audio clock does not advance when io_device.write returns 0."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        worker = _PreviewAudioWorker()
        try:
            worker.block_size = 320
            worker._sink_sr = 16000
            worker._sink_channels = 1
            worker._sink_is_float = True
            worker._is_playing = True
            worker._timeline_pos_ms = 500
            worker._tracks = []
            mock_sink = MagicMock()
            mock_sink.bytesFree.return_value = 100000
            mock_io = MagicMock()
            # Simulate sink buffer full: 0 bytes written
            mock_io.write.return_value = 0
            worker._sink = mock_sink
            worker._io_device = mock_io

            worker._on_timer_tick()
            # Clock should remain at 500
            self.assertEqual(worker._timeline_pos_ms, 500)

            # Now simulate successful write of 320 float32 samples (1280 bytes)
            mock_io.write.return_value = 1280
            worker._on_timer_tick()
            # 320 samples @ 16kHz = 20ms -> 500 + 20 = 520ms
            self.assertEqual(worker._timeline_pos_ms, 520)
        finally:
            worker.close()

    def test_atempo_playback_rate(self):
        """Ensure set_rate configures tempo filter on worker."""
        from ui.utils.preview_audio import _PreviewAudioWorker

        worker = _PreviewAudioWorker()
        try:
            worker.set_rate(1.5)
            self.assertEqual(worker._playback_rate, 1.5)
            # Setting close to 1.0 resets tempo graph
            worker.set_rate(1.0)
            self.assertEqual(worker._playback_rate, 1.0)
        finally:
            worker.close()

    def test_atempo_clock_accuracy_2x_and_half_x(self):
        """Ensure 100 ticks at 2.0x reaches ~2000 ms and at 0.5x reaches ~500 ms."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        for rate, expected_ms in ((2.0, 2000), (0.5, 500)):
            worker = _PreviewAudioWorker(sample_rate=16000, block_size=160)
            try:
                worker._sink_sr = 16000
                worker._sink_channels = 1
                worker._sink_is_float = True
                worker._is_playing = True
                worker._tracks = []
                worker.set_rate(rate)

                mock_sink = MagicMock()
                mock_sink.bytesFree.return_value = 100000
                mock_io = MagicMock()
                # 160 samples float32 = 640 bytes written per tick
                mock_io.write.return_value = 640
                worker._sink = mock_sink
                worker._io_device = mock_io

                for _ in range(100):
                    worker._on_timer_tick()

                self.assertEqual(worker._timeline_pos_ms, expected_ms)
            finally:
                worker.close()

    def test_partial_write_buffer_preservation(self):
        """Ensure partial sink writes buffer trailing bytes without dropping them."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        worker = _PreviewAudioWorker(sample_rate=16000, block_size=160)
        try:
            worker._sink_sr = 16000
            worker._sink_channels = 1
            worker._sink_is_float = True
            worker._is_playing = True
            worker._tracks = []

            mock_sink = MagicMock()
            mock_sink.bytesFree.return_value = 100000
            mock_io = MagicMock()
            worker._sink = mock_sink
            worker._io_device = mock_io

            # Block size 160 float32 = 640 bytes
            # Tick 1: only 320 bytes accepted by sink
            mock_io.write.return_value = 320
            worker._on_timer_tick()
            # Clock advanced by 320 bytes (80 samples = 5 ms)
            self.assertEqual(worker._timeline_pos_ms, 5)
            self.assertEqual(len(worker._pending_write_bytes), 320)

            # Tick 2: sink accepts remaining 320 bytes
            mock_io.write.return_value = 320
            worker._on_timer_tick()
            # Clock advances remaining 5 ms (total 10 ms = 160 samples)
            self.assertEqual(worker._timeline_pos_ms, 10)
            self.assertEqual(len(worker._pending_write_bytes), 0)
        finally:
            worker.close()

    def test_clock_accuracy_fractional_rates(self):
        """Ensure 100 ticks at 0.25x, 0.50x, 0.75x, 1.25x, 2.00x maintain exact timeline clock."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        rates_and_expected = [
            (0.25, 250),
            (0.50, 500),
            (0.75, 750),
            (1.25, 1250),
            (2.00, 2000),
        ]
        for rate, expected_ms in rates_and_expected:
            worker = _PreviewAudioWorker(sample_rate=16000, block_size=160)
            try:
                worker._sink_sr = 16000
                worker._sink_channels = 1
                worker._sink_is_float = True
                worker._is_playing = True
                worker._tracks = []
                worker.set_rate(rate)

                mock_sink = MagicMock()
                mock_sink.bytesFree.return_value = 100000
                mock_io = MagicMock()
                mock_io.write.return_value = 640
                worker._sink = mock_sink
                worker._io_device = mock_io

                for _ in range(100):
                    worker._on_timer_tick()

                self.assertEqual(worker._timeline_pos_ms, expected_ms)
            finally:
                worker.close()

    def test_pause_and_set_rate_reset_state(self):
        """Ensure pause() and set_rate() completely reset tempo graph, FIFO, PTS, resampler, and pending bytes."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        worker = _PreviewAudioWorker(sample_rate=16000, block_size=160)
        try:
            worker._sink_sr = 48000
            worker._is_playing = True
            worker._tempo_fifo = np.ones(100, dtype=np.float32)
            worker._tempo_graph = ("mock_graph", None, None)
            worker._tempo_in_pts = 1000
            worker._sink_resampler = "mock_resampler"
            worker._pending_write_bytes = b"dirty"

            worker.pause()
            self.assertEqual(len(worker._tempo_fifo), 0)
            self.assertIsNone(worker._tempo_graph)
            self.assertEqual(worker._tempo_in_pts, 0)
            self.assertIsNone(worker._sink_resampler)
            self.assertEqual(len(worker._pending_write_bytes), 0)

            # Test set_rate reset
            worker._tempo_fifo = np.ones(100, dtype=np.float32)
            worker._tempo_graph = ("mock_graph", None, None)
            worker._tempo_in_pts = 2000
            worker._sink_resampler = "mock_resampler"
            worker._pending_write_bytes = b"dirty"

            worker.set_rate(1.5)
            self.assertEqual(len(worker._tempo_fifo), 0)
            self.assertIsNone(worker._tempo_graph)
            self.assertEqual(worker._tempo_in_pts, 0)
            self.assertIsNone(worker._sink_resampler)
            self.assertEqual(len(worker._pending_write_bytes), 0)
        finally:
            worker.close()

    def test_multichannel_output_expansion(self):
        """Ensure 6-channel output sink receives properly tiled audio and advances clock correctly."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        worker = _PreviewAudioWorker(sample_rate=16000, block_size=160)
        try:
            worker._sink_sr = 16000
            worker._sink_channels = 6
            worker._sink_is_float = True
            worker._is_playing = True
            worker._tracks = []

            mock_sink = MagicMock()
            mock_sink.bytesFree.return_value = 100000
            mock_io = MagicMock()
            # 160 samples * 6 channels * 4 bytes = 3840 bytes
            mock_io.write.side_effect = lambda data: len(data)
            worker._sink = mock_sink
            worker._io_device = mock_io

            worker._on_timer_tick()
            written_bytes = mock_io.write.call_args[0][0]
            self.assertEqual(len(written_bytes), 160 * 6 * 4)
            # Clock should advance by 10 ms (160 frames at 16kHz)
            self.assertEqual(worker._timeline_pos_ms, 10)
        finally:
            worker.close()

    def test_audio_worker_audible_position_reflects_buffer_drain(self):
        """Ensure _timeline_pos_ms computes audible position accounting for buffered data."""
        from unittest.mock import MagicMock
        from ui.utils.preview_audio import _PreviewAudioWorker
        from PySide6.QtMultimedia import QAudioSink

        worker = _PreviewAudioWorker(sample_rate=48000, block_size=480)
        try:
            worker._sink_sr = 48000
            worker._sink_channels = 2
            worker._sink_is_float = True
            worker._is_playing = True
            worker._playback_rate = 1.0

            mock_sink = MagicMock(spec=QAudioSink)
            mock_sink.bufferSize.return_value = 96000  # 250 ms at 48kHz stereo float32
            mock_sink.bytesFree.return_value = 57600  # 38400 bytes buffered = 100 ms

            worker._sink = mock_sink
            # Set write cursor to 1000 ms
            worker._timeline_pos_exact_ms = 1000.0

            # Audible position should be 1000 ms - 100 ms buffered = 900 ms
            self.assertEqual(worker._timeline_pos_ms, 900)

            # When paused, _timeline_pos_ms returns exact position
            worker._is_playing = False
            self.assertEqual(worker._timeline_pos_ms, 1000)
        finally:
            worker.close()

    def test_sync_audio_to_video_tolerance_threshold(self):
        """Ensure media_backend desync tolerance avoids seeking unless desync > 300ms."""
        from unittest.mock import MagicMock
        from PySide6.QtMultimedia import QMediaPlayer
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "test.mp4"
        backend._video_frozen = False
        backend._last_seek_mono = 0.0
        backend._native_audio_active = True
        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""
        backend._video_time_warps = []
        backend._state = QMediaPlayer.PlayingState

        mock_player = MagicMock()
        mock_player.time_pos = 10.0  # 10,000 ms
        mock_player.pause = False
        backend._player = mock_player

        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine

        # Case 1: Audio is at 9850 ms (150 ms desync) -> should NOT seek
        mock_engine.timeline_position_ms.return_value = 9850
        backend._sync_audio_to_video()
        mock_engine.seek.assert_not_called()

        # Case 2: Audio is at 9750 ms (250 ms desync) -> should NOT seek
        mock_engine.timeline_position_ms.return_value = 9750
        backend._sync_audio_to_video()
        mock_engine.seek.assert_not_called()

        # Case 3: Audio is at 9600 ms (400 ms desync) -> SHOULD seek
        mock_engine.timeline_position_ms.return_value = 9600
        backend._sync_audio_to_video()
        mock_engine.seek.assert_called_once_with(10000)

    def test_sync_audio_to_video_paused_skips_when_in_tolerance(self):
        """Ensure _sync_audio_to_video returns early when paused and drift <= 300ms, but corrects when drift > 300ms."""
        from unittest.mock import MagicMock
        from PySide6.QtMultimedia import QMediaPlayer
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "test.mp4"
        backend._video_frozen = False
        backend._last_seek_mono = 0.0
        backend._native_audio_active = True
        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""
        backend._video_time_warps = []
        backend._state = QMediaPlayer.PausedState

        mock_player = MagicMock()
        mock_player.time_pos = 10.0  # 10,000 ms
        mock_player.pause = True
        backend._player = mock_player

        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine

        # Case 1: Audio is at 9850 ms (150 ms desync) while paused -> early returns, no seek
        mock_engine.is_playing.return_value = False
        mock_engine.timeline_position_ms.return_value = 9850
        backend._sync_audio_to_video()
        mock_engine.seek.assert_not_called()

        # Case 2: Audio is at 9200 ms (800 ms desync) while paused -> corrects drift
        mock_engine.timeline_position_ms.return_value = 9200
        backend._sync_audio_to_video()
        mock_engine.seek.assert_called_once_with(10000)
        mock_engine.pause.assert_called_once()

    def test_pyav_audio_reader_seek_past_eof_and_gap(self):
        """Ensure AudioReader seeking past EOF or over gaps does not raise EOFError or TypeError."""
        import av
        import fractions

        mkv_path = os.path.join(self.temp_dir, "test_gap_seek.mkv")
        container = av.open(mkv_path, mode="w", format="matroska")
        stream = container.add_stream("pcm_s16le", rate=16000)
        stream.time_base = fractions.Fraction(1, 16000)

        # 1600 samples (100 ms)
        data = np.zeros(1600, dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(data.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 16000
        frame.time_base = stream.time_base
        frame.pts = 32000  # Starts after 2.0s
        for p in stream.encode(frame):
            container.mux(p)
        for p in stream.encode(None):
            container.mux(p)
        container.close()

        try:
            with AudioReader(mkv_path, sample_rate=16000) as reader:
                # Seek chain: 0 -> 8000 -> 16000 -> 32000 -> 64000
                for seek_sample in (0, 8000, 16000, 32000, 64000):
                    block = reader.read(seek_sample, 160)
                    self.assertEqual(len(block), 160)
                    self.assertEqual(block.dtype, np.float32)
        finally:
            if os.path.exists(mkv_path):
                try:
                    os.remove(mkv_path)
                except OSError:
                    pass

    def test_pyav_audio_reader_preserves_pts_gap_silence(self):
        """Ensure AudioReader inserts silence on PTS gaps instead of gluing disjoint frames."""
        import av
        import fractions

        mkv_path = os.path.join(self.temp_dir, "test_pts_gap.mkv")
        container = av.open(mkv_path, mode="w", format="matroska")
        stream = container.add_stream("pcm_s16le", rate=16000, layout="mono")
        stream.time_base = fractions.Fraction(1, 16000)

        # Frame 1: 1600 samples at PTS 0
        f1_data = np.full((1, 1600), 10000, dtype=np.int16)
        f1 = av.AudioFrame.from_ndarray(f1_data, format="s16", layout="mono")
        f1.sample_rate = 16000
        f1.time_base = fractions.Fraction(1, 16000)
        f1.pts = 0
        for p in stream.encode(f1):
            container.mux(p)

        # Frame 2: 1600 samples at PTS 4800 (leaving 3200 samples gap)
        f2_data = np.full((1, 1600), 20000, dtype=np.int16)
        f2 = av.AudioFrame.from_ndarray(f2_data, format="s16", layout="mono")
        f2.sample_rate = 16000
        f2.time_base = fractions.Fraction(1, 16000)
        f2.pts = 4800
        for p in stream.encode(f2):
            container.mux(p)

        for p in stream.encode(None):
            container.mux(p)
        container.close()

        try:
            with AudioReader(mkv_path, sample_rate=16000) as reader:
                block = reader.read(0, 6400)
                self.assertEqual(len(block), 6400)
                # Frame 1 check
                np.testing.assert_allclose(block[:1600], 10000.0 / 32768.0, atol=1e-3)
                # Gap check: exactly 3200 samples of silence (1600 to 4800)
                np.testing.assert_array_equal(block[1600:4800], np.zeros(3200, dtype=np.float32))
                # Frame 2 check
                np.testing.assert_allclose(block[4800:6400], 20000.0 / 32768.0, atol=1e-3)
        finally:
            if os.path.exists(mkv_path):
                try:
                    os.remove(mkv_path)
                except OSError:
                    pass

    def test_tts_in_process_bytes_conversion(self):
        """Check conversion from in-memory audio using explicit and native sample rates."""
        import io
        import av
        import soundfile as sf
        from app.tts_processor import convert_audio_data_to_wav_16k_mono
        from app.capcut.tts import convert_audio_data_to_wav

        mp3_bio = io.BytesIO()
        container = av.open(mp3_bio, mode="w", format="mp3")
        stream = container.add_stream("mp3", rate=44100)
        tone = (np.sin(2 * np.pi * 440 * np.arange(8820) / 44100) * 16000).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="s16p", layout="mono")
        frame.sample_rate = 44100
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()

        mp3_bytes = mp3_bio.getvalue()
        self.assertGreater(len(mp3_bytes), 0)

        out_wav_1 = os.path.join(self.temp_dir, "test_tts_edge.wav")
        try:
            res_path = convert_audio_data_to_wav_16k_mono(mp3_bytes, out_wav_1)
            self.assertEqual(res_path, out_wav_1)
            self.assertTrue(os.path.exists(out_wav_1))
            data, sr = sf.read(out_wav_1)
            self.assertEqual(sr, 16000)
            self.assertEqual(data.ndim, 1)
            self.assertGreater(len(data), 0)
        finally:
            if os.path.exists(out_wav_1):
                os.remove(out_wav_1)

        out_wav_2 = os.path.join(self.temp_dir, "test_tts_capcut.wav")
        try:
            res_path = convert_audio_data_to_wav(io.BytesIO(mp3_bytes), out_wav_2)
            self.assertEqual(res_path, out_wav_2)
            self.assertTrue(os.path.exists(out_wav_2))
            data, sr = sf.read(out_wav_2)
            self.assertEqual(sr, 44100)
            self.assertEqual(data.ndim, 1)
            self.assertGreater(len(data), 0)
        finally:
            if os.path.exists(out_wav_2):
                os.remove(out_wav_2)

    def test_io_write_auditor_intercepts_writes(self):
        """Ensure IoWriteAuditor intercepts both regular file writes and soundfile writes."""
        from scripts.benchmark_preview import IoWriteAuditor
        import soundfile as sf

        test_txt = os.path.join(self.temp_dir, "test_audit.txt")
        test_wav = os.path.join(self.temp_dir, "test_audit.wav")

        with IoWriteAuditor() as auditor:
            with open(test_txt, "w") as f:
                f.write("hello world\n")
            sf.write(test_wav, np.zeros(160, dtype=np.float32), 16000)

        self.assertEqual(auditor.disk_writes_count, 2)
        expected_bytes = len("hello world\n") + os.path.getsize(test_wav)
        self.assertEqual(auditor.total_bytes_written, expected_bytes)
        self.assertEqual(len(auditor.files_opened_for_write), 2)

    def test_tts_in_memory_ffmpeg_fallback(self):
        """Ensure TTS converter falls back to FFmpeg via pipe:0 without creating temp files when PyAV fails."""
        from unittest.mock import patch
        import av
        import io
        import soundfile as sf
        from app.tts_processor import convert_audio_data_to_wav_16k_mono
        from app.capcut.tts import convert_audio_data_to_wav
        from scripts.benchmark_preview import IoWriteAuditor

        bio = io.BytesIO()
        c = av.open(bio, mode="w", format="mp3")
        s = c.add_stream("mp3", rate=44100)
        f = av.AudioFrame.from_ndarray(np.zeros((1, 4410), dtype=np.int16), format="s16p", layout="mono")
        f.sample_rate = 44100
        for p in s.encode(f):
            c.mux(p)
        for p in s.encode(None):
            c.mux(p)
        c.close()
        mp3_bytes = bio.getvalue()

        out_wav_1 = os.path.join(self.temp_dir, "test_fallback_edge.wav")
        out_wav_2 = os.path.join(self.temp_dir, "test_fallback_capcut.wav")

        try:
            with IoWriteAuditor() as auditor, patch("av.open", side_effect=av.FFmpegError(1, "Simulated PyAV error")):
                convert_audio_data_to_wav_16k_mono(mp3_bytes, out_wav_1)
                convert_audio_data_to_wav(io.BytesIO(mp3_bytes), out_wav_2)

            self.assertTrue(os.path.exists(out_wav_1))
            self.assertTrue(os.path.exists(out_wav_2))
            d1, sr1 = sf.read(out_wav_1)
            d2, sr2 = sf.read(out_wav_2)
            self.assertEqual(sr1, 16000)
            self.assertEqual(sr2, 44100)

            # Ensure Python opened 0 temporary files for writing (input streamed via pipe:0)
            self.assertEqual(auditor.files_opened_for_write, [])
            self.assertEqual(auditor.disk_writes_count, 0)
            self.assertEqual(auditor.total_bytes_written, 0)
        finally:
            for p in (out_wav_1, out_wav_2):
                if os.path.exists(p):
                    os.remove(p)


class TestTask4NativeAudioProcessing(unittest.TestCase):
    """Unit tests for Task 4: In-memory TTS conversion, native speed, fit, and silence trimming."""

    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.mkdtemp(prefix="test_task4_audio_")

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_change_pcm_speed_ratios(self):
        """Ensure change_pcm_speed correctly alters tempo with PyAV in-process across speed ratios."""
        from app.audio_mixer import change_pcm_speed

        sr = 16000
        # 1.0 second 440Hz sine wave
        t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
        pcm = 0.5 * np.sin(2 * np.pi * 440 * t)

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            for speed in [0.5, 0.75, 1.25, 2.0, 3.0]:
                out = change_pcm_speed(pcm, sample_rate=sr, speed_ratio=speed)
                self.assertEqual(out.dtype, np.float32)
                self.assertTrue(np.all(np.isfinite(out)))
                expected_len = int(len(pcm) / speed)
                # Allow tolerance for atempo filter buffer flush / grain alignment
                self.assertAlmostEqual(len(out), expected_len, delta=int(0.1 * sr))

    def test_change_wav_speed_native_no_subprocess(self):
        """Ensure change_wav_speed adjusts WAV speed atomically without subprocess."""
        from app.audio_mixer import change_wav_speed

        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
        pcm = 0.5 * np.sin(2 * np.pi * 440 * t)
        in_path = os.path.join(self.temp_dir, "input_tone.wav")
        out_path = os.path.join(self.temp_dir, "output_speed.wav")
        sf.write(in_path, pcm, sr, format="WAV", subtype="PCM_16")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            res = change_wav_speed(input_wav_path=in_path, output_wav_path=out_path, speed_ratio=1.25)

        self.assertEqual(res, out_path)
        self.assertTrue(os.path.exists(out_path))
        data, out_sr = sf.read(out_path)
        self.assertEqual(out_sr, 16000)
        expected_len = int(len(pcm) / 1.25)
        self.assertAlmostEqual(len(data), expected_len, delta=int(0.1 * sr))

    def test_fit_wav_to_duration_modes(self):
        """Ensure fit_wav_to_duration handles timeline, smart, and force modes without subprocess."""
        from app.audio_mixer import fit_wav_to_duration

        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
        pcm = 0.5 * np.sin(2 * np.pi * 440 * t)
        in_path = os.path.join(self.temp_dir, "input_fit.wav")
        sf.write(in_path, pcm, sr, format="WAV", subtype="PCM_16")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            # 1. Timeline mode: cut to target duration
            out_timeline = os.path.join(self.temp_dir, "out_timeline.wav")
            fit_wav_to_duration(
                input_wav_path=in_path,
                output_wav_path=out_timeline,
                target_duration_seconds=0.5,
                mode="timeline",
            )
            data, _ = sf.read(out_timeline)
            self.assertAlmostEqual(len(data), int(0.5 * sr), delta=10)

            # 2. Smart mode: audio is longer than target (1.0s vs 0.8s) -> stretch/speed up
            out_smart = os.path.join(self.temp_dir, "out_smart.wav")
            fit_wav_to_duration(
                input_wav_path=in_path,
                output_wav_path=out_smart,
                target_duration_seconds=0.8,
                mode="smart",
            )
            data, _ = sf.read(out_smart)
            self.assertAlmostEqual(len(data), int(0.8 * sr), delta=int(0.08 * sr))

            # 3. Smart mode: ratio < smart_min_ratio (0.77) -> returns original
            res = fit_wav_to_duration(
                input_wav_path=in_path,
                output_wav_path=os.path.join(self.temp_dir, "out_smart_min.wav"),
                target_duration_seconds=0.4,
                mode="smart",
            )
            self.assertEqual(res, in_path)

            # 4. Force mode: speed up to fit
            out_force = os.path.join(self.temp_dir, "out_force.wav")
            fit_wav_to_duration(
                input_wav_path=in_path,
                output_wav_path=out_force,
                target_duration_seconds=0.8,
                mode="force",
            )
            data, _ = sf.read(out_force)
            self.assertAlmostEqual(len(data), int(0.8 * sr), delta=int(0.08 * sr))

    def test_trim_trailing_silence_native(self):
        """Ensure trim_trailing_silence detects trailing silence and trims without subprocess."""
        from app.audio_mixer import trim_trailing_silence

        sr = 16000
        # 0.5s audio + 1.0s silence = 1.5s
        t = np.linspace(0, 0.5, int(0.5 * sr), endpoint=False, dtype=np.float32)
        tone = 0.5 * np.sin(2 * np.pi * 440 * t)
        silence = np.zeros(int(1.0 * sr), dtype=np.float32)
        audio = np.concatenate([tone, silence])

        in_path = os.path.join(self.temp_dir, "with_silence.wav")
        out_path = os.path.join(self.temp_dir, "trimmed.wav")
        sf.write(in_path, audio, sr, format="WAV", subtype="PCM_16")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            res = trim_trailing_silence(
                input_wav_path=in_path,
                output_wav_path=out_path,
                silence_threshold=-40.0,
                min_silence_duration=0.5,
            )

        self.assertEqual(res, out_path)
        self.assertTrue(os.path.exists(out_path))
        data, _ = sf.read(out_path)
        # Expected: 0.5s tone + ~0.1s padding = ~0.6s
        self.assertAlmostEqual(len(data), int(0.6 * sr), delta=int(0.05 * sr))

        # Test audio without trailing silence: constant tone returns in_path
        in_tone_path = os.path.join(self.temp_dir, "no_silence.wav")
        sf.write(in_tone_path, tone, sr, format="WAV", subtype="PCM_16")
        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            res_no_silence = trim_trailing_silence(
                input_wav_path=in_tone_path,
                output_wav_path=os.path.join(self.temp_dir, "should_not_trim.wav"),
                silence_threshold=-40.0,
                min_silence_duration=0.5,
            )
        self.assertEqual(res_no_silence, in_tone_path)

    def test_convert_audio_to_wav_16k_mono_media_decode(self):
        """Ensure convert_audio_to_wav_16k_mono in app.media_decode converts sources atomically."""
        from app.media_decode import convert_audio_to_wav_16k_mono

        sr = 16000
        t = np.linspace(0, 0.5, int(0.5 * sr), endpoint=False, dtype=np.float32)
        pcm = 0.4 * np.sin(2 * np.pi * 440 * t)

        bio = io.BytesIO()
        sf.write(bio, pcm, sr, format="WAV", subtype="PCM_16")
        wav_bytes = bio.getvalue()

        out_path = os.path.join(self.temp_dir, "converted_mono.wav")
        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            res = convert_audio_to_wav_16k_mono(wav_bytes, out_path)

        self.assertEqual(res, out_path)
        self.assertTrue(os.path.exists(out_path))
        data, out_sr = sf.read(out_path)
        self.assertEqual(out_sr, 16000)
        self.assertEqual(data.ndim, 1)
        self.assertEqual(len(data), len(pcm))
        # Ensure no .part files remain in directory
        part_files = [f for f in os.listdir(self.temp_dir) if ".part" in f]
        self.assertEqual(part_files, [])

    def test_change_pcm_speed_fallback_pitch_preserved(self):
        """Ensure change_pcm_speed fallback to FFmpeg pipe preserves pitch (440 Hz at 2.0x)."""
        from app.audio_mixer import change_pcm_speed

        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
        pcm = np.sin(2 * np.pi * 440.0 * t).astype(np.float32)

        # Force PyAV filter graph to fail to trigger FFmpeg pipe fallback
        with patch("av.filter.Graph", side_effect=RuntimeError("PyAV graph unavailable")):
            out = change_pcm_speed(pcm, sample_rate=sr, speed_ratio=2.0)

        # Output should be roughly half the length
        self.assertAlmostEqual(len(out), len(pcm) // 2, delta=200)

        # Detect dominant frequency using FFT
        fft = np.abs(np.fft.rfft(out))
        freqs = np.fft.rfftfreq(len(out), d=1.0 / sr)
        dom_freq = freqs[np.argmax(fft)]

        # Must be close to 440 Hz (NOT pitch shifted to 880 Hz)
        self.assertAlmostEqual(dom_freq, 440.0, delta=5.0)

    def test_resolve_segment_wav_path_prefers_smartfit_over_base(self):
        """Ensure _resolve_segment_wav_path prioritizes smartfit / speed adjusted files
        over raw base wav recorded in tts_cache_manifest.json.
        """
        import json
        from ui.main_window import VideoTranslatorGUI

        class MockGUI:
            def _resolve_tts_temp_dir(self):
                return self.td

        gui = MockGUI()
        gui.td = self.temp_dir
        gui._resolve_segment_wav_path = VideoTranslatorGUI._resolve_segment_wav_path.__get__(gui)

        manifest_path = os.path.join(self.temp_dir, "tts_cache_manifest.json")
        base_wav = os.path.join(self.temp_dir, "seg_0000_base.wav")
        smart_wav = os.path.join(self.temp_dir, "seg_0000_smartfit.wav")
        with open(base_wav, "w") as f:
            f.write("base")
        with open(smart_wav, "w") as f:
            f.write("smart")

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({"segments": {"0": {"wav_path": base_wav}}}, f)

        # 1. Empty seg dict should resolve smartfit
        seg = {}
        res = gui._resolve_segment_wav_path(seg, 0, tts_dir=self.temp_dir)
        self.assertEqual(res, smart_wav)
        self.assertEqual(seg.get("_wav_path"), smart_wav)

        # 2. Seg dict previously pointing to _base.wav should upgrade to smartfit
        seg2 = {"_wav_path": base_wav}
        res2 = gui._resolve_segment_wav_path(seg2, 0, tts_dir=self.temp_dir)
        self.assertEqual(res2, smart_wav)
        self.assertEqual(seg2.get("_wav_path"), smart_wav)


if __name__ == "__main__":
    unittest.main()


