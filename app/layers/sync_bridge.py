from __future__ import annotations

import os
from typing import Any

from app.layers.base import LayerType
from app.layers.dub_subtitle import DubSubtitleLayer
from app.layers.audio import AudioLayer
from app.layers.blur import BlurLayer
from app.layers.timeline import Timeline, Track


DUB_SUBTITLE_TRACK_NAME = "TS1"


def _get_segment_text(d: dict[str, Any]) -> str:
    return str(d.get("text", d.get("final_text", d.get("subtitle_text", ""))))


def find_or_create_track(
    timeline: Timeline,
    name: str,
    layer_type: LayerType,
    height: int = 80,
) -> Track:
    """Find track by name, or create it."""
    for t in timeline.tracks:
        if t.name == name and t.type == layer_type:
            return t
    track = Track(name=name, type=layer_type, height=height)
    timeline.tracks.append(track)
    return track


def remove_track(timeline: Timeline, name: str) -> None:
    """Remove tracks by name."""
    timeline.tracks[:] = [t for t in timeline.tracks if t.name != name]


def sync_segments_to_dub_subtitle_layers(
    timeline: Timeline,
    segments: list[dict[str, Any]],
) -> list[DubSubtitleLayer]:
    """Convert flat segment dicts to DubSubtitleLayers on the single TS1
    track. Each layer holds both the display text (subtitle) and the
    voice text (TTS). One layer = one segment.

    `dub_text` defaults to `text` so a never-voice-generated segment
    still works in voice-only mode. Audio path and voice settings are
    populated by the voice workflow after generation.

    Preserves any existing `audio_path` / `muted` / `tts_settings` for
    a layer that already exists for the given segment index, so
    re-syncing the timeline doesn't clobber voice data.
    """
    if not segments:
        for t in list(timeline.tracks):
            t_type = getattr(t, "type", None)
            name = str(getattr(t, "name", "") or "").strip().upper()
            if (
                t_type in (LayerType.DUB_SUBTITLE, LayerType.SUBTITLE)
                or name.startswith("TS")
                or name == "SUBTITLE"
            ):
                t.layers = []
        return []


    indexed = list(enumerate(segments))
    indexed.sort(key=lambda kv: float(kv[1].get("start", 0)))

    target = find_or_create_track(
        timeline, DUB_SUBTITLE_TRACK_NAME, LayerType.DUB_SUBTITLE, 100
    )

    existing_by_idx: dict[int, DubSubtitleLayer] = {}
    existing_by_key: dict[tuple[float, float, str], list[DubSubtitleLayer]] = {}
    layer_to_idx: dict[int, int] = {}
    for layer in target.layers:
        if isinstance(layer, DubSubtitleLayer):
            idx = -1
            if isinstance(layer.metadata, dict):
                raw_idx = layer.metadata.get("_seg_index")
                if raw_idx is None and isinstance(layer.metadata.get("_seg_dict"), dict):
                    raw_idx = layer.metadata["_seg_dict"].get("_seg_index")
                if raw_idx is not None:
                    try:
                        idx = int(raw_idx)
                    except (TypeError, ValueError):
                        idx = -1
            if idx < 0:
                try:
                    idx = int(getattr(layer, "z_index", -1))
                except (TypeError, ValueError):
                    idx = -1
            if idx >= 0:
                existing_by_idx[idx] = layer
                layer_to_idx[id(layer)] = idx
            key = (
                round(float(getattr(layer, "start", 0.0) or 0.0), 6),
                round(float(getattr(layer, "end", 0.0) or 0.0), 6),
                str(getattr(layer, "text", "") or ""),
            )
            existing_by_key.setdefault(key, []).append(layer)
    # Index fallback is useful when all subtitle text changes together, but
    # it is unsafe when the list length changes: inserting a cue would then
    # recycle the next existing layer and make its timeline identity move.
    allow_index_fallback = len(existing_by_idx) == len(segments)

    new_layers: list[DubSubtitleLayer] = []
    for orig_idx, d in indexed:
        start = float(d.get("start", 0))
        end = float(d.get("end", 0))
        text = _get_segment_text(d)
        dub_text = str(
            d.get("dubbing_vi") or d.get("tts_text") or text
        )
        # Stamp the segment's own _seg_index so downstream consumers
        # (e.g. sync_tts_to_dub_subtitle_layers) can match segments to
        # layers without having to walk the layer list. Without this,
        # segments loaded fresh from translation_final.json (which has
        # no _seg_index) would never reach the layer's metadata.
        d["_seg_index"] = int(orig_idx)

        key = (round(start, 6), round(end, 6), text)
        matching = existing_by_key.get(key) or []
        existing = matching.pop(0) if matching else (
            existing_by_idx.pop(orig_idx, None) if allow_index_fallback else None
        )
        if existing is not None:
            existing_idx = layer_to_idx.pop(id(existing), None)
            if existing_idx is not None:
                existing_by_idx.pop(existing_idx, None)
        if existing is not None:
            existing.text = text
            existing.dub_text = dub_text
            existing.start = start
            existing.end = end
            seg_speed = d.get("voice_speed")
            if seg_speed is not None:
                try:
                    existing.voice_speed = float(seg_speed)
                except (TypeError, ValueError):
                    pass
            existing.metadata["_seg_index"] = int(orig_idx)
            existing.metadata["_seg_dict"] = {
                k: v for k, v in d.items() if k != "text"
            }
            d_meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            raw_ae = d.get("_audio_end") if d.get("_audio_end") is not None else d_meta.get("_audio_end")
            if raw_ae is not None:
                try:
                    existing.metadata["_audio_end"] = float(raw_ae)
                except (TypeError, ValueError):
                    existing.metadata.pop("_audio_end", None)
            else:
                existing.metadata.pop("_audio_end", None)

            raw_ext = d.get("extended_duration") if d.get("extended_duration") is not None else d_meta.get("extended_duration")
            if raw_ext is not None:
                try:
                    existing.metadata["extended_duration"] = float(raw_ext)
                except (TypeError, ValueError):
                    existing.metadata.pop("extended_duration", None)
            else:
                existing.metadata.pop("extended_duration", None)

            raw_warp = d.get("time_warp_id") if d.get("time_warp_id") is not None else d_meta.get("time_warp_id")
            if raw_warp:
                existing.metadata["time_warp_id"] = str(raw_warp)
            else:
                existing.metadata.pop("time_warp_id", None)
            layer = existing
        else:
            seg_speed = d.get("voice_speed", 1.0)
            try:
                seg_speed = float(seg_speed)
            except (TypeError, ValueError):
                seg_speed = 1.0
            layer = DubSubtitleLayer(
                name=f"Sub {orig_idx + 1}",
                text=text,
                dub_text=dub_text,
                start=start,
                end=end,
                voice_speed=seg_speed,
            )
            layer.z_index = orig_idx
            layer.metadata["_seg_index"] = int(orig_idx)
            layer.metadata["_seg_dict"] = {
                k: v for k, v in d.items() if k != "text"
            }
            d_meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            raw_ae = d.get("_audio_end") if d.get("_audio_end") is not None else d_meta.get("_audio_end")
            if raw_ae is not None:
                try:
                    layer.metadata["_audio_end"] = float(raw_ae)
                except (TypeError, ValueError):
                    layer.metadata.pop("_audio_end", None)
            else:
                layer.metadata.pop("_audio_end", None)

            raw_ext = d.get("extended_duration") if d.get("extended_duration") is not None else d_meta.get("extended_duration")
            if raw_ext is not None:
                try:
                    layer.metadata["extended_duration"] = float(raw_ext)
                except (TypeError, ValueError):
                    layer.metadata.pop("extended_duration", None)
            else:
                layer.metadata.pop("extended_duration", None)

            raw_warp = d.get("time_warp_id") if d.get("time_warp_id") is not None else d_meta.get("time_warp_id")
            if raw_warp:
                layer.metadata["time_warp_id"] = str(raw_warp)
            else:
                layer.metadata.pop("time_warp_id", None)
            target.layers.append(layer)
        new_layers.append(layer)

    # Segment indices are renumbered after a deletion, so they cannot be used
    # to identify stale layers here: the following cue may now have the same
    # index as the removed cue.  The remaining entries in existing_by_idx are
    # the actual layer objects that were not matched above.
    stale_layer_ids = {id(layer) for layer in existing_by_idx.values()}
    if stale_layer_ids:
        target.layers[:] = [
            l for l in target.layers
            if id(l) not in stale_layer_ids
        ]
    return new_layers


