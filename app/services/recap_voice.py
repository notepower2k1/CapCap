"""Project-local WAV files for edited recap narration."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import wave


_AUDIO_DIR = "analysis/recap_audio"


def narration_digest(text):
    return sha256(text.strip().encode("utf-8")).hexdigest()


def audio_path(project_root, audio):
    if not isinstance(audio, dict) or not isinstance(audio.get("path"), str):
        return None
    try:
        directory = (Path(project_root) / _AUDIO_DIR).resolve()
        path = (Path(project_root) / audio["path"]).resolve()
        if path.parent == directory and path.suffix == ".wav" and path.is_file():
            return path
    except (OSError, ValueError):
        pass
    return None


def current_clip_path(project_root, segment, voice_id):
    audio = segment.get("audio")
    if (not isinstance(audio, dict) or audio.get("voice_id") != voice_id
            or audio.get("text_digest") != narration_digest(segment["narration"])
            or type(audio.get("duration_ms")) is not int or audio["duration_ms"] <= 0):
        return None
    return audio_path(project_root, audio)


def synthesize_recap_segment(engine, project_root, index, text, voice_id, *,
                             model_id, normalizer_dictionary, is_canceled):
    directory = Path(project_root) / _AUDIO_DIR
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="recap-", dir=directory, ignore_cleanup_errors=True) as temporary:
        staging = Path(temporary) / "narration.wav"
        engine.synthesize_segment(
            text=text, wav_path=str(staging), voice=voice_id, speed=1.0, language_code="vi",
            tmp_dir=temporary, model_id=model_id, normalizer_dictionary=normalizer_dictionary,
        )
        if is_canceled():
            raise InterruptedError()
        with wave.open(str(staging), "rb") as audio:
            duration_ms = round(audio.getnframes() * 1000 / audio.getframerate())
        if duration_ms <= 0:
            raise ValueError("The generated recap clip has no audio.")
        destination = directory / f"segment-{index + 1}-{uuid4().hex}.wav"
        staging.replace(destination)
    return {"path": destination.relative_to(project_root).as_posix(), "duration_ms": duration_ms,
            "voice_id": voice_id, "text_digest": narration_digest(text)}
