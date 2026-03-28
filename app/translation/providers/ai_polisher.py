import os
import time

import requests

from ..errors import TranslationConfigError, TranslationProviderError, TranslationValidationError
from ..srt_utils import parse_numbered_lines, validate_texts


class AIPolisherProvider:
    def __init__(self):
        self.openrouter_url = os.getenv(
            "OPENROUTER_API_URL",
            "https://openrouter.ai/api/v1/chat/completions",
        ).strip()
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", "stepfun/step-3.5-flash:free").strip()
        self.openrouter_site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
        self.openrouter_app_name = os.getenv("OPENROUTER_APP_NAME", "CapCap").strip()

        self.fallback_url = os.getenv("AI_POLISHER_URL", os.getenv("TRANSLATOR_URL", "")).strip()
        self.fallback_api_key = os.getenv("AI_POLISHER_API_KEY", os.getenv("TRANSLATOR_API_KEY", "")).strip()

        self.last_provider = ""
        self.last_warnings: list[str] = []

    def is_configured(self) -> bool:
        return self._has_openrouter() or self._has_fallback()

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
        self.last_provider = ""
        self.last_warnings = []

        if not self.is_configured():
            raise TranslationConfigError(
                "AI polisher is not configured. Please set OPENROUTER_API_KEY or AI_POLISHER_URL and AI_POLISHER_API_KEY in .env."
            )

        errors = []

        if self._has_openrouter():
            try:
                lines = self._polish_with_openrouter(
                    source_texts=source_texts,
                    translated_texts=translated_texts,
                    src_lang=src_lang,
                    target_lang=target_lang,
                    timeout=timeout,
                    max_retries=max_retries,
                )
                self.last_provider = "openrouter"
                return lines
            except TranslationProviderError as e:
                errors.append(str(e))
                if self._has_fallback():
                    self.last_warnings.append(
                        f"OpenRouter polish failed, falling back to translator-api.thach-nv: {e}"
                    )
            except TranslationValidationError as e:
                errors.append(str(e))
                if self._has_fallback():
                    self.last_warnings.append(
                        f"OpenRouter polish returned invalid output, falling back to translator-api.thach-nv: {e}"
                    )

        if self._has_fallback():
            try:
                lines = self._polish_with_fallback(
                    source_texts=source_texts,
                    translated_texts=translated_texts,
                    src_lang=src_lang,
                    target_lang=target_lang,
                    timeout=timeout,
                    max_retries=max_retries,
                )
                self.last_provider = "translator-api.thach-nv"
                return lines
            except TranslationProviderError as e:
                errors.append(str(e))
            except TranslationValidationError as e:
                errors.append(str(e))

        raise TranslationProviderError(" | ".join(errors) or "AI polisher failed.")

    def _has_openrouter(self) -> bool:
        return bool(self.openrouter_url and self.openrouter_api_key and self.openrouter_model)

    def _has_fallback(self) -> bool:
        return bool(self.fallback_url and self.fallback_api_key)

    def _polish_with_openrouter(
        self,
        *,
        source_texts: list[str],
        translated_texts: list[str],
        src_lang: str,
        target_lang: str,
        timeout: int,
        max_retries: int,
    ) -> list[str]:
        system_prompt = (
            f"You refine subtitle translations from {src_lang} into {target_lang} for short-form videos. "
            "Output must be plain text only. "
            "Return only numbered lines in the form '1. ...'. "
            "Keep the exact same number of items. "
            "Do not merge items. Do not split items. "
            "Do not add commentary, labels, explanations, markdown, or code blocks. "
            "Make each line sound natural, punchy, concise, and easy to read quickly on screen. "
            "Preserve the original meaning, tone, and intent without adding new information."
        )
        user_prompt = self._build_prompt(
            source_texts=source_texts,
            translated_texts=translated_texts,
            src_lang=src_lang,
            target_lang=target_lang,
        )
        payload = {
            "model": self.openrouter_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        if self.openrouter_site_url:
            headers["HTTP-Referer"] = self.openrouter_site_url
        if self.openrouter_app_name:
            headers["X-Title"] = self.openrouter_app_name

        return self._run_request(
            url=self.openrouter_url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            max_retries=max_retries,
            expected_items=len(translated_texts),
            label="OpenRouter polish",
        )

    def _polish_with_fallback(
        self,
        *,
        source_texts: list[str],
        translated_texts: list[str],
        src_lang: str,
        target_lang: str,
        timeout: int,
        max_retries: int,
    ) -> list[str]:
        prompt = self._build_prompt(
            source_texts=source_texts,
            translated_texts=translated_texts,
            src_lang=src_lang,
            target_lang=target_lang,
        )
        payload = {"text": prompt}
        headers = {
            "x-api-key": self.fallback_api_key,
            "Content-Type": "application/json",
        }
        return self._run_request(
            url=self.fallback_url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            max_retries=max_retries,
            expected_items=len(translated_texts),
            label="Fallback polish",
        )

    def _run_request(
        self,
        *,
        url: str,
        headers: dict,
        payload: dict,
        timeout: int,
        max_retries: int,
        expected_items: int,
        label: str,
    ) -> list[str]:
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                if response.status_code != 200:
                    last_error = f"{label} error ({response.status_code}): {response.text}"
                    if attempt < max_retries:
                        time.sleep(attempt)
                        continue
                    raise TranslationProviderError(last_error)

                polished = self._extract_text(response.json())
                lines = parse_numbered_lines(polished)
                if not validate_texts(lines, expected_items):
                    raise TranslationValidationError(
                        f"{label} returned {len(lines)} items for {expected_items} translated segments."
                    )
                return lines
            except requests.exceptions.Timeout as e:
                last_error = f"{label} timed out: {e}"
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue
            except requests.RequestException as e:
                last_error = f"{label} request failed: {e}"
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue

        raise TranslationProviderError(last_error or f"{label} failed.")

    def _build_prompt(self, *, source_texts: list[str], translated_texts: list[str], src_lang: str, target_lang: str) -> str:
        lines = []
        for idx, (source, translated) in enumerate(zip(source_texts, translated_texts), 1):
            lines.append(f"{idx}. SOURCE: {source}")
            lines.append(f"{idx}. DRAFT: {translated}")
        body = "\n".join(lines)
        return (
            f"Polish the following subtitle translations from {src_lang} into {target_lang} for short-form video subtitles.\n"
            "Rules:\n"
            "- Keep the exact same number of items.\n"
            "- Do not merge items or split items.\n"
            "- Each numbered item must contain exactly one polished subtitle line.\n"
            "- Do not add commentary, notes, labels, markdown, quotes, or code fences.\n"
            "- Preserve meaning, tone, and intent.\n"
            "- Make each line natural, concise, punchy, and easy to read quickly on screen.\n"
            "- Prefer conversational Vietnamese that fits short videos.\n"
            "- Keep wording tight. Remove unnecessary filler.\n"
            "- Do not invent details or exaggerate emotion.\n"
            "- Return only numbered lines in the form '1. ...'.\n\n"
            f"{body}"
        )

    def _extract_text(self, result) -> str:
        if isinstance(result, dict):
            choices = result.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message", {})
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(str(item.get("text", "")))
                    merged = "".join(text_parts).strip()
                    if merged:
                        return merged
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
