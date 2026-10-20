"""Shared normalization for Whisper recognition and transcript merge options."""

from __future__ import annotations

import math
import os
from numbers import Real


ASR_FILTER_VERSION = 1

DEFAULT_ASR_OPTIONS = {
    "dedup_enabled": True,
    "dedup_similarity": 0.82,
    "hallucination_filter_enabled": True,
    "hallucination_silence_threshold": 3.0,
}

_ENV_NAMES = {
    "dedup_enabled": "CAPCAP_ASR_DEDUP_ENABLED",
    "dedup_similarity": "CAPCAP_ASR_DEDUP_SIMILARITY",
    "hallucination_filter_enabled": "CAPCAP_WHISPER_HALLUCINATION_FILTER",
    "hallucination_silence_threshold": "CAPCAP_WHISPER_HALLUCINATION_SILENCE_SECONDS",
}


def _parse_bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean (true/false).")


def _parse_number(value, name: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number between {minimum} and {maximum}.")
    if isinstance(value, Real):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be a finite number between {minimum} and {maximum}.") from exc
    else:
        raise ValueError(f"{name} must be a finite number between {minimum} and {maximum}.")
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be a finite number between {minimum} and {maximum}.")
    return result


def normalize_asr_options(options: dict | None = None) -> dict:
    """Return a validated, complete ASR options snapshot.

    ``None`` reads the environment with the built-in defaults. An explicit
    dictionary is a complete client snapshot layered over built-in defaults;
    environment values never override that snapshot.
    """
    if options is None:
        supplied = {}
        for key, env_name in _ENV_NAMES.items():
            raw_value = os.environ.get(env_name)
            if raw_value is not None:
                supplied[key] = raw_value
    elif isinstance(options, dict):
        supplied = options
    else:
        raise ValueError("ASR options must be a dictionary or None.")

    result = dict(DEFAULT_ASR_OPTIONS)
    for key in DEFAULT_ASR_OPTIONS:
        if key in supplied:
            value = supplied[key]
        else:
            continue

        if key in {"dedup_enabled", "hallucination_filter_enabled"}:
            result[key] = _parse_bool(value, key)
        elif key == "dedup_similarity":
            result[key] = _parse_number(value, key, minimum=0.5, maximum=1.0)
        elif key == "hallucination_silence_threshold":
            result[key] = _parse_number(value, key, minimum=0.5, maximum=10.0)

    return result
