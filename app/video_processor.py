import subprocess
import os
import re


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ffmpeg_path(override=None):
    if override:
        return override
    return os.path.join(os.getcwd(), 'bin', 'ffmpeg', 'ffmpeg.exe')


def _ffprobe_path():
    return os.path.join(os.getcwd(), 'bin', 'ffmpeg', 'ffprobe.exe')


def _escape_path_for_filter(path):
    """Escape a file path for use inside an FFmpeg -vf filter value."""
    clean = path.replace("\\", "/")
    if ":" in clean:
        drive, rest = clean.split(":", 1)
        return f"{drive}\\:{rest}"
    return clean


def get_video_dimensions(video_path):
    """Return (width, height) of the first video stream using ffprobe.
    Falls back to (1920, 1080) if ffprobe is unavailable or fails.
    """
    ffprobe = _ffprobe_path()
    if not os.path.exists(ffprobe):
        print("ffprobe not found — using default resolution 1920x1080")
        return 1920, 1080
    try:
        result = subprocess.run(
            [ffprobe, '-v', 'error',
             '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height',
             '-of', 'csv=s=x:p=0',
             video_path],
            capture_output=True, text=True, check=True
        )
        w, h = result.stdout.strip().split('x')
        return int(w), int(h)
    except Exception as e:
        print(f"ffprobe failed ({e}) — using default 1920x1080")
        return 1920, 1080


# ---------------------------------------------------------------------------
# SRT → ASS conversion
# ---------------------------------------------------------------------------

def _srt_time_to_ass(ts: str) -> str:
    """Convert  HH:MM:SS,mmm  →  H:MM:SS.cc  (ASS centisecond format)."""
    ts = ts.strip()
    h, m, rest = ts.split(':')
    s, ms = rest.split(',')
    cs = int(ms) // 10
    return f"{int(h)}:{m}:{s}.{cs:02d}"


def srt_to_ass(srt_path: str,
               video_width: int, video_height: int,
               alignment: int = 2, margin_v: int = 30,
               font_name: str = "Arial", font_size: int = 18,
               font_color: str = "&H00FFFFFF",
               background_box: bool = False) -> str:
    """Convert an SRT file to a fully-styled ASS file.

    Key insight: by setting PlayResX/PlayResY equal to the ACTUAL video
    resolution, every pixel value (FontSize, MarginV …) is exact — no hidden
    scaling by FFmpeg's internal SRT→ASS converter.

    Returns the path of the generated .ass file (in the same folder as the
    SRT, name-suffixed with _styled).
    """
    ass_path = os.path.splitext(srt_path)[0] + "_styled.ass"

    # ASS script / style header
    border_style = 3 if background_box else 1
    outline = 0 if background_box else 2
    shadow = 0 if background_box else 1
    back_color = "&H80000000" if background_box else "&H80000000"

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 1\n"          # smart word-level wrapping
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},"
        f"{font_color},&H000000FF,&H00000000,{back_color},"
        f"-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},"
        f"{alignment},60,60,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # Parse SRT entries
    with open(srt_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    pattern = re.compile(
        r'\d+\s*\n'
        r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n'
        r'(.*?)(?=\n\s*\n\s*\d|\s*$)',
        re.DOTALL
    )

    events = []
    for m in pattern.finditer(content.strip() + "\n\n"):
        start = _srt_time_to_ass(m.group(1))
        end   = _srt_time_to_ass(m.group(2))
        text  = m.group(3).strip().replace('\n', '\\N')
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    with open(ass_path, 'w', encoding='utf-8-sig') as f:
        f.write(header)
        f.write('\n'.join(events) + '\n')

    print(f"Generated ASS: {ass_path}  ({len(events)} lines, "
          f"{video_width}x{video_height}, alignment={alignment}, marginV={margin_v})")
    return ass_path


def embed_ass_subtitles(video_path, ass_path, output_path, ffmpeg_path=None):
    """Burn subtitles into video using an already-prepared ASS file."""
    ffmpeg = _ffmpeg_path(ffmpeg_path)
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
    if not os.path.exists(ass_path):
        raise FileNotFoundError(f"ASS subtitle file not found at {ass_path}")

    escaped_ass = _escape_path_for_filter(ass_path)
    filter_complex = f"ass='{escaped_ass}'"

    command = [
        ffmpeg,
        '-i', video_path,
        '-vf', filter_complex,
        '-c:a', 'aac', '-b:a', '128k',
        '-y',
        output_path
    ]
    print(f"Executing: {' '.join(command)}")

    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
        print("ASS subtitles embedded successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error during ASS embedding:\n{e.stderr}")
        return False


# ---------------------------------------------------------------------------
# Main public functions
# ---------------------------------------------------------------------------

def extract_audio(video_path, audio_output_path, ffmpeg_path=None):
    """Extract audio from a video file using FFmpeg → WAV 16 kHz mono."""
    ffmpeg = _ffmpeg_path(ffmpeg_path)
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    command = [
        ffmpeg, '-i', video_path,
        '-ar', '16000', '-ac', '1',
        '-y', audio_output_path
    ]
    print(f"Executing: {' '.join(command)}")
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
        print("Audio extraction successful.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error during audio extraction: {e.stderr}")
        return False


def embed_subtitles(video_path, srt_path, output_path,
                    alignment=2, margin_v=30,
                    font_name="Arial", font_size=18, font_color="&H00FFFFFF",
                    background_box=False,
                    ffmpeg_path=None):
    """Burn subtitles into video using a properly-styled ASS file.

    Workflow:
    1. Query actual video resolution with ffprobe.
    2. Convert SRT → ASS with PlayRes = video resolution (pixel-accurate).
    3. Apply with FFmpeg's  ass=  filter (no force_style hacks).
    4. Remove the temporary ASS after success.
    """
    ffmpeg = _ffmpeg_path(ffmpeg_path)
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    # Step 1: get real video resolution
    video_w, video_h = get_video_dimensions(video_path)

    # Step 2: generate ASS
    ass_path = srt_to_ass(
        srt_path, video_w, video_h,
        alignment=alignment, margin_v=margin_v,
        font_name=font_name, font_size=font_size, font_color=font_color,
        background_box=background_box,
    )

    success = embed_ass_subtitles(video_path, ass_path, output_path, ffmpeg_path=ffmpeg)

    # Step 4: clean up temp ASS
    if os.path.exists(ass_path):
        try:
            os.remove(ass_path)
        except OSError:
            pass

    return success


if __name__ == "__main__":
    test_video = "test.mp4"
    test_audio = os.path.join("temp", "test_audio.wav")
    if os.path.exists(test_video):
        extract_audio(test_video, test_audio)
    else:
        print(f"Test video '{test_video}' not found.")
