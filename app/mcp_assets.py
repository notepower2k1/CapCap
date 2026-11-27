"""Read and track media assets for the headless MCP workflows."""

import json
import os
from pathlib import Path
import subprocess

from runtime_paths import bin_path, subprocess_hidden_kwargs


_PROJECT_ARTIFACTS = {
    "original_audio": ("extracted_audio", "audio_extracted"),
    "vocals": ("vocals",),
    "music": ("music",),
    "transcript": ("subtitle_original_srt", "srt_original"),
    "translated_subtitles": ("subtitle_translated_srt", "srt_translated"),
    "dubbed_audio": ("voice_vi",),
    "mixed_audio": ("mixed_vi",),
}
_BITMAP_SUBTITLE_CODECS = {"dvd_subtitle", "dvb_subtitle", "hdmv_pgs_subtitle", "xsub"}


def probe_video(path):
    result = subprocess.run(
        [bin_path("ffmpeg", "ffprobe.exe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=20,
        **subprocess_hidden_kwargs(),
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    if not video:
        raise ValueError("Input does not contain a video stream.")
    duration = data.get("format", {}).get("duration")
    tracks = [
        {
            "index": item["index"], "type": item["codec_type"], "codec": item.get("codec_name"),
            "language": item.get("tags", {}).get("language"),
            **({"can_extract_srt": item.get("codec_name") not in _BITMAP_SUBTITLE_CODECS}
               if item["codec_type"] == "subtitle" else {}),
        }
        for item in streams if item.get("codec_type") in {"audio", "subtitle"}
    ]
    return {
        "video_path": str(path), "duration_seconds": float(duration) if duration not in (None, "N/A") else None,
        "width": video.get("width"), "height": video.get("height"),
        "has_audio": any(track["type"] == "audio" for track in tracks),
        "streams": tracks,
    }


def asset(kind, path, **metadata):
    if not path or not Path(path).is_file() or not Path(path).stat().st_size:
        raise RuntimeError(f"The workflow did not produce {kind}.")
    return {"kind": kind, "path": str(Path(path).resolve()),
            **{key: value for key, value in metadata.items() if value not in (None, "", "auto")}}


def project_assets(project_path, *, origin="project"):
    project_path = Path(project_path)
    data = json.loads(project_path.read_text(encoding="utf-8-sig"))
    settings = data.get("settings", {})
    artifacts = data.get("artifacts", {})
    original_only = settings.get("use_original_subtitles") or data.get("steps", {}).get("translate_raw") == "skipped"
    metadata = {
        "transcript": {"language": data.get("input_language"), "engine": settings.get("transcription_engine")},
        "translated_subtitles": {"language": data.get("target_language"),
                                 "provider": settings.get("translation_provider")},
        "dubbed_audio": {"language": data.get("input_language") if original_only else data.get("target_language")},
    }
    result = []
    for kind, keys in _PROJECT_ARTIFACTS.items():
        if kind == "translated_subtitles" and original_only:
            continue
        for key in keys:
            value = artifacts.get(key)
            if not value:
                continue
            path = Path(value)
            if not path.is_absolute():
                path = project_path.parent / path
            try:
                result.append(asset(kind, path, origin=origin, **metadata.get(kind, {})))
            except (OSError, RuntimeError):
                continue
            break
    return result


def file_stamp(path):
    if not path:
        return None
    try:
        path = Path(path).resolve()
        stat = path.stat()
        if path.is_file():
            return {"path": os.path.normcase(str(path)), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        pass
    return None


def _load_manifest(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _current_stamp(stamp):
    return stamp is not None and file_stamp(stamp["path"]) == stamp


def _valid_manifest(manifest):
    return _current_stamp(manifest.get("source")) and all(
        _current_stamp(stamp) for stamp in manifest.get("inputs", [])
    )


def job_provenance(options, data_dir):
    source = file_stamp(options.get("video_path"))
    inputs = [file_stamp(options[key]) for key in ("srt_path", "audio_path") if options.get(key)]
    dependencies = list(inputs)
    for stamp in inputs:
        if stamp is None:
            continue
        path = Path(stamp["path"])
        for parent in path.parents:
            if parent.parent != data_dir:
                continue
            manifest = _load_manifest(parent / "assets.json")
            if (source is None or source == manifest.get("source")) and _valid_manifest(manifest):
                if any(item["stamp"] == stamp for item in manifest["assets"]):
                    source = manifest["source"]
                    dependencies.extend(manifest["inputs"])
            break
    return {"source": source, "inputs": dependencies}


def publish_assets(directory, provenance, records):
    if not records or not _valid_manifest(provenance):
        return
    manifest = dict(provenance, assets=[
        {"record": record, "stamp": stamp}
        for record in records if (stamp := file_stamp(record["path"])) is not None
    ])
    temporary = directory / "assets.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    temporary.replace(directory / "assets.json")


def inspect_video(path, data_dir):
    from services.project_service import ProjectService
    from runtime_paths import workspace_root

    info = probe_video(path)
    source = file_stamp(path)
    records = {}
    service = ProjectService(workspace_root())
    desktop_project = Path(service.projects_root) / service._build_project_id(path) / "project.json"
    if source and desktop_project.is_file():
        try:
            for record in project_assets(desktop_project):
                stamp = file_stamp(record["path"])
                if stamp and stamp["mtime_ns"] >= source["mtime_ns"]:
                    records[record["path"]] = record
        except (OSError, ValueError):
            pass
    for path in data_dir.glob("*/assets.json"):
        manifest = _load_manifest(path)
        if manifest.get("source") != source or not _valid_manifest(manifest):
            continue
        for item in manifest["assets"]:
            if _current_stamp(item["stamp"]):
                record = dict(item["record"], origin="mcp")
                records[record["path"]] = record
    info["assets"] = list(records.values())
    return info


def list_resources(language=None):
    from language_support import voice_matches_language
    from runtime_paths import workspace_root
    from services.resource_download_service import ResourceDownloadService
    from services.voice_catalog_service import VoiceCatalogService
    from engines.omnivoice_tts import ready as omnivoice_ready

    workspace = workspace_root()
    resources = ResourceDownloadService(workspace)
    models = [
        {key: record[key] for key in ("id", "name", "kind", "status")}
        for record in resources.list_resources()
    ]
    voice_status = {
        "vieneu": "installed" if resources.is_resource_installed("voice:vieneu") else "missing",
        "omnivoice": "installed" if omnivoice_ready() else "missing",
    }
    voices = []
    catalog = VoiceCatalogService(workspace).load_catalog(allow_elevenlabs_network=False)
    catalog = [voice for voice in catalog if voice["provider"] != "piper"]
    catalog.extend(resources._piper_voice_entries())
    for record in catalog:
        if not record.get("enabled", True) or (language and not voice_matches_language(record, language)):
            continue
        provider = record["provider"]
        voice_id = record["id"]
        if provider == "piper":
            voice_id = voice_id.removeprefix("piper:")
            status = "installed" if resources.is_resource_installed(f"voice:{voice_id}") else "missing"
        else:
            status = voice_status.get(provider, "online")
        voices.append({
            "voice_name": voice_id if ":" in voice_id or provider == "edge" else f"{provider}:{voice_id}",
            "name": record.get("name", voice_id), "language": record.get("language"),
            "engine": provider, "status": status,
        })
    return {"models": models, "voices": voices}
