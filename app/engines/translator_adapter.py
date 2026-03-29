from translator import (
    rewrite_translated_segments,
    rewrite_translated_segments_to_srt,
    translate_segments,
    translate_segments_to_srt,
)


class TranslatorAdapter:
    def translate_srt(self, srt_text: str, *, model_path=None, src_lang: str = "auto", enable_polish: bool = True) -> str:
        return translate_segments_to_srt(
            srt_text,
            model_path,
            src_lang=src_lang,
            enable_polish=enable_polish,
        )

    def translate_segments(self, segments, *, model_path=None, src_lang: str = "auto", enable_polish: bool = True):
        return translate_segments(
            segments,
            model_path,
            src_lang=src_lang,
            enable_polish=enable_polish,
        )

    def rewrite_segments(self, source_segments, translated_segments, *, model_path=None, src_lang: str = "auto", style_instruction: str = ""):
        return rewrite_translated_segments(
            source_segments,
            translated_segments,
            model_path,
            src_lang=src_lang,
            style_instruction=style_instruction,
        )

    def rewrite_srt(self, source_segments, translated_segments, *, model_path=None, src_lang: str = "auto", style_instruction: str = "") -> str:
        return rewrite_translated_segments_to_srt(
            source_segments,
            translated_segments,
            model_path,
            src_lang=src_lang,
            style_instruction=style_instruction,
        )
