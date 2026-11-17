"""Project-owned subtitle presentation, independent of transcription and TTS."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
from pathlib import Path

KEY = "subtitle_presentation"


def config(value=None):
    value = value if isinstance(value, dict) else {}
    result = copy.deepcopy(value)
    result["mode"] = value.get("mode") if value.get("mode") in {"none", "single", "bilingual"} else "single"
    result["source"] = value.get("source") if value.get("source") in {"original", "srt", "translate"} else "original"
    result.setdefault("language", "en")
    result.setdefault("secondary_segments", [])
    result.setdefault("styles", {})
    result.setdefault("secondary_preset_id", "general_default")
    result.setdefault("secondary_prompt", "")
    return result


def valid_segments(rows):
    output = []
    for row in rows or []:
        start, end = float(row["start"]), float(row["end"])
        text = str(row.get("text", "")).strip()
        if not math.isfinite(start + end) or start < 0 or end <= start or not text:
            raise ValueError("Invalid secondary subtitle timing or empty text.")
        cleaned = dict(start=start, end=end, text=text)
        for field in ("manual_highlights", "auto_highlights"):
            if row.get(field):
                cleaned[field] = copy.deepcopy(row[field])
        if row.get("source_text"):
            cleaned["source_text"] = str(row["source_text"])
        if isinstance(row.get("_completed_timing"), dict):
            cleaned["_completed_timing"] = copy.deepcopy(row["_completed_timing"])
        output.append(cleaned)
    return sorted(output, key=lambda row: (row["start"], row["end"]))


def signature(rows, language=""):
    content = valid_segments([{key: row.get(key) for key in ("start", "end", "text")} for row in rows or []])
    return hashlib.sha256(json.dumps([language, content], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def text_signature(rows):
    return hashlib.sha256(json.dumps([str(row.get("text", "")) for row in rows or []], ensure_ascii=False).encode()).hexdigest()


def secondary_signature(rows, settings):
    cfg = config(settings)
    prompt = json.dumps([cfg["language"], cfg["secondary_preset_id"], cfg["secondary_prompt"]], ensure_ascii=False)
    return signature(rows, prompt)


def secondary_prompt(settings, source_language):
    cfg = config(settings)
    custom = str(cfg["secondary_prompt"] or "").strip()
    if custom:
        return custom
    from .translation.prompt_loader import render_preset_prompt
    return render_preset_prompt(cfg["secondary_preset_id"], source_lang=source_language,
        target_lang=cfg["language"], style_clause="", context_guidance="{{context_guidance}}")


def read_srt(path):
    from .translation.srt_utils import parse_srt
    rows = valid_segments(parse_srt(Path(path).read_text(encoding="utf-8-sig")))
    if not rows:
        raise ValueError("The SRT contains no valid subtitles.")
    return rows


def save_secondary(state, service, settings):
    """Save a portable SRT copy as well as editable source-time project data."""
    from .translation.srt_utils import to_srt
    cfg = config(settings)
    state.set_setting(KEY, cfg)
    rows = valid_segments(cfg.get("secondary_segments") or [])
    if rows:
        path = Path(state.project_root) / "subtitle" / "secondary.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_srt(rows, max_gap_ms=0), encoding="utf-8")
        state.set_artifact("subtitle_secondary_srt", str(path))
    service.save_project(state)


def build_secondary(settings, original, *, engine=None, source_language="auto", translator_ai=True, translator_style="",
                    translation_options=None):
    cfg = config(settings)
    if cfg["mode"] != "bilingual":
        return cfg
    original = valid_segments(original)
    newly_prepared = False
    print(f"[Subtitles] Preparing secondary source={cfg['source']} language={cfg['language']} source_cues={len(original)}", flush=True)
    if cfg["source"] == "original":
        if cfg.get("secondary_initialized") and not cfg["secondary_segments"]:
            raise ValueError("Secondary subtitles are empty. Create or import them before exporting.")
        if not original and not cfg["secondary_segments"]:
            raise ValueError("Original subtitles are required. Run transcription first.")
        if not cfg["secondary_segments"]:
            cfg["secondary_segments"] = copy.deepcopy(original)
            newly_prepared = True
    elif cfg["source"] == "srt":
        if not cfg["secondary_segments"]:
            cfg["secondary_segments"] = read_srt(cfg.get("srt_path", ""))
            newly_prepared = True
    else:
        # Export the saved snapshot even if the original SRT was regenerated.
        if engine is not None or not cfg["secondary_segments"]:
            if not original:
                raise ValueError("Original subtitles are required before secondary translation.")
            key = secondary_signature(original, cfg)
            if cfg.get("source_signature") != key or not cfg["secondary_segments"]:
                if engine is None:
                    raise ValueError("Secondary subtitles need translation or an update.")
                from .translation.prompt_context import translation_prompt_scope
                with translation_prompt_scope(cfg["secondary_preset_id"]):
                    translated = engine.translate_segments(copy.deepcopy(original), src_lang=source_language,
                        target_lang=cfg["language"], enable_polish=translator_ai, optimize_subtitles=False,
                        style_instruction="", custom_system_prompt=secondary_prompt(cfg, source_language),
                        **(translation_options or {}))
                if len(translated) != len(original):
                    raise RuntimeError("Secondary translation returned an incomplete set of subtitles.")
                cfg["secondary_segments"] = [dict(start=source["start"], end=source["end"], text=row["text"], source_text=source["text"])
                    for source, row in zip(original, translated)]
                cfg["source_signature"] = key
                newly_prepared = True
    cfg["secondary_segments"] = valid_segments(cfg["secondary_segments"])
    if not cfg["secondary_segments"]:
        raise ValueError("Bilingual subtitles require a non-empty secondary subtitle track.")
    cfg["status"] = "ready"
    cfg["secondary_initialized"] = True
    from app.subtitle_timing import capture
    if newly_prepared:
        capture(cfg["secondary_segments"])
    print(f"[Subtitles] Secondary ready source={cfg['source']} language={cfg['language']} cues={len(cfg['secondary_segments'])}", flush=True)
    return cfg


def map_to_timeline(rows, warps):
    from .services.time_warp_service import TimeWarpService
    return [dict(row, start=TimeWarpService.media_to_timeline_time(row["start"], warps),
                 end=TimeWarpService.media_to_timeline_time(row["end"], warps)) for row in copy.deepcopy(rows)]


def secondary_style(primary):
    style = copy.deepcopy(primary)
    style.update(font_size=max(12, round(float(primary.get("font_size", 60)) * 0.75)),
        custom_position_enabled=True, custom_position_x=50, custom_position_y=76,
        custom_position_bottom_y=None, animation="Static", manual_highlights=[], word_timings=[],
        speaker_colors=[], auto_keyword_highlight=False)
    return style


def style_from_controls(controls, base):
    style = copy.deepcopy(base)
    fields = {"font": "font_name", "size": "font_size", "bold": "bold", "background": "background_box",
              "background_alpha": "background_alpha", "background_padding": "background_padding",
              "background_width": "background_width", "background_shape": "background_shape", "background_radius": "background_radius",
              "animation": "animation", "animation_time": "animation_duration", "single_line": "single_line"}
    for key, target in fields.items():
        if key in controls:
            style[target] = controls[key]
    for key, target in (("color", "font_color"), ("background_color", "background_color")):
        value = str(controls.get(key, ""))
        if value.startswith("#") and len(value) == 7:
            style[target] = "&H00" + value[5:7] + value[3:5] + value[1:3]
    if controls.get("outline") is False:
        style["outline_width"] = 0
    style.update(controls.get("position") or {})
    style.update(manual_highlights=[], word_timings=[], speaker_colors=[], auto_keyword_highlight=False)
    return style


def canvas_to_source(style, source_size, canvas_size, mode="fit", focus=(.5, .5)):
    style = copy.deepcopy(style)
    sw, sh = source_size
    cw, ch = canvas_size
    if not all((sw, sh, cw, ch)):
        return style
    fill = mode == "fill"
    scale = max(cw / sw, ch / sh) if fill else min(cw / sw, ch / sh)
    dw, dh = sw * scale, sh * scale
    ox, oy = (cw - dw) * (focus[0] if fill else .5), (ch - dh) * (focus[1] if fill else .5)
    left, top = max(0., ox), max(0., oy)
    vw, vh = min(cw, ox + dw) - left, min(ch, oy + dh) - top
    if style.get("custom_position_enabled"):
        x, y = style.get("custom_position_x", 50) / 100, style.get("custom_position_y", 86) / 100
    else:
        alignment = int(style.get("alignment", 2))
        x = .05 if alignment == 1 else .95 if alignment == 3 else .5
        margin = float(style.get("margin_v", 30)) + float(style.get("font_size", 60)) * .6
        y = min(.95, margin / max(1., vh)) if alignment == 8 else .5 if alignment == 5 else max(.05, 1 - margin / max(1., vh))
        x += float(style.get("x_offset", 0)) / max(1., vw)
    style["custom_position_enabled"] = True
    style["custom_position_x"] = max(0., min(100., (left + x * vw - ox) * 100 / dw))
    style["custom_position_y"] = max(0., min(100., (top + y * vh - oy) * 100 / dh))
    for field in ("font_size", "outline_width", "shadow_depth", "background_padding", "background_radius", "margin_v", "x_offset"):
        if field in style:
            style[field] = float(style[field]) / scale
    if "font_size" in style:
        style["font_size"] = max(1, round(style["font_size"]))
    return style


def subtitle_to_canvas(style, source_size, canvas_size, mode="fit", focus=(.5, .5)):
    """Author ASS after the video canvas transform, including letterbox areas."""
    result = copy.deepcopy(style)
    result.pop("limit_layers_to_video", None)  # Ignore the retired project option.
    return result


def render_ass(rows, style, width, height, path):
    from .video_processor import srt_to_ass
    from .translation.srt_utils import to_srt
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_srt(rows, max_gap_ms=0), encoding="utf-8")
    options = dict(style)
    options["animation_style"] = options.pop("animation", "Static")
    options["preset_key"] = options.get("preset_key", "")
    options["log_generation"] = False
    allowed = inspect.signature(srt_to_ass).parameters
    return srt_to_ass(str(path), video_width=width, video_height=height,
                      **{key: value for key, value in options.items() if key in allowed and key not in {"video_width", "video_height", "srt_path"}})


def compose_ass(primary_ass, secondary_ass, output):
    """Rename all secondary styles; merge events without coupling cue counts."""
    primary = Path(primary_ass).read_text(encoding="utf-8-sig")
    secondary = Path(secondary_ass).read_text(encoding="utf-8-sig")
    styles, events = [], []
    for line in secondary.splitlines():
        if line.startswith("Style:"):
            prefix, values = line.split(":", 1)
            parts = values.strip().split(",")
            parts[0] = "Secondary_" + parts[0]
            styles.append(prefix + ": " + ",".join(parts))
        elif line.startswith("Dialogue:"):
            parts = line.split(":", 1)[1].strip().split(",", 9)
            parts[0] = str(int(parts[0]) + 20)
            parts[3] = "Secondary_" + parts[3]
            events.append("Dialogue: " + ",".join(parts))
    primary = primary.replace("[Events]", "\n".join(styles) + "\n\n[Events]", 1)
    Path(output).write_text(primary.rstrip() + "\n" + "\n".join(events) + "\n", encoding="utf-8-sig")
    return str(output)


def presentation_ass(cfg, primary, secondary, primary_style, width, height, directory):
    cfg = config(cfg)
    directory = Path(directory)
    primary_ass = render_ass([] if cfg["mode"] == "none" else primary, primary_style, width, height, directory / "primary.srt")
    if cfg["mode"] != "bilingual":
        return primary_ass
    if not secondary:
        raise ValueError("Secondary subtitles are missing. Create or import them before exporting.")
    style = cfg.get("secondary_render_style") or style_from_controls(cfg["styles"].get("secondary", {}), secondary_style(primary_style))
    secondary_ass = render_ass(secondary, style, width, height, directory / "secondary.srt")
    return compose_ass(primary_ass, secondary_ass, directory / "bilingual.ass")
