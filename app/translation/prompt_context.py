"""Per-call prompt selection, isolated from main UI settings and other threads."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import sys

for name in ("translation.prompt_context", "app.translation.prompt_context"):
    sys.modules.setdefault(name, sys.modules[__name__])

_PRESET = ContextVar("capcap_translation_preset", default=None)


def active_translation_preset():
    selected = _PRESET.get()
    return selected if selected is not None else (os.getenv("CAPCAP_TRANSLATION_PRESET_ID") or "").strip()


@contextmanager
def translation_prompt_scope(preset_id):
    token = _PRESET.set(str(preset_id or "general_default"))
    try:
        yield
    finally:
        _PRESET.reset(token)
