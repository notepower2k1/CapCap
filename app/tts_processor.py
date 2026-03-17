import asyncio
import os
import re
import subprocess


def _ffmpeg_path():
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffmpeg.exe")


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip()
    return name[:120] if len(name) > 120 else name


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
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed:\n{proc.stderr or proc.stdout}")

    return wav_path

