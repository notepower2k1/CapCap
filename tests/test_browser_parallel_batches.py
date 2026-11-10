import concurrent.futures
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    sys.path.insert(0, str(directory))

from app.translation.errors import TranslationProviderError
from app.translation.orchestrator import TranslationOrchestrator
from app.translation.providers.browser_bridge_provider import BrowserBridgePolisherProvider, BrowserBridgeServer


class TestBrowserParallelBatches(unittest.TestCase):
    def setUp(self):
        self.server = BrowserBridgeServer(port=0)
        self.server.update_heartbeat(["chatgpt", "gemini"])
        self.provider = BrowserBridgePolisherProvider(bridge_server=self.server)
        self.provider.parallel_batches = True
        self.provider.begin_batch_requests()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def tearDown(self):
        self.provider.cancel_pending()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.server.server_close()

    def next_task(self, future=None):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = self.server.pop_pending_task()
            if task:
                return task
            if future is not None and future.done():
                future.result()  # Surface errors immediately instead of waiting.
            time.sleep(0.005)
        self.fail("No browser task received")

    def batch(self, number, **kwargs):
        return self.provider.polish_batch(
            source_texts=[f"source-{number}"], src_lang="en", target_lang="vi",
            batch_number=number, total_batches=3, total_cues=3,
            start_offset=number - 1, max_retries=kwargs.pop("max_retries", 1), **kwargs,
        )

    def test_unique_sessions_and_monotonic_progress_for_out_of_order_replies(self):
        first = self.pool.submit(self.batch, 1)
        first_task = self.next_task(first)
        second = self.pool.submit(self.batch, 2)
        second_task = self.next_task(second)
        self.assertNotEqual(first_task["session_id"], second_task["session_id"])
        self.assertTrue(first_task["isolated"])
        self.assertTrue(second_task["isolated"])
        self.server.record_task_result(second_task["id"], True, text="1. second")
        self.assertEqual(second.result(timeout=3)[0], ["second"])
        self.assertEqual(self.server.get_sync_state()["completed_cues"], 1)
        third = self.pool.submit(self.batch, 3)
        third_task = self.next_task(third)
        self.assertEqual(self.server.get_sync_state()["completed_cues"], 1)
        self.server.record_task_result(first_task["id"], True, text="1. first")
        self.assertEqual(first.result(timeout=3)[0], ["first"])
        self.assertEqual(self.server.get_sync_state()["completed_cues"], 2)
        self.server.record_task_result(third_task["id"], True, text="1. third")
        self.assertEqual(third.result(timeout=3)[0], ["third"])
        self.assertEqual(self.server.get_sync_state()["progress_percent"], 100)

    def test_cancel_pending_stops_both_workers_without_retrying(self):
        futures = [self.pool.submit(self.batch, i, max_retries=2) for i in (1, 2)]
        ids = {self.next_task()["id"], self.next_task()["id"]}
        self.provider.cancel_pending()
        for future in futures:
            with self.assertRaises(TranslationProviderError):
                future.result(timeout=3)
        self.assertEqual(set(self.server.get_canceled_task_ids()), ids)
        self.assertIsNone(self.server.pop_pending_task())
        with self.assertRaises(TranslationProviderError):
            self.batch(3)
        self.assertIsNone(self.server.pop_pending_task())

    def test_orchestrator_limits_workers_merges_by_index_and_shares_context(self):
        orchestrator = TranslationOrchestrator()
        texts = [f"source-{i}" for i in range(3)]
        batches = [([text], None, 1024, None) for text in texts]
        updates = []
        with patch.dict(os.environ, {"CAPCAP_BROWSER_AI_WORKERS": "2"}), patch.object(
            orchestrator, "_build_ai_batches", return_value=(batches, False)
        ):
            future = self.pool.submit(
                orchestrator._run_ai_batches, polisher=self.provider, provider_type="browser_ai",
                source_texts=texts, translated_texts=None, src_lang="en", target_lang="vi",
                style_instruction="", context_guidance="Use Anna and Ben consistently.", polish_batch_size=1,
                base_segments=[{"text": text} for text in texts],
                batch_callback=lambda start, cues: updates.append((start, cues[0]["text"])),
            )
            a, b = self.next_task(future), self.next_task(future)
            self.assertIsNone(self.server.pop_pending_task())
            # Identify requests by their source text, independent of thread scheduling.
            task_index = lambda task: next(i for i, text in enumerate(texts) if text in task["prompt"])
            for task in (a, b):
                self.assertIn("Use Anna and Ben consistently.", task["prompt"])
            index_b = task_index(b)
            self.server.record_task_result(b["id"], True, text=f"1. translated-{index_b}")
            c = self.next_task(future)
            self.assertEqual(task_index(c), 2)
            self.assertIn("Use Anna and Ben consistently.", c["prompt"])
            self.server.record_task_result(c["id"], True, text="1. translated-2")
            self.server.record_task_result(a["id"], True, text=f"1. translated-{task_index(a)}")
            result, providers, warnings = future.result(timeout=3)
        self.assertEqual(result, [f"translated-{i}" for i in range(3)])
        self.assertEqual(sorted(updates), [(i, f"translated-{i}") for i in range(3)])
        self.assertEqual(providers, ["browser_ai"])
        self.assertEqual(warnings, [])
        self.assertEqual(self.server.get_sync_state()["completed_cues"], 3)

    def test_failed_parallel_browser_batch_preserves_completed_batch_and_cancels_pending(self):
        orchestrator = TranslationOrchestrator()
        segments = [{"start": i, "end": i + 1, "text": f"source-{i}"} for i in range(3)]
        batches = [([segment["text"]], None, 1024, None) for segment in segments]
        updates = []
        with patch.dict(os.environ, {"CAPCAP_BROWSER_AI_WORKERS": "2", "CAPCAP_AUTO_TRANSLATION_CONTEXT": "0"}), patch.object(
            orchestrator, "_resolve_ai_provider", return_value=("browser_ai", self.provider)
        ), patch.object(orchestrator, "_build_ai_batches", return_value=(batches, False)), patch.object(
            orchestrator.google_web, "translate_batch", side_effect=lambda texts, **_: ["Google-" + text for text in texts]
        ) as google:
            future = self.pool.submit(
                orchestrator.translate_segments, segments=segments, src_lang="en",
                batch_callback=lambda start, cues: updates.append(start),
            )
            a, b = self.next_task(future), self.next_task(future)
            if "source-1" in a["prompt"]:
                a, b = b, a
            self.server.record_task_result(b["id"], True, text="1. AI-1")
            c = self.next_task(future)
            self.assertIn("source-2", c["prompt"])
            # Invalid output fails immediately without the transient-error retry.
            self.server.record_task_result(a["id"], True, text="99. invalid numbering")
            result = future.result(timeout=3)
        self.assertTrue(result.success)
        self.assertTrue(result.used_fallback)
        self.assertEqual([segment["text"] for segment in result.segments], ["Google-source-0", "AI-1", "Google-source-2"])
        self.assertEqual([segment["provider"] for segment in result.segments], ["google-web", "browser_ai", "google-web"])
        self.assertEqual([call.args[0] for call in google.call_args_list], [["source-0"], ["source-2"]])
        self.assertIn(c["id"], self.server.get_canceled_task_ids())
        self.assertEqual(sorted(updates), [0, 1, 2])

    def test_browser_parallel_setting_is_opt_in_and_independent_of_api_parallel_setting(self):
        orchestrator = TranslationOrchestrator()
        batches = [([str(i)], None, 1024, None) for i in range(5)]
        for workers in ("1", "2", "3", "4", "0", "5", "-1", "garbage"):
            with self.subTest(workers=workers), patch.dict(os.environ, {
                "CAPCAP_BROWSER_AI_WORKERS": workers, "CAPCAP_TRANSLATION_PARALLEL_BATCHES": "1"
            }), patch.object(orchestrator, "_build_ai_batches", return_value=(batches, False)), patch.object(
                orchestrator, "_run_ai_batch_requests", return_value=([], [], [])
            ) as parallel, patch.object(
                orchestrator, "_run_ai_batches_sequential", return_value=([], [], [])
            ) as sequential:
                orchestrator._run_ai_batches(
                    polisher=self.provider, provider_type="browser_ai", source_texts=[str(i) for i in range(5)],
                    translated_texts=None, src_lang="en", target_lang="vi", style_instruction="", polish_batch_size=1,
                )
                if workers in ("2", "3", "4"):
                    self.assertEqual(parallel.call_args.kwargs["max_workers"], int(workers))
                    self.assertTrue(self.provider.parallel_batches)
                    sequential.assert_not_called()
                else:
                    self.assertFalse(self.provider.parallel_batches)
                    sequential.assert_called_once()
                    parallel.assert_not_called()

    def test_single_batch_resets_previous_translation_progress_in_parallel_mode(self):
        orchestrator = TranslationOrchestrator()
        self.server.update_sync_state(completed_cues=99, progress_percent=100)
        with patch.dict(os.environ, {"CAPCAP_BROWSER_AI_WORKERS": "2"}), patch.object(
            orchestrator, "_build_ai_batches", return_value=([(["hello"], None, 1024, None)], False)
        ):
            future = self.pool.submit(
                orchestrator._run_ai_batches, polisher=self.provider, provider_type="browser_ai",
                source_texts=["hello"], translated_texts=None, src_lang="en", target_lang="vi",
                style_instruction="", polish_batch_size=1,
            )
            task = self.next_task(future)
            self.assertEqual(self.server.get_sync_state()["completed_cues"], 0)
            self.assertEqual(self.server.get_sync_state()["progress_percent"], 0)
            self.server.record_task_result(task["id"], True, text="1. translated")
            self.assertEqual(future.result(timeout=3)[0], ["translated"])
        self.assertEqual(self.server.get_sync_state()["completed_cues"], 1)


if __name__ == "__main__":
    unittest.main()
