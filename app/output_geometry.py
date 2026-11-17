"""Shared output canvas geometry for preview, queue and export."""
import math
import re

SOURCE_KEYS = {"", "source", "same", "original", "auto"}


def source_point_to_canvas(x, y, source_size, canvas_size, mode="fit", focus=(.5, .5)):
    sw, sh = source_size
    cw, ch = canvas_size
    scale = (max if mode == "fill" else min)(cw / sw, ch / sh)
    dw, dh = sw * scale, sh * scale
    ox = (cw - dw) * (focus[0] if mode == "fill" else .5)
    oy = (ch - dh) * (focus[1] if mode == "fill" else .5)
    return (float(x) * dw + ox) / cw, (float(y) * dh + oy) / ch


def normalize_canvas(value):
    key = str(value or "source").strip().lower().replace(" ", "").replace("×", "x")
    if key in SOURCE_KEYS:
        return "source"
    if key.startswith("size:"):
        key = key[5:]
    if "x" in key:
        if not re.fullmatch(r"\d+x\d+", key):
            raise ValueError("Use width x height, for example 1080x1920.")
        width, height = map(int, key.split("x"))
        if not all(2 <= v <= 8192 and v % 2 == 0 for v in (width, height)):
            raise ValueError("Width and height must be even numbers from 2 to 8192 pixels.")
        return f"size:{width}x{height}"
    if not re.fullmatch(r"\d+(?:\.\d+)?:\d+(?:\.\d+)?", key):
        raise ValueError("Use a ratio such as 3:2 or a size such as 1080x1920.")
    a, b = map(float, key.split(":"))
    if not all(math.isfinite(v) and v > 0 for v in (a, b)) or not .01 <= a / b <= 100:
        raise ValueError("Invalid video ratio.")
    return f"{a:g}:{b:g}"


def canvas_ratio(value):
    key = normalize_canvas(value)
    if key == "source":
        return None
    if key.startswith("size:"):
        return tuple(map(int, key[5:].split("x")))
    return tuple(map(float, key.split(":")))


def resolve_output_dimensions(src_w, src_h, quality="source", canvas="source"):
    key = normalize_canvas(canvas)
    if key.startswith("size:"):
        return canvas_ratio(key)  # Explicit size overrides quality, including upscale.
    if not src_w or not src_h:
        return None, None
    ratio = canvas_ratio(key)
    quality = str(quality or "source").strip().lower()
    if quality in SOURCE_KEYS:
        if ratio is None or abs(src_w / src_h - ratio[0] / ratio[1]) < .001:
            return None, None
        scale = min(src_w / ratio[0], src_h / ratio[1])
    else:
        sizes = {"720":720, "720p":720, "hd":720, "1080":1080, "1080p":1080,
                 "fullhd":1080, "fhd":1080, "full hd":1080, "full":1080,
                 "1440":1440, "1440p":1440, "2k":1440, "qhd":1440,
                 "2160":2160, "2160p":2160, "4k":2160, "uhd":2160}
        edge = sizes.get(quality)
        if edge is None:
            return None, None
        if ratio is None:
            if min(src_w, src_h) <= edge:
                return None, None
            ratio = (src_w, src_h)
        scale = edge / min(ratio)
        if src_w <= ratio[0] * scale and src_h <= ratio[1] * scale:
            scale = min(src_w / ratio[0], src_h / ratio[1])
    return tuple(max(2, int(v * scale // 2 * 2)) for v in ratio)
