"""Editable text size/colour conversion and basic native decorations.

CapCap sizes are canvas pixels (with its existing 0.85 preview calibration).
CapCut's text size scales with canvas width. The 200-unit reference below is
an approximate mapping, not a pixel-perfect calibration of CapCut's font
engine. Keep source sizes in the report for desktop round-trip verification.
Native field layout: GuanYixuan/pyCapCut, pycapcut/text_segment.py.
Canvas/font-dependent rendering measurements (no universal pixel calibration):
https://github.com/Davidb-2107/capcut-cli-david/blob/master/docs/cascade-words-font-calibration.md
"""
import math
import re

TEXT_REFERENCE_WIDTH = 200.0
TEXT_PREVIEW_SCALE = 0.85


def text_style(layer, canvas_width):
    try:
        source_size = float(layer.get("font_size", 30))
    except (ValueError, TypeError):
        source_size = 30.
    if not math.isfinite(source_size) or source_size <= 0:
        source_size = 30.
    value = str(layer.get("font_color", "#FFFFFF") or "#FFFFFF").strip()
    alpha = 1.
    if value.upper().startswith("&H"):
        ass = value[2:].rstrip("&")
        if re.fullmatch(r"[0-9a-fA-F]{8}", ass):
            alpha = 1. - int(ass[:2], 16) / 255.
            value = "#" + ass[6:8] + ass[4:6] + ass[2:4]
    value = value.lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", value):
        value = "".join(c * 2 for c in value)
    elif re.fullmatch(r"[0-9a-fA-F]{8}", value):
        # QColor serializes eight-digit colours as AARRGGBB.
        alpha, value = int(value[:2], 16) / 255., value[2:]
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value, alpha = "FFFFFF", 1.
    color = "#" + value.upper()
    rgb = [int(value[i:i + 2], 16) / 255. for i in (0, 2, 4)]
    size = source_size * TEXT_PREVIEW_SCALE * TEXT_REFERENCE_WIDTH / max(1., float(canvas_width))
    return {"size": size, "source_size": source_size, "color": color, "rgb": rgb, "alpha": alpha}


def decorations(layer, canvas_width):
    """Native editable basic styles; pixel measures are approximate.

    Field layout follows pyCapCut TextBorder/TextBackground/TextShadow.
    """
    style = {key: bool(layer.get("font_" + key, False)) for key in ("bold", "italic", "underline")}
    material = {"check_flag": 7, "alignment": {"left": 0, "center": 1, "right": 2}.get(layer.get("alignment"), 1)}
    stroke = float(layer.get("outline_width", 0))
    if not math.isfinite(stroke) or stroke < 0:
        raise ValueError("Invalid text outline width")
    if stroke:
        color = text_style({"font_color": layer.get("outline_color", "#000000")}, canvas_width)
        # Native width is relative to glyph size (not output pixels).
        width = min(.2, stroke / max(1., float(layer.get("font_size", 30))))
        style["strokes"] = [{"width": width, "content": {"solid": {"alpha": color["alpha"], "color": color["rgb"]}}}]
        material["check_flag"] |= 8
    background = layer.get("background_color", "")
    if background and layer.get("background_box", True):
        color = text_style({"font_color": background}, canvas_width)
        opacity = float(layer.get("background_opacity", layer.get("background_alpha", .5)))
        if not math.isfinite(opacity):
            raise ValueError("Invalid text background opacity")
        material.update(background_style=1, background_color=color["color"],
                        background_alpha=max(0., min(1., opacity)) * color["alpha"],
                        background_round_radius=max(0., min(1., float(layer.get("background_radius", 0))/100)),
                        background_height=.14, background_width=.14,
                        background_horizontal_offset=0., background_vertical_offset=0.)
        material["check_flag"] |= 16
    depth = float(layer.get("shadow_depth", 0))
    if not math.isfinite(depth) or depth < 0:
        raise ValueError("Invalid text shadow depth")
    if depth:
        color = text_style({"font_color": layer.get("shadow_color", "#000000")}, canvas_width)
        alpha = float(layer.get("shadow_alpha", .7))
        if not math.isfinite(alpha):
            raise ValueError("Invalid text shadow opacity")
        style["shadows"] = [{"diffuse": .025, "alpha": color["alpha"] * max(0., min(1., alpha)), "distance": depth,
                             "content": {"solid": {"color": color["rgb"]}}, "angle": -45.}]
        material["check_flag"] |= 32
    return style, material
