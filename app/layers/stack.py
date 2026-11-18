"""Shared bottom-to-top order for editable decoration tracks."""

TRACK_GROUPS = {"M1": "mask", "L1 Logo": "logo", "T1 Text": "text"}
DEFAULT_ORDER = ("mask", "logo", "text")


def overlay_order(value):
    """Return the saved order, filling in groups added after saving."""
    if not value:
        return []
    result = list(dict.fromkeys(group for group in value if group in DEFAULT_ORDER))
    return result + [group for group in DEFAULT_ORDER if group not in result]
