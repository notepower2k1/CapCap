"""Persist completed-processing timing separately from manual cue edits."""
import hashlib
import json
import math

KEY = "_completed_timing"


def warp_signature(warps):
    return hashlib.sha256(json.dumps(warps or [], sort_keys=True).encode()).hexdigest()


def capture(rows, warps=None):
    from app.services.time_warp_service import TimeWarpService
    signature = warp_signature(warps)
    media = TimeWarpService.map_segments_to_media_time(rows, warps or [])
    for row, source in zip(rows, media):
        row[KEY] = dict(start=float(row["start"]), end=float(row["end"]),
                        media_start=float(source["start"]), media_end=float(source["end"]), warp_signature=signature)


def baseline(row):
    value = row.get(KEY) or (row.get("metadata") or {}).get(KEY)
    if not isinstance(value, dict):
        return None
    try:
        values = [float(value[key]) for key in ("start", "end", "media_start", "media_end")]
        if not all(math.isfinite(item) for item in values) or values[0] < 0 or values[1] <= values[0] or values[2] < 0 or values[3] < values[2]:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return value


def restored(row, warps=None):
    from app.services.time_warp_service import TimeWarpService
    value = baseline(row)
    if value is None:
        return None
    if value.get("warp_signature") == warp_signature(warps):
        return float(value["start"]), float(value["end"])
    times = (TimeWarpService.media_to_timeline_time(float(value["media_start"]), warps or []),
             TimeWarpService.media_to_timeline_time(float(value["media_end"]), warps or []))
    return times if times[1] > times[0] else None
