from __future__ import annotations

import json
import os

from core.models import coerce_segments
from services.segment_service import SegmentService


class GUIProjectBridge:
    def __init__(self, project_service):
        self.project_service = project_service
        self.segment_service = SegmentService()

    def ensure_project(
        self,
        *,
        video_path: str,
        mode: str,
        translator_ai: bool,
        input_language: str,
        target_language: str = "vi",
    ):
        if not video_path or not os.path.exists(video_path):
            return None
        return self.project_service.ensure_project(
            video_path,
            mode=mode,
            translator_ai=translator_ai,
            input_language=input_language,
            target_language=target_language,
        )

    def update_step(self, state, step_name: str, status: str):
        if not state:
            return None
        return self.project_service.update_step(state, step_name, status)

    def update_artifact(self, state, artifact_name: str, path: str):
        if not state or not path:
            return None
        return self.project_service.update_artifact(state, artifact_name, path)

    def dict_segments_to_models(self, segments, *, translated: bool = False):
        return self.segment_service.segment_dicts_to_models(segments, translated=translated)

    def persist_transcription(self, state, raw_segments, srt_path: str = ""):
        if not state:
            return []
        segment_models = self.segment_service.transcript_dicts_to_models(raw_segments)
        self.project_service.save_json_artifact(
            state,
            "transcript_raw",
            os.path.join("analysis", "transcript_raw.json"),
            raw_segments,
        )
        self.project_service.save_segment_artifact(
            state,
            "transcript_segments",
            os.path.join("analysis", "transcript_segments.json"),
            segment_models,
        )
        if srt_path:
            self.update_artifact(state, "subtitle_original_srt", srt_path)
        self.update_step(state, "transcribe", "done")
        return segment_models

    def persist_translation(self, state, base_models, translated_segments, srt_path: str = ""):
        if not state:
            return []
        models = self.segment_service.apply_translations(base_models, translated_segments)

        self.project_service.save_segment_artifact(
            state,
            "translation_final",
            os.path.join("translation", "translation_final.json"),
            models,
        )
        if any(model.refined_translation for model in models):
            self.project_service.save_segment_artifact(
                state,
                "translation_refined",
                os.path.join("translation", "translation_refined.json"),
                models,
            )
            self.update_step(state, "refine_translation", "done")
        else:
            self.project_service.save_segment_artifact(
                state,
                "translation_raw",
                os.path.join("translation", "translation_raw.json"),
                models,
            )
            self.update_step(state, "refine_translation", "skipped")
        if srt_path:
            self.update_artifact(state, "subtitle_translated_srt", srt_path)
        self.update_step(state, "translate_raw", "done")
        return models

    def load_context(self, state):
        context = {
            "artifacts": {},
            "last_original_srt_path": "",
            "last_translated_srt_path": "",
            "last_extracted_audio": "",
            "last_vocals_path": "",
            "last_music_path": "",
            "last_voice_vi_path": "",
            "last_mixed_vi_path": "",
            "current_segments": [],
            "current_translated_segments": [],
            "current_segment_models": [],
            "current_translated_segment_models": [],
        }
        if not state:
            return context

        context["artifacts"] = dict(state.artifacts)
        context["last_original_srt_path"] = state.artifacts.get("subtitle_original_srt") or state.artifacts.get("srt_original") or ""
        context["last_translated_srt_path"] = state.artifacts.get("subtitle_translated_srt") or state.artifacts.get("srt_translated") or ""
        context["last_extracted_audio"] = state.artifacts.get("extracted_audio") or state.artifacts.get("audio_extracted") or ""
        context["last_vocals_path"] = state.artifacts.get("vocals", "")
        context["last_music_path"] = state.artifacts.get("music", "")
        context["last_voice_vi_path"] = state.artifacts.get("voice_vi", "")
        context["last_mixed_vi_path"] = state.artifacts.get("mixed_vi", "")

        transcript_json = state.artifacts.get("transcript_segments")
        if transcript_json and os.path.exists(transcript_json):
            transcript_models = self.project_service.load_segment_artifact(state, "transcript_segments")
            context["current_segment_models"] = transcript_models
            context["current_segments"] = [segment.to_original_subtitle_dict() for segment in transcript_models]

        translation_json = state.artifacts.get("translation_final")
        if translation_json and os.path.exists(translation_json):
            translation_models = self.project_service.load_segment_artifact(state, "translation_final")
            context["current_translated_segment_models"] = translation_models
            context["current_translated_segments"] = [segment.to_subtitle_dict() for segment in translation_models]

        return context
