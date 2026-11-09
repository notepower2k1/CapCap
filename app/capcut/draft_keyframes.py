"""Native linear keyframes; sample CapCap easing instead of guessing curves.

Schema: GuanYixuan/pyCapCut pycapcut/keyframe.py. Input times are relative
to the layer; trimmed Clip references carry _keyframe_offset/_keyframe_rate.
"""
import copy
import math

from app.capcut.draft_geometry import visual_clip


def native_points(property_type, points, make_id):
    return {"id": make_id(), "material_id": "", "property_type": property_type,
            "keyframe_list": [{"id": make_id(), "time_offset": t, "values": [v],
                "curveType": "Line", "graphID": "", "left_control": {"x": 0., "y": 0.},
                "right_control": {"x": 0., "y": 0.}} for t, v in points]}


def slice_keyframes(groups, offset, duration, make_id):
    result = []
    for group in groups:
        points = [(k["time_offset"]/1e6, k["values"][0], "linear") for k in group["keyframe_list"]]
        if not points:
            continue
        times = {0., duration}
        times.update(t-offset for t, _, _ in points if offset < t < offset+duration)
        result.append(native_points(group["property_type"],
            [(round(t*1e6), value_at(points, offset+t)) for t in sorted(times)], make_id))
    return result


def value_at(points, time):
    if time <= points[0][0]:
        return points[0][1]
    for (a, av, _), (b, bv, easing) in zip(points, points[1:]):
        if time <= b:
            t = (time - a) / (b - a)
            if easing == "ease_in":
                t *= t
            elif easing == "ease_out":
                t = 1 - (1 - t) ** 2
            elif easing == "ease_in_out":
                t = t * t * (3 - 2 * t)
            return av + (bv - av) * t
    return points[-1][1]


def visual_keyframes(layer, canvas, material, duration, make_id):
    tracks = layer.get("transform", {}).get("keyframes", [])
    if not tracks:
        return []
    supported = {"x", "y", "scale_x", "scale_y", "rotation", "opacity", "alpha", "volume"}
    parsed = {}
    offset, rate = float(layer.get("_keyframe_offset", 0)), float(layer.get("_keyframe_rate", 1))
    if not all(math.isfinite(v) for v in (offset, rate)) or rate < 0:
        raise ValueError("Invalid keyframe clock")
    times = {0., duration}
    for track in tracks:
        prop = track.get("property")
        if prop not in supported:
            raise ValueError(f"Unsupported keyframe property: {prop}")
        points = {}
        for point in track.get("keyframes", []):
            t, value = float(point["time"]), float(point["value"])
            easing = point.get("easing", "linear")
            if not all(math.isfinite(v) for v in (t, value)) or t < 0:
                raise ValueError("Invalid keyframe time or value")
            if easing not in ("linear", "ease_in", "ease_out", "ease_in_out"):
                raise ValueError(f"Unsupported keyframe easing: {easing}")
            points[t] = (t, value, easing)
        points = sorted(points.values())
        if not points:
            continue
        parsed[prop] = points
        if rate:
            for a, b in zip(points, points[1:]):
                steps = 16 if b[2] != "linear" else 1
                for i in range(steps + 1):
                    local = (a[0] + (b[0] - a[0]) * i / steps - offset) / rate
                    if 0 < local < duration:
                        times.add(local)
            for point in points:
                local = (point[0] - offset) / rate
                if 0 < local < duration:
                    times.add(local)
    if not parsed:
        return []
    # A logo's scale also moves its centre because CapCap anchors it at the
    # top-left. Recompute all clip fields at the shared sample times.
    samples = {name: {} for name in ("PositionX", "PositionY", "ScaleX", "ScaleY", "Rotation", "Alpha")}
    if "volume" in parsed:
        samples["Volume"] = {}
    for t in sorted(times):
        current = copy.deepcopy(layer)
        transform = current.setdefault("transform", {})
        for prop, points in parsed.items():
            v = value_at(points, offset + t * rate)
            if prop in ("opacity", "alpha", "volume"):
                current["opacity" if prop == "alpha" else prop] = v
            else:
                transform[prop] = v
        clip = visual_clip(current, canvas, material, framing=current.get("_framing"))
        values = (clip["transform"]["x"], clip["transform"]["y"], clip["scale"]["x"], clip["scale"]["y"],
                  clip["rotation"], clip["alpha"])
        microseconds = round(t * 1e6)
        for name, v in zip(samples, values):
            samples[name][microseconds] = v
        if "Volume" in samples:
            samples["Volume"][microseconds] = max(0., float(current.get("volume", 1)))
    return [native_points("KFType" + name, sorted(points.items()), make_id)
            for name, points in samples.items() if len(set(points.values())) > 1 or
            name == "Volume" or (name == "Alpha" and any(p in parsed for p in ("alpha", "opacity"))) or
            any(p in parsed for p in {"PositionX": ("x",), "PositionY": ("y",), "ScaleX": ("scale_x",),
                                     "ScaleY": ("scale_y",), "Rotation": ("rotation",)}.get(name, ()))]
