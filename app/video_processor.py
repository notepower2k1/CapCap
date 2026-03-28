import subprocess
import os
import re

from highlight_selector import find_highlights


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


def _build_blur_filter_chain(blur_region, video_width, video_height):
    if not isinstance(blur_region, dict):
        return ""
    try:
        x_norm = float(blur_region.get("x", 0.0))
        y_norm = float(blur_region.get("y", 0.0))
        w_norm = float(blur_region.get("width", 0.0))
        h_norm = float(blur_region.get("height", 0.0))
    except (TypeError, ValueError):
        return ""

    if w_norm <= 0 or h_norm <= 0 or video_width <= 0 or video_height <= 0:
        return ""

    x = max(0, min(video_width - 2, int(round(x_norm * video_width))))
    y = max(0, min(video_height - 2, int(round(y_norm * video_height))))
    w = max(16, min(video_width - x, int(round(w_norm * video_width))))
    h = max(16, min(video_height - y, int(round(h_norm * video_height))))
    return (
        f"split[main][tmp];"
        f"[tmp]crop=w={w}:h={h}:x={x}:y={y},boxblur=20:3[blur];"
        f"[main][blur]overlay={x}:{y}"
    )


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


def _srt_time_to_seconds(ts: str) -> float:
    ts = ts.strip()
    h, m, rest = ts.split(':')
    s, ms = rest.split(',')
    return (int(h) * 3600) + (int(m) * 60) + int(s) + (int(ms) / 1000.0)


def _seconds_to_ass(seconds: float) -> str:
    total_cs = max(0, int(round(float(seconds) * 100.0)))
    hrs, remainder = divmod(total_cs, 360000)
    mins, remainder = divmod(remainder, 6000)
    secs, cs = divmod(remainder, 100)
    return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"


def _alignment_anchor_position(video_width: int, video_height: int, alignment: int, margin_v: int):
    margin_x = 60
    horizontal = ((alignment - 1) % 3) + 1
    vertical = (alignment - 1) // 3

    if horizontal == 1:
        x = margin_x
    elif horizontal == 2:
        x = video_width // 2
    else:
        x = video_width - margin_x

    if vertical == 0:
        y = max(margin_v, video_height - margin_v)
    elif vertical == 1:
        y = video_height // 2
    else:
        y = margin_v
    return x, y


