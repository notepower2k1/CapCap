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
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from ui.i18n import get_language, set_language, t
from ui.main_window import VideoTranslatorGUI, _update_workers_combo_state


class TestUIVoiceWorkersAndLang(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app._capcap_test_mode = True

    def setUp(self):
        self.container = QWidget()

    def tearDown(self):
        if hasattr(self, "container") and self.container is not None:
            try:
                self.container.deleteLater()
            except Exception:
                pass
            self.container = None
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def create_combo(self) -> QComboBox:
        return QComboBox(self.container)

    def test_update_workers_combo_state_free_tier(self):
        """Verifies options 3 and 4 are disabled for Free tier and index clamped to 1."""
        combo = self.create_combo()
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
        combo = self.create_combo()
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
        gui.free_voice_combo = self.create_combo()
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
        gui.free_voice_combo = self.create_combo()

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
            win.close()
            win.deleteLater()

            # Set to 'vi' and verify VideoTranslatorGUI restores 'vi'
            s.setValue("target_language", "vi")
            win2 = VideoTranslatorGUI()
            self.assertEqual(win2.lang_target_combo.currentData(), "vi")
            win2.close()
            win2.deleteLater()
            QApplication.processEvents()
        finally:
            s.setValue("target_language", orig)

    def test_voice_combo_labels_dynamic_localization(self):
        """Verifies ElevenLabs voice labels dynamically localize between EN and VI."""
        orig_lang = get_language()
        gui = MagicMock()
        gui.VOICE_ENTRY_ID_ROLE = VideoTranslatorGUI.VOICE_ENTRY_ID_ROLE
        gui.free_voice_combo = self.create_combo()
        gui._selected_voice_gender.return_value = "all"
        gui._voice_signals_bound = True
        gui.get_target_language_code.return_value = "vi"
        gui._current_voice_engine_key.return_value = "elevenlabs"
        gui._voice_entry_sort_key = lambda e: 0
        gui._voice_catalog_data_value = lambda entry: f"elevenlabs:{entry.get('id', '')}"

        gui.voice_catalog_entries_all = [
            {
                "id": "restricted_1",
                "raw_name": "MC The Hao",
                "name": "MC The Hao 🔒 (Paid Plan Required)",
                "provider": "elevenlabs",
                "language": "multilingual",
                "is_usable_for_api": False,
                "voice_type": "community",
            },
            {
                "id": "premade_1",
                "raw_name": "Rachel",
                "name": "Rachel [Default]",
                "provider": "elevenlabs",
                "language": "multilingual",
                "is_usable_for_api": True,
                "voice_type": "premade",
            },
            {
                "id": "cloned_1",
                "raw_name": "My Clone",
                "name": "My Clone [Clone]",
                "provider": "elevenlabs",
                "language": "multilingual",
                "is_usable_for_api": True,
                "voice_type": "cloned",
            },
        ]

        try:
            # English locale
            set_language("en")
            VideoTranslatorGUI.refresh_voice_catalog_combos(gui)
            self.assertEqual(gui.free_voice_combo.count(), 3)
            self.assertEqual(gui.free_voice_combo.itemText(0), "MC The Hao 🔒 (Paid Plan Required)")
            self.assertEqual(gui.free_voice_combo.itemText(1), "Rachel [Default]")
            self.assertEqual(gui.free_voice_combo.itemText(2), "My Clone [Clone]")

            # Vietnamese locale
            set_language("vi")
            VideoTranslatorGUI.refresh_voice_catalog_combos(gui)
            self.assertEqual(gui.free_voice_combo.count(), 3)
            self.assertEqual(gui.free_voice_combo.itemText(0), "MC The Hao 🔒 (Cần gói trả phí)")
            self.assertEqual(gui.free_voice_combo.itemText(1), "Rachel [Mặc định]")
            self.assertEqual(gui.free_voice_combo.itemText(2), "My Clone [Bản sao]")
        finally:
            set_language(orig_lang)

    def test_settings_dialog_worker_combo_localization_and_state(self):
        """Verifies worker combo items are wrapped in t(...) and disabled for free tier."""
        orig_lang = get_language()
        try:
            # In English
            set_language("en")
            combo_en = self.create_combo()
            combo_en.addItem(t("1 worker (Safe / Slow network)"), "1")
            combo_en.addItem(t("2 workers (Recommended for Free tier)"), "2")
            combo_en.addItem(t("3 workers 🔒 (Paid Plan Required)"), "3")
            combo_en.addItem(t("4 workers 🔒 (Paid Plan Required)"), "4")

            self.assertEqual(combo_en.itemText(0), "1 worker (Safe / Slow network)")
            self.assertEqual(combo_en.itemText(1), "2 workers (Recommended for Free tier)")
            self.assertEqual(combo_en.itemText(2), "3 workers 🔒 (Paid Plan Required)")
            self.assertEqual(combo_en.itemText(3), "4 workers 🔒 (Paid Plan Required)")

            _update_workers_combo_state(combo_en, "free")
            model_en = combo_en.model()
            self.assertTrue(model_en.item(0).isEnabled())
            self.assertTrue(model_en.item(1).isEnabled())
            self.assertFalse(model_en.item(2).isEnabled())
            self.assertFalse(model_en.item(3).isEnabled())

            # In Vietnamese
            set_language("vi")
            combo_vi = self.create_combo()
            combo_vi.addItem(t("1 worker (Safe / Slow network)"), "1")
            combo_vi.addItem(t("2 workers (Recommended for Free tier)"), "2")
            combo_vi.addItem(t("3 workers 🔒 (Paid Plan Required)"), "3")
            combo_vi.addItem(t("4 workers 🔒 (Paid Plan Required)"), "4")

            self.assertEqual(combo_vi.itemText(0), "1 luồng (Mạng chậm / An toàn)")
            self.assertEqual(combo_vi.itemText(1), "2 luồng (Khuyến nghị gói Free)")
            self.assertEqual(combo_vi.itemText(2), "3 luồng 🔒 (Gói trả phí)")
            self.assertEqual(combo_vi.itemText(3), "4 luồng 🔒 (Gói trả phí)")

            _update_workers_combo_state(combo_vi, "free")
            model_vi = combo_vi.model()
            self.assertTrue(model_vi.item(0).isEnabled())
            self.assertTrue(model_vi.item(1).isEnabled())
            self.assertFalse(model_vi.item(2).isEnabled())
            self.assertFalse(model_vi.item(3).isEnabled())
        finally:
            set_language(orig_lang)

    def test_settings_dialog_elevenlabs_model_combo_population_and_migration(self):
        """Verifies elevenlabs_model_combo populates the 5 modern models and migrates eleven_multilingual_v2."""
        from app.engines.elevenlabs_tts import DEFAULT_ELEVENLABS_MODELS
        combo = self.create_combo()
        for m in DEFAULT_ELEVENLABS_MODELS:
            combo.addItem(t(m["name"]), m["model_id"])

        self.assertEqual(combo.count(), 5)
        model_ids = [combo.itemData(i) for i in range(combo.count())]
        self.assertNotIn("eleven_multilingual_v2", model_ids)
        self.assertEqual(
            model_ids,
            ["eleven_v4", "eleven_v4_turbo", "eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_v3"],
        )

        # Migration logic
        current_el_model = "eleven_multilingual_v2"
        if current_el_model == "eleven_multilingual_v2":
            current_el_model = "eleven_turbo_v2_5"
        idx = combo.findData(current_el_model)
        self.assertEqual(idx, 2)


if __name__ == "__main__":
    unittest.main()
