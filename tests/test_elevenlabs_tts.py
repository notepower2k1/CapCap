#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_elevenlabs_tts.py

Unit tests for ElevenLabs TTS engine module:
- Voice catalog fetching and caching behavior
- Error handling (401, 429, empty audio, network error)
- Speech synthesis and speed parameter clamping
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root and app are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.engines.elevenlabs_tts import (
    DEFAULT_ELEVENLABS_MODELS,
    fetch_elevenlabs_voice_catalog,
    fetch_elevenlabs_models,
    fetch_elevenlabs_voices,
    read_elevenlabs_voice_cache,
    synthesize_elevenlabs_tts_wav_16k_mono,
    write_elevenlabs_voice_cache,
)


class TestElevenLabsTTS(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.TemporaryDirectory()
        self.cache_file = os.path.join(self.cache_dir.name, "elevenlabs_voices_cache.json")
        self.cache_path_patcher = patch(
            "app.engines.elevenlabs_tts._resolve_temp_path",
            side_effect=lambda *parts: os.path.join(self.cache_dir.name, *parts),
        )
        self.cache_path_patcher.start()

    def tearDown(self):
        self.cache_path_patcher.stop()
        self.cache_dir.cleanup()

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
        self.assertEqual(cache_content["key_fingerprint"], hashlib.sha256(b"test_api_key_123").hexdigest())
        self.assertNotIn("test_api_key_123", json.dumps(cache_content))
        self.assertEqual(read_elevenlabs_voice_cache(" test_api_key_123 ")["voices"], voices)

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
            json.dump({
                "fetched_at": time.time() - 60,
                "user_subscription": {"tier": "creator"},
                "voices": cached_voices,
                "key_fingerprint": hashlib.sha256(b"test_api_key").hexdigest(),
            }, f)

        voices = fetch_elevenlabs_voices("test_api_key")
        mock_get.assert_not_called()
        self.assertEqual(voices, cached_voices)

    @patch("requests.get")
    def test_fetch_voices_cache_expired(self, mock_get):
        """Write an expired cache (> 3600s), assert HTTP is called and cache refreshed."""
        old_voices = [{"id": "elevenlabs:old", "name": "Old Voice"}]
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump({
                "fetched_at": time.time() - 3601,
                "user_subscription": {"tier": "creator"},
                "voices": old_voices,
                "key_fingerprint": hashlib.sha256(b"test_api_key").hexdigest(),
            }, f)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tier": "free",
            "voices": [
                {
                    "voice_id": "new_voice_1",
                    "name": "New Voice",
                    "labels": {"gender": "female"},
                }
            ],
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
            model_id="eleven_turbo_v2_5",
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
                "model_id": "eleven_turbo_v2_5",
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

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("requests.post")
    def test_synthesize_auto_switches_multilingual_v2_for_vietnamese(self, mock_post, mock_convert):
        """eleven_multilingual_v2 auto-switches to eleven_turbo_v2_5 when text has Vietnamese chars."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_mp3_audio_bytes"
        mock_post.return_value = mock_resp

        synthesize_elevenlabs_tts_wav_16k_mono(
            text="Xin chào các bạn",
            wav_path="out.wav",
            voice_id="21m00Tcm4TlvDq8ikWAM",
            api_key="valid_key",
            model_id="eleven_multilingual_v2",
        )
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["model_id"], "eleven_turbo_v2_5")
        self.assertEqual(kwargs["json"]["language_code"], "vi")

    @patch("requests.post")
    def test_synthesize_400_unsupported_language(self, mock_post):
        """Mock POST 400 with unsupported_language, assert clear Vietnamese RuntimeError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = '{"detail":{"status":"unsupported_language","message":"Model \'eleven_multilingual_v2\' does not support language_code \'vi\'."}}'
        mock_post.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            synthesize_elevenlabs_tts_wav_16k_mono(
                text="Xin chào",
                wav_path="out.wav",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                api_key="valid_key",
                model_id="some_model_without_vi",
                language_code="vi",
            )
        err_msg = str(ctx.exception)
        self.assertIn("Lỗi 400 - Model không hỗ trợ ngôn ngữ", err_msg)
        self.assertIn("eleven_turbo_v2_5", err_msg)
        self.assertIn("eleven_flash_v2_5", err_msg)

    @patch("requests.get")
    def test_fetch_elevenlabs_models_filters_multilingual_v2_and_non_tts(self, mock_get):
        """fetch_elevenlabs_models excludes eleven_multilingual_v2 and non-TTS models."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "model_id": "eleven_multilingual_v2",
                "name": "Eleven Multilingual v2",
                "can_do_text_to_speech": True,
            },
            {
                "model_id": "non_tts_model",
                "name": "Non TTS Model",
                "can_do_text_to_speech": False,
            },
            {
                "model_id": "eleven_v4",
                "name": "Eleven v4",
                "can_do_text_to_speech": True,
            },
            {
                "model_id": "eleven_turbo_v2_5",
                "name": "Turbo v2.5",
                "can_do_text_to_speech": True,
            },
        ]
        mock_get.return_value = mock_resp

        models = fetch_elevenlabs_models(api_key="test_key_123")
        mock_get.assert_called_once_with(
            "https://api.elevenlabs.io/v1/models",
            headers={"xi-api-key": "test_key_123"},
            timeout=15,
        )
        model_ids = [m["model_id"] for m in models]
        self.assertNotIn("eleven_multilingual_v2", model_ids)
        self.assertNotIn("non_tts_model", model_ids)
        self.assertIn("eleven_v4", model_ids)
        self.assertIn("eleven_turbo_v2_5", model_ids)

    @patch("requests.get")
    def test_fetch_elevenlabs_models_network_error_fallback(self, mock_get):
        """fetch_elevenlabs_models falls back to DEFAULT_ELEVENLABS_MODELS on network error or HTTP failure."""
        mock_get.side_effect = Exception("Connection error")
        models = fetch_elevenlabs_models(api_key="test_key")
        self.assertEqual(models, DEFAULT_ELEVENLABS_MODELS)

        mock_get.side_effect = None
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        models_500 = fetch_elevenlabs_models(api_key="test_key")
        self.assertEqual(models_500, DEFAULT_ELEVENLABS_MODELS)

    @patch("app.engines.elevenlabs_tts.convert_audio_data_to_wav_16k_mono")
    @patch("requests.post")
    def test_synthesize_auto_switches_multilingual_v2_unconditionally(self, mock_post, mock_convert):
        """eleven_multilingual_v2 auto-switches to eleven_turbo_v2_5 even for non-Vietnamese text."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_mp3_audio_bytes"
        mock_post.return_value = mock_resp

        synthesize_elevenlabs_tts_wav_16k_mono(
            text="Hello world",
            wav_path="out.wav",
            voice_id="21m00Tcm4TlvDq8ikWAM",
            api_key="valid_key",
            model_id="eleven_multilingual_v2",
        )
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["model_id"], "eleven_turbo_v2_5")

    @patch("requests.get")
    def test_cache_is_bound_to_api_key_and_legacy_cache_is_rejected(self, mock_get):
        """Only a fresh cache with a matching key fingerprint may supply account voices."""
        old_cache = {
            "fetched_at": time.time(),
            "user_subscription": {"tier": "creator"},
            "voices": [{"id": "elevenlabs:private_old_account"}],
            "key_fingerprint": hashlib.sha256(b"old_account_key").hexdigest(),
        }
        with open(self.cache_file, "w", encoding="utf-8") as cache_file:
            json.dump(old_cache, cache_file)

        self.assertEqual(read_elevenlabs_voice_cache("new_account_key"), {})

        def fake_get(url, *args, **kwargs):
            response = MagicMock()
            response.status_code = 200
            if url.endswith("/v1/voices"):
                response.json.return_value = {
                    "voices": [{"voice_id": "new_account_voice", "name": "New Account Voice"}]
                }
            else:
                response.json.return_value = {"tier": "free", "character_count": 0, "character_limit": 10000}
            return response

        mock_get.side_effect = fake_get
        voices = fetch_elevenlabs_voices("new_account_key")
        self.assertEqual([voice["provider_voice"] for voice in voices], ["new_account_voice"])
        self.assertEqual(mock_get.call_count, 2)
        with open(self.cache_file, "r", encoding="utf-8") as cache_file:
            updated_cache_text = cache_file.read()
        self.assertNotIn("new_account_key", updated_cache_text)
        self.assertNotIn("old_account_key", updated_cache_text)

        # A legacy timestamp-only cache is also a miss and cannot be returned.
        with open(self.cache_file, "w", encoding="utf-8") as cache_file:
            json.dump({"fetched_at": time.time(), "voices": [{"id": "legacy_private_voice"}]}, cache_file)
        self.assertEqual(read_elevenlabs_voice_cache("new_account_key"), {})

    @patch("requests.get")
    def test_staged_catalog_does_not_replace_active_cache_until_committed(self, mock_get):
        """Settings can validate a draft key, then commit its exact result after Save."""
        active_payload = {
            "fetched_at": time.time(),
            "user_subscription": {"tier": "creator"},
            "voices": [{"id": "elevenlabs:active_voice"}],
            "key_fingerprint": hashlib.sha256(b"active_key").hexdigest(),
        }
        self.assertTrue(write_elevenlabs_voice_cache("active_key", active_payload))

        def fake_get(url, *args, **kwargs):
            response = MagicMock()
            response.status_code = 200
            if url.endswith("/v1/voices"):
                response.json.return_value = {
                    "voices": [{"voice_id": "draft_voice", "name": "Draft Voice"}]
                }
            else:
                response.json.return_value = {
                    "tier": "starter",
                    "character_count": 23,
                    "character_limit": 30000,
                    "status": "active",
                }
            return response

        mock_get.side_effect = fake_get
        draft_payload = fetch_elevenlabs_voice_catalog(
            "draft_key", force_refresh=True, write_cache=False
        )

        self.assertEqual(len(draft_payload["voices"]), 1)
        self.assertEqual(draft_payload["user_subscription"]["tier"], "starter")
        self.assertEqual(read_elevenlabs_voice_cache("active_key")["voices"], active_payload["voices"])
        self.assertEqual(read_elevenlabs_voice_cache("draft_key"), {})
        self.assertEqual(mock_get.call_count, 2)

        self.assertTrue(write_elevenlabs_voice_cache("draft_key", draft_payload))
        self.assertEqual(read_elevenlabs_voice_cache("active_key"), {})
        self.assertEqual(read_elevenlabs_voice_cache("draft_key"), draft_payload)

    @patch("requests.get")
    def test_catalog_fetch_errors_are_distinct_from_an_empty_catalog(self, mock_get):
        """Malformed, failed, and offline requests raise; an explicit empty catalog succeeds."""
        import requests

        response = MagicMock()
        response.status_code = 500
        response.text = "server unavailable"
        cases = [
            (requests.RequestException("offline"), "network error"),
            (response, "HTTP error 500"),
        ]
        for result, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                mock_get.side_effect = result if isinstance(result, Exception) else None
                mock_get.return_value = result if not isinstance(result, Exception) else None
                with self.assertRaisesRegex(RuntimeError, expected_message):
                    fetch_elevenlabs_voice_catalog("draft_key", force_refresh=True, write_cache=False)

        bad_json = MagicMock()
        bad_json.status_code = 200
        bad_json.json.side_effect = ValueError("invalid JSON")
        mock_get.side_effect = None
        mock_get.return_value = bad_json
        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            fetch_elevenlabs_voice_catalog("draft_key", force_refresh=True, write_cache=False)

        malformed = MagicMock()
        malformed.status_code = 200
        malformed.json.return_value = {"unexpected": []}
        mock_get.return_value = malformed
        with self.assertRaisesRegex(RuntimeError, "missing a voices list"):
            fetch_elevenlabs_voice_catalog("draft_key", force_refresh=True, write_cache=False)

        empty_catalog = MagicMock()
        empty_catalog.status_code = 200
        empty_catalog.json.return_value = {"voices": []}
        subscription = MagicMock()
        subscription.status_code = 200
        subscription.json.return_value = {"tier": "free"}
        mock_get.side_effect = lambda url, *args, **kwargs: (
            empty_catalog if url.endswith("/v1/voices") else subscription
        )
        payload = fetch_elevenlabs_voice_catalog("draft_key", force_refresh=True, write_cache=False)
        self.assertEqual(payload["voices"], [])
        self.assertEqual(payload["user_subscription"]["tier"], "free")


if __name__ == "__main__":
    unittest.main()
