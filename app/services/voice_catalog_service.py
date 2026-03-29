from __future__ import annotations

import json
import os


class VoiceCatalogService:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.catalog_path = os.path.join(workspace_root, "app", "voice_preview_catalog.json")

    def _fpt_catalog(self) -> list[dict]:
        return [
            {
                "id": "fpt_banmai",
                "name": "FPT Ban Mai",
                "provider": "fpt",
                "provider_voice": "banmai",
                "language": "vi",
                "gender": "female",
                "tier": "premium",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["north", "female"],
            },
            {
                "id": "fpt_lannhi",
                "name": "FPT Lan Nhi",
                "provider": "fpt",
                "provider_voice": "lannhi",
                "language": "vi",
                "gender": "female",
                "tier": "premium",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["south", "female"],
            },
            {
                "id": "fpt_leminh",
                "name": "FPT Le Minh",
                "provider": "fpt",
                "provider_voice": "leminh",
                "language": "vi",
                "gender": "male",
                "tier": "premium",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["north", "male"],
            },
            {
                "id": "fpt_myan",
                "name": "FPT My An",
                "provider": "fpt",
                "provider_voice": "myan",
                "language": "vi",
                "gender": "female",
                "tier": "premium",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["central", "female"],
            },
            {
                "id": "fpt_thuminh",
                "name": "FPT Thu Minh",
                "provider": "fpt",
                "provider_voice": "thuminh",
                "language": "vi",
                "gender": "female",
                "tier": "premium",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["north", "female"],
            },
            {
                "id": "fpt_giahuy",
                "name": "FPT Gia Huy",
                "provider": "fpt",
                "provider_voice": "giahuy",
                "language": "vi",
                "gender": "male",
                "tier": "premium",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["central", "male"],
            },
            {
                "id": "fpt_linhsan",
                "name": "FPT Linh San",
                "provider": "fpt",
                "provider_voice": "linhsan",
                "language": "vi",
                "gender": "female",
                "tier": "premium",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["south", "female"],
            },
        ]

    def _fallback_catalog(self) -> list[dict]:
        return [
            {
                "id": "edge_female_default",
                "name": "Edge Female AI",
                "provider": "edge",
                "provider_voice": "vi-VN-HoaiMyNeural",
                "language": "vi",
                "gender": "female",
                "tier": "free",
                "preview_video_url": "",
                "preview_video_path": "",
                "enabled": True,
            },
            {
                "id": "edge_male_default",
                "name": "Edge Male AI",
                "provider": "edge",
                "provider_voice": "vi-VN-NamMinhNeural",
                "language": "vi",
                "gender": "male",
                "tier": "free",
                "preview_video_url": "",
                "preview_video_path": "",
                "enabled": True,
            },
        ] + self._fpt_catalog()

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

    def load_catalog(self) -> list[dict]:
        if not os.path.exists(self.catalog_path):
            return self._fallback_catalog()
        try:
            payload = self._read_payload()
            voices = list(payload.get("voices", []) or [])
            normalized_voices = []
            for voice in voices:
                if not voice.get("enabled", True):
                    continue
                provider = str(voice.get("provider", "")).strip().lower()
                if provider in {"vieneu", "vieneuclone", "eleven"}:
                    continue
                normalized_voices.append(self._normalize_loaded_voice(voice))
            existing_ids = {str(item.get("id", "")).strip() for item in normalized_voices}
            for voice in self._fpt_catalog():
                if voice["id"] not in existing_ids:
                    normalized_voices.append(dict(voice))
            return normalized_voices or self._fallback_catalog()
        except Exception:
            return self._fallback_catalog()