def _build_typewriter_text(text: str, duration_seconds: float) -> str:
    safe_text = (text or "").replace("\n", "\\N")
    visible_chars = [char for char in safe_text if char not in "{}"]
    total_chars = max(1, len(visible_chars))
    total_cs = max(18, int(round(max(duration_seconds, 0.3) * 100)))
    step_cs = max(4, total_cs // total_chars)

    current_start = 0
    rendered = []
    for char in safe_text:
        if char in "{}":
            rendered.append(char)
            continue
        current_end = min(total_cs, current_start + step_cs)
        rendered.append(r"{\alpha&HFF&\t(" + f"{current_start * 10},{current_end * 10}" + r",\alpha&H00&)}" + char)
        current_start = current_end
    return "".join(rendered)


def _ms(value: float) -> int:
    return max(1, int(round(value)))


def _apply_animation_tags(
    text: str,
    *,
    animation_style: str,
    video_width: int,
    video_height: int,
    alignment: int,
    margin_v: int,
    font_size: int,
    duration_seconds: float,
    animation_duration: float,
) -> str:
    safe_text = (text or "").replace("\n", "\\N")
    style = (animation_style or "Static").strip().lower()
    total_ms = _ms(max(0.05, animation_duration) * 1000.0)
    if style == "pop in":
        return rf"{{\fscx118\fscy118\t(0,{total_ms},\fscx100\fscy100)}}" + safe_text
    if style == "fade in":
        return rf"{{\fad({_ms(total_ms * 0.8)},{_ms(total_ms * 0.2)})}}" + safe_text
    if style == "fade out":
        return rf"{{\fad({_ms(total_ms * 0.2)},{_ms(total_ms * 0.8)})}}" + safe_text
    if style == "pulse":
        midpoint = _ms(total_ms * 0.5)
        return rf"{{\t(0,{midpoint},\fscx105\fscy105)\t({midpoint},{total_ms},\fscx100\fscy100)}}" + safe_text
    if style == "background appear":
        return rf"{{\fad({_ms(total_ms * 0.85)},{_ms(total_ms * 0.15)})}}" + safe_text
    if style == "typewriter":
        return _build_typewriter_text(text or "", min(duration_seconds, max(0.15, animation_duration)))
    if style == "slide up":
        x, y = _alignment_anchor_position(video_width, video_height, alignment, margin_v)
        start_y = y + max(24, int(font_size * 0.7))
        fade_in = _ms(total_ms * 0.35)
        fade_out = _ms(total_ms * 0.2)
        return rf"{{\move({x},{start_y},{x},{y},0,{total_ms})\fad({fade_in},{fade_out})}}{safe_text}"
    return safe_text


def _ass_escape_text(text: str) -> str:
    return (text or "").replace("\n", "\\N")


def _weighted_word_spans(text: str) -> list[tuple[int, int]]:
    source_text = text or ""
    return [(match.start(), match.end()) for match in re.finditer(r"\S+", source_text)]


def _word_weight(text: str) -> int:
    cleaned = re.sub(r"[^\w]+", "", str(text or ""), flags=re.UNICODE)
    return max(1, len(cleaned) or len(str(text or "").strip()) or 1)


def _normalize_word_timings(word_entries, segment_start: float, segment_end: float) -> list[dict]:
    normalized = []
    max_duration = max(0.0, segment_end - segment_start)
    for entry in word_entries or []:
        try:
            start = float(entry.get("start", 0.0))
            end = float(entry.get("end", 0.0))
        except (AttributeError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        text = str(entry.get("text", "") or "").strip()
        if not text:
            continue
        rel_start = max(0.0, start - segment_start)
        rel_end = min(max_duration, end - segment_start)
        if rel_end <= rel_start:
            continue
        normalized.append(
            {
                "text": text,
                "start_rel": rel_start,
                "end_rel": rel_end,
                "weight": _word_weight(text),
            }
        )
    return normalized


def _interpolate_progress_time(progress: float, checkpoints: list[float], times: list[float]) -> float:
    if not checkpoints or not times or len(checkpoints) != len(times):
        return 0.0
    if progress <= checkpoints[0]:
        return times[0]
    if progress >= checkpoints[-1]:
        return times[-1]
    for idx in range(1, len(checkpoints)):
        left_progress = checkpoints[idx - 1]
        right_progress = checkpoints[idx]
        if progress > right_progress:
            continue
        left_time = times[idx - 1]
        right_time = times[idx]
        span = max(1e-6, right_progress - left_progress)
        ratio = (progress - left_progress) / span
        return left_time + ((right_time - left_time) * ratio)
    return times[-1]


def _map_target_word_timings(text: str, source_words, segment_duration: float) -> list[dict]:
    target_spans = _weighted_word_spans(text)
    if not target_spans:
        return []

    if not source_words:
        total_weight = sum(_word_weight(text[start:end]) for start, end in target_spans) or len(target_spans)
        cursor = 0.0
        mapped = []
        for idx, span in enumerate(target_spans):
            weight = _word_weight(text[span[0]:span[1]])
            if idx == len(target_spans) - 1:
                next_time = max(cursor, segment_duration)
            else:
                next_time = min(segment_duration, cursor + (segment_duration * (weight / total_weight)))
            mapped.append({"span": span, "start_rel": cursor, "end_rel": max(cursor, next_time)})
            cursor = next_time
        return mapped

    if len(source_words) == len(target_spans):
        return [
            {
                "span": span,
                "start_rel": source_words[idx]["start_rel"],
                "end_rel": source_words[idx]["end_rel"],
            }
            for idx, span in enumerate(target_spans)
        ]

    source_checkpoints = [0.0]
    source_times = [max(0.0, min(segment_duration, source_words[0]["start_rel"]))]
    cumulative_source = 0.0
    for word in source_words:
        cumulative_source += word["weight"]
        source_checkpoints.append(cumulative_source)
        source_times.append(max(0.0, min(segment_duration, word["end_rel"])))

    total_target_weight = sum(_word_weight(text[start:end]) for start, end in target_spans) or len(target_spans)
    cursor = source_times[0]
    cumulative_target = 0.0
    mapped = []
    for idx, span in enumerate(target_spans):
        cumulative_target += _word_weight(text[span[0]:span[1]])
        progress = source_checkpoints[-1] * (cumulative_target / total_target_weight)
        mapped_end = _interpolate_progress_time(progress, source_checkpoints, source_times)
        if idx == len(target_spans) - 1:
            mapped_end = max(mapped_end, source_times[-1])
        mapped_end = max(cursor, min(segment_duration, mapped_end))
        mapped.append({"span": span, "start_rel": cursor, "end_rel": mapped_end})
        cursor = mapped_end
    return mapped


def _build_karaoke_overlay_text(text: str, active_span: tuple[int, int], highlight_color: str) -> str:
    source_text = text or ""
    start, end = active_span
    before = _ass_escape_text(source_text[:start])
    active = _ass_escape_text(source_text[start:end])
    after = _ass_escape_text(source_text[end:])
    return (
        r"{\alpha&HFF&}" + before
        + r"{\alpha&H00&\c" + (highlight_color or "&H00E5FF") + r"}" + active
        + r"{\alpha&HFF&\c}" + after
    )


def _build_karaoke_dialogue_events(
    *,
    start_seconds: float,
    end_seconds: float,
    start_ass: str,
    end_ass: str,
    text: str,
    highlight_color: str,
    word_timing_entries=None,
    timing_mode: str = "vietnamese",
) -> list[str]:
    source_text = text or ""
    segment_duration = max(0.1, float(end_seconds) - float(start_seconds))
    timing_key = str(timing_mode or "vietnamese").strip().lower()
    source_words = (
        _normalize_word_timings(word_timing_entries, start_seconds, end_seconds)
        if timing_key == "source"
        else []
    )
    mapped_words = _map_target_word_timings(source_text, source_words, segment_duration)
    base_text = _ass_escape_text(source_text)
    events = [f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{base_text}"]
    if not mapped_words:
        return events

    for mapped in mapped_words:
        word_start = float(start_seconds) + float(mapped["start_rel"])
        word_end = float(start_seconds) + float(mapped["end_rel"])
        if word_end <= word_start:
            continue
        overlay_text = _build_karaoke_overlay_text(source_text, mapped["span"], highlight_color)
        events.append(
            "Dialogue: 1,"
            + f"{_seconds_to_ass(word_start)},{_seconds_to_ass(word_end)},Default,,0,0,0,,"
            + overlay_text
        )

    return events


def _build_manual_highlight_spans(text: str, manual_highlights) -> list[tuple[int, int]]:
    source_text = text or ""
    spans: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    for phrase in manual_highlights or []:
        needle = str(phrase or "").strip()
        if not needle:
            continue
        start = 0
        lowered_source = source_text.lower()
        lowered_needle = needle.lower()
        while True:
            idx = lowered_source.find(lowered_needle, start)
            if idx == -1:
                break
            span = (idx, idx + len(needle))
            if span not in seen:
                spans.append(span)
                seen.add(span)
            start = idx + len(needle)

    return sorted(spans, key=lambda item: item[0])


def _apply_keyword_highlight(text: str, *, preset_key: str, highlight_color: str, manual_highlights=None) -> str:
    source_text = text or ""
    ordered_spans = _build_manual_highlight_spans(source_text, manual_highlights)
    if not ordered_spans and (preset_key or "").strip().lower() != "highlight":
        return source_text
    if not ordered_spans:
        candidates = find_highlights(source_text, max_highlights=2)
        if not candidates:
            return source_text
        ordered_spans = [(candidate.start, candidate.end) for candidate in sorted(candidates, key=lambda item: item.start)]

    primary_color = highlight_color or "&H00E5FF"
    if primary_color.upper() == "&H00FFFFFF":
        primary_color = "&H00E5FF"
    alternate_colors = [primary_color, "&H0000D4FF"]
    highlighted = source_text
    for idx, (start, end) in reversed(list(enumerate(ordered_spans))):
        color = alternate_colors[idx % len(alternate_colors)]
        highlighted = (
            highlighted[:start]
            + r"{\c"
            + color
            + r"}"
            + highlighted[start:end]
            + r"{\c}"
            + highlighted[end:]
        )
    return highlighted


def srt_to_ass(srt_path: str,
               video_width: int, video_height: int,
               alignment: int = 2, margin_v: int = 30,
               font_name: str = "Arial", font_size: int = 18,
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
               karaoke_timing_mode: str = "vietnamese") -> str:
    """Convert an SRT file to a fully-styled ASS file.

    Key insight: by setting PlayResX/PlayResY equal to the ACTUAL video
    resolution, every pixel value (FontSize, MarginV …) is exact — no hidden
    scaling by FFmpeg's internal SRT→ASS converter.

    Returns the path of the generated .ass file (in the same folder as the
    SRT, name-suffixed with _styled).
    """
    ass_path = os.path.splitext(srt_path)[0] + "_styled.ass"

    def _with_alpha(ass_color: str, alpha_ratio: float) -> str:
        alpha = max(0, min(255, int(round((1.0 - max(0.0, min(1.0, alpha_ratio))) * 255))))
        if ass_color.startswith("&H") and len(ass_color) >= 10:
            return "&H" + f"{alpha:02X}" + ass_color[4:]
        return ass_color

    border_style = 3 if background_box else 1
    outline = 0 if background_box else float(outline_width)
    shadow = 0 if background_box else float(shadow_depth)
    back_color = _with_alpha(background_color, background_alpha) if background_box else _with_alpha(shadow_color, 0.7)
    bold_flag = -1 if bold else 0

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
        f"{font_color},{highlight_color},{outline_color},{back_color},"
        f"{bold_flag},0,0,0,100,100,0,0,{border_style},{outline},{shadow},"
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
    for event_index, m in enumerate(pattern.finditer(content.strip() + "\n\n")):
        start_seconds = _srt_time_to_seconds(m.group(1))
        end_seconds = _srt_time_to_seconds(m.group(2))
        start = _srt_time_to_ass(m.group(1))
        end   = _srt_time_to_ass(m.group(2))
        line_manual_highlights = []
        if isinstance(manual_highlights, list) and event_index < len(manual_highlights):
            line_manual_highlights = manual_highlights[event_index] or []
        line_word_timings = []
        if isinstance(word_timings, list) and event_index < len(word_timings):
            line_word_timings = word_timings[event_index] or []
        raw_text = m.group(3).strip()
        style_key = (animation_style or "Static").strip().lower()
        if style_key == "word highlight karaoke":
            events.extend(
                _build_karaoke_dialogue_events(
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    start_ass=start,
                    end_ass=end,
                    text=raw_text,
                    highlight_color=highlight_color,
                    word_timing_entries=line_word_timings,
                    timing_mode=karaoke_timing_mode,
                )
            )
            continue

        text_content = _apply_keyword_highlight(
            raw_text,
            preset_key="highlight" if auto_keyword_highlight else preset_key,
            highlight_color=highlight_color,
            manual_highlights=line_manual_highlights,
        )
        text = _apply_animation_tags(
            text_content,
            animation_style=animation_style,
            video_width=video_width,
            video_height=video_height,
            alignment=alignment,
            margin_v=margin_v,
            font_size=font_size,
            duration_seconds=max(0.1, end_seconds - start_seconds),
            animation_duration=animation_duration,
        )
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    with open(ass_path, 'w', encoding='utf-8-sig') as f:
        f.write(header)
        f.write('\n'.join(events) + '\n')

    print(f"Generated ASS: {ass_path}  ({len(events)} lines, "
          f"{video_width}x{video_height}, alignment={alignment}, marginV={margin_v}, animation={animation_style}, preset={preset_key or 'custom'})")
    return ass_path


def embed_ass_subtitles(video_path, ass_path, output_path, ffmpeg_path=None, blur_region=None):
    """Burn subtitles into video using an already-prepared ASS file."""
    ffmpeg = _ffmpeg_path(ffmpeg_path)
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
    if not os.path.exists(ass_path):
        raise FileNotFoundError(f"ASS subtitle file not found at {ass_path}")

    escaped_ass = _escape_path_for_filter(ass_path)
    video_w, video_h = get_video_dimensions(video_path)
    blur_chain = _build_blur_filter_chain(blur_region, video_w, video_h)
    filter_complex = f"{blur_chain},ass='{escaped_ass}'" if blur_chain else f"ass='{escaped_ass}'"

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
                    animation_style="Static",
                    highlight_color="&H00FFFFFF",
                    outline_color="&H00000000",
                    outline_width=2.0,
                    shadow_color="&H80000000",
                    shadow_depth=1.0,
                    background_color="&H80000000",
                    background_alpha=0.5,
                    bold=False,
                    preset_key="",
                    auto_keyword_highlight=False,
                    animation_duration=0.22,
                    manual_highlights=None,
                    word_timings=None,
                    karaoke_timing_mode="vietnamese",
                    blur_region=None,
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
    )

    success = embed_ass_subtitles(video_path, ass_path, output_path, ffmpeg_path=ffmpeg, blur_region=blur_region)

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
