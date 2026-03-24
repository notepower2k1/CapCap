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
