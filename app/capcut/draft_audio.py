"""Audio envelopes in timeline time, including loops and Fit Voice splits."""
import math
from app.capcut.draft_keyframes import native_points, value_at


def fade_value(time, start, end, fade_in, fade_out):
    value = 1.
    if fade_in > 0:
        value = min(value, max(0., (time - start) / fade_in))
    if fade_out > 0:
        value = min(value, max(0., (end - time) / fade_out))
    return value


def apply_envelope(segment, layer, start, end, metadata, total, make_id):
    """Combine per-layer fades, project-wide track fades and volume keys.

    Use native fade handles for an entire, unsplit layer with no track fade;
    otherwise use absolute volume keyframes so internal cuts never restart it.
    """
    fades = [float(layer.get("fade_in", 0)), float(layer.get("fade_out", 0)),
             float(metadata.get("_fade_in", 0)), float(metadata.get("_fade_out", 0))]
    if not all(math.isfinite(v) and v >= 0 for v in fades):
        raise ValueError("Invalid audio fade duration")
    if not any(fades):
        return None
    target = segment["target_timerange"]
    left, duration = target["start"] / 1e6, target["duration"] / 1e6
    right = left + duration
    existing = next((k for k in segment["common_keyframes"] if k["property_type"] == "KFTypeVolume"), None)
    if not any(fades[2:]) and sum(fades[:2]) <= duration and existing is None and abs(left-start) < 1e-6 and abs(right-end) < 1e-6:
        material = {"id": make_id(), "type": "audio_fade", "fade_type": 0,
                    "fade_in_duration": round(min(duration, fades[0]) * 1e6),
                    "fade_out_duration": round(min(duration, fades[1]) * 1e6)}
        segment["extra_material_refs"].append(material["id"])
        return material
    times = {left, right}
    for a, b, fi, fo in ((start, end, *fades[:2]), (0., total, *fades[2:])):
        times.update(t for t in (a, a+fi, b-fo, b) if left <= t <= right)
        if fi > 0 and fo > 0 and fi+fo > b-a:
            crossing = a + (b-a) * fi / (fi+fo)
            if left < crossing < right:
                times.add(crossing)
    keys = [(p["time_offset"]/1e6, p["values"][0], "linear") for p in existing["keyframe_list"]] if existing else []
    times.update(left + p[0] for p in keys if 0 <= p[0] <= duration)
    if existing:
        segment["common_keyframes"].remove(existing)
    # Products of overlapping envelopes are curved; sample their ramps.
    edges = sorted(times)
    for a,b in zip(edges, edges[1:]):
        times.update(a+(b-a)*i/16 for i in range(1,16))
    base = segment["volume"]
    points = {}
    for t in sorted(times):
        gain = value_at(keys, t-left) if keys else base
        gain *= fade_value(t, start, end, *fades[:2]) * fade_value(t, 0., total, *fades[2:])
        points[round((t-left)*1e6)] = max(0., gain)
    segment["common_keyframes"].append(native_points("KFTypeVolume", sorted(points.items()), make_id))
    return None
