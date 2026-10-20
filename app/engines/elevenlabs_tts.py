"""app/engines/elevenlabs_tts.py

ElevenLabs TTS engine module for CapCap video localization app.
Provides voice catalog fetching with local disk caching and
high quality speech synthesis converting to 16kHz mono WAV.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from typing import Any, Callable

import requests

try:
    import runtime_paths
    from runtime_paths import temp_path
except ImportError:
    from app import runtime_paths
    from app.runtime_paths import temp_path

try:
    import tts_processor
    from tts_processor import convert_audio_data_to_wav_16k_mono
except ImportError:
    from app import tts_processor
    from app.tts_processor import convert_audio_data_to_wav_16k_mono

__all__ = [
    "DEFAULT_ELEVENLABS_MODELS",
    "fetch_elevenlabs_models",
    "fetch_elevenlabs_voices",
    "fetch_elevenlabs_voice_catalog",
    "read_elevenlabs_voice_cache",
    "write_elevenlabs_voice_cache",
    "fetch_elevenlabs_user_subscription",
    "synthesize_elevenlabs_tts_wav_16k_mono",
]

DEFAULT_ELEVENLABS_MODELS = [
    {"model_id": "eleven_v4", "name": "eleven_v4 (Eleven v4 Flagship - 90+ Languages, Hyper-realistic)"},
    {"model_id": "eleven_v4_turbo", "name": "eleven_v4_turbo (Eleven v4 Turbo - High-speed, Low-latency)"},
    {"model_id": "eleven_turbo_v2_5", "name": "eleven_turbo_v2_5 (Turbo v2.5 - Recommended Stable)"},
    {"model_id": "eleven_flash_v2_5", "name": "eleven_flash_v2_5 (Flash v2.5 - Ultra-fast & Economical)"},
    {"model_id": "eleven_v3", "name": "eleven_v3 (Eleven v3 - Expressive Audio Tags)"},
]

CACHE_FILE_NAME = "elevenlabs_voices_cache.json"
MAX_CACHE_AGE_SECONDS = 3600  # 1 hour


class ElevenLabsVoiceCatalogFetchError(RuntimeError):
    """Raised when ElevenLabs does not return a usable voice catalog."""

_orig_temp_path = temp_path
_orig_convert = convert_audio_data_to_wav_16k_mono


def _resolve_temp_path(*parts: str) -> str:
    """Helper to resolve temp path supporting module-level or runtime_paths mocks."""
    if temp_path is not _orig_temp_path:
        return temp_path(*parts)
    if hasattr(runtime_paths, "temp_path") and runtime_paths.temp_path is not _orig_temp_path:
        return runtime_paths.temp_path(*parts)
    return temp_path(*parts)


def _convert_audio_wav(source: bytes, wav_path: str, tmp_dir: str | None = None) -> str:
    """Helper to convert audio bytes to 16kHz WAV supporting module-level or tts_processor mocks."""
    if convert_audio_data_to_wav_16k_mono is not _orig_convert:
        return convert_audio_data_to_wav_16k_mono(source, wav_path, tmp_dir=tmp_dir)
    if hasattr(tts_processor, "convert_audio_data_to_wav_16k_mono") and tts_processor.convert_audio_data_to_wav_16k_mono is not _orig_convert:
        return tts_processor.convert_audio_data_to_wav_16k_mono(source, wav_path, tmp_dir=tmp_dir)
    return convert_audio_data_to_wav_16k_mono(source, wav_path, tmp_dir=tmp_dir)


def _elevenlabs_key_fingerprint(api_key: str) -> str:
    """Return a one-way fingerprint for binding cached account data to its key."""
    clean_key = str(api_key or "").strip()
    return hashlib.sha256(clean_key.encode("utf-8")).hexdigest() if clean_key else ""


def read_elevenlabs_voice_cache(api_key: str) -> dict:
    """Read a fresh voice cache belonging to ``api_key`` without making requests.

    Cache entries from older versions, incomplete payloads, expired entries, and
    entries written for another API key are treated as cache misses.
    """
    expected_fingerprint = _elevenlabs_key_fingerprint(api_key)
    if not expected_fingerprint:
        return {}

    cache_path = _resolve_temp_path(CACHE_FILE_NAME)
    try:
        with open(cache_path, "r", encoding="utf-8") as cache_file:
            cache_data = json.load(cache_file)

        if not isinstance(cache_data, dict):
            return {}
        if cache_data.get("key_fingerprint") != expected_fingerprint:
            return {}

        fetched_at = cache_data.get("fetched_at")
        if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
            return {}
        fetched_at = float(fetched_at)
        if not math.isfinite(fetched_at):
            return {}
        cache_age = time.time() - fetched_at
        if cache_age < 0 or cache_age >= MAX_CACHE_AGE_SECONDS:
            return {}

        voices = cache_data.get("voices")
        subscription = cache_data.get("user_subscription")
        if not isinstance(voices, list) or not all(isinstance(voice, dict) for voice in voices):
            return {}
        if not isinstance(subscription, dict):
            return {}

        return {
            "voices": voices,
            "fetched_at": fetched_at,
            "user_subscription": subscription,
            "key_fingerprint": expected_fingerprint,
        }
    except (OSError, TypeError, ValueError, OverflowError, json.JSONDecodeError):
        return {}


def write_elevenlabs_voice_cache(api_key: str, cache_data: dict) -> bool:
    """Atomically persist a fetched catalog if it belongs to ``api_key``.

    This lets Settings stage a refreshed catalog without changing the active
    account cache until the user saves the new key.
    """
    expected_fingerprint = _elevenlabs_key_fingerprint(api_key)
    if not expected_fingerprint or not isinstance(cache_data, dict):
        return False
    if cache_data.get("key_fingerprint") != expected_fingerprint:
        return False

    fetched_at = cache_data.get("fetched_at")
    voices = cache_data.get("voices")
    subscription = cache_data.get("user_subscription")
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
        return False
    fetched_at = float(fetched_at)
    cache_age = time.time() - fetched_at
    if not math.isfinite(fetched_at) or cache_age < 0 or cache_age >= MAX_CACHE_AGE_SECONDS:
        return False
    if not isinstance(voices, list) or not all(isinstance(voice, dict) for voice in voices):
        return False
    if not isinstance(subscription, dict):
        return False

    cache_path = _resolve_temp_path(CACHE_FILE_NAME)
    temp_cache_path = None
    try:
        cache_dir = os.path.dirname(os.path.abspath(cache_path))
        os.makedirs(cache_dir, exist_ok=True)
        fd, temp_cache_path = tempfile.mkstemp(
            prefix=os.path.basename(cache_path) + ".",
            suffix=".tmp",
            dir=cache_dir,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as cache_file:
            json.dump(
                {
                    "fetched_at": fetched_at,
                    "user_subscription": subscription,
                    "voices": voices,
                    "key_fingerprint": expected_fingerprint,
                },
                cache_file,
                ensure_ascii=False,
                indent=2,
            )
            cache_file.flush()
        os.replace(temp_cache_path, cache_path)
        return True
    except Exception as e:
        if temp_cache_path:
            try:
                os.remove(temp_cache_path)
            except OSError:
                pass
        print(f"[ElevenLabs] Could not write voice cache: {e}")
        return False


def fetch_elevenlabs_user_subscription(api_key: str) -> dict:
    """Fetch user subscription info from ElevenLabs API.
    Tries GET https://api.elevenlabs.io/v1/user/subscription first.
    If response status != 200 or missing tier, falls back to GET https://api.elevenlabs.io/v1/user
    (extracting data.get("subscription")).
    Returns dict with keys: tier, character_count, character_limit, status.
    Returns {} on failure.
    """
    clean_key = str(api_key or "").strip()
    if not clean_key:
        return {}
    headers = {"xi-api-key": clean_key}

    sub_data = None

    # 1. Try dedicated subscription endpoint
    try:
        url_sub = "https://api.elevenlabs.io/v1/user/subscription"
        response = requests.get(url_sub, headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data.get("tier"):
                sub_data = data
    except Exception as e:
        print(f"[ElevenLabs] Lỗi khi lấy thông tin subscription (v1/user/subscription): {e}")

    # 2. Fallback to /v1/user endpoint if not found or failed
    if not sub_data:
        try:
            url_user = "https://api.elevenlabs.io/v1/user"
            response = requests.get(url_user, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    nested_sub = data.get("subscription")
                    if isinstance(nested_sub, dict) and nested_sub.get("tier"):
                        sub_data = nested_sub
        except Exception as e:
            print(f"[ElevenLabs] Lỗi khi lấy thông tin subscription (v1/user fallback): {e}")

    if not sub_data:
        return {}

    return {
        "tier": str(sub_data.get("tier", "") or "").strip().lower(),
        "character_count": int(sub_data.get("character_count", 0) or 0),
        "character_limit": int(sub_data.get("character_limit", 0) or 0),
        "status": str(sub_data.get("status", "") or "").strip().lower(),
    }



def fetch_elevenlabs_models(api_key: str = "") -> list[dict]:
    """Fetch available TTS models from ElevenLabs API.
    Calls GET https://api.elevenlabs.io/v1/models with header xi-api-key if provided.
    Filters out models where can_do_text_to_speech is False.
    Excludes eleven_multilingual_v2 as it lacks Vietnamese support.
    Returns list of dicts [{"model_id": ..., "name": ...}].
    Returns DEFAULT_ELEVENLABS_MODELS on failure or empty response.
    """
    clean_key = str(api_key or "").strip()
    url = "https://api.elevenlabs.io/v1/models"
    headers = {"xi-api-key": clean_key} if clean_key else {}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return [dict(m) for m in DEFAULT_ELEVENLABS_MODELS]
        data = response.json()
        if isinstance(data, list):
            raw_models = data
        elif isinstance(data, dict) and isinstance(data.get("models"), list):
            raw_models = data["models"]
        else:
            return [dict(m) for m in DEFAULT_ELEVENLABS_MODELS]

        default_names = {m["model_id"]: m["name"] for m in DEFAULT_ELEVENLABS_MODELS}
        results = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            if item.get("can_do_text_to_speech") is False:
                continue
            mid = str(item.get("model_id") or "").strip()
            if not mid or mid == "eleven_multilingual_v2":
                continue
            name = default_names.get(mid) or str(item.get("name") or mid).strip()
            results.append({"model_id": mid, "name": name})

        if not results:
            return [dict(m) for m in DEFAULT_ELEVENLABS_MODELS]
        return results
    except Exception as e:
        print(f"[ElevenLabs] Lỗi khi lấy danh sách models: {e}")
        return [dict(m) for m in DEFAULT_ELEVENLABS_MODELS]


def fetch_elevenlabs_voice_catalog(
    api_key: str,
    *,
    force_refresh: bool = False,
    write_cache: bool = True,
) -> dict:
    """Fetch voices and account metadata as a cache-shaped payload.

    By default this uses and refreshes the one-hour local cache. ``write_cache=False``
    lets Settings stage a refreshed catalog without changing the active cache.
    Raises RuntimeError with clear message on auth failure (401) or rate limit (429).
    Raises RuntimeError on other fetch failures as well.
    """
    clean_key = str(api_key or "").strip()
    if not force_refresh:
        cached_payload = read_elevenlabs_voice_cache(clean_key)
        if cached_payload:
            return cached_payload

    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": clean_key}
    print("[ElevenLabs] Đang tải danh sách giọng nói từ API...")

    try:
        response = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise ElevenLabsVoiceCatalogFetchError(f"ElevenLabs voice catalog network error: {e}") from e

    if response.status_code == 401:
        raise RuntimeError("ElevenLabs API Key không hợp lệ hoặc đã hết hạn.")
    if response.status_code == 429:
        raise RuntimeError("ElevenLabs API: Đã đạt giới hạn quota hoặc rate limit. Vui lòng thử lại sau.")
    if response.status_code != 200:
        raise ElevenLabsVoiceCatalogFetchError(
            f"ElevenLabs voice catalog HTTP error {response.status_code}: {response.text}"
        )

    try:
        data = response.json()
    except Exception as e:
        raise ElevenLabsVoiceCatalogFetchError(f"ElevenLabs voice catalog returned invalid JSON: {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("voices"), list):
        raise ElevenLabsVoiceCatalogFetchError("ElevenLabs voice catalog response is missing a voices list.")
    raw_voices = data["voices"]

    sub_info = fetch_elevenlabs_user_subscription(clean_key)
    user_tier = str(sub_info.get("tier", "free") or "free").strip().lower()
    voices: list[dict] = []
    for item in raw_voices:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("voice_id", "") or "").strip()
        if not voice_id:
            continue
        base_name = str(item.get("name", "") or "").strip()
        labels = item.get("labels") or {}
        gender = "any"
        if isinstance(labels, dict):
            raw_gender = str(labels.get("gender") or "").strip().lower()
            if raw_gender in ("male", "female"):
                gender = raw_gender
        preview_url = str(item.get("preview_url") or "").strip()

        category = str(item.get("category") or "").strip().lower()
        sharing = item.get("sharing")
        is_library_voice = category in ("community", "professional", "library") or (
            isinstance(sharing, dict) and bool(sharing.get("public_owner_id"))
        )

        if user_tier in ("free", ""):
            if is_library_voice:
                is_usable_for_api = False
            else:
                is_usable_for_api = True
        else:
            is_usable_for_api = True

        if is_library_voice and not is_usable_for_api:
            display_name = f"{base_name} 🔒 (Paid Plan Required)"
            tier_label = "paid_only"
            voice_type = "community"
        elif is_library_voice:
            display_name = f"{base_name} [Library]"
            tier_label = "premium"
            voice_type = "community"
        elif category == "cloned":
            display_name = f"{base_name} [Clone]"
            tier_label = "cloned"
            voice_type = "cloned"
        elif category == "generated":
            display_name = f"{base_name} [Design]"
            tier_label = "generated"
            voice_type = "generated"
        elif category == "premade":
            display_name = f"{base_name} [Default]"
            tier_label = "free"
            voice_type = "premade"
        else:
            display_name = f"{base_name} [Default]"
            tier_label = "free"
            voice_type = "premade"

        voices.append({
            "id": f"elevenlabs:{voice_id}",
            "raw_name": base_name,
            "name": display_name,
            "provider": "elevenlabs",
            "provider_voice": voice_id,
            "language": "multilingual",
            "gender": gender,
            "tier": tier_label,
            "category": category,
            "voice_type": voice_type,
            "is_usable_for_api": is_usable_for_api,
            "enabled": True,
            "tags": ["elevenlabs", "online"],
            "preview_video_url": "",
            "preview_video_path": "",
            "preview_audio_url": preview_url,
            "preview_audio_path": "",
        })

    payload = {
        "fetched_at": time.time(),
        "user_subscription": sub_info,
        "voices": voices,
        "key_fingerprint": _elevenlabs_key_fingerprint(clean_key),
    }
    if write_cache:
        write_elevenlabs_voice_cache(clean_key, payload)
    return payload


def fetch_elevenlabs_voices(api_key: str, force_refresh: bool = False) -> list[dict]:
    """Fetch voices from ElevenLabs as CapCap catalog-compatible dictionaries."""
    try:
        payload = fetch_elevenlabs_voice_catalog(api_key, force_refresh=force_refresh)
    except ElevenLabsVoiceCatalogFetchError as e:
        print(f"[ElevenLabs] {e}")
        return []
    return payload.get("voices", [])


def synthesize_elevenlabs_tts_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice_id: str,
    api_key: str,
    model_id: str | None = None,
    language_code: str | None = None,
    speed: float = 1.0,
    tmp_dir: str | None = None,
    on_progress: callable = None,
) -> str:
    """Synthesize text to WAV 16kHz mono using ElevenLabs API.
    Uses requests to POST to https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
    with output_format=mp3_44100_128.
    Converts MP3 bytes to 16kHz mono WAV using convert_audio_data_to_wav_16k_mono
    (import from tts_processor).
    Raises RuntimeError with clear message on 401, 402, 429.
    Raises RuntimeError on empty audio response.
    Returns wav_path on success.
    """
    clean_voice_id = str(voice_id or "").strip()
    if clean_voice_id.startswith("elevenlabs:"):
        clean_voice_id = clean_voice_id[len("elevenlabs:"):]

    clamped_speed = max(0.7, min(1.2, float(speed)))

    resolved_model_id = str(model_id or os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5") or "eleven_turbo_v2_5").strip()
    if resolved_model_id == "eleven_multilingual_v2":
        print("[ElevenLabs] Model 'eleven_multilingual_v2' is excluded. Auto-switching to 'eleven_turbo_v2_5'.")
        resolved_model_id = "eleven_turbo_v2_5"

    clean_lang = None
    if language_code:
        cand = str(language_code).strip().lower().split("-")[0].split("_")[0]
        if cand and cand not in ("multilingual", "multi", "any", "all", "auto"):
            clean_lang = cand

    if not clean_lang:
        env_lang = os.getenv("TARGET_LANGUAGE", "").strip().lower()
        if env_lang:
            cand = env_lang.split("-")[0].split("_")[0]
            if cand and cand not in ("multilingual", "multi", "any", "all", "auto"):
                clean_lang = cand

    if not clean_lang:
        vietnamese_chars = set(
            "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
            "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ"
        )
        if any(c in vietnamese_chars for c in text):
            clean_lang = "vi"

    if on_progress:
        on_progress(f"[ElevenLabs] Đang tạo giọng nói ({clean_voice_id})...")
    print(f"[ElevenLabs] Synthesizing speech for voice_id='{clean_voice_id}'...")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{clean_voice_id}"
    headers = {
        "xi-api-key": str(api_key or "").strip(),
        "Content-Type": "application/json",
    }
    params = {"output_format": "mp3_44100_128"}
    payload = {
        "text": text,
        "model_id": resolved_model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "speed": clamped_speed,
        },
    }
    if clean_lang:
        payload["language_code"] = clean_lang

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(
                url,
                headers=headers,
                params=params,
                json=payload,
                stream=True,
                timeout=30,
            )
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"ElevenLabs API network error: {e}") from e
            time.sleep(1.0 * (attempt + 1))
            continue

        if response.status_code == 429:
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError("ElevenLabs API: Đã đạt giới hạn quota hoặc rate limit. Vui lòng thử lại sau.")

        if response.status_code == 400:
            err_text = response.text
            err_lower = err_text.lower()
            if "unsupported_language" in err_lower or "does not support language_code" in err_lower:
                raise RuntimeError(
                    f"ElevenLabs API (Lỗi 400 - Model không hỗ trợ ngôn ngữ): Model '{resolved_model_id}' không hỗ trợ mã ngôn ngữ '{clean_lang}'. "
                    "Vui lòng vào Settings chọn model 'eleven_turbo_v2_5' hoặc 'eleven_flash_v2_5' để hỗ trợ tiếng Việt."
                )
            raise RuntimeError(f"ElevenLabs API error 400: {err_text}")

        if response.status_code == 401:
            raise RuntimeError("ElevenLabs API Key không hợp lệ hoặc đã hết hạn.")
        if response.status_code == 402:
            err_text = response.text
            err_lower = err_text.lower()
            if "library_voice" in err_lower or "paid_plan_required" in err_lower or "voice library" in err_lower:
                raise RuntimeError(
                    "ElevenLabs API (Lỗi 402): Tài khoản Free không thể sử dụng giọng từ Voice Library (cộng đồng) qua API. "
                    "Vui lòng đổi sang các giọng có nhãn [Default] hoặc giọng tự Clone [Clone] trong tài khoản, "
                    "hoặc nâng cấp gói trả phí trên ElevenLabs."
                )
            raise RuntimeError(f"ElevenLabs API error 402 (Payment Required): {err_text}")
        if response.status_code != 200:
            raise RuntimeError(f"ElevenLabs API error {response.status_code}: {response.text}")

        audio_bytes = response.content
        if not audio_bytes:
            raise RuntimeError(f"ElevenLabs trả về audio rỗng cho đoạn text: '{text[:40]}'")

        _convert_audio_wav(audio_bytes, wav_path, tmp_dir=tmp_dir)

        if on_progress:
            on_progress("[ElevenLabs] Hoàn tất tạo giọng nói.")

        return wav_path