def sync_layers_to_segments(timeline: Timeline) -> list[dict[str, Any]]:
    """Read DubSubtitleLayers back as flat segment dicts (used by the
    SRT burn-in path and the voice workflow).
    """
    segments: list[dict[str, Any]] = []
    for track in timeline.tracks:
        if track.type != LayerType.DUB_SUBTITLE:
            continue
        for layer in track.layers:
            if not isinstance(layer, DubSubtitleLayer):
                continue
            d: dict[str, Any] = dict(layer.metadata.get("_seg_dict", {}))
            d["id"] = int(d.get("id", 0))
            d["start"] = layer.start
            d["end"] = layer.end
            d["text"] = layer.text
            d["tts_text"] = layer.dub_text
            d["dubbing_vi"] = layer.dub_text
            d["subtitle_vi"] = layer.text
            d["voice_speed"] = layer.voice_speed
            if "final_text" in d and d["final_text"]:
                d["final_text"] = layer.text
            segments.append(d)
    segments.sort(key=lambda s: (s["start"], s.get("id", 0)))
    return segments


def sync_tts_to_dub_subtitle_layers(
    timeline: Timeline,
    voice_track_path: str,
    segments: list[dict[str, Any]] | None = None,
) -> None:
    """Wire the generated TTS voice track onto each DubSubtitleLayer.

    For each segment in `segments`, finds the matching DubSubtitleLayer
    on TS1 by `_seg_index` and stores the `voice_track_path` + the
    segment's `_audio_end` (if present) on the layer. The layer's
    `dub_text` is also refreshed from the segment.
    """
    from app.layers.base import LayerType
    target = None
    for t in timeline.tracks:
        if t.name == DUB_SUBTITLE_TRACK_NAME and t.type == LayerType.DUB_SUBTITLE:
            target = t
            break
    if target is None or not voice_track_path:
        return

    index_to_audio_end: dict[int, float] = {}
    positional_audio_end: list[tuple[float, float]] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        try:
            start_s = float(seg.get("start", 0.0))
            end_s = float(seg.get("end", 0.0))
            audio_end = float(seg.get("_audio_end", end_s))
        except (TypeError, ValueError):
            continue
        positional_audio_end.append((start_s, audio_end))
        try:
            idx = int(seg.get("_seg_index", -1))
        except (TypeError, ValueError):
            idx = -1
        if idx >= 0:
            index_to_audio_end[idx] = (start_s, audio_end)
    for layer_pos, layer in enumerate(target.layers):
        idx = int(layer.metadata.get("_seg_index", -1)) if isinstance(layer.metadata, dict) else -1
        if idx in index_to_audio_end:
            _start, audio_end = index_to_audio_end[idx]
        elif layer_pos < len(positional_audio_end):
            # Fallback: match by layer position in the track, in case
            # the segment dicts never got _seg_index stamped on them
            # (e.g. when the timeline was hydrated from an older project
            # state). Layers are appended in segment-start order by
            # sync_segments_to_dub_subtitle_layers, so positional
            # alignment is safe.
            _start, audio_end = positional_audio_end[layer_pos]
        else:
            continue
        layer.audio_path = str(voice_track_path)
        layer.metadata["_audio_end"] = audio_end


