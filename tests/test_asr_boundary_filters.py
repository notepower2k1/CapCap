import copy
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import whisper_processor
from core.models import AudioChunk
from services.asr_merge_service import AsrMergeService


def chunk(chunk_id, start, end, *, left=0.0, right=0.0):
    return AudioChunk(
        chunk_id,
        f"{chunk_id}.wav",
        start,
        end,
        overlap_left_seconds=left,
        overlap_right_seconds=right,
    )


def chunk_result(audio_chunk, text, start=0.0, end=2.0, words=None):
    segment = {"start": start, "end": end, "text": text}
    if words is not None:
        segment["words"] = words
    return {"chunk": audio_chunk, "segments": [segment]}


class TestBoundaryDedupFilters(unittest.TestCase):
    def setUp(self):
        self.service = AsrMergeService()
        self.left = chunk("left", 0.0, 10.0, right=1.0)
        self.right = chunk("right", 5.0, 15.0, left=1.0)

    def test_disabled_dedup_preserves_overlapping_text(self):
        merged = self.service.merge_chunk_results(
            [
                chunk_result(self.left, "hello there", start=4.5, end=6.5),
                chunk_result(self.right, "hello there", start=0.0, end=2.0),
            ],
            asr_options={"dedup_enabled": False},
        )
        self.assertEqual([segment["text"] for segment in merged], ["hello there", "hello there"])

    def test_kh_short_partial_prefers_full_cue_with_supplied_boundary_timing(self):
        first_chunk = chunk("kh-a", 0.0, 12.5, right=0.5)
        second_chunk = chunk("kh-b", 11.5, 24.5, left=0.5)
        short = chunk_result(first_chunk, "ta vốn thể chất", start=11.2, end=12.5)
        full = chunk_result(second_chunk, "ta vốn thể chất tiên sinh đạo thể", start=0.0, end=2.8)
        raw_before = copy.deepcopy([short, full])

        merged = self.service.merge_chunk_results([short, full])
        reverse_merged = self.service.merge_chunk_results([full, short])

        self.assertEqual([segment["text"] for segment in merged], [full["segments"][0]["text"]])
        self.assertEqual([segment["text"] for segment in reverse_merged], [full["segments"][0]["text"]])
        self.assertEqual([short, full], raw_before)

    def test_kh_partial_is_kept_at_exact_only_threshold(self):
        first_chunk = chunk("kh-a", 0.0, 12.5, right=0.5)
        second_chunk = chunk("kh-b", 11.5, 24.5, left=0.5)
        short = chunk_result(first_chunk, "ta vốn thể chất", start=11.2, end=12.5)
        full = chunk_result(second_chunk, "ta vốn thể chất tiên sinh đạo thể", start=0.0, end=2.8)
        merged = self.service.merge_chunk_results(
            [short, full],
            asr_options={"dedup_similarity": 1.0},
        )
        self.assertEqual(
            [segment["text"] for segment in merged],
            [short["segments"][0]["text"], full["segments"][0]["text"]],
        )

    def test_kh_partial_with_word_evidence_prefers_full_cue(self):
        first_chunk = chunk("kh-a", 0.0, 12.5, right=0.5)
        second_chunk = chunk("kh-b", 11.5, 24.5, left=0.5)
        short_words = [
            {"start": 11.2, "end": 11.4, "text": "ta"},
            {"start": 11.4, "end": 11.7, "text": "vốn"},
            {"start": 11.7, "end": 12.0, "text": "thể"},
            {"start": 12.0, "end": 12.5, "text": "chất"},
        ]
        full_words = [
            {"start": 0.0, "end": 0.2, "text": "ta"},
            {"start": 0.2, "end": 0.45, "text": "vốn"},
            {"start": 0.45, "end": 0.7, "text": "thể"},
            {"start": 0.7, "end": 1.0, "text": "chất"},
            {"start": 1.0, "end": 1.3, "text": "tiên"},
            {"start": 1.3, "end": 1.55, "text": "sinh"},
            {"start": 1.55, "end": 1.85, "text": "đạo"},
            {"start": 1.85, "end": 2.3, "text": "thể"},
        ]
        short = chunk_result(first_chunk, "ta vốn thể chất", start=11.2, end=12.5, words=short_words)
        full = chunk_result(
            second_chunk,
            "ta vốn thể chất tiên sinh đạo thể",
            start=0.0,
            end=2.8,
            words=full_words,
        )
        merged = self.service.merge_chunk_results([short, full])
        self.assertEqual([segment["text"] for segment in merged], [full["segments"][0]["text"]])

    def test_full_contained_cue_wins_when_boundary_timing_is_strong(self):
        partial = chunk_result(self.left, "the captain of the guard", start=4.5, end=6.5)
        complete = chunk_result(self.right, "the captain of the guard arrived", start=0.0, end=2.0)
        merged = self.service.merge_chunk_results([partial, complete])
        self.assertEqual([segment["text"] for segment in merged], [complete["segments"][0]["text"]])

    def test_short_confirmation_is_not_removed_by_prefix_matching(self):
        short = chunk_result(self.left, "yes", start=4.5, end=6.5)
        longer = chunk_result(self.right, "yes I will", start=0.0, end=2.0)
        merged = self.service.merge_chunk_results([short, longer])
        self.assertEqual([segment["text"] for segment in merged], ["yes", "yes I will"])

    def test_latin_substring_inside_a_word_is_not_containment(self):
        short = chunk_result(self.left, "internationalization", start=4.5, end=6.5)
        longer = chunk_result(self.right, "supercalifragilisticinternationalization", start=0.0, end=2.0)
        merged = self.service.merge_chunk_results([short, longer])
        self.assertEqual(len(merged), 2)

    def test_chinese_boundary_duplicate_is_removed(self):
        left = chunk_result(self.left, "今天我们在这里", start=4.5, end=6.5)
        right = chunk_result(self.right, "今天我们在这里", start=0.0, end=2.0)
        merged = self.service.merge_chunk_results([left, right])
        self.assertEqual([segment["text"] for segment in merged], ["今天我们在这里"])

    def test_same_chunk_and_disjoint_repeated_dialogue_are_preserved(self):
        repeated_chunk = chunk("single", 0.0, 10.0)
        same_chunk_results = [
            chunk_result(repeated_chunk, "confirmed", start=1.0, end=2.0),
            chunk_result(repeated_chunk, "confirmed", start=1.2, end=2.2),
        ]
        same_chunk_merged = self.service.merge_chunk_results(same_chunk_results)
        self.assertEqual(len(same_chunk_merged), 2)

        disjoint = [
            chunk_result(self.left, "confirmed", start=0.5, end=1.5),
            chunk_result(self.right, "confirmed", start=5.0, end=6.0),
        ]
        disjoint_merged = self.service.merge_chunk_results(disjoint)
        self.assertEqual(len(disjoint_merged), 2)

    def test_out_of_range_segment_overlap_cannot_deduplicate_disjoint_chunks(self):
        first_chunk = chunk("adjacent-a", 0.0, 5.0)
        second_chunk = chunk("adjacent-b", 5.0, 10.0)
        results = [
            chunk_result(first_chunk, "same words", start=4.0, end=6.0),
            chunk_result(second_chunk, "same words", start=0.0, end=2.0),
        ]
        merged = self.service.merge_chunk_results(results)
        self.assertEqual(len(merged), 2)

    def test_word_timing_disagreement_prevents_false_boundary_dedup(self):
        left = chunk_result(
            self.left,
            "we can meet tomorrow",
            start=4.5,
            end=6.5,
            words=[{"start": 4.5, "end": 5.0, "text": "we"}],
        )
        right = chunk_result(
            self.right,
            "we can meet tomorrow",
            start=0.0,
            end=2.0,
            words=[{"start": 1.5, "end": 2.0, "text": "tomorrow"}],
        )
        merged = self.service.merge_chunk_results([left, right])
        self.assertEqual(len(merged), 2)

    def test_contained_text_requires_strong_word_timing_when_available(self):
        left = chunk_result(
            self.left,
            "we should leave now",
            start=4.5,
            end=6.5,
            words=[{"start": 4.5, "end": 5.0, "text": "we"}],
        )
        right = chunk_result(
            self.right,
            "we should leave",
            start=0.0,
            end=2.0,
            words=[{"start": 1.5, "end": 2.0, "text": "leave"}],
        )
        merged = self.service.merge_chunk_results([left, right])
        self.assertEqual(len(merged), 2)

    def test_similarity_threshold_is_configurable(self):
        left = chunk_result(self.left, "we should leave now", start=4.5, end=6.5)
        right = chunk_result(self.right, "we should leave soon", start=0.0, end=2.0)
        strict = self.service.merge_chunk_results(
            [left, right], asr_options={"dedup_similarity": 0.9},
        )
        permissive = self.service.merge_chunk_results(
            [left, right], asr_options={"dedup_similarity": 0.85},
        )
        self.assertEqual(len(strict), 2)
        self.assertEqual(len(permissive), 1)

    def test_recognition_cache_ignores_merge_only_settings(self):
        base = self.service._cache_key(
            chunk=self.left,
            model_path="small",
            language="en",
            transcription_config={},
            asr_options={"dedup_enabled": True, "dedup_similarity": 0.82},
        )
        changed_merge = self.service._cache_key(
            chunk=self.left,
            model_path="small",
            language="en",
            transcription_config={"dedup_similarity": 0.99},
            asr_options={"dedup_enabled": False, "dedup_similarity": 0.99},
        )
        changed_recognition = self.service._cache_key(
            chunk=self.left,
            model_path="small",
            language="en",
            transcription_config={},
            asr_options={"hallucination_silence_threshold": 4.0},
        )
        self.assertEqual(base, changed_merge)
        self.assertNotEqual(base, changed_recognition)


