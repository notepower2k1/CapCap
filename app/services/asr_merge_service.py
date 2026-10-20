from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
from difflib import SequenceMatcher

from core.models import AudioChunk
try:
    from asr_options import normalize_asr_options
except ModuleNotFoundError:
    from app.asr_options import normalize_asr_options


_ASR_WORKER_MODEL = None
_ASR_WORKER_OPTIONS = None


def _init_asr_worker(model_path: str, asr_options: dict | None = None) -> None:
    global _ASR_WORKER_MODEL, _ASR_WORKER_OPTIONS
    from whisper_processor import load_whisper_model
    _ASR_WORKER_MODEL = load_whisper_model(model_path)
    _ASR_WORKER_OPTIONS = normalize_asr_options(asr_options)


def _transcribe_chunk_job(audio_path: str, language: str) -> list[dict]:
    global _ASR_WORKER_MODEL
    if _ASR_WORKER_MODEL is None:
        raise RuntimeError("ASR worker model is not initialized.")
    from whisper_processor import transcribe_audio_with_model
    return transcribe_audio_with_model(
        _ASR_WORKER_MODEL,
        audio_path,
        language=language,
        asr_options=_ASR_WORKER_OPTIONS,
    )


def _format_time_hms(seconds: float) -> str:
    secs = int(max(0, seconds))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


