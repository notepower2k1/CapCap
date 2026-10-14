import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_gemini_300 import generate_mock_subtitles, probe_gemini_300


class TestGemini300Probe(unittest.TestCase):
    def test_generates_300_distinct_timed_cues(self):
        cues = generate_mock_subtitles()
        self.assertEqual(len(cues), 300)
        self.assertEqual(len({cue["text"] for cue in cues}), 300)
        self.assertTrue(all(cue["end"] > cue["start"] for cue in cues))

    def test_success_and_truncated_response_report_cue_counts_and_usage(self):
        for count in (300, 299):
            with self.subTest(count=count), tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder:
                response = SimpleNamespace(
                    model="test-model",
                    choices=[SimpleNamespace(message=SimpleNamespace(content="\n".join(
                        f"{index}. Câu dịch {index}" for index in range(1, count + 1))),
                        finish_reason="stop" if count == 300 else "length")],
                    usage=SimpleNamespace(prompt_tokens=5000, completion_tokens=8000, total_tokens=13000,
                                          completion_tokens_details=SimpleNamespace(reasoning_tokens=3000)),
                )
                with patch("scripts.probe_gemini_300.OpenAI") as constructor:
                    client = constructor.return_value
                    client.__enter__.return_value = client
                    client.chat.completions.create.return_value = response
                    report = probe_gemini_300("test-secret", output_dir=Path(folder))
                self.assertEqual(report["status"], "ok" if count == 300 else "incomplete")
                self.assertEqual(report["output_cues"], count)
                self.assertEqual(report["missing_ids"], [] if count == 300 else [300])
                self.assertEqual(report["usage"]["reasoning_tokens"], 3000)
                self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)
                client.chat.completions.create.assert_called_once()
                kwargs = client.chat.completions.create.call_args.kwargs
                self.assertEqual(kwargs["reasoning_effort"], "medium")
                self.assertEqual(kwargs["max_tokens"], 65536)
                self.assertIn("300. ", kwargs["messages"][1]["content"])
                for path in Path(folder).iterdir():
                    self.assertNotIn("test-secret", path.read_text(encoding="utf-8"))

    def test_api_error_is_reported_and_key_is_redacted(self):
        error = RuntimeError("HTTP 429 for test-secret")
        error.status_code = 429
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder, \
             patch("scripts.probe_gemini_300.OpenAI") as constructor:
            client = constructor.return_value
            client.__enter__.return_value = client
            client.chat.completions.create.side_effect = error
            report = probe_gemini_300("test-secret", output_dir=Path(folder))
            saved = json.loads((Path(folder) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["http_status"], 429)
        self.assertEqual(saved["status"], "error")
        self.assertNotIn("test-secret", saved["error"])
        client.chat.completions.create.assert_called_once()

    def test_empty_key_does_not_send_a_request(self):
        with patch("scripts.probe_gemini_300.OpenAI") as constructor, self.assertRaises(ValueError):
            probe_gemini_300("")
        constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
