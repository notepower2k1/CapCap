#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_elevenlabs_tts.py

Unit tests for ElevenLabs TTS engine module:
- Voice catalog fetching and caching behavior
- Error handling (401, 429, empty audio, network error)
- Speech synthesis and speed parameter clamping
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root and app are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.engines.elevenlabs_tts import (
    fetch_elevenlabs_voices,
    synthesize_elevenlabs_tts_wav_16k_mono,
)
from runtime_paths import temp_path


class TestElevenLabsTTS(unittest.TestCase):
    def setUp(self):
        self.cache_file = temp_path("elevenlabs_voices_cache.json")
        if os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
            except OSError:
                pass

    def tearDown(self):
        if os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
            except OSError:
                pass

    @patch("requests.get")
    def test_fetch_voices_success(self, mock_get):
        """Mock GET 200 with 2 voices, assert returns 2 correctly-shaped dicts and writes cache."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "voices": [
                {
                    "voice_id": "21m00Tcm4TlvDq8ikWAM",
                    "name": "Rachel",
                    "labels": {"gender": "female"},
                    "category": "premade",
                },
                {
                    "voice_id": "AZnzlk1XvdvUeBnXmlld",
                    "name": "Domi",
                    "labels": {"gender": "male"},
                    "category": "premade",
                    "preview_url": "https://elevenlabs.io/sample.mp3",
                },
            ]
        }
        mock_get.return_value = mock_resp

        voices = fetch_elevenlabs_voices("test_api_key_123")

        mock_get.assert_any_call(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": "test_api_key_123"},
            timeout=30,
        )
        mock_get.assert_any_call(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": "test_api_key_123"},
            timeout=15,
        )
        self.assertEqual(len(voices), 2)
        expected_v1 = {
            "id": "elevenlabs:21m00Tcm4TlvDq8ikWAM",
            "raw_name": "Rachel",
            "name": "Rachel [Default]",
            "provider": "elevenlabs",
            "provider_voice": "21m00Tcm4TlvDq8ikWAM",
            "language": "multilingual",
            "gender": "female",
            "tier": "free",
            "category": "premade",
            "voice_type": "premade",
            "is_usable_for_api": True,
            "enabled": True,
            "tags": ["elevenlabs", "online"],
            "preview_video_url": "",
            "preview_video_path": "",
            "preview_audio_url": "",
            "preview_audio_path": "",
        }
        expected_v2 = {
            "id": "elevenlabs:AZnzlk1XvdvUeBnXmlld",
            "raw_name": "Domi",
            "name": "Domi [Default]",
            "provider": "elevenlabs",
            "provider_voice": "AZnzlk1XvdvUeBnXmlld",
            "language": "multilingual",
            "gender": "male",
            "tier": "free",
            "category": "premade",
            "voice_type": "premade",
            "is_usable_for_api": True,
            "enabled": True,
            "tags": ["elevenlabs", "online"],
            "preview_video_url": "",
            "preview_video_path": "",
            "preview_audio_url": "https://elevenlabs.io/sample.mp3",
            "preview_audio_path": "",
        }
        self.assertEqual(voices[0], expected_v1)
        self.assertEqual(voices[1], expected_v2)

        # Check cache was saved properly
        self.assertTrue(os.path.isfile(self.cache_file))
        with open(self.cache_file, "r", encoding="utf-8") as f:
            cache_content = json.load(f)
        self.assertIn("fetched_at", cache_content)
        self.assertEqual(cache_content["voices"], voices)

    @patch("requests.get")
    def test_fetch_voices_cache_hit(self, mock_get):
        """Write a valid cache file, assert no HTTP call is made."""
        cached_voices = [
            {
                "id": "elevenlabs:cached_voice_id",
                "name": "Cached Voice",
                "provider": "elevenlabs",
                "provider_voice": "cached_voice_id",
                "language": "multilingual",
                "gender": "any",
                "tier": "premium",
                "enabled": True,
                "tags": ["elevenlabs", "online"],
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
            }
        ]
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time() - 60, "voices": cached_voices}, f)

        voices = fetch_elevenlabs_voices("test_api_key")
        mock_get.assert_not_called()
        self.assertEqual(voices, cached_voices)

    @patch("requests.get")
    def test_fetch_voices_cache_expired(self, mock_get):
        """Write an expired cache (> 3600s), assert HTTP is called and cache refreshed."""
        old_voices = [{"id": "elevenlabs:old", "name": "Old Voice"}]
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time() - 3601, "voices": old_voices}, f)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "voices": [
                {
                    "voice_id": "new_voice_1",
                    "name": "New Voice",
                    "labels": {"gender": "female"},
                }
            ]
        }
        mock_get.return_value = mock_resp

        voices = fetch_elevenlabs_voices("test_api_key")
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(len(voices), 1)
        self.assertEqual(voices[0]["id"], "elevenlabs:new_voice_1")

        with open(self.cache_file, "r", encoding="utf-8") as f:
            cache_content = json.load(f)
        self.assertEqual(cache_content["voices"], voices)

    @patch("requests.get")
    def test_fetch_voices_401(self, mock_get):
        """Mock GET 401, assert RuntimeError with correct Vietnamese message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            fetch_elevenlabs_voices("invalid_api_key")

        self.assertEqual(str(ctx.exception), "ElevenLabs API Key không hợp lệ hoặc đã hết hạn.")

    @patch("requests.get")
    def test_fetch_voices_429(self, mock_get):
        """Mock GET 429, assert RuntimeError with correct Vietnamese message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Rate limited"
        mock_get.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            fetch_elevenlabs_voices("quota_exceeded_key")

        self.assertEqual(str(ctx.exception), "ElevenLabs API: Đã đạt giới hạn quota hoặc rate limit. Vui lòng thử lại sau.")

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("requests.post")
    def test_synthesize_success(self, mock_post, mock_convert):
        """Mock POST 200 with MP3 bytes, assert convert fn is called, wav_path returned."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_mp3_audio_bytes"
        mock_post.return_value = mock_resp

        progress_messages = []
        target_wav = "D:/dummy/path/output.wav"

        result = synthesize_elevenlabs_tts_wav_16k_mono(
            text="Xin chào các bạn",
            wav_path=target_wav,
            voice_id="21m00Tcm4TlvDq8ikWAM",
            api_key="valid_key",
            model_id="eleven_multilingual_v2",
            speed=1.0,
            tmp_dir="D:/dummy/temp",
            on_progress=lambda msg: progress_messages.append(msg),
        )

        self.assertEqual(result, target_wav)
        mock_post.assert_called_once_with(
            "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
            headers={
                "xi-api-key": "valid_key",
                "Content-Type": "application/json",
            },
            params={"output_format": "mp3_44100_128"},
            json={
                "text": "Xin chào các bạn",
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "speed": 1.0,
                },
                "language_code": "vi",
            },
            stream=True,
            timeout=30,
        )
        mock_convert.assert_called_once_with(
            b"fake_mp3_audio_bytes",
            target_wav,
            tmp_dir="D:/dummy/temp",
        )
        self.assertTrue(any("Đang tạo giọng nói" in m for m in progress_messages))

    @patch("requests.post")
    def test_synthesize_401(self, mock_post):
        """Mock POST 401, assert RuntimeError with correct Vietnamese message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized key"
        mock_post.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            synthesize_elevenlabs_tts_wav_16k_mono(
                text="Hello world",
                wav_path="out.wav",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                api_key="bad_key",
            )
        self.assertEqual(str(ctx.exception), "ElevenLabs API Key không hợp lệ hoặc đã hết hạn.")

    @patch("requests.post")
    def test_synthesize_empty_audio(self, mock_post):
        """Mock POST 200 with empty bytes, assert RuntimeError with correct message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        mock_post.return_value = mock_resp

        sample_text = "Thử nghiệm âm thanh rỗng với văn bản dài hơn 40 ký tự để kiểm tra cắt chuỗi"
        with self.assertRaises(RuntimeError) as ctx:
            synthesize_elevenlabs_tts_wav_16k_mono(
                text=sample_text,
                wav_path="out.wav",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                api_key="valid_key",
            )
        expected_msg = f"ElevenLabs trả về audio rỗng cho đoạn text: '{sample_text[:40]}'"
        self.assertEqual(str(ctx.exception), expected_msg)

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("requests.post")
    def test_speed_clamping(self, mock_post, mock_convert):
        """Assert speed < 0.7 is clamped to 0.7, speed > 1.2 is clamped to 1.2."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"audio_bytes"
        mock_post.return_value = mock_resp

        # Test speed below minimum (0.4 -> 0.7)
        synthesize_elevenlabs_tts_wav_16k_mono(
            text="Low speed",
            wav_path="out.wav",
            voice_id="v1",
            api_key="k1",
            speed=0.4,
        )
        _, kwargs_low = mock_post.call_args
        self.assertEqual(kwargs_low["json"]["voice_settings"]["speed"], 0.7)

        # Test speed above maximum (1.8 -> 1.2)
        synthesize_elevenlabs_tts_wav_16k_mono(
            text="High speed",
            wav_path="out.wav",
            voice_id="v1",
            api_key="k1",
            speed=1.8,
        )
        _, kwargs_high = mock_post.call_args
        self.assertEqual(kwargs_high["json"]["voice_settings"]["speed"], 1.2)

        # Test speed within range (0.95 -> 0.95)
        synthesize_elevenlabs_tts_wav_16k_mono(
            text="Normal speed",
            wav_path="out.wav",
            voice_id="v1",
            api_key="k1",
            speed=0.95,
        )
        _, kwargs_mid = mock_post.call_args
        self.assertEqual(kwargs_mid["json"]["voice_settings"]["speed"], 0.95)

    @patch("requests.get")
    def test_fetch_voices_network_error(self, mock_get):
        """Assert network exceptions return empty list without crashing."""
        import requests
        mock_get.side_effect = requests.RequestException("DNS lookup failed")

        voices = fetch_elevenlabs_voices("key")
        self.assertEqual(voices, [])

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("requests.post")
    def test_synthesize_voice_id_with_prefix(self, mock_post, mock_convert):
        """Assert voice_id prefixed with elevenlabs: is cleaned before making API call."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"audio_bytes"
        mock_post.return_value = mock_resp

        synthesize_elevenlabs_tts_wav_16k_mono(
            text="Prefix test",
            wav_path="out.wav",
            voice_id="elevenlabs:21m00Tcm4TlvDq8ikWAM",
            api_key="key",
        )
        args, _ = mock_post.call_args
        self.assertEqual(args[0], "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM")

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("requests.post")
    def test_synthesize_with_explicit_language_code(self, mock_post, mock_convert):
        """Explicit language_code is cleaned and included in payload."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_audio_bytes"
        mock_post.return_value = mock_resp

        synthesize_elevenlabs_tts_wav_16k_mono(
            text="Hello world",
            wav_path="out.wav",
            voice_id="21m00Tcm4TlvDq8ikWAM",
            api_key="valid_key",
            language_code="ja-JP",
        )
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["language_code"], "ja")

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("requests.post")
    def test_synthesize_with_model_id_from_env(self, mock_post, mock_convert):
        """ELEVENLABS_MODEL in env is used when model_id is not explicitly passed."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_audio_bytes"
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_flash_v2_5"}):
            synthesize_elevenlabs_tts_wav_16k_mono(
                text="Test env model",
                wav_path="out.wav",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                api_key="valid_key",
            )
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["model_id"], "eleven_flash_v2_5")

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("requests.post")
    def test_synthesize_ascii_no_language_code(self, mock_post, mock_convert):
        """ASCII text without explicit language or TARGET_LANGUAGE env omits language_code."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_audio_bytes"
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {"TARGET_LANGUAGE": ""}, clear=False):
            synthesize_elevenlabs_tts_wav_16k_mono(
                text="Hello world",
                wav_path="out.wav",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                api_key="valid_key",
            )
        _, kwargs = mock_post.call_args
        self.assertNotIn("language_code", kwargs["json"])


if __name__ == "__main__":
    unittest.main()
