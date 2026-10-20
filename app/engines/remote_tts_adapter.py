from __future__ import annotations

import base64
import os

from remote_api import remote_api_post


class RemoteTTSAdapter:
    def synthesize_segment(
        self,
        *,
        text: str,
        wav_path: str,
        voice: str = "ngochuyen",
        speed: float = 1.0,
        tmp_dir: str | None = None,
        on_progress: callable = None,
        normalizer_dictionary=None,
        language: str | None = None,
        language_code: str | None = None,
        model_id: str | None = None,
    ) -> str:
        if on_progress:
            on_progress(f"Remote TTS: synthesizing with PC server ({voice})...")
        payload = {
            "text": text,
            "voice": voice,
            "speed": float(speed),
            "tmp_dir_hint": os.path.basename(str(tmp_dir or "").strip()) if tmp_dir else "",
            "normalizer_dictionary": normalizer_dictionary or {},
        }
        if language:
            payload["language"] = language
        if language_code:
            payload["language_code"] = language_code
        if model_id:
            payload["model_id"] = model_id
        response = remote_api_post(
            "/v1/tts/synthesize",
            payload,
        )
        audio_b64 = str(response.get("audio_b64", "") or "").strip()
        if not audio_b64:
            raise RuntimeError("Remote API did not return synthesized audio.")
        audio_bytes = base64.b64decode(audio_b64.encode("ascii"))
        os.makedirs(os.path.dirname(wav_path), exist_ok=True)
        with open(wav_path, "wb") as handle:
            handle.write(audio_bytes)
        if on_progress:
            on_progress(f"Remote TTS: done ({os.path.basename(wav_path)})")
        return wav_path
