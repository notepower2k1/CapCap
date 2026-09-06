import os
import time
import re

from services import EngineRuntime, ProjectService


class ExportWorkflow:
    def _emit_progress(self, on_progress, percent: int, message: str):
        if callable(on_progress):
            on_progress(int(percent), str(message or "Exporting video..."))

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.project_service = ProjectService(workspace_root)
        self.engine_runtime = EngineRuntime()

    def _load_state(self, project_state_path: str = ""):
        return self.project_service.load_project(project_state_path) if project_state_path else None

    def _mark_started(self, state):
        if state:
            self.project_service.update_step(state, "export", "running")

    def _mark_failed(self, state):
        if state:
            self.project_service.update_step(state, "export", "failed")

    def _mark_completed(self, state, output_path: str):
        if not state:
            return
        self.project_service.update_artifact(state, "final_video", output_path, save=False)
        self.project_service.update_step(state, "export", "done", save=False)
        self.project_service.save_project(state)

    def _subtitle_options(self, subtitle_style):
        style = dict(subtitle_style or {})
        style.setdefault("alignment", 2)
        style.setdefault("margin_v", 30)
        style.setdefault("font_name", "Arial")
        style.setdefault("font_size", 18)
        style.setdefault("font_color", "&H00FFFFFF")
        style.setdefault("background_box", False)
        style.setdefault("animation", "Static")
        style.setdefault("custom_position_enabled", False)
        style.setdefault("custom_position_x", 50)
        style.setdefault("custom_position_y", 86)
        style.setdefault("highlight_color", style.get("font_color", "&H00FFFFFF"))
        style.setdefault("outline_color", "&H00000000")
        style.setdefault("outline_width", 2.0)
        style.setdefault("shadow_color", "&H80000000")
        style.setdefault("shadow_depth", 1.0)
        style.setdefault("background_color", "&H80000000")
        style.setdefault("background_alpha", 0.5)
        style.setdefault("bold", False)
        style.setdefault("preset_key", "")
        style.setdefault("auto_keyword_highlight", False)
        style.setdefault("animation_duration", 0.22)
        style.setdefault("manual_highlights", [])
        style.setdefault("word_timings", [])
        style.setdefault("karaoke_timing_mode", "vietnamese")
        style.setdefault("single_line", False)
        style.setdefault("blur_region", None)
        return style

    def _resolve_target_dimensions(self, video_path: str, output_quality: str, output_ratio: str = "source"):
        key = str(output_quality or "source").strip().lower()
        ratio = self._resolve_target_ratio(output_ratio)

        src_w, src_h = self.engine_runtime.get_video_dimensions(video_path)
        if not src_w or not src_h:
            return None, None

        if key in ("", "source", "same", "original", "auto"):
            if not ratio:
                return None, None
            src_ratio = src_w / src_h
            target_ratio = ratio[0] / ratio[1]
            if abs(src_ratio - target_ratio) < 0.001:
                return None, None
            fit_scale = min(src_w / ratio[0], src_h / ratio[1])
            return (
                max(2, int((ratio[0] * fit_scale) // 2 * 2)),
                max(2, int((ratio[1] * fit_scale) // 2 * 2)),
            )

        if key in ("720", "720p", "hd"):
            short_edge = 720
        elif key in ("1080", "1080p", "fullhd", "fhd", "full hd", "full"):
            short_edge = 1080
        elif key in ("1440", "1440p", "2k", "qhd"):
            short_edge = 1440
        elif key in ("2160", "2160p", "4k", "uhd"):
            short_edge = 2160
        else:
            return None, None

        if ratio:
            target_scale = short_edge / min(ratio)
            base_w = max(2, int((ratio[0] * target_scale) // 2 * 2))
            base_h = max(2, int((ratio[1] * target_scale) // 2 * 2))
            if src_w <= base_w and src_h <= base_h:
                fit_scale = min(src_w / ratio[0], src_h / ratio[1])
                base_w = max(2, int((ratio[0] * fit_scale) // 2 * 2))
                base_h = max(2, int((ratio[1] * fit_scale) // 2 * 2))
            return base_w, base_h

        portrait = src_h > src_w
        base_w, base_h = (short_edge, int(round(short_edge * 16 / 9))) if portrait else (int(round(short_edge * 16 / 9)), short_edge)
        if src_w <= base_w and src_h <= base_h:
            return None, None
        return base_w, base_h

    def _resolve_target_fps(self, output_fps: str):
        key = str(output_fps or "source").strip().lower()
        if key in ("", "source", "same", "original", "auto"):
            return None
        try:
            fps = int(float(key))
        except Exception:
            return None
        return fps if fps > 0 else None

    def _resolve_target_ratio(self, output_ratio: str):
        key = str(output_ratio or "source").strip().lower()
        ratio_map = {
            "16:9": (16, 9),
            "9:16": (9, 16),
            "1:1": (1, 1),
            "4:3": (4, 3),
        }
        return ratio_map.get(key)

    def _build_temp_mux_path(self, project_temp_dir: str = "") -> str:
        tmp_dir = str(project_temp_dir or "").strip() or os.path.join(self.workspace_root, "temp")
        os.makedirs(tmp_dir, exist_ok=True)
        return os.path.join(tmp_dir, f"final_mux_{int(time.time())}.mp4")

    def _export_subtitle_video(
        self,
        *,
        video_path: str,
        srt_path: str,
        ass_path: str,
        output_path: str,
        subtitle_style,
        target_width=None,
        target_height=None,
        output_scale_mode="fit",
        output_fill_focus_x=0.5,
        output_fill_focus_y=0.5,
        output_fps=None,
        video_filter_state=None,
        mask_regions=None,
        logo_layers=None,
        blur_regions=None,
        text_ass_path="",
        text_image_layers=None,
        original_audio_gain_db=0.0,
        video_quality="medium",
    ):
        print(f"[Export] _export_subtitle_video: mask_regions={mask_regions}, logo_layers={logo_layers}")
        print(f"[Export] ass_path={ass_path}, exists={os.path.exists(ass_path) if ass_path else False}")
        effective_ass_path = ass_path if ass_path and os.path.exists(ass_path) else text_ass_path
        secondary_text_ass = text_ass_path if effective_ass_path != text_ass_path else ""
        if effective_ass_path and os.path.exists(effective_ass_path):
            ok = self.engine_runtime.embed_ass_subtitles(
                video_path,
                effective_ass_path,
                output_path,
                blur_region=blur_regions if blur_regions else subtitle_style.get("blur_region"),
                mask_regions=mask_regions,
                logo_layers=logo_layers,
                text_ass_path=secondary_text_ass,
                text_image_layers=text_image_layers,
                target_width=target_width,
                target_height=target_height,
                output_scale_mode=output_scale_mode,
                output_fill_focus_x=output_fill_focus_x,
                output_fill_focus_y=output_fill_focus_y,
                output_fps=output_fps,
                video_filter_state=video_filter_state,
                audio_gain_db=original_audio_gain_db,
                video_quality=video_quality,
            )
        else:
            ok = self.engine_runtime.embed_subtitles(
                video_path,
                srt_path,
                output_path,
                subtitle_style=self._subtitle_options(subtitle_style),
                mask_regions=mask_regions,
                logo_layers=logo_layers,
                text_image_layers=text_image_layers,
                target_width=target_width,
                target_height=target_height,
                output_scale_mode=output_scale_mode,
                output_fill_focus_x=output_fill_focus_x,
                output_fill_focus_y=output_fill_focus_y,
                output_fps=output_fps,
                video_filter_state=video_filter_state,
                audio_gain_db=original_audio_gain_db,
                video_quality=video_quality,
            )
        if not ok:
            raise RuntimeError("Failed to burn subtitles into the output video.")

    def _extract_overlay_layers(self, state):
        from app.layers.text import TEXT_LAYER_EXPORT_SCALE
        """Extract timed visual layers from project state for export."""
        mask_regions = []
        logo_layers = []
        text_layers = []
        blur_regions = []
        
        if not state:
            print("[Export] No project state available, skipping overlay extraction")
            return mask_regions, logo_layers, text_layers, blur_regions
        
        try:
            # Extract logo layers from timeline
            timeline_data = state.artifacts.get("timeline")
            print(f"[Export] timeline artifact: {timeline_data}")
            if timeline_data:
                import json
                # timeline_data is a file path, not JSON string
                if isinstance(timeline_data, str) and os.path.exists(timeline_data):
                    print(f"[Export] Reading timeline from file: {timeline_data}")
                    try:
                        with open(timeline_data, "r", encoding="utf-8") as f:
                            timeline_data = json.load(f)
                    except (json.JSONDecodeError, IOError) as e:
                        print(f"[Export] Failed to read timeline file: {e}")
                        timeline_data = None
                elif isinstance(timeline_data, str) and timeline_data.strip():
                    # Fallback: try parsing as JSON string (legacy format)
                    try:
                        timeline_data = json.loads(timeline_data)
                    except json.JSONDecodeError:
                        timeline_data = None
            
            if timeline_data and isinstance(timeline_data, dict):
                tracks = timeline_data.get("tracks", [])
                print(f"[Export] Found {len(tracks)} track(s) in timeline")
                for track in tracks:
                    track_type = track.get("type", "")
                    track_name = track.get("name", "")
                    layers = track.get("layers", [])
                    print(f"[Export] Checking track: name='{track_name}' type='{track_type}' has {len(layers)} layer(s)")
                    
                    # Extract mask regions from mask track (type="mask")
                    if track_type == "mask":
                        for layer in layers:
                            try:
                                mask_regions.append({
                                    "x": float(layer.get("position_x", 0.3)),
                                    "y": float(layer.get("position_y", 0.4)),
                                    "width": float(layer.get("width", 0.4)),
                                    "height": float(layer.get("height", 0.2)),
                                    "color": str(layer.get("color", "#000000")),
                                    "mode": str(layer.get("mode", "solid")),
                                    "opacity": float(layer.get("opacity", 1.0)),
                                    "pixelate_size": int(layer.get("pixelate_size", 12)),
                                    "blur_strength": int(layer.get("blur_strength", 20)),
                                    "start": max(0.0, float(layer.get("start", 0.0))),
                                    "end": max(0.0, float(layer.get("end", 0.0))),
                                })
                            except (TypeError, ValueError):
                                continue
                        print(f"[Export] Extracted {len(mask_regions)} mask region(s) from mask track")

                    if track_type == "blur":
                        for layer in layers:
                            if not layer.get("visible", True):
                                continue
                            try:
                                blur_regions.append({
                                    "x": float(layer.get("position_x", 0.0)),
                                    "y": float(layer.get("position_y", 0.0)),
                                    "width": float(layer.get("width", 0.0)),
                                    "height": float(layer.get("height", 0.0)),
                                    "blur_strength": float(layer.get("blur_strength", 20.0)),
                                    "blur_opacity": float(layer.get("blur_opacity", 1.0)),
                                    "start": max(0.0, float(layer.get("start", 0.0))),
                                    "end": max(0.0, float(layer.get("end", 0.0))),
                                })
                            except (TypeError, ValueError):
                                continue
                    
                    # Extract logo/image layers from image track (type="image")
                    if track_type == "image":
                        for layer in layers:
                            if layer.get("visible", True):
                                source = layer.get("source", "")
                                if not source:
                                    continue
                                transform = layer.get("transform", {})
                                # Transform x,y are in pixels, need to normalize to 0-1
                                # For now, assume they are percentages (0-100) and convert to 0-1
                                x = float(transform.get("x", 10.0)) / 100.0
                                y = float(transform.get("y", 10.0)) / 100.0
                                # Scale is relative, assume 0.2 (20%) as base size
                                scale_x = float(transform.get("scale_x", 1.0))
                                scale_y = float(transform.get("scale_y", 1.0))
                                w = 0.2 * scale_x
                                h = 0.2 * scale_y
                                logo_layers.append({
                                    "source": str(source),
                                    "x": x,
                                    "y": y,
                                    "width": w,
                                    "height": h,
                                    "opacity": float(layer.get("opacity", 1.0)),
                                    "rotation": float(transform.get("rotation", 0.0)),
                                    "start": max(0.0, float(layer.get("start", 0.0))),
                                    "end": max(0.0, float(layer.get("end", 0.0))),
                                })
                                print(f"[Export] Added logo layer: source={source}, x={x}, y={y}, w={w}, h={h}")
            
                    # Extract ordinary text layers. Their transform x/y is
                    # already normalized (unlike legacy logo percentages).
                    if track_type == "text":
                        for layer in layers:
                            if not layer.get("visible", True):
                                continue
                            text = str(layer.get("text", "") or "").strip()
                            if not text:
                                continue
                            transform = layer.get("transform", {}) or {}
                            try:
                                text_layers.append({
                                    "text": text,
                                    "font_name": str(layer.get("font_name", "Arial") or "Arial"),
                                # Text preview uses the same Qt-to-libass
                                # calibration as subtitles (0.85). Persist
                                # the logical source value in the project,
                                # then apply the calibration only to the ASS
                                # export size so both views match visually.
                                "font_size": max(1, int(round(float(layer.get("font_size", 60) or 60) * TEXT_LAYER_EXPORT_SCALE))),
                                    "font_color": str(layer.get("font_color", "#FFFFFF") or "#FFFFFF"),
                                    "background_color": str(layer.get("background_color", "") or ""),
                                    "background_opacity": max(0.0, min(1.0, float(layer.get("background_opacity", 0.5) or 0.0))),
                                    "font_bold": bool(layer.get("font_bold", False)),
                                    "font_italic": bool(layer.get("font_italic", False)),
                                    "font_underline": bool(layer.get("font_underline", False)),
                                    "x": max(0.0, min(1.0, float(transform.get("x", 0.5)))),
                                    "y": max(0.0, min(1.0, float(transform.get("y", 0.5)))),
                                    "start": max(0.0, float(layer.get("start", 0.0))),
                                    "end": max(0.0, float(layer.get("end", 0.0))),
                                })
                            except (TypeError, ValueError):
                                continue

            # Fallback: extract mask regions from settings if not found in timeline
            if not mask_regions:
                mask_state = state.settings.get("mask_state", {})
                print(f"[Export] Fallback: checking settings mask_state: {mask_state}")
                if mask_state and mask_state.get("enabled", False):
                    regions = mask_state.get("regions", [])
                    print(f"[Export] Found {len(regions)} mask region(s) in settings")
                    for region in regions:
                        mask_regions.append({
                            "x": float(region.get("x", 0.3)),
                            "y": float(region.get("y", 0.4)),
                            "width": float(region.get("width", 0.4)),
                            "height": float(region.get("height", 0.2)),
                            "mode": str(region.get("mode", "solid")),
                            "color": str(region.get("color", "#000000")),
                            "pixelate_size": int(region.get("pixelate_size", 12)),
                            "blur_strength": int(region.get("blur_strength", 20)),
                        })
        except Exception as e:
            print(f"Warning: Failed to extract overlay layers: {e}")
            import traceback
            traceback.print_exc()
        
        print(f"[Export] Final overlay extraction: {len(mask_regions)} mask(s), {len(logo_layers)} logo(s), {len(text_layers)} text layer(s), {len(blur_regions)} blur(s)")
        return mask_regions, logo_layers, text_layers, blur_regions

    @staticmethod
    def _ass_timestamp(seconds: float) -> str:
        total = max(0, int(round(float(seconds) * 100)))
        hours, total = divmod(total, 360000)
        minutes, total = divmod(total, 6000)
        whole, centis = divmod(total, 100)
        return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"

    @staticmethod
    def _ass_color(value: str) -> str:
        value = str(value or "#FFFFFF").strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
            value = "FFFFFF"
        return f"&H00{value[4:6]}{value[2:4]}{value[0:2]}"

    @classmethod
    def _ass_color_with_opacity(cls, value: str, opacity: float) -> str:
        """Return an ASS BGR color with opacity converted to ASS alpha."""
        base = cls._ass_color(value)
        alpha = max(0, min(255, int(round((1.0 - max(0.0, min(1.0, float(opacity)))) * 255))))
        return f"&H{alpha:02X}{base[4:]}"

    def _build_text_layer_ass(self, ass_path: str, text_layers, temp_dir: str = "", width=None, height=None) -> str:
        """Append editor TextLayer events to a disposable ASS file for export."""
        from app.layers.text import TEXT_LAYER_PADDING_X
        if not text_layers:
            return ass_path
        try:
            if ass_path and os.path.exists(ass_path):
                with open(ass_path, "r", encoding="utf-8-sig") as handle:
                    source = handle.read()
                width = int(re.search(r"(?mi)^PlayResX:\s*(\d+)", source).group(1))
                height = int(re.search(r"(?mi)^PlayResY:\s*(\d+)", source).group(1))
            else:
                width, height = max(1, int(width or 1920)), max(1, int(height or 1080))
                source = (
                    f"[Script Info]\nPlayResX: {width}\nPlayResY: {height}\n\n[V4+ Styles]\n"
                    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                    "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                )
        except Exception as exc:
            print(f"[Export] Could not build TextLayer ASS overlay: {exc}")
            return ass_path
        styles, events = [], []
        for index, layer in enumerate(text_layers):
            style_name = f"CapCapText{index}"
            styles.append(
                f"Style: {style_name},{layer['font_name']},{layer['font_size']},{self._ass_color(layer['font_color'])},"
                # Keep this field order exactly aligned with the ASS
                # Format line (including StrikeOut). A shifted style record
                # can make libass reject the whole subtitle style section.
                f"&H000000FF,{self._ass_color_with_opacity(layer['background_color'], layer.get('background_opacity', 0.5)) if layer.get('background_color') else '&H00000000'},{self._ass_color_with_opacity(layer['background_color'], layer.get('background_opacity', 0.5)) if layer.get('background_color') else '&H00000000'},{ -1 if layer['font_bold'] else 0},0,0,0,100,100,0,0,{3 if layer.get('background_color') else 1},{TEXT_LAYER_PADDING_X if layer.get('background_color') else 0},0,5,0,0,0,0"
            )
            text = str(layer["text"]).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\r\n", "\n").replace("\n", "\\N")
            start, end = float(layer["start"]), float(layer["end"])
            if end <= start:
                end = start + 0.01
            x = int(round(float(layer["x"]) * width))
            y = int(round(float(layer["y"]) * height))
            events.append(f"Dialogue: 10,{self._ass_timestamp(start)},{self._ass_timestamp(end)},{style_name},,0,0,0,,{{\\an5\\pos({x},{y})}}{text}")
        output_dir = str(temp_dir or os.path.dirname(ass_path) or os.path.join(self.workspace_root, "temp"))
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"text_layers_{int(time.time() * 1000)}.ass")
        source = source.replace("[Events]", "\n".join(styles) + "\n[Events]", 1)
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(source.rstrip() + "\n" + "\n".join(events) + "\n")
        print(f"[Export] Added {len(text_layers)} TextLayer event(s) to {output_path}")
        return output_path

    def _build_text_layer_images(self, text_layers, temp_dir: str, width: int, height: int) -> list[dict]:
        """Render static Text layers with the editor's Qt renderer for FFmpeg."""
        if not text_layers:
            return []
        from app.layers.text_renderer import render_text_layer

        base_temp_dir = str(temp_dir or "").strip() or os.path.join(self.workspace_root, "temp")
        output_dir = os.path.join(base_temp_dir, "text_layer_images")
        os.makedirs(output_dir, exist_ok=True)
        result = []
        for index, layer in enumerate(text_layers):
            try:
                rendered = render_text_layer(layer, int(width), int(height))
                path = os.path.join(output_dir, f"text_layer_{index}_{int(time.time() * 1000)}.png")
                if not rendered.image.save(path, "PNG"):
                    raise RuntimeError("QImage could not save PNG")
                result.append({
                    "path": path,
                    "x": int(round(rendered.rect.left())),
                    "y": int(round(rendered.rect.top())),
                    "start": float(layer.get("start", 0.0) or 0.0),
                    "end": float(layer.get("end", 0.0) or 0.0),
                })
                print(
                    f"[Export Geometry] TextLayer {index}: center="
                    f"({float(layer.get('x', 0.5)):.4f},{float(layer.get('y', 0.5)):.4f}) "
                    f"canvas={int(width)}x{int(height)}"
                )
            except Exception as exc:
                print(f"[Export] Could not render TextLayer {index} as PNG: {exc}")
        print(f"[Export] Rendered {len(result)} TextLayer image overlay(s).")
        return result

    def _ensure_subtitle_ass(self, ass_path: str, srt_path: str, subtitle_style, video_path: str, target_width=None, target_height=None) -> str:
        """Build ASS from the active SRT and current style controls.

        A project may already contain an ASS file from an earlier preview or
        export.  Reusing that file would silently ignore changes made in the
        subtitle style UI (notably background box, color, and opacity).
        """
        # The editor's live preview ASS is generated from the current segment
        # list and style immediately before export. Reusing it makes the
        # libass event geometry byte-for-byte identical in MPV and FFmpeg.
        # Other ASS paths are still rebuilt from SRT so stale legacy exports
        # cannot silently ignore style changes.
        if ass_path and os.path.exists(ass_path) and os.path.basename(ass_path).lower().startswith("live_preview_"):
            print(f"[Export] Reusing live preview ASS for WYSIWYG: {ass_path}")
            return ass_path
        if not srt_path or not os.path.exists(srt_path):
            return ass_path if ass_path and os.path.exists(ass_path) else ""
        from video_processor import srt_to_ass
        style = self._subtitle_options(subtitle_style)
        source_w, source_h = self.engine_runtime.get_video_dimensions(video_path)
        width = int(target_width or source_w or 1920)
        height = int(target_height or source_h or 1080)
        generated = srt_to_ass(
            srt_path, width, height,
            alignment=int(style["alignment"]), margin_v=int(style["margin_v"]),
            font_name=str(style["font_name"]), font_size=int(style["font_size"]),
            font_color=str(style["font_color"]), background_box=bool(style["background_box"]),
            animation_style=str(style["animation"]), highlight_color=str(style["highlight_color"]),
            outline_color=str(style["outline_color"]), outline_width=float(style["outline_width"]),
            shadow_color=str(style["shadow_color"]), shadow_depth=float(style["shadow_depth"]),
            background_color=str(style["background_color"]), background_alpha=float(style["background_alpha"]),
            background_width=str(style.get("background_width", "fit_text")), background_shape=str(style.get("background_shape", "rectangle")),
            background_padding=float(style.get("background_padding", 6)),
            background_radius=float(style.get("background_radius", 0)),
            bold=bool(style["bold"]), preset_key=str(style["preset_key"]),
            auto_keyword_highlight=bool(style["auto_keyword_highlight"]),
            animation_duration=float(style["animation_duration"]), manual_highlights=style["manual_highlights"],
            word_timings=style["word_timings"], karaoke_timing_mode=str(style["karaoke_timing_mode"]),
            speaker_colors=style.get("speaker_colors", []),
            custom_position_enabled=bool(style["custom_position_enabled"]),
            custom_position_x=float(style["custom_position_x"]), custom_position_y=float(style["custom_position_y"]),
            custom_position_bottom_y=style.get("custom_position_bottom_y"),
            single_line=bool(style["single_line"]), log_generation=True,
            font_scale=float(style.get("font_scale", 1.0)),
        )
        print(f"[Export] Generated missing subtitle ASS: {generated}")
        return generated

    def run(
        self,
        *,
        video_path: str,
        output_path: str,
        mode: str,
        srt_path: str = "",
        ass_path: str = "",
        audio_path: str = "",
        subtitle_style=None,
        output_quality: str = "source",
        output_fps: str = "source",
        output_ratio: str = "source",
        output_scale_mode: str = "fit",
        output_fill_focus_x: float = 0.5,
        output_fill_focus_y: float = 0.5,
        video_filter_state=None,
        original_audio_gain_db: float = 0.0,
        project_state_path: str = "",
        project_temp_dir: str = "",
        on_progress=None,
        video_quality: str = "medium",
    ) -> str:
        subtitle_style = subtitle_style or {}
        target_w, target_h = self._resolve_target_dimensions(video_path, output_quality, output_ratio)
        target_fps = self._resolve_target_fps(output_fps)
        # MPV renders the live ASS track on the source frame before its
        # Fit/Fill presentation transform.  Author export ASS in that same
        # source render space; embed_ass_subtitles applies the canvas transform
        # after the ASS pass so preview and export share one coordinate system.
        source_w, source_h = self.engine_runtime.get_video_dimensions(video_path)
        ass_style = dict(subtitle_style)
        if ass_style.get("custom_position_enabled") and target_w and target_h:
            try:
                mode_key = str(output_scale_mode or "fit").strip().lower()
                fx = max(0.0, min(1.0, float(output_fill_focus_x)))
                fy = max(0.0, min(1.0, float(output_fill_focus_y)))
                scale = max(target_w / source_w, target_h / source_h) if mode_key == "fill" else min(target_w / source_w, target_h / source_h)
                displayed_w, displayed_h = source_w * scale, source_h * scale
                offset_x = (target_w - displayed_w) * (fx if mode_key == "fill" else 0.5)
                offset_y = (target_h - displayed_h) * (fy if mode_key == "fill" else 0.5)
                x_canvas = float(ass_style.get("custom_position_x", 50.0)) * target_w / 100.0
                y_canvas = float(ass_style.get("custom_position_y", 86.0)) * target_h / 100.0
                ass_style["custom_position_x"] = max(0.0, min(100.0, (x_canvas - offset_x) * 100.0 / displayed_w))
                ass_style["custom_position_y"] = max(0.0, min(100.0, (y_canvas - offset_y) * 100.0 / displayed_h))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        ass_path = self._ensure_subtitle_ass(
            ass_path, srt_path, ass_style, video_path, source_w, source_h
        )

        state = self._load_state(project_state_path)
        self._mark_started(state)
        self._emit_progress(on_progress, 5, "Preparing final export...")
        
        print(f"[Export] Project state loaded: {state is not None}")
        if state:
            print(f"[Export] Project root: {state.project_root}")
            print(f"[Export] Artifacts: {list(state.artifacts.keys())}")
        
        # Text is rendered by Qt into cropped transparent PNGs, matching the
        # editor's QPainter/QFontMetrics geometry instead of libass metrics.
        mask_regions, logo_layers, text_layers, blur_regions = self._extract_overlay_layers(state)
        render_w, render_h = target_w, target_h
        if not render_w or not render_h:
            render_w, render_h = self.engine_runtime.get_video_dimensions(video_path)
        # Text layers are rendered to target-canvas PNGs.  Their stored
        # normalized positions are source-video coordinates, so use the same
        # Fit/Fill transform as the preview before Qt renders the bitmap.
        if target_w and target_h and text_layers:
            # Text x/y is a centre anchor (unlike rectangle layers' top-left).
            sw, sh = self.engine_runtime.get_video_dimensions(video_path)
            try:
                scale = max(target_w / float(sw), target_h / float(sh)) if str(output_scale_mode).lower() == "fill" else min(target_w / float(sw), target_h / float(sh))
                dw, dh = float(sw) * scale, float(sh) * scale
                fx = max(0.0, min(1.0, float(output_fill_focus_x)))
                fy = max(0.0, min(1.0, float(output_fill_focus_y)))
                ox = (target_w - dw) * (fx if str(output_scale_mode).lower() == "fill" else 0.5)
                oy = (target_h - dh) * (fy if str(output_scale_mode).lower() == "fill" else 0.5)
                for layer in text_layers:
                    layer["x"] = (float(layer.get("x", 0.5)) * dw + ox) / float(target_w)
                    layer["y"] = (float(layer.get("y", 0.5)) * dh + oy) / float(target_h)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        text_image_layers = self._build_text_layer_images(text_layers, project_temp_dir, render_w or 1920, render_h or 1080)
        print(f"[Export] Extracted {len(mask_regions)} mask(s), {len(logo_layers)} logo(s), {len(text_layers)} text layer(s), {len(blur_regions)} blur(s)")

        tmp_mux_path = ""
        try:
            if mode == "subtitle":
                self._emit_progress(on_progress, 20, "Burning subtitles into the video...")
                if abs(float(original_audio_gain_db or 0.0)) > 0.001:
                    print(f"[Export] Applying A1 Original audio gain: {float(original_audio_gain_db):.2f} dB")
                self._export_subtitle_video(
                    video_path=video_path,
                    srt_path=srt_path,
                    ass_path=ass_path,
                    output_path=output_path,
                    subtitle_style=subtitle_style,
                    target_width=target_w,
                    target_height=target_h,
                    output_scale_mode=output_scale_mode,
                    output_fill_focus_x=output_fill_focus_x,
                    output_fill_focus_y=output_fill_focus_y,
                    output_fps=target_fps,
                    video_filter_state=video_filter_state,
                    mask_regions=mask_regions,
                    logo_layers=logo_layers,
                    blur_regions=blur_regions,
                    text_image_layers=text_image_layers,
                    original_audio_gain_db=original_audio_gain_db,
                    video_quality=video_quality,
                )
            elif mode == "voice":
                self._emit_progress(on_progress, 25, "Muxing Vietnamese audio into the video...")
                # Voice-only exports normally skip the ASS pass. Keep that
                # fast path when there is no Text layer, but burn text after
                # muxing when the editor contains text overlays.
                voice_output = output_path
                if text_image_layers:
                    tmp_mux_path = self._build_temp_mux_path(project_temp_dir)
                    voice_output = tmp_mux_path
                self.engine_runtime.mux_audio_for_preview(
                    video_path,
                    audio_path,
                    voice_output,
                    # The subsequent Text/overlay pass owns scaling and the
                    # color grade, so keep this intermediate audio mux a
                    # stream-copy video pass.  Otherwise the filters would
                    # be applied once here and once again below.
                    target_width=None if voice_output != output_path else target_w,
                    target_height=None if voice_output != output_path else target_h,
                    output_scale_mode=output_scale_mode,
                    focus_x=output_fill_focus_x,
                    focus_y=output_fill_focus_y,
                    output_fps=None if voice_output != output_path else target_fps,
                    video_filter_state={} if voice_output != output_path else video_filter_state,
                    video_quality=video_quality,
                )
                if voice_output != output_path:
                    self._export_subtitle_video(
                        video_path=voice_output,
                        srt_path=srt_path,
                        ass_path=ass_path,
                        output_path=output_path,
                        subtitle_style=subtitle_style,
                        target_width=target_w,
                        target_height=target_h,
                        output_scale_mode=output_scale_mode,
                        output_fill_focus_x=output_fill_focus_x,
                        output_fill_focus_y=output_fill_focus_y,
                        output_fps=target_fps,
                        video_filter_state=video_filter_state,
                        mask_regions=mask_regions,
                        logo_layers=logo_layers,
                        blur_regions=blur_regions,
                        text_image_layers=text_image_layers,
                        video_quality=video_quality,
                    )
            elif mode == "both":
                tmp_mux_path = self._build_temp_mux_path(project_temp_dir)
                self._emit_progress(on_progress, 18, "Muxing Vietnamese audio with the source video...")
                # Keep this mux fast (no scaling). Scaling happens in the subtitle-burn step.
                self.engine_runtime.mux_audio_for_preview(
                    video_path,
                    audio_path,
                    tmp_mux_path,
                    output_scale_mode=output_scale_mode,
                    focus_x=output_fill_focus_x,
                    focus_y=output_fill_focus_y,
                    output_fps=target_fps,
                    video_quality=video_quality,
                )
                self._emit_progress(on_progress, 62, "Burning styled subtitles into the final video...")
                self._export_subtitle_video(
                    video_path=tmp_mux_path,
                    srt_path=srt_path,
                    ass_path=ass_path,
                    output_path=output_path,
                    subtitle_style=subtitle_style,
                    target_width=target_w,
                    target_height=target_h,
                    output_scale_mode=output_scale_mode,
                    output_fill_focus_x=output_fill_focus_x,
                    output_fill_focus_y=output_fill_focus_y,
                    output_fps=target_fps,
                    video_filter_state=video_filter_state,
                    mask_regions=mask_regions,
                    logo_layers=logo_layers,
                    blur_regions=blur_regions,
                    text_image_layers=text_image_layers,
                    video_quality=video_quality,
                )
            else:
                raise ValueError(f"Unsupported export mode: {mode}")

            self._emit_progress(on_progress, 95, "Finalizing exported video...")
            self._mark_completed(state, output_path)
            self._emit_progress(on_progress, 100, "Export completed.")
            return output_path
        except Exception:
            self._mark_failed(state)
            raise
        finally:
            if tmp_mux_path and os.path.exists(tmp_mux_path):
                try:
                    os.remove(tmp_mux_path)
                except OSError:
                    pass
            for item in text_image_layers:
                path = str(item.get("path", "") or "")
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

