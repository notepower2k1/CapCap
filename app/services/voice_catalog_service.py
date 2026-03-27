from __future__ import annotations

import json
import os


class VoiceCatalogService:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.catalog_path = os.path.join(workspace_root, "app", "voice_preview_catalog.json")

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
        ]

    def load_catalog(self) -> list[dict]:
        if not os.path.exists(self.catalog_path):
            return self._fallback_catalog()
        try:
            with open(self.catalog_path, "r", encoding="utf-8-sig") as catalog_file:
                payload = json.load(catalog_file)
            voices = list(payload.get("voices", []) or [])
            normalized_voices = []
            for voice in voices:
                if not voice.get("enabled", True):
                    continue
                normalized = dict(voice)
                for key in ("preview_video_path", "preview_audio_path"):
                    raw_path = str(normalized.get(key, "") or "").strip()
                    if raw_path and not os.path.isabs(raw_path):
                        normalized[key] = os.path.join(self.workspace_root, raw_path.replace("/", os.sep))
                normalized_voices.append(normalized)
            return normalized_voices or self._fallback_catalog()
        except Exception:
            return self._fallback_catalog()
