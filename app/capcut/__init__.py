"""CapCut Online API integration package for CapCap (STT & TTS)."""

try:
    from app.capcut.stt import transcribe_audio_capcut
    from app.capcut.tts import (
        list_capcut_voices,
        synthesize_capcut_tts_wav_16k_mono,
        synthesize_capcut_tts_batch,
        CapCutTTSClient,
    )
except ImportError:
    from .stt import transcribe_audio_capcut
    from .tts import (
        list_capcut_voices,
        synthesize_capcut_tts_wav_16k_mono,
        synthesize_capcut_tts_batch,
        CapCutTTSClient,
    )

__all__ = [
    "transcribe_audio_capcut",
    "list_capcut_voices",
    "synthesize_capcut_tts_wav_16k_mono",
    "synthesize_capcut_tts_batch",
    "CapCutTTSClient",
]
