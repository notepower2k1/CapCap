from .errors import TranslationError, TranslationValidationError
from .models import TranslationResult
from .providers import AIPolisherProvider, MicrosoftTranslatorProvider
from .srt_utils import clone_with_texts, parse_srt, split_text_batches, to_srt, validate_texts


class TranslationOrchestrator:
    def __init__(self):
        self.microsoft = MicrosoftTranslatorProvider()
        self.ai_polisher = AIPolisherProvider()

    def translate_segments(
        self,
        segments: list[dict],
        *,
        src_lang: str = "zh-Hans",
        target_lang: str = "vi",
        enable_polish: bool = True,
        ms_batch_size: int = 25,
        polish_batch_size: int = 5,
    ) -> TranslationResult:
        if not segments:
            return TranslationResult(success=False, errors=["No segments to translate."], stage="input")

        normalized_src = self._normalize_source_language(src_lang)
        source_texts = [(seg.get("text") or "").strip() for seg in segments]

        translated_texts = []
        for batch in split_text_batches(source_texts, ms_batch_size):
            translated_texts.extend(
                self.microsoft.translate_batch(
                    batch,
                    src_lang=normalized_src,
                    target_lang=target_lang,
                )
            )

        if not validate_texts(translated_texts, len(segments)):
            raise TranslationValidationError("Microsoft Translator returned an invalid number of translated segments.")

        translated_segments = clone_with_texts(segments, translated_texts, provider="microsoft", polished=False)
        warnings = []

        if enable_polish and self.ai_polisher.is_configured():
            try:
                polished_texts = []
                polish_providers_used = []
                for src_batch, draft_batch in zip(
                    split_text_batches(source_texts, polish_batch_size),
                    split_text_batches(translated_texts, polish_batch_size),
                ):
                    polished_batch = self.ai_polisher.polish_batch(
                        source_texts=src_batch,
                        translated_texts=draft_batch,
                        src_lang=normalized_src,
                        target_lang=target_lang,
                    )
                    polished_texts.extend(polished_batch)
                    warnings.extend(self.ai_polisher.last_warnings)
                    if self.ai_polisher.last_provider and self.ai_polisher.last_provider not in polish_providers_used:
                        polish_providers_used.append(self.ai_polisher.last_provider)

                if not validate_texts(polished_texts, len(segments)):
                    raise TranslationValidationError("AI polisher returned an invalid number of polished segments.")

                polished_segments = clone_with_texts(segments, polished_texts, provider="ai_polisher", polished=True)
                return TranslationResult(
                    success=True,
                    segments=polished_segments,
                    warnings=warnings,
                    stage="polish",
                    primary_provider="microsoft",
                    polish_provider=" -> ".join(polish_providers_used),
                    used_fallback=bool(warnings),
                )
            except TranslationError as e:
                warnings.append(f"AI polish failed, using Microsoft translation: {e}")

        return TranslationResult(
            success=True,
            segments=translated_segments,
            warnings=warnings,
            stage="translation",
            primary_provider="microsoft",
            polish_provider="openrouter -> translator-api.thach-nv" if self.ai_polisher.is_configured() else "",
            used_fallback=bool(warnings),
        )

    def translate_srt(
        self,
        srt_text: str,
        *,
        src_lang: str = "zh-Hans",
        target_lang: str = "vi",
        enable_polish: bool = True,
    ) -> TranslationResult:
        segments = parse_srt(srt_text)
        result = self.translate_segments(
            segments,
            src_lang=src_lang,
            target_lang=target_lang,
            enable_polish=enable_polish,
        )
        return result

    def rewrite_segments(
        self,
        source_segments: list[dict],
        translated_segments: list[dict],
        *,
        src_lang: str = "zh-Hans",
        target_lang: str = "vi",
        style_instruction: str = "",
        polish_batch_size: int = 5,
    ) -> TranslationResult:
        if not source_segments or not translated_segments:
            return TranslationResult(success=False, errors=["No segments to rewrite."], stage="input")
        if len(source_segments) != len(translated_segments):
            return TranslationResult(success=False, errors=["Source and translated segment counts do not match."], stage="input")
        if not self.ai_polisher.is_configured():
            return TranslationResult(success=False, errors=["AI polisher is not configured."], stage="config")

        normalized_src = self._normalize_source_language(src_lang)
        source_texts = [(seg.get("text") or "").strip() for seg in source_segments]
        translated_texts = [(seg.get("text") or "").strip() for seg in translated_segments]
        warnings = []
        polish_providers_used = []
        polished_texts = self.ai_polisher.polish_batch(
            source_texts=source_texts,
            translated_texts=translated_texts,
            src_lang=normalized_src,
            target_lang=target_lang,
            style_instruction=style_instruction,
        )
        warnings.extend(self.ai_polisher.last_warnings)
        if self.ai_polisher.last_provider:
            polish_providers_used.append(self.ai_polisher.last_provider)

        if not validate_texts(polished_texts, len(translated_segments)):
            raise TranslationValidationError("AI rewrite returned an invalid number of polished segments.")

        polished_segments = clone_with_texts(source_segments, polished_texts, provider="ai_polisher", polished=True)
        for idx, translated in enumerate(translated_segments):
            for key in ("manual_highlights", "source_text", "provider", "polished"):
                if key in translated and key not in polished_segments[idx]:
                    polished_segments[idx][key] = translated[key]
            if "manual_highlights" in translated:
                polished_segments[idx]["manual_highlights"] = translated["manual_highlights"]

        return TranslationResult(
            success=True,
            segments=polished_segments,
            warnings=warnings,
            stage="rewrite",
            primary_provider="microsoft",
            polish_provider=" -> ".join(polish_providers_used),
            used_fallback=bool(warnings),
        )

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
