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

    def _build_temp_mux_path(self) -> str:
        tmp_dir = os.path.join(self.workspace_root, "temp")
        os.makedirs(tmp_dir, exist_ok=True)
        return os.path.join(tmp_dir, f"final_mux_{int(time.time())}.mp4")

    def _export_subtitle_video(self, *, video_path: str, srt_path: str, ass_path: str, output_path: str, subtitle_style):
        if ass_path and os.path.exists(ass_path):
            ok = self.engine_runtime.embed_ass_subtitles(
                video_path,
                ass_path,
                output_path,
                blur_region=subtitle_style.get("blur_region"),
            )
        else:
            ok = self.engine_runtime.embed_subtitles(
                video_path,
                srt_path,
                output_path,
                subtitle_style=self._subtitle_options(subtitle_style),
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
        project_state_path: str = "",
    ) -> str:
        subtitle_style = subtitle_style or {}
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
                )
            elif mode == "voice":
                self.engine_runtime.mux_audio_for_preview(video_path, audio_path, output_path)
            elif mode == "both":
                tmp_mux_path = self._build_temp_mux_path()
                self.engine_runtime.mux_audio_for_preview(video_path, audio_path, tmp_mux_path)
                self._export_subtitle_video(
                    video_path=tmp_mux_path,
                    srt_path=srt_path,
                    ass_path=ass_path,
                    output_path=output_path,
                    subtitle_style=subtitle_style,
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
