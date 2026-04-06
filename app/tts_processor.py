import asyncio
import json
import os
import re
import subprocess
import time
import wave

import requests
from dotenv import load_dotenv
from piper import PiperVoice
from piper.config import SynthesisConfig
from vietnormalizer.normalizer import VietnameseNormalizer


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(os.path.dirname(BASE_DIR), ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)


def _ffmpeg_path():
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffmpeg.exe")


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
        tmp_dir = os.path.join(os.getcwd(), "temp")
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    # Normalize text
    if on_progress:
        on_progress(f"Normalizing Vietnamese text...")
    normalizer = VietnameseNormalizer()
    normalized_text = normalizer.normalize(text)

    # Load Piper voice
    if on_progress:
        on_progress(f"Loading Piper model from {os.path.basename(model_path)}...")
    voice = PiperVoice.load(model_path)

    # Configure synthesis
    if on_progress:
        on_progress(f"Synthesizing speech (speed: {speed}x)...")
    syn_config = SynthesisConfig(length_scale=1.0 / speed)

    # Synthesize to WAV
    with wave.open(wav_path, "wb") as wav_file:
        voice.synthesize_wav(normalized_text, wav_file, syn_config=syn_config)

    if on_progress:
        on_progress(f"✓ Synthesis complete: {os.path.basename(wav_path)}")
    
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
        tmp_dir = os.path.join(os.getcwd(), "temp")
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    base = _sanitize_filename(os.path.splitext(os.path.basename(wav_path))[0] or "tts")
    mp3_path = os.path.join(tmp_dir, f"{base}.mp3")

    # Run async edge-tts safely in sync context
    asyncio.run(_edge_tts_to_mp3_async(text, mp3_path, voice, rate, volume))

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
    proc = subprocess.run(cmd, capture_output=True, text=True, **_subprocess_run_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed:\n{proc.stderr or proc.stdout}")
    return wav_path


def synthesize_text_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice: str = "vi_VN-vais1000-medium",
    speed: float = 1.0,
    tmp_dir: str | None = None,
    on_progress: callable = None,
) -> str:
    # Load voice catalog
    catalog_path = os.path.join(BASE_DIR, "voice_preview_catalog.json")
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
        model_path = os.path.join(os.getcwd(), provider_voice)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Piper model not found at {model_path}. Please download and place the model there.")
        
        return piper_tts_to_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            model_path=model_path,
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

