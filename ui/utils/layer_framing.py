"""Keep authored text/logo positions inside the visible video after reframing."""
def lookup_layout(layouts, key):
    """Read exact layouts and migrate keys written before bounds options existed."""
    if key in layouts:
        return layouts[key]
    import json
    try:
        wanted = json.loads(key)
        for old_key, value in layouts.items():
            old = json.loads(old_key)
            if len(old) == 4 and isinstance(old[0], str) and old[1] in ("fit", "fill"):
                old = old[1:]
            if len(old) == 3 and old == wanted[:3]:
                return value
    except (ValueError, TypeError, IndexError):
        return None
    return None


def reframe_layers(tracks, old_source, old_visible, new_source, new_visible, *, font_scale=1., pixel_scale=1., old_key=None, new_key=None):
    if any(rect.width() <= 0 or rect.height() <= 0 for rect in (old_source, old_visible, new_source, new_visible)):
        return False
    if old_source == new_source and old_visible == new_visible and abs(font_scale - 1.) < 1e-6 and old_key == new_key:
        return False
    changed = False
    for track in tracks:
        if getattr(track, "locked", False):
            continue
        for layer in track.layers:
            kind = str(getattr(getattr(layer, "type", None), "value", ""))
            if kind not in ("text", "image") or getattr(layer, "locked", False):
                continue
            transform = getattr(layer, "transform", None)
            if transform is None:
                continue
            if old_key is not None and new_key is not None and old_key != new_key:
                layouts = layer.metadata.setdefault("_canvas_layouts", {})
                state = {name: getattr(transform, name) for name in ("x", "y", "scale_x", "scale_y")}
                if kind == "text":
                    state["font_size"] = layer.font_size
                layouts[old_key] = state
                saved = lookup_layout(layouts, new_key)
                if isinstance(saved, dict):
                    for name in ("x", "y", "scale_x", "scale_y"):
                        setattr(transform, name, saved[name])
                    if kind == "text":
                        layer.font_size = saved["font_size"]
                    changed = True
                    continue
            logo = kind == "image"
            divisor = 100. if logo else 1.
            x, y = transform.x / divisor, transform.y / divisor
            width = .2 * transform.scale_x * old_source.width() if logo else 0.
            height = .2 * transform.scale_y * old_source.height() if logo else 0.
            center_x = old_source.left() + x * old_source.width() + width / 2
            center_y = old_source.top() + y * old_source.height() + height / 2
            relative_x = (center_x - old_visible.left()) / old_visible.width()
            relative_y = (center_y - old_visible.top()) / old_visible.height()
            scale = min(new_visible.width() / old_visible.width(), new_visible.height() / old_visible.height())
            if logo and width > 0 and height > 0:
                scale = min(scale, new_visible.width() / width, new_visible.height() / height)
            width, height = width * scale, height * scale
            if not logo:
                from app.layers.text_renderer import layout_text_layer
                from app.layers.text import TEXT_LAYER_EXPORT_SCALE
                size = max(1, round(layer.font_size * font_scale))
                def measure(size):
                    item = dict(text=layer.text, font_name=layer.font_name, font_bold=layer.font_bold,
                                font_italic=layer.font_italic, font_size=max(1, round(size * TEXT_LAYER_EXPORT_SCALE)))
                    rect = layout_text_layer(item, 1, 1).rect
                    return rect.width() * pixel_scale, rect.height() * pixel_scale
                width, height = measure(size)
                fit = min(1., new_visible.width() * .95 / max(1., width), new_visible.height() * .95 / max(1., height))
                layer.font_size = max(1, int(size * fit))
                width, height = measure(layer.font_size)
            cx = new_visible.left() + max(0., min(1., relative_x)) * new_visible.width()
            cy = new_visible.top() + max(0., min(1., relative_y)) * new_visible.height()
            margin_x = min(width / 2, new_visible.width() / 2)
            margin_y = min(height / 2, new_visible.height() / 2)
            cx = max(new_visible.left() + margin_x, min(new_visible.right() - margin_x, cx))
            cy = max(new_visible.top() + margin_y, min(new_visible.bottom() - margin_y, cy))
            transform.x = (cx - (width / 2 if logo else 0) - new_source.left()) / new_source.width() * divisor
            transform.y = (cy - (height / 2 if logo else 0) - new_source.top()) / new_source.height() * divisor
            if logo:
                transform.scale_x = width / new_source.width() / .2
                transform.scale_y = height / new_source.height() / .2
            changed = True
    return changed


def subtitle_layout(style):
    import copy
    return {key: copy.deepcopy(style[key]) for key in ("size", "background_padding", "background_radius", "position") if key in style}
