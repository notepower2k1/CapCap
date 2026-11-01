"""Local processing policy. Version only invalidates derived voice tracks,
never provider/TTS synthesis caches.
"""
BOUNDARY_TRIM_VERSION = 1
BOUNDARY_SILENCE_DB = -60.0
BOUNDARY_MIN_SILENCE_SECONDS = 0.35
BOUNDARY_PADDING_SECONDS = 0.12
