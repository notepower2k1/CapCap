"""Editor accents; independent of video subtitle rendering styles."""
from functools import lru_cache

SECONDARY_SUBTITLE_FILL = "#16877e"
SECONDARY_SUBTITLE_BORDER = "#0d5d58"
SECONDARY_SUBTITLE_ACCENT = "#5eead4"
SELECTED_LAYER_ACCENT = "#b8d7ff"
LOCK_ACCENT = "#fbbf24"


@lru_cache(maxsize=1024)
def speaker_timeline_hue(speaker: str) -> int:
    """Distribute speaker IDs over hues excluding TS2's green/cyan band."""
    value = str(speaker or "").strip()
    try:
        index = int(value.rsplit("_", 1)[-1])
    except (TypeError, ValueError):
        index = sum(ord(char) for char in value)
    # Reserve 135..205 degrees, with headroom for RGB rounding. A golden-
    # angle step over the remaining 290 degrees avoids crowding early IDs.
    hue = (index * 111 + 20) % 290
    return hue if hue < 135 else hue + 70
