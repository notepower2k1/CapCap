#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_elevenlabs_tier.py

Unit tests for ElevenLabs subscription tier detection, voice classification,
and 402 library voice restriction handling.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root and app directory are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.engines.elevenlabs_tts import (
    fetch_elevenlabs_user_subscription,
    fetch_elevenlabs_voices,
    synthesize_elevenlabs_tts_wav_16k_mono,
)
from runtime_paths import temp_path
from ui.main_window import VideoTranslatorGUI


class TestElevenLabsTier(unittest.TestCase):
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
    def test_fetch_subscription_success(self, mock_get):
        """Mocks GET /v1/user/subscription and asserts tier, character_count, and character_limit."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tier": "creator",
            "character_count": 15420,
            "character_limit": 100000,
            "status": "active",
        }
        mock_get.return_value = mock_resp

        sub = fetch_elevenlabs_user_subscription("test_key")

        mock_get.assert_called_once_with(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": "test_key"},
            timeout=15,
        )
        self.assertEqual(sub.get("tier"), "creator")
        self.assertEqual(sub.get("character_count"), 15420)
        self.assertEqual(sub.get("character_limit"), 100000)
        self.assertEqual(sub.get("status"), "active")

    @patch("requests.get")
    def test_fetch_subscription_failure(self, mock_get):
        """Failure returns empty dict without throwing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        sub = fetch_elevenlabs_user_subscription("bad_key")
        self.assertEqual(sub, {})

    @patch("requests.get")
    def test_voices_classified_for_free_tier(self, mock_get):
        """Mocks premade, cloned, and community voices under Free tier; verifies classification."""
        def fake_get(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "subscription" in url:
                resp.json.return_value = {
                    "tier": "free",
                    "character_count": 2300,
                    "character_limit": 10000,
                    "status": "active",
                }
            elif "voices" in url:
                resp.json.return_value = {
                    "voices": [
                        {
                            "voice_id": "v_premade",
                            "name": "Rachel",
                            "category": "premade",
                            "sharing": None,
                        },
                        {
                            "voice_id": "v_cloned",
                            "name": "My Clone Voice",
                            "category": "cloned",
                            "sharing": None,
                        },
                        {
                            "voice_id": "v_generated",
                            "name": "My Design Voice",
                            "category": "generated",
                            "sharing": None,
                        },
                        {
                            "voice_id": "v_community",
                            "name": "MC The Hao",
                            "category": "community",
                            "sharing": {"public_owner_id": "creator_123"},
                        },
                    ]
                }
            return resp

        mock_get.side_effect = fake_get

        voices = fetch_elevenlabs_voices("test_free_key", force_refresh=True)

        voice_map = {v["provider_voice"]: v for v in voices}

        # Premade -> [Default], tier=free, is_usable_for_api=True
        self.assertIn("Rachel [Default]", voice_map["v_premade"]["name"])
        self.assertEqual(voice_map["v_premade"]["tier"], "free")
        self.assertTrue(voice_map["v_premade"]["is_usable_for_api"])

        # Cloned -> [Clone], tier=cloned, is_usable_for_api=True
        self.assertIn("My Clone Voice [Clone]", voice_map["v_cloned"]["name"])
        self.assertEqual(voice_map["v_cloned"]["tier"], "cloned")
        self.assertTrue(voice_map["v_cloned"]["is_usable_for_api"])

        # Generated -> [Design], tier=generated, is_usable_for_api=True
        self.assertIn("My Design Voice [Design]", voice_map["v_generated"]["name"])
        self.assertEqual(voice_map["v_generated"]["tier"], "generated")
        self.assertTrue(voice_map["v_generated"]["is_usable_for_api"])

        # Community voice on Free tier -> 🔒 (Paid Plan Required), tier=paid_only, is_usable_for_api=False
        self.assertIn("MC The Hao 🔒 (Paid Plan Required)", voice_map["v_community"]["name"])
        self.assertEqual(voice_map["v_community"]["tier"], "paid_only")
        self.assertFalse(voice_map["v_community"]["is_usable_for_api"])

        # Verify cache contains user_subscription
        self.assertTrue(os.path.isfile(self.cache_file))
        with open(self.cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
        self.assertIn("user_subscription", cache_data)
        self.assertEqual(cache_data["user_subscription"].get("tier"), "free")

    @patch("requests.get")
    def test_voices_classified_for_paid_tier(self, mock_get):
        """Mocks same voices under Creator tier; verifies community voice is usable with [Library]."""
        def fake_get(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "subscription" in url:
                resp.json.return_value = {
                    "tier": "creator",
                    "character_count": 12000,
                    "character_limit": 100000,
                    "status": "active",
                }
            elif "voices" in url:
                resp.json.return_value = {
                    "voices": [
                        {
                            "voice_id": "v_community",
                            "name": "MC The Hao",
                            "category": "community",
                            "sharing": {"public_owner_id": "creator_123"},
                        },
                    ]
                }
            return resp

        mock_get.side_effect = fake_get

        voices = fetch_elevenlabs_voices("test_paid_key", force_refresh=True)
        self.assertEqual(len(voices), 1)
        v = voices[0]
        self.assertIn("MC The Hao [Library]", v["name"])
        self.assertEqual(v["tier"], "premium")
        self.assertTrue(v["is_usable_for_api"])

    @patch("requests.post")
    def test_synthesize_402_library_voice_error(self, mock_post):
        """Mocks 402 with paid_plan_required; asserts descriptive Vietnamese error message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 402
        mock_resp.text = '{"detail": {"status": "paid_plan_required", "message": "Free users cannot use library voices via the API"}}'
        mock_post.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            synthesize_elevenlabs_tts_wav_16k_mono(
                text="Xin chào thế giới",
                wav_path="dummy.wav",
                voice_id="v_comm",
                api_key="free_key",
            )

        err_msg = str(ctx.exception)
        self.assertIn("Tài khoản Free không thể sử dụng giọng từ Voice Library", err_msg)
        self.assertIn("[Default]", err_msg)
        self.assertIn("[Clone]", err_msg)

    @patch("requests.post")
    def test_synthesize_402_generic_error(self, mock_post):
        """Mocks 402 generic payment required without library voice text."""
        mock_resp = MagicMock()
        mock_resp.status_code = 402
        mock_resp.text = "Card expired"
        mock_post.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            synthesize_elevenlabs_tts_wav_16k_mono(
                text="Xin chào",
                wav_path="dummy.wav",
                voice_id="v1",
                api_key="k1",
            )
        self.assertIn("ElevenLabs API error 402 (Payment Required): Card expired", str(ctx.exception))

    def test_sorting_puts_usable_voices_first(self):
        """Verifies _voice_entry_sort_key ranks usable voices before locked ones."""
        gui = MagicMock()
        gui._voice_gender_sort_rank.side_effect = lambda g: 0 if g == "female" else (1 if g == "male" else 2)

        voices = [
            # Locked voice with name starting with 'A' (would come first alphabetically)
            {
                "id": "elevenlabs:locked_1",
                "name": "Aaron 🔒 (Cần gói trả phí)",
                "provider": "elevenlabs",
                "gender": "male",
                "is_usable_for_api": False,
            },
            # Usable voice with name starting with 'Z'
            {
                "id": "elevenlabs:usable_1",
                "name": "Zoe [Default]",
                "provider": "elevenlabs",
                "gender": "female",
                "is_usable_for_api": True,
            },
            # Usable voice with name starting with 'B'
            {
                "id": "elevenlabs:usable_2",
                "name": "Bob [Clone]",
                "provider": "elevenlabs",
                "gender": "male",
                "is_usable_for_api": True,
            },
        ]

        sorted_voices = sorted(voices, key=lambda e: VideoTranslatorGUI._voice_entry_sort_key(gui, e))

        # Both usable voices must come before the locked voice
        self.assertTrue(sorted_voices[0]["is_usable_for_api"])
        self.assertTrue(sorted_voices[1]["is_usable_for_api"])
        self.assertFalse(sorted_voices[2]["is_usable_for_api"])
        self.assertEqual(sorted_voices[2]["id"], "elevenlabs:locked_1")

    @patch("PySide6.QtWidgets.QMessageBox.warning")
    def test_run_voiceover_guards_against_restricted_voice(self, mock_warning):
        """Verifies run_voiceover blocks restricted ElevenLabs voice with QMessageBox.warning."""
        gui = MagicMock()
        gui.workspace_root = PROJECT_ROOT
        gui.ensure_required_resources.return_value = True
        gui.resolve_background_audio_path.return_value = ""
        gui.get_audio_handling_mode.return_value = "replace"
        gui._get_voiceover_segments.return_value = [{"voice_name": "elevenlabs:locked_voice"}]
        gui._resolve_active_voice_name.return_value = "elevenlabs:locked_voice"
        gui.get_active_voice_name.return_value = "elevenlabs:locked_voice"
        gui.voice_output_folder_edit.text.return_value = ""
        gui._pipeline_active = True
        gui.voice_catalog_map = {
            "elevenlabs:locked_voice": {
                "id": "elevenlabs:locked_voice",
                "raw_name": "MC The Hao",
                "name": "MC The Hao 🔒 (Paid Plan Required)",
                "is_usable_for_api": False,
            }
        }

        VideoTranslatorGUI.run_voiceover(gui)

        mock_warning.assert_called_once()
        warning_args = mock_warning.call_args[0]
        self.assertIn("ElevenLabs Voice Restricted", str(warning_args[1]))
        self.assertIn("MC The Hao", str(warning_args[2]))
        gui._pipeline_fail.assert_called_once_with("Voice restricted")


if __name__ == "__main__":
    unittest.main()
