from importlib import import_module

__all__ = [
    "AudioMixAdapter",
    "DemucsAdapter",
    "FFmpegAdapter",
    "PreviewAdapter",
    "SubtitleAdapter",
    "TranslatorAdapter",
    "TTSAdapter",
    "WhisperAdapter",
]

_MODULE_MAP = {
    "AudioMixAdapter": ".audio_mix_adapter",
    "DemucsAdapter": ".demucs_adapter",
    "FFmpegAdapter": ".ffmpeg_adapter",
    "PreviewAdapter": ".preview_adapter",
    "SubtitleAdapter": ".subtitle_adapter",
    "TranslatorAdapter": ".translator_adapter",
    "TTSAdapter": ".tts_adapter",
    "WhisperAdapter": ".whisper_adapter",
}


def __getattr__(name):
    module_name = _MODULE_MAP.get(name)
    if not module_name:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
