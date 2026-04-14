from __future__ import annotations

import base64
import os

from remote_api import remote_api_post


class RemoteWhisperAdapter:
    def _model_name(self, model_path: str) -> str:
        raw = os.path.basename(str(model_path or "").strip()) or str(model_path or "").strip()
        lowered = raw.lower()
        if "medium" in lowered:
            return "medium"
        if "base" in lowered:
            return "base"
        return raw or "base"

    def transcribe(self, audio_path: str, model_path: str, *, language: str = "auto", task: str = "transcribe"):
        with open(audio_path, "rb") as handle:
            audio_b64 = base64.b64encode(handle.read()).decode("ascii")
        response = remote_api_post(
            "/v1/transcribe",
            {
                "audio_b64": audio_b64,
                "audio_filename": os.path.basename(audio_path),
                "model_name": self._model_name(model_path),
                "language": language,
                "task": task,
            },
        )
        return list(response.get("segments") or [])

    def load_model(self, model_path: str):
        return self._model_name(model_path)

    def transcribe_with_model(self, model, audio_path: str, *, language: str = "auto", task: str = "transcribe"):
        return self.transcribe(audio_path, model, language=language, task=task)
