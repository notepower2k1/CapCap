import os
import time

import requests

from ..errors import TranslationConfigError, TranslationProviderError


class MicrosoftTranslatorProvider:
    def __init__(self):
        self.endpoint = os.getenv("MS_TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com").rstrip("/")
        self.api_key = os.getenv("MS_TRANSLATOR_API_KEY", "").strip()
        self.region = os.getenv("MS_TRANSLATOR_REGION", "").strip()
        self.api_version = os.getenv("MS_TRANSLATOR_API_VERSION", "3.0").strip()

    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.region)

    def translate_batch(
        self,
        texts: list[str],
        *,
        src_lang: str,
        target_lang: str,
        timeout: int = 30,
        max_retries: int = 2,
    ) -> list[str]:
        if not self.is_configured():
            raise TranslationConfigError(
                "Microsoft Translator is not configured. Please set MS_TRANSLATOR_ENDPOINT, "
                "MS_TRANSLATOR_API_KEY, and MS_TRANSLATOR_REGION in .env."
            )

        url = f"{self.endpoint}/translate"
        params = {
            "api-version": self.api_version,
            "from": src_lang,
            "to": target_lang,
        }
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Ocp-Apim-Subscription-Region": self.region,
            "Content-Type": "application/json",
        }
        payload = [{"text": text} for text in texts]

        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, params=params, headers=headers, json=payload, timeout=timeout)
                if response.status_code != 200:
                    last_error = f"Microsoft Translator error ({response.status_code}): {response.text}"
                    if attempt < max_retries:
                        time.sleep(attempt)
                        continue
                    raise TranslationProviderError(last_error)

                data = response.json()
                translated = []
                for item in data:
                    translations = item.get("translations") or []
                    if not translations:
                        raise TranslationProviderError("Microsoft Translator returned an empty translations list.")
                    translated.append((translations[0].get("text") or "").strip())
                return translated
            except requests.exceptions.Timeout as e:
                last_error = f"Microsoft Translator timed out: {e}"
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue
            except requests.RequestException as e:
                last_error = f"Microsoft Translator request failed: {e}"
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue

        raise TranslationProviderError(last_error or "Microsoft Translator failed.")
