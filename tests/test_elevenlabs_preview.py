#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_elevenlabs_preview.py

Unit tests for ElevenLabs voice preview and resource checking:
1. ResourceDownloadService.is_resource_installed recognizing voice:elevenlabs and voice:elevenlabs:id
2. ResourceDownloadService.validate_piper_voice_runtime returning [] for elevenlabs:id
3. VideoTranslatorGUI._missing_resource_entries returning no missing resources for elevenlabs:id
4. VideoTranslatorGUI.preview_selected_voice_sample using fast-path direct preview playback when voice_entry has preview media
5. VideoTranslatorGUI.preview_selected_voice_sample toggling stop if already playing
6. VideoTranslatorGUI._stop_voice_library_preview restoring preview_voice_btn text and enabled state
"""

from __future__ import annotations

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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from app.services.resource_download_service import ResourceDownloadService
from ui.main_window import VideoTranslatorGUI


class TestElevenLabsPreview(unittest.TestCase):
    def setUp(self):
        self.service = ResourceDownloadService(workspace_root=PROJECT_ROOT)

    def test_resource_download_service_recognizes_elevenlabs(self):
        # 1. is_resource_installed recognition
        self.assertTrue(self.service.is_resource_installed("voice:elevenlabs"))
        self.assertTrue(self.service.is_resource_installed("voice:elevenlabs:voice_123"))
        self.assertTrue(self.service.is_resource_installed("voice:elevenlabs:rachel"))

        # 2. validate_piper_voice_runtime exemption
        self.assertEqual(self.service.validate_piper_voice_runtime("elevenlabs:voice_123"), [])
        self.assertEqual(self.service.validate_piper_voice_runtime("elevenlabs:rachel"), [])

    def test_missing_resource_entries_exempts_elevenlabs(self):
        gui = MagicMock()
        gui._resource_service.return_value = self.service
        gui.voice_catalog_map = {}
        gui.get_transcription_engine.return_value = "capcut"

        # Explicit voice_name parameter
        missing_explicit = VideoTranslatorGUI._missing_resource_entries(
            gui,
            include_voice=True,
            voice_name="elevenlabs:voice_123",
        )
        self.assertEqual(missing_explicit, [])

        # Active voice fallback
        gui.get_active_voice_name.return_value = "elevenlabs:rachel"
        missing_active = VideoTranslatorGUI._missing_resource_entries(
            gui,
            include_voice=True,
        )
        self.assertEqual(missing_active, [])

    def test_preview_selected_voice_sample_uses_direct_preview(self):
        gui = MagicMock()
        elevenlabs_entry = {
            "id": "elevenlabs:voice_abc",
            "name": "Eleven Rachel",
            "preview_audio_url": "https://api.elevenlabs.io/sample.mp3",
        }
        gui.voice_catalog_entries = [elevenlabs_entry]
        gui.voice_catalog_map = {"elevenlabs:voice_abc": elevenlabs_entry}
        gui.get_active_voice_name.return_value = "elevenlabs:voice_abc"
        gui._entry_has_preview_media.side_effect = lambda entry: VideoTranslatorGUI._entry_has_preview_media(gui, entry)
        gui.voice_preview_library_player.playbackState.return_value = QMediaPlayer.PlaybackState.StoppedState
        gui.preview_voice_btn = MagicMock()
        gui._play_voice_preview_entry = MagicMock()
        gui.ensure_required_resources = MagicMock()

        VideoTranslatorGUI.preview_selected_voice_sample(gui)

        gui._play_voice_preview_entry.assert_called_once_with(elevenlabs_entry, gui.preview_voice_btn)
        gui.ensure_required_resources.assert_not_called()

    def test_preview_selected_voice_sample_toggles_stop_if_playing(self):
        gui = MagicMock()
        elevenlabs_entry = {
            "id": "elevenlabs:voice_abc",
            "name": "Eleven Rachel",
            "preview_audio_url": "https://api.elevenlabs.io/sample.mp3",
        }
        gui.voice_catalog_entries = [elevenlabs_entry]
        gui.voice_preview_library_player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        gui._stop_voice_library_preview = MagicMock()
        gui._play_voice_preview_entry = MagicMock()

        VideoTranslatorGUI.preview_selected_voice_sample(gui)

        gui._stop_voice_library_preview.assert_called_once()
        gui._play_voice_preview_entry.assert_not_called()

    def test_preview_selected_voice_sample_passes_voice_name_to_ensure_resources(self):
        gui = MagicMock()
        piper_entry = {
            "id": "vi_VN-25hours-single",
            "name": "25hours Single",
        }
        gui.voice_catalog_entries = [piper_entry]
        gui.voice_catalog_map = {"vi_VN-25hours-single": piper_entry}
        gui.get_active_voice_name.return_value = "vi_VN-25hours-single"
        gui._entry_has_preview_media.side_effect = lambda entry: VideoTranslatorGUI._entry_has_preview_media(gui, entry)
        gui.voice_preview_library_player.playbackState.return_value = QMediaPlayer.PlaybackState.StoppedState
        gui.ensure_required_resources.return_value = False

        VideoTranslatorGUI.preview_selected_voice_sample(gui)

        gui.ensure_required_resources.assert_called_once_with(
            "Voice preview",
            include_voice=True,
            voice_name="vi_VN-25hours-single",
        )

    def test_stop_voice_library_preview_restores_button(self):
        gui = MagicMock()
        gui._voice_preview_row_buttons = {}
        gui.preview_voice_btn = MagicMock()
        gui.voice_preview_library_player = MagicMock()

        VideoTranslatorGUI._stop_voice_library_preview(gui)

        gui.preview_voice_btn.setText.assert_called_once()
        gui.preview_voice_btn.setEnabled.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
