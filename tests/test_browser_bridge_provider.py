import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.translation.errors import TranslationProviderError, TranslationValidationError
from app.translation.orchestrator import TranslationOrchestrator
from app.translation.providers.browser_bridge_provider import (
    BrowserAITimeoutError,
    BrowserBridgePolisherProvider,
    BrowserBridgeServer,
    RemoteBridgeClient,
    ensure_bridge_server_running,
    get_bridge_status,
    request_open_web_tab,
    stop_bridge_server,
)


class TestBrowserBridgeServerEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = BrowserBridgeServer(port=0)
        cls.server.start()
        cls.port = cls.server.actual_port
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _http_request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as resp:
                resp_body = resp.read().decode("utf-8")
                return resp.status, resp.headers, json.loads(resp_body) if resp_body else {}
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8")
            return err.code, err.headers, json.loads(err_body) if err_body else {}

    def test_options_cors(self):
        status, headers, body = self._http_request("OPTIONS", "/status")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("GET", headers.get("Access-Control-Allow-Methods", ""))

    def test_get_status_initial(self):
        status, headers, body = self._http_request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("port"), self.port)
        self.assertIn("sync_state", body)
        sync = body["sync_state"]
        self.assertIsInstance(sync, dict)
        # Check alias keys
        self.assertIn("status_text", sync)
        self.assertIn("translated_subs", sync)
        self.assertIn("total_subs", sync)

    def test_sync_state_idle_env_refresh(self):
        with patch.dict(os.environ, {"CAPCAP_BROWSER_AI_PLATFORM": "chatgpt", "CAPCAP_BROWSER_AI_TEMP_CHAT": "0"}):
            sync = self.server.get_sync_state()
            self.assertEqual(sync["platform"], "chatgpt")
            self.assertEqual(sync["platform_label"], "ChatGPT Web")
            self.assertFalse(sync["temporary_chat"])

    def test_poll_and_heartbeat(self):
        # Initial poll
        status, headers, body = self._http_request("POST", "/poll", body={"platforms": ["gemini", "chatgpt"]})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertFalse(body.get("has_task"))
        self.assertIn("sync_state", body)
        self.assertIn("canceled_task_ids", body)
        self.assertTrue(self.server.extension_connected)

        # GET /poll also works
        status, headers, body = self._http_request("GET", "/poll")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertIn("canceled_task_ids", body)

        # GET /heartbeat also works
        status, headers, body = self._http_request("GET", "/heartbeat?platforms=gemini")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertIn("sync_state", body)
        self.assertIn("canceled_task_ids", body)

    def test_heartbeat_does_not_pop_pending_task(self):
        # Enqueue a task
        task_id, event = self.server.enqueue_task(
            task_type="translate",
            payload={"prompt": "Task for heartbeat test", "platform": "gemini"},
        )
        self.assertGreaterEqual(len(self.server._pending_tasks), 1)

        # GET /heartbeat?platforms=gemini
        status, headers, body = self._http_request("GET", "/heartbeat?platforms=gemini")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertIn("sync_state", body)
        self.assertIn("canceled_task_ids", body)
        self.assertIsInstance(body["canceled_task_ids"], list)
        self.assertNotIn("has_task", body)
        self.assertNotIn("task", body)

        # POST /heartbeat also does not pop
        post_status, _, post_body = self._http_request("POST", "/heartbeat", body={"platforms": ["gemini"]})
        self.assertEqual(post_status, 200)
        self.assertTrue(post_body.get("ok"))
        self.assertIn("canceled_task_ids", post_body)
        self.assertNotIn("has_task", post_body)

        # Ensure task is STILL pending and popped by /poll
        poll_status, _, poll_body = self._http_request("GET", "/poll")
        self.assertEqual(poll_status, 200)
        self.assertTrue(poll_body.get("has_task"))
        self.assertEqual(poll_body.get("task", {}).get("id"), task_id)
        self.assertIn("canceled_task_ids", poll_body)

        # Clean up task
        self.server.record_task_result(task_id, success=True, text="done")

    def test_canceling_dispatched_vs_pending_task_canceled_ids(self):
        # 1. Enqueue task 1 and dispatch it via /poll
        task1_id, event1 = self.server.enqueue_task(
            task_type="translate",
            payload={"prompt": "Dispatched then canceled", "platform": "gemini"},
        )
        status, _, body = self._http_request("GET", "/poll")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("has_task"))
        self.assertEqual(body.get("task", {}).get("id"), task1_id)

        # Cancel task 1 after it was dispatched
        self.server.cancel_task(task1_id)
        self.assertTrue(event1.is_set())

        # 2. Enqueue task 2 but cancel it while still pending (never dispatched)
        task2_id, event2 = self.server.enqueue_task(
            task_type="translate",
            payload={"prompt": "Pending canceled", "platform": "gemini"},
        )
        self.server.cancel_task(task2_id)
        self.assertTrue(event2.is_set())

        # Check /heartbeat
        hb_status, _, hb_body = self._http_request("GET", "/heartbeat")
        self.assertEqual(hb_status, 200)
        hb_canceled = hb_body.get("canceled_task_ids", [])
        self.assertIn(task1_id, hb_canceled)
        self.assertNotIn(task2_id, hb_canceled)

        # Check /poll
        poll_status, _, poll_body = self._http_request("GET", "/poll")
        self.assertEqual(poll_status, 200)
        poll_canceled = poll_body.get("canceled_task_ids", [])
        self.assertIn(task1_id, poll_canceled)
        self.assertNotIn(task2_id, poll_canceled)

    def test_canceled_dispatched_ids_capped_at_50(self):
        server = BrowserBridgeServer(port=0)
        task_ids = []
        for i in range(60):
            tid, _ = server.enqueue_task("translate", {"index": i})
            server.pop_pending_task()
            server.cancel_task(tid)
            task_ids.append(tid)

        canceled = server.get_canceled_task_ids()
        self.assertEqual(len(canceled), 50)
        for tid in task_ids[:10]:
            self.assertNotIn(tid, canceled)
        for tid in task_ids[10:]:
            self.assertIn(tid, canceled)
        self.assertEqual(canceled, sorted(canceled))

    def test_enqueue_poll_and_result_flow(self):
        # Enqueue task
        task_id, event = self.server.enqueue_task(
            task_type="translate",
            payload={"prompt": "1. Hello", "platform": "gemini"},
        )
        self.assertEqual(len(self.server._pending_tasks), 1)

        # Extension polls task
        status, headers, body = self._http_request("GET", "/poll")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("has_task"))
        task = body.get("task")
        self.assertEqual(task.get("id"), task_id)
        self.assertEqual(task.get("prompt"), "1. Hello")

        # Extension posts result
        result_payload = {
            "task_id": task_id,
            "success": True,
            "text": "1. Xin chào",
            "error": "",
        }
        status, headers, body = self._http_request("POST", "/result", body=result_payload)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))

        # Event should be set immediately
        self.assertTrue(event.is_set())
        res = self.server.get_task_result(task_id)
        self.assertIsNotNone(res)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("text"), "1. Xin chào")


