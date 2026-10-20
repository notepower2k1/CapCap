import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from asr_options import ASR_FILTER_VERSION, DEFAULT_ASR_OPTIONS, normalize_asr_options


class TestNormalizeAsrOptions(unittest.TestCase):
    def test_defaults_enable_both_filters(self):
        self.assertEqual(
            normalize_asr_options(),
            {
                "dedup_enabled": True,
                "dedup_similarity": 0.82,
                "hallucination_filter_enabled": True,
                "hallucination_silence_threshold": 3.0,
            },
        )
        self.assertEqual(ASR_FILTER_VERSION, 1)

    def test_environment_is_used_only_when_no_snapshot_is_supplied(self):
        env = {
            "CAPCAP_ASR_DEDUP_ENABLED": "off",
            "CAPCAP_ASR_DEDUP_SIMILARITY": "0.91",
            "CAPCAP_WHISPER_HALLUCINATION_FILTER": "false",
            "CAPCAP_WHISPER_HALLUCINATION_SILENCE_SECONDS": "4.5",
        }
        with patch.dict("os.environ", env, clear=False):
            self.assertEqual(
                normalize_asr_options(),
                {
                    "dedup_enabled": False,
                    "dedup_similarity": 0.91,
                    "hallucination_filter_enabled": False,
                    "hallucination_silence_threshold": 4.5,
                },
            )
            self.assertEqual(
                normalize_asr_options({"dedup_enabled": True}),
                {**DEFAULT_ASR_OPTIONS, "dedup_enabled": True},
            )

    def test_rejects_invalid_values(self):
        invalid_options = (
            {"dedup_enabled": "maybe"},
            {"dedup_similarity": 0.49},
            {"dedup_similarity": math.inf},
            {"hallucination_silence_threshold": 10.1},
            {"hallucination_filter_enabled": 1},
        )
        for options in invalid_options:
            with self.subTest(options=options), self.assertRaises(ValueError):
                normalize_asr_options(options)


if __name__ == "__main__":
    unittest.main()