def sync_blur_regions_to_layers(
    timeline: Timeline,
    blur_regions: list[dict[str, Any]] | None,
) -> None:
    """Convert blur region data to BlurLayers on the B1 blur track."""
    if not blur_regions:
        remove_track(timeline, "B1")
        return

    # Keep B1 rows the same compact height as M1/L1 in the editor.
    blur_track = find_or_create_track(timeline, "B1", LayerType.BLUR, 60)
    blur_track.height = 60
    blur_track.layers.clear()

    for i, br in enumerate(blur_regions):
        layer = BlurLayer(
            name=f"Blur {i + 1}",
            start=float(br.get("start", 0)),
            end=float(br.get("end", timeline.duration)),
            position_x=float(br.get("x", br.get("position_x", 0))),
            position_y=float(br.get("y", br.get("position_y", 0))),
            width=float(br.get("width", 200)),
            height=float(br.get("height", 80)),
            blur_strength=float(br.get("blur_strength", br.get("intensity", 20))),
            blur_opacity=float(br.get("blur_opacity", 1.0)),
            pixelate=bool(br.get("pixelate", False)),
            pixelate_size=int(br.get("pixelate_size", 12)),
        )
        layer.z_index = i
        blur_track.layers.append(layer)


def ensure_v1_a1_tracks(timeline: Timeline, video_path: str, duration: float) -> None:
    """Ensure V1 (video) and A1 (audio) tracks exist after video import."""
    if duration <= 0:
        return
    from app.layers.video import VideoLayer
    from app.layers.transform import Transform

    v1 = find_or_create_track(timeline, "V1 Video", LayerType.VIDEO, 80)
    v1.visible = True
    v1.layers.clear()
    v1.layers.append(VideoLayer(
        name="V1 Video",
        source=video_path,
        start=0.0,
        end=duration,
        transform=Transform(x=0, y=0, scale_x=1.0, scale_y=1.0),
    ))

    a1 = find_or_create_track(timeline, "A1 Audio", LayerType.AUDIO, 80)
    a1.visible = True
    a1.layers.clear()
    a1.layers.append(AudioLayer(
        name="A1 Audio",
        source=video_path,
        start=0.0,
        end=duration,
        volume=1.0,
    ))
    if not isinstance(a1.metadata, dict):
        a1.metadata = {}
    a1.metadata.setdefault("_volume", 50.0)