class TestBrowserBridgePolisherProvider(unittest.TestCase):
    def setUp(self):
        self.server = BrowserBridgeServer(port=0)
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def test_provider_initialization_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = BrowserBridgePolisherProvider(bridge_server=self.server)
            self.assertEqual(provider.platform, "gemini")
            self.assertTrue(provider.temporary_chat)
            self.assertEqual(provider.display_name, "Gemini Web")
            self.assertIn("Temp Chat", provider.model_name)
            self.assertTrue(provider.is_configured())

    def test_provider_initialization_chatgpt(self):
        with patch.dict(os.environ, {"CAPCAP_BROWSER_AI_PLATFORM": "chatgpt", "CAPCAP_BROWSER_AI_TEMP_CHAT": "0"}):
            provider = BrowserBridgePolisherProvider(bridge_server=self.server)
            self.assertEqual(provider.platform, "chatgpt")
            self.assertFalse(provider.temporary_chat)
            self.assertEqual(provider.display_name, "ChatGPT Web")
            self.assertIn("Standard", provider.model_name)

    def test_finish_session_enqueues_cleanup_when_temp_chat(self):
        provider = BrowserBridgePolisherProvider(platform="gemini", temporary_chat=True, bridge_server=self.server)
        provider.finish_session()
        self.assertEqual(len(self.server._pending_tasks), 1)
        task = self.server._pending_tasks[0]
        self.assertEqual(task["type"], "cleanup_session")
        self.assertEqual(task["platform"], "gemini")

    def test_finish_session_no_cleanup_when_standard_chat(self):
        provider = BrowserBridgePolisherProvider(temporary_chat=False, bridge_server=self.server)
        provider.finish_session()
        self.assertEqual(len(self.server._pending_tasks), 0)

    def test_polish_batch_successful_flow(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        # Mark extension connected
        self.server.update_heartbeat(["gemini"])

        def mock_extension_worker():
            # Wait for task to be queued
            for _ in range(50):
                task = self.server.pop_pending_task()
                if task:
                    self.server.record_task_result(
                        task_id=task["id"],
                        success=True,
                        text="1. Xin chào\n2. Thế giới",
                    )
                    break
                time.sleep(0.05)

        worker = threading.Thread(target=mock_extension_worker)
        worker.start()

        lines, warnings, provider_id = provider.polish_batch(
            source_texts=["Hello", "World"],
            src_lang="en",
            target_lang="vi",
            batch_number=1,
            total_batches=1,
            start_offset=0,
        )
        worker.join()

        self.assertEqual(lines, ["Xin chào", "Thế giới"])
        self.assertEqual(provider_id, "browser_ai")
        # Check sync_state progress
        sync = self.server.get_sync_state()
        self.assertEqual(sync["completed_cues"], 2)
        self.assertEqual(sync["progress_percent"], 100)

    def test_generate_text_successful_flow(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        self.server.update_heartbeat(["gemini"])
        status_during = []

        def mock_extension_worker():
            for _ in range(50):
                task = self.server.pop_pending_task()
                if task:
                    status_during.append(self.server.get_sync_state().get("status_message", ""))
                    self.server.record_task_result(
                        task_id=task["id"],
                        success=True,
                        text="Tóm tắt nội dung video",
                    )
                    break
                time.sleep(0.05)

        worker = threading.Thread(target=mock_extension_worker)
        worker.start()

        text = provider.generate_text(system_msg="Tóm tắt", user_msg="Đoạn văn", timeout=10)
        worker.join()
        self.assertEqual(text, "Tóm tắt nội dung video")
        self.assertTrue(any("có thể mất tới vài chục giây" in s for s in status_during))

    def test_extension_not_connected_raises_error(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        # Extension is not connected (heartbeat is None)
        with patch.object(time, "sleep", return_value=None):
            with patch.object(time, "monotonic", side_effect=[0.0, 1.0, 21.0, 22.0]):
                with self.assertRaises(TranslationProviderError) as ctx:
                    provider._execute_prompt_on_browser(system_msg="", user_msg="Hello", timeout=5)
                self.assertIn("Browser AI Extension is not connected", str(ctx.exception))
                self.assertIn("chrome://extensions", str(ctx.exception))

    def test_polish_batch_validation_error(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        self.server.update_heartbeat(["gemini"])

        def mock_extension_worker():
            for _ in range(50):
                task = self.server.pop_pending_task()
                if task:
                    # Returns only 1 item for 2 requested cues
                    self.server.record_task_result(
                        task_id=task["id"],
                        success=True,
                        text="1. Chỉ có một dòng",
                    )
                    break
                time.sleep(0.05)

        worker = threading.Thread(target=mock_extension_worker)
        worker.start()

        with self.assertRaises(TranslationValidationError):
            provider.polish_batch(
                source_texts=["Hello", "World"],
                src_lang="en",
                target_lang="vi",
                max_retries=1,
            )
        worker.join()

    def test_polish_batch_plain_lines_positional_mapping(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        self.server.update_heartbeat(["gemini"])

        def mock_extension_worker():
            for _ in range(50):
                task = self.server.pop_pending_task()
                if task:
                    self.server.record_task_result(
                        task_id=task["id"],
                        success=True,
                        text="Dòng một\nDòng hai\nDòng ba",
                    )
                    break
                time.sleep(0.05)

        worker = threading.Thread(target=mock_extension_worker)
        worker.start()

        lines, warnings, provider_id = provider.polish_batch(
            source_texts=["Line 1", "Line 2", "Line 3"],
            src_lang="en",
            target_lang="vi",
            batch_number=1,
            total_batches=1,
            start_offset=0,
        )
        worker.join()

        self.assertEqual(lines, ["Dòng một", "Dòng hai", "Dòng ba"])
        self.assertEqual(provider_id, "browser_ai")

    def test_polish_batch_plain_lines_count_mismatch_raises_validation_error(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        self.server.update_heartbeat(["gemini"])

        def mock_extension_worker():
            for _ in range(50):
                task = self.server.pop_pending_task()
                if task:
                    self.server.record_task_result(
                        task_id=task["id"],
                        success=True,
                        text="Dòng một\nDòng hai",
                    )
                    break
                time.sleep(0.05)

        worker = threading.Thread(target=mock_extension_worker)
        worker.start()

        with self.assertRaises(TranslationValidationError):
            provider.polish_batch(
                source_texts=["Line 1", "Line 2", "Line 3"],
                src_lang="en",
                target_lang="vi",
                max_retries=1,
            )
        worker.join()

    def test_browser_ai_timeout_error_inheritance(self):
        self.assertTrue(issubclass(BrowserAITimeoutError, TranslationProviderError))

    def test_generate_text_timeout_no_retry(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        provider.MIN_GENERATE_TIMEOUT_SECONDS = 0.6
        provider.CONNECT_WAIT_SECONDS = 0.5
        self.server.update_heartbeat(["gemini"])

        with self.assertRaises(TranslationProviderError) as ctx:
            provider.generate_text(
                system_msg="Tóm tắt",
                user_msg="Đoạn văn",
                timeout=1,
                max_retries=2,
            )

        self.assertIn("generate_text failed", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, BrowserAITimeoutError)
        self.assertEqual(len(self.server._tasks), 1)

    def test_generate_text_effective_timeout_floor(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        provider.MIN_GENERATE_TIMEOUT_SECONDS = 1.5
        provider.CONNECT_WAIT_SECONDS = 0.5
        self.server.update_heartbeat(["gemini"])

        def delayed_worker():
            time.sleep(0.6)
            task = self.server.pop_pending_task()
            if task:
                self.server.record_task_result(
                    task_id=task["id"],
                    success=True,
                    text="Phân tích thành công",
                )

        worker = threading.Thread(target=delayed_worker)
        worker.start()

        # timeout=0.1 would fail without the 1.5s floor because delayed_worker waits 0.6s
        text = provider.generate_text(
            system_msg="Tóm tắt",
            user_msg="Đoạn văn",
            timeout=0.1,
        )
        worker.join()
        self.assertEqual(text, "Phân tích thành công")

    def test_polish_batch_timeout_no_retry(self):
        provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        provider.MIN_GENERATE_TIMEOUT_SECONDS = 0.5
        provider.CONNECT_WAIT_SECONDS = 0.5
        self.server.update_heartbeat(["gemini"])

        with self.assertRaises(TranslationProviderError) as ctx:
            provider.polish_batch(
                source_texts=["Hello", "World"],
                src_lang="en",
                target_lang="vi",
                timeout=0.5,
                max_retries=2,
            )

        self.assertIn("failed", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, BrowserAITimeoutError)
        self.assertEqual(len(self.server._tasks), 1)


class TestOrchestratorBrowserBridgeIntegration(unittest.TestCase):
    def setUp(self):
        self.orch = TranslationOrchestrator()

    def test_resolve_ai_provider_browser_ai(self):
        with patch.dict(os.environ, {"CAPCAP_BROWSER_AI_PLATFORM": "gemini", "CAPCAP_BROWSER_AI_TEMP_CHAT": "1"}):
            provider_type, polisher = self.orch._resolve_ai_provider("browser_ai")
            self.assertEqual(provider_type, "browser_ai")
            self.assertIsInstance(polisher, BrowserBridgePolisherProvider)
            self.assertEqual(polisher.platform, "gemini")
            self.assertTrue(polisher.temporary_chat)

    def test_resolve_ai_provider_chatgpt_web(self):
        provider_type, polisher = self.orch._resolve_ai_provider("chatgpt_web")
        self.assertEqual(provider_type, "browser_ai")
        self.assertIsInstance(polisher, BrowserBridgePolisherProvider)
        self.assertEqual(polisher.platform, "chatgpt")

    def test_describe_ai_provider(self):
        desc = self.orch._describe_ai_provider("browser_ai")
        self.assertIn("Web Browser AI", desc)

    def test_build_ai_batches_browser_ai_limits(self):
        # 150 items should exceed the 120 cue limit for safe_single_pass on browser_ai
        source_texts = [f"Line {i}" for i in range(150)]
        batches, full_context = self.orch._build_ai_batches(
            source_texts=source_texts,
            translated_texts=None,
            requested_max_segments=0,
            provider_type="browser_ai",
        )
        self.assertFalse(full_context)
        # Should chunk by default_max_segments = 80
        self.assertGreater(len(batches), 1)
        self.assertLessEqual(len(batches[0][0]), 80)

    @patch("webbrowser.open")
    def test_request_open_web_tab_webbrowser_fallback(self, mock_webbrowser):
        # When extension is not connected, fallback to webbrowser.open
        server = ensure_bridge_server_running()
        server._last_heartbeat_time = None  # Ensure disconnected
        res = request_open_web_tab("gemini", temporary_chat=True)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("via"), "webbrowser")
        self.assertIn("gemini.google.com", res.get("url"))
        mock_webbrowser.assert_called_once()

    def test_request_open_web_tab_extension_task(self):
        server = ensure_bridge_server_running()
        server.update_heartbeat(["chatgpt"])
        res = request_open_web_tab("chatgpt", temporary_chat=True)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("via"), "extension")
        self.assertIn("temporary-chat=true", res.get("url"))
        # Verify task enqueued
        task = server.pop_pending_task()
        self.assertIsNotNone(task)
        self.assertEqual(task.get("type"), "open_tab")
        self.assertEqual(task.get("platform"), "chatgpt")


class TestRemoteBridgeClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = BrowserBridgeServer(port=0)
        cls.server.start()
        cls.client = RemoteBridgeClient(port=cls.server.actual_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_remote_client_status_and_sync(self):
        status = self.client.get_status()
        self.assertTrue(status.get("ok"))
        self.assertEqual(status.get("port"), self.server.actual_port)

        self.client.update_sync_state(status_message="IPC Test Message")
        sync = self.client.get_sync_state()
        self.assertEqual(sync.get("status_message"), "IPC Test Message")

    def test_remote_client_enqueue_and_result(self):
        task_id, event = self.client.enqueue_task(
            task_type="translate",
            payload={"prompt": "Translate this", "platform": "gemini"},
            timeout=10,
        )
        self.assertTrue(bool(task_id))
        self.assertFalse(event.is_set())

        # Verify server received the task in pending_tasks
        server_task = self.server.pop_pending_task()
        self.assertIsNotNone(server_task)
        self.assertEqual(server_task.get("id"), task_id)

        # Simulate extension posting result
        self.server.record_task_result(task_id, success=True, text="1. Translated")

        # Wait for client event to be set
        self.assertTrue(event.wait(timeout=2.0))
        result = self.client.get_task_result(task_id)
        self.assertIsNotNone(result)
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("text"), "1. Translated")

    def test_remote_client_fire_and_forget(self):
        task_id = self.client.enqueue_fire_and_forget_task({
            "type": "cleanup_session",
            "session_id": "sess-test",
        })
        self.assertTrue(bool(task_id))
        server_task = self.server.pop_pending_task()
        self.assertIsNotNone(server_task)
        self.assertEqual(server_task.get("id"), task_id)

    def test_remote_client_cancel_task(self):
        task_id, event = self.client.enqueue_task(
            task_type="translate",
            payload={"prompt": "Cancel me"},
        )
        self.client.cancel_task(task_id)
        # Should be removed from pending tasks
        self.assertNotIn(task_id, [t.get("id") for t in self.server._pending_tasks])


class TestUiAiPolishEnabled(unittest.TestCase):
    def test_is_ai_polish_enabled_browser_ai(self):
        from ui.main_window import VideoTranslatorGUI
        for prov in ("browser_ai", "gemini_web", "chatgpt_web"):
            with patch.dict(os.environ, {"OPENAI_PROVIDER": prov}):
                dummy = MagicMock(spec=VideoTranslatorGUI)
                res = VideoTranslatorGUI.is_ai_polish_enabled(dummy)
                self.assertTrue(res, f"Expected is_ai_polish_enabled() to return True for {prov}")

    def test_is_ai_polish_enabled_google_web(self):
        from ui.main_window import VideoTranslatorGUI
        with patch.dict(os.environ, {"OPENAI_PROVIDER": "google"}):
            dummy = MagicMock(spec=VideoTranslatorGUI)
            dummy.translator_ai_cb = None
            res = VideoTranslatorGUI.is_ai_polish_enabled(dummy)
            self.assertFalse(res)


if __name__ == "__main__":
    unittest.main()
