import hashlib
import json
import os
import wave

from runtime_paths import app_path


def manifest_path(tmp_dir: str) -> str:
    return os.path.join(tmp_dir, "tts_cache_manifest.json")


def load_manifest(tmp_dir: str) -> dict:
    path = manifest_path(tmp_dir)
    if not os.path.exists(path):
        return {"segments": {}, "by_cache_key": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            payload.setdefault("segments", {})
            payload.setdefault("by_cache_key", {})
            return payload
    except Exception as exc:
        print(f"[VoicePreviewUtils] load_manifest failed: {exc}")
    return {"segments": {}, "by_cache_key": {}}


def save_manifest(tmp_dir: str, manifest: dict) -> None:
    path = manifest_path(tmp_dir)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)


def segment_cache_key(
    *,
    text: str,
    voice_name: str,
    provider_speed: float,
    normalizer_signature: str = "",
    model_id: str | None = None,
    language_code: str | None = None,
) -> str:
    # A pronunciation dictionary changes the generated waveform even when
    # subtitle text, voice, and speed remain identical. Include its stable
    # fingerprint so stale TTS cache entries cannot survive a dictionary edit.
    payload = f"{voice_name}|{provider_speed:.3f}|{normalizer_signature}|{text.strip()}"
    if model_id or language_code:
        payload += f"|model={str(model_id or '').strip()}|language={str(language_code or '').strip().lower()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def is_valid_non_silent_wav(wav_path: str) -> bool:
    """Check whether a generated cache candidate contains decodable audio."""
    if not wav_path or not os.path.exists(wav_path):
        return False
    try:
        if os.path.getsize(wav_path) <= 44:
            return False
        with wave.open(wav_path, "rb") as handle:
            sample_width = int(handle.getsampwidth())
            if handle.getnframes() <= 0 or sample_width <= 0:
                return False
            silence_byte = 128 if sample_width == 1 else 0
            while True:
                chunk = handle.readframes(8192)
                if not chunk:
                    return False
                if any(value != silence_byte for value in chunk):
                    return True
    except Exception:
        return False


def voice_provider(voice_name: str) -> str:
    raw = str(voice_name or "").strip()
    if raw.lower().startswith("f5:"):
        return "f5"
    if raw.lower().startswith(("vieneu:", "vieneu_clone:")):
        return "vieneu"
    if raw.lower().startswith("capcut:"):
        return "capcut"
    if ":" in raw:
        provider, _ = raw.split(":", 1)
        return provider.strip().lower() or "edge"
    catalog_paths = [
        app_path("voice_preview_catalog.json"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "voice_preview_catalog.json"),
        os.path.join(os.getcwd(), "app", "voice_preview_catalog.json"),
    ]
    for catalog_path in catalog_paths:
        try:
            if os.path.exists(catalog_path):
                with open(catalog_path, "r", encoding="utf-8") as f:
                    catalog = json.load(f)
                for voice in catalog.get("voices", []):
                    if voice.get("id") == raw:
                        return voice.get("provider", "edge").strip().lower()
        except Exception as exc:
            print(f"[VoicePreviewUtils] catalog read error {catalog_path}: {exc}")
    return "piper"


def provider_native_speed(*, provider: str, requested_speed: float) -> float:
    if provider in {"edge", "f5"}:
        return float(requested_speed)
    return 1.0


def clamp_requested_speed(requested_speed: float) -> float:
    speed_value = max(0.5, float(requested_speed or 1.0))
    if speed_value > 2.0:
        print(f"[VoicePreviewUtils] Requested speed {speed_value:.2f} exceeds safe cap. Clamping to 2.00.")
        return 2.0
    return speed_value
