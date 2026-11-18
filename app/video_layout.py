"""Portable video layout configuration and shared FFmpeg composition geometry."""
from __future__ import annotations
import copy
import math
import re

KEY = "video_layout"
DEFAULT = dict(template="free", background="color", color="#101826", image="", blur=24,
               inset=6, video_height=58, video_x=50, video_y=50, corner=0, shadow=0,
               custom_frame=False, hook="", title="", part="", description="")

FRAME_DEFAULTS = dict(inset=0, video_height=100, video_x=50, video_y=50, corner=0, shadow=0)


def has_video_frame(settings):
    return bool(settings and (settings.get('template', 'free') != 'free' or settings.get('custom_frame')))


def config(value=None):
    result = dict(DEFAULT)
    if isinstance(value, dict):
        result.update({k: copy.deepcopy(v) for k, v in value.items() if k in DEFAULT})
    if result["template"] not in ("free", "hook", "parts", "frame"):
        result["template"] = "free"
    if result["background"] not in ("color", "image", "blur"):
        result["background"] = "color"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(result["color"])):
        result["color"] = DEFAULT["color"]
    for key, lower, upper in (("blur", 1, 60), ("inset", 0, 30), ("video_height", 20, 100), ("video_x", 0, 100), ("video_y", 0, 100), ("corner", 0, 100), ("shadow", 0, 40)):
        try:
            number = float(result[key])
            result[key] = max(lower, min(upper, number)) if math.isfinite(number) else DEFAULT[key]
        except (TypeError, ValueError):
            result[key] = DEFAULT[key]
    for key in ("image", "hook", "title", "part", "description"):
        result[key] = str(result[key] or "")
    return result


def cache_key(settings):
    """Separate ratio-roundtrip caches for different video slots."""
    cfg = config(settings)
    return ':'.join(['layout']+[f'{float(cfg[key]):g}' for key in ('inset','video_height','video_x','video_y')])


def geometry(settings, width, height, source_w, source_h, mode="fit", focus=(.5, .5)):
    cfg = config(settings)
    if not has_video_frame(cfg):
        cfg.update(FRAME_DEFAULTS)
    # The template only defines a slot; Fit/Fill retains the source shape.
    inset = min(width, height) * cfg["inset"] / 100
    slot_w = max(2., width - inset * 2)
    slot_h = max(2., min(height - inset * 2, height * cfg["video_height"] / 100))
    x, y = (width - slot_w) * cfg['video_x']/100, (height - slot_h) * cfg['video_y']/100
    scale = (max if mode == "fill" else min)(slot_w / source_w, slot_h / source_h)
    dw, dh = source_w * scale, source_h * scale
    ox = x + (slot_w - dw) * (focus[0] if mode == "fill" else .5)
    oy = y + (slot_h - dh) * (focus[1] if mode == "fill" else .5)
    return (x, y, slot_w, slot_h), (ox, oy, dw, dh)


def map_point(x, y, settings, source_size, canvas_size, mode="fit", focus=(.5, .5)):
    _, (ox, oy, dw, dh) = geometry(settings, *canvas_size, *source_size, mode, focus)
    return (ox + float(x) * dw) / canvas_size[0], (oy + float(y) * dh) / canvas_size[1]


def canvas_point(x, y, settings, source_size, canvas_size, mode="fit", focus=(.5, .5)):
    _, (ox, oy, dw, dh) = geometry(settings, *canvas_size, *source_size, mode, focus)
    return (float(x) * canvas_size[0] - ox) / dw, (float(y) * canvas_size[1] - oy) / dh


