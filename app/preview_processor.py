import os
import subprocess


def _ffmpeg_path():
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffmpeg.exe")


def mux_audio_into_video_for_preview(video_path: str, audio_path: str, output_video_path: str) -> str:
    """
    Create a preview video by copying video stream and replacing audio.
    This is meant for quick local preview (temp file), not final export.
    """
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    os.makedirs(os.path.dirname(output_video_path) or ".", exist_ok=True)

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_video_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "FFmpeg mux failed.")
    return output_video_path

