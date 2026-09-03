import os
import time

from openai import OpenAI

from ..errors import TranslationConfigError, TranslationProviderError, TranslationValidationError
from ..prompt_builder import build_translation_messages
from ..srt_utils import parse_numbered_line_items, validate_texts


class OpenAICompatiblePolisherProvider:
    """Reusable numbered-subtitle provider for OpenAI-compatible services."""

    def __init__(self, *, provider_id: str, display_name: str, env_prefix: str,
                 default_base_url: str, default_model: str = ""):
        self.provider_id = provider_id
        self.display_name = display_name
        self.api_key = os.getenv(f"{env_prefix}_API_KEY", "").strip()
        self.model_name = os.getenv(f"{env_prefix}_MODEL", default_model).strip()
        self.base_url = os.getenv(f"{env_prefix}_BASE_URL", default_base_url).strip()
        self._client = None

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model_name and self.base_url)

    def _get_client(self):
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def _build_completion_kwargs(
        self, system_msg: str, user_msg: str, max_tokens: int, timeout: int
    ) -> dict:
        is_gemini = "generativelanguage.googleapis.com" in self.base_url or "gemini" in self.model_name.lower()
        is_reasoning_model = is_gemini or any(
            self.model_name.lower().startswith(p) for p in ("o1", "o3", "deepseek-r1")
        )

        kwargs = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": max(2048 if is_gemini else 1024, int(max_tokens or 4096)),
            "timeout": timeout,
        }

        if is_gemini:
            # Gemini 2.5/3.x models:
            # 1. Custom temperature (< 1.0) is not supported for reasoning models and returns 400.
            # 2. Set reasoning_effort to "low" to prevent reasoning tokens from causing timeouts on subtitle translation.
            kwargs["reasoning_effort"] = "low"
        elif is_reasoning_model:
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["temperature"] = 0.2

        return kwargs

    def polish_batch(
        self,
        *,
        source_texts: list[str],
        translated_texts: list[str] = None,
        src_lang: str,
        target_lang: str,
        style_instruction: str = "",
        timeout: int = 120,
        max_retries: int = 2,
        max_tokens: int = 4096,
    ) -> tuple[list[str], list[str], str]:
        if not self.is_configured():
            raise TranslationConfigError(f"{self.display_name} is not configured. Set its API key and model in Settings.")

        system_msg, user_msg = self._build_messages(
            source_texts=source_texts,
            translated_texts=translated_texts,
            src_lang=src_lang,
            target_lang=target_lang,
            style_instruction=style_instruction,
        )

        client = self._get_client()
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                kwargs = self._build_completion_kwargs(
                    system_msg=system_msg,
                    user_msg=user_msg,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                try:
                    response = client.chat.completions.create(**kwargs)
                except Exception as api_err:
                    if "reasoning_effort" in kwargs and "reasoning_effort" in str(api_err):
                        kwargs.pop("reasoning_effort", None)
                        response = client.chat.completions.create(**kwargs)
                    else:
                        raise
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    raise Exception("Empty response text")

                numbered_items = parse_numbered_line_items(text)
                expected = len(source_texts)
                expected_ids = list(range(1, expected + 1))
                actual_ids = [number for number, _line in numbered_items]
                if actual_ids != expected_ids:
                    raise TranslationValidationError(
                        f"Malformed or incomplete numbered output: expected IDs 1..{expected}, got {actual_ids[:8]}..."
                    )
                lines = [line for _number, line in numbered_items]
                if not validate_texts(lines, expected):
                    raise TranslationValidationError(
                        f"Expected {expected} lines, got {len(lines)}"
                    )
                return lines, [], self.provider_id
            except TranslationValidationError:
                # Retrying the exact same oversized request cannot restore a
                # truncated numbered response.  The orchestrator can instead
                # recover by switching immediately to ordered batches.
                raise
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue

        raise TranslationProviderError(f"{self.display_name} failed: {last_error}")

    def _build_messages(
        self, source_texts, translated_texts, src_lang, target_lang, style_instruction
    ) -> tuple[str, str]:
        return build_translation_messages(
            source_texts=source_texts,
            translated_texts=translated_texts,
            src_lang=src_lang,
            target_lang=target_lang,
            style_instruction=style_instruction,
        )
