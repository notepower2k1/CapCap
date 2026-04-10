import concurrent.futures
import os
import re

from .errors import TranslationValidationError
from .models import TranslationResult
from .providers import (
    AIPolisherProvider,
    GeminiPolisherProvider,
    LocalPolisherProvider,
    MicrosoftTranslatorProvider,
)
from .srt_utils import clone_with_texts, parse_srt, split_text_batches, to_srt, validate_texts


class TranslationOrchestrator:
    def __init__(self):
        self.microsoft = MicrosoftTranslatorProvider()
        self.ai_polisher = AIPolisherProvider()
        self.gemini_polisher = GeminiPolisherProvider()
        self.local_polisher = LocalPolisherProvider()

    def translate_segments(
        self,
        *,
        segments: list[dict],
        src_lang: str = "zh-Hans",
        target_lang: str = "vi",
        enable_polish: bool = True,
        ms_batch_size: int = 50,
        polish_batch_size: int = 25,
        style_instruction: str = "",
    ) -> TranslationResult:
        if not segments:
            return TranslationResult(success=False, errors=["No segments to translate."], stage="input")

        source_texts = [s.get("text") or "" for s in segments]
        normalized_src = self._normalize_source_language(src_lang)
        warnings = []

        if enable_polish:
            provider_type, polisher = self._resolve_ai_provider()
            if polisher.is_configured():
                try:
                    mode_label = self._describe_ai_provider(provider_type)
                    print(
                        f"[AI Translation] Starting translation (provider: {mode_label}, batch_size={polish_batch_size})..."
                    )
                    translated_texts, providers_used, batch_warnings = self._run_ai_batches(
                        polisher=polisher,
                        provider_type=provider_type,
                        source_texts=source_texts,
                        translated_texts=None,
                        src_lang=normalized_src,
                        target_lang=target_lang,
                        style_instruction=style_instruction,
                        polish_batch_size=polish_batch_size,
                    )
                    warnings.extend(batch_warnings)

                    if not validate_texts(translated_texts, len(segments)):
                        raise TranslationValidationError("AI translator returned an invalid number of segments.")

                    print(f"[AI Translation] Success: completed via {', '.join(providers_used) or 'AI'}")
                    final_segments = clone_with_texts(segments, translated_texts, provider=provider_type, polished=True)
                    final_segments = self._maybe_optimize_subtitle_segments(
                        polisher=polisher,
                        provider_type=provider_type,
                        source_segments=segments,
                        translated_segments=final_segments,
                        src_lang=normalized_src,
                        target_lang=target_lang,
                        warnings=warnings,
                        style_instruction=style_instruction,
                    )
                    return TranslationResult(
                        success=True,
                        segments=final_segments,
                        warnings=warnings,
                        stage="ai_direct",
                        primary_provider=" -> ".join(providers_used) or provider_type,
                        used_fallback=bool(warnings),
                    )
                except Exception as exc:
                    msg = f"AI translation failed, falling back to Microsoft: {exc}"
                    print(f"[AI Translation] WARNING: {msg}")
                    warnings.append(msg)
            else:
                print(f"[AI Translation] AI Provider ({provider_type}) not configured, using Microsoft.")

        print(f"[Translation] Starting Microsoft Translator (batch_size={ms_batch_size})...")
        try:
            translated_texts = []
            for batch in split_text_batches(source_texts, ms_batch_size):
                translated_batch = self.microsoft.translate_batch(
                    batch,
                    src_lang=normalized_src,
                    target_lang=target_lang,
                )
                translated_texts.extend(translated_batch)

            if not validate_texts(translated_texts, len(segments)):
                raise TranslationValidationError("Microsoft Translator returned an invalid number of segments.")

            print("[Translation] Success: Microsoft translation completed.")
            final_segments = clone_with_texts(segments, translated_texts, provider="microsoft", polished=False)
            return TranslationResult(
                success=True,
                segments=final_segments,
                warnings=warnings,
                stage="translation",
                primary_provider="microsoft",
                used_fallback=bool(warnings),
            )
        except Exception as exc:
            return TranslationResult(success=False, errors=[str(exc)], warnings=warnings, stage="translation")

    def rewrite_segments(
        self,
        source_segments: list[dict],
        translated_segments: list[dict],
        *,
        src_lang: str = "zh-Hans",
        target_lang: str = "vi",
        style_instruction: str = "",
    ) -> TranslationResult:
        if not source_segments:
            return TranslationResult(success=False, errors=["No source segments to rewrite."], stage="rewrite")
        if not translated_segments:
            return TranslationResult(success=False, errors=["No translated segments to rewrite."], stage="rewrite")
        if len(source_segments) != len(translated_segments):
            return TranslationResult(
                success=False,
                errors=["Source and translated subtitle counts do not match."],
                stage="rewrite",
            )

        provider_type, polisher = self._resolve_ai_provider()
        if not polisher.is_configured():
            return TranslationResult(
                success=False,
                errors=[f"AI provider '{provider_type}' is not configured."],
                stage="rewrite",
            )

        source_texts = [s.get("source_text") or s.get("text") or "" for s in source_segments]
        translated_texts = [s.get("text") or "" for s in translated_segments]
        normalized_src = self._normalize_source_language(src_lang)

        try:
            rewritten_texts, providers_used, warnings = self._run_ai_batches(
                polisher=polisher,
                provider_type=provider_type,
                source_texts=source_texts,
                translated_texts=translated_texts,
                src_lang=normalized_src,
                target_lang=target_lang,
                style_instruction=style_instruction,
                polish_batch_size=len(source_texts),
            )
            if not validate_texts(rewritten_texts, len(source_segments)):
                raise TranslationValidationError("AI rewrite returned an invalid number of segments.")

            final_segments = []
            for source_seg, rewritten_text in zip(source_segments, rewritten_texts):
                final_segments.append(
                    {
                        "start": source_seg["start"],
                        "end": source_seg["end"],
                        "text": (rewritten_text or "").strip(),
                        "source_text": source_seg.get("source_text") or source_seg.get("text", ""),
                        "provider": provider_type,
                        "polished": True,
                    }
                )
            return TranslationResult(
                success=True,
                segments=final_segments,
                warnings=warnings,
                stage="rewrite",
                primary_provider=" -> ".join(providers_used) or provider_type,
                used_fallback=bool(warnings),
            )
        except Exception as exc:
            return TranslationResult(success=False, errors=[str(exc)], stage="rewrite")

    def translate_srt(self, srt_content: str, **kwargs) -> TranslationResult:
        segments = parse_srt(srt_content)
        return self.translate_segments(segments=segments, **kwargs)

    def result_to_srt(self, result: TranslationResult) -> str:
        return to_srt(result.segments)

    def _normalize_source_language(self, src_lang: str) -> str:
        mapping = {
            "auto": "zh-Hans",
            "zh": "zh-Hans",
            "zh-cn": "zh-Hans",
            "zh-hans": "zh-Hans",
            "ja": "ja",
            "ko": "ko",
            "en": "en",
            "vi": "vi",
        }
        key = (src_lang or "zh").strip().lower()
        return mapping.get(key, src_lang)

    def _resolve_ai_provider(self):
        provider_type = (os.getenv("AI_POLISHER_PROVIDER") or "local").strip().lower()
        if provider_type == "gemini":
            return provider_type, self.gemini_polisher
        if provider_type == "local":
            return provider_type, self.local_polisher
        return "local", self.local_polisher

    def _describe_ai_provider(self, provider_type: str) -> str:
        if provider_type == "gemini":
            return f"Gemini ({self.gemini_polisher.model_name})"
        if provider_type == "local":
            return f"Local GGUF ({os.path.basename(self.local_polisher.model_path)})"
        return f"Local GGUF ({os.path.basename(self.local_polisher.model_path)})"

    def _run_ai_batches(
        self,
        *,
        polisher,
        provider_type: str,
        source_texts: list[str],
        translated_texts: list[str] | None,
        src_lang: str,
        target_lang: str,
        style_instruction: str,
        polish_batch_size: int,
    ) -> tuple[list[str], list[str], list[str]]:
        warnings = []
        providers_used = set()

        if provider_type == "local":
            local_batch_size = max(6, min(int(polish_batch_size or 12), 12))
            source_batches = list(split_text_batches(source_texts, local_batch_size))
            translated_batches = list(split_text_batches(translated_texts, local_batch_size)) if translated_texts else [None] * len(source_batches)
            merged: list[str] = []
            for idx, source_batch in enumerate(source_batches):
                batch_result, batch_warnings, provider_name = polisher.polish_batch(
                    source_texts=source_batch,
                    translated_texts=translated_batches[idx],
                    src_lang=src_lang,
                    target_lang=target_lang,
                    style_instruction=style_instruction,
                )
                merged.extend(batch_result)
                warnings.extend(batch_warnings)
                if provider_name:
                    providers_used.add(provider_name)
            return merged, sorted(providers_used), warnings

        source_batches = list(split_text_batches(source_texts, polish_batch_size))
        translated_batches = list(split_text_batches(translated_texts, polish_batch_size)) if translated_texts else [None] * len(source_batches)
        translated_texts_map = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(source_batches), 10)) as executor:
            future_to_idx = {}
            for idx, source_batch in enumerate(source_batches):
                future = executor.submit(
                    polisher.polish_batch,
                    source_texts=source_batch,
                    translated_texts=translated_batches[idx],
                    src_lang=src_lang,
                    target_lang=target_lang,
                    style_instruction=style_instruction,
                )
                future_to_idx[future] = idx

            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    batch_result, batch_warnings, provider_name = future.result()
                    translated_texts_map[idx] = batch_result
                    warnings.extend(batch_warnings)
                    if provider_name:
                        providers_used.add(provider_name)
                except Exception as exc:
                    raise Exception(f"Batch {idx + 1} failed: {exc}") from exc

        merged = []
        for idx in range(len(source_batches)):
            merged.extend(translated_texts_map[idx])
        return merged, sorted(providers_used), warnings
    def _maybe_optimize_subtitle_segments(
        self,
        *,
        polisher,
        provider_type: str,
        source_segments: list[dict],
        translated_segments: list[dict],
        src_lang: str,
        target_lang: str,
        warnings: list[str],
        style_instruction: str = "",
    ) -> list[dict]:
        if not translated_segments:
            return translated_segments
        try:
            print('[AI Subtitle Optimization] Starting subtitle optimization...')
            single_line = self._style_prefers_single_line(style_instruction)
            cleaned_style_instruction = self._strip_layout_markers(style_instruction)
            source_texts = [
                self._build_subtitle_optimization_source_text(seg, single_line=False)
                for seg in source_segments
            ]
            translated_texts = [seg.get('text') or '' for seg in translated_segments]
            optimization_instruction = (
                'Subtitle optimization mode. Start from the existing Vietnamese draft and keep its wording whenever it is already natural and clear. '
                'Only make minimal edits when needed for subtitle readability. '
                'Requirements: (1) keep the meaning faithful and do not add new ideas, (2) prefer the original draft unchanged if it already reads well, '
                '(3) only shorten or simplify when really needed for timing/readability, (4) preserve names, numbers, products, and key terms exactly, '
                '(5) prefer a single natural line whenever possible, (6) use <br> only when a second line is truly needed for readability, '
                '(7) allow up to 2 lines maximum, not as a target. '
                'Return only numbered lines. Keep exact line count.'
            )
            if cleaned_style_instruction:
                optimization_instruction += f' Extra style instruction: {cleaned_style_instruction}'
            optimized_texts, providers_used, batch_warnings = self._run_ai_batches(
                polisher=polisher,
                provider_type=provider_type,
                source_texts=source_texts,
                translated_texts=translated_texts,
                src_lang=src_lang,
                target_lang=target_lang,
                style_instruction=optimization_instruction,
                polish_batch_size=len(source_texts),
            )
            warnings.extend(batch_warnings)
            normalized_texts = [
                self._normalize_optimized_subtitle_text(
                    text,
                    source_seg,
                    draft_text=(translated_seg.get('text') or ''),
                    single_line=False,
                )
                for text, source_seg, translated_seg in zip(optimized_texts, source_segments, translated_segments)
            ]
            if not validate_texts(normalized_texts, len(translated_segments)):
                raise TranslationValidationError('Subtitle optimization returned invalid text count.')
            print(f"[AI Subtitle Optimization] Success: completed via {' -> '.join(providers_used) or provider_type}")
            optimized_segments = clone_with_texts(translated_segments, normalized_texts, provider=provider_type, polished=True)
            if single_line:
                before_count = len(optimized_segments)
                optimized_segments = self._split_segments_for_single_line(
                    optimized_segments,
                    polisher=polisher,
                    provider_type=provider_type,
                    target_lang=target_lang,
                )
                print(f'[AI Subtitle Optimization] Single-line cue split: {before_count} -> {len(optimized_segments)} cues')
            if self._style_prefers_ai_keyword_highlight(style_instruction):
                optimized_segments = self._maybe_generate_ai_keyword_highlights(
                    optimized_segments,
                    target_lang=target_lang,
                    warnings=warnings,
                )
            return optimized_segments
        except Exception as exc:
            msg = f'Subtitle optimization skipped: {exc}'
            print(f'[AI Subtitle Optimization] WARNING: {msg}')
            warnings.append(msg)
            return translated_segments

    def _build_subtitle_optimization_source_text(self, seg: dict, *, single_line: bool = False) -> str:
        start = float(seg.get('start', 0.0) or 0.0)
        end = float(seg.get('end', 0.0) or 0.0)
        duration = max(0.6, end - start)
        max_cps = self._target_max_cps(duration, single_line=single_line)
        max_chars = self._target_max_chars(duration, single_line=single_line)
        max_lines = 1 if single_line else 2
        return (
            f"[duration={duration:.2f}s][max_cps={max_cps}][max_chars={max_chars}]"
            f"[max_lines={max_lines}] {seg.get('text') or ''}"
        )

    def _normalize_optimized_subtitle_text(self, text: str, seg: dict, *, draft_text: str = '', single_line: bool = False) -> str:
        cleaned = str(text or '').replace('<br/>', '\n').replace('<br />', '\n').replace('<br>', '\n')
        cleaned = '\n'.join(' '.join(part.split()) for part in cleaned.splitlines() if part.strip())
        draft = str(draft_text or '').replace('<br/>', '\n').replace('<br />', '\n').replace('<br>', '\n')
        draft = '\n'.join(' '.join(part.split()) for part in draft.splitlines() if part.strip())
        if not cleaned:
            cleaned = draft or str(seg.get('text') or '').strip()
        duration = max(0.6, float(seg.get('end', 0.0) or 0.0) - float(seg.get('start', 0.0) or 0.0))
        wrapped_cleaned = self._wrap_subtitle_text(cleaned, duration, single_line=single_line)
        wrapped_draft = self._wrap_subtitle_text(draft, duration, single_line=single_line) if draft else ''
        if wrapped_draft and self._should_keep_original_translation(wrapped_draft, wrapped_cleaned, duration, single_line=single_line):
            return wrapped_draft
        return wrapped_cleaned

    def _should_keep_original_translation(self, original_text: str, optimized_text: str, duration: float, *, single_line: bool = False) -> bool:
        original = str(original_text or '').strip()
        optimized = str(optimized_text or '').strip()
        if not original or not optimized or original == optimized:
            return False
        if not self._is_subtitle_readable(original, duration, single_line=single_line):
            return False
        overlap = self._token_overlap_ratio(original, optimized)
        length_delta = abs(len(original.replace('\n', ' ')) - len(optimized.replace('\n', ' ')))
        return overlap < 0.58 and length_delta >= 8

    def _is_subtitle_readable(self, text: str, duration: float, *, single_line: bool = False) -> bool:
        normalized = str(text or '').strip()
        if not normalized:
            return False
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return False
        max_lines = 1 if single_line else 2
        if len(lines) > max_lines:
            return False
        max_chars = self._target_max_chars(duration, single_line=single_line)
        if any(len(line) > max_chars + 6 for line in lines):
            return False
        cps = len(' '.join(lines).replace(' ', '')) / max(duration, 0.6)
        return cps <= (self._target_max_cps(duration, single_line=single_line) + 1.5)

    def _token_overlap_ratio(self, left_text: str, right_text: str) -> float:
        left_tokens = re.findall(r'\w+', str(left_text or '').lower())
        right_tokens = re.findall(r'\w+', str(right_text or '').lower())
        if not left_tokens or not right_tokens:
            return 1.0
        left_set = set(left_tokens)
        right_set = set(right_tokens)
        common = len(left_set & right_set)
        return common / max(1, len(left_set | right_set))

    def _wrap_subtitle_text(self, text: str, duration: float, *, single_line: bool = False) -> str:
        normalized = ' '.join(str(text or '').replace('\n', ' \n ').split())
        normalized = normalized.replace(' \n ', '\n').strip()
        if not normalized:
            return ''
        existing_lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if single_line:
            compact = ' '.join(existing_lines).strip()
            return self._shorten_single_line_text(compact, duration)
        single = ' '.join(existing_lines).strip() if len(existing_lines) > 1 else existing_lines[0]
        max_line_chars = self._target_max_chars(duration, single_line=False)
        cps_limit = self._target_max_cps(duration, single_line=False)
        compact_single = ' '.join(single.split()).strip()
        if compact_single:
            compact_cps = len(compact_single.replace(' ', '')) / max(duration, 0.6)
            if len(compact_single) <= max_line_chars + 6 and compact_cps <= cps_limit + 0.8:
                return compact_single
        if len(existing_lines) > 1:
            if len(existing_lines) <= 2 and all(len(line) <= max_line_chars + 6 for line in existing_lines):
                return '\n'.join(existing_lines[:2])
        if len(single) <= max_line_chars or len(single.split()) < 3:
            return single
        words = single.split()
        target = len(single) / 2
        best_index = 1
        best_score = float('inf')
        for idx in range(1, len(words)):
            left = ' '.join(words[:idx]).strip()
            right = ' '.join(words[idx:]).strip()
            if not left or not right:
                continue
            score = abs(len(left) - target) + abs(len(right) - target)
            if len(left) > max_line_chars + 4 or len(right) > max_line_chars + 4:
                score += 12
            if score < best_score:
                best_score = score
                best_index = idx
        left = ' '.join(words[:best_index]).strip()
        right = ' '.join(words[best_index:]).strip()
        if not left or not right:
            return single
        return f'{left}\n{right}'

    def _shorten_single_line_text(self, text: str, duration: float) -> str:
        compact = ' '.join(str(text or '').split()).strip()
        if not compact:
            return ''
        max_chars = self._target_max_chars(duration, single_line=True)
        if len(compact) <= max_chars:
            return compact

        filler_words = {
            'thì', 'là', 'mà', 'đó', 'này', 'ấy', 'vậy', 'nha', 'nhé', 'đi', 'liền', 'ngay', 'rồi', 'đang', 'cũng'
        }
        filler_phrases = [
            'một cách', 'kiểu như', 'có thể nói là', 'thật sự là', 'về cơ bản', 'nói chung là'
        ]

        candidate = compact
        lowered = candidate.lower()
        for phrase in filler_phrases:
            if phrase in lowered:
                candidate = self._remove_phrase_case_insensitive(candidate, phrase)
                lowered = candidate.lower()
        filtered_words = [word for word in candidate.split() if word.lower() not in filler_words]
        candidate = ' '.join(filtered_words).strip() or candidate
        candidate = re.sub(r'\s+([,.;:!?])', r'\1', candidate)
        candidate = re.sub(r'([,.;:!?]){2,}', r'\1', candidate).strip(' ,;:')

        # Keep the full subtitle meaning in single-line mode.
        # We prefer a slightly longer one-line subtitle over trimming away content.
        return candidate or compact

    def _target_max_chars(self, duration: float, *, single_line: bool) -> int:
        if single_line:
            if duration <= 1.2:
                return 12
            if duration <= 1.8:
                return 16
            if duration <= 2.6:
                return 20
            if duration <= 3.4:
                return 24
            return 28
        if duration <= 1.4:
            return 18
        if duration <= 2.6:
            return 22
        if duration <= 4.0:
            return 26
        return 30

    def _target_max_cps(self, duration: float, *, single_line: bool) -> int:
        if single_line:
            if duration <= 1.2:
                return 10
            if duration <= 2.0:
                return 11
            if duration <= 3.2:
                return 12
            return 13
        return max(10, min(16, int(round(13 + min(duration, 4.0) * 0.75))))

    def _strip_layout_markers(self, style_instruction: str) -> str:
        text = str(style_instruction or '')
        text = text.replace('[subtitle_layout=single_line]', ' ')
        text = text.replace('[keyword_highlight=ai_local]', ' ')
        text = text.replace('|  |', '|')
        text = text.replace('||', '|')
        parts = [part.strip() for part in text.split('|') if part.strip()]
        return ' | '.join(parts)

    def _style_prefers_single_line(self, style_instruction: str) -> bool:
        normalized = str(style_instruction or '').strip().lower()
        return (
            '[subtitle_layout=single_line]' in normalized
            or 'single-line' in normalized
            or 'one line only' in normalized
            or 'netflix-style' in normalized
        )

    def _remove_phrase_case_insensitive(self, text: str, phrase: str) -> str:
        return re.sub(re.escape(phrase), '', text, flags=re.IGNORECASE).replace('  ', ' ').strip()

    def _style_prefers_ai_keyword_highlight(self, style_instruction: str) -> bool:
        normalized = str(style_instruction or '').strip().lower()
        return '[keyword_highlight=ai_local]' in normalized

    def _maybe_generate_ai_keyword_highlights(
        self,
        segments: list[dict],
        *,
        target_lang: str,
        warnings: list[str],
    ) -> list[dict]:
        if not segments:
            return segments
        polisher = self.local_polisher
        if not polisher.is_configured():
            msg = 'AI keyword highlight skipped: Local provider is not configured.'
            print(f'[AI Keyword Highlight] WARNING: {msg}')
            warnings.append(msg)
            return segments

        texts = [' '.join(str(seg.get('text') or '').replace('\n', ' ').split()).strip() for seg in segments]
        batch_size = 10
        all_highlights: list[list[str]] = []
        try:
            for start_idx in range(0, len(texts), batch_size):
                batch = texts[start_idx:start_idx + batch_size]
                all_highlights.extend(
                    polisher.select_keyword_highlights_batch(
                        texts=batch,
                        target_lang=target_lang,
                        max_keywords=2,
                    )
                )
            if len(all_highlights) != len(segments):
                raise TranslationValidationError('AI keyword highlight returned invalid segment count.')
        except Exception as exc:
            msg = f'AI keyword highlight skipped: {exc}'
            print(f'[AI Keyword Highlight] WARNING: {msg}')
            warnings.append(msg)
            return segments

        updated_segments: list[dict] = []
        for seg, phrases in zip(segments, all_highlights):
            updated = dict(seg)
            updated['auto_highlights'] = self._normalize_auto_highlight_phrases(updated.get('text', ''), phrases)
            updated_segments.append(updated)
        print('[AI Keyword Highlight] Success: local highlight phrases generated.')
        return updated_segments

    def _normalize_auto_highlight_phrases(self, text: str, phrases) -> list[str]:
        source_text = str(text or '')
        lowered = source_text.lower()
        cleaned: list[str] = []
        seen = set()
        for phrase in phrases or []:
            normalized = ' '.join(str(phrase or '').replace('\n', ' ').split()).strip(' ,;|')
            key = normalized.lower()
            if not normalized or key in seen or key not in lowered:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return cleaned

    def _split_segments_for_single_line(
        self,
        segments: list[dict],
        *,
        polisher=None,
        provider_type: str = "",
        target_lang: str = "vi",
    ) -> list[dict]:
        split_segments: list[dict] = []
        for seg in segments or []:
            text = ' '.join(str(seg.get('text') or '').replace('\n', ' ').split()).strip()
            if not text:
                split_segments.append(dict(seg))
                continue
            start = float(seg.get('start', 0.0) or 0.0)
            end = float(seg.get('end', 0.0) or 0.0)
            duration = max(0.6, end - start)
            tts_text = ' '.join(str(seg.get('tts_text') or text).replace('\n', ' ').split()).strip() or text
            group_id = f"tts-{seg.get('id', len(split_segments) + 1)}-{int(round(start * 1000))}"
            chunks = self._split_text_into_single_line_chunks(
                text,
                duration,
                polisher=polisher,
                provider_type=provider_type,
                target_lang=target_lang,
            )
            if len(chunks) <= 1:
                updated = dict(seg)
                updated['text'] = chunks[0] if chunks else text
                updated['tts_text'] = tts_text
                updated['tts_group_id'] = group_id
                updated['tts_group_start'] = round(start, 3)
                updated['tts_group_end'] = round(end, 3)
                split_segments.append(updated)
                continue

            total_weight = sum(max(1, len(chunk.replace(' ', ''))) for chunk in chunks)
            cursor = start
            for idx, chunk in enumerate(chunks):
                updated = dict(seg)
                updated['text'] = chunk
                updated['tts_text'] = tts_text
                updated['tts_group_id'] = group_id
                updated['tts_group_start'] = round(start, 3)
                updated['tts_group_end'] = round(end, 3)
                if idx == len(chunks) - 1:
                    chunk_end = end
                else:
                    weight = max(1, len(chunk.replace(' ', '')))
                    share = duration * (weight / total_weight)
                    min_slice = 0.45
                    remaining_needed = min_slice * (len(chunks) - idx - 1)
                    max_end = end - remaining_needed
                    chunk_end = min(max_end, cursor + max(min_slice, share))
                updated['start'] = round(cursor, 3)
                updated['end'] = round(max(cursor + 0.2, chunk_end), 3)
                cursor = updated['end']
                split_segments.append(updated)
        return split_segments

    def _try_ai_split_single_line_chunks(
        self,
        text: str,
        duration: float,
        *,
        polisher=None,
        provider_type: str = "",
        target_lang: str = "vi",
        max_chars: int = 24,
    ) -> list[str] | None:
        if polisher is None or not hasattr(polisher, 'split_single_line_text'):
            return None
        if len(text.split()) <= 5:
            return None
        max_chunks = 2 if duration <= 1.6 else 3 if duration <= 3.4 else 4
        try:
            chunks = polisher.split_single_line_text(
                text=text,
                target_lang=target_lang,
                max_chars=max_chars,
                max_chunks=max_chunks,
            )
        except Exception:
            return None
        normalized = [self._normalize_ai_split_chunk(part) for part in chunks if self._normalize_ai_split_chunk(part)]
        if len(normalized) <= 1:
            return None
        original_key = self._normalized_chunk_key(text)
        candidate_key = self._normalized_chunk_key(' '.join(normalized))
        if original_key != candidate_key:
            return None
        if any(len(chunk.split()) <= 1 for chunk in normalized):
            return None
        return normalized

    def _normalize_ai_split_chunk(self, text: str) -> str:
        return ' '.join(str(text or '').replace('\n', ' ').split()).strip(' |,;')

    def _normalized_chunk_key(self, text: str) -> str:
        return re.sub(r'\W+', '', str(text or '').lower(), flags=re.UNICODE)

    def _split_text_into_single_line_chunks(
        self,
        text: str,
        duration: float,
        *,
        polisher=None,
        provider_type: str = "",
        target_lang: str = "vi",
    ) -> list[str]:
        compact = ' '.join(str(text or '').replace('\n', ' ').split()).strip()
        if not compact:
            return []

        max_chars = max(18, self._target_max_chars(duration, single_line=True))
        if len(compact) <= max_chars or len(compact.split()) <= 4:
            return [compact]

        ai_chunks = self._try_ai_split_single_line_chunks(
            compact,
            duration,
            polisher=polisher,
            provider_type=provider_type,
            target_lang=target_lang,
            max_chars=max_chars,
        )
        if ai_chunks:
            return ai_chunks

        sentence_parts = [part.strip(' ,') for part in re.split(r'(?<=[,.;:!?])\s+', compact) if part.strip(' ,')]
        if len(sentence_parts) > 1:
            chunks: list[str] = []
            per_part_duration = max(0.6, duration / max(1, len(sentence_parts)))
            for part in sentence_parts:
                chunks.extend(self._split_text_into_single_line_chunks(part, per_part_duration))
            return chunks

        words = compact.split()
        if len(words) <= 5:
            return [compact]

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        soft_limit = max(18, max_chars)
        for word in words:
            addition = len(word) if not current else len(word) + 1
            if current and current_len + addition > soft_limit and len(current) >= 3:
                chunks.append(' '.join(current).strip())
                current = [word]
                current_len = len(word)
            else:
                current.append(word)
                current_len += addition
        if current:
            chunks.append(' '.join(current).strip())

        return [chunk for chunk in chunks if chunk] or [compact]

