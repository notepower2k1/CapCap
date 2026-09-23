import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import quote

import requests

from ..errors import TranslationProviderError


class _MobileResultParser(HTMLParser):
    """Extract the translated text from Google's mobile Translate page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._result_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if self._result_depth:
            self._result_depth += 1
            return
        if tag.lower() == "div":
            classes = dict(attrs).get("class", "")
            if "result-container" in str(classes).split():
                self._result_depth = 1

    def handle_endtag(self, tag):
        if self._result_depth:
            self._result_depth -= 1

    def handle_data(self, data):
        if self._result_depth and data:
            self.parts.append(data)


class GoogleWebTranslatorProvider:
    BASE_URL = "https://translate.googleapis.com/translate_a/single"
    # The undocumented JSON endpoint is frequently rate-limited.  Google
    # still exposes the same free translation through this mobile page.
    MOBILE_URL = "https://translate.google.com/m"
    MOBILE_MAX_CHARS = 2048
    DELIMITER = "\n===@@@===\n"
    CHUNK_MAX_CUES = 15
    CHUNK_MAX_CHARS = 1800

    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def is_configured(self) -> bool:
        return True

    def _chunk_texts(self, texts: list[str]) -> list[list[str]]:
        chunks: list[list[str]] = []
        current: list[str] = []
        current_len = 0

        for text in texts:
            text_len = len(text or "")
            if current and (
                len(current) >= self.CHUNK_MAX_CUES
                or (current_len + text_len) > self.CHUNK_MAX_CHARS
            ):
                chunks.append(current)
                current = []
                current_len = 0
            current.append(text)
            current_len += text_len

        if current:
            chunks.append(current)

        return chunks

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

        results: list[str] = []
        chunks = self._chunk_texts(texts)

        for chunk_idx, chunk in enumerate(chunks):
            if chunk_idx > 0:
                time.sleep(0.25)

            if len(chunk) == 1:
                translated = self._translate_text(
                    text=chunk[0],
                    src_lang=src_lang,
                    target_lang=target_lang,
                    timeout=timeout,
                    max_retries=max_retries,
                )
                results.append(translated)
                continue

            # Batch translation using delimiter
            joined = self.DELIMITER.join(chunk)
            batch_success = False
            try:
                translated_joined = self._translate_text(
                    text=joined,
                    src_lang=src_lang,
                    target_lang=target_lang,
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
                # Fallback to translating individual cues for this chunk
                for item in chunk:
                    time.sleep(0.1)
                    res = self._translate_text(
                        text=item,
                        src_lang=src_lang,
                        target_lang=target_lang,
                        timeout=timeout,
                        max_retries=max_retries,
                    )
                    results.append(res)

        return results

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
        headers = self.headers

        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.get(url, headers=headers, timeout=timeout)
                if response.status_code != 200:
                    last_error = f"Google web translate error ({response.status_code}): {response.text}"
                    # The free JSON endpoint is undocumented and can be
                    # blocked per IP.  Try the public mobile Translate page
                    # before retrying the same blocked endpoint.
                    if response.status_code in {403, 429}:
                        try:
                            mobile_text = self._translate_mobile(
                                text=text,
                                src_lang=src_lang,
                                target_lang=target_lang,
                                timeout=timeout,
                                headers=headers,
                            )
                            if mobile_text:
                                return mobile_text
                        except Exception as mobile_exc:
                            last_error = f"{last_error}; mobile fallback failed: {mobile_exc}"
                    if attempt < max_retries:
                        retry_after = response.headers.get("Retry-After", "")
                        try:
                            delay = max(1.0, min(float(retry_after), 10.0))
                        except (TypeError, ValueError):
                            delay = min(float(attempt), 5.0)
                        time.sleep(delay)
                        continue
                    raise TranslationProviderError(last_error)

                try:
                    payload = response.json()
                    translated = self._extract_text(payload)
                except ValueError as exc:
                    translated = ""
                    last_error = f"Google web translate returned invalid JSON: {exc}"
                if translated:
                    return translated

                # A future endpoint change may return HTTP 200 with a
                # different payload shape.  Treat that the same as a blocked
                # endpoint and try the mobile response before retrying.
                try:
                    mobile_text = self._translate_mobile(
                        text=text,
                        src_lang=src_lang,
                        target_lang=target_lang,
                        timeout=timeout,
                        headers=headers,
                    )
                    if mobile_text:
                        return mobile_text
                except Exception as mobile_exc:
                    last_error = (
                        last_error or "Google web translate returned empty text."
                    ) + f"; mobile fallback failed: {mobile_exc}"
                raise TranslationProviderError(
                    last_error or "Google web translate returned empty text."
                )
            except (requests.RequestException, json.JSONDecodeError, TranslationProviderError) as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue

        raise TranslationProviderError(last_error or "Google web translate failed.")

    def _translate_mobile(
        self,
        *,
        text: str,
        src_lang: str,
        target_lang: str,
        timeout: int,
        headers: dict,
    ) -> str:
        """Translate one cue through Google's mobile web surface."""
        if len(text or "") > self.MOBILE_MAX_CHARS:
            raise TranslationProviderError(
                f"Google mobile fallback supports at most {self.MOBILE_MAX_CHARS} characters per subtitle."
            )
        mobile_headers = dict(headers or {})
        mobile_headers["Accept"] = "text/html,application/xhtml+xml"
        response = self.session.get(
            self.MOBILE_URL,
            params={"sl": src_lang, "tl": target_lang, "q": text or ""},
            headers=mobile_headers,
            timeout=timeout,
        )
        if response.status_code != 200:
            raise TranslationProviderError(
                f"Google mobile translate error ({response.status_code}): {response.text[:300]}"
            )
        parser = _MobileResultParser()
        parser.feed(response.text or "")
        translated = "".join(parser.parts).strip()
        if not translated:
            raise TranslationProviderError("Google mobile translate returned empty text.")
        return translated

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
