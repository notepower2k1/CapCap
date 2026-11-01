"""Selective, portable CapCap resources. No provider credentials or caches.

Packages are ordinary folders: selected readable assets + a versioned
manifest with SHA-256 checksums. Import always creates a new local project.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import csv
import errno
import hashlib
import io
import json
import math
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
from uuid import UUID, uuid4

from app.capcut.draft_export import ExportCancelled
from app.translation.srt_utils import to_srt, parse_srt

FORMAT = "capcap-resource-package"
MANIFEST = "capcap_package.json"
MISSING_VIDEO = "This package is missing the source video. Export it again with Source video selected to import it into CapCap."
PATH_KEYS = {"source", "audio_path", "voice_file", "_wav_path", "_voice_file", "path"}
SEGMENT_KEYS = {"id", "start", "end", "text", "original_text", "source_text", "tts_text", "voice_edited",
                "voice_name", "voice_speed", "_auto_fit_applied", "_audio_end", "extended_duration", "time_warp_id",
                "speaker", "words", "manual_highlights", "auto_highlights", "_wav_path", "voice_file",
                "tts_group_id", "tts_group_start", "tts_group_end"}
META_KEYS = {"_seg_index", "_audio_end", "_wav_path", "_tts_dirty", "speaker", "voice_name", "voice_speed",
             "_auto_fit_applied", "words", "manual_highlights", "auto_highlights", "extended_duration", "time_warp_id"}
TRACK_META = {"_volume", "_gain_db", "_fade_in", "_fade_out", "_pan", "_muted", "_solo", "_audio_role"}
SETTINGS_KEYS = {"video_time_warps", "subtitle_style_controls", "preview_track_visibility", "video_mirror_horizontal",
                 "audio_handling_mode", "speaker_voice_assignments", "transcript_imported", "transcription_engine", "blur_state", "package_output_controls"}


def _windows_lock_error(exc):
    winerror = getattr(exc, "winerror", None)
    # Python's CRT-backed open() can lose the original Windows sharing code
    # and surface only EACCES; native rename/unlink retain WinError 32/5.
    return winerror in (5, 32, 33) or (winerror is None and os.name == "nt" and
                                      exc.errno in (errno.EACCES, errno.EPERM, errno.EBUSY))


def _retry_windows_io(action, check=lambda: None, *, deadline=None):
    """New files can briefly be held by Windows scanners/indexers.

    Retry only Windows sharing/lock/access errors, closing all our own file
    handles on each attempt. Keep cancellation responsive during the wait.
    """
    deadline = time.monotonic()+4. if deadline is None else deadline
    delay = .05
    while True:
        check()
        try:
            return action()
        except OSError as exc:
            if not _windows_lock_error(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(min(delay, max(0., deadline-time.monotonic())))
            delay = min(.25, delay*1.5)


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k,v in value.items() if not any(s in k.lower() for s in
                ("api_key", "apikey", "token", "secret", "password", "credential"))}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return copy.deepcopy(value)


def safe_segment(segment):
    result = _clean({k: v for k,v in segment.items() if k in SEGMENT_KEYS})
    start, end = float(result.get("start", 0)), float(result.get("end", 0))
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end < start:
        raise ValueError("Invalid subtitle timing")
    return result


def _safe_settings(settings):
    # Nested style maps contain only simple editor controls. Remove credential
    # and external path keys defensively, even inside a malformed project.
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k,v in value.items() if not any(s in k.lower() for s in
                    ("api", "token", "secret", "password", "credential", "path", "prompt"))}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return {k: clean(v) for k,v in settings.items() if k in SETTINGS_KEYS}


def safe_timeline(timeline):
    from app.layers.timeline import Timeline
    value = Timeline.from_dict(copy.deepcopy(timeline)).to_dict()
    for track in value.get("tracks", []):
        track["metadata"] = {k:v for k,v in track.get("metadata", {}).items() if k in TRACK_META}
        for layer in track.get("layers", []):
            raw = layer.get("metadata", {})
            layer["metadata"] = _clean({k:v for k,v in raw.items() if k in META_KEYS})
            if isinstance(raw.get("_seg_dict"), dict):
                layer["metadata"]["_seg_dict"] = safe_segment(raw["_seg_dict"])
            # Filter metadata is intentionally not part of this package.
            layer.pop("filters", None)
    return value


def build_catalog(snapshot):
    """Describe available files; never read/copy media on the UI thread."""
    items = []
    def payload(item_id, group, label, relative, role, value=None, text=None):
        data = text.encode("utf-8-sig") if text is not None else json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        items.append({"id": item_id, "group": group, "label": label, "relative_path": relative, "role": role,
                      "data": data, "payload": value, "size": len(data), "default_checked": True})
    def file(item_id, group, label, path, relative, role, metadata=None, checked=True):
        if not path or not Path(path).is_file():
            return
        path = Path(path).resolve()
        items.append({"id": item_id, "group": group, "label": label, "relative_path": relative,
                      "role": role, "source": str(path), "size": path.stat().st_size,
                      "metadata": metadata or {}, "default_checked": checked})
    for kind, group in (("source", "Original transcript"), ("translated", "Translated subtitles")):
        segments = [safe_segment(s) for s in snapshot.get(kind+"_segments", [])]
        if not segments:
            continue
        payload(kind+"_srt", group, "Complete subtitles (SRT)", f"subtitles/{kind}.srt", kind+"_srt", text=to_srt(segments, max_gap_ms=0))
        payload(kind+"_txt", group, "Plain text (TXT)", f"subtitles/{kind}.txt", kind+"_txt", text="\n".join(s.get("text", "") for s in segments))
        payload(kind+"_json", group, "Timed cues and speaker data (JSON)", f"subtitles/{kind}.json", kind+"_json", value=segments)
    for entry in snapshot.get("tts", []):
        index = int(entry["index"])
        metadata = {k: entry[k] for k in ("index", "start", "end", "text") if k in entry}
        suffix = Path(entry.get("path", "")).suffix.lower() or ".wav"
        file(f"tts_{index}", "Individual TTS clips", f"{index:04d} · {entry.get('text', '')[:80]}", entry.get("path"),
             f"audio/tts/{index:04d}{suffix}", "tts_clip", metadata)
    labels = {"voice_vi": "Full dubbed voice", "mixed_vi": "Full audio mix", "music": "Separated background music",
              "vocals": "Separated original vocals", "extracted_audio": "Original audio", "final_video": "Rendered video"}
    for role, label in labels.items():
        path = snapshot.get("files", {}).get(role, "")
        file(role, "Audio and rendered video", label, path, f"{'video' if role == 'final_video' else 'audio'}/{role}{Path(path).suffix.lower()}", role,
             checked=role != "final_video")
    path = snapshot.get("video_path", "")
    file("source_video", "Media", "Source video (large file)", path, f"media/source_video/{Path(path).name}", "source_video", checked=False)
    if snapshot.get("timeline"):
        timeline = safe_timeline(snapshot["timeline"])
        payload("timeline", "Project editing data", "Timeline, layers and clip positions", "project/timeline.json", "timeline", value=timeline)
        known = {str(Path(path).resolve())} if path else set()
        for track in timeline["tracks"]:
            for layer in track["layers"]:
                source = layer.get("source", "")
                if source and str(Path(source).resolve()) not in known:
                    known.add(str(Path(source).resolve()))
                    number = len(known)
                    file(f"layer_media_{number}", "Media", Path(source).name, source,
                         f"media/layer_{number}{Path(source).suffix.lower()}", "layer_media")
    context = {k: copy.deepcopy(snapshot[k]) for k in ("input_language", "target_language", "mode") if k in snapshot}
    context["settings"] = _safe_settings(snapshot.get("settings", {}))
    payload("editor", "Project editing data", "Subtitle appearance, Fit Voice and editor settings", "project/editor.json", "editor", value=context)
    return items


def _relative(value):
    value = str(value).replace("\\", "/")
    part = PurePosixPath(value)
    if not value or part.is_absolute() or any(p in ("..", ".") for p in part.parts) or ":" in value:
        raise ValueError("Invalid path in CapCap package")
    return part.as_posix()


def _file(root, relative):
    relative = _relative(relative)
    path = (root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or unsafe package file: {relative}")
    return path


def _read(path):
    if path.stat().st_size > 64*1024*1024:
        raise ValueError("Package document is too large")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_package(source):
    root = Path(source).resolve()
    manifest = _read(_file(root, MANIFEST))
    if manifest.get("format") != FORMAT or manifest.get("version") != 1:
        raise ValueError("This is not a supported CapCap resource package")
    entries = manifest.get("items", [])
    if not isinstance(entries, list) or not entries or len(entries) > 20000:
        raise ValueError("Invalid package resource list")
    ids, paths = set(), set()
    roles = {"source_video", "timeline", "editor", "tts_clip", "layer_media", "voice_vi", "mixed_vi", "music", "vocals", "extracted_audio", "final_video"}
    roles.update(k+"_"+ext for k in ("source", "translated") for ext in ("json", "srt", "txt"))
    for item in entries:
        if not isinstance(item, dict) or item.get("role") not in roles or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("Invalid package resource list")
        if not all(isinstance(item.get(k), str) for k in ("label", "group", "relative_path")) or not isinstance(item.get("size"), int) or item["size"] < 0:
            raise ValueError("Invalid package resource list")
        relative = _relative(item["relative_path"])
        if item["id"] in ids or relative.casefold() in paths:
            raise ValueError("Duplicate resource in package")
        ids.add(item["id"])
        paths.add(relative.casefold())
        if item["role"] == "source_video" and not (root/relative).is_file():
            raise ValueError(MISSING_VIDEO)
        path = _file(root, relative)
        if path.stat().st_size != item["size"] or len(item.get("sha256", "")) != 64:
            raise ValueError(f"Package file changed: {relative}")
    if manifest.get("package_id"):
        try:
            manifest["package_id"] = str(UUID(str(manifest["package_id"])))
        except ValueError as exc:
            raise ValueError("Invalid CapCap package ID") from exc
    else:
        # Old packages did not have a UUID. Derive a stable identity without
        # rewriting them, independent of folder name and manifest formatting.
        identity = sorted(({k: i.get(k) for k in ("role", "relative_path", "sha256", "metadata")} for i in entries),
                          key=lambda i: i["relative_path"])
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        manifest["package_id"] = "legacy-"+digest
    if manifest.get("project_uuid"):
        try:
            manifest["project_uuid"] = str(UUID(str(manifest["project_uuid"])))
        except ValueError as exc:
            raise ValueError("Invalid CapCap project ID") from exc
    return manifest


def require_package_video(manifest, selected=None):
    video = next((i for i in manifest["items"] if i["role"] == "source_video"), None)
    if video is None:
        raise ValueError(MISSING_VIDEO)
    if selected is not None and video["id"] not in selected:
        raise ValueError("The source video must be selected when importing a CapCap package.")
    return video


def find_imported_package(workspace, package_id, project_uuid=None):
    from app.core.state import ProjectState
    for path in sorted((Path(workspace).resolve()/"projects").glob("*/project.json")):
        try:
            data = _read(path)
            video = Path(data.get("input_video", ""))
            same_project = bool(project_uuid and ProjectState.from_dict(data).project_uuid == project_uuid)
            if (same_project or data.get("settings", {}).get("resource_package_id") == package_id) and video.is_file():
                return {"path": str(path.parent), "video_path": str(video), "files": 0,
                        "warnings": [], "already_imported": True, "package_id": package_id,
                        "duplicate_kind": "project" if same_project else "package"}
        except (OSError, ValueError, TypeError):
            continue
    return None


@contextmanager
def _import_lock(workspace):
    """Prevent two app instances from passing the duplicate check together."""
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root/".capcap-resource-import.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Another CapCap package import is running. Please wait for it to finish.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _copy(source, target, check, expected=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    def attempt():
        digest = hashlib.sha256()
        with source.open("rb") as src, target.open("wb") as dst:
            while block := src.read(4*1024*1024):
                check()
                digest.update(block)
                dst.write(block)
        return digest.hexdigest()
    result = _retry_windows_io(attempt, check)
    if expected and result != expected:
        raise ValueError(f"Package file checksum mismatch: {source.name}")
    return result


def _cleanup_owned(path, parent):
    if path.resolve().parent != parent.resolve():
        raise RuntimeError("Refusing to clean a directory outside the staging parent")
    deadline = time.monotonic()+4.
    def retry(function, filename, exc):
        if not _windows_lock_error(exc):
            raise exc
        _retry_windows_io(lambda: function(filename), deadline=deadline)
    shutil.rmtree(path, onexc=retry)


def _cleanup_after_error(path, parent, original):
    try:
        _cleanup_owned(path, parent)
    except Exception as cleanup_error:
        # Do not replace a failed commit/checksum/cancellation with an error
        # about removing a locked WAV. Leave the exact staging path in logs.
        original.add_note(f"Temporary directory could not be removed: {path}: {cleanup_error}")
        logging.getLogger(__name__).warning("Could not clean resource staging directory %s", path, exc_info=True)


def _map_paths(value, mapping, video, *, importing=False, key=""):
    if isinstance(value, dict):
        return {k: _map_paths(v, mapping, video, importing=importing, key=k) for k,v in value.items()}
    if isinstance(value, list):
        return [_map_paths(v, mapping, video, importing=importing, key=key) for v in value]
    if isinstance(value, str) and key in PATH_KEYS and value:
        if importing:
            return video if value == "@video" else mapping.get(value.removeprefix("@asset:"), "") if value.startswith("@asset:") else ""
        path = str(Path(value).resolve())
        return "@video" if path == video else "@asset:"+mapping[path] if path in mapping else ""
    return value


def export_resources(snapshot, items, selected, destination, *, progress=None, cancelled=None):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(str(destination))
    selected = set(selected)
    chosen = [i for i in items if i["id"] in selected]
    if not chosen:
        raise ValueError("Select at least one resource")
    project_uuid = str(UUID(str(snapshot["project_uuid"]))) if snapshot.get("project_uuid") else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".capcap-resources-", dir=destination.parent))
    def check():
        if cancelled and cancelled():
            raise ExportCancelled()
    mapping = {str(Path(i["source"]).resolve()): i["relative_path"] for i in chosen if i.get("source")}
    video = str(Path(snapshot.get("video_path", "")).resolve())
    entries = []
    package_id = str(uuid4())
    try:
        for index, item in enumerate(chosen, 1):
            check()
            if progress:
                progress(index-1, len(chosen), item["label"])
            relative = _relative(item["relative_path"])
            target = staging/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.get("source"):
                digest = _copy(Path(item["source"]), target, check)
            else:
                data = item["data"]
                if item.get("payload") is not None:
                    data = json.dumps(_map_paths(item["payload"], mapping, video), ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
                target.write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
            entries.append({k: copy.deepcopy(item[k]) for k in ("id", "group", "label", "relative_path", "role", "metadata") if k in item} |
                           {"size": target.stat().st_size, "sha256": digest})
        cues = [i for i in entries if i["role"] == "tts_clip"]
        if cues:
            table = io.StringIO(newline="")
            writer = csv.writer(table)
            writer.writerow(("cue", "start", "end", "text", "file"))
            for cue in sorted(cues, key=lambda i: int(i["metadata"]["index"])):
                meta = cue["metadata"]
                writer.writerow((meta["index"], meta.get("start", 0), meta.get("end", 0), meta.get("text", ""), Path(cue["relative_path"]).name))
            (staging/"audio/tts/index.csv").write_text(table.getvalue(), encoding="utf-8-sig")
        manifest = {"format": FORMAT, "version": 1, "package_id": package_id, "items": entries}
        if project_uuid:
            manifest["project_uuid"] = project_uuid
        (staging/MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging/"README.txt").write_text("CapCap resources\n\nUse the selected SRT/TXT/audio/media files directly, or copy this entire folder and choose Import CapCap package in CapCap.\nThe source video MUST be included to import this package back into CapCap. Packages without the source video can only be used as exported resources.\nOnly selected resources are included; caches, models and provider credentials are excluded.\n", encoding="utf-8")
        check()
        if progress:
            progress(len(chosen), len(chosen), "Finalizing package…")
        _retry_windows_io(lambda: staging.rename(destination), check)
    except BaseException as original:
        _cleanup_after_error(staging, destination.parent, original)
        raise
    return {"path": str(destination), "files": len(entries), "size": sum(i["size"] for i in entries), "package_id": package_id}


def import_resources(source, workspace, selected=None, *, progress=None, cancelled=None):
    with _import_lock(workspace):
        return _import_resources(source, workspace, selected, progress=progress, cancelled=cancelled)


def _import_resources(source, workspace, selected=None, *, progress=None, cancelled=None):
    """Install selected assets then persist a standard, independently reopenable project."""
    from app.services.project_service import ProjectService
    from app.core.state import ProjectState
    from app.services.segment_service import SegmentService
    source, workspace = Path(source).resolve(), Path(workspace).resolve()
    manifest = read_package(source)
    video_item = require_package_video(manifest, selected)
    existing = find_imported_package(workspace, manifest["package_id"], manifest.get("project_uuid"))
    if existing:
        return existing
    selected = set(selected) if selected is not None else None
    chosen = [i for i in manifest["items"] if selected is None or i["id"] in selected]
    if not chosen:
        raise ValueError("Select at least one resource")
    # A unique source location gives ProjectService its normal path-based ID.
    # Reopening the imported video therefore finds this exact project again.
    media_parent = workspace/"imported_packages"
    media_parent.mkdir(parents=True, exist_ok=True)
    media_final = media_parent/uuid4().hex
    media_stage = Path(tempfile.mkdtemp(prefix=".capcap-import-", dir=media_parent))
    service = ProjectService(str(workspace))
    project_stage = None
    project_final = None
    committed_media = False
    def check():
        if cancelled and cancelled():
            raise ExportCancelled()
    warnings = []
    try:
        for index, item in enumerate(chosen, 1):
            check()
            if progress:
                progress(index-1, len(chosen), item["label"])
            _copy(_file(source, item["relative_path"]), media_stage/item["relative_path"], check, item["sha256"])
        video_relative = video_item["relative_path"]
        video = str(media_final/video_relative)
        project_id = service._build_project_id(video)
        project_final = Path(service.projects_root)/project_id
        if project_final.exists():
            raise FileExistsError(str(project_final))
        project_final.parent.mkdir(parents=True, exist_ok=True)
        project_stage = Path(tempfile.mkdtemp(prefix=".capcap-project-", dir=project_final.parent))
        mapping = {i["relative_path"]: str(media_final/i["relative_path"]) for i in chosen}
        by_role = {}
        for item in chosen:
            by_role.setdefault(item["role"], []).append(item)
        def document(role):
            items = by_role.get(role, [])
            return _map_paths(_read(media_stage/items[0]["relative_path"]), mapping, video, importing=True) if items else None
        editor = document("editor") or {}
        state = ProjectState(project_id=project_id, project_root=str(project_final), input_video=video,
                             input_language=str(editor.get("input_language", "auto")), target_language=str(editor.get("target_language", "vi")),
                             mode=str(editor.get("mode", "subtitle")), settings=_safe_settings(editor.get("settings", {})))
        if manifest.get("project_uuid"):
            state.project_uuid = manifest["project_uuid"]
        state.settings["resource_package_imported"] = True
        state.settings["resource_package_id"] = manifest["package_id"]
        def write_artifact(role, relative, data):
            path = project_stage/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
            state.artifacts[role] = str(project_final/relative)
        source_segments = document("source_json")
        translated = document("translated_json")
        timeline = document("timeline")
        for kind in ("source", "translated"):
            if by_role.get(kind+"_srt"):
                state.artifacts["subtitle_original_srt" if kind == "source" else "subtitle_translated_srt"] = mapping[by_role[kind+"_srt"][0]["relative_path"]]
                segments = parse_srt((media_stage/by_role[kind+"_srt"][0]["relative_path"]).read_text(encoding="utf-8-sig"))
                if kind == "source" and source_segments is None:
                    source_segments = segments
                elif kind == "translated" and translated is None:
                    translated = segments
        if translated is None and timeline:
            translated = []
            for track in timeline.get("tracks", []):
                for layer in track.get("layers", []):
                    if layer.get("type") not in ("subtitle", "dub_subtitle"):
                        continue
                    metadata = layer.get("metadata", {})
                    segment = safe_segment(metadata.get("_seg_dict", {}))
                    segment.update(start=layer.get("start", 0), end=layer.get("end", 0), text=layer.get("text", ""))
                    for key in ("_wav_path", "_audio_end", "speaker"):
                        if key in metadata:
                            segment[key] = metadata[key]
                    translated.append(segment)
        tts = {int(i.get("metadata", {}).get("index", -1)): i for i in by_role.get("tts_clip", [])}
        if translated:
            for index, segment in enumerate(translated, 1):
                if index in tts:
                    segment["_wav_path"] = mapping[tts[index]["relative_path"]]
        converter = SegmentService()
        source_models = converter.transcript_dicts_to_models(source_segments or [])
        if source_segments:
            write_artifact("transcript_raw", "analysis/transcript_raw.json", [safe_segment(s) for s in source_segments])
            write_artifact("transcript_segments", "analysis/transcript_segments.json", [s.to_dict() for s in source_models])
            state.steps["transcribe"] = "done"
            state.settings["transcript_imported"] = True
        if translated:
            models = converter.apply_translations(source_models, [safe_segment(s) for s in translated])
            write_artifact("translation_final", "translation/translation_final.json", [s.to_dict() for s in models])
            state.steps["translate_raw"] = "done"
        if timeline:
            timeline = safe_timeline(timeline)
            for track in timeline["tracks"]:
                kept = []
                for layer in track["layers"]:
                    if layer.get("type") in ("video", "audio", "image", "sticker") and not layer.get("source"):
                        warnings.append("Some media layers were omitted because their files were not selected.")
                        continue
                    kept.append(layer)
                track["layers"] = kept
                valid = {l["id"] for l in kept}
                track["clips"] = [c for c in track["clips"] if c["layer_id"] in valid]
            write_artifact("timeline", "timeline/timeline.json", timeline)
        for role in ("voice_vi", "mixed_vi", "music", "vocals", "extracted_audio", "final_video"):
            if by_role.get(role):
                state.artifacts[role] = mapping[by_role[role][0]["relative_path"]]
        if state.artifacts.get("voice_vi"):
            state.steps["generate_tts"] = "done"
        if state.artifacts.get("mixed_vi"):
            state.steps["mix_audio"] = "done"
        if tts and not state.artifacts.get("voice_vi"):
            warnings.append("Individual TTS clips were restored. Include the full dubbed voice for immediate voice-track playback.")
        (project_stage/"project.json").write_text(json.dumps(state.to_dict(), ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        check()
        if progress:
            progress(len(chosen), len(chosen), "Finalizing package…")
        _retry_windows_io(lambda: media_stage.rename(media_final), check)
        committed_media = True
        _retry_windows_io(lambda: project_stage.rename(project_final), check)
    except BaseException as original:
        if project_stage and project_stage.exists():
            _cleanup_after_error(project_stage, project_final.parent, original)
        _cleanup_after_error(media_final if committed_media else media_stage, media_parent, original)
        raise
    return {"path": str(project_final), "video_path": video, "files": len(chosen), "warnings": list(dict.fromkeys(warnings)),
            "package_id": manifest["package_id"]}
