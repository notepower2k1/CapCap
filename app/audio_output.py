"""Audio formats for final video export and rendered previews."""

DEFAULT_QUALITY = "standard"

FORMATS = {
    "high": (48000, 2, "256k"),
    "standard": (48000, 2, "128k"),
    "economy": (16000, 1, "64k"),
}


def normalize_audio_quality(quality):
    return quality if isinstance(quality, str) and quality in FORMATS else DEFAULT_QUALITY


def audio_encoding_args(quality=None):
    if not isinstance(quality, str) or quality not in FORMATS:
        return ["-c:a", "aac", "-b:a", "192k"]
    rate, channels, bitrate = FORMATS[quality]
    return ["-c:a", "aac", "-ar", str(rate), "-ac", str(channels), "-b:a", bitrate]
