from __future__ import annotations

from remote_api import remote_api_post


class RemoteTranslatorAdapter:
    def translate_srt(
        self,
        srt_text: str,
        *,
        model_path=None,
        src_lang: str = "auto",
        target_lang: str = "vi",
        enable_polish: bool = True,
        optimize_subtitles: bool = False,
        style_instruction: str = "",
    ) -> str:
        response = remote_api_post(
            "/v1/translate-srt",
            {
                "srt_text": srt_text,
                "src_lang": src_lang,
                "target_lang": target_lang,
                "enable_polish": bool(enable_polish),
                "optimize_subtitles": bool(optimize_subtitles),
                "style_instruction": style_instruction,
            },
        )
        return str(response.get("srt_text") or "")

    def translate_segments(
        self,
        segments,
        *,
        model_path=None,
        src_lang: str = "auto",
        target_lang: str = "vi",
        enable_polish: bool = True,
        optimize_subtitles: bool = False,
        style_instruction: str = "",
        batch_callback=None,
        status_callback=None,
    ):
        response = remote_api_post(
            "/v1/translate-segments",
            {
                "segments": list(segments or []),
                "src_lang": src_lang,
                "target_lang": target_lang,
                "enable_polish": bool(enable_polish),
                "optimize_subtitles": bool(optimize_subtitles),
                "style_instruction": style_instruction,
            },
        )
        if status_callback:
            for update in response.get("translation_status", []) or []:
                if not isinstance(update, (list, tuple)) or len(update) != 2:
                    continue
                provider, message = update
                if not isinstance(provider, str) or not isinstance(message, str):
                    continue
                try:
                    status_callback(provider, message)
                except Exception:
                    pass
        return list(response.get("segments") or [])

    def rewrite_segments(self, source_segments, translated_segments, *, model_path=None, src_lang: str = "auto", style_instruction: str = ""):
        response = remote_api_post(
            "/v1/rewrite-segments",
            {
                "source_segments": list(source_segments or []),
                "translated_segments": list(translated_segments or []),
                "src_lang": src_lang,
                "style_instruction": style_instruction,
            },
        )
        return list(response.get("segments") or [])

    def rewrite_srt(self, source_segments, translated_segments, *, model_path=None, src_lang: str = "auto", style_instruction: str = "") -> str:
        response = remote_api_post(
            "/v1/rewrite-srt",
            {
                "source_segments": list(source_segments or []),
                "translated_segments": list(translated_segments or []),
                "src_lang": src_lang,
                "style_instruction": style_instruction,
            },
        )
        return str(response.get("srt_text") or "")