def filter_chain(settings, width, height, source_w, source_h, mode="fit", focus=(.5, .5)):
    """Single-input/output lavfi graph; backgrounds inherit source timestamps."""
    cfg = config(settings)
    width, height = int(width), int(height)
    slot, displayed = geometry(cfg, width, height, source_w, source_h, mode, focus)
    if mode == "fill":
        x, y, fw, fh = slot
        fw, fh = max(2, int(fw)//2*2), max(2, int(fh)//2*2)
        main = f"scale={fw}:{fh}:force_original_aspect_ratio=increase,crop={fw}:{fh}:(iw-ow)*{focus[0]}:(ih-oh)*{focus[1]}"
    else:
        x, y, fw, fh = displayed
        fw, fh = max(2, int(fw)//2*2), max(2, int(fh)//2*2)
        main = f"scale={fw}:{fh}"
    x, y = int(round(x)), int(round(y))
    fill = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    graph = ["split=2[layout_bg_src][layout_main_src]"]
    if cfg["background"] == "blur":
        graph.append(f"[layout_bg_src]{fill},gblur=sigma={cfg['blur']},setsar=1[layout_bg]")
    elif cfg["background"] == "image" and cfg["image"]:
        # Escape the option parser, then the surrounding filtergraph parser.
        # Passing this as one argv value avoids shell quoting entirely.
        path = cfg['image'].replace('\\', '/')
        path = ''.join('\\'+c if c in "\\':" else c for c in path)
        path = ''.join('\\'+c if c in "\\'[],;" else c for c in path)
        # Keep the source video as the clock. A looping 25-fps image source
        # would otherwise drive the overlay and stutter/drop 30/60-fps video.
        # Decode/scale one image frame; framesync retains it after EOF.
        graph += [f"[layout_bg_src]scale={width}:{height},drawbox=color=black:t=fill,setsar=1[layout_clock]",
                  f"movie=filename={path}:loop=1:dec_threads=1,trim=end_frame=1,{fill},setsar=1,setpts=PTS-STARTPTS[layout_still]",
                  "[layout_clock][layout_still]overlay=0:0:eof_action=repeat:repeatlast=1[layout_bg]"]
    else:
        graph.append(f"[layout_bg_src]scale={width}:{height},drawbox=color={cfg['color']}:t=fill,setsar=1[layout_bg]")
    main += ",setsar=1,format=yuva420p"
    radius = min(cfg["corner"], fw / 2, fh / 2)
    if radius > 0:
        r = f"{radius:g}"
        alpha = f"if(gt(between(X,{r},W-{r})+between(Y,{r},H-{r}),0),255,if(lte(pow(X-if(lt(X,{r}),{r},W-{r}),2)+pow(Y-if(lt(Y,{r}),{r},H-{r}),2),{r}*{r}),255,0))"
        # The rounded alpha mask is static. Calculate it once, rather than
        # evaluating GEQ over every RGB/YUV pixel in every video frame.
        graph.append(f"color=c=white:s={fw}x{fh}:r=1,trim=end_frame=1,format=gray,geq=lum='{alpha}'[layout_mask]")
        if cfg['shadow'] > 0:
            graph.append('[layout_mask]split=2[layout_mask_main][layout_mask_shadow]')
        else:
            graph.append('[layout_mask]null[layout_mask_main]')
        graph += [f"[layout_main_src]{main}[layout_video]",
                  '[layout_video][layout_mask_main]alphamerge=eof_action=repeat:repeatlast=1[layout_face]']
    else:
        graph.append(f"[layout_main_src]{main}[layout_face]")
    if cfg["shadow"] > 0:
        offset = max(2, int(cfg["shadow"]))
        # The shadow depends on the frame's silhouette, not video content.
        # Its alpha, colour conversion and blur can all be cached as one frame.
        shadow = f"color=c=black:s={fw}x{fh}:r=1,trim=end_frame=1,format=yuva420p"
        if radius > 0:
            graph += [shadow+'[layout_shadow_black]',
                      '[layout_shadow_black][layout_mask_shadow]alphamerge=shortest=1[layout_shadow_shape]']
            shadow = '[layout_shadow_shape]'
        else:
            shadow += ','
        graph += [f"{shadow}colorchannelmixer=aa=0.5,gblur=sigma={max(1, offset / 2)}[layout_shadow]",
                  f"[layout_bg][layout_shadow]overlay={x+offset}:{y+offset}:eof_action=repeat:repeatlast=1[layout_shadowed]",
                  f"[layout_shadowed][layout_face]overlay={x}:{y}:shortest=1,format=yuv420p"]
    else:
        graph.append(f"[layout_bg][layout_face]overlay={x}:{y}:shortest=1,format=yuv420p")
    return ";".join(graph)
