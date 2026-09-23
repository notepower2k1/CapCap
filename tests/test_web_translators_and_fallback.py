import unittest
from unittest.mock import MagicMock, patch

from app.translation.orchestrator import TranslationOrchestrator
from app.translation.providers.bing_web_translator import BingWebTranslatorProvider
from app.translation.providers.google_web_translator import GoogleWebTranslatorProvider


class TestGoogleWebTranslator(unittest.TestCase):
    def setUp(self):
        self.provider = GoogleWebTranslatorProvider()

    def test_empty_list_returns_empty(self):
        self.assertEqual(self.provider.translate_batch([], src_lang="en", target_lang="vi"), [])

    @patch.object(GoogleWebTranslatorProvider, "_translate_text")
    def test_delimiter_batch_success(self, mock_translate):
        # 3 cues joined by delimiter translated in one shot
        mock_translate.return_value = "Chào\n===@@@===\nThế giới\n===@@@===\nCapCap"
        cues = ["Hello", "World", "CapCap"]
        results = self.provider.translate_batch(cues, src_lang="en", target_lang="vi")
        self.assertEqual(results, ["Chào", "Thế giới", "CapCap"])
        mock_translate.assert_called_once()

    @patch.object(GoogleWebTranslatorProvider, "_translate_text")
    def test_delimiter_mismatch_falls_back_to_single_cues(self, mock_translate):
        # Batch join returns fewer items than cues, triggering fallback
        mock_translate.side_effect = [
            "Chào một mình",  # batch result (1 item != 2 cues)
            "Chào",           # single cue 1
            "Thế giới",       # single cue 2
        ]
        cues = ["Hello", "World"]
        results = self.provider.translate_batch(cues, src_lang="en", target_lang="vi")
        self.assertEqual(results, ["Chào", "Thế giới"])
        self.assertEqual(mock_translate.call_count, 3)


class TestBingWebTranslator(unittest.TestCase):
    def setUp(self):
        self.provider = BingWebTranslatorProvider()

    def test_empty_list_returns_empty(self):
        self.assertEqual(self.provider.translate_batch([], src_lang="en", target_lang="vi"), [])

    @patch.object(BingWebTranslatorProvider, "_fetch_session_params")
    @patch.object(BingWebTranslatorProvider, "_translate_single")
    def test_delimiter_batch_success(self, mock_translate, mock_fetch):
        mock_translate.return_value = "Xin chào\n===@@@===\nCác bạn"
        cues = ["Hello", "Friends"]
        results = self.provider.translate_batch(cues, src_lang="en", target_lang="vi")
        self.assertEqual(results, ["Xin chào", "Các bạn"])

    @patch.object(BingWebTranslatorProvider, "_fetch_session_params")
    @patch.object(BingWebTranslatorProvider, "_translate_single")
    def test_delimiter_mismatch_fallback(self, mock_translate, mock_fetch):
        mock_translate.side_effect = [
            "Mất delimiter",
            "Xin chào",
            "Các bạn",
        ]
        cues = ["Hello", "Friends"]
        results = self.provider.translate_batch(cues, src_lang="en", target_lang="vi")
        self.assertEqual(results, ["Xin chào", "Các bạn"])


class TestOrchestratorWebFallback(unittest.TestCase):
    def setUp(self):
        self.orchestrator = TranslationOrchestrator()
        self.segments = [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "Hello world"},
            {"id": 2, "start": 2.0, "end": 4.0, "text": "Goodbye"},
        ]

    @patch.dict("os.environ", {"OPENAI_PROVIDER": "google"})
    def test_google_success(self):
        self.orchestrator.google_web.translate_batch = MagicMock(return_value=["Xin chào", "Tạm biệt"])
        result = self.orchestrator.translate_segments(
            segments=self.segments,
            src_lang="en",
            target_lang="vi",
            enable_polish=False,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.primary_provider, "google-web")
        self.assertEqual(result.segments[0]["text"], "Xin chào")
        self.assertEqual(result.segments[1]["text"], "Tạm biệt")

    @patch.dict("os.environ", {"OPENAI_PROVIDER": "google"})
    def test_google_fails_auto_fallback_to_bing(self):
        self.orchestrator.google_web.translate_batch = MagicMock(side_effect=Exception("HTTP 429 Too Many Requests"))
        self.orchestrator.bing_web.translate_batch = MagicMock(return_value=["Xin chào Bing", "Tạm biệt Bing"])

        result = self.orchestrator.translate_segments(
            segments=self.segments,
            src_lang="en",
            target_lang="vi",
            enable_polish=False,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.primary_provider, "bing-web")
        self.assertTrue(result.used_fallback)
        self.assertTrue(any("Bing Translator" in w for w in result.warnings))
        self.assertEqual(result.segments[0]["text"], "Xin chào Bing")
        self.assertEqual(result.segments[1]["text"], "Tạm biệt Bing")

    @patch.dict("os.environ", {"OPENAI_PROVIDER": "bing"})
    def test_bing_explicitly_selected(self):
        self.orchestrator.bing_web.translate_batch = MagicMock(return_value=["Bing 1", "Bing 2"])
        result = self.orchestrator.translate_segments(
            segments=self.segments,
            src_lang="en",
            target_lang="vi",
            enable_polish=False,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.primary_provider, "bing-web")
        self.assertEqual(result.segments[0]["text"], "Bing 1")
        self.assertEqual(result.segments[1]["text"], "Bing 2")

    @patch.dict("os.environ", {"OPENAI_PROVIDER": "google"})
    def test_status_callback_on_google_to_bing_fallback(self):
        self.orchestrator.google_web.translate_batch = MagicMock(side_effect=Exception("HTTP 429 Too Many Requests"))
        self.orchestrator.bing_web.translate_batch = MagicMock(return_value=["Xin chào Bing", "Tạm biệt Bing"])

        status_updates = []
        def status_cb(provider, msg):
            status_updates.append((provider, msg))

        result = self.orchestrator.translate_segments(
            segments=self.segments,
            src_lang="en",
            target_lang="vi",
            enable_polish=False,
            status_callback=status_cb,
        )
        self.assertTrue(result.success)
        self.assertTrue(any(p == "Bing Translator" for p, _ in status_updates))

    def test_google_client_rotation_on_429(self):
        provider = GoogleWebTranslatorProvider()
        # Mock session.get to return 429 on first candidate, 200 on second
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.text = "Sorry 429"

        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = [[["Xin chào", "Hello", None, None]]]

        with patch.object(provider.session, "get", side_effect=[mock_resp_429, mock_resp_200]):
            res = provider._translate_text(text="Hello", src_lang="en", target_lang="vi", timeout=5, max_retries=1)
            self.assertEqual(res, "Xin chào")
            # Should have rotated index
            self.assertNotEqual(provider.current_client_idx, 0)


if __name__ == "__main__":
    unittest.main()
