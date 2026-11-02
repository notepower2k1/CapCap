import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "app", ROOT / "ui"):
    sys.path.insert(0, str(path))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from language_support import (
    LANGUAGES, SOURCE_LANGUAGES, VOICE_PREVIEW_TEXT, available_voice_engines,
    elevenlabs_supports_language, voice_matches_language,
)
from translation.prompt_loader import render_preset_prompt
from translation.prompt_builder import build_translation_messages
from translation.context_analyzer import learn_dialogue_context
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QWidget
from ui.main_window import VideoTranslatorGUI


class LanguageSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app._capcap_test_mode = True

    def test_all_requested_languages_have_input_output_and_preview(self):
        requested = {"ko", "ja", "th", "ru", "fr", "de"}
        self.assertTrue(requested <= {code for _, code in LANGUAGES})
        self.assertTrue(requested <= {code for _, code in SOURCE_LANGUAGES})
        for code in requested:
            self.assertTrue(VOICE_PREVIEW_TEXT[code])
            self.assertNotEqual(VOICE_PREVIEW_TEXT[code], VOICE_PREVIEW_TEXT["vi"])

    def test_catalog_drives_engine_choices_without_enabling_wrong_voices(self):
        voices = [
            {"provider": "piper", "language": "en"},
            {"provider": "vieneu", "language": "multilingual"},
            {"provider": "capcut", "language": "jp"},
            {"provider": "capcut", "language": "th"},
            {"provider": "capcut", "language": "fr", "enabled": False},
        ]
        def engines(code):
            return {key for _, key in available_voice_engines(code, voices)}
        self.assertEqual(engines("ko"), {"elevenlabs", "omnivoice"})
        self.assertEqual(engines("ru"), {"elevenlabs", "omnivoice"})
        self.assertEqual(engines("fr"), {"elevenlabs", "omnivoice"})
        self.assertEqual(engines("ja"), {"capcut", "elevenlabs", "omnivoice"})
        self.assertEqual(engines("th"), {"capcut", "elevenlabs", "omnivoice"})
        self.assertTrue({"piper", "vieneu"} <= engines("vi"))
        self.assertFalse(voice_matches_language({"provider": "piper", "language": "vi"}, "ko"))
        self.assertFalse(voice_matches_language({"provider": "vieneu", "language": "multilingual"}, "de"))

    def test_native_engine_selector_switches_and_restores_choices(self):
        host = QWidget()
        host.voice_engine_combo = QComboBox(host)
        host.voice_engine_combo.addItem("Piper Voice", "piper")
        host.voice_catalog_entries_all = [{"provider": "capcut", "language": "ja"}]
        host.log = MagicMock()
        try:
            for target in ("ko", "ja", "th", "ru", "fr", "de", "vi", "en"):
                host.get_target_language_code = lambda code=target: code
                VideoTranslatorGUI._refresh_language_voice_engines(host)
                actual = [host.voice_engine_combo.itemData(i) for i in range(host.voice_engine_combo.count())]
                self.assertEqual(actual, [key for _, key in available_voice_engines(target, host.voice_catalog_entries_all)])
                self.assertIn(host.voice_engine_combo.currentData(), actual)
        finally:
            host.deleteLater()

    def test_sensevoice_rejects_unsupported_instead_of_treating_it_as_chinese(self):
        from sensevoice_processor import _lang_code
        for code in ("vi", "th", "ru", "fr", "de"):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "Whisper"):
                _lang_code(code)
        for code in ("ja", "ko", "zh", "en", "auto"):
            self.assertEqual(_lang_code(code), code)

    def test_range_dialog_filters_sensevoice_for_each_new_language(self):
        host = QWidget()
        host.current_segments = []
        host.settings = MagicMock()
        host.settings.value.return_value = "rapidocr"
        host.get_transcription_engine = lambda: "sensevoice"
        host.get_whisper_model_name = lambda: "base"
        try:
            for language in ("ko", "ja", "th", "ru", "fr", "de"):
                host.get_source_language_code = lambda code=language: code
                with patch.object(QDialog, "exec", return_value=QDialog.Accepted):
                    result = VideoTranslatorGUI._show_range_transcription_dialog(host, (0.0, 5.0))
                self.assertEqual(result["engine"], "sensevoice" if language in {"ko", "ja"} else "whisper")
                self.assertEqual(result["language"], language)
        finally:
            host.deleteLater()

    def test_elevenlabs_requires_supported_model_for_thai(self):
        self.assertFalse(elevenlabs_supports_language("eleven_turbo_v2_5", "th"))
        self.assertFalse(elevenlabs_supports_language("eleven_flash_v2_5", "th"))
        self.assertTrue(elevenlabs_supports_language("eleven_v3", "th"))
        for language in ("ko", "ja", "ru", "fr", "de", "vi", "en"):
            self.assertTrue(elevenlabs_supports_language("eleven_flash_v2_5", language))

    def test_non_vietnamese_translation_does_not_inherit_vietnamese_genre_rules(self):
        for target in ("ko", "ja", "th", "ru", "fr", "de"):
            prompt = render_preset_prompt("zh_wuxia_xianxia", source_lang="zh", target_lang=target)
            self.assertIn(f"zh->{target}", prompt)
            self.assertNotIn("Hán-Việt", prompt)
            with patch.dict(os.environ, {"CAPCAP_TRANSLATION_PRESET_ID": "zh_wuxia_xianxia"}):
                system, _ = build_translation_messages(
                    source_texts=["source"], translated_texts=["draft"], src_lang="zh", target_lang=target,
                )
            self.assertNotIn("Inherited Genre", system)
        self.assertIn("Hán-Việt", render_preset_prompt("zh_wuxia_xianxia", target_lang="vi"))

    def test_dialogue_context_uses_target_language_rules(self):
        polisher = MagicMock()
        polisher.generate_text.return_value = "context"
        learn_dialogue_context(
            source_segments=[{"text": "line"}] * 3, polisher=polisher,
            src_lang="zh", target_lang="ko",
        )
        prompt = polisher.generate_text.call_args.kwargs["system_msg"]
        self.assertIn("target language (ko)", prompt)
        self.assertNotIn("Trương Vỹ", prompt)
        self.assertNotIn("anh - em", prompt)

    def test_six_languages_reach_tts_and_keep_separate_audio_caches(self):
        from workflows.voice_workflow import VoiceWorkflow
        from tests.test_elevenlabs_workflow_regressions import _write_wav
        with tempfile.TemporaryDirectory() as folder:
            workflow = VoiceWorkflow(folder)
            calls = []
            def synthesize(**kwargs):
                calls.append(kwargs)
                return _write_wav(kwargs["wav_path"])
            workflow.engine_runtime.synthesize_segment = MagicMock(side_effect=synthesize)
            paths = []
            with patch.dict(os.environ, {"ELEVENLABS_TTS_WORKERS": "1"}):
                for language in ("ko", "ja", "th", "ru", "fr", "de"):
                    # Same wording/voice isolates language as the cache discriminator.
                    result = workflow.prime_tts_cache(
                        segments=[{"text": "test", "start": 0.0, "end": 1.0}],
                        tmp_dir=folder, voice_name="elevenlabs:test-voice",
                        target_language=language, elevenlabs_model_id="eleven_v3",
                    )
                    paths.append(result[0])
                workflow.prime_tts_cache(
                    segments=[{"text": "test", "start": 0.0, "end": 1.0}],
                    tmp_dir=folder, voice_name="elevenlabs:test-voice",
                    target_language="ko", elevenlabs_model_id="eleven_v3",
                )
            self.assertEqual(len(set(paths)), 6)
            self.assertEqual([call["language_code"] for call in calls], ["ko", "ja", "th", "ru", "fr", "de"])
            self.assertTrue(all(call["model_id"] == "eleven_v3" for call in calls))

    def test_invalid_saved_voice_assignment_is_rejected_before_synthesis(self):
        from workflows.voice_workflow import VoiceWorkflow
        with tempfile.TemporaryDirectory() as folder:
            workflow = VoiceWorkflow(folder)
            workflow.engine_runtime.synthesize_segment = MagicMock()
            with self.assertRaisesRegex(ValueError, "Vietnamese"):
                workflow.prime_tts_cache(
                    segments=[{"text": "test", "start": 0.0, "end": 1.0, "voice_name": "vieneu:old-voice"}],
                    tmp_dir=folder, voice_name="elevenlabs:test", target_language="ko",
                )
            workflow.engine_runtime.synthesize_segment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
