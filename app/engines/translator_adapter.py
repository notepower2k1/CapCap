class TranslatorAdapter:
    @staticmethod
    def _translator_module():
        from translator import (
            rewrite_translated_segments,
            rewrite_translated_segments_to_srt,
            translate_segments,
            translate_segments_to_srt,
        )

        return {
            "rewrite_translated_segments": rewrite_translated_segments,
            "rewrite_translated_segments_to_srt": rewrite_translated_segments_to_srt,
            "translate_segments": translate_segments,
            "translate_segments_to_srt": translate_segments_to_srt,
        }

    def translate_srt(
        self,
        srt_text: str,
        *,
        model_path=None,
        src_lang: str = "auto",
        enable_polish: bool = True,
        optimize_subtitles: bool = True,
        style_instruction: str = "",
    ) -> str:
        funcs = self._translator_module()
        return funcs["translate_segments_to_srt"](
            srt_text,
            model_path,
            src_lang=src_lang,
            enable_polish=enable_polish,
            optimize_subtitles=optimize_subtitles,
            style_instruction=style_instruction,
        )

    def translate_segments(
        self,
        segments,
        *,
        model_path=None,
        src_lang: str = "auto",
        enable_polish: bool = True,
        optimize_subtitles: bool = True,
        style_instruction: str = "",
    ):
        funcs = self._translator_module()
        return funcs["translate_segments"](
            segments,
            model_path,
            src_lang=src_lang,
            enable_polish=enable_polish,
            optimize_subtitles=optimize_subtitles,
            style_instruction=style_instruction,
        )

    def rewrite_segments(self, source_segments, translated_segments, *, model_path=None, src_lang: str = "auto", style_instruction: str = ""):
        funcs = self._translator_module()
        return funcs["rewrite_translated_segments"](
            source_segments,
            translated_segments,
            model_path,
            src_lang=src_lang,
            style_instruction=style_instruction,
        )

    def rewrite_srt(self, source_segments, translated_segments, *, model_path=None, src_lang: str = "auto", style_instruction: str = "") -> str:
        funcs = self._translator_module()
        return funcs["rewrite_translated_segments_to_srt"](
            source_segments,
            translated_segments,
            model_path,
            src_lang=src_lang,
            style_instruction=style_instruction,
        )
