import os


def _ffmpeg_path():
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffmpeg.exe")


def _require_pydub():
    try:
        from pydub import AudioSegment
        # Point pydub to our bundled ffmpeg to avoid PATH warnings on Windows.
        ffmpeg = _ffmpeg_path()
        if os.path.exists(ffmpeg):
            AudioSegment.converter = ffmpeg
    except Exception as e:
        raise ImportError(
            "Missing dependency 'pydub'.\n"
            "Please run:\n"
            "python -m pip install pydub\n"
            f"Original error: {e}"
        ) from e


def build_voice_track_from_srt_segments(
    *,
    segments: list,
    tts_wav_paths: list,
    output_wav_path: str,
    total_duration_ms: int | None = None,
    gain_db: float = 0.0,
) -> str:
    """
    Build a single voice track by overlaying each segment wav at its start time.

    segments: list of dicts {start: seconds, end: seconds, text: str}
    tts_wav_paths: list of wav paths aligned to segments index
    """
    _require_pydub()
    from pydub import AudioSegment

    if len(segments) != len(tts_wav_paths):
        raise ValueError("segments and tts_wav_paths length mismatch")

    if total_duration_ms is None:
        max_end = 0.0
        for seg in segments:
            max_end = max(max_end, float(seg.get("end", 0.0)))
        total_duration_ms = int(max_end * 1000) + 500

    base = AudioSegment.silent(duration=max(0, total_duration_ms), frame_rate=16000).set_channels(1)

    for seg, wav_path in zip(segments, tts_wav_paths):
        if not wav_path or not os.path.exists(wav_path):
            continue
        start_ms = int(float(seg.get("start", 0.0)) * 1000)
        end_ms = int(float(seg.get("end", 0.0)) * 1000)
        max_len = max(0, end_ms - start_ms)

        clip = AudioSegment.from_file(wav_path)
        clip = clip.set_frame_rate(16000).set_channels(1)
        if gain_db:
            clip = clip + gain_db
        if max_len > 0:
            clip = clip[:max_len]
        base = base.overlay(clip, position=max(0, start_ms))

    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    base.export(output_wav_path, format="wav")
    return output_wav_path


def mix_voice_with_background(
    *,
    background_wav_path: str,
    voice_wav_path: str,
    output_wav_path: str,
    background_gain_db: float = 0.0,
    voice_gain_db: float = 0.0,
) -> str:
    _require_pydub()
    from pydub import AudioSegment

    if not os.path.exists(background_wav_path):
        raise FileNotFoundError(f"Background file not found: {background_wav_path}")
    if not os.path.exists(voice_wav_path):
        raise FileNotFoundError(f"Voice file not found: {voice_wav_path}")

    bg = AudioSegment.from_file(background_wav_path).set_frame_rate(16000).set_channels(1)
    vc = AudioSegment.from_file(voice_wav_path).set_frame_rate(16000).set_channels(1)

    if background_gain_db:
        bg = bg + background_gain_db
    if voice_gain_db:
        vc = vc + voice_gain_db

    # Ensure output covers the longer one
    if len(vc) > len(bg):
        bg = bg + AudioSegment.silent(duration=(len(vc) - len(bg)), frame_rate=16000)
    elif len(bg) > len(vc):
        vc = vc + AudioSegment.silent(duration=(len(bg) - len(vc)), frame_rate=16000)

    mixed = bg.overlay(vc)
    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    mixed.export(output_wav_path, format="wav")
    return output_wav_path

