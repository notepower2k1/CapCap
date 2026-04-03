import os
import concurrent.futures
from .errors import TranslationError, TranslationValidationError
from .models import TranslationResult
from .providers import AIPolisherProvider, GeminiPolisherProvider, MicrosoftTranslatorProvider
from .srt_utils import clone_with_texts, parse_srt, split_text_batches, to_srt, validate_texts

class TranslationOrchestrator:
    def __init__(self):
        self.microsoft = MicrosoftTranslatorProvider()
        self.ai_polisher = AIPolisherProvider()
        self.gemini_polisher = GeminiPolisherProvider()

    def translate_segments(
        self,
        *,
        segments: list[dict],
        src_lang: str = "zh-Hans",
        target_lang: str = "vi",
        enable_polish: bool = True,
        ms_batch_size: int = 50,
        polish_batch_size: int = 25,
    ) -> TranslationResult:
        if not segments:
            return TranslationResult(success=False, errors=["No segments to translate."], stage="input")

        source_texts = [s.get("text") or "" for s in segments]
        normalized_src = self._normalize_source_language(src_lang)
        warnings = []

        if enable_polish:
            provider_type = (os.getenv("AI_POLISHER_PROVIDER") or "openrouter").strip().lower()
            polisher = self.gemini_polisher if provider_type == "gemini" else self.ai_polisher
            
            if polisher.is_configured():
                try:
                    mode_label = "Gemini" if provider_type == "gemini" else f"OpenRouter ({self.ai_polisher.openrouter_model})"
                    print(f"[AI Translation] Starting parallel translation (provider: {mode_label}, batch_size={polish_batch_size})...")
                    
                    batches = list(split_text_batches(source_texts, polish_batch_size))
                    translated_texts_map = {} # To keep order
                    providers_used = set()
                    
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(batches), 10)) as executor:
                        future_to_idx = {
                            executor.submit(
                                polisher.polish_batch,
                                source_texts=batch,
                                translated_texts=None,
                                src_lang=normalized_src,
                                target_lang=target_lang
                            ): i for i, batch in enumerate(batches)
                        }
                        
                        for future in concurrent.futures.as_completed(future_to_idx):
                            idx = future_to_idx[future]
                            try:
                                batch_result, batch_warnings, provider_name = future.result()
                                translated_texts_map[idx] = batch_result
                                warnings.extend(batch_warnings)
                                if provider_name:
                                    providers_used.add(provider_name)
                            except Exception as e:
                                raise Exception(f"Batch {idx+1} failed: {e}")

                    # Reassemble in order
                    translated_texts = []
                    for i in range(len(batches)):
                        translated_texts.extend(translated_texts_map[i])

                    if not validate_texts(translated_texts, len(segments)):
                        raise TranslationValidationError("AI translator returned an invalid number of segments.")

                    print(f"[AI Translation] Success: Parallel translation completed via {', '.join(providers_used) or 'AI'}")
                    final_segments = clone_with_texts(segments, translated_texts, provider="ai_polisher", polished=True)
                    return TranslationResult(
                        success=True,
                        segments=final_segments,
                        warnings=warnings,
                        stage="ai_direct",
                        primary_provider=" -> ".join(providers_used),
                        used_fallback=bool(warnings),
                    )
                except Exception as e:
                    msg = f"Parallel AI translation failed, falling back to Microsoft: {e}"
                    print(f"[AI Translation] WARNING: {msg}")
                    warnings.append(msg)
            else:
                print(f"[AI Translation] AI Provider ({provider_type}) not configured, using Microsoft.")

        # Fallback to Microsoft Translator (Sequential because it's usually free/limited)
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
        except Exception as e:
            return TranslationResult(success=False, errors=[str(e)], warnings=warnings, stage="translation")

    def translate_srt(self, srt_content: str, **kwargs) -> TranslationResult:
        segments = parse_srt(srt_content)
        return self.translate_segments(segments=segments, **kwargs)

    def result_to_srt(self, result: TranslationResult) -> str:
        return to_srt(result.segments)

    def _normalize_source_language(self, src_lang: str) -> str:
        mapping = {
            "auto": "zh-Hans", "zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-hans": "zh-Hans",
            "ja": "ja", "ko": "ko", "en": "en", "vi": "vi",
        }
        key = (src_lang or "zh").strip().lower()
        return mapping.get(key, src_lang)
