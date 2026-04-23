import os
import time
import requests

from ..errors import TranslationConfigError, TranslationProviderError, TranslationValidationError
from ..srt_utils import parse_numbered_lines, validate_texts

class GeminiPolisherProvider:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def polish_batch(
        self,
        *,
        source_texts: list[str],
        translated_texts: list[str] = None,
        src_lang: str,
        target_lang: str,
        style_instruction: str = "",
        timeout: int = 60,
        max_retries: int = 2,
    ) -> tuple[list[str], list[str], str]:
        """
        Returns (polished_texts, warnings, provider_name)
        """
        if not self.is_configured():
            raise TranslationConfigError("GEMINI_API_KEY is not set in .env")

        prompt = self._build_prompt(
            source_texts=source_texts,
            translated_texts=translated_texts,
            src_lang=src_lang,
            target_lang=target_lang,
            style_instruction=style_instruction,
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "topK": 40,
                "topP": 0.95,
                "maxOutputTokens": 4096,
            }
        }
        headers = {"Content-Type": "application/json"}

        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                if response.status_code != 200:
                    last_error = f"Gemini error ({response.status_code}): {response.text}"
                    if attempt < max_retries:
                        time.sleep(attempt)
                        continue
                    raise Exception(last_error)

                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    raise Exception(f"No candidates: {data}")

                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                if not text:
                    raise Exception("Empty response text")

                lines = parse_numbered_lines(text)
                if not validate_texts(lines, len(source_texts)):
                    raise TranslationValidationError(f"Expected {len(source_texts)} lines, got {len(lines)}")
                
                return lines, [], "gemini"
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue
        
        raise TranslationProviderError(f"Gemini failed: {last_error}")

    def _build_prompt(self, source_texts, translated_texts, src_lang, target_lang, style_instruction) -> str:
        is_direct = not translated_texts
        style_part = f" Style: {style_instruction}" if style_instruction else ""
        dubbing_mode = "[mode=dubbing_rewrite]" in str(style_instruction or "").lower()
        
        if is_direct:
            lines = [f"{i+1}. {s}" for i, s in enumerate(source_texts)]
            header = f"Translate these {src_lang}->{target_lang} subtitles directly.{style_part}\nFormat: Number. Text"
        else:
            lines = [f"{i+1}. {s} ||| {t}" for i, (s, t) in enumerate(zip(source_texts, translated_texts))]
            if dubbing_mode:
                header = f"Rewrite these {src_lang}->{target_lang} dubbing drafts for TTS timing rescue.{style_part}\nFormat: Number. Source ||| Draft"
            else:
                header = f"Refine these {src_lang}->{target_lang} subtitle translations.{style_part}\nFormat: Number. Source ||| Draft"
        rules = "Rules: Natural, punchy, concise. One translation per number. No commentary."
        if dubbing_mode:
            rules = (
                "Rules: Spoken dubbing rescue only. Follow source metadata as hard timing constraints. "
                "Make the result shorter than the draft when needed. Preserve names, numbers, products, and key claims exactly. "
                "One short spoken line per number. No commentary."
            )

        return (
            f"{header}\n"
            f"{rules}\n\n"
            + "\n".join(lines)
        )
