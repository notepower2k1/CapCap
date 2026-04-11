import json
import time
from urllib.parse import quote

import requests

from ..errors import TranslationProviderError


class GoogleWebTranslatorProvider:
    BASE_URL = "https://translate.googleapis.com/translate_a/single"

    def is_configured(self) -> bool:
        return True

    def translate_batch(
        self,
        texts: list[str],
        *,
        src_lang: str,
        target_lang: str,
        timeout: int = 20,
        max_retries: int = 2,
    ) -> list[str]:
        translated = []
        for text in texts:
            translated.append(
                self._translate_text(
                    text=text,
                    src_lang=src_lang,
                    target_lang=target_lang,
                    timeout=timeout,
                    max_retries=max_retries,
                )
            )
        return translated

    def _translate_text(
        self,
        *,
        text: str,
        src_lang: str,
        target_lang: str,
        timeout: int,
        max_retries: int,
    ) -> str:
        last_error = ""
        query = quote(text or "", safe="")
        url = (
            f"{self.BASE_URL}?client=gtx&sl={src_lang}&tl={target_lang}"
            f"&dt=t&q={query}"
        )
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        }

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, headers=headers, timeout=timeout)
                if response.status_code != 200:
                    last_error = f"Google web translate error ({response.status_code}): {response.text}"
                    if attempt < max_retries:
                        time.sleep(attempt)
                        continue
                    raise TranslationProviderError(last_error)

                payload = response.json()
                translated = self._extract_text(payload)
                if translated:
                    return translated
                raise TranslationProviderError("Google web translate returned empty text.")
            except (requests.RequestException, json.JSONDecodeError, TranslationProviderError) as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue

        raise TranslationProviderError(last_error or "Google web translate failed.")

    def _extract_text(self, payload) -> str:
        if not isinstance(payload, list) or not payload:
            return ""
        sentences = payload[0]
        if not isinstance(sentences, list):
            return ""
        parts = []
        for item in sentences:
            if isinstance(item, list) and item:
                chunk = item[0]
                if isinstance(chunk, str):
                    parts.append(chunk)
        return "".join(parts).strip()
