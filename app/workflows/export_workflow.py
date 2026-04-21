import os
import time

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
        return {
            "alignment": subtitle_style.get("alignment", 2),
            "margin_v": subtitle_style.get("margin_v", 30),
            "font_name": subtitle_style.get("font_name", "Arial"),
            "font_size": subtitle_style.get("font_size", 18),
            "font_color": subtitle_style.get("font_color", "&H00FFFFFF"),
            "background_box": subtitle_style.get("background_box", False),
            "animation": subtitle_style.get("animation", "Static"),
            "custom_position_enabled": subtitle_style.get("custom_position_enabled", False),
            "custom_position_x": subtitle_style.get("custom_position_x", 50),
            "custom_position_y": subtitle_style.get("custom_position_y", 86),
            "blur_region": subtitle_style.get("blur_region"),
        }

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
    ):
        if ass_path and os.path.exists(ass_path):
            ok = self.engine_runtime.embed_ass_subtitles(
                video_path,
                ass_path,
                output_path,
                blur_region=subtitle_style.get("blur_region"),
                target_width=target_width,
                target_height=target_height,
                output_scale_mode=output_scale_mode,
                output_fill_focus_x=output_fill_focus_x,
                output_fill_focus_y=output_fill_focus_y,
                output_fps=output_fps,
                video_filter_state=video_filter_state,
            )
        else:
            ok = self.engine_runtime.embed_subtitles(
                video_path,
                srt_path,
                output_path,
                subtitle_style=self._subtitle_options(subtitle_style),
                target_width=target_width,
                target_height=target_height,
                output_scale_mode=output_scale_mode,
                output_fill_focus_x=output_fill_focus_x,
                output_fill_focus_y=output_fill_focus_y,
                output_fps=output_fps,
                video_filter_state=video_filter_state,
            )
        if not ok:
            raise RuntimeError("Failed to burn subtitles into the output video.")

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
        project_state_path: str = "",
        project_temp_dir: str = "",
        on_progress=None,
    ) -> str:
        subtitle_style = subtitle_style or {}
        target_w, target_h = self._resolve_target_dimensions(video_path, output_quality, output_ratio)
        target_fps = self._resolve_target_fps(output_fps)

        state = self._load_state(project_state_path)
        self._mark_started(state)
        self._emit_progress(on_progress, 5, "Preparing final export...")

        tmp_mux_path = ""
        try:
            if mode == "subtitle":
                self._emit_progress(on_progress, 20, "Burning subtitles into the video...")
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
                )
            elif mode == "voice":
                self._emit_progress(on_progress, 25, "Muxing Vietnamese audio into the video...")
                self.engine_runtime.mux_audio_for_preview(
                    video_path,
                    audio_path,
                    output_path,
                    target_width=target_w,
                    target_height=target_h,
                    output_scale_mode=output_scale_mode,
                    focus_x=output_fill_focus_x,
                    focus_y=output_fill_focus_y,
                    output_fps=target_fps,
                    video_filter_state=video_filter_state,
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

