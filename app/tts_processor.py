import asyncio
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from runtime_paths import app_path, bin_path, models_path, temp_path, subprocess_text_kwargs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(os.path.dirname(BASE_DIR), ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)


_PIPER_VOICE_CACHE = {}
_PIPER_VOICE_CACHE_LOCK = threading.Lock()
_VIETNAMESE_NORMALIZER = None
_VIETNAMESE_NORMALIZER_DATA_DIR = ""
_VIETNAMESE_NORMALIZER_CACHE = {}
_VIETNAMESE_NORMALIZER_CACHE_LOCK = threading.RLock()


def _canonical_normalizer_dictionary(dictionary) -> dict:
    """Return the project dictionary in a stable, validated representation.

    The dictionary is project data, not a global resource.  Keep only the
    two pronunciation maps supported by the UI and normalize keys before
    they reach vietnormalizer so cache keys and matching are deterministic.
    """
    raw = dictionary if isinstance(dictionary, dict) else {}

    def _rows(*names):
        value = []
        for name in names:
            candidate = raw.get(name)
            if candidate is not None:
                value = candidate
                break
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return []
        result = []
        for row in value:
            if not isinstance(row, dict):
                continue
            key = ""
            for field in ("acronym", "original", "word", "source", "text", "key"):
                if field in row:
                    key = str(row.get(field) or "").strip()
                    if key:
                        break
            pronunciation = ""
            for field in ("transliteration", "pronunciation", "vietnamese_pronunciation", "replacement", "value"):
                if field in row:
                    pronunciation = str(row.get(field) or "").strip()
                    if pronunciation:
                        break
            if key and pronunciation:
                result.append({"key": key.lower(), "value": pronunciation})
        result.sort(key=lambda item: (len(item["key"]), item["key"], item["value"]))
        return result

    return {
        "acronyms": _rows("acronyms", "acronym"),
        "non_vietnamese_words": _rows(
            "non_vietnamese_words",
            "non-vietnamese-words",
            "non_vietnamese",
            "words",
        ),
    }


