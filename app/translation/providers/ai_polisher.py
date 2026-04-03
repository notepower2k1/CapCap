import os
import time
import requests

from ..errors import TranslationConfigError, TranslationProviderError, TranslationValidationError
from ..srt_utils import parse_numbered_lines, validate_texts

class AIPolisherProvider:
    def __init__(self):
        self.openrouter_url = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions").strip()
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", "stepfun/step-3.5-flash:free").strip()
        self.openrouter_site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
        self.openrouter_app_name = os.getenv("OPENROUTER_APP_NAME", "CapCap").strip()

        self.fallback_url = os.getenv("AI_POLISHER_URL", os.getenv("TRANSLATOR_URL", "")).strip()
        self.fallback_api_key = os.getenv("AI_POLISHER_API_KEY", os.getenv("TRANSLATOR_API_KEY", "")).strip()

    def is_configured(self) -> bool:
        return self._has_openrouter() or self._has_fallback()

    def polish_batch(self, *, source_texts: list[str], translated_texts: list[str] = None, src_lang: str, target_lang: str, style_instruction: str = "", timeout: int = 60, max_retries: int = 2) -> tuple[list[str], list[str], str]:
        if not self.is_configured():
            raise TranslationConfigError("AI polisher is not configured. Set OPENROUTER_API_KEY or AI_POLISHER_URL in .env.")

        local_warnings = []
        errors = []

        if self._has_openrouter():
            try:
                lines = self._polish_with_openrouter(source_texts=source_texts, translated_texts=translated_texts, src_lang=src_lang, target_lang=target_lang, style_instruction=style_instruction, timeout=timeout, max_retries=max_retries)
                return lines, local_warnings, "openrouter"
            except Exception as e:
                errors.append(str(e))
                if self._has_fallback():
                    local_warnings.append(f"OpenRouter failed, using fallback: {e}")

        if self._has_fallback():
            try:
                lines = self._polish_with_fallback(source_texts=source_texts, translated_texts=translated_texts, src_lang=src_lang, target_lang=target_lang, style_instruction=style_instruction, timeout=timeout, max_retries=max_retries)
                return lines, local_warnings, "translator-api.thach-nv"
            except Exception as e:
                errors.append(str(e))

        raise TranslationProviderError(" | ".join(errors) or "AI polisher failed.")

    def _has_openrouter(self) -> bool:
        return bool(self.openrouter_url and self.openrouter_api_key and self.openrouter_model)

    def _has_fallback(self) -> bool:
        return bool(self.fallback_url and self.fallback_api_key)

    def _polish_with_openrouter(self, **kwargs) -> list[str]:
        is_direct = not kwargs.get('translated_texts')
        mode_str = "Translate" if is_direct else "Refine"
        system_prompt = f"{mode_str} subtitles from {kwargs['src_lang']} to {kwargs['target_lang']}. Return only numbered lines: '1. Resulting text'. No merge/split."
        user_prompt = self._build_prompt(source_texts=kwargs['source_texts'], translated_texts=kwargs['translated_texts'], src_lang=kwargs['src_lang'], target_lang=kwargs['target_lang'], style_instruction=kwargs['style_instruction'])
        
        payload = {
            "model": self.openrouter_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.openrouter_api_key}", "Content-Type": "application/json"}
        return self._run_request(url=self.openrouter_url, headers=headers, payload=payload, **kwargs)

    def _polish_with_fallback(self, **kwargs) -> list[str]:
        prompt = self._build_prompt(source_texts=kwargs['source_texts'], translated_texts=kwargs['translated_texts'], src_lang=kwargs['src_lang'], target_lang=kwargs['target_lang'], style_instruction=kwargs['style_instruction'])
        payload = {"text": prompt}
        headers = {"x-api-key": self.fallback_api_key, "Content-Type": "application/json"}
        return self._run_request(url=self.fallback_url, headers=headers, payload=payload, **kwargs)

    def _run_request(self, url, headers, payload, **kwargs) -> list[str]:
        last_error = ""
        for attempt in range(1, kwargs.get('max_retries', 2) + 1):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=kwargs.get('timeout', 60))
                if response.status_code != 200:
                    last_error = f"API error ({response.status_code}): {response.text}"
                    if attempt < kwargs.get('max_retries', 2):
                        time.sleep(attempt)
                        continue
                    raise Exception(last_error)
                
                data = response.json()
                text = self._extract_text(data)
                lines = parse_numbered_lines(text)
                if not validate_texts(lines, len(kwargs['source_texts'])):
                    raise Exception(f"Expected {len(kwargs['source_texts'])} lines, got {len(lines)}")
                return lines
            except Exception as e:
                last_error = str(e)
                if attempt < kwargs.get('max_retries', 2):
                    time.sleep(attempt)
                    continue
        raise Exception(last_error)

    def _build_prompt(self, source_texts, translated_texts, src_lang, target_lang, style_instruction) -> str:
        is_direct = not translated_texts
        style_part = f" Style: {style_instruction}" if style_instruction else ""
        if is_direct:
            lines = [f"{i+1}. {s}" for i, s in enumerate(source_texts)]
            header = f"Translate these {src_lang}->{target_lang} subtitles directly.{style_part}\nFormat: Number. Text"
        else:
            lines = [f"{i+1}. {s} ||| {t}" for i, (s, t) in enumerate(zip(source_texts, translated_texts))]
            header = f"Refine these {src_lang}->{target_lang} subtitle translations.{style_part}\nFormat: Number. Source ||| Draft"

        return (f"{header}\nRules: Natural, punchy, concise. One resulting line per number. No commentary.\n\n" + "\n".join(lines))

    def _extract_text(self, result) -> str:
        if isinstance(result, dict):
            choices = result.get("choices")
            if isinstance(choices, list) and choices:
                content = choices[0].get("message", {}).get("content")
                if isinstance(content, str): return content
            if "response" in result: return result["response"]
            if "result" in result and isinstance(result["result"], str): return result["result"]
        return str(result)
