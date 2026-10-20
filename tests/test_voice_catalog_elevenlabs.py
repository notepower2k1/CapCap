#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_voice_catalog_elevenlabs.py

Unit tests for ElevenLabs voices loading in VoiceCatalogService:
1. test_elevenlabs_voices_loaded_when_key_set
2. test_elevenlabs_voices_skipped_when_no_key
3. test_elevenlabs_voices_exception_is_silenced
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root and app directory are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import app.engines.elevenlabs_tts
# Also alias engines.elevenlabs_tts in sys.modules so either import path resolves to the same module
sys.modules["engines.elevenlabs_tts"] = app.engines.elevenlabs_tts

from app.services.voice_catalog_service import VoiceCatalogService


class TestVoiceCatalogElevenLabs(unittest.TestCase):
    def setUp(self):
        self.service = VoiceCatalogService(workspace_root=PROJECT_ROOT)
        self.fake_voices = [
            {
                "id": "elevenlabs:voice_1",
                "name": "Eleven Adam",
                "provider": "elevenlabs",
                "provider_voice": "voice_1",
                "language": "multilingual",
                "gender": "male",
                "tier": "premium",
                "enabled": True,
                "tags": ["elevenlabs", "online"],
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
            },
            {
                "id": "elevenlabs:voice_2",
                "name": "Eleven Rachel",
                "provider": "elevenlabs",
                "provider_voice": "voice_2",
                "language": "multilingual",
                "gender": "female",
                "tier": "premium",
                "enabled": True,
                "tags": ["elevenlabs", "online"],
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
            },
        ]

    @patch("app.engines.elevenlabs_tts.fetch_elevenlabs_voices")
    @patch("os.getenv")
    def test_elevenlabs_voices_loaded_when_key_set(self, mock_getenv, mock_fetch):
        """When ELEVENLABS_API_KEY is configured, voices should be fetched and added to catalog."""
        mock_getenv.side_effect = lambda key, default="": "fake_api_key_123" if key == "ELEVENLABS_API_KEY" else default
        mock_fetch.return_value = self.fake_voices

        result = self.service.load_catalog()

        mock_fetch.assert_called_once_with("fake_api_key_123")
        voice_ids = [v.get("id") for v in result]
        self.assertIn("elevenlabs:voice_1", voice_ids)
        self.assertIn("elevenlabs:voice_2", voice_ids)

        voice_map = {v.get("id"): v for v in result}
        self.assertEqual(voice_map["elevenlabs:voice_1"]["name"], "Eleven Adam")
        self.assertEqual(voice_map["elevenlabs:voice_2"]["name"], "Eleven Rachel")
        self.assertEqual(voice_map["elevenlabs:voice_1"]["provider"], "elevenlabs")

    @patch("app.engines.elevenlabs_tts.fetch_elevenlabs_voices")
    @patch("os.getenv")
    def test_elevenlabs_voices_skipped_when_no_key(self, mock_getenv, mock_fetch):
        """When ELEVENLABS_API_KEY is empty, fetch_elevenlabs_voices should not be called."""
        mock_getenv.side_effect = lambda key, default="": "" if key == "ELEVENLABS_API_KEY" else default

        result = self.service.load_catalog()

        mock_fetch.assert_not_called()
        eleven_ids = [v.get("id") for v in result if str(v.get("id", "")).startswith("elevenlabs:")]
        self.assertEqual(eleven_ids, [])

    @patch("app.engines.elevenlabs_tts.fetch_elevenlabs_voices")
    @patch("os.getenv")
    def test_elevenlabs_voices_skipped_when_whitespace_key(self, mock_getenv, mock_fetch):
        """When ELEVENLABS_API_KEY contains only whitespace, fetch_elevenlabs_voices should not be called."""
        mock_getenv.side_effect = lambda key, default="": "   " if key == "ELEVENLABS_API_KEY" else default

        result = self.service.load_catalog()

        mock_fetch.assert_not_called()
        eleven_ids = [v.get("id") for v in result if str(v.get("id", "")).startswith("elevenlabs:")]
        self.assertEqual(eleven_ids, [])

    @patch("app.engines.elevenlabs_tts.fetch_elevenlabs_voices")
    @patch("os.getenv")
    def test_elevenlabs_voices_exception_is_silenced(self, mock_getenv, mock_fetch):
        """When fetch_elevenlabs_voices raises an exception, load_catalog still succeeds without raising."""
        mock_getenv.side_effect = lambda key, default="": "fake_api_key_123" if key == "ELEVENLABS_API_KEY" else default
        mock_fetch.side_effect = RuntimeError("ElevenLabs API connection timeout")

        # Must not raise
        result = self.service.load_catalog()
        self.assertIsInstance(result, list)
        eleven_ids = [v.get("id") for v in result if str(v.get("id", "")).startswith("elevenlabs:")]
        self.assertEqual(eleven_ids, [])

    @patch("app.engines.elevenlabs_tts.fetch_elevenlabs_voices")
    @patch("app.engines.elevenlabs_tts.read_elevenlabs_voice_cache")
    @patch("os.getenv")
    def test_elevenlabs_offline_load_uses_cache_without_network(self, mock_getenv, mock_read_cache, mock_fetch):
        """UI-thread catalog loading can read cached voices without making API calls."""
        mock_getenv.side_effect = lambda key, default="": "cached_key" if key == "ELEVENLABS_API_KEY" else default
        mock_read_cache.return_value = {"voices": self.fake_voices}

        result = self.service.load_catalog(allow_elevenlabs_network=False)

        mock_read_cache.assert_called_once_with("cached_key")
        mock_fetch.assert_not_called()
        eleven_ids = [v.get("id") for v in result if str(v.get("id", "")).startswith("elevenlabs:")]
        self.assertEqual(eleven_ids, ["elevenlabs:voice_1", "elevenlabs:voice_2"])


if __name__ == "__main__":
    unittest.main()