def normalizer_dictionary_fingerprint(dictionary=None) -> str:
    """Return a stable cache key for a project's pronunciation dictionary."""
    payload = json.dumps(
        _canonical_normalizer_dictionary(dictionary),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def reset_vietnamese_normalizer_cache() -> None:
    """Drop all in-process normalizer instances after dictionary edits."""
    global _VIETNAMESE_NORMALIZER, _VIETNAMESE_NORMALIZER_DATA_DIR
    with _VIETNAMESE_NORMALIZER_CACHE_LOCK:
        _VIETNAMESE_NORMALIZER_CACHE.clear()
        _VIETNAMESE_NORMALIZER = None
        _VIETNAMESE_NORMALIZER_DATA_DIR = ""


def _build_vietnamese_normalizer(dictionary) -> object:
    """Build a normalizer from bundled data plus project overrides."""
    from vietnormalizer.normalizer import VietnameseNormalizer

    normalizer = VietnameseNormalizer()
    canonical = _canonical_normalizer_dictionary(dictionary)
    for row in canonical["acronyms"]:
        normalizer.acronym_map[row["key"]] = row["value"]
    for row in canonical["non_vietnamese_words"]:
        normalizer.non_vietnamese_map[row["key"]] = row["value"]
    normalizer.acronym_map = dict(
        sorted(normalizer.acronym_map.items(), key=lambda item: len(item[0]), reverse=True)
    )
    normalizer.non_vietnamese_map = dict(
        sorted(normalizer.non_vietnamese_map.items(), key=lambda item: len(item[0]), reverse=True)
    )
    normalizer._build_replacement_dict()
    return normalizer


def _get_vietnamese_normalizer(dictionary=None):
    global _VIETNAMESE_NORMALIZER, _VIETNAMESE_NORMALIZER_DATA_DIR
    canonical = _canonical_normalizer_dictionary(dictionary)
    cache_key = normalizer_dictionary_fingerprint(canonical)
    with _VIETNAMESE_NORMALIZER_CACHE_LOCK:
        if cache_key in _VIETNAMESE_NORMALIZER_CACHE:
            normalizer = _VIETNAMESE_NORMALIZER_CACHE[cache_key]
            _VIETNAMESE_NORMALIZER = normalizer
            _VIETNAMESE_NORMALIZER_DATA_DIR = f"project:{cache_key}"
            return normalizer
    try:
        normalizer = _build_vietnamese_normalizer(canonical)
        entries = len(canonical["acronyms"]) + len(canonical["non_vietnamese_words"])
        print(f"[TTS] Vietnamese normalizer loaded (project entries: {entries}).")
    except Exception as exc:
        # Keep Piper usable even if optional normalizer data is unavailable;
        # the detailed reason remains in the log for packaged-build support.
        print(f"[TTS] Vietnamese normalizer unavailable; using original text: {exc}")
        normalizer = False
    with _VIETNAMESE_NORMALIZER_CACHE_LOCK:
        _VIETNAMESE_NORMALIZER_CACHE[cache_key] = normalizer
    _VIETNAMESE_NORMALIZER = normalizer
    _VIETNAMESE_NORMALIZER_DATA_DIR = f"project:{cache_key}"
    return normalizer


def _voice_catalog_path() -> str:
    return app_path("voice_preview_catalog.json")


def _append_local_piper_manifest_voices(catalog: dict) -> dict:
    """Add piper-new voices that are not present in the static catalog.

    The packaged catalog is read-only, while users can download additional
    piper-new models into ``models/piper``.  Keep TTS/Preview Voice aligned
    with the UI by resolving those manifest entries in memory.
    """
    if not isinstance(catalog, dict):
        catalog = {"voices": []}
    voices = catalog.setdefault("voices", [])
    if not isinstance(voices, list):
        voices = []
        catalog["voices"] = voices
    known_ids = {
        str(item.get("id", "")).strip()
        for item in voices
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    manifest_path = models_path("piper", "voices.json")
    if not os.path.isfile(manifest_path):
        return catalog
    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as handle:
            manifest = json.load(handle)
    except Exception:
        return catalog
    if not isinstance(manifest, list):
        return catalog
    for item in manifest:
        if not isinstance(item, dict):
            continue
        audio_path = str(item.get("audio_path", "") or "").replace("\\", "/").strip()
        filename = os.path.basename(audio_path)
        voice_id = os.path.splitext(filename)[0]
        if not voice_id or voice_id in known_ids:
            continue
        model_path = models_path("piper", filename)
        if not os.path.isfile(model_path) or os.path.getsize(model_path) <= 0:
            continue
        voices.append(
            {
                "id": voice_id,
                "name": str(item.get("name", "") or "").strip() or voice_id,
                "provider": "piper",
                "provider_voice": f"models/piper/{filename}",
                "language": "vi",
                "gender": str(item.get("gender", "") or "").strip(),
                "tier": "free",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["local", "piper"],
            }
        )
        known_ids.add(voice_id)
    return catalog


def _resolve_piper_model_path(provider_voice: str) -> str:
    raw = str(provider_voice or "").strip().replace("/", os.sep)
    if not raw:
        return ""
    normalized = os.path.normpath(raw)
    if os.path.isabs(normalized):
        return normalized
    if normalized.startswith(f"models{os.sep}"):
        # models_path() prefers user-installed files and falls back to the
        # read-only PyInstaller bundle.  Resolving through bundle_root()
        # directly would make a bundled voice shadow a newer voice downloaded
        # into CapCap/models after installation.
        relative = normalized[len("models" + os.sep):]
        return models_path(*Path(relative).parts)
    return models_path(normalized)


def _resolve_piper_config_path(model_path: str, config_path: str | None = None) -> str:
    """Resolve the Piper JSON config for a model.

    Older Piper packs put a separate ``<model>.onnx.json`` next to every
    voice.  The current Vietnamese ``piper-new`` pack intentionally shares a
    single ``models/piper/config.json`` between all ONNX voices.  Prefer an
    explicitly supplied config, then the shared Vietnamese config when it is
    present, and finally the legacy per-model file so both layouts remain
    compatible.
    """
    model_key = os.path.abspath(str(model_path or "").strip()) if model_path else ""
    if config_path:
        candidate = os.path.abspath(str(config_path).strip())
        if os.path.isfile(candidate):
            return candidate
    if not model_key:
        return ""
    legacy = f"{model_key}.json"
    shared = os.path.join(os.path.dirname(model_key), "config.json")
    folder_name = os.path.basename(os.path.dirname(model_key)).lower()
    # piper-new is the canonical Vietnamese layout.  If both an old
    # per-model sidecar and the new shared config are present, use the shared
    # config so every voice is rendered with the same metadata.
    if folder_name == "piper" and os.path.isfile(shared):
        return shared
    if os.path.isfile(legacy):
        return legacy
    if os.path.isfile(shared):
        return shared
    # A model may be downloaded into the writable folder while the shared
    # config is still bundled (or vice versa).  Look up the matching language
    # directory through models_path() before giving up.
    if folder_name:
        bundled_or_writable_shared = models_path(folder_name, "config.json")
        if os.path.isfile(bundled_or_writable_shared):
            return bundled_or_writable_shared
    return ""


def _get_cached_piper_voice(*, model_path: str, config_path: str | None = None, on_progress: callable = None):
    from piper import PiperVoice
    model_key = os.path.abspath(str(model_path or "").strip())
    if not model_key:
        raise ValueError("model_path is required for Piper TTS")
    config_key = _resolve_piper_config_path(model_key, config_path)
    cache_key = (model_key, config_key)

    with _PIPER_VOICE_CACHE_LOCK:
        cached = _PIPER_VOICE_CACHE.get(cache_key)
        if cached is not None:
            return cached

    if on_progress:
        on_progress(f"Loading Piper model from {os.path.basename(model_key)}...")

    # Passing the resolved path is important for the shared-config Piper pack;
    # Piper otherwise assumes a sibling ``<model>.onnx.json`` file exists.
    voice = PiperVoice.load(model_key, config_path=config_key or None)

    with _PIPER_VOICE_CACHE_LOCK:
        # Avoid double-load if another thread raced.
        _PIPER_VOICE_CACHE.setdefault(cache_key, voice)
        return _PIPER_VOICE_CACHE[cache_key]


def _ffmpeg_path():
    return bin_path("ffmpeg", "ffmpeg.exe")


def _subprocess_run_kwargs() -> dict:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)    
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip()
    return name[:120] if len(name) > 120 else name


def _validate_generated_wav(wav_path: str) -> None:
    if not wav_path or not os.path.exists(wav_path):
        raise RuntimeError("Generated WAV file is missing.")
    if os.path.getsize(wav_path) <= 44:
        raise RuntimeError("Generated WAV file is empty.")
    try:
        with wave.open(wav_path, "rb") as wav_file:
            channels = int(wav_file.getnchannels() or 0)
            frame_rate = int(wav_file.getframerate() or 0)
            frame_count = int(wav_file.getnframes() or 0)
        if channels <= 0:
            raise RuntimeError("Generated WAV file has no audio channels.")
        if frame_rate <= 0 or frame_count <= 0:
            raise RuntimeError("Generated WAV file has no valid audio frames.")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Generated WAV file is invalid: {exc}") from exc


def _voice_provider_and_id(voice: str) -> tuple[str, str]:
    raw = (voice or "").strip()
    if ":" in raw:
        provider, voice_id = raw.split(":", 1)
        return provider.strip().lower(), voice_id.strip()
    return "edge", raw


def _speed_to_float(speed) -> float:
    if isinstance(speed, (int, float)):
        return float(speed)
    text = str(speed or "").strip().lower().replace("x", "")
    try:
        return float(text or "1.0")
    except ValueError:
        return 1.0


def normalize_text_for_tts(
    text: str,
    *,
    provider: str = "piper",
    language: str = "vi",
    normalizer_dictionary=None,
) -> str:
    value = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if not value:
        return ""
    if str(provider or "").strip().lower() != "piper":
        return value
    # vietnormalizer is deliberately Vietnamese-specific. English Piper
    # voices should receive the translated text unchanged.
    if not str(language or "vi").strip().lower().startswith("vi"):
        return value

    normalizer = _get_vietnamese_normalizer(normalizer_dictionary)
    if normalizer is False:
        return value
    try:
        normalized = normalizer.normalize(value)
        return " ".join(str(normalized or "").replace("\n", " ").split()).strip() or value
    except Exception:
        return value


def _subprocess_run_kwargs() -> dict:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip()
    return name[:120] if len(name) > 120 else name


def _voice_provider_and_id(voice: str) -> tuple[str, str]:
    raw = (voice or "").strip()
    if ":" in raw:
        provider, voice_id = raw.split(":", 1)
        return provider.strip().lower(), voice_id.strip()
    return "edge", raw


def _speed_to_float(speed) -> float:
    if isinstance(speed, (int, float)):
        return float(speed)
    text = str(speed or "").strip().lower().replace("x", "")
    try:
        return float(text or "1.0")
    except ValueError:
        return 1.0





def piper_tts_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    model_path: str,
    language: str = "vi",
    speed: float = 1.0,
    tmp_dir: str | None = None,
    on_progress: callable = None,
    normalizer_dictionary=None,
) -> str:
    """
    Synthesize text to WAV (16kHz, mono) using Piper TTS with ONNX model.
    
    Args:
        on_progress: Optional callback for progress updates: on_progress(message)
    """
    if tmp_dir is None:
        tmp_dir = temp_path()
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    # Normalize text
    normalized_text = normalize_text_for_tts(
        text,
        provider="piper",
        language=language,
        normalizer_dictionary=normalizer_dictionary,
    )

    # Load Piper voice
    voice = _get_cached_piper_voice(model_path=model_path, on_progress=on_progress)

    # Configure synthesis
    from piper.config import SynthesisConfig
    syn_config = SynthesisConfig(length_scale=1.0 / speed)

    # Synthesize to WAV
    with wave.open(wav_path, "wb") as wav_file:
        voice.synthesize_wav(normalized_text, wav_file, syn_config=syn_config)
    _validate_generated_wav(wav_path)
    
    return wav_path


