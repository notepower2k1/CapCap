"""Slice a source layer's expanded (Fit Voice) clock into editable clips."""
import math


def source_clip_parts(parts, clips):
    """Clip.source_start is an offset in the referenced source layer timeline.

    V1/A1's source-layer timeline already includes global Fit Voice holds and
    slows. A clip can trim/retime that clock without applying a warp twice.
    Return the original clip alongside each piece for its layer properties.
    """
    total = max(start+duration for _,start,duration,_,_ in parts)
    result = []
    for clip in clips:
        start, source, duration, speed = (float(clip.get(k, default)) for k,default in (
            ("track_start", 0), ("source_start", 0), ("duration", 0), ("speed", 1)))
        if not all(math.isfinite(v) for v in (start, source, duration, speed)) or min(start, source) < 0 or min(duration, speed) <= 0:
            raise ValueError("Invalid source track clip")
        end = source + duration*speed
        if end > total + .000001:
            raise ValueError("Source track clip extends beyond its Fit Voice timeline")
        for media, target, length, base_speed, freeze in parts:
            left, right = max(source, target), min(end, target+length)
            if right > left:
                result.append(((media if freeze else media+(left-target)*base_speed,
                                start+(left-source)/speed, (right-left)/speed,
                                1. if freeze else base_speed*speed, freeze), clip))
    return result
