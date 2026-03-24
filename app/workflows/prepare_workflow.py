import os

from services import EngineRuntime, ProjectService, SegmentService


class PrepareWorkflow:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.project_service = ProjectService(workspace_root)
        self.segment_service = SegmentService()
        self.engine_runtime = EngineRuntime()

    def run(
        self,
        video_path: str,
        *,
        source_language: str = "auto",
        target_language: str = "vi",
        mode: str = "subtitle",
        translator_ai: bool = True,
        whisper_model_name: str = "ggml-base.bin",
    ):
        whisper_model = os.path.join(self.workspace_root, "models", whisper_model_name)
        project_state = self.project_service.ensure_project(
            video_path,
            mode=mode,
            translator_ai=translator_ai,
            input_language=source_language,
            target_language=target_language,
        )
        project_state.set_setting("whisper_model", whisper_model)
        self.project_service.save_project(project_state)

        audio_output_path = self.project_service.build_path(project_state, "source", "extracted_audio.wav")
        srt_original_path = self.project_service.build_path(project_state, "subtitle", "original.srt")
        srt_translated_path = self.project_service.build_path(project_state, "subtitle", "subtitle.srt")

        print("--- Step 1: Extracting audio ---")
        project_state.set_step_status("extract_audio", "running")
        self.project_service.save_project(project_state)
        if not self.engine_runtime.extract_audio(video_path, audio_output_path):
            project_state.set_step_status("extract_audio", "failed")
            self.project_service.save_project(project_state)
            raise RuntimeError("Audio extraction failed.")
        print(f"Success: Audio saved to {audio_output_path}")
        project_state.set_step_status("extract_audio", "done")
        project_state.set_artifact("extracted_audio", audio_output_path)
        self.project_service.save_project(project_state)

        working_audio_path = audio_output_path
        if mode in ("voice", "both"):
            print("\n--- Step 1.5: Separating vocals/background ---")
            project_state.set_step_status("separate_audio", "running")
            self.project_service.save_project(project_state)
            separated_root = self.project_service.build_path(project_state, "audio", "separated")
            vocal_path, music_path = self.engine_runtime.separate_vocals(audio_output_path, separated_root)
            if not vocal_path or not music_path:
                project_state.set_step_status("separate_audio", "failed")
                self.project_service.save_project(project_state)
                raise RuntimeError("Audio separation failed.")
            working_audio_path = vocal_path
            project_state.set_step_status("separate_audio", "done")
            project_state.set_artifact("vocals", vocal_path)
            project_state.set_artifact("music", music_path)
            self.project_service.save_project(project_state)

        print("\n--- Step 2: Transcribing audio (Whisper) ---")
        project_state.set_step_status("transcribe", "running")
        self.project_service.save_project(project_state)
        raw_segments = self.engine_runtime.transcribe_audio(
            working_audio_path,
            whisper_model,
            language=source_language,
        )
        if not raw_segments:
            project_state.set_step_status("transcribe", "failed")
            self.project_service.save_project(project_state)
            raise RuntimeError("Transcription failed.")
        segment_models = self.segment_service.transcript_dicts_to_models(raw_segments)
        print(f"Success: Generated {len(segment_models)} segments.")
        self.project_service.save_json_artifact(
            project_state,
            "transcript_raw",
            os.path.join("analysis", "transcript_raw.json"),
            raw_segments,
        )
        self.project_service.save_segment_artifact(
            project_state,
            "transcript_segments",
            os.path.join("analysis", "transcript_segments.json"),
            segment_models,
        )
        project_state.set_step_status("transcribe", "done")
        self.project_service.save_project(project_state)

        print("\n--- Step 3: Generating Original Subtitle ---")
        project_state.set_step_status("build_subtitle", "running")
        self.project_service.save_project(project_state)
        self.engine_runtime.generate_srt(
            [segment.to_original_subtitle_dict() for segment in segment_models],
            srt_original_path,
        )
        project_state.set_step_status("build_subtitle", "done")
        project_state.set_artifact("subtitle_original_srt", srt_original_path)
        self.project_service.save_project(project_state)

        print("\n--- Step 4: Translating to Vietnamese ---")
        project_state.set_step_status("translate_raw", "running")
        self.project_service.save_project(project_state)
        try:
            translated_segments = self.engine_runtime.translate_segments(
                raw_segments,
                src_lang=source_language,
                enable_polish=translator_ai,
            )
            segment_models = self.segment_service.apply_translations(segment_models, translated_segments)
            self.project_service.save_segment_artifact(
                project_state,
                "translation_final",
                os.path.join("translation", "translation_final.json"),
                segment_models,
            )
            if any(segment.refined_translation for segment in segment_models):
                self.project_service.save_segment_artifact(
                    project_state,
                    "translation_refined",
                    os.path.join("translation", "translation_refined.json"),
                    segment_models,
                )
                project_state.set_step_status("refine_translation", "done")
            else:
                self.project_service.save_segment_artifact(
                    project_state,
                    "translation_raw",
                    os.path.join("translation", "translation_raw.json"),
                    segment_models,
                )
                project_state.set_step_status("refine_translation", "skipped")
            project_state.set_step_status("translate_raw", "done")
            self.project_service.save_project(project_state)
        except Exception:
            project_state.set_step_status("translate_raw", "failed")
            if translator_ai:
                project_state.set_step_status("refine_translation", "failed")
            self.project_service.save_project(project_state)
            raise

        print("\n--- Step 5: Generating Vietnamese Subtitle ---")
        self.engine_runtime.generate_srt(segment_models, srt_translated_path)
        project_state.set_artifact("subtitle_translated_srt", srt_translated_path)
        self.project_service.save_project(project_state)
        print(f"\nCOMPLETED! Project saved at: {project_state.project_root}")
        return project_state