async def _edge_tts_to_mp3_async(text: str, mp3_path: str, voice: str, rate: str, volume: str):
    try:
        import edge_tts
    except Exception as e:
        raise ImportError(
            "Missing dependency 'edge-tts'.\n"
            "Please run:\n"
            "python -m pip install edge-tts\n"
            f"Original error: {e}"
        ) from e

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    await communicate.save(mp3_path)


def edge_tts_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice: str = "vi-VN-HoaiMyNeural",
    rate: str = "+0%",
    volume: str = "+0%",
    tmp_dir: str | None = None,
) -> str:
    """
    Synthesize text to WAV (16kHz, mono) using Edge TTS.
    Edge TTS outputs mp3, then we convert to wav using ffmpeg.
    Returns wav_path.
    """
    if tmp_dir is None:
        tmp_dir = temp_path()
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    base = _sanitize_filename(os.path.splitext(os.path.basename(wav_path))[0] or "tts")
    mp3_path = os.path.join(tmp_dir, f"{base}.mp3")

    # Run async edge-tts safely in sync context with a few retries for transient empty-audio failures.
    last_error = None
    for attempt in range(1, 4):
        try:
            asyncio.run(_edge_tts_to_mp3_async(text, mp3_path, voice, rate, volume))
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            if attempt >= 3:
                raise
            time.sleep(0.6 * attempt)
    if last_error is not None:
        raise last_error

    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        mp3_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        wav_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, **subprocess_text_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed:\n{proc.stderr or proc.stdout}")
    return wav_path


