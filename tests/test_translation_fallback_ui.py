import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx2 as httpx
from openai import APIStatusError
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from translation.errors import (
    TranslationConfigError, TranslationProviderError, TranslationValidationError, translation_failure_reason,
)
from translation.orchestrator import TranslationOrchestrator
from translation.providers import GoogleWebTranslatorProvider
from ui import i18n
from ui.controllers.pipeline_controller import PipelineController
from ui.controllers.subtitle_controller import SubtitleController
from workflows.prepare_workflow import PrepareWorkflow


class TestTranslationFallbackUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def api_error(status):
        response = httpx.Response(status, request=httpx.Request("POST", "https://example.test/v1/chat/completions"))
        error = TranslationProviderError("AI request failed; private API details stay in logs")
        error.__cause__ = APIStatusError("Server response", response=response, body=None)
        return error

    def test_failure_reason_uses_underlying_api_error_without_exposing_details(self):
        cases = [
            (self.api_error(401), "AI API authentication failed (HTTP 401)."),
            (self.api_error(403), "AI API access was denied (HTTP 403)."),
            (self.api_error(429), "AI API request or quota limit reached (HTTP 429)."),
            (self.api_error(503), "AI service is temporarily overloaded or unavailable (HTTP 503)."),
            (self.api_error(504), "AI service did not respond in time (HTTP 504)."),
            (self.api_error(500), "AI service returned a server error."),
            (TimeoutError("Read timed out"), "AI service did not respond within the time limit."),
            (TranslationConfigError("Missing secret API key"), "AI API key or model is not configured."),
            (TranslationValidationError("Missing ID 14"), "AI returned incomplete or invalid subtitles."),
            (RuntimeError("secret-key-123"), "AI translation failed for an unknown reason."),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__, expected=expected):
                self.assertEqual(translation_failure_reason(error), expected)

    def test_google_reason_stays_visible_in_progress_and_completion(self):
        reason = translation_failure_reason(self.api_error(503))
        notice = f"{reason}\nAI Provider is unavailable. Falling back to Google Translate..."
        parent = QWidget()
        controller = SubtitleController(parent)
        with patch.object(i18n, "_current_language", "vi"):
            controller._show_translation_progress(is_retranslation=False, provider_name="google_ai_studio")
            try:
                controller.on_translation_status_changed("Google Translate", notice)
                controller.on_translation_status_changed("Google Translate", "")
                controller.on_translation_progress(2, 4)
                parent._translation_progress_timer.timeout.emit()
                dialog = parent._translation_progress_dialog
                self.assertIn("Dịch vụ AI đang quá tải", dialog.labelText())
                self.assertIn("HTTP 503", dialog.labelText())
                self.assertIn("2/4", dialog.labelText())
                self.assertTrue(dialog.findChild(QLabel).wordWrap())
            finally:
                controller._close_translation_progress()
                parent.close()
                parent.deleteLater()

            gui = MagicMock()
            gui.video_path_edit.text.return_value = ""
            gui.current_translated_segments = [{"text": "Xin chào"}]
            gui._last_translation_provider = "google_ai_studio"
            controller = SubtitleController(gui)
            with patch("ui.controllers.subtitle_controller.QMessageBox.information") as information:
                controller.on_translation_finished("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", "", notice)
            message = information.call_args.args[2]
            self.assertIn("Google Translate", message)
            self.assertIn("Dịch vụ AI đang quá tải", message)
            self.assertNotIn("temporarily overloaded", message)
            gui.current_project_state.set_setting.assert_called_with("translation_fallback_notice", notice)

    def test_prepare_workflow_forwards_reason_saves_it_and_clears_it_after_ai_success(self):
        env = {"OPENAI_PROVIDER": "google_ai_studio", "CAPCAP_AUTO_TRANSLATION_CONTEXT": "0",
               "CAPCAP_RUNTIME_PROFILE": "local", "CAPCAP_TRANSLATION_PARALLEL_BATCHES": "0"}
        provider = SimpleNamespace(model_name="gemini-test", is_configured=lambda: True,
                                   polish_batch=MagicMock(side_effect=self.api_error(503)))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, env), \
                patch("workflows.prepare_workflow.ResourceDownloadService.validate_pipeline_runtime", return_value=[]), \
                patch("workflows.prepare_workflow.ResourceDownloadService.validate_ocr_runtime", return_value=[]), \
                patch("workflows.prepare_workflow.subprocess.run"), \
                patch.object(TranslationOrchestrator, "_resolve_ai_provider", return_value=("google_ai_studio", provider)), \
                patch.object(GoogleWebTranslatorProvider, "translate_batch",
                      side_effect=lambda texts, **kwargs: ["Xin chào"] * len(texts)), \
                redirect_stdout(io.StringIO()):
            video = Path(directory) / "input.mp4"
            video.touch()
            workflow = PrepareWorkflow(directory)
            segments = [{"start": 0, "end": 1, "text": "你好"}, {"start": 1, "end": 2, "text": "再见"}]
            with patch.object(workflow.engine_runtime, "transcribe_video_ocr", return_value=segments):
                progress = []
                state = workflow.run(str(video), source_language="zh", transcription_engine="ocr",
                                     step_callback=lambda *update: progress.append(update))
                saved = workflow.project_service.load_project(workflow.project_service.project_file(state.project_root))
                notice = saved.settings["translation_fallback_notice"]
                self.assertIn("HTTP 503", notice)
                self.assertIn("Google Translate", notice)
                translated_updates = [message for phase, message, percent in progress if phase == "translation"]
                self.assertTrue(any("HTTP 503" in message and "2/2" in message for message in translated_updates))

                gui = MagicMock()
                gui.current_project_state = saved
                gui.is_ai_polish_enabled.return_value = True
                gui.current_translated_segment_models = workflow.project_service.load_segment_artifact(saved, "translation_final")
                controller = PipelineController(gui)
                footer = QLabel()
                controller.progress_dialog = SimpleNamespace(footer=footer, steps={})
                gui._pipeline_active = True
                with patch.object(i18n, "_current_language", "vi"), \
                        patch("ui.controllers.pipeline_controller.QMessageBox.information") as information:
                    controller._on_prepare_step_progress("translation", 100, detail=translated_updates[-1])
                    self.assertIn("Dịch vụ AI đang quá tải", footer.text())
                    controller._notify_translation_fallback_if_used()
                    self.assertIn("Dịch vụ AI đang quá tải", information.call_args.args[2])

                cached = workflow.run(str(video), source_language="zh", transcription_engine="ocr")
                self.assertEqual(cached.settings["translation_fallback_notice"], notice)
                self.assertEqual(provider.polish_batch.call_count, 1)
                cached.set_setting("translation_signature", "force-new-translation")
                workflow.project_service.save_project(cached)
                provider.polish_batch.side_effect = None
                provider.polish_batch.return_value = (["Xin chào", "Tạm biệt"], [], "google_ai_studio")
                success = workflow.run(str(video), source_language="zh", transcription_engine="ocr")
                self.assertEqual(success.settings["translation_fallback_notice"], "")

    def test_remote_adapter_reports_reason_and_accepts_older_server_responses(self):
        from engines.remote_translator_adapter import RemoteTranslatorAdapter

        notice = translation_failure_reason(self.api_error(429))
        status = MagicMock()
        response = {"segments": [{"text": "Xin chào"}],
                    "translation_status": [["Google Translate", notice]]}
        with patch("engines.remote_translator_adapter.remote_api_post", return_value=response):
            segments = RemoteTranslatorAdapter().translate_segments([{"text": "你好"}], status_callback=status)
        self.assertEqual(segments, response["segments"])
        status.assert_called_once_with("Google Translate", notice)
        with patch("engines.remote_translator_adapter.remote_api_post", return_value={"segments": []}):
            self.assertEqual(RemoteTranslatorAdapter().translate_segments([], status_callback=status), [])

    def test_ai_recovery_does_not_report_google_fallback(self):
        from translation.models import TranslationResult
        from ui.worker_adapters.processing_workers import TranslationWorker

        worker = TranslationWorker(
            srt_text="", model_path="", src_lang="zh", target_lang="vi", enable_polish=True,
            provider="google_ai_studio", segments=[{"start": 0, "end": 1, "text": "你好"}],
        )
        result = TranslationResult(
            success=True, segments=[{"start": 0, "end": 1, "text": "Xin chào"}],
            primary_provider="google_ai_studio", used_fallback=True, warnings=["Recovered using AI batches."],
        )
        finished = []
        worker.finished.connect(lambda *args: finished.append(args))
        with patch.object(TranslationOrchestrator, "translate_segments", return_value=result), \
                patch.object(TranslationOrchestrator, "_describe_ai_provider", return_value="Gemini"):
            worker.run()
        self.assertEqual(len(finished), 1)
        self.assertIn("Xin chào", finished[0][0])
        self.assertEqual(finished[0][1:], ("", ""))

    def test_remote_server_preserves_reason_for_client_ui(self):
        from remote_api_server import CapCapRemoteHandler

        notice = translation_failure_reason(self.api_error(503))

        def translate(segments, **kwargs):
            kwargs["status_callback"]("Google Translate", notice)
            return [{"start": 0, "end": 1, "text": "Xin chào"}]

        handler = CapCapRemoteHandler.__new__(CapCapRemoteHandler)
        with patch("remote_api_server.translate_segments", side_effect=translate):
            response = handler._handle_translate_segments({"segments": [{"text": "你好"}]})
        self.assertEqual(response["translation_status"], [("Google Translate", notice)])
        self.assertEqual(response["segments"][0]["text"], "Xin chào")


if __name__ == "__main__":
    unittest.main()
