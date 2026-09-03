import concurrent.futures
import math
import os
import re

from .errors import TranslationValidationError
from .models import TranslationResult
from .prompt_loader import render_prompt
from .providers import (
    GoogleWebTranslatorProvider,
    OpenAICompatiblePolisherProvider,
)
from .srt_utils import clone_with_texts, parse_srt, split_text_batches, to_srt, validate_texts


class AIBatchTranslationError(Exception):
    """The full-context recovery batches could not be completed."""


class TranslationOrchestrator:
    def __init__(self):
        self.google_web = GoogleWebTranslatorProvider()

    def translate_segments(
        self,
        *,
        segments: list[dict],
        src_lang: str = "zh-Hans",
        target_lang: str = "vi",
        enable_polish: bool = True,
        optimize_subtitles: bool = False,
        ms_batch_size: int = 50,
        polish_batch_size: int = 80,
        style_instruction: str = "",
        batch_callback=None,
    ) -> TranslationResult:
        if not segments:
            return TranslationResult(success=False, errors=["No segments to translate."], stage="input")

        source_texts = [s.get("text") or "" for s in segments]
        normalized_src = self._normalize_source_language(src_lang)
        warnings = []
        optimize_subtitles = False

        if enable_polish:
            provider_type, polisher = self._resolve_ai_provider()
            if polisher.is_configured():
                try:
                    mode_label = self._describe_ai_provider(provider_type)
                    merged_style = str(style_instruction or "")
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
                        style_instruction=merged_style,
                        polish_batch_size=polish_batch_size,
                    )
                    warnings.extend(batch_warnings)

                    if not validate_texts(translated_texts, len(segments)):
                        raise TranslationValidationError("AI translator returned an invalid number of segments.")

                    print(f"[AI Translation] Success: completed via {', '.join(providers_used) or 'AI'}")
                    final_segments = clone_with_texts(segments, translated_texts, provider=provider_type, polished=True)
                    return TranslationResult(
                        success=True,
                        segments=final_segments,
                        warnings=warnings,
                        stage="ai_direct",
                        primary_provider=" -> ".join(providers_used) or provider_type,
                        used_fallback=bool(warnings),
                    )
                except Exception as exc:
                    if isinstance(exc, AIBatchTranslationError):
                        msg = "AI batch translation failed. Falling back to Google Translate."
                    else:
                        msg = "AI Provider is unavailable. Falling back to Google Translate..."
                    print(f"[AI Translation] WARNING: {msg} ({exc})")
                    warnings.append(msg)
            else:
                selected_provider = str(os.getenv("OPENAI_PROVIDER") or "google").strip().lower()
                if selected_provider != "google":
                    msg = "AI Provider is unavailable. Falling back to Google Translate..."
                    print(f"[AI Translation] WARNING: {msg}")
                    warnings.append(msg)
                else:
                    print("[AI Translation] Google Translate selected.")

        print(f"[Translation] Starting Google web translate fallback (batch_size={ms_batch_size})...")
        try:
            translated_texts = []
            offset = 0
            for batch in split_text_batches(source_texts, ms_batch_size):
                translated_batch = self.google_web.translate_batch(
                    batch,
                    src_lang=normalized_src,
                    target_lang=target_lang,
                )
                translated_texts.extend(translated_batch)
                self._emit_batch_callback(
                    batch_callback=batch_callback,
                    base_segments=segments,
                    start_idx=offset,
                    translated_texts=translated_batch,
                    provider="google-web",
                    polished=False,
                )
                offset += len(batch)

            if not validate_texts(translated_texts, len(segments)):
                raise TranslationValidationError("Google web translate returned an invalid number of segments.")

            print("[Translation] Success: Google web translate completed.")
            final_segments = clone_with_texts(segments, translated_texts, provider="google-web", polished=False)
            return TranslationResult(
                success=True,
                segments=final_segments,
                warnings=warnings,
                stage="translation",
                primary_provider="google-web",
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
        # All selectable API providers use the same compatible client.  The
        # provider definition here is the only place needed to add another
        # OpenAI-compatible service in the future.
        configured_provider = (os.getenv("OPENAI_PROVIDER") or os.getenv("AI_POLISHER_PROVIDER") or "gemini").strip().lower()
        provider_type = configured_provider
        if provider_type == "gemini":  # backward compatibility for saved settings
            provider_type = "google_ai_studio"
        definitions = {
            "google_ai_studio": ("Google AI Studio", "GOOGLE_AI_STUDIO", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.7-flash"),
            "openai": ("OpenAI", "OPENAI", "https://api.openai.com/v1/", "gpt-4o-mini"),
            "ollama": ("Ollama", "OPENAI", "http://localhost:11434/v1", "gemma4:31b-cloud"),
        }
        if provider_type not in definitions:
            provider_type = "google_ai_studio"
        display_name, env_prefix, base_url, default_model = definitions[provider_type]
        if configured_provider == "gemini":
            env_prefix = "OPENAI"
        return provider_type, OpenAICompatiblePolisherProvider(
            provider_id=provider_type,
            display_name=display_name,
            env_prefix=env_prefix,
            default_base_url=base_url,
            default_model=default_model,
        )

    def _describe_ai_provider(self, provider_type: str) -> str:
        names = {"google_ai_studio": "Google AI Studio", "openai": "OpenAI", "ollama": "Ollama"}
        return f"{names.get(provider_type, 'AI')} ({getattr(self._resolve_ai_provider()[1], 'model_name', '')})"

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

        # Modern cloud models benefit from enough neighbouring subtitle cues
        # to keep terminology, names, and tone consistent.  Do not rely only
        # on a cue count though: long subtitle files can still exceed a
        # provider's practical context/output budget.  The same boundaries
        # are used for source and draft text, preventing misaligned rewrites.
        batches, full_context_request = self._build_ai_batches(
            source_texts=source_texts,
            translated_texts=translated_texts,
            requested_max_segments=polish_batch_size,
        )
        if not full_context_request:
            print(
                "[AI Translation] Batching: "
                f"segments={len(source_texts)}, requests={len(batches)}, "
                f"max_segments={max((len(source) for source, _draft, _tokens in batches), default=0)}"
            )
        try:
            return self._run_ai_batch_requests(
                polisher=polisher,
                batches=batches,
                src_lang=src_lang,
                target_lang=target_lang,
                style_instruction=style_instruction,
                max_workers=1 if full_context_request else min(len(batches), 4),
            )
        except TranslationValidationError as exc:
            if not full_context_request:
                raise
            print("[AI Translation] Full-context response incomplete. Retrying with ordered batches.")
            fallback_batches, _unused_full_context = self._build_ai_batches(
                source_texts=source_texts,
                translated_texts=translated_texts,
                requested_max_segments=polish_batch_size,
                force_ordered=True,
            )
            print(
                "[AI Translation] Ordered batch retry: "
                f"requests={len(fallback_batches)}, "
                f"max_segments={max((len(source) for source, _draft, _tokens in fallback_batches), default=0)}"
            )
            try:
                recovered = self._run_ai_batch_requests(
                    polisher=polisher,
                    batches=fallback_batches,
                    src_lang=src_lang,
                    target_lang=target_lang,
                    style_instruction=style_instruction,
                    max_workers=min(len(fallback_batches), 4),
                )
                print("[AI Translation] Batch translation completed successfully.")
                return recovered
            except Exception as batch_exc:
                print(f"[AI Translation] AI batch translation failed. Falling back to Google Translate. ({batch_exc})")
                raise AIBatchTranslationError(str(batch_exc)) from exc

    @staticmethod
    def _run_ai_batch_requests(*, polisher, batches, src_lang, target_lang, style_instruction, max_workers):
        """Submit validated ordered batches and merge their results by index."""
        warnings = []
        providers_used = set()
        translated_texts_map = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            future_to_idx = {}
            for idx, (source_batch, translated_batch, max_tokens) in enumerate(batches):
                future = executor.submit(
                    polisher.polish_batch,
                    source_texts=source_batch,
                    translated_texts=translated_batch,
                    src_lang=src_lang,
                    target_lang=target_lang,
                    style_instruction=style_instruction,
                    max_tokens=max_tokens,
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
                    if isinstance(exc, TranslationValidationError):
                        raise
                    raise Exception(f"Batch {idx + 1} failed: {exc}") from exc

        merged = []
        for idx in range(len(batches)):
            merged.extend(translated_texts_map[idx])
        return merged, sorted(providers_used), warnings

    @staticmethod
    def _build_ai_batches(
        *,
        source_texts: list[str],
        translated_texts: list[str] | None,
        requested_max_segments: int,
        force_ordered: bool = False,
    ) -> tuple[list[tuple[list[str], list[str] | None, int]], bool]:
        """Create ordered AI batches with both cue and prompt-size limits.

        Prefer one request for the full video whenever its estimated input and
        numbered response fit a conservative provider-independent budget.
        This gives the model full narrative context.  Longer videos fall back
        to ordered context-sized batches.  Environment values are optional
        escape hatches for self-hosted providers with smaller limits.
        """
        if translated_texts is not None and len(source_texts) != len(translated_texts):
            raise TranslationValidationError("Source and draft subtitle counts do not match.")

        def _env_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError:
                return default

        def _estimate_tokens(text: str) -> int:
            # CJK glyphs commonly consume approximately one token each;
            # Latin-script text is generally less dense.  Using the larger
            # estimate keeps the automatic fallback conservative.
            text = str(text or "")
            cjk = sum(
                1 for char in text
                if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"
            )
            return cjk + math.ceil((len(text) - cjk) / 3.5)

        source_token_estimate = sum(_estimate_tokens(text) for text in source_texts)
        draft_token_estimate = (
            sum(_estimate_tokens(text) for text in translated_texts)
            if translated_texts is not None else 0
        )
        input_tokens = source_token_estimate + draft_token_estimate + (12 * len(source_texts)) + 220
        # Translation can expand compact CJK dialogue considerably.  This
        # leaves response room without assuming a specific destination language.
        response_tokens = max(512, math.ceil(max(source_token_estimate, draft_token_estimate) * 1.8) + (10 * len(source_texts)))
        context_limit = max(4096, _env_int("CAPCAP_AI_TRANSLATION_CONTEXT_TOKENS", 24000))
        output_limit = max(1024, _env_int("CAPCAP_AI_TRANSLATION_MAX_OUTPUT_TOKENS", 8192))
        if not force_ordered and input_tokens + response_tokens <= context_limit and response_tokens <= output_limit:
            print(
                "[AI Translation] Full-context request: "
                f"input~{input_tokens} tokens, output~{response_tokens} tokens."
            )
            return ([(list(source_texts), list(translated_texts) if translated_texts is not None else None,
                     min(output_limit, max(1024, response_tokens)))], True)

        try:
            configured_max = int(os.getenv("CAPCAP_AI_TRANSLATION_MAX_SEGMENTS", "80"))
        except ValueError:
            configured_max = 80
        max_segments = max(1, min(int(requested_max_segments or 80), max(1, configured_max)))
        max_chars = _env_int("CAPCAP_AI_TRANSLATION_MAX_CHARS", 18000)
        # Draft rewriting sends both source and translated text in the prompt.
        max_chars = max(2000, max_chars // (2 if translated_texts is not None else 1))

        batches: list[tuple[list[str], list[str] | None, int]] = []
        current_source: list[str] = []
        current_drafts: list[str] | None = [] if translated_texts is not None else None
        current_chars = 0
        current_response_tokens = 0
        for index, source in enumerate(source_texts):
            source = str(source or "")
            draft = str(translated_texts[index] or "") if translated_texts is not None else ""
            item_chars = len(source) + len(draft) + 16
            item_response_tokens = math.ceil(max(_estimate_tokens(source), _estimate_tokens(draft)) * 1.8) + 10
            if current_source and (
                len(current_source) >= max_segments
                or current_chars + item_chars > max_chars
                or current_response_tokens + item_response_tokens > 3600
            ):
                batches.append((current_source, current_drafts, max(1024, min(4096, current_response_tokens + 128))))
                current_source = []
                current_drafts = [] if translated_texts is not None else None
                current_chars = 0
                current_response_tokens = 0
            current_source.append(source)
            if current_drafts is not None:
                current_drafts.append(draft)
            current_chars += item_chars
            current_response_tokens += item_response_tokens
        if current_source:
            batches.append((current_source, current_drafts, max(1024, min(4096, current_response_tokens + 128))))
        return batches, False

    def _emit_batch_callback(
        self,
        *,
        batch_callback,
        base_segments: list[dict],
        start_idx: int,
        translated_texts: list[str],
        provider: str,
        polished: bool,
    ) -> None:
        if batch_callback is None or not translated_texts:
            return
        try:
            batch_segments = clone_with_texts(
                list(base_segments[start_idx:start_idx + len(translated_texts)]),
                list(translated_texts),
                provider=provider,
                polished=polished,
            )
            batch_callback(start_idx, batch_segments)
        except Exception as exc:
            print(f"[Translation] Batch callback skipped: {exc}")
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
            style_clause = (
                f' Extra style instruction: {cleaned_style_instruction}'
                if cleaned_style_instruction else ''
            )
            optimization_instruction = render_prompt(
                'subtitle_optimization.instruction.md',
                style_clause=style_clause,
            )
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

    def _split_segments_for_single_line(
        self,
        segments: list[dict],
        *,
        polisher=None,
        provider_type: str = "",
        target_lang: str = "vi",
        words_per_segment: int | None = None,
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
            group_id = f"tts-{seg.get('id', len(split_segments) + 1)}-{int(round(start * 1000))}"
            chunks = self._split_text_into_single_line_chunks(
                text,
                duration,
                polisher=polisher,
                provider_type=provider_type,
                target_lang=target_lang,
                words_per_segment=words_per_segment,
            )
            if len(chunks) <= 1:
                updated = dict(seg)
                updated['text'] = chunks[0] if chunks else text
                updated['tts_group_id'] = group_id
                updated['tts_group_start'] = round(start, 3)
                updated['tts_group_end'] = round(end, 3)
                updated.pop('_audio_end', None)
                split_segments.append(updated)
                continue

            total_weight = sum(max(1, len(chunk.replace(' ', ''))) for chunk in chunks)
            cursor = start
            for idx, chunk in enumerate(chunks):
                updated = dict(seg)
                updated.pop('_audio_end', None)
                updated['text'] = chunk
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
        words_per_segment: int | None = None,
    ) -> list[str]:
        compact = ' '.join(str(text or '').replace('\n', ' ').split()).strip()
        if not compact:
            return []

        if words_per_segment is not None:
            limit = max(1, int(words_per_segment))
            words = compact.split()
            return [" ".join(words[index:index + limit]) for index in range(0, len(words), limit)]

        max_chars = max(18, self._target_max_chars(duration, single_line=True))
        if len(compact) <= max_chars or len(compact.split()) <= 4:
            return [compact]

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
