"""Shared bottom-to-top order for editable decoration tracks."""

TRACK_GROUPS = {"M1": "mask", "L1 Logo": "logo", "T1 Text": "text"}
DEFAULT_ORDER = ("mask", "logo", "text")


def overlay_order(value):
    """Return the saved order, filling in groups added after saving."""
    if not value:
        return []
    result = list(dict.fromkeys(group for group in value if group in DEFAULT_ORDER))
    return result + [group for group in DEFAULT_ORDER if group not in result]


def sync_overlay_rows(timeline):
    """Keep movable rows in render order while leaving pinned rows in place."""
    order = overlay_order(timeline.overlay_order)
    if not order:
        return
    ranks = {group: index for index, group in enumerate(order)}
    indices = [i for i, track in enumerate(timeline.tracks) if track.name in TRACK_GROUPS]
    tracks = sorted((timeline.tracks[i] for i in indices),
                    key=lambda track: ranks[TRACK_GROUPS[track.name]], reverse=True)
    for index, track in zip(indices, tracks):
        timeline.tracks[index] = track
