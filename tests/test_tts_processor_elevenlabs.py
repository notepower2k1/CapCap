#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_tts_processor_elevenlabs.py

Unit tests for ElevenLabs provider dispatch in tts_processor.py:
1. Fast path for voice starting with 'elevenlabs:'
2. Fast path error when ELEVENLABS_API_KEY is not configured
3. Catalog lookup path when provider is 'elevenlabs'
4. Catalog lookup error when ELEVENLABS_API_KEY is not configured
5. Catalog lookup with unsupported provider includes 'elevenlabs' in error message
6. Preload voice returns False for ElevenLabs voices
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Ensure project root and app directory are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import app.engines.elevenlabs_tts
sys.modules["engines.elevenlabs_tts"] = app.engines.elevenlabs_tts

import app.tts_processor as tts_processor


class TestTTSProcessorElevenLabs(unittest.TestCase):
    @patch("app.engines.elevenlabs_tts.synthesize_elevenlabs_tts_wav_16k_mono")
    @patch("os.getenv")
    def test_fast_path_elevenlabs_voice(self, mock_getenv, mock_synthesize):
        """voice='elevenlabs:some_id' should trigger fast path and strip prefix."""
        mock_getenv.side_effect = lambda key, default="": "test_api_key_123" if key == "ELEVENLABS_API_KEY" else default
        mock_synthesize.return_value = "D:/dummy/output.wav"

        progress_callback = MagicMock()
        result = tts_processor.synthesize_text_to_wav_16k_mono(
            text="Xin chào ElevenLabs",
            wav_path="D:/dummy/output.wav",
            voice="elevenlabs:21m00Tcm4TlvDq8ikWAM",
            speed=1.05,
            tmp_dir="D:/dummy/tmp",
            on_progress=progress_callback,
        )

        self.assertEqual(result, "D:/dummy/output.wav")
        mock_synthesize.assert_called_once_with(
            text="Xin chào ElevenLabs",
            wav_path="D:/dummy/output.wav",
            voice_id="21m00Tcm4TlvDq8ikWAM",
            api_key="test_api_key_123",
            model_id="eleven_multilingual_v2",
            language_code=None,
            speed=1.05,
            tmp_dir="D:/dummy/tmp",
            on_progress=progress_callback,
        )

    @patch("os.getenv")
    def test_fast_path_no_api_key(self, mock_getenv):
        """voice='elevenlabs:some_id' without API key should raise RuntimeError."""
        mock_getenv.side_effect = lambda key, default="": "" if key == "ELEVENLABS_API_KEY" else default

        with self.assertRaises(RuntimeError) as ctx:
            tts_processor.synthesize_text_to_wav_16k_mono(
                text="Xin chào",
                wav_path="D:/dummy/output.wav",
                voice="elevenlabs:some_id",
            )
        self.assertIn("ElevenLabs API Key chưa được cấu hình", str(ctx.exception))

        # Also test with whitespace-only key
        mock_getenv.side_effect = lambda key, default="": "   " if key == "ELEVENLABS_API_KEY" else default
        with self.assertRaises(RuntimeError) as ctx:
            tts_processor.synthesize_text_to_wav_16k_mono(
                text="Xin chào",
                wav_path="D:/dummy/output.wav",
                voice="elevenlabs:some_id",
            )
        self.assertIn("ElevenLabs API Key chưa được cấu hình", str(ctx.exception))

    @patch("app.engines.elevenlabs_tts.synthesize_elevenlabs_tts_wav_16k_mono")
    @patch("app.tts_processor.json.load")
    @patch("builtins.open", mock_open())
    @patch("os.getenv")
    def test_catalog_path_elevenlabs_provider(self, mock_getenv, mock_json_load, mock_synthesize):
        """Catalog entry with provider='elevenlabs' dispatches to synthesize_elevenlabs_tts_wav_16k_mono."""
        mock_getenv.side_effect = lambda key, default="": "test_api_key_abc" if key == "ELEVENLABS_API_KEY" else default
        mock_synthesize.return_value = "D:/dummy/catalog_output.wav"

        mock_json_load.return_value = {
            "voices": [
                {
                    "id": "eleven_custom_voice",
                    "name": "Eleven Custom",
                    "provider": "elevenlabs",
                    "provider_voice": "provider_voice_999",
                }
            ]
        }

        progress_callback = MagicMock()
        result = tts_processor.synthesize_text_to_wav_16k_mono(
            text="Kiểm tra catalog elevenlabs",
            wav_path="D:/dummy/catalog_output.wav",
            voice="eleven_custom_voice",
            speed=0.9,
            tmp_dir="D:/dummy/tmp",
            on_progress=progress_callback,
        )

        self.assertEqual(result, "D:/dummy/catalog_output.wav")
        mock_synthesize.assert_called_once_with(
            text="Kiểm tra catalog elevenlabs",
            wav_path="D:/dummy/catalog_output.wav",
            voice_id="provider_voice_999",
            api_key="test_api_key_abc",
            model_id="eleven_multilingual_v2",
            language_code=None,
            speed=0.9,
            tmp_dir="D:/dummy/tmp",
            on_progress=progress_callback,
        )

    @patch("app.tts_processor.json.load")
    @patch("builtins.open", mock_open())
    @patch("os.getenv")
    def test_catalog_path_no_api_key(self, mock_getenv, mock_json_load):
        """Catalog entry with provider='elevenlabs' without API key should raise RuntimeError."""
        mock_getenv.side_effect = lambda key, default="": "" if key == "ELEVENLABS_API_KEY" else default

        mock_json_load.return_value = {
            "voices": [
                {
                    "id": "eleven_custom_voice",
                    "name": "Eleven Custom",
                    "provider": "elevenlabs",
                    "provider_voice": "provider_voice_999",
                }
            ]
        }

        with self.assertRaises(RuntimeError) as ctx:
            tts_processor.synthesize_text_to_wav_16k_mono(
                text="Kiểm tra catalog thiếu key",
                wav_path="D:/dummy/output.wav",
                voice="eleven_custom_voice",
            )
        self.assertIn("ElevenLabs API Key chưa được cấu hình", str(ctx.exception))

    @patch("app.tts_processor.json.load")
    @patch("builtins.open", mock_open())
    @patch("os.getenv")
    def test_unsupported_provider_error_message(self, mock_getenv, mock_json_load):
        """Unsupported provider error message should list 'elevenlabs'."""
        mock_getenv.side_effect = lambda key, default="": default

        mock_json_load.return_value = {
            "voices": [
                {
                    "id": "unknown_voice",
                    "name": "Unknown",
                    "provider": "unsupported_provider_xyz",
                    "provider_voice": "xyz",
                }
            ]
        }

        with self.assertRaises(ValueError) as ctx:
            tts_processor.synthesize_text_to_wav_16k_mono(
                text="Test unknown provider",
                wav_path="D:/dummy/output.wav",
                voice="unknown_voice",
            )
        self.assertIn("Only 'piper', 'edge', 'vieneu', and 'elevenlabs' are supported", str(ctx.exception))

    @patch("app.tts_processor.json.load")
    @patch("builtins.open", mock_open())
    def test_preload_tts_voice_elevenlabs(self, mock_json_load):
        """preload_tts_voice returns False for elevenlabs voices (no preload needed)."""
        mock_json_load.return_value = {
            "voices": [
                {
                    "id": "eleven_custom_voice",
                    "name": "Eleven Custom",
                    "provider": "elevenlabs",
                    "provider_voice": "provider_voice_999",
                }
            ]
        }

        # Test catalog-resolved elevenlabs voice
        result = tts_processor.preload_tts_voice("eleven_custom_voice")
        self.assertFalse(result)

        # Test fast-path format (unlisted or prefix format)
        result_fast = tts_processor.preload_tts_voice("elevenlabs:some_voice")
        self.assertFalse(result_fast)

    @patch("app.tts_processor.piper_tts_to_wav_16k_mono")
    @patch("app.tts_processor._resolve_piper_model_path")
    @patch("app.tts_processor.os.path.exists")
    @patch("app.tts_processor.json.load")
    @patch("builtins.open", mock_open())
    def test_synthesize_piper_voice_does_not_raise_unbound_local_os(
        self, mock_json_load, mock_os_exists, mock_resolve_model, mock_piper_tts
    ):
        """synthesize_text_to_wav_16k_mono with piper voice should not raise UnboundLocalError for os."""
        mock_json_load.return_value = {
            "voices": [
                {
                    "id": "ngochuyen",
                    "name": "Ngọc Huyền",
                    "provider": "piper",
                    "provider_voice": "vi_VN-25hours-single-low.onnx",
                    "language": "vi",
                }
            ]
        }
        mock_resolve_model.return_value = "D:/dummy/models/piper/vi_VN-25hours-single-low.onnx"
        mock_os_exists.return_value = True
        mock_piper_tts.return_value = "D:/dummy/output.wav"

        result = tts_processor.synthesize_text_to_wav_16k_mono(
            text="Xin chào",
            wav_path="D:/dummy/output.wav",
            voice="ngochuyen",
        )
        self.assertEqual(result, "D:/dummy/output.wav")
        mock_piper_tts.assert_called_once()

    @patch("app.engines.elevenlabs_tts.synthesize_elevenlabs_tts_wav_16k_mono")
    @patch("os.getenv")
    def test_synthesize_forwards_model_id_and_language_code(self, mock_getenv, mock_synthesize):
        """synthesize_text_to_wav_16k_mono forwards explicit model_id and language_code to elevenlabs engine."""
        mock_getenv.side_effect = lambda key, default="": "test_api_key_123" if key == "ELEVENLABS_API_KEY" else default
        mock_synthesize.return_value = "D:/dummy/output.wav"

        result = tts_processor.synthesize_text_to_wav_16k_mono(
            text="Hello world",
            wav_path="D:/dummy/output.wav",
            voice="elevenlabs:21m00Tcm4TlvDq8ikWAM",
            model_id="eleven_turbo_v2_5",
            language_code="en",
        )

        self.assertEqual(result, "D:/dummy/output.wav")
        mock_synthesize.assert_called_once_with(
            text="Hello world",
            wav_path="D:/dummy/output.wav",
            voice_id="21m00Tcm4TlvDq8ikWAM",
            api_key="test_api_key_123",
            model_id="eleven_turbo_v2_5",
            language_code="en",
            speed=1.0,
            tmp_dir=None,
            on_progress=None,
        )


if __name__ == "__main__":
    unittest.main()

