import re
import threading
import time
from urllib.parse import quote

import requests

from ..errors import TranslationProviderError


class BingWebTranslatorProvider:
    """Free Bing Translator web provider with session caching, delimiter batching, and fallback."""

    BASE_URL = "https://www.bing.com/translator"
    DELIMITER = "\n===@@@===\n"
    CHUNK_MAX_CUES = 12
    CHUNK_MAX_CHARS = 1000

    LANG_MAP = {
        "zh": "zh-Hans",
        "zh-cn": "zh-Hans",
        "zh-hans": "zh-Hans",
        "zh-tw": "zh-Hant",
        "zh-hant": "zh-Hant",
        "auto": "auto-detect",
    }

    _cache_lock = threading.Lock()
    _sid_cache = None
    _sid_timestamp = 0
    _sid_cache_ttl = 300  # 5 minutes cache TTL

    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

    def is_configured(self) -> bool:
        return True

    def _normalize_lang(self, lang: str) -> str:
        code = str(lang or "").strip().lower()
        return self.LANG_MAP.get(code, lang)

    def _fetch_session_params(self, timeout: int = 15) -> tuple[str, str, str, str, str]:
        """Fetch and cache session tokens (url, ig, iid, key, token) needed for ttranslatev3."""
        current_time = time.time()
        with self._cache_lock:
            if (
                self._sid_cache is not None
                and (current_time - self._sid_timestamp) < self._sid_cache_ttl
            ):
                return self._sid_cache

        try:
            response = self.session.get(self.BASE_URL, headers=self.headers, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TranslationProviderError(f"Bing translator session initialization failed: {exc}") from exc

        url = response.url.split("?")[0]
        if not url.endswith("/"):
            url = url.rsplit("/", 1)[0] + "/"

        try:
            ig_matches = re.findall(r'\"ig\":\"(.*?)\"', response.text)
            if not ig_matches:
                ig_matches = re.findall(r'IG:\"([A-Za-z0-9]+)\"', response.text)
            ig = ig_matches[0]

            iid_matches = re.findall(r'data-iid=\"(.*?)\"', response.text)
            iid = iid_matches[-1] if iid_matches else "translator.5028"

            abuse_matches = re.findall(
                r'params_AbusePreventionHelper\s*=\s*\[(.*?),\"(.*?)\",',
                response.text,
            )
            if not abuse_matches:
                raise TranslationProviderError("Bing translator abuse token not found in page.")
            key, token = abuse_matches[0]
        except (IndexError, TranslationProviderError) as exc:
            raise TranslationProviderError(f"Failed to parse Bing translator tokens: {exc}") from exc

        result = (url, ig, iid, key, token)
        with self._cache_lock:
            self._sid_cache = result
            self._sid_timestamp = current_time

        return result

    def translate_batch(
        self,
        texts: list[str],
        *,
        src_lang: str,
        target_lang: str,
        timeout: int = 20,
        max_retries: int = 2,
    ) -> list[str]:
        if not texts:
            return []

        src = self._normalize_lang(src_lang)
        target = self._normalize_lang(target_lang)

        results: list[str] = []
        chunks = self._chunk_texts(texts)

        for chunk_idx, chunk in enumerate(chunks):
            if chunk_idx > 0:
                time.sleep(0.25)

            if len(chunk) == 1:
                translated = self._translate_single(
                    chunk[0],
                    src_lang=src,
                    target_lang=target,
                    timeout=timeout,
                    max_retries=max_retries,
                )
                results.append(translated)
                continue

            # Try batch translation using delimiter
            joined = self.DELIMITER.join(chunk)
            batch_success = False
            try:
                translated_joined = self._translate_single(
                    joined,
                    src_lang=src,
                    target_lang=target,
                    timeout=timeout,
                    max_retries=max_retries,
                )
                parts = [p.strip() for p in translated_joined.split(self.DELIMITER.strip())]
                if len(parts) == len(chunk):
                    results.extend(parts)
                    batch_success = True
            except Exception:
                batch_success = False

            if not batch_success:
                # Fallback to single translations for this chunk
                for item in chunk:
                    time.sleep(0.1)
                    res = self._translate_single(
                        item,
                        src_lang=src,
                        target_lang=target,
                        timeout=timeout,
                        max_retries=max_retries,
                    )
                    results.append(res)

        return results

    def _chunk_texts(self, texts: list[str]) -> list[list[str]]:
        chunks: list[list[str]] = []
        current: list[str] = []
        current_len = 0

        for text in texts:
            text_len = len(text or "")
            if current and (len(current) >= self.CHUNK_MAX_CUES or (current_len + text_len) > self.CHUNK_MAX_CHARS):
                chunks.append(current)
                current = []
                current_len = 0
            current.append(text)
            current_len += text_len

        if current:
            chunks.append(current)

        return chunks

    def _translate_single(
        self,
        text: str,
        *,
        src_lang: str,
        target_lang: str,
        timeout: int,
        max_retries: int,
    ) -> str:
        if not text or not text.strip():
            return ""

        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                url, ig, iid, key, token = self._fetch_session_params(timeout=timeout)
                endpoint = f"{url}ttranslatev3?isVertical=1&IG={ig}&IID={iid}"
                post_headers = dict(self.headers)
                post_headers.update({
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://www.bing.com",
                    "Referer": self.BASE_URL,
                })
                data = {
                    "fromLang": src_lang,
                    "to": target_lang,
                    "text": text,
                    "token": token,
                    "key": key,
                }
                response = self.session.post(endpoint, data=data, headers=post_headers, timeout=timeout)
                if response.status_code != 200:
                    # Invalidate session cache if 401 or 403
                    if response.status_code in {401, 403}:
                        with self._cache_lock:
                            self._sid_cache = None
                    last_error = f"Bing translate error ({response.status_code}): {response.text[:200]}"
                    if attempt < max_retries:
                        time.sleep(attempt * 1.5)
                        continue
                    raise TranslationProviderError(last_error)

                data_json = response.json()
                if isinstance(data_json, list) and data_json:
                    translations = data_json[0].get("translations", [])
                    if translations and isinstance(translations, list):
                        trans_text = translations[0].get("text", "")
                        if trans_text is not None:
                            return trans_text.strip()

                raise TranslationProviderError("Bing translate returned unexpected response format.")
            except (requests.RequestException, ValueError, KeyError, TranslationProviderError) as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    time.sleep(attempt * 1.5)
                    continue

        raise TranslationProviderError(last_error or "Bing translate failed.")
