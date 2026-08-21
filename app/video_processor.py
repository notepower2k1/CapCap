import subprocess
import os
import re
import math
import hashlib
import threading
from functools import lru_cache

from new_highlight_selector import auto_select_matches
from runtime_paths import asset_path, bin_path, temp_path
from video_filter_chain import (
    build_video_color_chain,
    build_video_filter_chain,
    build_video_lut_chain,
    normalize_video_filter_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ffmpeg_path(override=None):
    if override:
        return override
    return bin_path("ffmpeg", "ffmpeg.exe")


def _ffprobe_path():
    return bin_path("ffmpeg", "ffprobe.exe")


def _subprocess_run_kwargs() -> dict:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _text_subprocess_run_kwargs() -> dict:
    """Decode FFmpeg diagnostics independently of the Windows ANSI locale."""
    return {"text": True, "encoding": "utf-8", "errors": "replace", **_subprocess_run_kwargs()}


_FFMPEG_ENCODER_CACHE = {}
# libass FontSize is not a one-to-one Pillow pixel size on Windows.  In the
# current bundled libass/DirectWrite path it renders at roughly 64% of the
# Pillow pixel metric.  Apply this conversion once before measuring layouts;
# ASS itself still receives the original FontSize and ScaleX/ScaleY values.
_LIBASS_METRIC_SCALE = 0.64
_LIBASS_METRIC_CALIBRATIONS: dict[tuple, float] = {}
_LIBASS_LAYOUT_BOUNDS_CACHE: dict[str, list[tuple[int, int, int, int] | None]] = {}
_LIBASS_CUE_BOUNDS_CACHE: dict[str, tuple[int, int, int, int] | None] = {}
_LIBASS_LAYOUT_CACHE_LOCK = threading.RLock()


@lru_cache(maxsize=32)
def _subtitle_metric_font(font_name: str, pixel_size: int, bold: bool):
    """Load the same bundled face used by libass for lightweight sizing.

    This is deliberately best-effort: export still uses libass as the text
    renderer, while Pillow supplies real glyph metrics for the accompanying
    vector background instead of a character-count estimate.
    """
    try:
        from PIL import ImageFont
    except Exception:
        return None

    requested = re.sub(r"[^a-z0-9]", "", str(font_name or "").lower())
    font_dirs = [asset_path("fonts"), os.path.join(os.environ.get("WINDIR", r"C:\\Windows"), "Fonts")]
    candidates: list[tuple[int, str]] = []
    for directory in font_dirs:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for entry in entries:
            if not entry.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            stem = re.sub(r"[^a-z0-9]", "", os.path.splitext(entry)[0].lower())
            if requested and requested not in stem and stem not in requested:
                continue
            is_bold = "bold" in stem or "semibold" in stem
            score = 0 if is_bold == bool(bold) else 1
            candidates.append((score, os.path.join(directory, entry)))
    for _score, path in sorted(candidates):
        try:
            return ImageFont.truetype(path, max(1, int(pixel_size)))
        except OSError:
            continue
    return None


def _ffmpeg_supports_encoder(ffmpeg_path: str, encoder_name: str) -> bool:
    cache_key = (os.path.abspath(ffmpeg_path), encoder_name)
    if cache_key in _FFMPEG_ENCODER_CACHE:
        return _FFMPEG_ENCODER_CACHE[cache_key]
    try:
        result = subprocess.run(
            [ffmpeg_path, '-hide_banner', '-encoders'],
            capture_output=True,
            check=True,
            **_text_subprocess_run_kwargs(),
        )
        supported = encoder_name in (result.stdout or '')
    except Exception:
        supported = False
    _FFMPEG_ENCODER_CACHE[cache_key] = supported
    return supported


def _preferred_h264_encoder_args(ffmpeg_path: str, fast: bool = False) -> list[str]:
    if _ffmpeg_supports_encoder(ffmpeg_path, 'h264_nvenc'):
        return ['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '23', '-pix_fmt', 'yuv420p']
    preset = 'veryfast' if fast else 'medium'
    return ['-c:v', 'libx264', '-preset', preset, '-crf', '18', '-pix_fmt', 'yuv420p']


def _escape_path_for_filter(path):
    """Escape a file path for use inside an FFmpeg -vf filter value."""
    clean = path.replace("\\", "/")
    if ":" in clean:
        drive, rest = clean.split(":", 1)
        return f"{drive}\\:{rest}"
    return clean


def _ass_filter_expression(ass_path: str) -> str:
    """Build an ASS filter that uses the same local font collection as Qt."""
    escaped_ass = _escape_path_for_filter(ass_path)
    bundled_fonts = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets", "fonts"))
    if os.path.isdir(bundled_fonts) and any(name.lower().endswith((".ttf", ".otf")) for name in os.listdir(bundled_fonts)):
        escaped_fonts = _escape_path_for_filter(bundled_fonts)
        return f"ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}'"
    windows_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(windows_fonts):
        escaped_fonts = _escape_path_for_filter(windows_fonts)
        return f"ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}'"
    return f"ass=filename='{escaped_ass}'"


def _calibrated_libass_metric_scale(font_name: str, font_size: int, font_scale: float, bold: bool) -> float:
    """Calibrate Pillow's font metrics against the active bundled libass path.

    Pillow and libass use different font-size units on Windows. A global scale
    happens to work for some faces but not, for example, Montserrat at 100% or
    Verdana. Render one tiny reference cue through the same FFmpeg/libass
    configuration used by export, cache its measured width, and use that
    ratio for this exact face/size/style.
    """
    requested_size = max(1, int(font_size))
    requested_scale = max(0.1, float(font_scale))
    key = (str(font_name or ""), requested_size, round(requested_scale, 4), bool(bold))
    cached = _LIBASS_METRIC_CALIBRATIONS.get(key)
    if cached is not None:
        return cached

    fallback = _LIBASS_METRIC_SCALE
    nominal_size = max(1, int(round(requested_size * requested_scale)))
    metric_font = _subtitle_metric_font(font_name, nominal_size, bool(bold))
    if metric_font is None:
        _LIBASS_METRIC_CALIBRATIONS[key] = fallback
        return fallback

    probe_text = "CapCap WMWM 0123456789 AaEeGg ÀÁÂĂĐÊÔƠƯ àáâăđêôơư"
    pillow_width = float(metric_font.getlength(probe_text))
    if pillow_width <= 1.0:
        _LIBASS_METRIC_CALIBRATIONS[key] = fallback
        return fallback

    digest = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12]
    probe_dir = temp_path("subtitle_metric_probe")
    ass_path = os.path.join(probe_dir, f"{digest}.ass")
    image_path = os.path.join(probe_dir, f"{digest}.png")
    try:
        os.makedirs(probe_dir, exist_ok=True)
        ass_scale = max(10, int(round(requested_scale * 100)))
        with open(ass_path, "w", encoding="utf-8") as handle:
            handle.write(
                "[Script Info]\nScriptType: v4.00+\nPlayResX: 2048\nPlayResY: 256\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                f"Style: Default,{font_name},{requested_size},&H000000FF,&H000000FF,&H00000000,&H00000000,{-1 if bold else 0},0,0,0,{ass_scale},{ass_scale},0,0,1,0,0,7,0,0,0,1\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                f"Dialogue: 0,0:00:00.00,0:00:00.10,Default,,0,0,0,,{{\\an7\\pos(20,20)}}{probe_text}\n"
            )
        command = [
            _ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=2048x256:d=0.1",
            "-vf", _ass_filter_expression(ass_path), "-frames:v", "1", image_path,
        ]
        subprocess.run(command, check=True, timeout=15, **_subprocess_run_kwargs())
        from PIL import Image
        image = Image.open(image_path).convert("RGB")
        xs = []
        for y in range(image.height):
            for x in range(image.width):
                red, green, blue = image.getpixel((x, y))
                if red > 32 and red > green * 1.5 and red > blue * 1.5:
                    xs.append(x)
        if xs:
            rendered_width = max(xs) - min(xs) + 1
            scale = max(0.35, min(1.2, float(rendered_width) / pillow_width))
            _LIBASS_METRIC_CALIBRATIONS[key] = scale
            return scale
    except Exception:
        pass
    finally:
        for path in (ass_path, image_path):
            try:
                os.remove(path)
            except OSError:
                pass

    _LIBASS_METRIC_CALIBRATIONS[key] = fallback
    return fallback


def _measure_libass_cue_bounds(
    cues: list[dict], *, video_width: int, video_height: int, font_name: str,
    font_size: int, font_scale: float, bold: bool, alignment: int,
    outline_width: float,
) -> list[tuple[int, int, int, int] | None]:
    """Return exact libass text bounds for cues, in ASS PlayRes coordinates.

    This is deliberately a batch render: one synthetic frame per cue, using
    the same FFmpeg/libass/fontsdir path as export. It avoids Qt/Pillow font
    metrics and preserves libass's own automatic wrapping and fallback.
    """
    if not cues:
        return []
    # Cache each cue rather than only the whole subtitle document.  Changing
    # one subtitle, its colour, or a single timing entry must not force a
    # complete libass measurement pass for hundreds of unaffected cues.
    style_key = (
        int(video_width), int(video_height), str(font_name), int(font_size),
        round(float(font_scale), 5), bool(bold), int(alignment),
        round(float(outline_width), 4),
    )

    def _cue_cache_key(cue: dict) -> str:
        layout = (
            str(cue.get("text", "") or ""),
            int(cue.get("margin_v", 0) or 0),
            bool(cue.get("custom_position_enabled", False)),
            round(float(cue.get("custom_position_x", 50.0) or 50.0), 4),
            round(float(cue.get("custom_position_y", 86.0) or 86.0), 4),
            cue.get("custom_position_bottom_y"),
            style_key,
        )
        return hashlib.sha1(repr(layout).encode("utf-8")).hexdigest()

    cue_keys = [_cue_cache_key(cue) for cue in cues]
    with _LIBASS_LAYOUT_CACHE_LOCK:
        if all(key in _LIBASS_CUE_BOUNDS_CACHE for key in cue_keys):
            return [_LIBASS_CUE_BOUNDS_CACHE[key] for key in cue_keys]

    # Identical text and layout appears frequently (repeated captions and
    # duplicated timeline segments). Render it only once per probe batch.
    missing_cues: list[dict] = []
    missing_keys: list[str] = []
    seen_missing: set[str] = set()
    with _LIBASS_LAYOUT_CACHE_LOCK:
        cached_cues = dict(_LIBASS_CUE_BOUNDS_CACHE)
    for cue, cue_key in zip(cues, cue_keys):
        if cue_key in cached_cues or cue_key in seen_missing:
            continue
        seen_missing.add(cue_key)
        missing_keys.append(cue_key)
        missing_cues.append(cue)
    if not missing_cues:
        with _LIBASS_LAYOUT_CACHE_LOCK:
            return [_LIBASS_CUE_BOUNDS_CACHE.get(key) for key in cue_keys]

    cache_input = repr((missing_keys, style_key))
    cache_key = hashlib.sha1(cache_input.encode("utf-8")).hexdigest()

    probe_dir = temp_path("subtitle_libass_layout")
    ass_path = os.path.join(probe_dir, f"{cache_key}.ass")
    ass_scale = max(10, int(round(max(0.1, float(font_scale)) * 100)))
    style = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {video_width}\nPlayResY: {video_height}\nWrapStyle: 1\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Probe,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H00000000,{-1 if bold else 0},0,0,0,{ass_scale},{ass_scale},0,0,1,{max(0.0, float(outline_width))},0,{alignment},60,60,0,1\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    try:
        os.makedirs(probe_dir, exist_ok=True)
        with open(ass_path, "w", encoding="utf-8") as handle:
            handle.write(style)
            for index, cue in enumerate(missing_cues):
                text = _ass_escape_text(str(cue.get("text", "") or ""))
                position_tag = _position_override_tag(
                    video_width, video_height,
                    custom_position_enabled=bool(cue.get("custom_position_enabled", False)),
                    custom_position_x=float(cue.get("custom_position_x", 50.0)),
                    custom_position_y=float(cue.get("custom_position_y", 86.0)),
                    custom_position_bottom_y=cue.get("custom_position_bottom_y"),
                )
                prefix = f"{{{position_tag}}}" if position_tag else ""
                handle.write(
                    f"Dialogue: 0,{_seconds_to_ass(float(index))},{_seconds_to_ass(float(index + 1))},"
                    f"Probe,,0,0,{int(cue.get('margin_v', 0))},,{prefix}{text}\n"
                )

        command = [
            _ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            f"color=c=black:s={video_width}x{video_height}:r=1:d={len(missing_cues)}",
            "-vf", _ass_filter_expression(ass_path), "-frames:v", str(len(missing_cues)),
            "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_subprocess_run_kwargs()
        )
        frame_size = max(1, int(video_width) * int(video_height))
        bounds: list[tuple[int, int, int, int] | None] = []
        for _index in range(len(missing_cues)):
            frame = b""
            while len(frame) < frame_size:
                chunk = process.stdout.read(frame_size - len(frame)) if process.stdout else b""
                if not chunk:
                    break
                frame += chunk
            if len(frame) != frame_size:
                raise RuntimeError("libass probe returned an incomplete frame")
            left, top, right, bottom = video_width, video_height, -1, -1
            for pixel_index, value in enumerate(frame):
                if value <= 8:
                    continue
                y, x = divmod(pixel_index, video_width)
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
            bounds.append((left, top, right + 1, bottom + 1) if right >= left else None)
        process.wait(timeout=20)
        if process.returncode != 0:
            raise RuntimeError("libass probe failed")
        with _LIBASS_LAYOUT_CACHE_LOCK:
            _LIBASS_LAYOUT_BOUNDS_CACHE[cache_key] = bounds
            _LIBASS_CUE_BOUNDS_CACHE.update(zip(missing_keys, bounds))
            return [_LIBASS_CUE_BOUNDS_CACHE.get(key) for key in cue_keys]
    except Exception:
        return [None] * len(cues)
    finally:
        try:
            os.remove(ass_path)
        except OSError:
            pass


def _build_blur_filter_chain(blur_region, video_width, video_height):
    raw_regions = blur_region
    if isinstance(raw_regions, dict):
        raw_regions = [raw_regions]
    if not isinstance(raw_regions, list):
        return ""
    if video_width <= 0 or video_height <= 0:
        return ""

    regions = []
    for region in raw_regions:
        if not isinstance(region, dict):
            continue
        try:
            x_norm = float(region.get("x", 0.0))
            y_norm = float(region.get("y", 0.0))
            w_norm = float(region.get("width", 0.0))
            h_norm = float(region.get("height", 0.0))
        except (TypeError, ValueError):
            continue
        if w_norm <= 0 or h_norm <= 0:
            continue

        x = max(0, min(video_width - 2, int(round(x_norm * video_width))))
        y = max(0, min(video_height - 2, int(round(y_norm * video_height))))
        w = max(2, min(video_width - x, int(round(w_norm * video_width))))
        h = max(2, min(video_height - y, int(round(h_norm * video_height))))
        min_dimension = min(w, h)
        luma_radius = max(1, min(20, int(min_dimension // 2)))
        chroma_radius = max(0, min(20, int(min_dimension // 4)))
        try:
            start = max(0.0, float(region.get("start", 0.0) or 0.0))
            end = float(region.get("end", 0.0) or 0.0)
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        regions.append((x, y, w, h, luma_radius, chroma_radius, start, end))
    if not regions:
        return ""

    crop_parts = []
    overlay_parts = []
    for index, (x, y, w, h, luma_radius, chroma_radius, start, end) in enumerate(regions):
        crop_parts.append(
            f"[tmp{index}]crop=w={w}:h={h}:x={x}:y={y},boxblur={luma_radius}:3:{chroma_radius}:3[blur{index}]"
        )
        base_label = "main" if index == 0 else f"b{index - 1}"
        output_label = "" if index == len(regions) - 1 else f"[b{index}]"
        timing = f":enable='between(t,{start:.3f},{end:.3f})'" if end > start else ""
        overlay_parts.append(f"[{base_label}][blur{index}]overlay={x}:{y}{timing}{output_label}")
    split_outputs = "[main]" + "".join(f"[tmp{i}]" for i in range(len(regions)))
    return f"split={len(regions) + 1}{split_outputs};" + ";".join(crop_parts + overlay_parts)


def _build_mask_filter_chain(mask_regions, video_width, video_height):
    """Build FFmpeg filter chain for mask layers.
    
    Mask modes:
    - solid: Fill with color (black by default)
    - pixelate: Apply pixelation effect
    - blur: Apply blur effect
    
    Args:
        mask_regions: List of mask region dicts with keys:
            x, y, width, height (normalized 0-1)
            mode: "solid" | "pixelate" | "blur"
            color: hex color string (for solid mode)
            pixelate_size: int (for pixelate mode)
            blur_strength: int (for blur mode)
        video_width: Video width in pixels
        video_height: Video height in pixels
    
    Returns:
        FFmpeg filter chain string or empty string if no valid regions
    """
    raw_regions = mask_regions
    if isinstance(raw_regions, dict):
        raw_regions = [raw_regions]
    if not isinstance(raw_regions, list):
        return ""
    if video_width <= 0 or video_height <= 0:
        return ""

    regions = []
    for region in raw_regions:
        if not isinstance(region, dict):
            continue
        try:
            x_norm = float(region.get("x", 0.0))
            y_norm = float(region.get("y", 0.0))
            w_norm = float(region.get("width", 0.0))
            h_norm = float(region.get("height", 0.0))
            mode = str(region.get("mode", "solid")).strip().lower()
            color = str(region.get("color", "#000000")).strip()
            pixelate_size = int(region.get("pixelate_size", 12))
            blur_strength = int(region.get("blur_strength", 20))
        except (TypeError, ValueError):
            continue
        if w_norm <= 0 or h_norm <= 0:
            continue

        x = max(0, min(video_width - 2, int(round(x_norm * video_width))))
        y = max(0, min(video_height - 2, int(round(y_norm * video_height))))
        w = max(2, min(video_width - x, int(round(w_norm * video_width))))
        h = max(2, min(video_height - y, int(round(h_norm * video_height))))
        try:
            start = max(0.0, float(region.get("start", 0.0) or 0.0))
            end = float(region.get("end", 0.0) or 0.0)
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        regions.append((x, y, w, h, mode, color, pixelate_size, blur_strength, start, end))
    
    if not regions:
        return ""

    filter_statements = []
    current_input = "[0:v]"

    for index, (x, y, w, h, mode, color, pixelate_size, blur_strength, start, end) in enumerate(regions):
        output_label = f"[m{index}]"
        timing = f":enable='between(t,{start:.3f},{end:.3f})'" if end > start else ""

        if mode == "solid":
            color_clean = color.lstrip("#")
            if len(color_clean) != 6:
                color_clean = "000000"
            filter_statements.append(
                f"color=c=0x{color_clean}:s={w}x{h}:d=1[solid{index}]"
            )
            filter_statements.append(f"{current_input}[solid{index}]overlay={x}:{y}{timing}{output_label}")

        elif mode == "pixelate":
            pixel_size = max(1, min(50, pixelate_size))
            scale_factor = 1.0 / pixel_size
            small_w = max(1, int(w * scale_factor))
            small_h = max(1, int(h * scale_factor))
            filter_statements.append(
                f"{current_input}split=2[main{index}][tmp{index}];"
                f"[tmp{index}]crop=w={w}:h={h}:x={x}:y={y},"
                f"scale={small_w}:{small_h},scale={w}:{h}[pix{index}];"
                f"[main{index}][pix{index}]overlay={x}:{y}{timing}{output_label}"
            )

        elif mode == "blur":
            min_dimension = min(w, h)
            luma_radius = max(1, min(20, int(min_dimension // 2)))
            chroma_radius = max(0, min(20, int(min_dimension // 4)))
            filter_statements.append(
                f"{current_input}split=2[main{index}][tmp{index}];"
                f"[tmp{index}]crop=w={w}:h={h}:x={x}:y={y},boxblur={luma_radius}:3:{chroma_radius}:3[blur{index}];"
                f"[main{index}][blur{index}]overlay={x}:{y}{timing}{output_label}"
            )

        current_input = output_label

    if not filter_statements:
        return ""

    return ";".join(filter_statements)


def _build_logo_filter_chain(logo_layers, video_width, video_height):
    """Build FFmpeg filter chain for logo/image overlay layers.
    
    Args:
        logo_layers: List of logo layer dicts with keys:
            source: path to image file
            x, y, width, height (normalized 0-1)
            opacity: float 0-1
            rotation: float degrees
        video_width: Video width in pixels
        video_height: Video height in pixels
    
    Returns:
        FFmpeg filter chain string or empty string if no valid layers
    """
    if not isinstance(logo_layers, list):
        return ""
    if video_width <= 0 or video_height <= 0:
        return ""

    layers = []
    for layer in logo_layers:
        if not isinstance(layer, dict):
            continue
        try:
            source = str(layer.get("source", "")).strip()
            if not source or not os.path.exists(source):
                continue
            x_norm = float(layer.get("x", 0.0))
            y_norm = float(layer.get("y", 0.0))
            w_norm = float(layer.get("width", 0.0))
            h_norm = float(layer.get("height", 0.0))
            opacity = float(layer.get("opacity", 1.0))
            rotation = float(layer.get("rotation", 0.0))
        except (TypeError, ValueError):
            continue
        if w_norm <= 0 or h_norm <= 0:
            continue

        x = max(0, min(video_width - 2, int(round(x_norm * video_width))))
        y = max(0, min(video_height - 2, int(round(y_norm * video_height))))
        w = max(2, min(video_width - x, int(round(w_norm * video_width))))
        h = max(2, min(video_height - y, int(round(h_norm * video_height))))
        layers.append((source, x, y, w, h, opacity, rotation))
    
    if not layers:
        return ""

    # Build filter chain for each logo
    # Note: Logo overlay requires complex filter graph with multiple inputs
    # For simplicity, we'll use the overlay filter with scale
    filter_parts = []
    for index, (source, x, y, w, h, opacity, rotation) in enumerate(layers):
        escaped_source = _escape_path_for_filter(source)
        # Scale logo to target size
        scale_filter = f"[{index}:v]scale={w}:{h}[logo{index}]"
        filter_parts.append(scale_filter)
        
        # Apply opacity if needed
        if opacity < 1.0:
            opacity_filter = f"[logo{index}]format=rgba,colorchannelmixer=aa={opacity}[logo{index}]"
            filter_parts.append(opacity_filter)
        
        # Apply rotation if needed
        if abs(rotation) > 0.1:
            rotation_rad = rotation * 3.14159265 / 180.0
            rotate_filter = f"[logo{index}]rotate={rotation_rad}:c=none:ow={w}:oh={h}[logo{index}]"
            filter_parts.append(rotate_filter)
    
    # This is a simplified approach - full implementation would require
    # complex filter graph with multiple video inputs
    # For now, return empty string as logo export needs more work
    return ""


def _build_canvas_filter_chain(target_width=None, target_height=None, scale_mode: str = "fit", focus_x: float = 0.5, focus_y: float = 0.5):
    try:
        if target_width and target_height:
            tw = int(target_width)
            th = int(target_height)
            if tw > 0 and th > 0:
                mode = str(scale_mode or "fit").strip().lower()
                if mode == "fill":
                    fx = max(0.0, min(1.0, float(focus_x)))
                    fy = max(0.0, min(1.0, float(focus_y)))
                    return f"scale=w={tw}:h={th}:force_original_aspect_ratio=increase,crop={tw}:{th}:(iw-{tw})*{fx:.6f}:(ih-{th})*{fy:.6f}"
                return f"scale=w={tw}:h={th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2"
    except Exception:
        return ""
    return ""


def _map_normalized_overlays_to_canvas(regions, source_width, source_height,
                                       target_width, target_height,
                                       scale_mode="fit", focus_x=0.5, focus_y=0.5):
    """Map source-video-normalized rectangles into the output canvas.

    Editor overlays are authored in source-video coordinates.  The preview
    first fits/crops that source into the selected canvas, so export must use
    the same transform before converting to pixels.  This is intentionally a
    small geometry helper shared by every overlay type; it does not alter the
    stored normalized layer state.
    """
    if not isinstance(regions, (list, tuple)):
        return regions
    try:
        sw, sh = float(source_width), float(source_height)
        tw, th = float(target_width), float(target_height)
        if sw <= 0 or sh <= 0 or tw <= 0 or th <= 0:
            return list(regions)
        mode = str(scale_mode or "fit").strip().lower()
        scale = max(tw / sw, th / sh) if mode == "fill" else min(tw / sw, th / sh)
        displayed_w, displayed_h = sw * scale, sh * scale
        fx = max(0.0, min(1.0, float(focus_x)))
        fy = max(0.0, min(1.0, float(focus_y)))
        if mode == "fill":
            offset_x = (tw - displayed_w) * fx
            offset_y = (th - displayed_h) * fy
        else:
            offset_x = (tw - displayed_w) * 0.5
            offset_y = (th - displayed_h) * 0.5
    except (TypeError, ValueError):
        return list(regions)

    mapped = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        try:
            x = float(region.get("x", 0.0)) * displayed_w + offset_x
            y = float(region.get("y", 0.0)) * displayed_h + offset_y
            w = float(region.get("width", 0.0)) * displayed_w
            h = float(region.get("height", 0.0)) * displayed_h
        except (TypeError, ValueError):
            continue
        # Clip exactly as the preview canvas clips the native video surface.
        left, top = max(0.0, x), max(0.0, y)
        right, bottom = min(tw, x + w), min(th, y + h)
        if right <= left or bottom <= top:
            continue
        item = dict(region)
        item["x"] = left / tw
        item["y"] = top / th
        item["width"] = (right - left) / tw
        item["height"] = (bottom - top) / th
        mapped.append(item)
    return mapped


def _normalize_video_filter_state(video_filter_state) -> dict:
    return normalize_video_filter_state(video_filter_state)


def _build_video_filter_chain(video_filter_state=None):
    return build_video_filter_chain(video_filter_state)


def _build_video_color_chain(video_filter_state=None):
    return build_video_color_chain(video_filter_state)


def _build_video_lut_chain(video_filter_state=None):
    return build_video_lut_chain(video_filter_state)


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
            capture_output=True, check=True,
            **_text_subprocess_run_kwargs(),
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


def _custom_anchor_position(video_width: int, video_height: int, position_x: float, position_y: float):
    x_ratio = max(0.0, min(100.0, float(position_x))) / 100.0
    y_ratio = max(0.0, min(100.0, float(position_y))) / 100.0
    x = int(round(video_width * x_ratio))
    y = int(round(video_height * y_ratio))
    return x, y


def _subtitle_position_and_alignment(
    video_width: int,
    video_height: int,
    *,
    alignment: int,
    margin_v: int,
    custom_position_enabled: bool = False,
    custom_position_x: float = 50.0,
    custom_position_y: float = 86.0,
    custom_position_bottom_y: float | None = None,
) -> tuple[int, int, int]:
    """Return the exact ASS anchor point and alignment used by a subtitle.

    A dragged subtitle uses an explicit ``\\pos`` override.  Its alignment is
    intentionally fixed to ``an2`` (bottom) or ``an5`` (centre), regardless of
    the Style alignment.  Consumers which draw a companion element, such as a
    full-block background, must use this same effective alignment or they will
    drift everywhere except by coincidence at the centre.
    """
    if custom_position_enabled:
        x, y = _custom_anchor_position(
            video_width, video_height, custom_position_x, custom_position_y
        )
        if custom_position_bottom_y is not None:
            _unused, y = _custom_anchor_position(
                video_width, video_height, custom_position_x, custom_position_bottom_y
            )
            return x, y, 2
        return x, y, 5

    x, y = _alignment_anchor_position(video_width, video_height, alignment, margin_v)
    return x, y, int(alignment)


def _position_override_tag(
    video_width: int,
    video_height: int,
    *,
    custom_position_enabled: bool = False,
    custom_position_x: float = 50.0,
    custom_position_y: float = 86.0,
    custom_position_bottom_y: float | None = None,
) -> str:
    if not custom_position_enabled:
        return ""
    x, y, effective_alignment = _subtitle_position_and_alignment(
        video_width,
        video_height,
        alignment=2,
        margin_v=0,
        custom_position_enabled=True,
        custom_position_x=custom_position_x,
        custom_position_y=custom_position_y,
        custom_position_bottom_y=custom_position_bottom_y,
    )
    return rf"\an{effective_alignment}\pos({x},{y})"


def _build_typewriter_text(text: str, duration_seconds: float, mapped_words=None) -> str:
    safe_text = (text or "").replace("\n", "\\N")
    if not mapped_words:
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

    reveal_windows = [None] * len(text or "")
    for mapped in mapped_words:
        start_idx, end_idx = mapped["span"]
        visible_indices = [idx for idx in range(start_idx, min(end_idx, len(text or ""))) if not (text or "")[idx].isspace()]
        if not visible_indices:
            continue
        word_start_cs = int(round(float(mapped["start_rel"]) * 100.0))
        word_end_cs = int(round(float(mapped["end_rel"]) * 100.0))
        word_duration_cs = max(len(visible_indices), word_end_cs - word_start_cs)
        step_cs = max(1, word_duration_cs // len(visible_indices))
        cursor = word_start_cs
        for pos, idx in enumerate(visible_indices):
            char_start_cs = cursor
            if pos == len(visible_indices) - 1:
                char_end_cs = max(char_start_cs + 1, word_end_cs)
            else:
                char_end_cs = min(word_end_cs, max(char_start_cs + 1, cursor + step_cs))
            reveal_windows[idx] = (char_start_cs, char_end_cs)
            cursor = char_end_cs

    fallback_total_cs = max(18, int(round(max(duration_seconds, 0.3) * 100)))
    fallback_visible = [idx for idx, char in enumerate(text or "") if not char.isspace()]
    fallback_step_cs = max(4, fallback_total_cs // max(1, len(fallback_visible)))
    fallback_cursor = 0
    rendered = []
    for idx, char in enumerate(safe_text):
        if idx >= len(reveal_windows) or char in "{}":
            rendered.append(char)
            continue
        if char.isspace():
            rendered.append(char)
            continue
        window = reveal_windows[idx]
        if window is None:
            char_start_cs = fallback_cursor
            char_end_cs = min(fallback_total_cs, char_start_cs + fallback_step_cs)
            fallback_cursor = char_end_cs
        else:
            char_start_cs, char_end_cs = window
        rendered.append(
            r"{\alpha&HFF&\t(" + f"{char_start_cs * 10},{char_end_cs * 10}" + r",\alpha&H00&)}" + char
        )
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
    word_timing_entries=None,
    timing_mode: str = "vietnamese",
    custom_position_enabled: bool = False,
    custom_position_x: float = 50.0,
    custom_position_y: float = 86.0,
    custom_position_bottom_y: float | None = None,
) -> str:
    safe_text = (text or "").replace("\n", "\\N")
    style = (animation_style or "Static").strip().lower()
    total_ms = _ms(max(0.05, animation_duration) * 1000.0)
    position_tag = _position_override_tag(
        video_width,
        video_height,
        custom_position_enabled=custom_position_enabled,
        custom_position_x=custom_position_x,
        custom_position_y=custom_position_y,
        custom_position_bottom_y=custom_position_bottom_y,
    )
    if style == "pop in":
        return rf"{{{position_tag}\fscx118\fscy118\t(0,{total_ms},\fscx100\fscy100)}}" + safe_text
    if style == "fade in":
        return rf"{{{position_tag}\fad({_ms(total_ms * 0.8)},{_ms(total_ms * 0.2)})}}" + safe_text
    if style == "fade out":
        return rf"{{{position_tag}\fad({_ms(total_ms * 0.2)},{_ms(total_ms * 0.8)})}}" + safe_text
    if style == "pulse":
        midpoint = _ms(total_ms * 0.5)
        return rf"{{{position_tag}\t(0,{midpoint},\fscx105\fscy105)\t({midpoint},{total_ms},\fscx100\fscy100)}}" + safe_text
    if style == "background appear":
        return rf"{{{position_tag}\fad({_ms(total_ms * 0.85)},{_ms(total_ms * 0.15)})}}" + safe_text
    if style == "typewriter":
        source_words = (
            _normalize_word_timings(word_timing_entries, 0.0, duration_seconds)
            if str(timing_mode or "vietnamese").strip().lower() == "source"
            else []
        )
        mapped_words = _map_target_word_timings(text or "", source_words, duration_seconds)
        typewriter_text = _build_typewriter_text(text or "", min(duration_seconds, max(0.15, animation_duration)), mapped_words=mapped_words)
        return (rf"{{{position_tag}}}" if position_tag else "") + typewriter_text
    if style == "slide up":
        if custom_position_enabled:
            x, y = _custom_anchor_position(video_width, video_height, custom_position_x, custom_position_y)
        else:
            x, y = _alignment_anchor_position(video_width, video_height, alignment, margin_v)
        start_y = y + max(24, int(font_size * 0.7))
        fade_in = _ms(total_ms * 0.35)
        fade_out = _ms(total_ms * 0.2)
        return rf"{{\an5\move({x},{start_y},{x},{y},0,{total_ms})\fad({fade_in},{fade_out})}}{safe_text}"
    return (rf"{{{position_tag}}}" if position_tag else "") + safe_text

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
    video_width: int = 1920,
    video_height: int = 1080,
    custom_position_enabled: bool = False,
    custom_position_x: float = 50.0,
    custom_position_y: float = 86.0,
    custom_position_bottom_y: float | None = None,
    margin_v: int = 0,
    base_color: str = "",
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
    position_tag = _position_override_tag(
        video_width,
        video_height,
        custom_position_enabled=custom_position_enabled,
        custom_position_x=custom_position_x,
        custom_position_y=custom_position_y,
        custom_position_bottom_y=custom_position_bottom_y,
    )
    base_tags = "".join(part for part in (position_tag, rf"\c{base_color}" if base_color else ""))
    base_text = (rf"{{{base_tags}}}" if base_tags else "") + base_text
    mv = int(max(0, margin_v or 0))
    events = [f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,{mv},,{base_text}"]
    if not mapped_words:
        return events

    for mapped in mapped_words:
        word_start = float(start_seconds) + float(mapped["start_rel"])
        word_end = float(start_seconds) + float(mapped["end_rel"])
        if word_end <= word_start:
            continue
        overlay_text = _build_karaoke_overlay_text(source_text, mapped["span"], highlight_color)
        if position_tag:
            overlay_text = rf"{{{position_tag}}}" + overlay_text
        events.append(
            "Dialogue: 1,"
            + f"{_seconds_to_ass(word_start)},{_seconds_to_ass(word_end)},Default,,0,0,{mv},,"
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


def _apply_keyword_highlight(text: str, *, preset_key: str, highlight_color: str, manual_highlights=None, base_color: str = "") -> str:
    source_text = text or ""
    ordered_spans = _build_manual_highlight_spans(source_text, manual_highlights)
    if not ordered_spans and (preset_key or "").strip().lower() != "highlight":
        return source_text
    if not ordered_spans:
        candidates = auto_select_matches(source_text, max_keywords=2)
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
            + (r"{\c" + base_color + r"}" if base_color else r"{\c}")
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
               background_width: str = "fit_text",
               background_shape: str = "rectangle",
               background_padding: float = 6.0,
               background_radius: float = 0.0,
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
               custom_position_bottom_y: float | None = None,
               single_line: bool = False,
               font_scale: float = 1.0,
               speaker_colors=None,
               log_generation: bool = True) -> str:
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

    full_background = str(background_width or "fit_text").strip().lower() == "full_area"
    fit_background = bool(background_box and not full_background)
    # Keep the text style independent from its background. BorderStyle=3
    # cannot draw a glyph outline and a box outline at the same time, so Fit
    # Text uses a separate transparent-background dialogue below the normal
    # outlined text dialogue.
    outline = max(0.0, float(outline_width))
    box_outline = max(0.0, float(background_padding), float(outline_width))
    shadow = 0 if background_box else float(shadow_depth)
    box_color = _with_alpha(background_color, background_alpha) if background_box else None
    style_outline_color = outline_color
    back_color = _with_alpha(shadow_color, 0.7)
    bold_flag = -1 if bold else 0

    wrap_style = 2 if single_line else 1
    ass_font_scale = max(10, int(round(max(0.1, float(font_scale)) * 100)))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "ScaledBorderAndShadow: yes\n"
        f"WrapStyle: {wrap_style}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},"
        f"{font_color},{highlight_color},{style_outline_color},{back_color},"
        f"{bold_flag},0,0,0,{ass_font_scale},{ass_font_scale},0,0,1,{outline},{shadow},"
        f"{alignment},60,60,{margin_v},1\n"
    )
    if fit_background:
        header += (
            f"Style: Background,{font_name},{font_size},&HFF000000,&HFF000000,{box_color},{box_color},"
            f"{bold_flag},0,0,0,{ass_font_scale},{ass_font_scale},0,0,3,{box_outline},0,"
            f"{alignment},60,60,{margin_v},1\n"
        )
    header += (
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    background_debug_logged = False

    def _final_subtitle_layout(text: str) -> tuple[str, int, int, dict]:
        """Measure a subtitle without changing its authored line breaks."""
        pad = max(3, int(round(float(background_padding))))
        effective_font_size = max(1.0, float(font_size) * max(0.1, float(font_scale)))
        metric_scale = _calibrated_libass_metric_scale(font_name, int(font_size), float(font_scale), bool(bold))
        metric_font_size = max(1, int(round(effective_font_size * metric_scale)))
        usable_width = max(1, int(video_width - 120))
        metric_font = _subtitle_metric_font(font_name, metric_font_size, bool(bold))
        stroke = int(math.ceil(max(0.0, float(outline_width))))
        source_lines = [line.strip() for line in str(text or "").splitlines() if line.strip()] or [""]
        if metric_font is None:
            # This fallback is only for unexpectedly incomplete installations.
            max_chars = max((len(line) for line in source_lines), default=1)
            width = min(usable_width, max(1, int(round(effective_font_size * 0.55 * max_chars + pad * 2))))
            height = max(1, int(math.ceil(effective_font_size * 0.8 * len(source_lines) + pad * 2 + stroke * 2)))
            return "\n".join(source_lines), width, height, {
                "font_ascent": None,
                "font_descent": None,
                "line_count": len(source_lines),
                "line_widths": [],
                "line_height": None,
                "font_source": "fallback",
                "metric_font_size": metric_font_size,
                "metric_scale": metric_scale,
            }

        # Do not insert artificial line breaks. Existing/manual breaks remain
        # intact and libass retains ownership of automatic wrapping. We only
        # mirror its word/character wrapping here to estimate the background
        # bounds; the returned text is deliberately left untouched.
        max_text_width = max(1, usable_width - pad * 2)

        def _estimated_wrapped_lines(line: str) -> list[str]:
            if metric_font.getlength(line) <= max_text_width:
                return [line or " "]
            tokens = line.split() if len(line.split()) > 1 else list(line)
            separator = " " if len(line.split()) > 1 else ""
            rows: list[str] = []
            current = ""
            for token in tokens:
                candidate = f"{current}{separator if current else ''}{token}"
                if current and metric_font.getlength(candidate) > max_text_width:
                    rows.append(current)
                    current = token
                else:
                    current = candidate
            if current:
                rows.append(current)
            return rows or [" "]

        measured_lines = [
            row for source_line in source_lines for row in _estimated_wrapped_lines(source_line)
        ]
        bounds = [metric_font.getbbox(line or " ", stroke_width=stroke) for line in measured_lines]
        line_widths = [max(1, right - left) for left, _top, right, _bottom in bounds]
        line_heights = [max(1, bottom - top) for _left, top, _right, bottom in bounds]
        text_width = max((right - left for left, _top, right, _bottom in bounds), default=1)
        # This is a measurement only; text layout still belongs to libass.
        text_height = sum(line_heights)
        font_ascent, font_descent = metric_font.getmetrics()
        return (
            str(text or ""),
            min(usable_width, max(1, int(math.ceil(text_width + pad * 2)))),
            max(1, int(math.ceil(text_height + pad * 2))),
            {
                "font_ascent": font_ascent,
                "font_descent": font_descent,
                "line_count": len(measured_lines),
                "line_widths": line_widths,
                "line_height": max(line_heights, default=0),
                "font_source": "bundled-or-system Pillow face",
                "metric_font_size": metric_font_size,
                "metric_scale": metric_scale,
            },
        )

    def _full_background_event(start: str, end: str, layout, event_margin: int) -> str:
        nonlocal background_debug_logged
        pad = max(3, int(round(float(background_padding))))
        if isinstance(layout, dict) and layout.get("debug", {}).get("source") == "libass":
            width, height = int(layout["width"]), int(layout["height"])
            left, top = int(layout["left"]), int(layout["top"])
            metric_debug = layout["debug"]
            anchor_x, anchor_y, effective_alignment = _subtitle_position_and_alignment(
                video_width, video_height, alignment=alignment, margin_v=event_margin,
                custom_position_enabled=custom_position_enabled, custom_position_x=custom_position_x,
                custom_position_y=custom_position_y, custom_position_bottom_y=custom_position_bottom_y,
            )
        else:
            _final_text, width, height, metric_debug = layout
            anchor_x, anchor_y, effective_alignment = _subtitle_position_and_alignment(
                video_width, video_height, alignment=alignment, margin_v=event_margin,
                custom_position_enabled=custom_position_enabled, custom_position_x=custom_position_x,
                custom_position_y=custom_position_y, custom_position_bottom_y=custom_position_bottom_y,
            )
            horizontal = ((effective_alignment - 1) % 3) + 1
            vertical = (effective_alignment - 1) // 3
            left = anchor_x if horizontal == 1 else anchor_x - width // 2 if horizontal == 2 else anchor_x - width
            top = anchor_y - height if vertical == 0 else anchor_y - height // 2 if vertical == 1 else anchor_y
            left = max(0, min(int(left), max(0, video_width - width)))
            top = max(0, min(int(top), max(0, video_height - height)))
        if not background_debug_logged and (metric_debug.get("source") == "libass" or int(metric_debug.get("line_count", 0) or 0) > 1):
            background_debug_logged = True
            print(
                "[Subtitle Background] "
                f"font={font_name}, fontSize={font_size}, metricFontSize={metric_debug.get('metric_font_size')}, "
                f"metricScale={metric_debug.get('metric_scale')}, source={metric_debug.get('source')}, "
                f"scaleX/Y={ass_font_scale}/{ass_font_scale}, "
                f"ascent={metric_debug.get('font_ascent')}, descent={metric_debug.get('font_descent')}, "
                f"lineHeight={metric_debug.get('line_height')}, lineCount={metric_debug.get('line_count')}, "
                f"lineWidths={metric_debug.get('line_widths')}, block={width}x{height}, "
                f"paddingX={pad}, paddingY={pad}, PlayRes={video_width}x{video_height}, alignment={effective_alignment}, "
                f"anchor=({anchor_x},{anchor_y}), rect=({left},{top},{left + width},{top + height}), "
                f"metricSource={metric_debug.get('font_source')}"
            )
        rgb, alpha = str(box_color)[4:], str(box_color)[2:4]
        radius = max(0, min(int(round(float(background_radius))), width // 2, height // 2))
        if radius <= 0:
            path = f"m 0 0 l {width} 0 {width} {height} 0 {height} 0 0"
        else:
            # Cubic curves approximate rounded corners in ASS vector drawing.
            k = int(round(radius * 0.5523))
            path = (
                f"m {radius} 0 l {width-radius} 0 "
                f"b {width-radius+k} 0 {width} {radius-k} {width} {radius} "
                f"l {width} {height-radius} "
                f"b {width} {height-radius+k} {width-radius+k} {height} {width-radius} {height} "
                f"l {radius} {height} "
                f"b {radius-k} {height} 0 {height-radius+k} 0 {height-radius} "
                f"l 0 {radius} b 0 {radius-k} {radius-k} 0 {radius} 0"
            )
        # Vector drawings inherit the Style's ScaleX/ScaleY.  Explicitly
        # neutralize that font scaling: ``width``/``height`` above are already
        # calculated in final PlayRes pixels.  Without this override, changing
        # the subtitle scale resized the vector a second time while libass
        # resized the text only once.
        return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\an7\\pos({left},{top})\\fscx100\\fscy100\\p1\\1c&H{rgb}&\\1a&H{alpha}&}}{path}"

    def _fit_background_event(start: str, end: str, text: str, event_margin: int) -> str:
        """Render Fit Text's box on a lower layer, leaving glyph styling intact."""
        position_tag = _position_override_tag(
            video_width, video_height,
            custom_position_enabled=custom_position_enabled,
            custom_position_x=custom_position_x,
            custom_position_y=custom_position_y,
            custom_position_bottom_y=custom_position_bottom_y,
        )
        prefix = f"{{{position_tag}}}" if position_tag else ""
        return (
            f"Dialogue: 0,{start},{end},Background,,0,0,{event_margin},,"
            f"{prefix}{_ass_escape_text(text)}"
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

    matches = list(pattern.finditer(content.strip() + "\n\n"))
    libass_background_layouts: list[dict | None] = [None] * len(matches)
    if full_background and matches:
        probe_cues: list[dict] = []
        probe_rows: list[float] = []
        probe_line_height = max(20, int(font_size * 1.4))
        for match in matches:
            start_seconds = _srt_time_to_seconds(match.group(1))
            end_seconds = _srt_time_to_seconds(match.group(2))
            raw_probe_text = match.group(3).strip()
            if single_line:
                raw_probe_text = " ".join(part.strip() for part in raw_probe_text.splitlines() if part.strip())
            row_index = next((idx for idx, last_end in enumerate(probe_rows) if last_end <= start_seconds), len(probe_rows))
            if row_index == len(probe_rows):
                probe_rows.append(end_seconds)
            else:
                probe_rows[row_index] = max(probe_rows[row_index], end_seconds)
            probe_cues.append({
                "text": raw_probe_text,
                "margin_v": int(max(0, margin_v + row_index * probe_line_height)),
                "custom_position_enabled": custom_position_enabled,
                "custom_position_x": custom_position_x,
                "custom_position_y": custom_position_y,
                "custom_position_bottom_y": custom_position_bottom_y,
            })
        exact_bounds = _measure_libass_cue_bounds(
            probe_cues, video_width=video_width, video_height=video_height,
            font_name=font_name, font_size=font_size, font_scale=font_scale,
            bold=bold, alignment=alignment, outline_width=outline,
        )
        pad = max(3, int(round(float(background_padding))))
        for index, bounds in enumerate(exact_bounds):
            if bounds is None:
                continue
            text_left, text_top, text_right, text_bottom = bounds
            left = max(0, text_left - pad)
            top = max(0, text_top - pad)
            right = min(video_width, text_right + pad)
            bottom = min(video_height, text_bottom + pad)
            libass_background_layouts[index] = {
                "left": left, "top": top,
                "width": max(1, right - left), "height": max(1, bottom - top),
                "debug": {"source": "libass", "text_bounds": bounds, "line_count": 2 if "\n" in probe_cues[index]["text"] else 1},
            }

    events = []
    line_height = max(20, int(font_size * 1.4))
    row_last_end: list[float] = []
    for event_index, m in enumerate(matches):
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
        line_speaker_color = ""
        if isinstance(speaker_colors, list) and event_index < len(speaker_colors):
            candidate = str(speaker_colors[event_index] or "").strip()
            if candidate.startswith("&H"):
                line_speaker_color = candidate
        if single_line:
            raw_text = " ".join(part.strip() for part in raw_text.splitlines() if part.strip())
        full_background_layout = None
        if full_background:
            full_background_layout = libass_background_layouts[event_index] or _final_subtitle_layout(raw_text)
        style_key = (animation_style or "Static").strip().lower()

        # Greedy row assignment: a segment goes to the first row whose
        # last segment has already ended by the time this one starts. When
        # two segments overlap, the later one gets a higher row index so it
        # is drawn on a new line above the previous one.
        row_index = 0
        for r_idx, last_end in enumerate(row_last_end):
            if last_end <= start_seconds:
                row_index = r_idx
                break
        else:
            row_index = len(row_last_end)
            row_last_end.append(end_seconds)
        if row_index < len(row_last_end):
            row_last_end[row_index] = max(row_last_end[row_index], end_seconds)
        row_margin_v = row_index * line_height
        # Dialogue MarginV overrides the style MarginV rather than adding to
        # it. Preserve the configured subtitle padding for every row.
        event_margin_v = int(max(0, margin_v + row_margin_v))

        if style_key == "word highlight karaoke":
            if full_background:
                events.append(_full_background_event(start, end, full_background_layout, event_margin_v))
            elif fit_background:
                events.append(_fit_background_event(start, end, raw_text, event_margin_v))
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
                    video_width=video_width,
                    video_height=video_height,
                    custom_position_enabled=custom_position_enabled,
                    custom_position_x=custom_position_x,
                    custom_position_y=custom_position_y,
                    custom_position_bottom_y=custom_position_bottom_y,
                    margin_v=event_margin_v,
                    base_color=line_speaker_color,
                )
            )
            continue

        text_content = _apply_keyword_highlight(
            raw_text,
            preset_key="highlight" if auto_keyword_highlight else preset_key,
            highlight_color=highlight_color,
            manual_highlights=line_manual_highlights,
            base_color=line_speaker_color,
        )
        if line_speaker_color:
            text_content = r"{\c" + line_speaker_color + r"}" + text_content
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
            word_timing_entries=line_word_timings,
            timing_mode=karaoke_timing_mode,
            custom_position_enabled=custom_position_enabled,
            custom_position_x=custom_position_x,
            custom_position_y=custom_position_y,
            custom_position_bottom_y=custom_position_bottom_y,
        )
        dialogue = f"Dialogue: 0,{start},{end},Default,,0,0,{event_margin_v},,{text}"
        if full_background:
            events.append(_full_background_event(start, end, full_background_layout, event_margin_v))
            dialogue = dialogue.replace("Dialogue: 0,", "Dialogue: 1,", 1)
        elif fit_background:
            events.append(_fit_background_event(start, end, raw_text, event_margin_v))
            dialogue = dialogue.replace("Dialogue: 0,", "Dialogue: 1,", 1)
        events.append(dialogue)

    with open(ass_path, 'w', encoding='utf-8-sig') as f:
        f.write(header)
        f.write('\n'.join(events) + '\n')

    if log_generation:
        print(f"Generated ASS: {ass_path}  ({len(events)} lines, "
              f"{video_width}x{video_height}, font={font_name}, fontSize={font_size}, fontScale={font_scale}, bold={bool(bold)}, "
              f"alignment={alignment}, marginV={margin_v}, animation={animation_style}, preset={preset_key or 'custom'})")
    return ass_path


def _valid_text_image_layers(text_image_layers):
    return [
        layer for layer in (text_image_layers or [])
        if isinstance(layer, dict) and os.path.exists(str(layer.get("path", "") or ""))
    ]


def _append_text_image_filter_parts(filter_parts, current_label, text_image_layers, input_start_index):
    """Composite cropped Qt TextLayer PNGs in their timeline intervals."""
    for index, layer in enumerate(text_image_layers):
        input_index = input_start_index + index
        x = int(round(float(layer.get("x", 0) or 0)))
        y = int(round(float(layer.get("y", 0) or 0)))
        start = max(0.0, float(layer.get("start", 0.0) or 0.0))
        end = float(layer.get("end", 0.0) or 0.0)
        image_label = f"text_image_{index}"
        next_label = f"with_text_image_{index}"
        filter_parts.append(f"[{input_index}:v]format=rgba[{image_label}]")
        timing = f":enable='between(t,{start:.3f},{end:.3f})'" if end > start else ""
        filter_parts.append(
            f"[{current_label}][{image_label}]overlay={x}:{y}:shortest=1{timing}[{next_label}]"
        )
        current_label = next_label
    return current_label


def embed_ass_subtitles(video_path, ass_path, output_path, ffmpeg_path=None, blur_region=None, mask_regions=None, logo_layers=None, text_ass_path="", text_image_layers=None, target_width=None, target_height=None, output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, output_fps=None, video_filter_state=None, audio_gain_db=0.0, fast=False):
    """Burn subtitles into video using an already-prepared ASS file."""
    print(f"[FFmpeg] embed_ass_subtitles called with mask_regions={mask_regions}, logo_layers={logo_layers}")
    ffmpeg = _ffmpeg_path(ffmpeg_path)
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
    if not os.path.exists(ass_path):
        raise FileNotFoundError(f"ASS subtitle file not found at {ass_path}")

    source_w, source_h = get_video_dimensions(video_path)
    video_w, video_h = source_w, source_h

    scale_chain = _build_canvas_filter_chain(target_width, target_height, output_scale_mode, output_fill_focus_x, output_fill_focus_y)
    canvas_w, canvas_h = source_w, source_h
    try:
        if target_width and target_height:
            tw = int(target_width)
            th = int(target_height)
            if tw > 0 and th > 0:
                canvas_w, canvas_h = tw, th
    except Exception:
        canvas_w, canvas_h = source_w, source_h

    print(
        f"[Export Geometry] subtitle/filter space={source_w}x{source_h} "
        f"canvas={canvas_w}x{canvas_h} "
        f"mode={str(output_scale_mode or 'fit').lower()} "
        f"focus=({float(output_fill_focus_x):.3f},{float(output_fill_focus_y):.3f})"
    )

    # Logos are composited after the canvas transform, so convert their
    # source-normalized rectangles into output-canvas coordinates first.
    logo_layers_for_canvas = _map_normalized_overlays_to_canvas(
        logo_layers, source_w, source_h, canvas_w, canvas_h,
        output_scale_mode, output_fill_focus_x, output_fill_focus_y,
    ) if target_width and target_height else logo_layers

    blur_chain = _build_blur_filter_chain(blur_region, video_w, video_h)
    mask_chain = _build_mask_filter_chain(mask_regions, video_w, video_h)
    print(f"[FFmpeg] mask_chain={mask_chain[:100] if mask_chain else 'empty'}")
    
    # Check if we have logo layers
    text_image_layers = _valid_text_image_layers(text_image_layers)
    has_logos = logo_layers_for_canvas and len(logo_layers_for_canvas) > 0
    print(f"[FFmpeg] has_logos={has_logos}, logo_layers count={len(logo_layers_for_canvas) if logo_layers_for_canvas else 0}")
    
    # Build command based on whether we have logos
    if has_logos:
        # Complex filter graph with multiple inputs
        command = _build_logo_overlay_command(
            ffmpeg, video_path, ass_path, output_path, logo_layers_for_canvas,
            blur_region, mask_regions, canvas_w, canvas_h,
            scale_chain, blur_chain, mask_chain,
            output_fps, video_filter_state, text_ass_path, text_image_layers,
            source_width=source_w, source_height=source_h,
            audio_gain_db=audio_gain_db,
        )
    else:
        # Simple filter chain (no logos)
        filter_parts = []
        current_label = "[0:v]"
        
        filter_video_chain = _build_video_color_chain(video_filter_state)
        if filter_video_chain:
            filter_parts.append(f"{current_label}{filter_video_chain}[filtered]")
            current_label = "[filtered]"
        
        if blur_chain:
            filter_parts.append(f"{current_label}{blur_chain}[blurred]")
            current_label = "[blurred]"
        if mask_chain:
            # Keep export consistent with MPV: Blur, then Mask.
            mask_chain = mask_chain.replace("[0:v]", current_label, 1)
            filter_parts.append(mask_chain)
            if "[m" in mask_chain:
                matches = re.findall(r'\[m\d+\]', mask_chain)
                if matches:
                    current_label = matches[-1]
        lut_chain = _build_video_lut_chain(video_filter_state)
        if lut_chain:
            filter_parts.append(f"{current_label}{lut_chain}[lut_filtered]")
            current_label = "[lut_filtered]"
        
        # TS1 remains an ASS/libass pass in source-video coordinates, matching
        # MPV preview. The output canvas transform follows this pass.
        filter_parts.append(f"{current_label}{_ass_filter_expression(ass_path)}[subbed]")
        current_label = "subbed"
        if scale_chain:
            filter_parts.append(f"[{current_label}]{scale_chain}[scaled]")
            current_label = "scaled"
        if text_ass_path and os.path.exists(text_ass_path):
            filter_parts.append(f"[{current_label}]{_ass_filter_expression(text_ass_path)}[text_ass]")
            current_label = "text_ass"
        current_label = _append_text_image_filter_parts(
            filter_parts, current_label, text_image_layers, 1
        )
        filter_parts.append(f"[{current_label}]null[out]")
        filter_complex = ";".join(part for part in filter_parts if part)
        
        video_encoder_args = _preferred_h264_encoder_args(ffmpeg, fast=fast)

        command = [
            ffmpeg,
            '-hide_banner',
            '-loglevel',
            # Keep libass diagnostics in the captured process output so we
            # can record the actual selected font face without flooding the
            # application log.
            'verbose',
            '-y',
            '-i', video_path,
            '-map', '[out]',
            '-map', '0:a?',
            '-filter_complex', filter_complex,
            *video_encoder_args,
            '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart',
        ]
        # FFmpeg needs a looping image stream so each static text bitmap can
        # remain available for its entire timed overlay interval.
        insert_at = command.index('-map')
        image_inputs = []
        for layer in text_image_layers:
            image_inputs += ['-loop', '1', '-framerate', '30', '-i', str(layer['path'])]
        command[insert_at:insert_at] = image_inputs
        try:
            if output_fps:
                parsed_fps = int(float(output_fps))
                if parsed_fps > 0:
                    command += ['-r', str(parsed_fps)]
        except Exception:
            pass
        try:
            gain_db = float(audio_gain_db or 0.0)
            if abs(gain_db) > 0.001:
                command += ['-af', f'volume={gain_db:.4f}dB']
        except (TypeError, ValueError):
            pass
        command += [output_path]
    
    encoder_name = 'libx264' if 'libx264' in ' '.join(command) else 'h264_nvenc'
    print(f"Executing ({encoder_name}): {' '.join(command)}")

    try:
        result = subprocess.run(command, capture_output=True, check=True, **_text_subprocess_run_kwargs())
        font_selects = re.findall(r".*fontselect:.*", result.stderr or "", flags=re.IGNORECASE)
        if font_selects:
            print(f"[Subtitle Font] libass selected: {font_selects[-1].strip()}")
        else:
            print("[Subtitle Font] libass did not report a selected face; check FFmpeg/libass build diagnostics.")
        print(f"ASS subtitles embedded successfully using {encoder_name}.")
        return True
    except subprocess.CalledProcessError as e:
        if encoder_name != 'libx264':
            # Retry with libx264
            command = [c if c != 'h264_nvenc' else 'libx264' for c in command]
            if '-preset' in command:
                idx = command.index('-preset')
                if idx + 1 < len(command):
                    command[idx + 1] = 'medium'
            if '-cq' in command:
                idx = command.index('-cq')
                command[idx] = '-crf'
                if idx + 1 < len(command):
                    command[idx + 1] = '18'
            
            print(f"NVENC failed, retrying with libx264. Error:\n{e.stderr}")
            try:
                result = subprocess.run(command, capture_output=True, check=True, **_text_subprocess_run_kwargs())
                font_selects = re.findall(r".*fontselect:.*", result.stderr or "", flags=re.IGNORECASE)
                if font_selects:
                    print(f"[Subtitle Font] libass selected: {font_selects[-1].strip()}")
                print("ASS subtitles embedded successfully using libx264 fallback.")
                return True
            except subprocess.CalledProcessError as fallback_error:
                print(f"Error during fallback:\n{fallback_error.stderr}")
                return False
        print(f"Error during ASS embedding:\n{e.stderr}")
        return False


def _build_logo_overlay_command(ffmpeg, video_path, ass_path, output_path, logo_layers,
                                 blur_region, mask_regions, video_w, video_h,
                                 scale_chain, blur_chain, mask_chain,
                                 output_fps, video_filter_state, text_ass_path="", text_image_layers=None,
                                 source_width=None, source_height=None, audio_gain_db=0.0):
    """Build FFmpeg command with logo overlay using filter_complex."""
    
    # Start building the command with video input
    command = [
        ffmpeg,
        '-hide_banner',
        '-loglevel',
        'error',
        '-y',
        '-i', video_path,
    ]
    
    # Add logo image inputs
    for i, logo in enumerate(logo_layers):
        source = logo.get('source', '')
        if source and os.path.exists(source):
            command += ['-i', source]
    text_image_layers = _valid_text_image_layers(text_image_layers)
    for layer in text_image_layers:
        command += ['-loop', '1', '-framerate', '30', '-i', str(layer['path'])]
    
    # Build filter_complex
    filter_parts = []
    
    # Apply source-video filters and ASS before the final Fit/Fill transform,
    # matching MPV's subtitle render order. Logo/Text images are composited
    # after scaling because they are authored in output-canvas coordinates.
    main_label = "0:v"
    
    # Apply video filter chain
    filter_video_chain = _build_video_color_chain(video_filter_state)
    if filter_video_chain:
        filter_parts.append(f"[{main_label}]{filter_video_chain}[filtered]")
        main_label = "filtered"
    
    # Apply Blur then Mask, matching the managed MPV graph.
    if blur_chain:
        filter_parts.append(f"[{main_label}]{blur_chain}[blurred]")
        main_label = "blurred"
    if mask_chain:
        adjusted_mask_chain = mask_chain.replace("[0:v]", f"[{main_label}]", 1)
        filter_parts.append(adjusted_mask_chain)
        import re
        matches = re.findall(r'\[m\d+\]', adjusted_mask_chain)
        if matches:
            main_label = matches[-1].strip("[]")
        else:
            main_label = "masked"

    lut_chain = _build_video_lut_chain(video_filter_state)
    if lut_chain:
        filter_parts.append(f"[{main_label}]{lut_chain}[lut_filtered]")
        main_label = "lut_filtered"

    filter_parts.append(f"[{main_label}]{_ass_filter_expression(ass_path)}[subbed]")
    main_label = "subbed"
    if scale_chain:
        filter_parts.append(f"[{main_label}]{scale_chain}[scaled]")
        main_label = "scaled"
    
    # 2. Process each logo layer
    logo_input_idx = 1
    current_label = main_label
    
    for i, logo in enumerate(logo_layers):
        source = logo.get('source', '')
        if not source or not os.path.exists(source):
            continue
        
        x = float(logo.get('x', 0.1))
        y = float(logo.get('y', 0.1))
        width = float(logo.get('width', 0.2))
        height = float(logo.get('height', 0.2))
        opacity = float(logo.get('opacity', 1.0))
        rotation = float(logo.get('rotation', 0.0))
        start = max(0.0, float(logo.get('start', 0.0) or 0.0))
        end = float(logo.get('end', 0.0) or 0.0)
        
        # Calculate pixel dimensions
        logo_w = int(video_w * width)
        logo_h = int(video_h * height)
        logo_x = int(video_w * x)
        logo_y = int(video_h * y)
        
        # Scale logo
        filter_parts.append(f"[{logo_input_idx}:v]scale={logo_w}:{logo_h}[logo{i}_scaled]")
        
        # Apply opacity if needed
        if opacity < 1.0:
            filter_parts.append(f"[logo{i}_scaled]format=rgba,colorchannelmixer=aa={opacity}[logo{i}_opacity]")
            logo_label = f"logo{i}_opacity"
        else:
            logo_label = f"logo{i}_scaled"
        
        # Apply rotation if needed
        if abs(rotation) > 0.1:
            # FFmpeg uses radians, convert from degrees
            rotation_rad = rotation * 3.14159265 / 180.0
            filter_parts.append(f"[{logo_label}]rotate={rotation_rad}:c=none:ow={logo_w}:oh={logo_h}[logo{i}_rotated]")
            logo_label = f"logo{i}_rotated"
        
        # Overlay logo on video
        next_label = f"with_logo{i}"
        timing = f":enable='between(t,{start:.3f},{end:.3f})'" if end > start else ""
        filter_parts.append(f"[{current_label}][{logo_label}]overlay={logo_x}:{logo_y}{timing}[{next_label}]")
        current_label = next_label
        logo_input_idx += 1
    
    # Optional legacy secondary ASS/Text pass remains a canvas overlay.
    if text_ass_path and os.path.exists(text_ass_path):
        filter_parts.append(f"[{current_label}]{_ass_filter_expression(text_ass_path)}[text_ass]")
        current_label = "text_ass"
    current_label = _append_text_image_filter_parts(
        filter_parts, current_label, text_image_layers, 1 + sum(
            1 for logo in logo_layers if logo.get('source', '') and os.path.exists(logo.get('source', ''))
        )
    )
    filter_parts.append(f"[{current_label}]null[final]")
    
    # Join all filter parts
    filter_complex = ";".join(filter_parts)
    
    # Complete the command
    video_encoder_args = _preferred_h264_encoder_args(ffmpeg)
    command += [
        '-filter_complex', filter_complex,
        '-map', '[final]',
        '-map', '0:a?',
        *video_encoder_args,
        '-c:a', 'aac', '-b:a', '192k',
        '-movflags', '+faststart',
    ]
    try:
        gain_db = float(audio_gain_db or 0.0)
        if abs(gain_db) > 0.001:
            command += ['-af', f'volume={gain_db:.4f}dB']
    except (TypeError, ValueError):
        pass
    
    try:
        if output_fps:
            parsed_fps = int(float(output_fps))
            if parsed_fps > 0:
                command += ['-r', str(parsed_fps)]
    except Exception:
        pass
    
    command += [output_path]
    return command


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
        subprocess.run(command, capture_output=True, check=True, **_text_subprocess_run_kwargs())
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
                    custom_position_enabled=False,
                    custom_position_x=50.0,
                    custom_position_y=86.0,
                    custom_position_bottom_y=None,
                    single_line=False,
                    font_scale=1.0,
                    speaker_colors=None,
                    blur_region=None,
                    mask_regions=None,
                    logo_layers=None,
                    text_ass_path="",
                    text_image_layers=None,
                     target_width=None,
                     target_height=None,
                     output_scale_mode="fit",
                     output_fill_focus_x=0.5,
                     output_fill_focus_y=0.5,
                     output_fps=None,
                     ffmpeg_path=None,
                    video_filter_state=None,
                    audio_gain_db=0.0,
                    fast=False):
    """Burn subtitles into video using a properly-styled ASS file.

    Workflow:
    1. Query actual video resolution with ffprobe.
    2. Convert SRT → ASS with PlayRes = video resolution (pixel-accurate).
    3. Apply with FFmpeg's  ass=  filter (no force_style hacks).
    4. Remove the temporary ASS after success.
    """
    print(f"[FFmpeg] embed_subtitles called with mask_regions={mask_regions}, logo_layers={logo_layers}")
    ffmpeg = _ffmpeg_path(ffmpeg_path)
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    # Step 1: get real video resolution
    # ASS is rendered before the output Fit/Fill transform, just like MPV's
    # live subtitle track, so author it in source-video coordinates.
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
        custom_position_enabled=custom_position_enabled,
        custom_position_x=custom_position_x,
        custom_position_y=custom_position_y,
        custom_position_bottom_y=custom_position_bottom_y,
        single_line=single_line,
        font_scale=font_scale,
        speaker_colors=speaker_colors,
    )

    success = embed_ass_subtitles(
        video_path,
        ass_path,
        output_path,
        ffmpeg_path=ffmpeg,
        blur_region=blur_region,
        mask_regions=mask_regions,
        logo_layers=logo_layers,
        text_ass_path=text_ass_path,
        text_image_layers=text_image_layers,
        target_width=target_width,
        target_height=target_height,
        output_scale_mode=output_scale_mode,
        output_fill_focus_x=output_fill_focus_x,
        output_fill_focus_y=output_fill_focus_y,
        output_fps=output_fps,
        video_filter_state=video_filter_state,
        audio_gain_db=audio_gain_db,
        fast=fast,
    )

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






