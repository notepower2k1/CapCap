import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
from translation.errors import TranslationProviderError, TranslationValidationError
from translation.providers.browser_bridge_provider import BrowserBridgePolisherProvider, BrowserAITimeoutError


class TestBrowserFormatRetry(unittest.TestCase):
    def run_batch(self, provider, **kwargs):
        return provider.polish_batch(source_texts=["Hello", "Bye"], src_lang="en", target_lang="vi",
                                     context_guidance="A calls B anh; A refers to herself as em.", **kwargs)

    def test_invalid_reply_retries_once_with_same_rules_in_fresh_chat(self):
        server = MagicMock()
        provider = BrowserBridgePolisherProvider(bridge_server=server)
        with patch.object(provider, "_execute_prompt_on_browser", side_effect=["1. incomplete", "1. Hello\n2. Bye"]) as execute:
            self.assertEqual(self.run_batch(provider)[0], ["Hello", "Bye"])
        first, retry = [call.kwargs for call in execute.call_args_list]
        self.assertFalse(first.pop("fresh_chat"))
        self.assertTrue(retry.pop("fresh_chat"))
        self.assertEqual(first, retry)
        self.assertIn("A calls B anh", retry["system_msg"])
        self.assertEqual(sum("completed_cues" in call.kwargs and call.kwargs["completed_cues"] == 2
                             for call in server.update_sync_state.call_args_list), 1)

    def test_second_invalid_reply_exhausts_retry(self):
        provider = BrowserBridgePolisherProvider(bridge_server=MagicMock())
        with patch.object(provider, "_execute_prompt_on_browser", return_value="1. incomplete") as execute:
            with self.assertRaises(TranslationValidationError):
                self.run_batch(provider, _no_split=True)
        self.assertEqual(execute.call_count, 2)

    def test_operational_errors_never_retry(self):
        for error in [BrowserAITimeoutError("timeout"), TranslationProviderError("blocked"),
                      TranslationProviderError("tab closed"), RuntimeError("connection lost")]:
            with self.subTest(error=str(error)):
                provider = BrowserBridgePolisherProvider(bridge_server=MagicMock())
                with patch.object(provider, "_execute_prompt_on_browser", side_effect=error) as execute:
                    with self.assertRaises(TranslationProviderError):
                        self.run_batch(provider)
                self.assertEqual(execute.call_count, 1)

    def test_two_invalid_replies_split_once_and_keep_rules_and_atomic_progress(self):
        server = MagicMock()
        provider = BrowserBridgePolisherProvider(bridge_server=server)
        provider.parallel_batches = True
        with patch.object(provider, "_execute_prompt_on_browser", side_effect=["99. bad", "99. bad", "1. Hello", "1. Bye"]) as execute:
            self.assertEqual(self.run_batch(provider)[0], ["Hello", "Bye"])
        self.assertEqual(execute.call_count, 4)
        for call in execute.call_args_list[1:]:
            self.assertTrue(call.kwargs["fresh_chat"])
            self.assertIn("A calls B anh", call.kwargs["system_msg"])
        self.assertEqual(provider._parallel_completed_cues, 2)

    def test_split_operational_failure_stops_without_attempting_second_half(self):
        provider = BrowserBridgePolisherProvider(bridge_server=MagicMock())
        provider.parallel_batches = True
        with patch.object(provider, "_execute_prompt_on_browser", side_effect=["99. bad", "99. bad", TranslationProviderError("blocked")]) as execute:
            with self.assertRaises(TranslationProviderError):
                self.run_batch(provider)
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(provider._parallel_completed_cues, 0)

    def test_split_validation_failure_does_not_recurse(self):
        provider = BrowserBridgePolisherProvider(bridge_server=MagicMock())
        with patch.object(provider, "_execute_prompt_on_browser", return_value="99. bad") as execute:
            with self.assertRaises(TranslationValidationError):
                self.run_batch(provider)
        self.assertEqual(execute.call_count, 3)

    def test_empty_response_is_no_response_not_format_retry(self):
        provider = BrowserBridgePolisherProvider(bridge_server=MagicMock())
        with patch.object(provider, "_execute_prompt_on_browser", return_value="") as execute:
            with self.assertRaises(TranslationProviderError):
                self.run_batch(provider)
        self.assertEqual(execute.call_count, 1)

    def test_cancellation_after_malformed_reply_never_resubmits(self):
        provider = BrowserBridgePolisherProvider(bridge_server=MagicMock())
        canceled = [False]
        def reply(**kwargs):
            canceled[0] = True
            return "1. incomplete"
        with patch.object(provider, "_execute_prompt_on_browser", side_effect=reply) as execute:
            with self.assertRaises(InterruptedError):
                self.run_batch(provider, is_canceled=lambda: canceled[0])
        self.assertEqual(execute.call_count, 1)
