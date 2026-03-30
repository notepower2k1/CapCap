import os
import subprocess
import wave


def _ffmpeg_path():
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffmpeg.exe")


def _ffprobe_path():
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffprobe.exe")


def _subprocess_run_kwargs() -> dict:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _probe_wav_duration_seconds(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wav_file:
        frame_rate = wav_file.getframerate() or 16000
        frame_count = wav_file.getnframes()
    return max(0.0, float(frame_count) / float(frame_rate))


def _build_atempo_filter(speed_ratio: float) -> str:
    ratio = max(0.01, float(speed_ratio))
    filters = []
    while ratio < 0.5 or ratio > 2.0:
        if ratio < 0.5:
            filters.append("atempo=0.5")
            ratio /= 0.5
        else:
            filters.append("atempo=2.0")
            ratio /= 2.0
    filters.append(f"atempo={ratio:.6f}")
    return ",".join(filters)


def fit_wav_to_duration(
    *,
    input_wav_path: str,
    output_wav_path: str,
    target_duration_seconds: float,
    mode: str = "off",
    smart_min_ratio: float = 0.55,
    smart_max_ratio: float = 1.25,
) -> str:
    mode_key = (mode or "off").strip().lower()
    if mode_key == "force fit":
        mode_key = "force"
    if mode_key not in {"smart", "force"}:
        return input_wav_path
    if not os.path.exists(input_wav_path):
        raise FileNotFoundError(f"Input wav not found: {input_wav_path}")

    source_duration = _probe_wav_duration_seconds(input_wav_path)
    target_duration = max(0.0, float(target_duration_seconds))
    if source_duration <= 0.0 or target_duration <= 0.0:
        return input_wav_path

    fit_ratio = target_duration / source_duration
    if abs(fit_ratio - 1.0) < 0.02:
        return input_wav_path
    if mode_key == "smart":
        # In Smart mode we compress long clips more aggressively so subtitles
        # are less likely to jump while the old sentence is still speaking.
        if fit_ratio < 1.0 and fit_ratio < smart_min_ratio:
            return input_wav_path
        if fit_ratio > 1.0 and fit_ratio > smart_max_ratio:
            return input_wav_path

    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    filter_chain = _build_atempo_filter(1.0 / fit_ratio)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        input_wav_path,
        "-filter:a",
        filter_chain,
        "-ar",
        "16000",
        "-ac",
        "1",
        output_wav_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, **_subprocess_run_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg time-stretch failed:\n{proc.stderr or proc.stdout}")
    return output_wav_path


def change_wav_speed(
    *,
    input_wav_path: str,
    output_wav_path: str,
    speed_ratio: float,
) -> str:
    if not os.path.exists(input_wav_path):
        raise FileNotFoundError(f"Input wav not found: {input_wav_path}")

    ratio = max(0.01, float(speed_ratio))
    if abs(ratio - 1.0) < 0.02:
        return input_wav_path

    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    filter_chain = _build_atempo_filter(ratio)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        input_wav_path,
        "-filter:a",
        filter_chain,
        "-ar",
        "16000",
        "-ac",
        "1",
        output_wav_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, **_subprocess_run_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg speed adjustment failed:\n{proc.stderr or proc.stdout}")
    return output_wav_path


def _require_pydub():
    try:
        ffmpeg = _ffmpeg_path()
        ffprobe = _ffprobe_path()
        ffmpeg_dir = os.path.dirname(ffmpeg)
        if ffmpeg_dir and os.path.isdir(ffmpeg_dir):
            current_path = os.environ.get("PATH", "")
            path_entries = current_path.split(os.pathsep) if current_path else []
            normalized_dir = os.path.normcase(os.path.normpath(ffmpeg_dir))
            normalized_entries = {
                os.path.normcase(os.path.normpath(entry))
                for entry in path_entries
                if entry
            }
            if normalized_dir not in normalized_entries:
                os.environ["PATH"] = ffmpeg_dir + os.pathsep + current_path if current_path else ffmpeg_dir

        from pydub import AudioSegment
        # Point pydub to our bundled ffmpeg to avoid PATH warnings on Windows.
        if os.path.exists(ffmpeg):
            AudioSegment.converter = ffmpeg
            AudioSegment.ffmpeg = ffmpeg
        if os.path.exists(ffprobe):
            AudioSegment.ffprobe = ffprobe
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