class _FakeModel:
    def __init__(self, runtime_device="cpu"):
        self._capcap_runtime_device = runtime_device
        self.calls = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append((audio_path, kwargs))
        return iter([SimpleNamespace(start=0.0, end=1.0, text="hello", words=[])]), SimpleNamespace(duration=1.0)


class TestWhisperHallucinationFilter(unittest.TestCase):
    def test_enabled_filter_passes_threshold_and_uses_standard_gpu(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
            model = _FakeModel("cuda")
            with patch.object(whisper_processor, "_whisper_gpu_batch_size", return_value=8), \
                 patch.object(whisper_processor, "_get_batched_whisper_pipeline") as get_pipeline, \
                 redirect_stdout(io.StringIO()) as output:
                whisper_processor.transcribe_audio_with_model(
                    model,
                    audio.name,
                    asr_options={
                        "hallucination_filter_enabled": True,
                        "hallucination_silence_threshold": 4.25,
                    },
                )
            get_pipeline.assert_not_called()
            self.assertEqual(model.calls[0][1]["hallucination_silence_threshold"], 4.25)
            self.assertTrue(model.calls[0][1]["word_timestamps"])
            self.assertIn("standard GPU inference", output.getvalue())

    def test_disabled_filter_keeps_batched_gpu_path_and_omits_threshold(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
            model = _FakeModel("cuda")
            pipeline = _FakeModel("cuda")
            with patch.object(whisper_processor, "_whisper_gpu_batch_size", return_value=8), \
                 patch.object(whisper_processor, "_get_batched_whisper_pipeline", return_value=pipeline):
                whisper_processor.transcribe_audio_with_model(
                    model,
                    audio.name,
                    asr_options={"hallucination_filter_enabled": False},
                )
            self.assertEqual(len(pipeline.calls), 1)
            self.assertEqual(pipeline.calls[0][1]["batch_size"], 8)
            self.assertNotIn("hallucination_silence_threshold", pipeline.calls[0][1])


if __name__ == "__main__":
    unittest.main()
