from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess


DEFAULT_CLONE_REFERENCE_TEXT = (
    "Xin chào, hôm nay là một ngày thật đẹp. "
    "Tôi đang thử ghi âm giọng nói để tạo ra bản sao giọng nói của mình. "
    "Hy vọng kết quả sẽ thật tự nhiên và rõ ràng."
)


class VoiceCatalogService:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.catalog_path = os.path.join(workspace_root, "app", "voice_preview_catalog.json")
        self.clone_assets_dir = os.path.join(workspace_root, "assets", "clones")

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

    def _write_payload(self, payload: dict) -> None:
        os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)
        with open(self.catalog_path, "w", encoding="utf-8") as catalog_file:
            json.dump(payload, catalog_file, ensure_ascii=False, indent=4)

    def _normalize_path(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if os.path.isabs(raw):
            return os.path.normpath(raw)
        return os.path.normpath(os.path.join(self.workspace_root, raw.replace("/", os.sep)))

    def _relative_to_workspace(self, value: str) -> str:
        normalized = self._normalize_path(value)
        if not normalized:
            return ""
        try:
            return os.path.relpath(normalized, self.workspace_root)
        except ValueError:
            return normalized

    def _sanitize_slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "")).strip("_").lower()
        return slug or "clone"

    def _ffmpeg_path(self) -> str:
        return os.path.join(self.workspace_root, "bin", "ffmpeg", "ffmpeg.exe")

    def _convert_audio_to_clone_wav(self, source_audio_path: str, output_audio_path: str) -> str:
        ffmpeg = self._ffmpeg_path()
        if not os.path.exists(ffmpeg):
            raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")
        os.makedirs(os.path.dirname(output_audio_path), exist_ok=True)
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                source_audio_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                output_audio_path,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg could not prepare the clone audio:\n{proc.stderr or proc.stdout}")
        return output_audio_path

    def _normalize_loaded_voice(self, voice: dict) -> dict:
        normalized = dict(voice)
        provider = str(normalized.get("provider", "")).strip().lower()
        if provider == "eleven":
            normalized["provider"] = "vieneuclone"
            normalized["provider_voice"] = str(normalized.get("id", "")).strip()
            normalized["reference_audio_path"] = str(normalized.get("preview_audio_path", "")).strip()
            normalized["reference_text"] = DEFAULT_CLONE_REFERENCE_TEXT
            normalized["tier"] = "clone"
        elif provider == "vieneuclone":
            normalized["tier"] = "clone"

        for key in ("preview_video_path", "preview_audio_path", "reference_audio_path"):
            raw_path = str(normalized.get(key, "") or "").strip()
            if raw_path:
                normalized[key] = self._normalize_path(raw_path)

        if normalized.get("provider") == "vieneuclone":
            normalized["reference_text"] = str(normalized.get("reference_text", "")).strip() or DEFAULT_CLONE_REFERENCE_TEXT

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
                normalized_voices.append(self._normalize_loaded_voice(voice))
            existing_ids = {str(item.get("id", "")).strip() for item in normalized_voices}
            for voice in self._fpt_catalog():
                if voice["id"] not in existing_ids:
                    normalized_voices.append(dict(voice))
            return normalized_voices or self._fallback_catalog()
        except Exception:
            return self._fallback_catalog()

    def create_clone_entry(
        self,
        *,
        name: str,
        source_audio_path: str,
        reference_text: str,
        gender: str = "any",
        save_to_catalog: bool = True,
    ) -> dict:
        clean_name = str(name or "").strip() or "Custom Clone"
        source_path = self._normalize_path(source_audio_path)
        if not source_path or not os.path.exists(source_path):
            raise FileNotFoundError(f"Clone source audio not found: {source_audio_path}")
        clean_text = re.sub(r"\s+", " ", str(reference_text or "")).strip()
        if not clean_text:
            raise RuntimeError("Whisper could not extract a usable transcript from the clone audio.")

        slug = self._sanitize_slug(clean_name)
        digest = hashlib.sha1(f"{os.path.abspath(source_path)}|{clean_name}".encode("utf-8")).hexdigest()[:8]
        entry_id = f"vieneuclone_{slug}_{digest}"
        normalized_audio_path = os.path.join(self.clone_assets_dir, f"{entry_id}.wav")
        self._convert_audio_to_clone_wav(source_path, normalized_audio_path)

        relative_audio_path = self._relative_to_workspace(normalized_audio_path)
        entry = {
            "id": entry_id,
            "name": clean_name,
            "provider": "vieneuclone",
            "provider_voice": entry_id,
            "language": "vi",
            "gender": str(gender or "any").strip().lower() or "any",
            "tier": "clone",
            "preview_video_url": "",
            "preview_video_path": "",
            "preview_audio_url": "",
            "preview_audio_path": relative_audio_path,
            "reference_audio_path": relative_audio_path,
            "reference_text": clean_text,
            "enabled": True,
            "tags": ["clone", "user"],
        }

        if save_to_catalog:
            payload = self._read_payload()
            voices = list(payload.get("voices", []) or [])
            voices = [voice for voice in voices if str(voice.get("id", "")).strip() != entry_id]
            voices.append(dict(entry))
            payload["voices"] = voices
            self._write_payload(payload)

        return self._normalize_loaded_voice(entry)
