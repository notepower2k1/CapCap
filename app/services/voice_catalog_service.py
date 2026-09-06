from __future__ import annotations

import json
import os

from runtime_paths import app_path, bundle_root
from runtime_profile import is_remote_profile


class VoiceCatalogService:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.catalog_path = app_path("voice_preview_catalog.json")
        # A frozen build may create writable placeholder directories beside
        # the executable while the bundled voices live under _internal.  Scan
        # both roots so the placeholder never hides bundled Piper models.
        candidates = []
        for root in (self.workspace_root, bundle_root()):
            for folder in ("piper", "piper-en"):
                path = os.path.join(root, "models", folder)
                if path not in candidates:
                    candidates.append(path)
        self.piper_models_dirs = tuple(candidates)

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
        model_ids: set[str] = set()
        for models_dir in self.piper_models_dirs:
            if not os.path.isdir(models_dir):
                continue
            try:
                model_ids.update(
                    os.path.splitext(name)[0]
                    for name in os.listdir(models_dir)
                    if name.lower().endswith(".onnx")
                )
            except Exception:
                continue
        return model_ids

    def load_catalog(self) -> list[dict]:
        try:
            payload = self._read_payload()
            voices = list(payload.get("voices", []) or [])
            piper_model_ids = self._iter_piper_model_ids()
            is_remote = is_remote_profile()
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
                # In remote mode the backend API owns the models, so skip the local file check.
                if not is_remote and provider == "piper" and voice_id and voice_id not in piper_model_ids:
                    continue
                if provider == "piper" and not voice_id:
                    continue
                normalized_voices.append(self._normalize_loaded_voice(voice))

            # Include VieNeu voices (presets + cloned voices)
            try:
                from vieneu_tts import list_all_vieneu_voices
                for vv in list_all_vieneu_voices():
                    normalized_voices.append(self._normalize_loaded_voice(vv))
            except Exception as e:
                print(f"[VoiceCatalog] Failed to load VieNeu voices: {e}")

            # Include CapCut voices
            try:
                try:
                    from app.capcut import list_capcut_voices
                except ImportError:
                    from capcut import list_capcut_voices
                for cv in list_capcut_voices():
                    normalized_voices.append(self._normalize_loaded_voice(cv))
            except Exception as e:
                print(f"[VoiceCatalog] Failed to load CapCut voices: {e}")

            return normalized_voices
        except Exception as exc:
            print(f"[VoiceCatalog] ERROR loading catalog: {exc}")
            return []
