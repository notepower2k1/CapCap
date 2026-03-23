import os
import time

import requests

from ..errors import TranslationConfigError, TranslationProviderError, TranslationValidationError
from ..srt_utils import parse_numbered_lines, validate_texts


class AIPolisherProvider:
    def __init__(self):
        self.url = os.getenv("AI_POLISHER_URL", os.getenv("TRANSLATOR_URL", "")).strip()
        self.api_key = os.getenv("AI_POLISHER_API_KEY", os.getenv("TRANSLATOR_API_KEY", "")).strip()

    def is_configured(self) -> bool:
        return bool(self.url and self.api_key)

    def polish_batch(
        self,
        *,
        source_texts: list[str],
        translated_texts: list[str],
        src_lang: str,
        target_lang: str,
        timeout: int = 60,
        max_retries: int = 2,
    ) -> list[str]:
        if not self.is_configured():
            raise TranslationConfigError(
                "AI polisher is not configured. Please set AI_POLISHER_URL and AI_POLISHER_API_KEY in .env."
            )

        prompt = self._build_prompt(
            source_texts=source_texts,
            translated_texts=translated_texts,
            src_lang=src_lang,
            target_lang=target_lang,
        )
        payload = {"text": prompt}
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(self.url, headers=headers, json=payload, timeout=timeout)
                if response.status_code != 200:
                    last_error = f"AI polisher error ({response.status_code}): {response.text}"
                    if attempt < max_retries:
                        time.sleep(attempt)
                        continue
                    raise TranslationProviderError(last_error)

                polished = self._extract_text(response.json())
                lines = parse_numbered_lines(polished)
                if not validate_texts(lines, len(translated_texts)):
                    raise TranslationValidationError(
                        f"AI polisher returned {len(lines)} items for {len(translated_texts)} translated segments."
                    )
                return lines
            except requests.exceptions.Timeout as e:
                last_error = f"AI polisher timed out: {e}"
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue
            except requests.RequestException as e:
                last_error = f"AI polisher request failed: {e}"
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue

        raise TranslationProviderError(last_error or "AI polisher failed.")

    def _build_prompt(self, *, source_texts: list[str], translated_texts: list[str], src_lang: str, target_lang: str) -> str:
        lines = []
        for idx, (source, translated) in enumerate(zip(source_texts, translated_texts), 1):
            lines.append(f"{idx}. SOURCE: {source}")
            lines.append(f"{idx}. DRAFT: {translated}")
        body = "\n".join(lines)
        return (
            f"Polish the following subtitle translations from {src_lang} into {target_lang}.\n"
            "Rules:\n"
            "- Keep the exact same number of items.\n"
            "- Do not add commentary.\n"
            "- Preserve meaning.\n"
            "- Make each line natural, concise, and suitable for spoken video subtitles.\n"
            "- Return only numbered lines in the form '1. ...'.\n\n"
            f"{body}"
        )

    def _extract_text(self, result) -> str:
        if isinstance(result, dict):
            if "response" in result:
                return result["response"]
            if "translation" in result:
                return result["translation"]
            if "translated_text" in result:
                return result["translated_text"]
            if "result" in result:
                inner = result["result"]
                if isinstance(inner, dict) and "response" in inner:
                    return inner["response"]
                if isinstance(inner, str):
                    return inner
        raise TranslationProviderError(f"Unexpected AI polisher response format: {result}")
