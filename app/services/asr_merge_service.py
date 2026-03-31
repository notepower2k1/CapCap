from __future__ import annotations

import hashlib
import json
import os
import re
from difflib import SequenceMatcher

from core.models import AudioChunk


class AsrMergeService:
    def _cache_key(self, *, chunk: AudioChunk, model_path: str, language: str, transcription_config: dict) -> str:
        payload = {
            "audio_path": os.path.abspath(chunk.audio_path),
            "chunk_start": round(float(chunk.start_seconds), 3),
            "chunk_end": round(float(chunk.end_seconds), 3),
            "speech_start": round(float(chunk.speech_start_seconds), 3),
            "speech_end": round(float(chunk.speech_end_seconds), 3),
            "model_path": str(model_path or ""),
            "language": str(language or "auto"),
            "config": dict(transcription_config or {}),
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _cache_path(self, cache_dir: str, cache_key: str) -> str:
        return os.path.join(cache_dir, f"{cache_key}.json")

    def _load_cached_segments(self, cache_dir: str, cache_key: str):
        cache_path = self._cache_path(cache_dir, cache_key)
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return list(payload.get("segments", []) or [])
        except Exception:
            return None

    def _save_cached_segments(self, cache_dir: str, cache_key: str, segments: list[dict]) -> None:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = self._cache_path(cache_dir, cache_key)
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump({"segments": list(segments or [])}, handle, ensure_ascii=False, indent=2)

    def _normalize_text(self, text: str) -> str:
        value = str(text or "").strip().lower()
        value = re.sub(r"[^\w\s]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _similarity(self, left: str, right: str) -> float:
        left_normalized = self._normalize_text(left)
        right_normalized = self._normalize_text(right)
        if not left_normalized or not right_normalized:
            return 0.0
        return SequenceMatcher(None, left_normalized, right_normalized).ratio()

    def _segment_midpoint(self, segment: dict) -> float:
        return (float(segment.get("start", 0.0)) + float(segment.get("end", 0.0))) / 2.0

    def _midpoint_in_core(self, segment: dict, chunk: AudioChunk) -> bool:
        midpoint = self._segment_midpoint(segment)
        return float(chunk.core_start_seconds) <= midpoint <= float(chunk.core_end_seconds)

    def _time_overlap(self, left: dict, right: dict) -> float:
        start = max(float(left.get("start", 0.0)), float(right.get("start", 0.0)))
        end = min(float(left.get("end", 0.0)), float(right.get("end", 0.0)))
        return max(0.0, end - start)

    def transcribe_chunks(
        self,
        chunks: list[AudioChunk],
        *,
        whisper_adapter,
        model_path: str,
        language: str,
        cache_dir: str = "",
        transcription_config: dict | None = None,
    ) -> list[dict]:
        results = []
        reusable_model = None
        config_payload = dict(transcription_config or {})
        try:
            if hasattr(whisper_adapter, "load_model"):
                reusable_model = whisper_adapter.load_model(model_path)
        except Exception:
            reusable_model = None

        for chunk in chunks:
            cache_key = self._cache_key(
                chunk=chunk,
                model_path=model_path,
                language=language,
                transcription_config=config_payload,
            )
            cached_segments = self._load_cached_segments(cache_dir, cache_key) if cache_dir else None
            from_cache = cached_segments is not None
            segments = cached_segments
            if segments is None:
                if reusable_model is not None and hasattr(whisper_adapter, "transcribe_with_model"):
                    segments = whisper_adapter.transcribe_with_model(
                        reusable_model,
                        chunk.audio_path,
                        language=language,
                    )
                else:
                    segments = whisper_adapter.transcribe(
                        chunk.audio_path,
                        model_path,
                        language=language,
                    )
                if cache_dir:
                    self._save_cached_segments(cache_dir, cache_key, segments)
            results.append(
                {
                    "chunk": chunk,
                    "segments": list(segments or []),
                    "cache_key": cache_key,
                    "from_cache": from_cache,
                }
            )
        return results

    def merge_chunk_results(self, chunk_results: list[dict]) -> list[dict]:
        merged_segments: list[dict] = []
        for chunk_result in chunk_results:
            chunk: AudioChunk = chunk_result["chunk"]
            for raw_segment in chunk_result.get("segments", []) or []:
                global_segment = {
                    "start": float(chunk.start_seconds) + float(raw_segment.get("start", 0.0)),
                    "end": float(chunk.start_seconds) + float(raw_segment.get("end", 0.0)),
                    "text": str(raw_segment.get("text", "") or "").strip(),
                    "words": [],
                    "chunk_id": chunk.chunk_id,
                }
                words = []
                for word in raw_segment.get("words", []) or []:
                    try:
                        words.append(
                            {
                                "start": float(chunk.start_seconds) + float(word.get("start", 0.0)),
                                "end": float(chunk.start_seconds) + float(word.get("end", 0.0)),
                                "text": str(word.get("text", "") or "").strip(),
                            }
                        )
                    except (TypeError, ValueError, AttributeError):
                        continue
                if words:
                    global_segment["words"] = words
                candidate = {
                    "segment": global_segment,
                    "chunk": chunk,
                }
                self._append_with_dedup(merged_segments, candidate)
        return [entry["segment"] for entry in merged_segments]

    def _append_with_dedup(self, merged_entries: list[dict], candidate: dict) -> None:
        if not candidate["segment"]["text"]:
            return
        if not merged_entries:
            merged_entries.append(candidate)
            return

        previous = merged_entries[-1]
        previous_segment = previous["segment"]
        candidate_segment = candidate["segment"]
        overlap = self._time_overlap(previous_segment, candidate_segment)
        similarity = self._similarity(previous_segment.get("text", ""), candidate_segment.get("text", ""))
        if overlap <= 0.0 and similarity < 0.7:
            merged_entries.append(candidate)
            return

        previous_in_core = self._midpoint_in_core(previous_segment, previous["chunk"])
        candidate_in_core = self._midpoint_in_core(candidate_segment, candidate["chunk"])
        previous_duration = max(0.0, float(previous_segment["end"]) - float(previous_segment["start"]))
        candidate_duration = max(0.0, float(candidate_segment["end"]) - float(candidate_segment["start"]))

        if previous_in_core != candidate_in_core:
            if candidate_in_core:
                merged_entries[-1] = candidate
            return

        if similarity >= 0.7:
            if candidate_duration > previous_duration:
                merged_entries[-1] = candidate
            return

        merged_entries.append(candidate)
