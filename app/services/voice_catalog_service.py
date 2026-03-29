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
                provider = str(normalized.get("provider", "")).strip().lower()
                if provider == "eleven":
                    normalized["provider"] = "vieneuclone"
                    normalized["provider_voice"] = str(normalized.get("id", "")).strip()
                    normalized["reference_audio_path"] = str(normalized.get("preview_audio_path", "")).strip()
                    normalized["reference_text"] = (
                        "Xin chào, hôm nay là một ngày thật đẹp. "
                        "Tôi đang thử ghi âm giọng nói để tạo ra bản sao giọng nói của mình. "
                        "Hy vọng kết quả sẽ thật tự nhiên và rõ ràng."
                    )
                for key in ("preview_video_path", "preview_audio_path"):
                    raw_path = str(normalized.get(key, "") or "").strip()
                    if raw_path and not os.path.isabs(raw_path):
                        normalized[key] = os.path.join(self.workspace_root, raw_path.replace("/", os.sep))
                raw_reference_audio = str(normalized.get("reference_audio_path", "") or "").strip()
                if raw_reference_audio and not os.path.isabs(raw_reference_audio):
                    normalized["reference_audio_path"] = os.path.join(self.workspace_root, raw_reference_audio.replace("/", os.sep))
                normalized_voices.append(normalized)
            existing_ids = {str(item.get("id", "")).strip() for item in normalized_voices}
            for voice in self._fpt_catalog():
                if voice["id"] not in existing_ids:
                    normalized_voices.append(dict(voice))
            return normalized_voices or self._fallback_catalog()
        except Exception:
            return self._fallback_catalog()
