#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_elevenlabs_concurrency.py

Unit tests for ElevenLabs concurrency limits and retry logic:
- Worker count capping for elevenlabs in voice workflow (max 2 by default, configurable).
- 429 retry success on transient rate limit with backoff.
- 429 fatal error after max retries exhausted.
- Transient network error retry and fatal handling.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.engines.elevenlabs_tts import synthesize_elevenlabs_tts_wav_16k_mono
from app.workflows.voice_workflow import VoiceWorkflow


class TestElevenLabsConcurrency(unittest.TestCase):
    """Test suite for ElevenLabs concurrency limits and retry mechanisms."""

    def test_elevenlabs_worker_count_capped_default(self):
        """Verify worker count is capped to max 2 by default when elevenlabs is used."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wf = VoiceWorkflow(tmp_dir)
            segments = [
                {
                    "text": f"Segment {i}",
                    "start": float(i),
                    "end": float(i + 1),
                    "voice_name": "elevenlabs:21m00Tcm4TlvDq8ikWAM",
                }
                for i in range(5)
            ]

            with patch.dict(os.environ, {}, clear=False):
                if "ELEVENLABS_TTS_WORKERS" in os.environ:
                    del os.environ["ELEVENLABS_TTS_WORKERS"]
                with patch("app.workflows.voice_workflow.ThreadPoolExecutor") as mock_tpe, \
                     patch("app.workflows.voice_workflow.as_completed", return_value=[]):
                    wf._synthesize_segment_wavs(
                        segments=segments,
                        tmp_dir=tmp_dir,
                        voice_name="elevenlabs:21m00Tcm4TlvDq8ikWAM",
                        log=False,
                    )
                    mock_tpe.assert_called_once_with(max_workers=2)

    def test_elevenlabs_worker_count_configurable_via_env(self):
        """Verify worker count respects ELEVENLABS_TTS_WORKERS and upper bound of 4."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wf = VoiceWorkflow(tmp_dir)
            segments = [
                {
                    "text": f"Segment {i}",
                    "start": float(i),
                    "end": float(i + 1),
                    "voice_name": "elevenlabs:21m00Tcm4TlvDq8ikWAM",
                }
                for i in range(6)
            ]

            # Configured to 3 workers
            with patch.dict(os.environ, {"ELEVENLABS_TTS_WORKERS": "3"}):
                with patch("app.workflows.voice_workflow.ThreadPoolExecutor") as mock_tpe, \
                     patch("app.workflows.voice_workflow.as_completed", return_value=[]):
                    wf._synthesize_segment_wavs(
                        segments=segments,
                        tmp_dir=tmp_dir,
                        voice_name="elevenlabs:21m00Tcm4TlvDq8ikWAM",
                        log=False,
                    )
                    mock_tpe.assert_called_once_with(max_workers=3)

            # Value > 4 is clamped to 4
            with patch.dict(os.environ, {"ELEVENLABS_TTS_WORKERS": "10"}):
                with patch("app.workflows.voice_workflow.ThreadPoolExecutor") as mock_tpe, \
                     patch("app.workflows.voice_workflow.as_completed", return_value=[]):
                    wf._synthesize_segment_wavs(
                        segments=segments,
                        tmp_dir=tmp_dir,
                        voice_name="elevenlabs:21m00Tcm4TlvDq8ikWAM",
                        log=False,
                    )
                    mock_tpe.assert_called_once_with(max_workers=4)

            # Invalid string falls back to default 2
            with patch.dict(os.environ, {"ELEVENLABS_TTS_WORKERS": "invalid_number"}):
                with patch("app.workflows.voice_workflow.ThreadPoolExecutor") as mock_tpe, \
                     patch("app.workflows.voice_workflow.as_completed", return_value=[]):
                    wf._synthesize_segment_wavs(
                        segments=segments,
                        tmp_dir=tmp_dir,
                        voice_name="elevenlabs:21m00Tcm4TlvDq8ikWAM",
                        log=False,
                    )
                    mock_tpe.assert_called_once_with(max_workers=2)

    def test_elevenlabs_worker_count_single_job(self):
        """Verify worker count is 1 when only 1 pending job exists."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wf = VoiceWorkflow(tmp_dir)
            segments = [
                {
                    "text": "Single segment",
                    "start": 0.0,
                    "end": 2.0,
                    "voice_name": "elevenlabs:21m00Tcm4TlvDq8ikWAM",
                }
            ]

            with patch("app.workflows.voice_workflow.ThreadPoolExecutor") as mock_tpe, \
                 patch("app.workflows.voice_workflow.as_completed", return_value=[]):
                wf._synthesize_segment_wavs(
                    segments=segments,
                    tmp_dir=tmp_dir,
                    voice_name="elevenlabs:21m00Tcm4TlvDq8ikWAM",
                    log=False,
                )
                mock_tpe.assert_called_once_with(max_workers=1)

    def test_elevenlabs_mixed_provider_capping(self):
        """Verify when elevenlabs is among pending providers, worker count is capped to 2."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wf = VoiceWorkflow(tmp_dir)
            segments = [
                {
                    "text": "Segment 1",
                    "start": 0.0,
                    "end": 1.0,
                    "voice_name": "elevenlabs:21m00Tcm4TlvDq8ikWAM",
                },
                {
                    "text": "Segment 2",
                    "start": 1.0,
                    "end": 2.0,
                    "voice_name": "capcut:female_vietnamese",
                },
                {
                    "text": "Segment 3",
                    "start": 2.0,
                    "end": 3.0,
                    "voice_name": "edge:vi-VN-HoaiMyNeural",
                },
            ]

            with patch("app.workflows.voice_workflow.ThreadPoolExecutor") as mock_tpe, \
                 patch("app.workflows.voice_workflow.as_completed", return_value=[]):
                wf._synthesize_segment_wavs(
                    segments=segments,
                    tmp_dir=tmp_dir,
                    voice_name="elevenlabs:21m00Tcm4TlvDq8ikWAM",
                    log=False,
                )
                mock_tpe.assert_called_once_with(max_workers=2)

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("time.sleep")
    @patch("requests.post")
    def test_synthesize_retries_on_429(self, mock_post, mock_sleep, mock_convert):
        """Mock 429 on first attempt, 200 on second attempt; assert retry succeeds with backoff."""
        resp_429 = MagicMock()
        resp_429.status_code = 429

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.content = b"audio_payload_bytes"

        mock_post.side_effect = [resp_429, resp_200]

        target_wav = "D:/dummy/out.wav"
        result = synthesize_elevenlabs_tts_wav_16k_mono(
            text="Testing 429 retry",
            wav_path=target_wav,
            voice_id="21m00Tcm4TlvDq8ikWAM",
            api_key="valid_api_key",
        )

        self.assertEqual(result, target_wav)
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(1.5)
        mock_convert.assert_called_once_with(b"audio_payload_bytes", target_wav, tmp_dir=None)

    @patch("time.sleep")
    @patch("requests.post")
    def test_synthesize_fails_after_max_retries_on_429(self, mock_post, mock_sleep):
        """Mock 429 continuously; assert RuntimeError is raised after 3 attempts with proper backoff."""
        resp_429 = MagicMock()
        resp_429.status_code = 429

        mock_post.return_value = resp_429

        with self.assertRaises(RuntimeError) as ctx:
            synthesize_elevenlabs_tts_wav_16k_mono(
                text="Testing 429 exhaustion",
                wav_path="out.wav",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                api_key="valid_api_key",
            )

        self.assertIn("giới hạn quota hoặc rate limit", str(ctx.exception))
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_has_calls([call(1.5), call(3.0)])

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("time.sleep")
    @patch("requests.post")
    def test_synthesize_retries_on_network_error(self, mock_post, mock_sleep, mock_convert):
        """Mock network error on first attempt, 200 on second attempt; assert retry succeeds."""
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.content = b"audio_payload_bytes"

        mock_post.side_effect = [requests.RequestException("Temporary drop"), resp_200]

        target_wav = "D:/dummy/out.wav"
        result = synthesize_elevenlabs_tts_wav_16k_mono(
            text="Testing network retry",
            wav_path=target_wav,
            voice_id="21m00Tcm4TlvDq8ikWAM",
            api_key="valid_api_key",
        )

        self.assertEqual(result, target_wav)
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(1.0)
        mock_convert.assert_called_once_with(b"audio_payload_bytes", target_wav, tmp_dir=None)

    @patch("time.sleep")
    @patch("requests.post")
    def test_synthesize_fails_after_max_retries_on_network_error(self, mock_post, mock_sleep):
        """Mock network error continuously; assert RuntimeError is raised after 3 attempts."""
        mock_post.side_effect = requests.RequestException("Fatal connection failure")

        with self.assertRaises(RuntimeError) as ctx:
            synthesize_elevenlabs_tts_wav_16k_mono(
                text="Testing network exhaustion",
                wav_path="out.wav",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                api_key="valid_api_key",
            )

        self.assertIn("ElevenLabs API network error", str(ctx.exception))
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_has_calls([call(1.0), call(2.0)])


if __name__ == "__main__":
    unittest.main()
