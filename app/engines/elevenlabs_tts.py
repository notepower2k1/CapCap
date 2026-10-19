"""app/engines/elevenlabs_tts.py

ElevenLabs TTS engine module for CapCap video localization app.
Provides voice catalog fetching with local disk caching and
high quality speech synthesis converting to 16kHz mono WAV.
"""

from __future__ import annotations

import json
import os
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
    "fetch_elevenlabs_voices",
    "synthesize_elevenlabs_tts_wav_16k_mono",
]

CACHE_FILE_NAME = "elevenlabs_voices_cache.json"
MAX_CACHE_AGE_SECONDS = 3600  # 1 hour

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


def fetch_elevenlabs_voices(api_key: str) -> list[dict]:
    """Fetch voices from ElevenLabs API. Returns list of CapCap catalog-compatible voice dicts.
    Uses requests. Caches to temp/elevenlabs_voices_cache.json (max age 1 hour).
    Raises RuntimeError with clear message on auth failure (401) or rate limit (429).
    Returns [] on any other network error (with print warning).
    """
    cache_path = _resolve_temp_path(CACHE_FILE_NAME)
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            fetched_at = float(cache_data.get("fetched_at", 0))
            if time.time() - fetched_at < MAX_CACHE_AGE_SECONDS:
                cached_voices = cache_data.get("voices")
                if isinstance(cached_voices, list):
                    return cached_voices
        except Exception:
            pass

    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": str(api_key or "").strip()}
    print("[ElevenLabs] Đang tải danh sách giọng nói từ API...")

    try:
        response = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        print(f"[ElevenLabs] Lỗi mạng khi tải danh sách giọng nói: {e}")
        return []

    if response.status_code == 401:
        raise RuntimeError("ElevenLabs API Key không hợp lệ hoặc đã hết hạn.")
    if response.status_code == 429:
        raise RuntimeError("ElevenLabs API: Đã đạt giới hạn quota hoặc rate limit. Vui lòng thử lại sau.")
    if response.status_code != 200:
        print(f"[ElevenLabs] Lỗi HTTP khi tải danh sách giọng nói: {response.status_code} {response.text}")
        return []

    try:
        data = response.json()
    except Exception as e:
        print(f"[ElevenLabs] Lỗi phân tích JSON danh sách giọng nói: {e}")
        return []

    raw_voices = data.get("voices", []) if isinstance(data, dict) else []
    voices: list[dict] = []
    for item in raw_voices:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("voice_id", "") or "").strip()
        if not voice_id:
            continue
        name = str(item.get("name", "") or "").strip()
        labels = item.get("labels") or {}
        gender = "any"
        if isinstance(labels, dict):
            raw_gender = str(labels.get("gender") or "").strip().lower()
            if raw_gender in ("male", "female"):
                gender = raw_gender
        preview_url = str(item.get("preview_url") or "").strip()

        voices.append({
            "id": f"elevenlabs:{voice_id}",
            "name": name,
            "provider": "elevenlabs",
            "provider_voice": voice_id,
            "language": "multilingual",
            "gender": gender,
            "tier": "premium",
            "enabled": True,
            "tags": ["elevenlabs", "online"],
            "preview_video_url": "",
            "preview_video_path": "",
            "preview_audio_url": preview_url,
            "preview_audio_path": "",
        })

    try:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time(), "voices": voices}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ElevenLabs] Cảnh báo: Không thể ghi cache giọng nói: {e}")

    return voices


def synthesize_elevenlabs_tts_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice_id: str,
    api_key: str,
    model_id: str = "eleven_multilingual_v2",
    speed: float = 1.0,
    tmp_dir: str | None = None,
    on_progress: callable = None,
) -> str:
    """Synthesize text to WAV 16kHz mono using ElevenLabs API.
    Uses requests to POST to https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
    with output_format=mp3_44100_128.
    Converts MP3 bytes to 16kHz mono WAV using convert_audio_data_to_wav_16k_mono
    (import from tts_processor).
    Raises RuntimeError with clear message on 401, 429.
    Raises RuntimeError on empty audio response.
    Returns wav_path on success.
    """
    clean_voice_id = str(voice_id or "").strip()
    if clean_voice_id.startswith("elevenlabs:"):
        clean_voice_id = clean_voice_id[len("elevenlabs:"):]

    clamped_speed = max(0.7, min(1.2, float(speed)))

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
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "speed": clamped_speed,
        },
    }

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
        raise RuntimeError(f"ElevenLabs API network error: {e}") from e

    if response.status_code == 401:
        raise RuntimeError("ElevenLabs API Key không hợp lệ hoặc đã hết hạn.")
    if response.status_code == 429:
        raise RuntimeError("ElevenLabs API: Đã đạt giới hạn quota hoặc rate limit. Vui lòng thử lại sau.")
    if response.status_code != 200:
        raise RuntimeError(f"ElevenLabs API error {response.status_code}: {response.text}")

    audio_bytes = response.content
    if not audio_bytes:
        raise RuntimeError(f"ElevenLabs trả về audio rỗng cho đoạn text: '{text[:40]}'")

    _convert_audio_wav(audio_bytes, wav_path, tmp_dir=tmp_dir)

    if on_progress:
        on_progress("[ElevenLabs] Hoàn tất tạo giọng nói.")

    return wav_path
