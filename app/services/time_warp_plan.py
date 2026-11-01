"""One immutable timing plan shared by playback and both export formats.

Legacy project warps remain untouched. Extensions are ordered by source end
(the point at which Fit Voice ripples later cues). Shared source frames belong
to the earlier ending extension; later extensions stretch only unused frames.
Extensions ending together share that last source interval. Thus nested cues,
overlaps and repeated Fit Voice edits never replay source or lose added time.
"""
from dataclasses import dataclass
from functools import lru_cache
import math


PLAN_VERSION = 2


@dataclass(frozen=True)
class WarpSpan:
    source_start: float
    source_end: float
    duration: float  # added time, not total target duration
    type: str
    indices: tuple[int, ...]
    timeline_start: float
    timeline_end: float
    speed: float


def compile_plan(warps):
    key = []
    for w in warps or ():
        kind = str(w.get("type", "freeze"))
        start = float(w.get("media_start", w.get("time", 0))) if kind == "slow" else float(w.get("time", 0))
        end = float(w.get("media_end", start)) if kind == "slow" else start
        key.append((kind, start, end, float(w.get("duration", 0))))
    return _compile(tuple(key))


@lru_cache(maxsize=128)
def _compile(key):
    ordered = []
    for i, (kind, start, end, extra) in enumerate(key):
        if not all(math.isfinite(v) for v in (start, end, extra)):
            raise ValueError("Non-finite Fit Voice time")
        if extra <= 0:
            continue
        if kind not in ("slow", "freeze") or start < 0 or end < start:
            raise ValueError("Invalid Fit Voice source range")
        ordered.append((end, kind == "freeze", start, i, kind, extra))
    ordered.sort()
    items, source = [], 0.
    for end, _, start, i, kind, extra in ordered:
        if kind == "slow":
            start = max(start, source)
            if end <= start:
                previous = next((x for x in reversed(items) if x[3] == "slow" and x[1] == end), None)
                if previous is not None:
                    previous[2] += extra
                    previous[4].append(i)
                    continue
                # A zero-length source cue can only hold a frame.
                kind, start = "freeze", end
        items.append([start, end, extra, kind, [i]])
        source = end
    result, shift = [], 0.
    for start, end, extra, kind, indices in items:
        target_duration = end - start + extra
        speed = (end - start) / target_duration if kind == "slow" else 1.
        result.append(WarpSpan(start, end, extra, kind, tuple(indices), start + shift,
                               end + shift + extra, speed))
        shift += extra
    return tuple(result)


def normalized_warps(warps):
    """Return copies with canonical bounds/speeds; never rewrite saved cues."""
    result = []
    for span in compile_plan(warps):
        item = dict(warps[span.indices[0]])
        item.update(type=span.type, time=span.source_end, media_start=span.source_start,
                    media_end=span.source_end, duration=span.duration, speed=span.speed)
        result.append(item)
    return result


def video_parts(duration, warps, on_adjustment=None):
    """(source start, target start, target duration, speed, frozen)."""
    duration = float(duration)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("Invalid source video duration")
    source, target, result = 0., 0., []
    for span in compile_plan(warps):
        if span.source_end > duration + .01:
            raise ValueError(f"Video time warp outside source range: {span.source_end:.3f}s; video {duration:.3f}s")
        start, end = min(span.source_start, duration), min(span.source_end, duration)
        if on_adjustment:
            original = warps[span.indices[0]]
            original_start = float(original.get("media_start", original.get("time", 0)))
            if start != original_start or len(span.indices) > 1:
                on_adjustment(f"Shared Fit Voice source range exported once: {original_start:.3f}-{start:.3f}s; added duration {span.duration:.3f}s preserved.")
        if start > source:
            result.append((source, target, start - source, 1., False))
            target += start - source
        length = end - start + span.duration
        freeze = span.type == "freeze" or end == start
        speed = (end - start) / length if not freeze else 1.
        result.append((start, target, length, speed, freeze))
        source, target = end, target + length
    if source < duration:
        result.append((source, target, duration - source, 1., False))
    return result


def timeline_to_media(t, warps):
    shift = 0.
    for s in compile_plan(warps):
        if t < s.timeline_start:
            break
        if t < s.timeline_end:
            return s.source_start + (t - s.timeline_start) * s.speed if s.type == "slow" else s.source_start
        shift += s.duration
    return max(0., t - shift)


def media_to_timeline(m, warps):
    """At a freeze anchor choose its END; the inverse is otherwise unique."""
    shift = 0.
    for s in compile_plan(warps):
        if m < s.source_start:
            break
        if s.type == "slow" and m < s.source_end:
            return s.timeline_start + (m - s.source_start) / s.speed
        shift += s.duration
    return m + shift


def speed_at_media(m, warps):
    for s in compile_plan(warps):
        if s.type == "slow" and s.source_start <= m < s.source_end:
            return s.speed
    return 1.