class AsrMergeService:
    DEFAULT_MAX_WORKERS = 3

    def _cache_key(
        self,
        *,
        chunk: AudioChunk,
        model_path: str,
        language: str,
        transcription_config: dict,
        asr_options: dict | None = None,
    ) -> str:
        options = normalize_asr_options(asr_options)
        return self._cache_key_from_options(
            chunk=chunk,
            model_path=model_path,
            language=language,
            transcription_config=transcription_config,
            options=options,
        )

    def _cache_key_from_options(
        self,
        *,
        chunk: AudioChunk,
        model_path: str,
        language: str,
        transcription_config: dict,
        options: dict,
    ) -> str:
        # Dedup controls only affect how raw chunk results are merged. Keep
        # those settings out of recognition keys so cached audio can be
        # remerged without rerunning Whisper.
        non_asr_config = {
            key: value
            for key, value in dict(transcription_config or {}).items()
            if key not in {
                "dedup_enabled",
                "dedup_similarity",
                "hallucination_filter_enabled",
                "hallucination_silence_threshold",
            }
        }
        payload = {
            "audio_path": os.path.abspath(chunk.audio_path),
            "chunk_start": round(float(chunk.start_seconds), 3),
            "chunk_end": round(float(chunk.end_seconds), 3),
            "speech_start": round(float(chunk.speech_start_seconds), 3),
            "speech_end": round(float(chunk.speech_end_seconds), 3),
            "model_path": str(model_path or ""),
            "language": str(language or "auto"),
            "config": non_asr_config,
            "hallucination_filter_enabled": options["hallucination_filter_enabled"],
            "hallucination_silence_threshold": options["hallucination_silence_threshold"],
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

    def _chunk_time_overlap(self, left: AudioChunk, right: AudioChunk) -> float:
        start = max(float(left.start_seconds), float(right.start_seconds))
        end = min(float(left.end_seconds), float(right.end_seconds))
        return max(0.0, end - start)

    def _recommended_worker_count(self, pending_count: int) -> int:
        if pending_count <= 1:
            return 1
        cpu_count = max(1, os.cpu_count() or 1)
        return max(1, min(self.DEFAULT_MAX_WORKERS, pending_count, cpu_count))

    def _transcribe_chunks_parallel(
        self,
        pending_items: list[dict],
        *,
        cache_dir: str,
        model_path: str,
        language: str,
        asr_options: dict,
        on_item_done=None,
    ) -> None:
        worker_count = self._recommended_worker_count(len(pending_items))
        if worker_count <= 1:
            raise RuntimeError("Parallel ASR requested with only one pending chunk.")

        print(f"[ASR] Using process worker pool on CPU: workers={worker_count}, pending={len(pending_items)}")
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=worker_count,
            initializer=_init_asr_worker,
            initargs=(model_path, asr_options),
        ) as executor:
            future_map = {
                executor.submit(_transcribe_chunk_job, item["chunk"].audio_path, language): item
                for item in pending_items
            }
            for future in concurrent.futures.as_completed(future_map):
                item = future_map[future]
                segments = future.result()
                item["segments"] = list(segments or [])
                if cache_dir:
                    self._save_cached_segments(cache_dir, item["cache_key"], item["segments"])
                if on_item_done is not None:
                    on_item_done(item)

    def _transcribe_chunks_sequential(
        self,
        pending_items: list[dict],
        *,
        whisper_adapter,
        model_path: str,
        language: str,
        asr_options: dict,
        cache_dir: str,
        on_item_done=None,
    ) -> None:
        reusable_model = None
        try:
            if hasattr(whisper_adapter, "load_model"):
                reusable_model = whisper_adapter.load_model(model_path)
        except Exception:
            reusable_model = None

        print("[ASR] Pre-chunked audio uses standard Whisper inference (GPU batching disabled for accuracy).")
        for item in pending_items:
            chunk: AudioChunk = item["chunk"]
            if reusable_model is not None and hasattr(whisper_adapter, "transcribe_with_model"):
                segments = whisper_adapter.transcribe_with_model(
                    reusable_model,
                    chunk.audio_path,
                    language=language,
                    use_batched=False,
                    # vad_filter=True is required for within-chunk sentence segmentation.
                    # Disabling it causes Whisper to merge all speech into one long segment.
                    vad_filter=True,
                    asr_options=asr_options,
                )
            else:
                segments = whisper_adapter.transcribe(
                    chunk.audio_path,
                    model_path,
                    language=language,
                    asr_options=asr_options,
                )
            item["segments"] = list(segments or [])
            if cache_dir:
                self._save_cached_segments(cache_dir, item["cache_key"], item["segments"])
            if on_item_done is not None:
                on_item_done(item)

    def transcribe_chunks(
        self,
        chunks: list[AudioChunk],
        *,
        whisper_adapter,
        model_path: str,
        language: str,
        cache_dir: str = "",
        transcription_config: dict | None = None,
        asr_options: dict | None = None,
        ordered_callback=None,
        on_progress=None,
    ) -> list[dict]:
        results = []
        config_payload = dict(transcription_config or {})
        options_snapshot = normalize_asr_options(asr_options)
        pending_items: list[dict] = []
        for index, chunk in enumerate(chunks):
            cache_key = self._cache_key_from_options(
                chunk=chunk,
                model_path=model_path,
                language=language,
                transcription_config=config_payload,
                options=options_snapshot,
            )
            cached_segments = self._load_cached_segments(cache_dir, cache_key) if cache_dir else None
            from_cache = cached_segments is not None
            result = {
                "chunk": chunk,
                "segments": list(cached_segments or []),
                "cache_key": cache_key,
                "from_cache": from_cache,
                "_index": index,
                "_ready": from_cache,
            }
            results.append(result)
            if not from_cache:
                pending_items.append(result)

        total_chunks = len(chunks)
        if total_chunks == 0:
            return []

        total_duration = max((float(c.end_seconds) for c in chunks), default=0.0)
        completed_count = sum(1 for item in results if item.get("_ready"))

        def _notify_progress(completed: int, cur_chunk: AudioChunk | None = None) -> None:
            pct = min(100, max(1, int((completed / total_chunks) * 100))) if total_chunks > 0 else 0
            cur_time = float(cur_chunk.end_seconds) if cur_chunk else (
                (completed / total_chunks) * total_duration if total_chunks > 0 else 0.0
            )
            cur_hms = _format_time_hms(cur_time)
            tot_hms = _format_time_hms(total_duration) if total_duration > 0 else "--:--"
            log_line = f"[ASR Progress] {completed}/{total_chunks} chunks ({pct}%) - {cur_hms} / {tot_hms}"
            print(log_line, flush=True)
            if on_progress is not None:
                try:
                    on_progress(pct, f"Transcribing audio: {completed}/{total_chunks} chunks ({pct}%)", log_line)
                except TypeError:
                    try:
                        on_progress(pct, f"Transcribing audio: {completed}/{total_chunks} chunks ({pct}%)")
                    except Exception:
                        pass
                except Exception:
                    pass

        if completed_count > 0:
            pct = int((completed_count / total_chunks) * 100)
            print(f"[ASR Progress] Reused {completed_count}/{total_chunks} chunks from cache ({pct}%)", flush=True)
            if on_progress is not None:
                try:
                    on_progress(pct, f"Transcribing audio: {completed_count}/{total_chunks} chunks ({pct}%)")
                except Exception:
                    pass

        next_emit_index = 0

        def _drain_ready() -> None:
            nonlocal next_emit_index
            if ordered_callback is None:
                return
            while next_emit_index < len(results) and results[next_emit_index].get("_ready"):
                ordered_callback(results[next_emit_index])
                next_emit_index += 1

        _drain_ready()

        if pending_items:
            used_parallel = False
            def _on_item_done(item: dict) -> None:
                nonlocal completed_count
                item["_ready"] = True
                completed_count += 1
                _notify_progress(completed_count, item.get("chunk"))
                _drain_ready()
            try:
                from whisper_processor import _detect_faster_whisper_runtime
                runtime = _detect_faster_whisper_runtime()
                if runtime.get("device") == "cuda" and len(pending_items) > 1:
                    self._transcribe_chunks_sequential(
                        pending_items,
                        whisper_adapter=whisper_adapter,
                        model_path=model_path,
                        language=language,
                        asr_options=options_snapshot,
                        cache_dir=cache_dir,
                        on_item_done=_on_item_done,
                    )
                    used_parallel = True
                elif len(pending_items) > 1:
                    self._transcribe_chunks_parallel(
                        pending_items,
                        cache_dir=cache_dir,
                        model_path=model_path,
                        language=language,
                        asr_options=options_snapshot,
                        on_item_done=_on_item_done,
                    )
                    used_parallel = True
            except Exception as exc:
                print(f"[ASR] Parallel mode failed, falling back to sequential: {exc}")
            if not used_parallel:
                self._transcribe_chunks_sequential(
                    pending_items,
                    whisper_adapter=whisper_adapter,
                    model_path=model_path,
                    language=language,
                    asr_options=options_snapshot,
                    cache_dir=cache_dir,
                    on_item_done=_on_item_done,
                )
        _drain_ready()
        for result in results:
            result.pop("_index", None)
            result.pop("_ready", None)
        print(f"[ASR Progress] 100% completed: all {total_chunks}/{total_chunks} chunks transcribed.", flush=True)
        if on_progress is not None:
            try:
                on_progress(100, f"Transcribing audio: 100% ({total_chunks}/{total_chunks} chunks)")
            except Exception:
                pass
        return results

    def merge_chunk_results(
        self,
        chunk_results: list[dict],
        *,
        asr_options: dict | None = None,
    ) -> list[dict]:
        options_snapshot = normalize_asr_options(asr_options)
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
                if options_snapshot["dedup_enabled"]:
                    self._append_with_dedup(merged_segments, candidate, asr_options=options_snapshot)
                elif global_segment["text"]:
                    merged_segments.append(candidate)
        return self.normalize_segment_timeline([entry["segment"] for entry in merged_segments])

    def _word_overlap_ratio(self, left: dict, right: dict) -> float | None:
        left_words = left.get("words") or []
        right_words = right.get("words") or []
        if not left_words or not right_words:
            return None
        shorter, longer = (left_words, right_words) if len(left_words) <= len(right_words) else (right_words, left_words)
        total_duration = 0.0
        covered_duration = 0.0
        for word in shorter:
            try:
                start = float(word.get("start", 0.0))
                end = float(word.get("end", 0.0))
            except (TypeError, ValueError, AttributeError):
                continue
            duration = max(0.0, end - start)
            if duration <= 0.0:
                continue
            total_duration += duration
            best_overlap = 0.0
            for other in longer:
                try:
                    other_start = float(other.get("start", 0.0))
                    other_end = float(other.get("end", 0.0))
                except (TypeError, ValueError, AttributeError):
                    continue
                best_overlap = max(best_overlap, max(0.0, min(end, other_end) - max(start, other_start)))
            covered_duration += min(duration, best_overlap)
        if total_duration <= 0.0:
            return None
        return covered_duration / total_duration

    def _is_informative_containment(self, left_text: str, right_text: str) -> bool:
        if not left_text or not right_text or left_text == right_text:
            return False
        short_text, long_text = sorted((left_text, right_text), key=len)
        short_words = short_text.split()
        long_words = long_text.split()
        informative_chars = sum(1 for char in short_text if char.isalnum())
        if informative_chars >= 10 and len(short_words) >= 3:
            width = len(short_words)
            if any(long_words[index:index + width] == short_words for index in range(len(long_words) - width + 1)):
                return True

        def _is_han(character: str) -> bool:
            codepoint = ord(character)
            return (
                0x3400 <= codepoint <= 0x4DBF
                or 0x4E00 <= codepoint <= 0x9FFF
                or 0x20000 <= codepoint <= 0x2FA1F
            )

        han_count = sum(1 for char in short_text if _is_han(char))
        return han_count >= 8 and short_text in long_text

    def _append_with_dedup(
        self,
        merged_entries: list[dict],
        candidate: dict,
        *,
        asr_options: dict | None = None,
    ) -> None:
        options = normalize_asr_options(asr_options)
        if not candidate["segment"]["text"]:
            return
        if not options["dedup_enabled"]:
            merged_entries.append(candidate)
            return
        if not merged_entries:
            merged_entries.append(candidate)
            return

        previous = merged_entries[-1]
        previous_segment = previous["segment"]
        candidate_segment = candidate["segment"]
        previous_chunk_id = previous_segment.get("chunk_id")
        candidate_chunk_id = candidate_segment.get("chunk_id")
        if previous_chunk_id is not None and candidate_chunk_id is not None:
            same_chunk = previous_chunk_id == candidate_chunk_id
        else:
            same_chunk = previous.get("chunk") is candidate.get("chunk")
        if same_chunk:
            merged_entries.append(candidate)
            return
        if self._chunk_time_overlap(previous["chunk"], candidate["chunk"]) <= 0.0:
            merged_entries.append(candidate)
            return

        overlap = self._time_overlap(previous_segment, candidate_segment)
        similarity = self._similarity(previous_segment.get("text", ""), candidate_segment.get("text", ""))
        # Repeated dialogue is valid.  Only the overlapping boundary audio
        # of adjacent chunks can represent the same utterance; using text
        # similarity alone discarded real consecutive phrases such as
        # "confirmed?" / "confirmed" in a single chunk.
        if overlap <= 0.0:
            merged_entries.append(candidate)
            return

        previous_in_core = self._midpoint_in_core(previous_segment, previous["chunk"])
        candidate_in_core = self._midpoint_in_core(candidate_segment, candidate["chunk"])
        previous_duration = max(0.0, float(previous_segment["end"]) - float(previous_segment["start"]))
        candidate_duration = max(0.0, float(candidate_segment["end"]) - float(candidate_segment["start"]))

        # A time overlap alone is not enough: speakers can overlap and an
        # ASR engine can legitimately emit adjacent partial phrases with
        # intersecting timestamps. Only collapse a chunk-boundary duplicate
        # when most of the shorter cue overlaps and its text is essentially
        # identical after normalization.
        shorter_duration = min(previous_duration, candidate_duration)
        substantial_overlap = overlap >= max(0.10, shorter_duration * 0.60)
        similarity_threshold = options["dedup_similarity"]
        previous_text = self._normalize_text(previous_segment.get("text", ""))
        candidate_text = self._normalize_text(candidate_segment.get("text", ""))
        is_containment = (
            previous_text != candidate_text
            and bool(previous_text)
            and bool(candidate_text)
            and (previous_text in candidate_text or candidate_text in previous_text)
        )
        word_overlap_ratio = self._word_overlap_ratio(previous_segment, candidate_segment)
        if word_overlap_ratio is None:
            containment_timing_overlap = overlap >= max(0.30, shorter_duration * 0.75)
        else:
            containment_timing_overlap = (
                overlap >= max(0.30, shorter_duration * 0.60)
                and word_overlap_ratio >= 0.50
            )
        informative_containment = (
            is_containment
            and self._is_informative_containment(previous_text, candidate_text)
            and containment_timing_overlap
            and similarity_threshold < 1.0
        )
        if is_containment:
            # Prefix/contained cues need their stronger word-boundary and
            # timing evidence even when their text ratio is high.
            is_boundary_duplicate = informative_containment
        else:
            is_boundary_duplicate = substantial_overlap and similarity >= similarity_threshold
        if is_boundary_duplicate and word_overlap_ratio is not None:
            required_word_overlap = 0.50 if informative_containment else 0.30
            if word_overlap_ratio < required_word_overlap:
                is_boundary_duplicate = False
        if not is_boundary_duplicate:
            merged_entries.append(candidate)
            return

        previous_log_text = str(previous_segment.get("text", "")[:30]).encode("ascii", "backslashreplace").decode("ascii")
        candidate_log_text = str(candidate_segment.get("text", "")[:30]).encode("ascii", "backslashreplace").decode("ascii")
        print(f"[ASR] Dedup boundary: '{previous_log_text}' ~ '{candidate_log_text}' (sim={similarity:.2f}, overlap={overlap:.2f}s)")

        if is_containment:
            # Keep the fuller cue when one side is a clipped prefix/suffix.
            if len(candidate_text) > len(previous_text):
                merged_entries[-1] = candidate
            return

        if previous_in_core != candidate_in_core:
            if candidate_in_core:
                merged_entries[-1] = candidate
            return

        if candidate_duration > previous_duration:
            merged_entries[-1] = candidate

    def normalize_segment_timeline(self, segments: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        previous_end = 0.0
        for segment in segments or []:
            current = dict(segment)
            start = float(current.get("start", 0.0) or 0.0)
            end = float(current.get("end", 0.0) or 0.0)
            if end <= start:
                end = start + 0.12
            if start < previous_end:
                shift = previous_end - start
                start = previous_end
                if end <= start:
                    end = start + max(0.12, 0.12 - shift)
            current["start"] = round(start, 3)
            current["end"] = round(max(start + 0.12, end), 3)
            if current.get("words"):
                adjusted_words = []
                for word in current.get("words") or []:
                    try:
                        word_start = max(float(word.get("start", start) or start), start)
                        word_end = max(word_start, float(word.get("end", word_start) or word_start))
                        word_end = min(word_end, current["end"])
                        adjusted_words.append({
                            "start": round(word_start, 3),
                            "end": round(word_end, 3),
                            "text": str(word.get("text", "") or "").strip(),
                        })
                    except (TypeError, ValueError, AttributeError):
                        continue
                current["words"] = adjusted_words
            normalized.append(current)
            previous_end = current["end"]
        return normalized