def preload_tts_voice(voice: str, on_progress: callable = None) -> bool:
    voice_to_search = str(voice).strip()
    if voice_to_search.startswith(("vieneu:", "vieneu_clone:")):
        try:
            from vieneu_tts import get_cached_vieneu_model
            get_cached_vieneu_model(on_progress=on_progress)
            return True
        except Exception:
            return False

    catalog_path = _voice_catalog_path()
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    catalog = _append_local_piper_manifest_voices(catalog)

    voice_entry = None
    for v in catalog.get("voices", []):
        if v["id"] == voice_to_search:
            voice_entry = v
            break
    if not voice_entry and ":" in voice_to_search:
        provider, provider_voice = voice_to_search.split(":", 1)
        provider = provider.strip().lower()
        provider_voice = provider_voice.strip()
        for v in catalog.get("voices", []):
            if v.get("provider") == provider and (v.get("provider_voice") == provider_voice or v.get("id") == provider_voice):
                voice_entry = v
                break
    if not voice_entry:
        return False

    provider = voice_entry.get("provider", "").strip().lower()
    provider_voice = str(voice_entry.get("provider_voice", "")).strip()
    if provider == "vieneu":
        try:
            from vieneu_tts import get_cached_vieneu_model
            get_cached_vieneu_model(on_progress=on_progress)
            return True
        except Exception:
            return False
    if provider != "piper":
        return False

    model_path = _resolve_piper_model_path(provider_voice)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Piper model not found at {model_path}. Please download and place the model there.")

    _get_cached_piper_voice(model_path=model_path, on_progress=on_progress)
    return True


