"""Experimental, local-only CapCut Desktop draft writer.

Schema references: sun-guannan/VectCutAPI (pyJianYingDraft) and a Windows
CapCut 9.2 draft. No server, account, Node runtime or cloud upload is required.
All times in the public snapshot are seconds; draft times are microseconds.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4
from fractions import Fraction
from app.capcut.draft_geometry import visual_clip
from app.capcut.draft_text import text_style, decorations
from app.capcut.draft_effects import native_blur, rectangle_mask
from app.capcut.draft_keyframes import visual_keyframes, slice_keyframes
from app.capcut.draft_audio import apply_envelope
from app.capcut.draft_fonts import apply_font
from app.capcut.draft_clips import source_clip_parts


class ExportCancelled(Exception):
    pass


def _id():
    return str(uuid4()).upper()


def _us(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid timeline time: {value}")
    return round(value * 1_000_000)


def _range(start, duration):
    return {"start": _us(start), "duration": _us(duration)}


def video_parts(duration, warps, on_adjustment=None):
    from app.services.time_warp_service import TimeWarpService
    return TimeWarpService.build_video_parts(duration, warps, on_adjustment)


class DraftExporter:
    def __init__(self, *, ffmpeg, ffprobe, progress=None, cancelled=None):
        self.ffmpeg, self.ffprobe = str(ffmpeg), str(ffprobe)
        self.progress = progress or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self._probes = {}

    def _check(self):
        if self.cancelled():
            raise ExportCancelled()

    def _run(self, command):
        self._check()
        # A file avoids deadlocks from an unconsumed stderr pipe on long inputs.
        with tempfile.TemporaryFile() as stderr:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
                started = time.monotonic()
                while True:
                    try:
                        output, _ = process.communicate(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        if self.cancelled() or time.monotonic() - started > 120:
                            process.kill()
                            process.communicate()
                            self._check()
                            raise RuntimeError("Media preparation timed out")
                if process.returncode:
                    stderr.seek(0)
                    raise RuntimeError(stderr.read()[-3000:].decode("utf-8", "replace"))
        return output

    def _probe(self, path):
        key = str(Path(path).resolve())
        if key not in self._probes:
            data = json.loads(self._run([self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", key]))
            video = next((s for s in data["streams"] if s.get("codec_type") == "video"), {})
            self._probes[key] = {
                "duration": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
                "width": int(video.get("width", 0)), "height": int(video.get("height", 0)),
                "has_audio": any(s.get("codec_type") == "audio" for s in data["streams"]),
                "fps": float(Fraction(video.get("avg_frame_rate") or "0/1")) if video.get("avg_frame_rate") != "0/0" else 0.,
            }
        return self._probes[key]

    def export(self, snapshot, destination):
        """Create a NEW self-contained directory. Never replace an existing draft."""
        snapshot = copy.deepcopy(snapshot)
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(str(destination))
        staging = Path(tempfile.mkdtemp(prefix=".capcap-draft-", dir=destination.parent))
        self.staging, self.destination = staging, destination
        self.assets, self.material_cache = {}, {}
        self._pan_cache, self._audio_envelopes = {}, []
        self.warnings = ["Experimental Windows CapCut draft; verify playback after opening and saving in CapCut.",
                         "Text appearance and keyframe easing are approximate; verify them in CapCut. Fonts require the same installed family on the destination. Text animation and colour filters are not transferred."]
        self.materials = {key: [] for key in (
            "videos", "audios", "texts", "speeds", "canvases", "effects", "transitions", "masks",
            "common_mask", "material_animations", "audio_effects", "audio_fades", "beats", "stickers",
            "sound_channel_mappings", "vocal_separations", "vocal_beautifys", "chromas", "drafts", "video_effects")}
        self.tracks, self.captions = [], []
        self.layer_report = []
        try:
            (staging / "media").mkdir()
            self._build(snapshot)
            self._check()
            staging.rename(destination)
        except BaseException:
            shutil.rmtree(staging)
            raise
        return {"path": str(destination), "warnings": self.warnings,
                "tracks": len(self.tracks), "media": len(self.assets), "native_blur_count": len(self.materials["video_effects"]),
                "native_mask_count": len(self.materials["common_mask"])}

    def _asset(self, source, audio=False):
        source = Path(source).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Missing project media: {source}")
        # Audio extracted from a video must be a native audio file in CapCut.
        extract = audio and source.suffix.lower() not in (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")
        key = (str(source), extract)
        if key not in self.assets:
            self.progress(f"Preparing media: {source.name}")
            name = f"{uuid4().hex[:12]}{'.wav' if extract else source.suffix}"
            target = self.staging / "media" / name
            if extract:
                self._run([self.ffmpeg, "-v", "error", "-i", str(source), "-vn", "-c:a", "pcm_s16le", "-y", str(target)])
            else:
                with source.open("rb") as src, target.open("wb") as dst:
                    while block := src.read(4 * 1024 * 1024):
                        self._check()
                        dst.write(block)
            self.assets[key] = (target, self.destination / "media" / name)
        return self.assets[key]

    def _material(self, source, kind):
        local, final = self._asset(source, audio=kind == "audio")
        cache_key = (str(local), kind)
        if cache_key in self.material_cache:
            return self.material_cache[cache_key]
        info = self._probe(local)
        material = {"id": _id(), "path": final.as_posix(), "duration": _us(info["duration"]),
                    "type": "extract_music" if kind == "audio" else kind,
                    "name": Path(source).name, "material_name": Path(source).name,
                    "category_id": "", "category_name": "local", "local_material_id": _id()}
        if kind == "audio":
            material.update(music_id=material["id"], check_flag=1, wave_points=[])
        else:
            material.update(width=info["width"], height=info["height"], has_audio=info["has_audio"],
                            media_path="", crop_ratio="free", crop_scale=1.0, audio_fade=None,
                            crop={"upper_left_x": 0., "upper_left_y": 0., "upper_right_x": 1., "upper_right_y": 0.,
                                  "lower_left_x": 0., "lower_left_y": 1., "lower_right_x": 1., "lower_right_y": 1.})
            if kind == "photo":
                material["duration"] = 10_800_000_000
        self.materials["audios" if kind == "audio" else "videos"].append(material)
        self.material_cache[cache_key] = (material, info)
        return material, info

    def _segment(self, material, start, duration, source=0., speed=1., volume=1., layer=None):
        if duration <= 0 or not math.isfinite(speed) or speed <= 0:
            raise ValueError("Invalid clip duration or speed")
        speed_material = {"id": _id(), "type": "speed", "mode": 0, "speed": speed, "curve_speed": None}
        self.materials["speeds"].append(speed_material)
        current = layer or {}
        clip = visual_clip(current, self.canvas, material, framing=current.get("_framing"))
        segment = {"id": _id(), "material_id": material["id"], "target_timerange": _range(start, duration),
                "source_timerange": _range(source, duration * speed), "render_timerange": _range(0, 0),
                "speed": speed, "volume": volume, "last_nonzero_volume": volume or 1.,
                "extra_material_refs": [speed_material["id"]], "clip": clip,
                "uniform_scale": {"on": abs(clip["scale"]["x"] - clip["scale"]["y"]) < 1e-8, "value": 1.}, "visible": True,
                "reverse": False, "track_attribute": 0, "track_render_index": 0,
                "render_index": 0, "common_keyframes": [], "keyframe_refs": [],
                "enable_adjust": True, "enable_lut": True, "enable_color_curves": True,
                "enable_color_wheels": True, "is_tone_modify": True}
        segment["common_keyframes"] = visual_keyframes(current, self.canvas, material, duration, _id)
        if volume == 0:
            segment["common_keyframes"] = [k for k in segment["common_keyframes"] if k["property_type"] != "KFTypeVolume"]
        else:
            gain = volume / float(current.get("volume", 1) or 1)
            for group in segment["common_keyframes"]:
                if group["property_type"] == "KFTypeVolume":
                    for key in group["keyframe_list"]:
                        key["values"][0] *= gain
        if any(k["property_type"] in ("KFTypeScaleX", "KFTypeScaleY") for k in segment["common_keyframes"]):
            segment["uniform_scale"]["on"] = False
        return segment

    def _track(self, name, kind, segments):
        # CapCut does not support overlapping segments on one track. Split
        # into lanes while retaining each cue's absolute target time.
        lanes = []
        for seg in sorted(segments, key=lambda s: s["target_timerange"]["start"]):
            start = seg["target_timerange"]["start"]
            lane = next((items for items in lanes if items[-1]["target_timerange"]["start"] + items[-1]["target_timerange"]["duration"] <= start), None)
            if lane is None:
                lane = []
                lanes.append(lane)
            lane.append(seg)
        for index, lane in enumerate(lanes):
            render = len(self.tracks)
            for seg in lane:
                seg["track_render_index"] = render
                seg["render_index"] = max(11000, render * 1000) if kind == "effect" else render * 1000
            self.tracks.append({"id": _id(), "type": kind, "name": name if not index else f"{name} {index + 1}",
                                "segments": lane, "attribute": 0, "flag": 0, "is_default_name": False})

    def _text(self, layer, start, duration):
        if layer.get("type") in ("subtitle", "dub_subtitle") and self.subtitle_position.get("custom_position_enabled"):
            layer = dict(layer, position_x=float(self.subtitle_position.get("custom_position_x", 50)),
                         position_y=float(self.subtitle_position.get("custom_position_y", 86)))
        text = str(layer.get("text", ""))
        if not text.strip():
            return None
        style = text_style(layer, self.canvas[0])
        # CapCut text ranges use UTF-16 code units (emoji count as two).
        content = {"text": text, "styles": [{"range": [0, len(text.encode("utf-16-le")) // 2],
                   "size": style["size"], "fill": {"alpha": 1., "content": {"render_type": "solid", "solid": {"alpha": style["alpha"], "color": style["rgb"]}}}}]}
        material = {"id": _id(), "type": "text", "content": json.dumps(content, ensure_ascii=False),
                    "font_size": style["size"], "alignment": 1, "check_flag": 7, "font_path": "", "font_id": "",
                    "text_color": style["color"], "line_spacing": 0.02, "letter_spacing": 0.,
                    "global_alpha": 1., "background_alpha": 0., "add_type": 0,
                    "typesetting": 0, "is_rich_text": True, "text_size": 30,
                    "fixed_width": -1, "fixed_height": -1, "line_feed": 1,
                    "line_max_width": .82, "force_apply_line_max_width": False, "fonts": []}
        self.materials["texts"].append(material)
        extra_style, extra_material = decorations(layer, self.canvas[0])
        if layer.get("background_box", True) and layer.get("background_width") == "full_area":
            warning = "Full-area subtitle backgrounds use a native text background approximation; verify their width in CapCut."
            if warning not in self.warnings:
                self.warnings.append(warning)
        content["styles"][0].update(extra_style)
        material.update(extra_material)
        family = str(layer.get("font_name", "Arial") or "Arial")
        if not apply_font(material, content["styles"][0], family):
            warning = f"Font unavailable on this machine; select it in CapCut: {family}"
            if warning not in self.warnings:
                self.warnings.append(warning)
        material["content"] = json.dumps(content, ensure_ascii=False)
        if self.layer_report:
            self.layer_report[-1].update(source_font_size=style["source_size"], capcut_font_size=style["size"], font_color=style["color"])
        seg = self._segment(material, start, duration, layer=layer)
        seg["source_timerange"] = None
        return seg

    @staticmethod
    def _layers(track, all_layers):
        if not track.get("clips"):
            return track.get("layers", [])
        result = []
        for clip in track["clips"]:
            if clip.get("layer_id") not in all_layers:
                raise ValueError(f"Missing layer for timeline clip: {clip.get('layer_id')}")
            layer = copy.deepcopy(all_layers[clip["layer_id"]])
            layer["_keyframe_offset"] = float(clip.get("source_start", 0)) - float(layer.get("source_start", 0))
            layer["_keyframe_rate"] = float(clip.get("speed", 1))
            start, duration = float(clip.get("track_start", 0)), float(clip.get("duration", 0))
            rate = layer["_keyframe_rate"]
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError("Invalid timeline clip speed")
            layer["_audio_layer_start"] = start - layer["_keyframe_offset"] / rate
            layer["_audio_layer_end"] = layer["_audio_layer_start"] + (float(layer.get("end", 0))-float(layer.get("start", 0))) / rate
            for key in ("fade_in", "fade_out"):
                if key in layer:
                    layer[key] = float(layer[key]) / rate
            layer.update(start=start, end=start + duration, source_start=float(clip.get("source_start", 0)),
                         speed=float(clip.get("speed", 1)), _clip_source_start=float(clip.get("source_start", 0)))
            if layer.get("type") == "dub_subtitle":
                layer.setdefault("metadata", {})["_audio_end"] = start + duration
            result.append(layer)
        return result

    def _audio(self, path, start, duration, source=0., speed=1., volume=1., loop=False, layer=None, metadata=None):
        if not math.isfinite(speed) or speed <= 0 or source < 0:
            raise ValueError("Invalid audio source range or speed")
        layer, metadata = layer or {}, metadata or {}
        pan = float(layer.get("pan", metadata.get("_pan", 0)))
        if not math.isfinite(pan) or not -1 <= pan <= 1:
            raise ValueError("Invalid audio pan")
        if pan:
            key = (str(Path(path).resolve()), pan)
            if key not in self._pan_cache:
                image = self.staging / f"pan_{uuid4().hex}.wav"
                left, right = 1-max(0., pan), 1+min(0., pan)
                self.progress("Preparing stereo audio")
                self._run([self.ffmpeg, "-v", "error", "-i", str(path), "-vn", "-af",
                           f"aformat=channel_layouts=stereo,pan=stereo|c0={left}*c0|c1={right}*c1",
                           "-c:a", "pcm_s16le", "-y", str(image)])
                self._pan_cache[key] = self._material(image, "audio")
                image.unlink()
            material, info = self._pan_cache[key]
            warning = "Audio pan is preserved in a processed stereo WAV; native pan controls are not transferred."
            if warning not in self.warnings:
                self.warnings.append(warning)
        else:
            material, info = self._material(path, "audio")
        if not info["has_audio"]:
            raise ValueError(f"No audio stream: {path}")
        segments, remaining, offset = [], duration, source
        call_start = start
        layer_start = float(layer.get("_audio_layer_start", layer.get("start", start)))
        layer_end = float(layer.get("_audio_layer_end", layer.get("end", start+duration)))
        while remaining > 0.000001:
            available = (info["duration"] - offset) / speed
            if available <= 0.000001:
                if not loop:
                    if remaining > .02:
                        self.warnings.append(f"Audio ends before requested clip: {Path(path).name}, {start:.3f}s, missing {remaining:.3f}s.")
                    break
                offset = 0.
                available = info["duration"] / speed
                if available <= 0:
                    raise ValueError(f"Empty audio: {path}")
            length = min(remaining, available)
            current = dict(layer)
            current["_keyframe_offset"] = float(layer.get("_keyframe_offset", 0)) + (start-call_start)*speed
            current["_keyframe_rate"] = float(layer.get("_keyframe_rate", speed))
            segments.append(self._segment(material, start, length, offset, speed, volume, current))
            segments[-1]["clip"] = None
            segments[-1]["common_keyframes"] = [k for k in segments[-1]["common_keyframes"] if k["property_type"] == "KFTypeVolume"]
            self._audio_envelopes.append((segments[-1], layer, layer_start, layer_end, metadata))
            remaining -= length
            start += length
            offset += length * speed
        return segments

    def _regional_tracks(self, timeline, all_layers, base_segments, source_size):
        """Editable, muted source copies under text/logo, split at every warp.

        Match the reference recipe (spectrika/capcut-mcp blur_region): source
        clip + rectangle mask + per-clip effect. Share the media file only;
        speed/effect/mask materials belong to each independent overlay clip.
        """
        if not base_segments:
            return
        media = {m["id"]: m for m in self.materials["videos"]}
        solids = {}
        for track in timeline.get("tracks", []):
            overlays = []
            for layer in self._layers(track, all_layers):
                self._check()
                kind = layer.get("type")
                if kind not in ("blur", "mask") or not track.get("visible", True) or not layer.get("visible", True):
                    continue
                start, end = _us(layer.get("start", 0)), _us(layer.get("end", 0))
                if end <= start:
                    continue
                mode = "pixelate" if layer.get("pixelate") else "blur"
                if kind == "mask":
                    mode = layer.get("mode", "solid")
                if mode not in ("blur", "pixelate", "solid"):
                    raise ValueError(f"Unsupported mask mode: {mode}")
                opacity = float(layer.get("opacity", 1)) * (float(layer.get("blur_opacity", 1)) if kind == "blur" else 1.)
                if not math.isfinite(opacity):
                    raise ValueError("Invalid region opacity")
                if opacity <= 0:
                    continue
                mask = rectangle_mask(layer, source_size, _id,
                                      mirrored=bool(base_segments[0]["clip"]["flip"]["horizontal"]))
                if mask is None:
                    continue
                effect = native_blur(dict(layer, pixelate=mode == "pixelate"), _id) if mode != "solid" else None
                solid = None
                if mode == "solid":
                    style = text_style({"font_color": layer.get("color", "#000000")}, self.canvas[0])
                    key = (style["color"], style["alpha"])
                    if key not in solids:
                        from PIL import Image
                        image = self.staging / f"mask_{uuid4().hex}.png"
                        rgba = tuple(round(v * 255) for v in style["rgb"]) + (round(style["alpha"] * 255),)
                        Image.new("RGBA", source_size, rgba).save(image)
                        solids[key], _ = self._material(image, "photo")
                        image.unlink()
                    solid = solids[key]
                count = 0
                for base in base_segments:
                    target = base["target_timerange"]
                    left, right = max(start, target["start"]), min(end, target["start"] + target["duration"])
                    if right <= left:
                        continue
                    segment = copy.deepcopy(base)
                    segment.update(id=_id(), target_timerange={"start": left, "duration": right - left},
                                   volume=0., last_nonzero_volume=1.)
                    segment["clip"]["alpha"] *= max(0., min(1., opacity))
                    segment["common_keyframes"] = slice_keyframes(base["common_keyframes"],
                        (left-target["start"])/1e6, (right-left)/1e6, _id)
                    for group in segment["common_keyframes"]:
                        if group["property_type"] == "KFTypeAlpha":
                            for key in group["keyframe_list"]:
                                key["values"][0] *= max(0., min(1., opacity))
                    source = base["source_timerange"]
                    material = solid or media[base["material_id"]]
                    if material["type"] == "photo":
                        segment["source_timerange"] = {"start": 0, "duration": right - left}
                        segment["speed"] = 1.
                    else:
                        # Use shared integer boundaries so adjacent slices do
                        # not overlap or drift at rounded slow-motion speeds.
                        a = round((left - target["start"]) * source["duration"] / target["duration"])
                        b = round((right - target["start"]) * source["duration"] / target["duration"])
                        segment["source_timerange"] = {"start": source["start"] + a, "duration": b - a}
                    segment["material_id"] = material["id"]
                    speed = {"id": _id(), "type": "speed", "mode": 0, "speed": segment["speed"], "curve_speed": None}
                    self.materials["speeds"].append(speed)
                    native_mask = copy.deepcopy(mask)
                    native_mask["id"] = _id()
                    self.materials["common_mask"].append(native_mask)
                    segment["extra_material_refs"] = [speed["id"], native_mask["id"]]
                    if effect:
                        native_effect = copy.deepcopy(effect)
                        native_effect["id"] = _id()
                        self.materials["video_effects"].append(native_effect)
                        segment["extra_material_refs"].append(native_effect["id"])
                    overlays.append(segment)
                    count += 1
                self.layer_report.append({"name": layer.get("name", track.get("name", "Region")), "type": kind,
                    "start": start / 1e6, "end": end / 1e6, "status": "native_region" if count else "outside_video",
                    "effect": effect["name"] if effect else "Solid", "region_transferred": bool(count),
                    "segments": count, "source_region": {key: layer.get(key) for key in ("position_x", "position_y", "width", "height")}})
            self._track(f"{track.get('name', 'Region')} Regions", "video", overlays)
        if self.materials["common_mask"]:
            self.warnings.append("Blur/mask rectangles are transferred as editable source overlays. Effect intensity is approximate; verify areas in CapCut. CapCut may need to download native mask/effect resources.")

    def _build(self, snapshot):
        timeline = snapshot["timeline"]
        all_layers = {layer["id"]: layer for track in timeline.get("tracks", []) for layer in track.get("layers", []) if layer.get("id")}
        source_tracks = {t.get("name"): t for t in timeline.get("tracks", []) if t.get("name") in ("V1 Video", "A1 Audio")}
        for track in source_tracks.values():
            for clip in track.get("clips", []):
                if clip.get("layer_id") not in all_layers:
                    raise ValueError(f"Missing layer for source track clip: {clip.get('layer_id')}")
                path = all_layers[clip["layer_id"]].get("source", "")
                allowed = (snapshot["source_video"], snapshot.get("original_audio", ""))
                if path and not any(str(Path(path).resolve()).casefold() == str(Path(p).resolve()).casefold() for p in allowed if p):
                    raise ValueError("Source track clip must reference the project source; use an overlay track for other media")
        comp = timeline.get("composition", {})
        width, height = int(comp.get("width", 1920)), int(comp.get("height", 1080))
        fps = float(comp.get("fps", 30))
        if min(width, height, fps) <= 0 or not math.isfinite(fps):
            raise ValueError("Invalid output canvas")
        main, info = self._material(snapshot["source_video"], "video")
        if snapshot.get("source_canvas"):
            width, height = info["width"], info["height"]
        if snapshot.get("source_fps") and info["fps"] > 0:
            fps = info["fps"]
        self.canvas = (width, height)
        self.subtitle_position = snapshot.get("subtitle_position", {})
        if min(info["width"], info["height"]) <= 0:
            raise ValueError("Source file has no usable video dimensions")
        if info["duration"] <= 0:
            raise ValueError("Cannot determine source video duration")
        full_parts = video_parts(info["duration"], snapshot.get("warps", []), self.warnings.append)
        v1, a1 = source_tracks.get("V1 Video", {}), source_tracks.get("A1 Audio", {})
        specs = source_clip_parts(full_parts, v1["clips"]) if v1.get("clips") else [(part, None) for part in full_parts]
        parts = [part for part, _ in specs]
        base_layer = {"type": "video", "mirror_horizontal": snapshot.get("mirror_horizontal", False),
                      "_framing": snapshot.get("framing", {})}
        video_segments = []
        for (source, start, duration, speed, freeze), source_clip in specs:
            current = dict(base_layer)
            if source_clip:
                current.update(copy.deepcopy(all_layers[source_clip["layer_id"]]))
                current.update(base_layer)
            current.update(_keyframe_offset=source, _keyframe_rate=0. if freeze else speed)
            material = main
            if freeze:
                image = self.staging / f"freeze_{uuid4().hex}.png"
                self.progress("Preparing freeze frame")
                self._run([self.ffmpeg, "-v", "error", "-ss", str(max(0, min(source, info["duration"] - 1. / fps))),
                           "-i", snapshot["source_video"], "-frames:v", "1", "-y", str(image)])
                material, _ = self._material(image, "photo")
                image.unlink()
            video_segments.append(self._segment(material, start, duration, 0 if freeze else source, speed, 0., current))
        if v1.get("visible", True):
            self._track("V1 Video", "video", video_segments)
        self._regional_tracks(timeline, all_layers, video_segments if v1.get("visible", True) else [], (info["width"], info["height"]))
        original = snapshot.get("original_audio")
        if original and not self._probe(original)["has_audio"]:
            self.warnings.append("Source has no original audio stream; exported video and other audio tracks.")
            original = ""
        if original and float(snapshot.get("original_volume", 1)) > 0 and a1.get("visible", True) and not (a1.get("muted") or a1.get("metadata", {}).get("_muted")):
            original_segments = []
            audio_specs = source_clip_parts(full_parts, a1["clips"]) if a1.get("clips") else [(part, None) for part in parts]
            for (source, start, duration, speed, freeze), source_clip in audio_specs:
                if not freeze:
                    layer = copy.deepcopy(all_layers[source_clip["layer_id"]]) if source_clip else {}
                    if layer.get("muted") or not layer.get("visible", True):
                        continue
                    layer.update(_audio_layer_start=float(source_clip.get("track_start", 0)) if source_clip else 0.,
                                 _audio_layer_end=float(source_clip.get("track_start", 0))+float(source_clip["duration"]) if source_clip else max(p[1]+p[2] for p in parts),
                                 _keyframe_offset=source, _keyframe_rate=speed)
                    original_segments.extend(self._audio(original, start, duration, source, speed,
                        float(snapshot.get("original_volume", 1))*float(layer.get("volume", 1)), layer=layer, metadata=a1.get("metadata", {})))
            self._track("A1 Original", "audio", original_segments)
        voice = snapshot.get("voice_audio", "")
        voice_volume = float(snapshot.get("voice_volume", 1))
        if voice and snapshot.get("voice_full_track") and voice_volume > 0:
            duration = max(start + length for _, start, length, _, _ in parts)
            voice_track = next((t for t in timeline.get("tracks", []) if t.get("name") in ("TS1", "A2 Dub")), {})
            self._track("Imported Voice", "audio", self._audio(voice, 0, duration, volume=voice_volume, metadata=voice_track.get("metadata", {})))
        for track in timeline.get("tracks", []):
            self._check()
            name = track.get("name", "Layer")
            if name in ("V1 Video", "A1 Audio"):
                continue
            text_visible = track.get("visible", True)
            track_muted = track.get("muted", False) or track.get("metadata", {}).get("_muted", False)
            segments, voices = [], []
            for layer in self._layers(track, all_layers):
                if not layer.get("visible", True) and layer.get("type") != "dub_subtitle":
                    continue
                kind = layer.get("type")
                if kind in ("blur", "mask"):
                    continue  # already inserted directly above the source
                start, end = float(layer.get("start", 0)), float(layer.get("end", 0))
                duration = end - start
                if duration <= 0:
                    continue
                supported = kind in ("subtitle", "dub_subtitle", "text", "image", "video", "audio", "sticker", "blur")
                self.layer_report.append({"name": layer.get("name", name), "type": kind, "start": start, "end": end,
                                          "status": "converted" if supported else "unsupported"})
                if kind in ("subtitle", "dub_subtitle", "text"):
                    seg = self._text(layer, start, duration)
                    if seg and text_visible and layer.get("visible", True):
                        segments.append(seg)
                        if kind != "text":
                            self.captions.append((start, end, layer.get("text", "")))
                    if kind == "dub_subtitle" and not snapshot.get("voice_full_track") and voice_volume > 0 and not track_muted and not layer.get("muted", False):
                        # Prefer the independent processed cue WAV (already
                        # speed-adjusted). Legacy layers may only have a full
                        # voice-track path, which must be read at timeline time.
                        cue_file = layer.get("_voice_file", "")
                        path = cue_file or voice or layer.get("audio_path", "")
                        if path:
                            if layer.get("metadata", {}).get("_tts_dirty"):
                                self.warnings.append(f"Voice needs regeneration: {name}, {start:.3f}s; exported the existing audio.")
                            audio_end = float(layer.get("metadata", {}).get("_audio_end", end))
                            voices.extend(self._audio(path, start, max(0, audio_end - start),
                                                      float(layer.get("_clip_source_start", 0. if cue_file else start)),
                                                      float(layer.get("speed", 1)), volume=voice_volume, layer=layer, metadata=track.get("metadata", {})))
                elif kind in ("image", "video", "sticker"):
                    if not text_visible or not layer.get("visible", True):
                        continue
                    if kind == "sticker":
                        if Path(layer.get("source", "")).suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
                            self.layer_report[-1]["status"] = "unsupported"
                            self.warnings.append(f"Skipped unsupported animated sticker layer: {name}")
                            continue
                    material, media = self._material(layer.get("source", ""), "photo" if kind in ("image", "sticker") else "video")
                    source = float(layer.get("source_start", 0))
                    speed = float(layer.get("speed", 1))
                    if not math.isfinite(speed) or speed <= 0:
                        raise ValueError("Invalid video layer speed")
                    if kind == "video":
                        duration = min(duration, (media["duration"] - source) / speed)
                    if duration > 0:
                        volume = 0.
                        if kind == "video" and media["has_audio"] and not track_muted and not layer.get("muted", False):
                            volume = float(layer.get("volume", 1)) * float(track.get("metadata", {}).get("_volume", 100)) / 100. * 10**(float(track.get("metadata", {}).get("_gain_db", 0))/20)
                        segment = self._segment(material, start, duration, source, speed, volume, layer)
                        if volume:
                            if float(layer.get("pan", track.get("metadata", {}).get("_pan", 0))):
                                segment["volume"] = 0.
                                segment["common_keyframes"] = [k for k in segment["common_keyframes"] if k["property_type"] != "KFTypeVolume"]
                                voices.extend(self._audio(layer["source"], start, duration, source, speed, volume,
                                                          layer=layer, metadata=track.get("metadata", {})))
                            else:
                                self._audio_envelopes.append((segment, layer, start, start+duration, track.get("metadata", {})))
                        segments.append(segment)
                    else:
                        self.layer_report[-1]["status"] = "empty_source_range"
                        self.warnings.append(f"Skipped video clip outside its source duration: {name}")
                elif kind == "audio":
                    # Music is supplied separately through the same resolver
                    # as preview, including loop, solo, mute and track volume.
                    if any(m.get("id") == layer.get("id") for m in snapshot.get("music", [])):
                        continue
                    if text_visible and not track_muted and not layer.get("muted", False):
                        gain = float(layer.get("volume", 1)) * float(track.get("metadata", {}).get("_volume", 100)) / 100. * 10 ** (float(track.get("metadata", {}).get("_gain_db", 0)) / 20.)
                        voices.extend(self._audio(layer.get("source", ""), start, duration,
                                                 float(layer.get("source_start", 0)), float(layer.get("speed", 1)), gain,
                                                 layer=layer, metadata=track.get("metadata", {})))
                else:
                    self.warnings.append(f"Skipped unsupported layer: {kind} ({name})")
            self._track(name, "text" if track.get("type") in ("text", "subtitle", "dub_subtitle") else "video", segments)
            self._track(f"{name} Audio", "audio", voices)
        for music in snapshot.get("music", []):
            if not music.get("muted"):
                self._track(music.get("track_name", "Music"), "audio", self._audio(
                    music["path"], float(music.get("start", 0)), float(music["end"]) - float(music.get("start", 0)),
                    float(music.get("source_start", 0)), speed=float(music.get("speed", 1)),
                    volume=float(music.get("volume", 100)) / 100., loop=bool(music.get("loop")),
                    layer=music.get("layer", music), metadata=music.get("track_metadata", {})))
        total = max((s["target_timerange"]["start"]+s["target_timerange"]["duration"] for t in self.tracks for s in t["segments"]), default=0)/1e6
        for segment, layer, start, end, metadata in self._audio_envelopes:
            fade = apply_envelope(segment, layer, start, end, metadata, total, _id)
            if fade:
                self.materials["audio_fades"].append(fade)
        self._write(snapshot, width, height, fps)

    def _write(self, snapshot, width, height, fps):
        now, timeline_id, draft_id = time.time_ns() // 1000, _id(), _id()
        duration = max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                        for track in self.tracks for s in track["segments"]), default=0)
        platform = {"app_id": 359289, "app_source": "cc", "app_version": "9.2.0", "os": "windows", "os_version": "10",
                    "device_id": "", "hard_disk_id": "", "mac_address": ""}
        draft = {"id": timeline_id, "version": 360000, "new_version": "181.0.0", "name": self.destination.name,
                 "duration": duration, "create_time": now, "update_time": now, "fps": fps,
                 "canvas_config": {"width": width, "height": height, "ratio": "original", "background": None},
                 "config": {"maintrack_adsorb": False, "subtitle_sync": False, "use_float_render": False},
                 "tracks": self.tracks, "materials": self.materials, "keyframes": {}, "keyframe_graph_list": [],
                 "platform": platform, "last_modified_platform": platform, "color_space": 0,
                 "relationships": [], "group_container": None, "mutable_config": None,
                 "mixed_track_mode_on": False, "render_index_track_mode_on": False, "free_render_index_mode_on": False}
        meta = {"draft_id": draft_id, "draft_name": self.destination.name, "draft_fold_path": self.destination.as_posix(),
                "draft_root_path": self.destination.parent.as_posix(), "draft_materials": [], "draft_cover": "",
                "tm_draft_create": now, "tm_draft_modified": now, "tm_draft_removed": 0, "tm_duration": duration,
                "draft_is_invisible": False, "draft_is_cloud_temp_draft": False}
        project = {"id": draft_id, "version": 0, "main_timeline_id": timeline_id, "create_time": now, "update_time": now,
                   "timelines": [{"id": timeline_id, "name": "Timeline 01", "create_time": now, "update_time": now,
                                  "is_marked_delete": False}], "config": {"color_space": -1, "use_float_render": False}}
        for filename in ("draft_content.json", "draft_content.json.bak", "template-2.tmp",
                         f"Timelines/{timeline_id}/draft_content.json", f"Timelines/{timeline_id}/draft_content.json.bak",
                         f"Timelines/{timeline_id}/template.tmp", f"Timelines/{timeline_id}/template-2.tmp"):
            self._json(filename, draft)
        self._json("draft_meta_info.json", meta)
        self._json("Timelines/project.json", project)
        self._json("Timelines/project.json.bak", project)
        self._json("capcap_package.json", {"format": "capcap-capcut-package", "version": 1,
                   "fonts": [{"material_id": m["id"], "family": m["font_name"]} for m in self.materials["texts"]],
                   "media": [{"material_id": material["id"],
                              "relative_path": Path(material["path"]).relative_to(self.destination).as_posix()}
                             for key in ("videos", "audios") for material in self.materials[key]]})
        # Deliberate allowlist: never serialize ProjectState/settings/API keys.
        self._json("capcap_export_report.json", {"schema_version": 1, "experimental": True, "warnings": self.warnings,
                   "native_blur_count": len(self.materials["video_effects"]),
                   "native_mask_count": len(self.materials["common_mask"]),
                   "tracks": len(self.tracks), "media": len(self.assets), "duration_seconds": duration / 1e6,
                   "canvas": {"width": width, "height": height, "fps": fps}, "source_name": Path(snapshot["source_video"]).name})
        self._json("capcap_layers.json", {"layers": self.layer_report,
                   "framing": snapshot.get("framing", {"mode": "fit", "focus": [.5, .5]})})
        def stamp(value):
            ms = round(value * 1000)
            return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
        srt = "\n\n".join(f"{i}\n{stamp(start)} --> {stamp(end)}\n{text}"
                            for i, (start, end, text) in enumerate(sorted(self.captions), 1))
        (self.staging / "subtitles.srt").write_text(srt + "\n", encoding="utf-8-sig")
        (self.staging / "README.txt").write_text(
            "CapCap experimental CapCut Desktop export\n\n"
            "Export into the CapCut local drafts directory, then restart CapCut.\n"
            "Default: %LOCALAPPDATA%/CapCut/User Data/Projects/com.lveditor.draft\n"
            "To move this package, copy the entire folder and use More > Import exported CapCut package in CapCap.\n"
            "Import creates a new draft and relinks copied media to the destination.\n"
            "Verify video, subtitles and audio, save, close and reopen before editing further.\n"
            "Desktop compatibility requires a real open/save test on your installed version.\n\n"
            + "\n".join(self.warnings), encoding="utf-8")

    def _json(self, name, data):
        path = self.staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def register_local_draft(destination):
    """Append to the local draft index; caller must ensure CapCut is closed.

    Preserve unknown fields and keep an exact backup. This never changes an
    existing project. Only call when explicitly exporting into a drafts root.
    """
    destination = Path(destination).resolve()
    index = destination.parent / "root_meta_info.json"
    original = index.read_bytes() if index.exists() else None
    data = json.loads(original.decode("utf-8-sig")) if original is not None else {
        "all_draft_store": [], "draft_ids": 0, "root_path": destination.parent.as_posix()}
    entries = data.get("all_draft_store")
    if not isinstance(entries, list) or not isinstance(data.get("draft_ids", 0), int):
        raise ValueError("Unsupported CapCut project index; exported files are intact")
    meta = json.loads((destination / "draft_meta_info.json").read_text(encoding="utf-8"))
    if any(item.get("draft_id") == meta["draft_id"] for item in entries):
        return
    entry = dict(meta)
    entry["draft_json_file"] = str(destination / "draft_content.json")
    entries.append(entry)
    data["draft_ids"] = max(data.get("draft_ids", 0) + 1, len(entries))
    # Keep the index backup OUTSIDE the shareable project: it may contain
    # names and paths belonging to unrelated projects on this computer.
    backup = destination.parent / f"capcap_index_before_{meta['draft_id']}.bak"
    if original is not None:
        backup.write_bytes(original)
    pending = destination.parent / f".capcap-index-{uuid4().hex}.tmp"
    try:
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        current = index.read_bytes() if index.exists() else None
        if current != original:
            raise RuntimeError("CapCut project index changed during export; exported files are intact")
        os.replace(pending, index)
    finally:
        pending.unlink(missing_ok=True)
