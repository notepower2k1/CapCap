import os
import subprocess

from runtime_paths import bin_path


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


def trim_video_clip(video_path: str, output_video_path: str, start_seconds: float, duration_seconds: float) -> str:
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    os.makedirs(os.path.dirname(output_video_path) or ".", exist_ok=True)

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(max(0.0, float(start_seconds))),
        "-t",
        str(max(0.1, float(duration_seconds))),
        "-i",
        video_path,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        output_video_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, **_subprocess_run_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "FFmpeg trim video clip failed.")
    return output_video_path


def mux_audio_into_video_for_preview(
    video_path: str,
    audio_path: str,
    output_video_path: str,
    *,
    target_width=None,
    target_height=None,
    output_fps=None,
) -> str:
    """Create a video by replacing audio.

    - When no target size is provided, this keeps video stream copy for speed.
    - When target size is provided, video is re-encoded and scaled/padded.

    This is used for quick local preview and also for voice-only final export.
    """
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    os.makedirs(os.path.dirname(output_video_path) or ".", exist_ok=True)

    vf = ""
    try:
        if target_width and target_height:
            tw = int(target_width)
            th = int(target_height)
            if tw > 0 and th > 0:
                vf = (
                    f"scale=w={tw}:h={th}:force_original_aspect_ratio=decrease,"
                    f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2"
                )
    except Exception:
        vf = ""
    fps_value = None
    try:
        if output_fps:
            parsed_fps = int(float(output_fps))
            if parsed_fps > 0:
                fps_value = parsed_fps
    except Exception:
        fps_value = None

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]

    if vf:
        cmd += [
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        if fps_value:
            cmd += [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
            ]
        else:
            cmd += ["-c:v", "copy"]

    if fps_value:
        cmd += ["-r", str(fps_value)]

    cmd += [
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        output_video_path,
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, **_subprocess_run_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "FFmpeg mux failed.")
    return output_video_path

def mux_audio_into_video_clip_for_preview(
    video_path: str,
    audio_path: str,
    output_video_path: str,
    start_seconds: float,
    duration_seconds: float,
    target_width=None,
    target_height=None,
) -> str:
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    os.makedirs(os.path.dirname(output_video_path) or ".", exist_ok=True)

    vf = ""
    try:
        if target_width and target_height:
            tw = int(target_width)
            th = int(target_height)
            if tw > 0 and th > 0:
                vf = (
                    f"scale=w={tw}:h={th}:force_original_aspect_ratio=decrease,"
                    f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2"
                )
    except Exception:
        vf = ""

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(max(0.0, float(start_seconds))),
        "-t",
        str(max(0.1, float(duration_seconds))),
        "-i",
        video_path,
        "-ss",
        str(max(0.0, float(start_seconds))),
        "-t",
        str(max(0.1, float(duration_seconds))),
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    if vf:
        cmd += [
            "-vf",
            vf,
        ]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        output_video_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, **_subprocess_run_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "FFmpeg mux clip failed.")
    return output_video_path


def render_subtitle_frame_preview(
    video_path: str,
    srt_path: str,
    output_image_path: str,
    timestamp_seconds: float,
    *,
    alignment: int = 2,
    margin_v: int = 30,
    font_name: str = "Arial",
    font_size: int = 18,
    font_color: str = "&H00FFFFFF",
    background_box: bool = False,
    animation_style: str = "Static",
    highlight_color: str = "&H00FFFFFF",
    outline_color: str = "&H00000000",
    outline_width: float = 2.0,
    shadow_color: str = "&H80000000",
    shadow_depth: float = 1.0,
    background_color: str = "&H80000000",
    background_alpha: float = 0.5,
    bold: bool = False,
    preset_key: str = "",
    auto_keyword_highlight: bool = False,
    animation_duration: float = 0.22,
    manual_highlights=None,
    word_timings=None,
    karaoke_timing_mode: str = "vietnamese",
    custom_position_enabled: bool = False,
    custom_position_x: float = 50.0,
    custom_position_y: float = 86.0,
    single_line: bool = False,
) -> str:
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(srt_path):
        raise FileNotFoundError(f"SRT not found: {srt_path}")

    from video_processor import get_video_dimensions, srt_to_ass

    os.makedirs(os.path.dirname(output_image_path) or ".", exist_ok=True)
    video_w, video_h = get_video_dimensions(video_path)
    ass_path = srt_to_ass(
        srt_path,
        video_w,
        video_h,
        alignment=alignment,
        margin_v=margin_v,
        font_name=font_name,
        font_size=font_size,
        font_color=font_color,
        background_box=background_box,
        animation_style=animation_style,
        highlight_color=highlight_color,
        outline_color=outline_color,
        outline_width=outline_width,
        shadow_color=shadow_color,
        shadow_depth=shadow_depth,
        background_color=background_color,
        background_alpha=background_alpha,
        bold=bold,
        preset_key=preset_key,
        auto_keyword_highlight=auto_keyword_highlight,
        animation_duration=animation_duration,
        manual_highlights=manual_highlights,
        word_timings=word_timings,
        karaoke_timing_mode=karaoke_timing_mode,
        custom_position_enabled=custom_position_enabled,
        custom_position_x=custom_position_x,
        custom_position_y=custom_position_y,
        single_line=single_line,
    )

    escaped_ass = ass_path.replace("\\", "/")
    if ":" in escaped_ass:
        drive, rest = escaped_ass.split(":", 1)
        escaped_ass = f"{drive}\\:{rest}"

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(max(0.0, float(timestamp_seconds))),
        "-i",
        video_path,
        "-vf",
        f"ass='{escaped_ass}'",
        "-frames:v",
        "1",
        output_image_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, **_subprocess_run_kwargs())
    try:
        if os.path.exists(ass_path):
            os.remove(ass_path)
    except OSError:
        pass
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "FFmpeg frame preview render failed.")
    return output_image_path


