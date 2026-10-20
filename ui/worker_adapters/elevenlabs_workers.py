"""Background workers for ElevenLabs catalog operations used by the GUI."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class ElevenLabsSettingsSyncWorker(QThread):
    """Fetch staged ElevenLabs account data without changing the active cache."""

    completed = Signal(object)

    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self.api_key = str(api_key or "").strip()

    def run(self):
        result = {"api_key": self.api_key, "payload": None, "models": [], "error": ""}
        try:
            try:
                from app.engines.elevenlabs_tts import (
                    fetch_elevenlabs_models,
                    fetch_elevenlabs_voice_catalog,
                )
            except ImportError:
                from engines.elevenlabs_tts import (
                    fetch_elevenlabs_models,
                    fetch_elevenlabs_voice_catalog,
                )

            result["payload"] = fetch_elevenlabs_voice_catalog(
                self.api_key,
                force_refresh=True,
                write_cache=False,
            )
            result["models"] = fetch_elevenlabs_models(self.api_key)
        except Exception as exc:
            result["error"] = str(exc) or exc.__class__.__name__
        self.completed.emit(result)


class ElevenLabsCatalogRefreshWorker(QThread):
    """Refresh one key's catalog into a staged payload for guarded GUI commit."""

    completed = Signal(object)

    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self.api_key = str(api_key or "").strip()

    def run(self):
        result = {"api_key": self.api_key, "payload": None, "error": ""}
        try:
            try:
                from app.engines.elevenlabs_tts import fetch_elevenlabs_voice_catalog
            except ImportError:
                from engines.elevenlabs_tts import fetch_elevenlabs_voice_catalog

            result["payload"] = fetch_elevenlabs_voice_catalog(
                self.api_key,
                force_refresh=True,
                write_cache=False,
            )
        except Exception as exc:
            result["error"] = str(exc) or exc.__class__.__name__
        self.completed.emit(result)
