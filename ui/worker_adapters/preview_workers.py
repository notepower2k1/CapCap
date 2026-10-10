import os
import shutil
import sys
import time

from PySide6.QtCore import QThread, Signal

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "app")
if APP_PATH not in sys.path:
    sys.path.append(APP_PATH)

from services import EngineRuntime


class PreviewMuxWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, video_path, audio_path, output_path, mode="voice", srt_path="", subtitle_style=None, render_subtitles=True, target_width=None, target_height=None, output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, video_filter_state=None, mask_regions=None, logo_layers=None, temp_dir="", parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.audio_path = audio_path
        self.output_path = output_path
        self.mode = mode
        self.srt_path = srt_path
        self.subtitle_style = subtitle_style or {}
        self.render_subtitles = bool(render_subtitles)
        self.target_width = target_width
        self.target_height = target_height
        self.output_scale_mode = output_scale_mode
        self.output_fill_focus_x = output_fill_focus_x
        self.output_fill_focus_y = output_fill_focus_y
        self.video_filter_state = video_filter_state or {}
        self.mask_regions = mask_regions or []
        self.logo_layers = logo_layers or []
        self.temp_dir = temp_dir

    def run(self):
        temp_paths = []
        try:
            from video_processor import get_video_duration
            from preview_processor import apply_timewarp_to_video_clip, mux_audio_into_video_for_preview

            final_render_applies_filters = bool(
                self.render_subtitles
                and self.mode in ("subtitle", "both")
                and self.srt_path
                and os.path.exists(self.srt_path)
            )

            temp_dir = self.temp_dir or os.path.join(os.getcwd(), "temp")
            os.makedirs(temp_dir, exist_ok=True)
            warps = (self.subtitle_style or {}).get("video_time_warps")
            has_dubbed_audio = bool(self.mode in ("voice", "both") and self.audio_path and os.path.exists(self.audio_path))
            current_video = self.video_path

            # When warps exist and we have dubbed/timeline audio (voice/both):
            # Warp the video track FIRST (without audio) so video frames match timeline audio coordinates.
            if warps and has_dubbed_audio:
                temp_warped_path = os.path.normpath(os.path.join(temp_dir, f"preview_warped_{int(time.time())}.mp4"))
                temp_paths.append(temp_warped_path)
                apply_timewarp_to_video_clip(
                    self.video_path,
                    temp_warped_path,
                    warps,
                    get_video_duration(self.video_path),
                    include_audio=False,
                )
                current_video = temp_warped_path
                effective_warps_for_subtitles = None
            else:
                effective_warps_for_subtitles = warps

            if has_dubbed_audio:
                temp_mux_path = os.path.normpath(os.path.join(temp_dir, f"preview_mux_{int(time.time())}.mp4"))
                temp_paths.append(temp_mux_path)
                current_video = mux_audio_into_video_for_preview(
                    current_video,
                    self.audio_path,
                    temp_mux_path,
                    target_width=None if final_render_applies_filters else self.target_width,
                    target_height=None if final_render_applies_filters else self.target_height,
                    scale_mode=self.output_scale_mode,
                    focus_x=self.output_fill_focus_x,
                    focus_y=self.output_fill_focus_y,
                    video_filter_state={} if final_render_applies_filters else self.video_filter_state,
                )

            if self.render_subtitles and self.mode in ("subtitle", "both") and self.srt_path and os.path.exists(self.srt_path):
                engine = EngineRuntime()
                ok = engine.embed_subtitles(
                    current_video,
                    self.srt_path,
                    self.output_path,
                    subtitle_style=self.subtitle_style,
                    mask_regions=self.mask_regions,
                    logo_layers=self.logo_layers,
                    target_width=self.target_width,
                    target_height=self.target_height,
                    output_scale_mode=self.output_scale_mode,
                    output_fill_focus_x=self.output_fill_focus_x,
                    output_fill_focus_y=self.output_fill_focus_y,
                    video_filter_state=self.video_filter_state,
                    fast=True,
                    video_time_warps=effective_warps_for_subtitles,
                )
                if not ok:
                    raise RuntimeError("Failed to render subtitle preview video.")
                output = self.output_path
            elif effective_warps_for_subtitles:
                from video_processor import embed_ass_subtitles, srt_to_ass
                empty_srt = os.path.join(temp_dir, f"empty_preview_{int(time.time())}.srt")
                with open(empty_srt, "w", encoding="utf-8") as f:
                    f.write("")
                empty_ass = os.path.join(temp_dir, f"empty_preview_{int(time.time())}.ass")
                srt_to_ass(empty_srt, empty_ass)
                ok = embed_ass_subtitles(
                    current_video,
                    empty_ass,
                    self.output_path,
                    mask_regions=self.mask_regions,
                    logo_layers=self.logo_layers,
                    target_width=self.target_width,
                    target_height=self.target_height,
                    output_scale_mode=self.output_scale_mode,
                    output_fill_focus_x=self.output_fill_focus_x,
                    output_fill_focus_y=self.output_fill_focus_y,
                    video_filter_state=self.video_filter_state,
                    fast=True,
                    video_time_warps=effective_warps_for_subtitles,
                )
                for p in (empty_srt, empty_ass):
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except OSError:
                        pass
                if not ok:
                    raise RuntimeError("Failed to render freeze frame preview video.")
                output = self.output_path
            else:
                if current_video != self.output_path:
                    shutil.copyfile(current_video, self.output_path)
                output = self.output_path

            self.finished.emit(output, "")
        except Exception as exc:
            self.finished.emit("", str(exc))
        finally:
            for p in temp_paths:
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass


class QuickPreviewWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, video_path, output_path, mode, start_seconds, duration_seconds, srt_path="", ass_path="", audio_path="", subtitle_style=None, target_width=None, target_height=None, output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, video_filter_state=None, original_audio_gain_db=0.0, mask_regions=None, blur_regions=None, logo_layers=None, text_ass_path="", text_image_layers=None, temp_dir="", video_time_warps=None, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.output_path = output_path
        self.mode = mode
        self.start_seconds = start_seconds
        self.duration_seconds = duration_seconds
        self.srt_path = srt_path
        self.ass_path = ass_path
        self.audio_path = audio_path
        self.subtitle_style = subtitle_style or {}
        self.target_width = target_width
        self.target_height = target_height
        self.output_scale_mode = output_scale_mode
        self.output_fill_focus_x = output_fill_focus_x
        self.output_fill_focus_y = output_fill_focus_y
        self.video_filter_state = video_filter_state or {}
        self.original_audio_gain_db = float(original_audio_gain_db or 0.0)
        self.mask_regions = mask_regions or []
        self.blur_regions = blur_regions or []
        self.logo_layers = logo_layers or []
        self.text_ass_path = text_ass_path
        self.text_image_layers = text_image_layers or []
        self.temp_dir = temp_dir
        self.video_time_warps = list(video_time_warps if video_time_warps is not None else (self.subtitle_style or {}).get("video_time_warps") or [])

    def run(self):
        temp_paths = []
        try:
            from preview_processor import apply_timewarp_to_video_clip, mux_audio_into_video_clip_for_preview, trim_video_clip
            from app.services.time_warp_service import TimeWarpService

            temp_dir = self.temp_dir or os.path.join(os.getcwd(), "temp")
            os.makedirs(temp_dir, exist_ok=True)
            if self.text_ass_path and os.path.exists(self.text_ass_path):
                temp_paths.append(self.text_ass_path)
            if self.ass_path and os.path.exists(self.ass_path):
                temp_paths.append(self.ass_path)
            temp_paths.extend(str(item.get("path", "")) for item in self.text_image_layers if item.get("path"))
            stamp = int(time.time())

            raw_warps = list(self.video_time_warps or (self.subtitle_style or {}).get("video_time_warps") or [])
            media_start, media_dur, clip_warps = TimeWarpService.slice_time_warps_for_window(
                raw_warps, self.start_seconds, self.duration_seconds
            )

            base_clip = os.path.join(temp_dir, f"preview_base_{stamp}.mp4")
            temp_paths.append(base_clip)
            trim_video_clip(self.video_path, base_clip, media_start, media_dur)

            # Apply timewarps (slow-motion / freeze) to the base clip if any exist
            if clip_warps:
                warped_clip = os.path.join(temp_dir, f"preview_warped_{stamp}.mp4")
                temp_paths.append(warped_clip)
                # If subtitle-only, keep and warp original audio; if voice/both, audio comes from audio_path
                include_audio = (self.mode == "subtitle")
                apply_timewarp_to_video_clip(
                    base_clip,
                    warped_clip,
                    clip_warps,
                    media_dur,
                    include_audio=include_audio,
                )
                current_video = warped_clip
            else:
                current_video = base_clip

            # If voice or both mode, mux the timeline audio slice into current_video
            if self.mode in ("voice", "both") and self.audio_path and os.path.exists(self.audio_path):
                voice_clip = os.path.join(temp_dir, f"preview_voice_{stamp}.mp4")
                temp_paths.append(voice_clip)
                mux_audio_into_video_clip_for_preview(
                    current_video,
                    self.audio_path,
                    voice_clip,
                    self.start_seconds,
                    self.duration_seconds,
                    target_width=self.target_width if self.mode == "voice" and not (self.mask_regions or self.blur_regions or self.logo_layers or self.text_image_layers) else None,
                    target_height=self.target_height if self.mode == "voice" and not (self.mask_regions or self.blur_regions or self.logo_layers or self.text_image_layers) else None,
                    scale_mode=self.output_scale_mode,
                    focus_x=self.output_fill_focus_x,
                    focus_y=self.output_fill_focus_y,
                    video_filter_state=self.video_filter_state if self.mode == "voice" else {},
                    video_is_pretrimmed=True,
                )
                current_video = voice_clip

            has_overlays = bool(
                self.mask_regions
                or self.blur_regions
                or self.logo_layers
                or self.text_image_layers
                or self.text_ass_path
                or (self.target_width and self.target_height)
                or self.video_filter_state
            )

            if self.mode in ("subtitle", "both") and self.ass_path and os.path.exists(self.ass_path):
                engine = EngineRuntime()
                ok = engine.embed_ass_subtitles(
                    current_video,
                    self.ass_path,
                    self.output_path,
                    blur_region=self.blur_regions,
                    mask_regions=self.mask_regions,
                    logo_layers=self.logo_layers,
                    text_ass_path=self.text_ass_path,
                    text_image_layers=self.text_image_layers,
                    target_width=self.target_width,
                    target_height=self.target_height,
                    output_scale_mode=self.output_scale_mode,
                    output_fill_focus_x=self.output_fill_focus_x,
                    output_fill_focus_y=self.output_fill_focus_y,
                    video_filter_state=self.video_filter_state,
                    audio_gain_db=self.original_audio_gain_db if self.mode == "subtitle" else 0.0,
                    fast=True,
                    video_time_warps=None,
                )
                if not ok:
                    raise RuntimeError("Failed to render subtitle preview clip.")
            elif self.mode in ("subtitle", "both") and self.srt_path and os.path.exists(self.srt_path):
                engine = EngineRuntime()
                subtitle_style = dict(self.subtitle_style)
                subtitle_style["blur_region"] = self.blur_regions
                ok = engine.embed_subtitles(
                    current_video,
                    self.srt_path,
                    self.output_path,
                    subtitle_style=subtitle_style,
                    mask_regions=self.mask_regions,
                    logo_layers=self.logo_layers,
                    text_ass_path=self.text_ass_path,
                    text_image_layers=self.text_image_layers,
                    target_width=self.target_width,
                    target_height=self.target_height,
                    output_scale_mode=self.output_scale_mode,
                    output_fill_focus_x=self.output_fill_focus_x,
                    output_fill_focus_y=self.output_fill_focus_y,
                    video_filter_state=self.video_filter_state,
                    audio_gain_db=self.original_audio_gain_db if self.mode == "subtitle" else 0.0,
                    fast=True,
                    video_time_warps=None,
                )
                if not ok:
                    raise RuntimeError("Failed to render subtitle preview clip.")
            elif has_overlays:
                from video_processor import embed_ass_subtitles, srt_to_ass
                empty_srt = os.path.join(temp_dir, f"empty_clip_{stamp}.srt")
                with open(empty_srt, "w", encoding="utf-8") as f:
                    f.write("")
                empty_ass = os.path.join(temp_dir, f"empty_clip_{stamp}.ass")
                srt_to_ass(empty_srt, empty_ass)
                ok = embed_ass_subtitles(
                    current_video,
                    empty_ass,
                    self.output_path,
                    blur_region=self.blur_regions,
                    mask_regions=self.mask_regions,
                    logo_layers=self.logo_layers,
                    target_width=self.target_width,
                    target_height=self.target_height,
                    output_scale_mode=self.output_scale_mode,
                    output_fill_focus_x=self.output_fill_focus_x,
                    output_fill_focus_y=self.output_fill_focus_y,
                    video_filter_state=self.video_filter_state,
                    audio_gain_db=self.original_audio_gain_db if self.mode == "subtitle" else 0.0,
                    fast=True,
                    video_time_warps=None,
                )
                for p in (empty_srt, empty_ass):
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except OSError:
                        pass
                if not ok:
                    raise RuntimeError("Failed to render preview clip.")
            else:
                shutil.copyfile(current_video, self.output_path)

            self.finished.emit(self.output_path, "")
        except Exception as exc:
            self.finished.emit("", str(exc))
        finally:
            for path in temp_paths:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass


class ExactFramePreviewWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, video_path, output_path, timestamp_seconds, srt_path="", subtitle_style=None, target_width=None, target_height=None, output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, video_filter_state=None, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.output_path = output_path
        self.timestamp_seconds = timestamp_seconds
        self.srt_path = srt_path
        self.subtitle_style = subtitle_style or {}
        self.target_width = target_width
        self.target_height = target_height
        self.output_scale_mode = output_scale_mode
        self.output_fill_focus_x = output_fill_focus_x
        self.output_fill_focus_y = output_fill_focus_y
        self.video_filter_state = video_filter_state or {}

    def run(self):
        try:
            from preview_processor import render_subtitle_frame_preview

            output = render_subtitle_frame_preview(
                self.video_path,
                self.srt_path,
                self.output_path,
                self.timestamp_seconds,
                alignment=self.subtitle_style.get("alignment", 2),
                margin_v=self.subtitle_style.get("margin_v", 30),
                font_name=self.subtitle_style.get("font_name", "Arial"),
                font_size=self.subtitle_style.get("font_size", 18),
                font_color=self.subtitle_style.get("font_color", "&H00FFFFFF"),
                background_box=self.subtitle_style.get("background_box", False),
                animation_style=self.subtitle_style.get("animation", "Static"),
                highlight_color=self.subtitle_style.get("highlight_color", self.subtitle_style.get("font_color", "&H00FFFFFF")),
                outline_color=self.subtitle_style.get("outline_color", "&H00000000"),
                outline_width=self.subtitle_style.get("outline_width", 2.0),
                shadow_color=self.subtitle_style.get("shadow_color", "&H80000000"),
                shadow_depth=self.subtitle_style.get("shadow_depth", 1.0),
                background_color=self.subtitle_style.get("background_color", "&H80000000"),
                background_alpha=self.subtitle_style.get("background_alpha", 0.5),
                bold=self.subtitle_style.get("bold", False),
                preset_key=self.subtitle_style.get("preset_key", ""),
                auto_keyword_highlight=self.subtitle_style.get("auto_keyword_highlight", False),
                animation_duration=self.subtitle_style.get("animation_duration", 0.22),
                manual_highlights=self.subtitle_style.get("manual_highlights", []),
                word_timings=self.subtitle_style.get("word_timings", []),
                karaoke_timing_mode=self.subtitle_style.get("karaoke_timing_mode", "vietnamese"),
                custom_position_enabled=self.subtitle_style.get("custom_position_enabled", False),
                custom_position_x=self.subtitle_style.get("custom_position_x", 50),
                custom_position_y=self.subtitle_style.get("custom_position_y", 86),
                target_width=self.target_width,
                target_height=self.target_height,
                scale_mode=self.output_scale_mode,
                focus_x=self.output_fill_focus_x,
                focus_y=self.output_fill_focus_y,
                video_filter_state=self.video_filter_state,
            )
            self.finished.emit(output, "")
        except Exception as exc:
            self.finished.emit("", str(exc))
