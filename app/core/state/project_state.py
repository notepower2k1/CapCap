from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


DEFAULT_STEP_STATUSES = {
    "extract_audio": "pending",
    "transcribe": "pending",
    "translate_raw": "pending",
    "refine_translation": "pending",
    "separate_audio": "pending",
    "generate_tts": "pending",
    "build_subtitle": "pending",
    "mix_audio": "pending",
    "export": "pending",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ProjectState:
    project_id: str
    project_root: str
    input_video: str
    input_language: str = "auto"
    target_language: str = "vi"
    mode: str = "subtitle"
    translator_ai: bool = True
    steps: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_STEP_STATUSES))
    settings: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectState":
        steps = dict(DEFAULT_STEP_STATUSES)
        steps.update(dict(data.get("steps", {}) or {}))
        return cls(
            project_id=str(data.get("project_id", "")),
            project_root=str(data.get("project_root", "")),
            input_video=str(data.get("input_video", "")),
            input_language=str(data.get("input_language", "auto") or "auto"),
            target_language=str(data.get("target_language", "vi") or "vi"),
            mode=str(data.get("mode", "subtitle") or "subtitle"),
            translator_ai=bool(data.get("translator_ai", True)),
            steps=steps,
            settings=dict(data.get("settings", {}) or {}),
            artifacts=dict(data.get("artifacts", {}) or {}),
            created_at=str(data.get("created_at", _utc_now_iso())),
            updated_at=str(data.get("updated_at", _utc_now_iso())),
        )

    def touch(self) -> None:
        self.updated_at = _utc_now_iso()

    def set_step_status(self, step_name: str, status: str) -> None:
        self.steps[step_name] = status
        self.touch()

    def set_artifact(self, name: str, path: str) -> None:
        self.artifacts[name] = path
        self.touch()

    def set_setting(self, name: str, value: Any) -> None:
        self.settings[name] = value
        self.touch()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_root": self.project_root,
            "input_video": self.input_video,
            "input_language": self.input_language,
            "target_language": self.target_language,
            "mode": self.mode,
            "translator_ai": self.translator_ai,
            "steps": self.steps,
            "settings": self.settings,
            "artifacts": self.artifacts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

