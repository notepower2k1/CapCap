"""Offscreen Qt coverage for staged, asynchronous ElevenLabs Settings sync."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for _path in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QWidget

from ui import main_window
from ui.i18n import get_language, set_language, t
from ui.main_window import VideoTranslatorGUI


class TestElevenLabsSettingsAsync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app._capcap_test_mode = True

    def setUp(self):
        self.original_language = get_language()
        set_language("en")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temp_dir.name) / "settings.ini"
        self.host = QWidget()
        self.host.settings = QSettings(str(self.settings_path), QSettings.IniFormat)
        self.host.get_transcription_engine = lambda: "sensevoice"
        self.host.get_whisper_model_name = lambda: "base"
        self.host.selected_voice_engine = "piper"
        self.host.selected_whisper_model_name = "base"
        self.host.log = MagicMock()
        self.host.open_resource_manager_dialog = MagicMock()
        self.host.load_voice_preview_catalog = MagicMock()
        self.host.set_project_transcription_engine = MagicMock()
        self.host.save_user_settings = MagicMock()
        self.host._update_ocr_overlay = MagicMock()

    def tearDown(self):
        try:
            if self.host is not None:
                self.host.close()
                self.host.deleteLater()
        except RuntimeError:
            pass
        self.app.processEvents()
        self.temp_dir.cleanup()
        set_language(self.original_language)

    @staticmethod
    def _voice_payload(key: str, voices=None):
        import hashlib

        return {
            "voices": list(voices if voices is not None else [{"id": "elevenlabs:voice-1", "provider": "elevenlabs"}]),
            "fetched_at": time.time(),
            "user_subscription": {"tier": "creator", "character_count": 12, "character_limit": 1000},
            "key_fingerprint": hashlib.sha256(key.strip().encode("utf-8")).hexdigest(),
        }

    def _dialog_controls(self):
        dialog = QApplication.activeModalWidget()
        self.assertIsNotNone(dialog)
        api_key_edit = dialog.findChild(QLineEdit, "elevenlabs_api_key")
        self.assertIsNotNone(api_key_edit)
        sync_button = next(button for button in dialog.findChildren(QPushButton) if "Sync voices from ElevenLabs" in button.text())
        save_button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Save")
        cancel_button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Cancel")
        return dialog, api_key_edit, sync_button, save_button, cancel_button

    def _open_dialog_in_temp_cwd(self, schedule):
        previous_cwd = os.getcwd()
        try:
            os.chdir(self.temp_dir.name)
            with patch.object(main_window, "is_remote_profile", return_value=False):
                QTimer.singleShot(0, schedule)
                VideoTranslatorGUI.open_model_settings_dialog(self.host)
        finally:
            os.chdir(previous_cwd)

    def _wait_for_sync_cleanup(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        worker = getattr(self.host, "_elevenlabs_sync_worker", None)
        if worker is not None:
            worker.wait(max(0, int(timeout * 1000)))
        while time.monotonic() < deadline:
            self.app.processEvents()
            worker = getattr(self.host, "_elevenlabs_sync_worker", None)
            if worker is None:
                return
            time.sleep(0.005)
        self.fail("ElevenLabs sync worker did not finish cleanly")

    def test_sync_runs_off_gui_thread_cancel_keeps_active_state_and_ui_responsive(self):
        old_env = dict(os.environ)
        os.environ["ELEVENLABS_API_KEY"] = "active-key"
        os.environ["ELEVENLABS_MODEL"] = "eleven_v4"
        os.environ["ELEVENLABS_TTS_WORKERS"] = "2"
        original_catalog = [{"id": "elevenlabs:active", "provider": "elevenlabs"}]
        self.host.voice_catalog_entries_all = original_catalog
        release_fetch = threading.Event()
        fetch_started = threading.Event()
        fetch_thread_ids = []
        timer_ticks = []
        payload = self._voice_payload("draft-key")

        def blocked_fetch(key, *, force_refresh, write_cache):
            fetch_thread_ids.append(threading.get_ident())
            fetch_started.set()
            self.assertEqual(key, "draft-key")
            self.assertTrue(force_refresh)
            self.assertFalse(write_cache)
            release_fetch.wait(2.0)
            return payload

        heartbeat = QTimer()
        heartbeat.setInterval(5)
        heartbeat.timeout.connect(lambda: timer_ticks.append(time.monotonic()))

        def schedule_cancel():
            dialog, key_edit, sync_button, _save, cancel_button = self._dialog_controls()
            watchdog = QTimer(dialog)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(dialog.reject)
            dialog.finished.connect(watchdog.stop)
            watchdog.start(5000)
            key_edit.setText("draft-key")
            sync_button.click()
            heartbeat.start()

            def cancel_when_request_started():
                if fetch_started.is_set() and len(timer_ticks) >= 2:
                    cancel_button.click()
                else:
                    QTimer.singleShot(5, cancel_when_request_started)

            QTimer.singleShot(0, cancel_when_request_started)

        try:
            with patch("app.engines.elevenlabs_tts.read_elevenlabs_voice_cache", return_value={}), \
                    patch("app.engines.elevenlabs_tts.fetch_elevenlabs_user_subscription") as subscription_fetch, \
                    patch("app.engines.elevenlabs_tts.fetch_elevenlabs_voice_catalog", side_effect=blocked_fetch), \
                    patch("app.engines.elevenlabs_tts.fetch_elevenlabs_models", return_value=[]), \
                    patch.object(QMessageBox, "critical"), patch.object(QMessageBox, "information"), \
                    patch.object(QMessageBox, "warning"):
                self._open_dialog_in_temp_cwd(schedule_cancel)
                self.assertTrue(fetch_started.wait(1.0))
                self.assertTrue(release_fetch.is_set() is False)
                self.assertEqual(os.environ["ELEVENLABS_API_KEY"], "active-key")
                self.assertEqual(os.environ["ELEVENLABS_MODEL"], "eleven_v4")
                self.assertEqual(os.environ["ELEVENLABS_TTS_WORKERS"], "2")
                self.assertIs(self.host.voice_catalog_entries_all, original_catalog)
                self.host.load_voice_preview_catalog.assert_not_called()
                self.assertTrue(timer_ticks, "Qt timers should continue while the mocked request blocks")
                self.assertNotEqual(fetch_thread_ids[0], threading.get_ident())
                subscription_fetch.assert_not_called()
                release_fetch.set()
                self._wait_for_sync_cleanup()
                self.assertIs(self.host.voice_catalog_entries_all, original_catalog)
                self.host.load_voice_preview_catalog.assert_not_called()
        finally:
            heartbeat.stop()
            release_fetch.set()
            os.environ.clear()
            os.environ.update(old_env)

    def test_successful_sync_is_staged_until_save_then_commits_snapshot(self):
        old_env = dict(os.environ)
        os.environ["ELEVENLABS_API_KEY"] = "active-key"
        os.environ["ELEVENLABS_MODEL"] = "eleven_turbo_v2_5"
        os.environ["ELEVENLABS_TTS_WORKERS"] = "2"
        payload = self._voice_payload("draft-key")
        write_calls = []

        def write_cache(key, cache_payload):
            write_calls.append((key, cache_payload))
            return True

        def schedule_save():
            dialog, key_edit, sync_button, save_button, _cancel = self._dialog_controls()
            watchdog = QTimer(dialog)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(dialog.reject)
            dialog.finished.connect(watchdog.stop)
            watchdog.start(5000)
            key_edit.setText("draft-key")
            sync_button.click()

            def save_after_staged_result():
                if save_button.isEnabled():
                    save_button.click()
                else:
                    QTimer.singleShot(5, save_after_staged_result)

            QTimer.singleShot(5, save_after_staged_result)

        try:
            with patch("app.engines.elevenlabs_tts.read_elevenlabs_voice_cache", return_value={}), \
                    patch("app.engines.elevenlabs_tts.fetch_elevenlabs_voice_catalog", return_value=payload), \
                    patch("app.engines.elevenlabs_tts.fetch_elevenlabs_models", return_value=[{"model_id": "eleven_v4", "name": "eleven_v4"}]), \
                    patch("app.engines.elevenlabs_tts.write_elevenlabs_voice_cache", side_effect=write_cache), \
                    patch.object(QMessageBox, "critical"), patch.object(QMessageBox, "information"), \
                    patch.object(QMessageBox, "warning"):
                self._open_dialog_in_temp_cwd(schedule_save)
            self.assertEqual(os.environ["ELEVENLABS_API_KEY"], "draft-key")
            self.assertEqual(write_calls, [("draft-key", payload)])
            self.host.load_voice_preview_catalog.assert_called_once_with()
            self.host.set_project_transcription_engine.assert_called_once_with("sensevoice")
        finally:
            worker = getattr(self.host, "_elevenlabs_sync_worker", None)
            if worker is not None:
                worker.wait(2000)
                self.app.processEvents()
            os.environ.clear()
            os.environ.update(old_env)

    def test_key_edit_during_sync_discards_stale_result_before_save(self):
        old_env = dict(os.environ)
        os.environ["ELEVENLABS_API_KEY"] = "active-key"
        os.environ["ELEVENLABS_MODEL"] = "eleven_turbo_v2_5"
        os.environ["ELEVENLABS_TTS_WORKERS"] = "2"
        fetch_started = threading.Event()
        release_fetch = threading.Event()
        payload = self._voice_payload("draft-key")
        write_calls = []

        def blocked_fetch(*_args, **_kwargs):
            fetch_started.set()
            release_fetch.wait(2.0)
            return payload

        def schedule_edit_and_save():
            dialog, key_edit, sync_button, save_button, _cancel = self._dialog_controls()
            watchdog = QTimer(dialog)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(dialog.reject)
            dialog.finished.connect(watchdog.stop)
            watchdog.start(5000)
            key_edit.setText("draft-key")
            sync_button.click()

            def edit_key_after_request_starts():
                if not fetch_started.is_set():
                    QTimer.singleShot(5, edit_key_after_request_starts)
                    return
                key_edit.setText("new-final-key")
                release_fetch.set()

                def save_when_result_discarded():
                    expected_status = t("API Key changed during sync; the result was discarded.")
                    status_is_discarded = any(
                        label.text() == expected_status for label in dialog.findChildren(QLabel)
                    )
                    if status_is_discarded and save_button.isEnabled():
                        save_button.click()
                    else:
                        QTimer.singleShot(5, save_when_result_discarded)

                QTimer.singleShot(5, save_when_result_discarded)

            QTimer.singleShot(5, edit_key_after_request_starts)

        try:
            with patch("app.engines.elevenlabs_tts.read_elevenlabs_voice_cache", return_value={}), \
                    patch("app.engines.elevenlabs_tts.fetch_elevenlabs_voice_catalog", side_effect=blocked_fetch), \
                    patch("app.engines.elevenlabs_tts.fetch_elevenlabs_models", return_value=[]), \
                    patch("app.engines.elevenlabs_tts.write_elevenlabs_voice_cache", side_effect=lambda *args: write_calls.append(args) or True), \
                    patch.object(QMessageBox, "critical"), patch.object(QMessageBox, "information"), \
                    patch.object(QMessageBox, "warning"):
                self._open_dialog_in_temp_cwd(schedule_edit_and_save)
            self.assertEqual(os.environ["ELEVENLABS_API_KEY"], "new-final-key")
            self.assertEqual(write_calls, [])
            self.host.load_voice_preview_catalog.assert_called_once_with()
        finally:
            release_fetch.set()
            worker = getattr(self.host, "_elevenlabs_sync_worker", None)
            if worker is not None:
                worker.wait(2000)
                self.app.processEvents()
            os.environ.clear()
            os.environ.update(old_env)


class TestElevenLabsVoiceSignatureInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_signature_builder_passes_model_and_language_for_mixed_provider_segments(self):
        host = QWidget()
        host.project_service = MagicMock()
        host.project_service.build_voice_signature.return_value = "signature"
        host.current_project_state = None
        host.voice_timing_sync_combo = QComboBox(host)
        host.voice_timing_sync_combo.addItem("Off")
        host.get_active_voice_name = lambda: "Piper Default"
        host._get_voiceover_segments = lambda: [{"text": "Hello", "voice_name": "elevenlabs:assigned-voice"}]
        host.get_audio_handling_mode = lambda: "fast"
        host._parse_voice_speed_value = lambda: 1.0
        host.get_target_language_code = lambda: "en"
        try:
            with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_v3"}):
                result = VideoTranslatorGUI.build_current_voice_signature(host)
            self.assertEqual(result, "signature")
            kwargs = host.project_service.build_voice_signature.call_args.kwargs
            self.assertEqual(kwargs["model_id"], "eleven_v3")
            self.assertEqual(kwargs["language_code"], "en")
        finally:
            host.deleteLater()
            QApplication.instance().processEvents()

    def test_catalog_load_is_cache_only_and_preserves_same_key_voices_during_refresh(self):
        host = QWidget()
        key = "active-key"
        voice = {"id": "elevenlabs:active", "provider": "elevenlabs"}
        host._auto_sync_piper_voices_to_catalog = MagicMock()
        host.voice_catalog_service = MagicMock()
        host.voice_catalog_service.load_catalog.side_effect = lambda **_kwargs: [{"id": "piper:local", "provider": "piper"}]
        host.voice_catalog_entries_all = [voice]
        host._voice_catalog_elevenlabs_key = key
        host._start_elevenlabs_catalog_refresh = MagicMock()
        host._merge_piper_manifest_voices = MagicMock()
        host._apply_piper_voice_meta_overrides = MagicMock()
        host.voice_preview_dialog = None
        host.refresh_voice_catalog_combos = MagicMock()
        try:
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": key}), \
                    patch("app.engines.elevenlabs_tts.read_elevenlabs_voice_cache", return_value={}):
                VideoTranslatorGUI.load_voice_preview_catalog(host)
            host.voice_catalog_service.load_catalog.assert_called_once_with(allow_elevenlabs_network=False)
            host._start_elevenlabs_catalog_refresh.assert_called_once_with(key)
            self.assertIn(voice, host.voice_catalog_entries_all)

            host._voice_catalog_elevenlabs_key = key
            host.voice_catalog_entries_all = [voice]
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "different-key"}), \
                    patch("app.engines.elevenlabs_tts.read_elevenlabs_voice_cache", return_value={}):
                VideoTranslatorGUI.load_voice_preview_catalog(host)
            self.assertFalse(any(entry.get("provider") == "elevenlabs" for entry in host.voice_catalog_entries_all))
        finally:
            host.deleteLater()
            QApplication.instance().processEvents()

    def test_editor_persistence_does_not_relabel_existing_audio_after_settings_change(self):
        host = QWidget()
        settings = {"voice_signature": "model-a-language-vi"}
        state = SimpleNamespace(settings=settings)
        state.set_setting = lambda name, value: settings.__setitem__(name, value)
        host._is_cleaning_or_resetting = False
        host.ensure_current_project = lambda: state
        host.current_segments = []
        host.current_translated_segments = []
        host.current_segment_models = []
        host.current_translated_segment_models = []
        host.last_original_srt_path = ""
        host.last_translated_srt_path = ""
        host._blur_effect_enabled = lambda: False
        host._current_subtitle_style_controls_state = lambda: {}
        host.project_service = MagicMock()
        host.build_current_voice_signature = MagicMock(side_effect=AssertionError("editor save must not recertify TTS"))
        try:
            VideoTranslatorGUI.persist_current_timeline_project_data(host)
            self.assertEqual(settings["voice_signature"], "model-a-language-vi")
            host.build_current_voice_signature.assert_not_called()
            host.project_service.save_project.assert_called_once_with(state)
        finally:
            host.deleteLater()
            QApplication.instance().processEvents()

    def test_voiceover_completion_uses_the_worker_model_and_language_snapshot(self):
        host = QWidget()
        state_settings = {"voice_signature": "before"}
        state = SimpleNamespace(settings=state_settings)
        state.set_setting = lambda name, value: state_settings.__setitem__(name, value)
        host.current_project_state = state
        host.progress_bar = QProgressBar(host)
        host.voice_thread = SimpleNamespace(elevenlabs_model_id="eleven_v4", target_language="fr")
        host.project_service = MagicMock()
        host.build_current_voice_signature = MagicMock(return_value="captured-job-signature")
        host._music_audio_tracks = lambda: []
        host._apply_generated_tts_texts = lambda _segments: False
        host._get_voiceover_segments = lambda: [{"text": "Bonjour", "voice_name": "elevenlabs:voice-1"}]
        host.resolve_background_audio_path = lambda: ""
        host.update_project_step = MagicMock()
        host._pipeline_active = False
        host._voiceover_force_refresh = True
        host._pending_voice_signature = "pending"
        host.schedule_timeline_visual_refresh = MagicMock()
        host.refresh_ui_state = MagicMock()
        host.sync_preview_audio_track_to_output = MagicMock()
        host.log = MagicMock()
        try:
            with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_v3"}), \
                    patch.object(QMessageBox, "information"):
                VideoTranslatorGUI.on_voiceover_finished(host, "", "", [], "")
            kwargs = host.build_current_voice_signature.call_args.kwargs
            self.assertEqual(kwargs["model_id"], "eleven_v4")
            self.assertEqual(kwargs["language_code"], "fr")
            self.assertEqual(state_settings["voice_signature"], "captured-job-signature")
            host.project_service.save_project.assert_called_once_with(state)
        finally:
            host.deleteLater()
            QApplication.instance().processEvents()


if __name__ == "__main__":
    unittest.main()
