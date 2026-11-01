"""Install a CapCap-exported folder at a new location with local media links.

Only manifest-listed media and known draft documents are imported. Original
packages are never modified; import always creates a separate project.
"""
import copy
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
from uuid import uuid4

from app.capcut.draft_export import ExportCancelled
from app.capcut.draft_fonts import apply_font


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _local(root, relative):
    relative = str(relative).replace("\\", "/")
    part = PurePosixPath(relative)
    if not relative or part.is_absolute() or any(p in ("..", "") for p in part.parts) or ":" in relative:
        raise ValueError("Invalid media path in CapCut package")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError(f"Missing or unsafe package file: {relative}")
    return candidate


def install_package(source, destination, *, progress=None, cancelled=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    progress, cancelled = progress or (lambda _: None), cancelled or (lambda: False)
    def read(relative):
        return _read(_local(source, relative))
    manifest = read("capcap_package.json")
    if manifest.get("format") != "capcap-capcut-package" or manifest.get("version") != 1:
        raise ValueError("Unsupported CapCut package format")
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError("Choose a new destination outside the source package")
    assets = {item["material_id"]: item for item in manifest.get("media", [])}
    if len(assets) != len(manifest.get("media", [])):
        raise ValueError("Duplicate material IDs in CapCut package")
    # Prefer the active timeline; CapCut may have refreshed it after a save.
    project_path = source / "Timelines/project.json"
    project = read("Timelines/project.json") if project_path.is_file() else {}
    if len([x for x in project.get("timelines", []) if not x.get("is_marked_delete")]) > 1:
        raise ValueError("This package contains multiple timelines; export a new single-timeline package")
    active = str(project.get("main_timeline_id", ""))
    if active and any(ch not in "0123456789abcdefABCDEF-" for ch in active):
        raise ValueError("Invalid timeline ID in package")
    draft_file = source / f"Timelines/{active}/draft_content.json" if active else source / "draft_content.json"
    if not draft_file.is_file():
        draft_file = source / "draft_content.json"
    draft = copy.deepcopy(read(draft_file.relative_to(source).as_posix()))
    font_families = {item["material_id"]: str(item["family"]) for item in manifest.get("fonts", [])}
    font_warnings = []
    for material in draft.get("materials", {}).get("texts", []):
        family = font_families.get(material.get("id"))
        if not family:
            continue
        content = json.loads(material["content"])
        for style in content.get("styles", []):
            # Respect a different CapCut resource font chosen after export.
            if style.get("font", {}).get("id"):
                continue
            if not apply_font(material, style, family):
                warning = f"Font unavailable on this machine; select it in CapCut: {family}"
                if warning not in font_warnings:
                    font_warnings.append(warning)
        material["content"] = json.dumps(content, ensure_ascii=False)
    native_blur_count = 0
    for effect in draft.get("materials", {}).get("video_effects", []):
        if effect.get("resource_id") in ("6739752823140913675", "6770564289074827784"):
            native_blur_count += 1
            # CapCut may have cached this effect after opening the draft.
            # Keep its portable resource ID, not another machine's cache path.
            for key in ("path", "request_id", "algorithm_artifact_path"):
                effect.pop(key, None)
    native_mask_count = 0
    for group in ("common_mask", "masks"):
        for mask in draft.get("materials", {}).get(group, []):
            if mask.get("resource_id") == "7374021450748924432":
                native_mask_count += 1
                for key in ("path", "loader_work_space", "contour_path"):
                    mask.pop(key, None)
    needed = {}
    for group in ("videos", "audios"):
        for material in draft.get("materials", {}).get(group, []):
            item = assets.get(material.get("id"))
            if item is None:
                raise ValueError("Package media changed after export; export a new package before importing")
            relative = item["relative_path"]
            path = _local(source, relative)
            if not str(relative).replace("\\", "/").startswith("media/"):
                raise ValueError("Package media must be inside its media folder")
            needed[relative] = path
            material["path"] = (destination / relative).as_posix()
            # Do not leave alternative absolute references to the old machine.
            for key in ("media_path", "reverse_path", "intensifies_path", "reverse_intensifies_path"):
                if material.get(key):
                    raise ValueError("Package contains an unsupported external media reference")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".capcap-import-", dir=destination.parent))
    def check():
        if cancelled():
            raise ExportCancelled()
    def write(name, data):
        path = staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    try:
        for relative, path in needed.items():
            check()
            progress(f"Preparing media: {path.name}")
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as src, target.open("wb") as dst:
                while block := src.read(4 * 1024 * 1024):
                    check()
                    dst.write(block)
        now, draft_id, timeline_id = time.time_ns() // 1000, str(uuid4()).upper(), str(uuid4()).upper()
        draft.update(id=timeline_id, name=destination.name, create_time=now, update_time=now)
        meta = {"draft_id": draft_id, "draft_name": destination.name,
                "draft_fold_path": destination.as_posix(), "draft_root_path": destination.parent.as_posix(),
                "draft_materials": [], "draft_cover": "", "tm_draft_create": now, "tm_draft_modified": now,
                "tm_draft_removed": 0, "tm_duration": draft["duration"], "draft_is_invisible": False,
                "draft_is_cloud_temp_draft": False}
        project = {"id": draft_id, "main_timeline_id": timeline_id, "version": 0, "create_time": now, "update_time": now,
                   "timelines": [{"id": timeline_id, "name": "Timeline 01", "create_time": now, "update_time": now,
                                  "is_marked_delete": False}], "config": {"color_space": -1, "use_float_render": False}}
        for name in ("draft_content.json", "draft_content.json.bak", "template-2.tmp",
                     f"Timelines/{timeline_id}/draft_content.json", f"Timelines/{timeline_id}/draft_content.json.bak",
                     f"Timelines/{timeline_id}/template.tmp", f"Timelines/{timeline_id}/template-2.tmp"):
            write(name, draft)
        write("draft_meta_info.json", meta)
        for name in ("Timelines/project.json", "Timelines/project.json.bak"):
            write(name, project)
        write("capcap_package.json", manifest)
        report = read("capcap_export_report.json") if (source / "capcap_export_report.json").is_file() else {}
        report["imported_package"] = True
        report["warnings"] = list(dict.fromkeys(report.get("warnings", []) + font_warnings))
        write("capcap_export_report.json", report)
        if (source / "capcap_layers.json").is_file():
            write("capcap_layers.json", read("capcap_layers.json"))
        for name in ("subtitles.srt", "README.txt"):
            path = source / name
            if path.is_file() and not path.is_symlink():
                shutil.copyfile(path, staging / name)
        check()
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return {"path": str(destination), "warnings": report.get("warnings", []),
            "tracks": len(draft.get("tracks", [])), "media": len(needed), "native_blur_count": native_blur_count,
            "native_mask_count": native_mask_count}
