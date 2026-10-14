import os
import threading
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from openai import OpenAI

from ..errors import TranslationConfigError, TranslationProviderError, TranslationValidationError
from ..prompt_builder import build_translation_messages
from ..srt_utils import parse_numbered_line_items, validate_texts

REQUEST_LOG_INTERVAL = 20.0


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
        self._logged_reasoning_effort = False

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model_name and self.base_url)

    def _get_client(self):
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            # Observe the SDK's own client without changing retries, timeouts or cleanup.
            self._client._client.event_hooks["request"].append(self._log_http_request)
            self._client._client.event_hooks["response"].append(self._log_http_response)
        return self._client

    def _safe_error(self, exc):
        message = str(exc)
        return message.replace(self.api_key, "[redacted]") if self.api_key else message

    def _log_request(self, message, thread_id=None):
        trace = f"{os.getpid()}:{thread_id or threading.get_ident()}"
        print(f"[AI Request] {time.strftime('%H:%M:%S')} trace={trace} provider={self.provider_id} {message}\n",
              end="", flush=True)

    def _log_http_request(self, request):
        retry_count = request.headers.get("x-stainless-retry-count", "0")
        self._log_request(f"HTTP SEND sdk_retry={retry_count}")

    def _log_http_response(self, response):
        self._log_request(
            f"HTTP RESPONSE status={response.status_code} "
            f"request_id={response.headers.get('x-request-id') or response.headers.get('x-goog-request-id') or '-'} "
            f"retry_after={response.headers.get('retry-after') or '-'}"
        )

    def _request_completion(self, client, kwargs, *, operation, attempt, max_retries):
        started = time.monotonic()
        thread_id = threading.get_ident()
        prompt_chars = sum(len(message.get("content") or "") for message in kwargs["messages"])
        self._log_request(
            f"START operation={operation} attempt={attempt}/{max_retries} model={kwargs['model']} "
            f"max_tokens={kwargs['max_tokens']} timeout={kwargs['timeout']}s "
            f"reasoning_effort={kwargs.get('reasoning_effort', '-')} "
            f"num_ctx={kwargs.get('extra_body', {}).get('options', {}).get('num_ctx', '-')} "
            f"prompt_chars={prompt_chars} sdk_max_retries={getattr(client, 'max_retries', None)}"
        )
        stopped = threading.Event()

        def log_wait():
            while not stopped.wait(REQUEST_LOG_INTERVAL):
                self._log_request(
                    f"WAIT operation={operation} attempt={attempt}/{max_retries} "
                    f"elapsed={time.monotonic() - started:.1f}s (waiting for API/SDK retry)", thread_id,
                )

        heartbeat = threading.Thread(target=log_wait, name="translation-request-log", daemon=True)
        heartbeat.start()
        try:
            response = client.chat.completions.create(**kwargs)
            stopped.set()
            choice = response.choices[0] if response.choices else None
            usage = getattr(response, "usage", None)
            details = getattr(usage, "completion_tokens_details", None)
            self._log_request(
                f"DONE operation={operation} elapsed={time.monotonic() - started:.2f}s "
                f"model={getattr(response, 'model', '-')} finish_reason={getattr(choice, 'finish_reason', '-')} "
                f"response_chars={len(choice.message.content or '') if choice else 0} "
                f"prompt_tokens={getattr(usage, 'prompt_tokens', None)} "
                f"completion_tokens={getattr(usage, 'completion_tokens', None)} "
                f"total_tokens={getattr(usage, 'total_tokens', None)} "
                f"reasoning_tokens={getattr(details, 'reasoning_tokens', None)} "
                f"request_id={getattr(response, '_request_id', None)}"
            )
            return response
        except Exception as exc:
            self._log_request(
                f"ERROR operation={operation} elapsed={time.monotonic() - started:.2f}s "
                f"error_type={type(exc).__name__} http_status={getattr(exc, 'status_code', None)} "
                f"request_id={getattr(exc, 'request_id', None)} error={self._safe_error(exc)!r}"
            )
            raise
        finally:
            stopped.set()
            heartbeat.join()

    def _create_completion(self, client, kwargs, *, operation, attempt, max_retries):
        try:
            return self._request_completion(client, kwargs, operation=operation,
                                            attempt=attempt, max_retries=max_retries)
        except Exception as api_err:
            if "reasoning_effort" in kwargs and "reasoning_effort" in str(api_err):
                dropped = "reasoning_effort"
            elif "extra_body" in kwargs and any(term in str(api_err).lower() for term in ("extra_body", "options", "unexpected")):
                dropped = "extra_body"
            else:
                raise
            kwargs.pop(dropped, None)
            self._log_request(f"RETRY operation={operation} without unsupported parameter {dropped}")
            return self._request_completion(client, kwargs, operation=operation,
                                            attempt=attempt, max_retries=max_retries)

    def _build_completion_kwargs(
        self, system_msg: str, user_msg: str, max_tokens: int, timeout: int
    ) -> dict:
        is_gemini = "generativelanguage.googleapis.com" in self.base_url or "gemini" in self.model_name.lower()
        is_ollama = "localhost:11434" in self.base_url or "ollama" in self.provider_id.lower() or "ollama" in self.base_url.lower()
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

        if is_ollama:
            try:
                num_ctx = int(os.getenv("CAPCAP_OLLAMA_NUM_CTX") or "16384")
            except (ValueError, TypeError):
                num_ctx = 16384
            kwargs["extra_body"] = {
                "options": {
                    "num_ctx": num_ctx,
                }
            }

        if is_gemini:
            # Gemini 2.5/3.x models:
            # 1. Custom temperature (< 1.0) is not supported for reasoning models and returns 400.
            # 2. Configurable reasoning_effort via CAPCAP_GEMINI_REASONING_EFFORT ("low", "medium", "high"), defaulting to "medium".
            reasoning_effort = os.getenv("CAPCAP_GEMINI_REASONING_EFFORT", "medium").strip().lower()
            if reasoning_effort not in ("low", "medium", "high"):
                reasoning_effort = "medium"
            if reasoning_effort != "medium" and not getattr(self, "_logged_reasoning_effort", False):
                print(f"[{self.display_name}] Effective reasoning_effort: {reasoning_effort}")
                self._logged_reasoning_effort = True
            kwargs["reasoning_effort"] = reasoning_effort
        elif is_reasoning_model:
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["temperature"] = 0.2

        return kwargs

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "429" in msg
            or "resource_exhausted" in msg
            or "rate limit" in msg
            or "ratelimit" in msg
            or "quota" in msg
            or "too many requests" in msg
        )

    def generate_text(
        self,
        *,
        system_msg: str,
        user_msg: str,
        max_tokens: int = 1024,
        timeout: int = 60,
        max_retries: int = 4,
    ) -> str:
        if not self.is_configured():
            raise TranslationConfigError(f"{self.display_name} is not configured. Set its API key and model in Settings.")
        client = self._get_client()
        last_error = ""
        last_exception = None
        for attempt in range(1, max_retries + 1):
            kwargs = self._build_completion_kwargs(
                system_msg=system_msg,
                user_msg=user_msg,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            try:
                response = self._create_completion(client, kwargs, operation="context",
                                                   attempt=attempt, max_retries=max_retries)
                return (response.choices[0].message.content or "").strip()
            except Exception as e:
                last_exception = e
                last_error = self._safe_error(e)
                if attempt < max_retries:
                    if self._is_rate_limit_error(e):
                        sleep_s = min(60.0, 5.0 * (2.2 ** (attempt - 1)))
                        print(
                            f"[{self.display_name}] Rate limit (429/Quota) on generate_text attempt {attempt}/{max_retries}. "
                            f"Backing off for {sleep_s:.1f}s...", flush=True,
                        )
                    else:
                        sleep_s = min(15.0, 2.0 * attempt)
                        print(
                            f"[{self.display_name}] Error on generate_text attempt {attempt}/{max_retries}: {last_error}. "
                            f"Retrying in {sleep_s:.1f}s...", flush=True,
                        )
                    time.sleep(sleep_s)
                    continue

        raise TranslationProviderError(f"{self.display_name} generate_text failed: {last_error}") from last_exception

    def polish_batch(
        self,
        *,
        source_texts: list[str],
        translated_texts: list[str] = None,
        source_speakers: list[str] = None,
        src_lang: str,
        target_lang: str,
        style_instruction: str = "",
        custom_system_prompt: str = "",
        context_guidance: str = "",
        timeout: int = 120,
        max_retries: int = 4,
        max_tokens: int = 4096,
        is_canceled=None,
    ) -> tuple[list[str], list[str], str]:
        if not self.is_configured():
            raise TranslationConfigError(f"{self.display_name} is not configured. Set its API key and model in Settings.")

        system_msg, user_msg = self._build_messages(
            source_texts=source_texts,
            translated_texts=translated_texts,
            source_speakers=source_speakers,
            src_lang=src_lang,
            target_lang=target_lang,
            style_instruction=style_instruction,
            custom_system_prompt=custom_system_prompt,
            context_guidance=context_guidance,
        )

        client = self._get_client()
        last_error = ""
        last_exception = None
        for attempt in range(1, max_retries + 1):
            if is_canceled and is_canceled():
                raise InterruptedError("Translation canceled by user.")
            try:
                kwargs = self._build_completion_kwargs(
                    system_msg=system_msg,
                    user_msg=user_msg,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                response = self._create_completion(client, kwargs, operation="batch",
                                                   attempt=attempt, max_retries=max_retries)
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    raise Exception("Empty response text")

                numbered_items = parse_numbered_line_items(text)
                expected = len(source_texts)
                expected_ids = list(range(1, expected + 1))
                actual_ids = [number for number, _line in numbered_items]
                self._log_request(f"VALIDATION expected_cues={expected} returned_cues={len(numbered_items)}")
                if actual_ids != expected_ids:
                    self._log_request(
                        f"INVALID missing_ids={sorted(set(expected_ids) - set(actual_ids))[:12]} "
                        f"unexpected_ids={sorted(set(actual_ids) - set(expected_ids))[:12]} "
                        f"response_head={self._safe_error(text[:200])!r} response_tail={self._safe_error(text[-200:])!r}"
                    )
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
                if is_canceled and is_canceled():
                    raise InterruptedError("Translation canceled by user.")
                last_exception = e
                last_error = self._safe_error(e)
                if attempt < max_retries:
                    if self._is_rate_limit_error(e):
                        sleep_s = min(60.0, 5.0 * (2.2 ** (attempt - 1)))
                        print(
                            f"[{self.display_name}] Rate limit (429/Quota) on batch attempt {attempt}/{max_retries}. "
                            f"Backing off for {sleep_s:.1f}s...", flush=True,
                        )
                    else:
                        sleep_s = min(15.0, 2.0 * attempt)
                        print(
                            f"[{self.display_name}] Error on batch attempt {attempt}/{max_retries}: {last_error}. "
                            f"Retrying in {sleep_s:.1f}s...", flush=True,
                        )
                    # Sliced sleep to respond immediately (<0.2s) if user cancels
                    end_time = time.monotonic() + sleep_s
                    while time.monotonic() < end_time:
                        if is_canceled and is_canceled():
                            raise InterruptedError("Translation canceled by user.")
                        time.sleep(min(0.2, max(0.0, end_time - time.monotonic())))
                    continue

        raise TranslationProviderError(f"{self.display_name} failed: {last_error}") from last_exception

    def _build_messages(
        self,
        source_texts,
        translated_texts,
        src_lang,
        target_lang,
        style_instruction,
        custom_system_prompt: str = "",
        context_guidance: str = "",
        source_speakers: list[str] = None,
    ) -> tuple[str, str]:
        return build_translation_messages(
            source_texts=source_texts,
            translated_texts=translated_texts,
            source_speakers=source_speakers,
            src_lang=src_lang,
            target_lang=target_lang,
            style_instruction=style_instruction,
            custom_system_prompt=custom_system_prompt,
            context_guidance=context_guidance,
        )
