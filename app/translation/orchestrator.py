import concurrent.futures
import os

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
            result_lines, batch_warnings, provider_name = polisher.polish_batch(
                source_texts=source_texts,
                translated_texts=translated_texts,
                src_lang=src_lang,
                target_lang=target_lang,
                style_instruction=style_instruction,
            )
            warnings.extend(batch_warnings)
            if provider_name:
                providers_used.add(provider_name)
            return result_lines, sorted(providers_used), warnings

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