def synthesize_text_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice: str = "ngochuyen",
    speed: float = 1.0,
    tmp_dir: str | None = None,
    on_progress: callable = None,
    normalizer_dictionary=None,
) -> str:
    voice_to_search = str(voice).strip()

    # Fast path for VieNeu voices
    if voice_to_search.startswith(("vieneu:", "vieneu_clone:")):
        from vieneu_tts import vieneu_synthesize_wav_16k_mono
        return vieneu_synthesize_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            voice_id=voice_to_search,
            speed=speed,
            tmp_dir=tmp_dir,
            on_progress=on_progress,
        )

    # Fast path for CapCut voices
    if voice_to_search.startswith("capcut:"):
        try:
            from app.capcut import synthesize_capcut_tts_wav_16k_mono
        except ImportError:
            from capcut import synthesize_capcut_tts_wav_16k_mono
        return synthesize_capcut_tts_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            voice_id=voice_to_search,
            speed=speed,
            tmp_dir=tmp_dir,
            on_progress=on_progress,
        )

    # Load voice catalog
    catalog_path = _voice_catalog_path()
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    catalog = _append_local_piper_manifest_voices(catalog)
    
    # Find voice in catalog
    voice_entry = None
    
    # First try exact match by ID
    for v in catalog.get("voices", []):
        if v["id"] == voice_to_search:
            voice_entry = v
            break
    
    # If not found, try to parse it (e.g., "edge:...", "piper:...", etc.)
    if not voice_entry and ":" in voice_to_search:
        parts = voice_to_search.split(":", 1)
        provider = parts[0].strip().lower()
        provider_voice = parts[1].strip()
        for v in catalog.get("voices", []):
            if v.get("provider") == provider and (v.get("provider_voice") == provider_voice or v.get("id") == provider_voice):
                voice_entry = v
                break
    
    # Fallback: use the first available voice
    if not voice_entry:
        voices = catalog.get("voices", [])
        if voices:
            voice_entry = voices[0]
            if on_progress:
                on_progress(f"Voice '{voice_to_search}' not found, using fallback: {voice_entry.get('name')}")
    
    if not voice_entry:
        raise ValueError(f"No voice found in catalog for: {voice_to_search}")
    
    provider = voice_entry["provider"]
    provider_voice = voice_entry["provider_voice"]
    
    if provider == "piper":
        # Use Piper TTS with local model
        model_path = _resolve_piper_model_path(provider_voice)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Piper model not found at {model_path}. Please download and place the model there.")
        
        return piper_tts_to_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            model_path=model_path,
            language=str(voice_entry.get("language", "vi") or "vi"),
            speed=speed,
            tmp_dir=tmp_dir,
            on_progress=on_progress,
            normalizer_dictionary=normalizer_dictionary,
        )
    elif provider == "edge":
        # Use Edge TTS
        speed_value = _speed_to_float(speed)
        edge_rate_percent = int(round((speed_value - 1.0) * 100.0))
        edge_rate = f"{edge_rate_percent:+d}%"
        return edge_tts_to_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            voice=provider_voice or "vi-VN-HoaiMyNeural",
            rate=edge_rate,
            tmp_dir=tmp_dir,
        )
    elif provider == "vieneu":
        from vieneu_tts import vieneu_synthesize_wav_16k_mono
        return vieneu_synthesize_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            voice_id=provider_voice or voice_entry.get("id", voice_to_search),
            speed=speed,
            tmp_dir=tmp_dir,
            on_progress=on_progress,
        )
    else:
        raise ValueError(f"Unsupported TTS provider: {provider}. Only 'piper', 'edge', and 'vieneu' are supported.")
