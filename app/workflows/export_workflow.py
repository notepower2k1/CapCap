import os
import time

from services import EngineRuntime, ProjectService


class ExportWorkflow:
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

    def _resolve_target_dimensions(self, video_path: str, output_quality: str):
        key = str(output_quality or "source").strip().lower()
        if key in ("", "source", "same", "original", "auto"):
            return None, None

        src_w, src_h = self.engine_runtime.get_video_dimensions(video_path)
        if not src_w or not src_h:
            return None, None

        portrait = src_h > src_w
        if key in ("720", "720p", "hd"):
            base_w, base_h = (720, 1280) if portrait else (1280, 720)
        elif key in ("1080", "1080p", "fullhd", "fhd", "full hd", "full"):
            base_w, base_h = (1080, 1920) if portrait else (1920, 1080)
        elif key in ("1440", "1440p", "2k", "qhd"):
            base_w, base_h = (1440, 2560) if portrait else (2560, 1440)
        elif key in ("2160", "2160p", "4k", "uhd"):
            base_w, base_h = (2160, 3840) if portrait else (3840, 2160)
        else:
            return None, None

        # Avoid upscaling: only scale if source is larger than the target.
        if src_w <= base_w and src_h <= base_h:
            return None, None
        return base_w, base_h

    def _build_temp_mux_path(self) -> str:
        tmp_dir = os.path.join(self.workspace_root, "temp")
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
    ):
        if ass_path and os.path.exists(ass_path):
            ok = self.engine_runtime.embed_ass_subtitles(
                video_path,
                ass_path,
                output_path,
                blur_region=subtitle_style.get("blur_region"),
                target_width=target_width,
                target_height=target_height,
            )
        else:
            ok = self.engine_runtime.embed_subtitles(
                video_path,
                srt_path,
                output_path,
                subtitle_style=self._subtitle_options(subtitle_style),
                target_width=target_width,
                target_height=target_height,
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
        project_state_path: str = "",
    ) -> str:
        subtitle_style = subtitle_style or {}
        target_w, target_h = self._resolve_target_dimensions(video_path, output_quality)

        state = self._load_state(project_state_path)
        self._mark_started(state)

        tmp_mux_path = ""
        try:
            if mode == "subtitle":
                self._export_subtitle_video(
                    video_path=video_path,
                    srt_path=srt_path,
                    ass_path=ass_path,
                    output_path=output_path,
                    subtitle_style=subtitle_style,
                    target_width=target_w,
                    target_height=target_h,
                )
            elif mode == "voice":
                self.engine_runtime.mux_audio_for_preview(
                    video_path,
                    audio_path,
                    output_path,
                    target_width=target_w,
                    target_height=target_h,
                )
            elif mode == "both":
                tmp_mux_path = self._build_temp_mux_path()
                # Keep this mux fast (no scaling). Scaling happens in the subtitle-burn step.
                self.engine_runtime.mux_audio_for_preview(video_path, audio_path, tmp_mux_path)
                self._export_subtitle_video(
                    video_path=tmp_mux_path,
                    srt_path=srt_path,
                    ass_path=ass_path,
                    output_path=output_path,
                    subtitle_style=subtitle_style,
                    target_width=target_w,
                    target_height=target_h,
                )
            else:
                raise ValueError(f"Unsupported export mode: {mode}")

            self._mark_completed(state, output_path)
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

