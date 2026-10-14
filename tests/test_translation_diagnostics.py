import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx
from openai import DefaultHttpxClient

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from app.translation.errors import TranslationProviderError, TranslationValidationError
from app.translation.orchestrator import TranslationOrchestrator
from app.translation.providers import gemini_polisher


class TestTranslationDiagnostics(unittest.TestCase):
    def make_provider(self, handler, provider_id="google_ai_studio", module=gemini_polisher):
        provider = module.OpenAICompatiblePolisherProvider(
            provider_id=provider_id, display_name="Diagnostic Test", env_prefix="DIAGNOSTIC_TEST",
            default_base_url=("http://localhost:11434/v1" if provider_id == "ollama"
                              else "https://generativelanguage.googleapis.com/v1beta/openai/"),
            default_model="qwen35b" if provider_id == "ollama" else "gemini-2.5-pro",
        )
        provider.api_key = "diagnostic-secret-key"
        factory = module.OpenAI
        with patch.object(module, "OpenAI", side_effect=lambda **kwargs: factory(
                http_client=DefaultHttpxClient(transport=httpx.MockTransport(handler)), **kwargs)):
            client = provider._get_client()
        self.addCleanup(client.close)
        return provider

    @staticmethod
    def response(text="1. Xin chào", finish_reason="stop"):
        return httpx.Response(200, headers={"x-request-id": "test-request-id"}, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": "test-returned-model",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180,
                      "completion_tokens_details": {"reasoning_tokens": 60}},
        })

    def test_sdk_retry_logs_actual_http_attempts_without_changing_parameters(self):
        requests = []

        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(429, headers={"retry-after": "1"},
                                      json={"error": {"message": "Rate limit", "type": "rate_limit"}})
            return self.response("1. Xin chào\n2. Tạm biệt")

        provider = self.make_provider(handler)
        log = io.StringIO()
        with redirect_stdout(log), patch("openai._base_client.time.sleep"), \
                patch.dict(os.environ, {"CAPCAP_GEMINI_REASONING_EFFORT": "medium"}):
            result = provider.polish_batch(source_texts=["你好", "再见"], src_lang="zh", target_lang="vi",
                                           timeout=300, max_tokens=4500)
        self.assertEqual(result[0], ["Xin chào", "Tạm biệt"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(provider._client.max_retries, 2)
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["max_tokens"], 4500)
        self.assertEqual(payload["reasoning_effort"], "medium")
        self.assertEqual(requests[0].extensions["timeout"]["read"], 300)
        for marker in ("sdk_retry=0", "sdk_retry=1", "status=429", "retry_after=1", "status=200",
                       "operation=batch attempt=1/4", "finish_reason=stop", "completion_tokens=80",
                       "reasoning_tokens=60", "request_id=test-request-id", "returned_cues=2"):
            self.assertIn(marker, log.getvalue())
        self.assertNotIn(provider.api_key, log.getvalue())

    def test_truncated_response_logs_finish_reason_and_missing_cues(self):
        requests = []

        def handler(request):
            requests.append(request)
            return self.response("1. Xin chào", finish_reason="length")

        provider = self.make_provider(handler)
        log = io.StringIO()
        with redirect_stdout(log), self.assertRaises(TranslationValidationError):
            provider.polish_batch(source_texts=["你好", "再见", "谢谢"], src_lang="zh", target_lang="vi")
        self.assertEqual(len(requests), 1)
        for marker in ("finish_reason=length", "expected_cues=3 returned_cues=1", "missing_ids=[2, 3]",
                       "response_head=", "response_tail="):
            self.assertIn(marker, log.getvalue())

    def test_timeout_logs_sdk_retries_and_exception_type(self):
        requests = []

        def handler(request):
            requests.append(request)
            raise httpx.ReadTimeout("Read timed out", request=request)

        provider = self.make_provider(handler)
        log = io.StringIO()
        with redirect_stdout(log), patch("openai._base_client.time.sleep"), \
                self.assertRaises(TranslationProviderError):
            provider.generate_text(system_msg="context", user_msg="cues", max_retries=1)
        self.assertEqual(len(requests), 3)
        self.assertIn("sdk_retry=2", log.getvalue())
        self.assertIn("error_type=APITimeoutError http_status=None", log.getvalue())

    def test_manual_retries_and_error_logs_redact_api_key(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(429, headers={"x-request-id": "quota-request"},
                                  json={"error": {"message": "Quota for diagnostic-secret-key", "type": "quota"}})

        provider = self.make_provider(handler)
        log = io.StringIO()
        with redirect_stdout(log), patch("openai._base_client.time.sleep"), \
                self.assertRaises(TranslationProviderError) as raised:
            provider.generate_text(system_msg="context", user_msg="cues")
        self.assertEqual(len(requests), 12)
        self.assertEqual(log.getvalue().count("START operation=context"), 4)
        self.assertIn("attempt=4/4", log.getvalue())
        self.assertIn("error_type=RateLimitError http_status=429", log.getvalue())
        self.assertIn("[redacted]", log.getvalue())
        self.assertNotIn(provider.api_key, log.getvalue() + str(raised.exception))

    def test_wait_message_stops_when_request_finishes(self):
        def handler(request):
            threading.Event().wait(0.06)
            return self.response()

        provider = self.make_provider(handler)
        log = io.StringIO()
        with redirect_stdout(log), patch.object(gemini_polisher, "REQUEST_LOG_INTERVAL", 0.01):
            provider.polish_batch(source_texts=["你好"], src_lang="zh", target_lang="vi")
        self.assertIn("WAIT operation=batch", log.getvalue())
        self.assertIn("elapsed=", log.getvalue())
        self.assertFalse(any(thread.name == "translation-request-log" for thread in threading.enumerate()))

    def test_real_batches_above_300_record_range_progress_and_fallback(self):
        segments = [{"start": i * 3, "end": i * 3 + 2, "text": f"测试第{i + 1}句"} for i in range(650)]
        for provider_id, batch_size in (("google_ai_studio", 50), ("ollama", 25)):
            for fail_at_301 in (False, True):
                with self.subTest(provider=provider_id, fail_at_301=fail_at_301):
                    requests = []

                    def handler(request):
                        payload = json.loads(request.content)
                        requests.append(payload)
                        if "expert dialogue and script analyzer" in payload["messages"][0]["content"]:
                            return self.response("### Hồ sơ nhân vật:\n- A gọi B là anh, xưng tôi.")
                        source_lines = payload["messages"][1]["content"].splitlines()
                        failed = fail_at_301 and "测试第301句" in source_lines[0]
                        count = len(source_lines) - int(failed)
                        return self.response("\n".join(f"{i + 1}. Tôi gọi anh {i + 1}" for i in range(count)),
                                             "length" if failed else "stop")

                    provider = self.make_provider(handler, provider_id)
                    orchestrator = TranslationOrchestrator()
                    batches = []
                    log = io.StringIO()
                    env = {"CAPCAP_AUTO_TRANSLATION_CONTEXT": "1", "CAPCAP_TRANSLATION_PARALLEL_BATCHES": "0",
                           "CAPCAP_AI_TRANSLATION_MAX_SEGMENTS": str(batch_size),
                           "CAPCAP_AI_TRANSLATION_BATCH_TOKENS": "1800", "CAPCAP_AI_TRANSLATION_MAX_CHARS": "12000"}
                    with redirect_stdout(log), patch.dict(os.environ, env), \
                            patch.object(orchestrator, "_resolve_ai_provider", return_value=(provider_id, provider)), \
                            patch.object(orchestrator.google_web, "translate_batch",
                                         side_effect=lambda texts, **kwargs: ["FREE"] * len(texts)) as google, \
                            patch("app.translation.orchestrator.time.sleep"):
                        result = orchestrator.translate_segments(
                            segments=segments, src_lang="zh", target_lang="vi",
                            batch_callback=lambda start, cues: batches.append((start, len(cues))),
                        )
                    self.assertTrue(result.success)
                    self.assertEqual(len(result.segments), 650)
                    self.assertIn("Context analysis: sampled_cues=200/650", log.getvalue())
                    self.assertIn("mode=sequential", log.getvalue())
                    self.assertIn(f"cues=301-{300 + batch_size}", log.getvalue())
                    if fail_at_301:
                        self.assertTrue(result.used_fallback)
                        self.assertTrue(google.called)
                        self.assertIn("Batch ERROR", log.getvalue())
                        self.assertIn("error_type=TranslationValidationError", log.getvalue())
                        self.assertIn("fallback_scope=all 650 cues", log.getvalue())
                    else:
                        self.assertFalse(result.used_fallback)
                        google.assert_not_called()
                        self.assertIn("cues=", log.getvalue())
                        self.assertEqual(sum(count for start, count in batches), 650)
                        self.assertIn((300, batch_size), batches)

    def test_full_context_recovery_and_parallel_workers_keep_batch_ranges(self):
        requests = []

        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            count = len(payload["messages"][1]["content"].splitlines())
            failed = len(requests) == 1
            return self.response("\n".join(f"{i + 1}. Xin chào" for i in range(count - int(failed))),
                                 "length" if failed else "stop")

        provider = self.make_provider(handler)
        orchestrator = TranslationOrchestrator()
        log = io.StringIO()
        env = {"CAPCAP_TRANSLATION_PARALLEL_BATCHES": "1", "CAPCAP_AI_TRANSLATION_MAX_SEGMENTS": "4"}
        with redirect_stdout(log), patch.dict(os.environ, env), \
                patch.object(orchestrator, "_resolve_ai_provider", return_value=("google_ai_studio", provider)):
            result = orchestrator.translate_segments(
                segments=[{"start": i, "end": i + 1, "text": "你好"} for i in range(12)],
                context_guidance="__SKIP__",
            )
        self.assertTrue(result.success)
        self.assertEqual(len(requests), 4)
        for marker in ("mode=full_context", "cues=1-12", "cues=1-4", "cues=5-8", "cues=9-12",
                       "Full-context response incomplete"):
            self.assertIn(marker, log.getvalue())

    def test_native_translation_worker_logs_are_saved_by_runtime_collector(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication
        # Match python ui/gui.py even when another test has prepended app/.
        with patch.object(sys, "path", [str(ROOT / "ui"), *sys.path]):
            from ui.gui import _LogTee, _RuntimeLogCollector
        from ui.worker_adapters.processing_workers import TranslationWorker
        from translation.orchestrator import TranslationOrchestrator as WorkerOrchestrator

        application = QApplication.instance() or QApplication([])
        provider = self.make_provider(lambda request: self.response("1. Xin chào\n2. Tạm biệt"))
        worker = TranslationWorker(
            srt_text="", model_path="", src_lang="zh", target_lang="vi", enable_polish=True,
            provider="google_ai_studio", context_guidance="__SKIP__",
            segments=[{"start": i, "end": i + 1, "text": "你好"} for i in range(2)],
        )
        results = []
        progress = []
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)

        def on_finished(srt, error, notice):
            results.append((srt, error, notice))
            loop.quit()

        worker.finished.connect(on_finished)
        worker.progress.connect(lambda completed, total: progress.append((completed, total)))
        with tempfile.TemporaryDirectory() as directory, \
                patch("runtime_paths.workspace_root", return_value=directory), \
                patch.object(WorkerOrchestrator, "_resolve_ai_provider", return_value=("google_ai_studio", provider)):
            collector = _RuntimeLogCollector()
            with redirect_stdout(_LogTee(io.StringIO(), collector)):
                worker.start()
                timer.start(5000)
                loop.exec()
                timer.stop()
                self.assertTrue(worker.wait(5000))
                application.processEvents()
            saved = Path(collector._file_path).read_text(encoding="utf-8")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1:], ("", ""))
        self.assertIn("Xin chào", results[0][0])
        self.assertEqual(progress, [(2, 2)])
        for marker in ("Batch START", "cues=1-2", "[AI Request]", "HTTP SEND", "HTTP RESPONSE status=200",
                       "finish_reason=stop", "VALIDATION expected_cues=2 returned_cues=2", "Batch DONE"):
            self.assertIn(marker, saved)
        self.assertNotIn(provider.api_key, saved)


if __name__ == "__main__":
    unittest.main()
