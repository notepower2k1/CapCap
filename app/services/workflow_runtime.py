from __future__ import annotations

from services.project_service import ProjectService
from workflows import ExportWorkflow, PrepareWorkflow, VoiceWorkflow


class WorkflowRuntime:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.project_service = ProjectService(workspace_root)
        self.prepare_workflow = PrepareWorkflow(workspace_root)
        self.voice_workflow = VoiceWorkflow(workspace_root)
        self.export_workflow = ExportWorkflow(workspace_root)

    def run_prepare(
        self,
        video_path: str,
        *,
        source_language: str = "auto",
        target_language: str = "vi",
        mode: str = "subtitle",
        audio_handling_mode: str = "fast",
        translator_ai: bool = True,
        translator_style: str = "",
        whisper_model_name: str = "ggml-base.bin",
    ):
        return self.prepare_workflow.run(
            video_path,
            source_language=source_language,
            target_language=target_language,
            mode=mode,
            audio_handling_mode=audio_handling_mode,
            translator_ai=translator_ai,
            translator_style=translator_style,
            whisper_model_name=whisper_model_name,
        )

    def run_voice(
        self,
        *,
        segments,
        output_dir: str,
        background_path: str = "",
        audio_handling_mode: str = "fast",
        voice_name: str = "vi-VN-HoaiMyNeural",
        voice_speed: float = 1.0,
        timing_sync_mode: str = "off",
        voice_gain_db: float = 0.0,
        bg_gain_db: float = 0.0,
        project_state_path: str = "",
    ):
        return self.voice_workflow.run(
            segments=segments,
            output_dir=output_dir,
            background_path=background_path,
            audio_handling_mode=audio_handling_mode,
            voice_name=voice_name,
            voice_speed=voice_speed,
            timing_sync_mode=timing_sync_mode,
            voice_gain_db=voice_gain_db,
            bg_gain_db=bg_gain_db,
            project_state_path=project_state_path,
        )

    def run_export(
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
        return self.export_workflow.run(
            video_path=video_path,
            output_path=output_path,
            mode=mode,
            srt_path=srt_path,
            ass_path=ass_path,
            audio_path=audio_path,
            subtitle_style=subtitle_style,
            project_state_path=project_state_path,
        )

    def project_state_path(self, state) -> str:
        return self.project_service.project_file(state.project_root)
