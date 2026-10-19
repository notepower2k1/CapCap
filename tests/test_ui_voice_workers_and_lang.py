#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_ui_voice_workers_and_lang.py

Unit tests for:
1. ElevenLabs worker count selector state (_update_workers_combo_state).
2. Disabling restricted ElevenLabs voices in free_voice_combo.model() and defaulting to first enabled voice.
3. Synchronizing VIE and EN target language (persistence in QSettings and start panel).
4. Ensuring _resolve_active_voice_name does not return 'ngochuyen' when target language is English.
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

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QComboBox

from ui.main_window import VideoTranslatorGUI, _update_workers_combo_state


class TestUIVoiceWorkersAndLang(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_update_workers_combo_state_free_tier(self):
        """Verifies options 3 and 4 are disabled for Free tier and index clamped to 1."""
        combo = QComboBox()
        combo.addItem("1 luồng (Mạng chậm / An toàn)", "1")
        combo.addItem("2 luồng (Khuyến nghị gói Free)", "2")
        combo.addItem("3 luồng 🔒 (Gói trả phí)", "3")
        combo.addItem("4 luồng 🔒 (Gói trả phí)", "4")

        # Start with index 3 (4 workers)
        combo.setCurrentIndex(3)
        _update_workers_combo_state(combo, "free")

        # Capped to 2 workers (index 1)
        self.assertEqual(combo.currentIndex(), 1)
        model = combo.model()
        self.assertTrue(model.item(0).isEnabled())
        self.assertTrue(model.item(1).isEnabled())
        self.assertFalse(model.item(2).isEnabled())
        self.assertFalse(model.item(3).isEnabled())

        # Also test with empty or None tier string
        combo.setCurrentIndex(2)
        _update_workers_combo_state(combo, "")
        self.assertEqual(combo.currentIndex(), 1)
        self.assertFalse(model.item(2).isEnabled())
        self.assertFalse(model.item(3).isEnabled())

    def test_update_workers_combo_state_paid_tier(self):
        """Verifies options 3 and 4 are enabled for Paid tiers."""
        combo = QComboBox()
        combo.addItem("1 luồng (Mạng chậm / An toàn)", "1")
        combo.addItem("2 luồng (Khuyến nghị gói Free)", "2")
        combo.addItem("3 luồng 🔒 (Gói trả phí)", "3")
        combo.addItem("4 luồng 🔒 (Gói trả phí)", "4")

        for paid_tier in ("starter", "creator", "pro", "enterprise", "paid"):
            _update_workers_combo_state(combo, paid_tier)
            model = combo.model()
            self.assertTrue(model.item(0).isEnabled(), f"Failed for tier {paid_tier}")
            self.assertTrue(model.item(1).isEnabled(), f"Failed for tier {paid_tier}")
            self.assertTrue(model.item(2).isEnabled(), f"Failed for tier {paid_tier}")
            self.assertTrue(model.item(3).isEnabled(), f"Failed for tier {paid_tier}")

            # User can select 3 or 4 workers on paid plans
            combo.setCurrentIndex(3)
            _update_workers_combo_state(combo, paid_tier)
            self.assertEqual(combo.currentIndex(), 3)

    def test_free_voice_combo_disables_restricted_voices(self):
        """Verifies restricted ElevenLabs voices have setEnabled(False) in combo model."""
        gui = MagicMock()
        gui.VOICE_ENTRY_ID_ROLE = VideoTranslatorGUI.VOICE_ENTRY_ID_ROLE
        gui.free_voice_combo = QComboBox()
        gui._selected_voice_gender.return_value = "all"
        gui._voice_signals_bound = True
        gui.get_target_language_code.return_value = "vi"
        gui._current_voice_engine_key.return_value = "elevenlabs"
        gui._voice_entry_sort_key = lambda e: 0
        gui._voice_catalog_data_value = lambda entry: f"elevenlabs:{entry.get('id', '')}"

        gui.voice_catalog_entries_all = [
            {
                "id": "locked_1",
                "name": "Locked Voice 🔒",
                "provider": "elevenlabs",
                "language": "multilingual",
                "is_usable_for_api": False,
            },
            {
                "id": "usable_1",
                "name": "Usable Voice",
                "provider": "elevenlabs",
                "language": "multilingual",
                "is_usable_for_api": True,
            },
        ]

        VideoTranslatorGUI.refresh_voice_catalog_combos(gui)

        cb_model = gui.free_voice_combo.model()
        self.assertEqual(gui.free_voice_combo.count(), 2)
        self.assertFalse(cb_model.item(0).isEnabled(), "Locked voice item must be disabled")
        self.assertTrue(cb_model.item(1).isEnabled(), "Usable voice item must be enabled")
        # Combobox must default to the first enabled voice (index 1)
        self.assertEqual(gui.free_voice_combo.currentIndex(), 1)

    def test_on_target_language_changed_persists_settings(self):
        """Verifies on_target_language_changed saves target_language into QSettings."""
        gui = MagicMock()
        gui.settings = MagicMock()
        gui.voice_catalog_entries_all = []

        # Target language English
        gui.get_target_language_code.return_value = "en"
        VideoTranslatorGUI.on_target_language_changed(gui)
        gui.settings.setValue.assert_called_with("target_language", "en")

        # Target language Vietnamese
        gui.get_target_language_code.return_value = "vi"
        VideoTranslatorGUI.on_target_language_changed(gui)
        gui.settings.setValue.assert_called_with("target_language", "vi")

    def test_resolve_active_voice_name_does_not_return_ngochuyen_for_en(self):
        """Verifies _resolve_active_voice_name avoids 'ngochuyen' fallback when target_language is 'en'."""
        gui = MagicMock()
        gui.VOICE_ENTRY_ID_ROLE = VideoTranslatorGUI.VOICE_ENTRY_ID_ROLE
        gui.get_target_language_code.return_value = "en"
        gui._current_voice_engine_key.return_value = "piper"
        gui.free_voice_combo = QComboBox()

        # Case 1: Combo has ngochuyen and an English voice, but ngochuyen is currently selected
        gui.free_voice_combo.addItem("Ngọc Huyền", "ngochuyen")
        gui.free_voice_combo.addItem("Jenny", "edge:en-US-JennyNeural")
        gui.free_voice_combo.setCurrentIndex(0)

        resolved = VideoTranslatorGUI._resolve_active_voice_name(gui)
        self.assertEqual(resolved, "edge:en-US-JennyNeural")
        self.assertNotEqual(resolved, "ngochuyen")

        # Case 2: Combo only contains ngochuyen
        gui.free_voice_combo.clear()
        gui.free_voice_combo.addItem("Ngọc Huyền", "ngochuyen")
        gui.free_voice_combo.setCurrentIndex(0)

        resolved_only_vi = VideoTranslatorGUI._resolve_active_voice_name(gui)
        self.assertEqual(resolved_only_vi, "")
        self.assertNotEqual(resolved_only_vi, "ngochuyen")

        # Case 3: Target language is Vietnamese -> ngochuyen is allowed
        gui.get_target_language_code.return_value = "vi"
        gui.voice_catalog_map = {"ngochuyen": {"id": "ngochuyen"}}
        resolved_vi = VideoTranslatorGUI._resolve_active_voice_name(gui)
        self.assertEqual(resolved_vi, "ngochuyen")

    def test_start_panel_restores_saved_target_language(self):
        """Verifies start panel restores saved target_language from gui.settings."""
        s = QSettings("CapCap", "VideoTranslatorGUI")
        orig = s.value("target_language", "vi")
        try:
            # Set to 'en' and verify VideoTranslatorGUI restores 'en'
            s.setValue("target_language", "en")
            win = VideoTranslatorGUI()
            self.assertEqual(win.lang_target_combo.currentData(), "en")

            # Set to 'vi' and verify VideoTranslatorGUI restores 'vi'
            s.setValue("target_language", "vi")
            win2 = VideoTranslatorGUI()
            self.assertEqual(win2.lang_target_combo.currentData(), "vi")
        finally:
            s.setValue("target_language", orig)


if __name__ == "__main__":
    unittest.main()
