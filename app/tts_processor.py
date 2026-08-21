import asyncio
import json
import os
import re
import subprocess
import threading
import time
import wave

from dotenv import load_dotenv
from runtime_paths import app_path, bin_path, bundle_root, models_path, temp_path, subprocess_text_kwargs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(os.path.dirname(BASE_DIR), ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)


_PIPER_VOICE_CACHE = {}
_PIPER_VOICE_CACHE_LOCK = threading.Lock()
_VIETNAMESE_NORMALIZER = None
_VIETNAMESE_NORMALIZER_DATA_DIR = ""


def _voice_catalog_path() -> str:
    return app_path("voice_preview_catalog.json")


def _resolve_piper_model_path(provider_voice: str) -> str:
    raw = str(provider_voice or "").strip().replace("/", os.sep)
    if not raw:
        return ""
    normalized = os.path.normpath(raw)
    if os.path.isabs(normalized):
        return normalized
    if normalized.startswith(f"models{os.sep}"):
        candidate = os.path.join(bundle_root(), normalized)
        if os.path.exists(candidate):
            return candidate
        return os.path.join(os.path.dirname(bundle_root()), normalized)
    return models_path(normalized)


def _get_cached_piper_voice(*, model_path: str, on_progress: callable = None):
    from piper import PiperVoice
    model_key = os.path.abspath(str(model_path or "").strip())
    if not model_key:
        raise ValueError("model_path is required for Piper TTS")

    with _PIPER_VOICE_CACHE_LOCK:
        cached = _PIPER_VOICE_CACHE.get(model_key)
        if cached is not None:
            return cached

    if on_progress:
        on_progress(f"Loading Piper model from {os.path.basename(model_key)}...")

    voice = PiperVoice.load(model_key)

    with _PIPER_VOICE_CACHE_LOCK:
        # Avoid double-load if another thread raced.
        _PIPER_VOICE_CACHE.setdefault(model_key, voice)
        return _PIPER_VOICE_CACHE[model_key]


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


def normalize_text_for_tts(text: str, *, provider: str = "piper", language: str = "vi") -> str:
    value = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if not value:
        return ""
    if str(provider or "").strip().lower() != "piper":
        return value
    # vietnormalizer is deliberately Vietnamese-specific. English Piper
    # voices should receive the translated text unchanged.
    if not str(language or "vi").strip().lower().startswith("vi"):
        return value

    global _VIETNAMESE_NORMALIZER, _VIETNAMESE_NORMALIZER_DATA_DIR
    if _VIETNAMESE_NORMALIZER is None:
        try:
            from vietnormalizer.normalizer import VietnameseNormalizer
            from vietnormalizer import normalizer as vn_mod
            from pathlib import Path
            import csv

            custom_dir = Path(models_path("vietnormalizer"))
            combined_dir = custom_dir / "_combined"
            default_data = Path(vn_mod.__file__).parent / "data"

            if custom_dir.exists() and any(custom_dir.glob("*.csv")):
                combined_dir.mkdir(parents=True, exist_ok=True)
                dict_files = [
                    ("acronyms.csv", "acronym"),
                    ("non-vietnamese-words.csv", "original"),
                ]
                for filename, key_col in dict_files:
                    target = combined_dir / filename
                    src = default_data / filename
                    entries = {}
                    if src.exists():
                        with open(src, encoding="utf-8", newline="") as f:
                            for row in csv.DictReader(f):
                                k = (row.get(key_col) or "").strip().lower()
                                if k:
                                    entries[k] = row
                    custom_file = custom_dir / filename
                    if custom_file.exists():
                        with open(custom_file, encoding="utf-8", newline="") as f:
                            for row in csv.DictReader(f):
                                k = (row.get(key_col) or "").strip().lower()
                                if k:
                                    entries[k] = row
                    rows = list(entries.values())
                    rows.sort(key=lambda r: len(r.get(key_col, "") or ""), reverse=True)
                    fieldnames = list(rows[0].keys()) if rows else [key_col, "transliteration"]
                    with open(target, "w", encoding="utf-8", newline="") as f:
                        w = csv.DictWriter(f, fieldnames=fieldnames)
                        w.writeheader()
                        w.writerows(rows)

                _VIETNAMESE_NORMALIZER = VietnameseNormalizer(data_dir=str(combined_dir))

                custom_acro = custom_dir / "acronyms.csv"
                if custom_acro.exists():
                    with open(custom_acro, encoding="utf-8", newline="") as f:
                        for row in csv.DictReader(f):
                            k = (row.get("acronym") or "").strip().lower()
                            v = (row.get("transliteration") or "").strip()
                            if k and v:
                                _VIETNAMESE_NORMALIZER.non_vietnamese_map[k] = v
                    _VIETNAMESE_NORMALIZER.non_vietnamese_map = dict(
                        sorted(_VIETNAMESE_NORMALIZER.non_vietnamese_map.items(), key=lambda x: len(x[0]), reverse=True)
                    )
                    _VIETNAMESE_NORMALIZER._build_replacement_dict()

                _VIETNAMESE_NORMALIZER_DATA_DIR = str(custom_dir)
                print(f"[TTS] Vietnamese normalizer loaded with custom dicts from: {custom_dir}")
            else:
                _VIETNAMESE_NORMALIZER = VietnameseNormalizer()
                _VIETNAMESE_NORMALIZER_DATA_DIR = ""
        except Exception:
            _VIETNAMESE_NORMALIZER = False
            _VIETNAMESE_NORMALIZER_DATA_DIR = ""
    if _VIETNAMESE_NORMALIZER is False:
        return value
    try:
        normalized = _VIETNAMESE_NORMALIZER.normalize(value)
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
    normalized_text = normalize_text_for_tts(text, provider="piper", language=language)

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
    catalog_path = _voice_catalog_path()
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    voice_entry = None
    voice_to_search = str(voice).strip()
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
) -> str:
    # Load voice catalog
    catalog_path = _voice_catalog_path()
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    
    # Find voice in catalog
    voice_entry = None
    voice_to_search = str(voice).strip()
    
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
    else:
        raise ValueError(f"Unsupported TTS provider: {provider}. Only 'piper' and 'edge' are supported.")

