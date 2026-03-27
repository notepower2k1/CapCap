import asyncio
import os
import re
import subprocess
import time

import requests
from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(os.path.dirname(BASE_DIR), ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)


def _ffmpeg_path():
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffmpeg.exe")


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


def _humanize_zalo_error(*, status_code: int | None = None, payload: dict | None = None, fallback: str = "") -> str:
    payload = payload or {}
    error_code = int(payload.get("error_code", -1)) if payload else -1
    error_message = str(payload.get("error_message", "")).strip() if payload else ""
    combined = f"{status_code or ''} {error_code} {error_message} {fallback}".lower()

    if error_code == 155 or "allowed limit of 2000 characters" in combined:
        return "Zalo TTS rejected the request because the text is too long. Please shorten the subtitle line or split it into smaller segments."
    if status_code == 401 or error_code == 401 or "wrong apikey" in combined:
        return "Zalo TTS rejected the API key. Please check ZALO_API_KEY."
    if status_code == 429 or "rate limit" in combined or "quota" in combined or "limit" in combined or "insufficient" in combined:
        return "Zalo TTS usage limit has been reached for this API key. Please check your Zalo AI quota or try again later."
    if status_code == 403 or "forbidden" in combined:
        return "Zalo TTS denied access for this request. Please verify your API key permissions."
    if status_code == 500 or error_code == 500:
        return "Zalo TTS returned an internal server error. Please try again in a moment."
    return error_message or fallback or "Zalo TTS request failed."


def _humanize_elevenlabs_error(*, status_code: int | None = None, payload: dict | None = None, fallback: str = "") -> str:
    payload = payload or {}
    detail = payload.get("detail", {})
    if isinstance(detail, dict):
        error_message = str(detail.get("message", "")).strip()
    else:
        error_message = str(detail or payload.get("message", "")).strip()
    combined = f"{status_code or ''} {error_message} {fallback}".lower()

    if status_code == 401 or "api key" in combined or "unauthorized" in combined:
        return "ElevenLabs rejected the API key. Please check ELEVENLABS_API_KEY."
    if status_code == 429 or "quota" in combined or "credit" in combined or "limit" in combined:
        return "ElevenLabs usage limit has been reached for this API key. Please check your ElevenLabs quota or billing."
    if status_code == 422:
        return error_message or "ElevenLabs rejected the request payload. Please verify the selected voice and text."
    if status_code == 400:
        return error_message or "ElevenLabs rejected the request. Please verify the selected voice ID and model settings."
    if status_code == 500:
        return "ElevenLabs returned an internal server error. Please try again in a moment."
    return error_message or fallback or "ElevenLabs request failed."


def _resolve_env_or_literal(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    return os.getenv(candidate, candidate).strip()


def _download_with_retry(url: str, output_path: str, *, timeout: int = 120, attempts: int = 8) -> str:
    last_error = ""
    session = requests.Session()
    headers = {
        "User-Agent": "CapCap/1.0",
        "Accept": "*/*",
    }
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, headers=headers, timeout=timeout, stream=True)
            if response.status_code == 200:
                with open(output_path, "wb") as output_file:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            output_file.write(chunk)
                if os.path.getsize(output_path) > 0:
                    return output_path
                last_error = "Downloaded audio file is empty."
            else:
                last_error = f"{response.status_code} {response.reason}"
        except requests.RequestException as exc:
            last_error = str(exc)

        if attempt < attempts:
            time.sleep(min(4.0, 0.6 * attempt))

    raise RuntimeError(f"Failed to download generated Zalo audio after {attempts} attempts: {last_error}")


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


def zalo_tts_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    speaker_id: str = "1",
    speed: float = 1.0,
    tmp_dir: str | None = None,
    quality: int = 0,
) -> str:
    api_key = os.getenv("ZALO_API_KEY") or os.getenv("ZALO_TTS_API_KEY")
    if not api_key:
        raise RuntimeError("Missing Zalo TTS API key. Please set ZALO_API_KEY in your environment or .env file.")

    if tmp_dir is None:
        tmp_dir = os.path.join(os.getcwd(), "temp")
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    base = _sanitize_filename(os.path.splitext(os.path.basename(wav_path))[0] or "tts")
    download_mp3_path = os.path.join(tmp_dir, f"{base}_zalo.mp3")
    request_speed = max(0.8, min(1.2, float(speed)))

    response = requests.post(
        "https://api.zalo.ai/v1/tts/synthesize",
        headers={"apikey": api_key},
        data={
            "input": text,
            "speaker_id": str(speaker_id),
            "speed": f"{request_speed:.2f}",
            "quality": str(int(quality)),
            "encode_type": "1",
        },
        timeout=60,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        raise RuntimeError(_humanize_zalo_error(status_code=response.status_code, payload=payload, fallback=response.text[:200]))
    if int(payload.get("error_code", -1)) != 0:
        raise RuntimeError(_humanize_zalo_error(status_code=response.status_code, payload=payload))

    audio_url = (((payload.get("data") or {}).get("url")) or "").strip()
    if not audio_url:
        raise RuntimeError("Zalo TTS did not return an audio URL.")

    try:
        _download_with_retry(audio_url, download_mp3_path)
    except Exception as exc:
        raise RuntimeError(_humanize_zalo_error(fallback=str(exc))) from exc

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        download_mp3_path,
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


def elevenlabs_tts_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice_id: str,
    speed: float = 1.0,
    tmp_dir: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
) -> str:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ElevenLabs API key. Please set ELEVENLABS_API_KEY in your environment or .env file.")

    resolved_voice_id = _resolve_env_or_literal(voice_id)
    if not resolved_voice_id:
        raise RuntimeError("Missing ElevenLabs voice ID. Please set the configured ELEVENLABS_VOICE_ID_* environment variable.")

    if tmp_dir is None:
        tmp_dir = os.path.join(os.getcwd(), "temp")
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    base = _sanitize_filename(os.path.splitext(os.path.basename(wav_path))[0] or "tts")
    download_mp3_path = os.path.join(tmp_dir, f"{base}_eleven.mp3")
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{resolved_voice_id}",
        headers={
            "xi-api-key": api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": model_id,
            "language_code": "vi",
            "output_format": "mp3_44100_128",
        },
        timeout=120,
    )

    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        raise RuntimeError(_humanize_elevenlabs_error(status_code=response.status_code, payload=payload, fallback=response.text[:200]))

    with open(download_mp3_path, "wb") as audio_file:
        audio_file.write(response.content)

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        download_mp3_path,
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


def synthesize_text_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice: str = "vi-VN-HoaiMyNeural",
    speed: float = 1.0,
    tmp_dir: str | None = None,
) -> str:
    provider, voice_id = _voice_provider_and_id(voice)
    speed_value = _speed_to_float(speed)
    if provider == "zalo":
        return zalo_tts_to_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            speaker_id=voice_id or "1",
            speed=speed_value,
            tmp_dir=tmp_dir,
        )
    if provider == "eleven":
        return elevenlabs_tts_to_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            voice_id=voice_id,
            speed=speed_value,
            tmp_dir=tmp_dir,
        )

    edge_rate_percent = int(round((speed_value - 1.0) * 100.0))
    edge_rate = f"{edge_rate_percent:+d}%"
    return edge_tts_to_wav_16k_mono(
        text=text,
        wav_path=wav_path,
        voice=voice_id or "vi-VN-HoaiMyNeural",
        rate=edge_rate,
        tmp_dir=tmp_dir,
    )

