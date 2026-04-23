from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from core.models import Segment, coerce_segments
from core.state import ProjectState


class ProjectService:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.projects_root = os.path.join(workspace_root, "projects")

    def ensure_project(
        self,
        video_path: str,
        *,
        mode: str = "subtitle",
        translator_ai: bool = True,
        translator_style: str = "",
        input_language: str = "auto",
        target_language: str = "vi",
    ) -> ProjectState:
        os.makedirs(self.projects_root, exist_ok=True)
        project_id = self._build_project_id(video_path)
        project_root = os.path.join(self.projects_root, project_id)
        self._ensure_project_dirs(project_root)
 
        state_path = self.project_file(project_root)
        if os.path.exists(state_path):
            state = self.load_project(state_path)
            state.input_video = video_path
            state.mode = mode
            state.translator_ai = translator_ai
            state.translator_style = translator_style
            state.input_language = input_language
            state.target_language = target_language
            self.save_project(state)
            return state
 
        state = ProjectState(
            project_id=project_id,
            project_root=project_root,
            input_video=video_path,
            input_language=input_language,
            target_language=target_language,
            mode=mode,
            translator_ai=translator_ai,
            translator_style=translator_style,
        )
        self.save_project(state)
        return state

    def load_project(self, state_path: str) -> ProjectState:
        with open(state_path, "r", encoding="utf-8") as handle:
            return ProjectState.from_dict(json.load(handle))

    def save_project(self, state: ProjectState) -> str:
        self._ensure_project_dirs(state.project_root)
        state.touch()
        state_path = self.project_file(state.project_root)
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
        return state_path

    def save_json_artifact(
        self,
        state: ProjectState,
        artifact_name: str,
        relative_path: str,
        payload: Any,
    ) -> str:
        output_path = os.path.join(state.project_root, relative_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        state.set_artifact(artifact_name, output_path)
        self.save_project(state)
        return output_path

    def save_segment_artifact(
        self,
        state: ProjectState,
        artifact_name: str,
        relative_path: str,
        segments: list[Segment],
    ) -> str:
        return self.save_json_artifact(
            state,
            artifact_name,
            relative_path,
            [segment.to_dict() for segment in coerce_segments(segments)],
        )

    def load_json_artifact(self, state: ProjectState, artifact_name: str, default=None):
        path = state.artifacts.get(artifact_name, "")
        if not path or not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def load_segment_artifact(self, state: ProjectState, artifact_name: str) -> list[Segment]:
        payload = self.load_json_artifact(state, artifact_name, default=[])
        return coerce_segments(payload or [])

    def update_step(self, state: ProjectState, step_name: str, status: str, *, save: bool = True) -> ProjectState:
        state.set_step_status(step_name, status)
        if save:
            self.save_project(state)
        return state

    def update_artifact(self, state: ProjectState, artifact_name: str, path: str, *, save: bool = True) -> ProjectState:
        state.set_artifact(artifact_name, path)
        if save:
            self.save_project(state)
        return state

    def project_file(self, project_root: str) -> str:
        return os.path.join(project_root, "project.json")

    def build_path(self, state: ProjectState, *parts: str) -> str:
        return os.path.join(state.project_root, *parts)

    def _ensure_project_dirs(self, project_root: str) -> None:
        for relative_dir in (
            "source",
            "analysis",
            "translation",
            os.path.join("audio", "separated"),
            os.path.join("audio", "tts_segments"),
            "subtitle",
            os.path.join("preview", "cache"),
            "export",
            "logs",
        ):
            os.makedirs(os.path.join(project_root, relative_dir), exist_ok=True)

    def _build_project_id(self, video_path: str) -> str:
        video_name = os.path.splitext(os.path.basename(video_path))[0] or "project"
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", video_name).strip("_").lower() or "project"
        digest = hashlib.sha1(os.path.abspath(video_path).encode("utf-8")).hexdigest()[:8]
        return f"{slug}_{digest}"

    def _hash_payload(self, payload: Any) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    def _file_signature(self, path: str) -> dict[str, Any]:
        normalized = str(path or "").strip()
        if not normalized:
            return {"path": "", "exists": False}
        try:
            stat = os.stat(normalized)
            return {
                "path": os.path.abspath(normalized),
                "exists": True,
                "size": int(stat.st_size),
                "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
            }
        except OSError:
            return {"path": os.path.abspath(normalized), "exists": False}

    def build_translation_signature(
        self,
        source_segments,
        *,
        src_lang: str = "auto",
        target_lang: str = "vi",
        enable_polish: bool = True,
        optimize_subtitles: bool = True,
        style_instruction: str = "",
    ) -> str:
        payload = {
            "src_lang": str(src_lang or "auto").strip().lower(),
            "target_lang": str(target_lang or "vi").strip().lower(),
            "enable_polish": bool(enable_polish),
            "optimize_subtitles": bool(optimize_subtitles),
            "style_instruction": str(style_instruction or "").strip(),
            "segments": [
                {
                    "start": round(float((seg or {}).get("start", 0.0) or 0.0), 3),
                    "end": round(float((seg or {}).get("end", 0.0) or 0.0), 3),
                    "text": str((seg or {}).get("source_text") or (seg or {}).get("text") or "").strip(),
                }
                for seg in list(source_segments or [])
            ],
        }
        return self._hash_payload(payload)

    def build_voice_signature(
        self,
        segments,
        *,
        audio_handling_mode: str = "fast",
        voice_name: str = "",
        voice_speed: float = 1.0,
        timing_sync_mode: str = "off",
        background_path: str = "",
        voice_gain_db: float = 0.0,
        bg_gain_db: float = 0.0,
        ducking_amount_db: float = -6.0,
    ) -> str:
        safe_voice_speed = max(0.5, min(1.30, float(voice_speed or 1.0)))
        payload = {
            "audio_handling_mode": str(audio_handling_mode or "fast").strip().lower(),
            "voice_name": str(voice_name or "").strip(),
            "voice_speed": round(safe_voice_speed, 3),
            "timing_sync_mode": str(timing_sync_mode or "off").strip().lower(),
            "background": self._file_signature(background_path),
            "voice_gain_db": round(float(voice_gain_db or 0.0), 3),
            "bg_gain_db": round(float(bg_gain_db or 0.0), 3),
            "ducking_amount_db": round(float(ducking_amount_db or 0.0), 3),
            "segments": [
                {
                    "start": round(float((seg or {}).get("start", 0.0) or 0.0), 3),
                    "end": round(float((seg or {}).get("end", 0.0) or 0.0), 3),
                    "text": str((seg or {}).get("tts_text") or (seg or {}).get("text") or "").strip(),
                    "group_id": str((seg or {}).get("tts_group_id") or "").strip(),
                }
                for seg in list(segments or [])
            ],
        }
        return self._hash_payload(payload)

    def build_extraction_signature(self, video_path: str) -> str:
        return self._hash_payload(
            {
                "video": self._file_signature(video_path),
            }
        )

    def build_separation_signature(self, extracted_audio_path: str, *, audio_handling_mode: str = "fast") -> str:
        return self._hash_payload(
            {
                "audio_handling_mode": str(audio_handling_mode or "fast").strip().lower(),
                "extracted_audio": self._file_signature(extracted_audio_path),
            }
        )

    def build_transcription_signature(
        self,
        audio_path: str,
        *,
        whisper_model: str,
        source_language: str = "auto",
        audio_handling_mode: str = "fast",
    ) -> str:
        return self._hash_payload(
            {
                "audio": self._file_signature(audio_path),
                "whisper_model": str(whisper_model or "").strip(),
                "source_language": str(source_language or "auto").strip().lower(),
                "audio_handling_mode": str(audio_handling_mode or "fast").strip().lower(),
            }
        )
