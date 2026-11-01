"""Canvas conversions for editable CapCut clips (no image/video baking)."""
import math


def visual_clip(layer, canvas, media=None, *, framing=None):
    width, height = canvas
    transform = layer.get("transform", {})
    sx, sy = float(transform.get("scale_x", 1)), float(transform.get("scale_y", 1))
    kind = layer.get("type", "video")
    if kind in ("image", "sticker"):
        if not media or min(media["width"], media["height"]) <= 0:
            raise ValueError("Cannot determine image dimensions")
        fit = min(width / media["width"], height / media["height"])
        x = float(transform.get("x", 10)) / 100 + .1 * sx
        y = float(transform.get("y", 10)) / 100 + .1 * sy
        sx, sy = .2 * width * sx / (media["width"] * fit), .2 * height * sy / (media["height"] * fit)
        x, y = 2 * x - 1, 1 - 2 * y
    elif kind in ("subtitle", "dub_subtitle"):
        x, y = float(layer.get("position_x", 50)) / 50 - 1, 1 - float(layer.get("position_y", 90)) / 50
    elif kind == "text":
        x, y = 2 * float(transform.get("x", .5)) - 1, 1 - 2 * float(transform.get("y", .5))
    else:
        x, y = 2 * float(transform.get("x", 0)) / width, -2 * float(transform.get("y", 0)) / height
    if framing and framing.get("mode") == "fill":
        mw, mh = media["width"], media["height"]
        fit, fill = min(width / mw, height / mh), max(width / mw, height / mh)
        fx, fy = framing.get("focus", (.5, .5))
        fx, fy = max(0., min(1., float(fx))), max(0., min(1., float(fy)))
        if layer.get("mirror_horizontal"):
            fx = 1 - fx
        sx, sy = sx * fill / fit, sy * fill / fit
        x -= 2 * (mw * fill - width) * (fx - .5) / width
        y += 2 * (mh * fill - height) * (fy - .5) / height
    values = (sx, sy, x, y, float(transform.get("rotation", 0)), float(layer.get("opacity", 1)))
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Non-finite layer transform")
    return {"scale": {"x": sx, "y": sy}, "transform": {"x": x, "y": y},
            "rotation": values[4], "alpha": max(0., min(1., values[5])),
            "flip": {"horizontal": bool(layer.get("mirror_horizontal", False)), "vertical": False}}
