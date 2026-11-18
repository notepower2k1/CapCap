"""Portable layout recipes; never include a project's media or subtitle content."""
import copy
import math
from uuid import uuid4

from app.video_layout import config, geometry
from app.layers.timeline import Track
from app.runtime_paths import asset_path

VISUAL_TYPES = frozenset(('background', 'text', 'image', 'blur', 'mask'))
LOGO_ASSET = '@layout/logo'
BACKGROUND_ASSET = '@layout/background'


def validate(recipe):
    from app.output_geometry import normalize_canvas
    try:
        if recipe['version'] != 1:
            raise ValueError('Unsupported layout preset version')
        for key in ('source', 'canvas'):
            values = recipe[key]
            if len(values) != 2 or any(not math.isfinite(float(v)) or float(v) <= 0 for v in values):
                raise ValueError('Invalid layout dimensions')
        if not math.isfinite(float(recipe['duration'])) or float(recipe['duration']) <= 0:
            raise ValueError('Invalid layout duration')
        normalize_canvas(recipe['controls']['ratio'])
        if not isinstance(recipe['styles'], dict) or any(not isinstance(recipe['styles'].get(role), dict)
                                                        for role in ('primary', 'secondary')):
            raise ValueError('Invalid subtitle styles')
        for row in recipe['tracks']:
            track = Track.from_dict(row['track'])
            if any(l.type.value not in VISUAL_TYPES for l in track.layers):
                raise ValueError('Unsupported layer in layout preset')
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('Invalid layout preset') from exc


def sample_asset(token):
    if token == LOGO_ASSET:
        return asset_path('capcap_white.png')
    if token == BACKGROUND_ASSET:
        return asset_path('layouts', 'background.png')
    raise ValueError('Unknown layout image asset')


def visual_tracks(model):
    """Also retain original row indices for exact undo ordering."""
    result = []
    for index, track in enumerate(model.tracks):
        layers = [l for l in track.layers if l.type.value in VISUAL_TYPES]
        if not layers:
            continue
        saved = copy.deepcopy(track.to_dict())
        saved['layers'] = [copy.deepcopy(l.to_dict()) for l in layers]
        ids = {l.id for l in layers}
        saved['clips'] = [c for c in saved['clips'] if c['layer_id'] in ids]
        result.append(dict(index=index, track=saved))
    return result


def replace_visual_tracks(model, saved):
    """Leave video, subtitle and audio data (and their IDs) untouched."""
    for track in model.tracks:
        ids = {l.id for l in track.layers if l.type.value in VISUAL_TYPES}
        track.layers[:] = [l for l in track.layers if l.id not in ids]
        track.clips[:] = [c for c in track.clips if c.layer_id not in ids]
    restored_ids = {row['track']['id'] for row in saved}
    model.tracks[:] = [tr for tr in model.tracks if tr.layers or tr.clips or
                      (tr.type.value not in VISUAL_TYPES and tr.id not in restored_ids)]
    for row in saved:
        restored = Track.from_dict(copy.deepcopy(row['track']))
        existing = next((tr for tr in model.tracks if tr.id == restored.id), None)
        if existing:
            existing.layers.extend(restored.layers)
            existing.clips.extend(restored.clips)
        else:
            model.tracks.insert(min(row['index'], len(model.tracks)), restored)


def capture(model, *, layout, controls, styles, canvas, source):
    """Replace all image paths with bundled asset tokens before persistence."""
    cfg = config(layout) if layout else None
    if cfg:
        cfg['image'] = BACKGROUND_ASSET if cfg['background'] == 'image' else ''
    tracks = visual_tracks(model)
    for row in tracks:
        tr = row['track']
        tr['metadata'] = {}
        # Visual layers carry their own intervals; clips are project-specific.
        tr['clips'] = []
        for layer in tr['layers']:
            layer['metadata'] = {}
            if layer['type'] == 'image':
                layer['source'] = LOGO_ASSET
                layer['filters'] = {}
            elif layer['type'] == 'background':
                layer['source'] = BACKGROUND_ASSET if layer['mode'] == 'image' else ''
    return dict(version=1, layout=cfg, controls=copy.deepcopy(controls), styles=copy.deepcopy(styles),
                canvas=list(canvas), source=list(source), duration=max(.001, float(model.duration)), tracks=tracks)


def _display_rect(layout, source, canvas, controls):
    return geometry(config(layout), *canvas, *source, controls.get('scale_mode', 'fit'),
                    controls.get('focus', (.5, .5)))[1]


def materialize(recipe, *, layout, source, canvas, controls, duration):
    """Rebase canvas placement to the new source and scale intervals/keyframes."""
    validate(recipe)
    duration = max(.001, float(duration))
    factor = duration / max(.001, float(recipe['duration']))
    old_canvas = recipe['canvas']
    old = _display_rect(recipe['layout'], recipe['source'], old_canvas, recipe['controls'])
    new = _display_rect(layout, source, canvas, controls)
    height_factor = canvas[1] / old_canvas[1]

    def position(value, axis, divisor):
        pixel = old[axis] + float(value) / divisor * old[axis + 2]
        return (pixel / old_canvas[axis] * canvas[axis] - new[axis]) / new[axis + 2] * divisor

    def scale(value, axis):
        return float(value) * old[axis + 2] / old_canvas[axis] * canvas[axis] / new[axis + 2]

    tracks = copy.deepcopy(recipe['tracks'])
    for row in tracks:
        tr = row['track']
        tr.update(id=uuid4().hex[:12], clips=[], metadata={'layout_owned': True})
        for layer in tr['layers']:
            kind = layer['type']
            if kind not in VISUAL_TYPES:
                raise ValueError('Unsupported layer in layout preset')
            layer['id'] = uuid4().hex[:12]
            layer['metadata'] = {'layout_role': 'saved-' + layer['id']}
            layer['start'] = max(0., min(duration, float(layer['start']) * factor))
            layer['end'] = max(layer['start'], min(duration, float(layer['end']) * factor))
            if kind == 'background':
                layer.update(start=0., end=duration)
                if layer['mode'] == 'image':
                    layer['source'] = sample_asset(BACKGROUND_ASSET)
            elif kind == 'image':
                layer['source'] = sample_asset(LOGO_ASSET)
            if kind in ('image', 'text'):
                transform = layer['transform']
                divisor = 100. if kind == 'image' else 1.
                for axis, name in enumerate(('x', 'y')):
                    transform[name] = position(transform[name], axis, divisor)
                if kind == 'image':
                    for axis, name in enumerate(('scale_x', 'scale_y')):
                        transform[name] = scale(transform[name], axis)
                for channel in transform.get('keyframes', []):
                    name = channel['property']
                    for keyframe in channel['keyframes']:
                        keyframe['time'] *= factor
                        if name in ('x', 'y'):
                            keyframe['value'] = position(keyframe['value'], ('x', 'y').index(name), divisor)
                        elif kind == 'image' and name in ('scale_x', 'scale_y'):
                            keyframe['value'] = scale(keyframe['value'], ('scale_x', 'scale_y').index(name))
                if kind == 'text':
                    for name in ('font_size', 'outline_width', 'letter_spacing'):
                        layer[name] *= height_factor
                    layer['font_size'] = max(1, round(layer['font_size']))
    styles = copy.deepcopy(recipe['styles'])
    for style in styles.values():
        for key in ('size', 'background_padding', 'background_radius'):
            if key in style:
                style[key] = max(0, round(style[key] * height_factor))
    # Parse now, before any live timeline mutation.
    for row in tracks:
        Track.from_dict(row['track'])
    return tracks, styles
