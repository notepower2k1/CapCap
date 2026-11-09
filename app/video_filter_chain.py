"""Shared FFmpeg video-filter normalization and chain construction."""

from __future__ import annotations

import os


FILTER_FIELDS = (
    "brightness",
    "contrast",
    "saturation",
    "gamma",
    "hue",
    "temperature",
    "highlights",
    "shadows",
)


def normalize_video_filter_state(video_filter_state) -> dict:
    if not isinstance(video_filter_state, dict):
        return {}
    final_values = video_filter_state.get("final", video_filter_state)
    if not isinstance(final_values, dict):
        return {}
    normalized = {}
    normalized["mirror_horizontal"] = video_filter_state.get("mirror_horizontal", False) is True
    for field in FILTER_FIELDS:
        try:
            normalized[field] = max(-100.0, min(100.0, float(final_values.get(field, 0.0))))
        except (TypeError, ValueError):
            normalized[field] = 0.0
    lut_path = str(video_filter_state.get("lut_path", "") or "").strip()
    normalized["lut_path"] = lut_path if lut_path and os.path.exists(lut_path) else ""
    try:
        normalized["lut_strength"] = max(0.0, min(1.0, float(video_filter_state.get("lut_strength", 0.0) or 0.0)))
    except (TypeError, ValueError):
        normalized["lut_strength"] = 0.0
    return normalized


def _escape_ffmpeg_filter_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    return value.replace(":", "\\:").replace("'", "\\'")


def build_video_filter_chain(video_filter_state=None) -> str:
    """Return the authoritative FFmpeg color/LUT chain.

    Export callers with Blur/Mask stage color first, apply Blur then Mask,
    and append ``build_video_lut_chain`` last so the effective order matches
    MPV gpu-next.
    """
    values = normalize_video_filter_state(video_filter_state)
    if not values:
        return ""
    brightness = values["brightness"]
    contrast = values["contrast"]
    saturation = values["saturation"]
    temperature = values["temperature"]
    highlights = values["highlights"]
    shadows = values["shadows"]
    lut_path = values["lut_path"]
    lut_strength = values["lut_strength"]
    if not values["mirror_horizontal"] and not any(abs(values[field]) > 0.01 for field in FILTER_FIELDS) and not (lut_path and lut_strength > 0.001):
        return ""

    chain = ["hflip"] if values["mirror_horizontal"] else []
    eq_parts = []
    if abs(brightness) > 0.01:
        eq_parts.append(f"brightness={max(-1.0, min(1.0, brightness / 100.0 * 0.35)):.4f}")
    if abs(contrast) > 0.01:
        eq_parts.append(f"contrast={max(0.4, min(1.8, 1.0 + contrast / 100.0 * 0.65)):.4f}")
    if abs(saturation) > 0.01:
        eq_parts.append(f"saturation={max(0.0, min(2.2, 1.0 + saturation / 100.0 * 1.2)):.4f}")
    gamma = values["gamma"]
    if abs(gamma) > 0.01:
        eq_parts.append(f"gamma={max(0.5, min(2.0, 1.0 + gamma / 100.0 * 0.75)):.4f}")
    if eq_parts:
        chain.append("eq=" + ":".join(eq_parts))
    hue = values["hue"]
    if abs(hue) > 0.01:
        chain.append(f"hue=h={max(-180.0, min(180.0, hue * 1.8)):.3f}")
    if abs(temperature) > 0.01:
        temp = max(-0.35, min(0.35, temperature / 100.0 * 0.35))
        chain.append(f"colorbalance=rs={temp:.4f}:gs={temp * 0.2:.4f}:bs={-temp:.4f}")
    if abs(highlights) > 0.01 or abs(shadows) > 0.01:
        shadow_point = max(0.0, min(0.45, 0.25 + shadows / 100.0 * 0.18))
        highlight_point = max(0.55, min(1.0, 0.75 + highlights / 100.0 * 0.18))
        chain.append("curves=master=" f"'0/0 0.25/{shadow_point:.3f} 0.75/{highlight_point:.3f} 1/1'")
    # Canonical V1 order: color adjustments first, then LUT. MPV gpu-next
    # applies its native LUT after the managed vf graph (which contains
    # color, Blur, and Mask), so this keeps Export in the same logical order:
    # color -> Blur/Mask -> LUT.
    if lut_path and lut_strength > 0.001:
        if chain:
            chain = [",".join(chain)]
        chain.append(
            "split=2[base][lutsrc];"
            f"[lutsrc]lut3d=file='{_escape_ffmpeg_filter_path(lut_path)}'[lutapplied];"
            f"[base][lutapplied]blend=all_expr='A*(1-{lut_strength:.4f})+B*{lut_strength:.4f}'"
        )
    return ",".join(part for part in chain if part)


def build_video_color_chain(video_filter_state=None) -> str:
    """Return only color adjustments, excluding the LUT stage."""
    values = normalize_video_filter_state(video_filter_state)
    if not values:
        return ""
    state = dict(video_filter_state or {}) if isinstance(video_filter_state, dict) else {}
    state["lut_path"] = ""
    state["lut_strength"] = 0.0
    return build_video_filter_chain(state)


def build_video_lut_chain(video_filter_state=None) -> str:
    """Return only the LUT blend stage for chaining after Blur/Mask."""
    values = normalize_video_filter_state(video_filter_state)
    if not values or not values["lut_path"] or values["lut_strength"] <= 0.001:
        return ""
    lut_path = values["lut_path"]
    strength = values["lut_strength"]
    return (
        "split=2[base][lutsrc];"
        f"[lutsrc]lut3d=file='{_escape_ffmpeg_filter_path(lut_path)}'[lutapplied];"
        f"[base][lutapplied]blend=all_expr='A*(1-{strength:.4f})+B*{strength:.4f}'"
    )
