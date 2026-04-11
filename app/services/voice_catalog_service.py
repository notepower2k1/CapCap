from __future__ import annotations

import json
import os

from runtime_paths import app_path, models_path


class VoiceCatalogService:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.catalog_path = app_path("voice_preview_catalog.json")
        self.piper_models_dir = models_path("piper")

    def _read_payload(self) -> dict:
        if not os.path.exists(self.catalog_path):
            return {"schema_version": 2, "voices": []}
        with open(self.catalog_path, "r", encoding="utf-8-sig") as catalog_file:
            payload = json.load(catalog_file)
        if not isinstance(payload, dict):
            return {"schema_version": 2, "voices": []}
        payload.setdefault("schema_version", 2)
        payload.setdefault("voices", [])
        return payload

    def _normalize_path(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if os.path.isabs(raw):
            return os.path.normpath(raw)
        return os.path.normpath(os.path.join(self.workspace_root, raw.replace("/", os.sep)))

    def _normalize_loaded_voice(self, voice: dict) -> dict:
        normalized = dict(voice)
        for key in ("preview_video_path", "preview_audio_path"):
            raw_path = str(normalized.get(key, "") or "").strip()
            if raw_path:
                normalized[key] = self._normalize_path(raw_path)
        return normalized

    def _iter_piper_model_ids(self) -> set[str]:
        if not os.path.isdir(self.piper_models_dir):
            return set()
        try:
            return {
                os.path.splitext(name)[0]
                for name in os.listdir(self.piper_models_dir)
                if name.lower().endswith(".onnx")
            }
        except Exception:
            return set()

    def load_catalog(self) -> list[dict]:
        try:
            payload = self._read_payload()
            voices = list(payload.get("voices", []) or [])
            piper_model_ids = self._iter_piper_model_ids()

            normalized_voices: list[dict] = []
            for voice in voices:
                if not isinstance(voice, dict):
                    continue
                if not voice.get("enabled", True):
                    continue
                provider = str(voice.get("provider", "")).strip().lower()
                if provider not in {"piper", "edge"}:
                    continue
                voice_id = str(voice.get("id", "")).strip()
                if provider == "piper" and voice_id and voice_id not in piper_model_ids:
                    continue
                if provider == "piper" and not voice_id:
                    continue
                normalized_voices.append(self._normalize_loaded_voice(voice))

            return normalized_voices
        except Exception:
            return []
