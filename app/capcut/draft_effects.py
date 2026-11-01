"""Basic native effects; no video baking or external resource paths.

International CapCut IDs/parameter names verified against VectCutAPI's
pyJianYingDraft/metadata/capcut_effect_meta.py (not JianYing's IDs).
Segment layout also matches a local Windows CapCut 9.2 effect track.
"""
import math


def native_blur(layer, make_id):
    opacity = float(layer.get("opacity", 1)) * float(layer.get("blur_opacity", 1))
    strength = float(layer.get("blur_strength", 20))
    if not all(math.isfinite(x) for x in (opacity, strength)):
        raise ValueError("Invalid blur layer strength or opacity")
    if opacity <= 0:
        return None
    mosaic = bool(layer.get("pixelate", False))
    resource_id, effect_id, name = (("6770564289074827784", "15207266", "Mosaic") if mosaic
                                   else ("6739752823140913675", "15206412", "Blur"))
    # Approximate intensity only; CapCap's pixel radius is not CapCut's
    # normalized effect amount. The user can adjust it in CapCut afterwards.
    amount = float(layer.get("pixelate_size", 12)) / 100. if mosaic else strength / 100.
    if not math.isfinite(amount):
        raise ValueError("Invalid blur layer intensity")
    material = {
        "id": make_id(), "type": "video_effect", "name": name,
        "effect_id": effect_id, "resource_id": resource_id,
        "apply_target_type": 0, "bind_segment_id": "", "platform": "all", "source_platform": 0,
        "value": 1., "category_id": "", "category_name": "",
        "adjust_params": [{"name": "effects_adjust_blur", "value": max(0., min(1., amount)), "default_value": .5}],
        "apply_time_range": None, "time_range": None, "version": "", "formula_id": "",
        "common_keyframes": [], "disable_effect_faces": [], "render_index": 11000, "track_render_index": 0,
        "enable_mask": True, "effect_mask": [],
    }
    return material


def rectangle_mask(layer, source_size, make_id, *, mirrored=False):
    """Source-normalized top-left rectangle -> native material-space mask.

    Mask is attached to a copy of the source clip, so its existing Fit/Fill
    transform carries both the image and mask into the output canvas.
    CapCap applies mirror before regional effects; undo that mirror in the
    material-space mask so the exported clip flip leaves the region in place.
    Layout/ID: VectCutAPI video_segment.Mask + capcut_mask_meta.Rectangle.
    """
    sw, sh = source_size
    x, y, w, h = (float(layer.get(key, default)) for key, default in (
        ("position_x", 0.), ("position_y", 0.), ("width", .4), ("height", .1)))
    if not all(math.isfinite(v) for v in (x, y, w, h)) or min(sw, sh) < 2:
        raise ValueError("Invalid mask region")
    if w <= 0 or h <= 0:
        return None
    # Match the preview's source-pixel rounding and minimum crop size.
    px, py = max(0, min(sw - 2, round(x * sw))), max(0, min(sh - 2, round(y * sh)))
    pw, ph = max(2, min(sw - px, round(w * sw))), max(2, min(sh - py, round(h * sh)))
    cx, cy = 2 * (px + pw / 2) / sw - 1, 1 - 2 * (py + ph / 2) / sh
    return {
        "id": make_id(), "type": "mask", "category": "video", "category_id": "", "category_name": "",
        "name": "Rectangle", "platform": "all", "position_info": "", "resource_type": "rectangle",
        "resource_id": "7374021450748924432",
        "config": {"aspectRatio": 1., "centerX": -cx if mirrored else cx, "centerY": cy,
                   "width": pw / sw, "height": ph / sh, "rotation": 0., "feather": 0.,
                   "roundCorner": 0., "expansion": 0., "invert": False},
    }
