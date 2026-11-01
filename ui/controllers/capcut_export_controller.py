"""Snapshot the live editor on the UI thread; package media in a worker."""
from __future__ import annotations

import copy
import os
import csv
import subprocess
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout

from app.capcut.draft_export import DraftExporter, ExportCancelled, register_local_draft
from runtime_paths import bin_path
from i18n import t


def capture_snapshot(gui):
    video = gui._resolve_preview_original_video_path() or gui.video_path_edit.text().strip()
    if not video or not Path(video).is_file():
        raise ValueError(t("Please choose a video first."))
    model = getattr(getattr(gui, "timeline", None), "_timeline", None)
    if model is None:
        raise ValueError(t("Timeline is not ready."))
    timeline = copy.deepcopy(model.to_dict())
    width, height = gui.preview_controller._resolve_output_canvas_dimensions(video)
    if width and height:
        timeline["composition"].update(width=width, height=height)
    fps_key = gui.get_output_fps_key()
    if fps_key != "source":
        timeline["composition"]["fps"] = float(fps_key)
    voice_state = gui._tts_audio_track_state()
    voice = gui._resolve_preview_voice_only_audio_path()
    existing = gui.using_existing_audio_source()
    if existing:
        voice = gui.resolve_selected_audio_path()
    # Resolve project-relative paths before passing an immutable snapshot to
    # the worker. It must never query QWidget or mutable ProjectState objects.
    root = Path(getattr(getattr(gui, "current_project_state", None), "project_root", "") or os.getcwd())
    def resolve(value):
        value = gui._normalize_local_file_path(str(value or ""))
        if value and not Path(value).is_absolute():
            value = str(root / value)
        return value
    subtitle_style = gui.get_subtitle_export_style(segments=[])
    music_resolved = copy.deepcopy(gui._music_audio_tracks())
    music_by_id = {m.get("id"): m for m in music_resolved}
    music = []
    for track in timeline["tracks"]:
        name = track.get("name", "")
        visibility_flags = {"dub_subtitle": "_subtitle_track_preview_visible",
                            "subtitle": "_subtitle_track_preview_visible",
                            "text": "_text_track_preview_visible", "image": "_logo_track_preview_visible",
                            "mask": "_mask_track_preview_visible"}
        flag = visibility_flags.get(track.get("type"))
        if flag:
            track["visible"] = bool(track.get("visible", True) and getattr(gui, flag, True))
        if track.get("type") == "blur":
            track["visible"] = bool(track.get("visible", True) and gui._blur_effect_enabled())
        meta = track.setdefault("metadata", {})
        meta["_muted"] = bool(gui._is_audio_track_muted(name))
        for layer in track.get("layers", []):
            for key in ("source", "audio_path"):
                if layer.get(key):
                    layer[key] = resolve(layer[key])
            if layer.get("type") == "dub_subtitle":
                metadata = layer.get("metadata", {})
                cue_file = metadata.get("_wav_path") or metadata.get("_seg_dict", {}).get("_wav_path")
                if cue_file:
                    cue_file = resolve(cue_file)
                    if Path(cue_file).is_file():
                        layer["_voice_file"] = cue_file
            if layer.get("type") in ("subtitle", "dub_subtitle"):
                for key in ("font_name", "outline_color", "outline_width", "shadow_color", "shadow_depth", "shadow_alpha",
                            "background_color", "background_alpha", "background_radius", "background_box", "background_width", "background_shape"):
                    layer[key] = copy.deepcopy(subtitle_style.get(key))
                layer["font_bold"] = bool(subtitle_style.get("bold", False))
                layer["background_opacity"] = float(subtitle_style.get("background_alpha", 0))
                # Native text uses horizontal alignment; ASS's 1..9 also
                # includes the vertical anchor, which is already positioned.
                layer["alignment"] = ("left", "center", "right")[(int(subtitle_style.get("alignment", 2))-1) % 3]
                layer["font_size"] = int(gui.subtitle_font_size_spin.value())
                # The live subtitle colour may be selected per speaker; layer
                # defaults can still be white after the UI colour changes.
                segment = layer.get("metadata", {}).get("_seg_dict", {})
                layer["font_color"] = gui._subtitle_color_for_segment(segment).name()
        all_layers = {l["id"]: l for tr in timeline["tracks"] for l in tr.get("layers", []) if l.get("id")}
        for layer in DraftExporter._layers(track, all_layers):
            if layer.get("id") not in music_by_id:
                continue
            item = copy.deepcopy(music_by_id[layer["id"]])
            item.update(path=resolve(layer.get("source", item["path"])),
                        start=float(layer.get("start", item["start"])), end=float(layer.get("end", item["end"])),
                        source_start=float(layer.get("source_start", 0)), speed=float(layer.get("speed", 1)),
                        volume=float(item.get("volume", 100))*float(layer.get("volume", 1))*10**(float(meta.get("_gain_db", 0))/20),
                        layer=copy.deepcopy(layer), track_metadata=copy.deepcopy(meta))
            music.append(item)
    original_muted = gui._is_audio_track_muted("A1 Audio")
    return {
        "timeline": timeline, "source_video": resolve(video),
        "source_canvas": not (width and height), "source_fps": fps_key == "source",
        "original_audio": resolve(gui._resolve_preview_original_audio_path()),
        "original_volume": 0. if original_muted else gui._get_audio_track_volume("A1 Audio") / 100. * 10 ** (gui._get_audio_track_gain_db("A1 Audio") / 20.),
        "voice_audio": resolve(voice), "voice_full_track": existing,
        "voice_volume": 0. if voice_state.get("muted") else float(voice_state.get("volume", 100)) / 100. * 10 ** (gui._get_audio_track_gain_db(voice_state.get("track_name", "TS1")) / 20.),
        "music": music,
        "warps": copy.deepcopy(getattr(gui, "video_time_warps", []) or []),
        "mirror_horizontal": bool(getattr(gui, "_video_mirror_horizontal", False)),
        "framing": {"mode": gui.get_output_scale_mode_key(), "focus": gui.get_output_fill_focus()},
        "subtitle_position": copy.deepcopy(gui.get_subtitle_position_config()),
    }


def _require_capcut_closed():
    if os.name != "nt":
        return
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(t("Could not check CapCut. Choose another export folder."))
    rows = csv.reader(result.stdout.decode("utf-8", "replace").splitlines())
    if any(row and row[0].lower() == "capcut.exe" for row in rows):
        raise RuntimeError(t("Close CapCut before exporting into its drafts folder, or choose another export folder."))


class _ExportWorker(QThread):
    progress = Signal(str)

    def __init__(self, snapshot, destination, parent):
        super().__init__(parent)
        self.snapshot, self.destination = snapshot, destination
        self.result, self.error, self.was_cancelled = None, "", False

    def run(self):
        try:
            if self.snapshot.get("package_source"):
                from app.capcut.draft_package import install_package
                self.result = install_package(self.snapshot["package_source"], self.destination,
                                              progress=self.progress.emit, cancelled=self.isInterruptionRequested)
            else:
                self.result = DraftExporter(
                    ffmpeg=bin_path("ffmpeg", "ffmpeg.exe"), ffprobe=bin_path("ffmpeg", "ffprobe.exe"),
                    progress=self.progress.emit, cancelled=self.isInterruptionRequested,
                ).export(self.snapshot, self.destination)
            if self.snapshot.get("register_draft"):
                try:
                    _require_capcut_closed()
                    register_local_draft(self.destination)
                except Exception as exc:
                    self.result["registration_warning"] = str(exc)
        except ExportCancelled:
            self.was_cancelled = True
        except Exception as exc:
            self.error = str(exc)


class _ExportDialog(QDialog):
    def __init__(self, snapshot, destination, parent):
        super().__init__(parent)
        self.setWindowTitle(t("Import exported CapCut package…") if snapshot.get("package_source") else t("Export CapCut Project (Experimental)"))
        self.setMinimumWidth(460)
        self.label = QLabel(t("Preparing CapCut project…"))
        self.label.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.cancel = QPushButton(t("Cancel"))
        layout = QVBoxLayout(self)
        for widget in (self.label, self.bar, self.cancel):
            layout.addWidget(widget)
        self.worker = _ExportWorker(snapshot, destination, self)
        self.worker.progress.connect(self._show_progress)
        self.worker.finished.connect(self.accept)
        self.cancel.clicked.connect(self.reject)

    def _show_progress(self, message):
        if self.worker.isInterruptionRequested():
            return
        if message.startswith("Preparing media: "):
            message = t("Preparing media: {name}").format(name=message[len("Preparing media: "):])
        elif message == "Preparing freeze frame":
            message = t("Preparing freeze frame")
        self.label.setText(message)

    def reject(self):
        # Keep the modal parent alive until file copies/subprocesses stop and
        # the worker has removed its staging directory. No terminate().
        if self.worker.isRunning():
            self.worker.requestInterruption()
            self.cancel.setEnabled(False)
            self.label.setText(t("Cancelling export…"))
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


def _confirm_capcut_export(gui):
    box = QMessageBox(gui)
    box.setWindowTitle(t("Export CapCut Project (Experimental)"))
    box.setIcon(QMessageBox.Information)
    box.setText(t("Before exporting to CapCut\n\nKept: video, audio/TTS, subtitles, logo and other layers; Fit Voice, framing, mirror, text styles, fades and keyframes.\n\nSkipped: filters/LUT, colour adjustments and preset animations.\n\nMay look different: text size/decorations, blur/mask intensity and keyframe easing. The destination needs the same fonts. Pan is preserved in processed audio."))
    export_button = box.addButton(t("Export"), QMessageBox.AcceptRole)
    cancel_button = box.addButton(t("Cancel"), QMessageBox.RejectRole)
    box.setDefaultButton(export_button)
    box.setEscapeButton(cancel_button)
    box.exec()
    return box.clickedButton() == export_button


def export_capcut_project(gui):
    try:
        snapshot = capture_snapshot(gui)
    except Exception as exc:
        QMessageBox.warning(gui, t("Export CapCut Project (Experimental)"), str(exc))
        return
    if not _confirm_capcut_export(gui):
        return
    default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CapCut/User Data/Projects/com.lveditor.draft"
    parent = QFileDialog.getExistingDirectory(gui, t("Choose CapCut drafts folder or an export folder"), str(default if default.is_dir() else Path.home()))
    if not parent:
        return
    snapshot["register_draft"] = (Path(parent) / "root_meta_info.json").is_file() or Path(parent).name == "com.lveditor.draft"
    if snapshot["register_draft"]:
        try:
            _require_capcut_closed()
        except Exception as exc:
            QMessageBox.warning(gui, t("Export CapCut Project (Experimental)"), str(exc))
            return
    stem = Path(snapshot["source_video"]).stem
    # A short random suffix prevents collisions without ever replacing drafts.
    destination = Path(parent) / f"CapCap_{stem}_{uuid4().hex[:8]}"
    dialog = _ExportDialog(snapshot, destination, gui)
    dialog.worker.start()
    dialog.exec()
    worker = dialog.worker
    if worker.error:
        gui.log(f"[CapCut Export] Failed: {worker.error}")
        QMessageBox.warning(gui, t("Export failed"), worker.error)
    elif worker.result:
        gui.log(f"[CapCut Export] Created: {destination}; tracks={worker.result['tracks']}; media={worker.result['media']}")
        QMessageBox.information(gui, t("Export CapCut Project (Experimental)"),
            t("Project exported to:\n{path}\n\nRestart CapCut to check the draft. Check text styles, audio fades, keyframes and blur areas before continuing to edit. Fonts require the same family on the destination machine. See the export report for approximations.").format(path=destination)
            + ("\n\n" + t("Check blur and mask areas in CapCut. Effect intensity may differ, and native resources may need to be downloaded there.") if worker.result.get("native_blur_count") or worker.result.get("native_mask_count") else "")
            + ("\n\n" + worker.result["registration_warning"] if worker.result.get("registration_warning") else ""))
    dialog.deleteLater()


def import_capcut_package(gui):
    source = QFileDialog.getExistingDirectory(gui, t("Choose the exported CapCut package folder"))
    if not source:
        return
    if not (Path(source) / "capcap_package.json").is_file():
        QMessageBox.warning(gui, t("Import exported CapCut package…"), t("This folder is not a CapCap-exported CapCut package. Export it again using the current version."))
        return
    default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CapCut/User Data/Projects/com.lveditor.draft"
    parent = QFileDialog.getExistingDirectory(gui, t("Choose CapCut drafts folder or an export folder"), str(default if default.is_dir() else Path.home()))
    if not parent:
        return
    register = (Path(parent) / "root_meta_info.json").is_file() or Path(parent).name == "com.lveditor.draft"
    try:
        if register:
            _require_capcut_closed()
    except Exception as exc:
        QMessageBox.warning(gui, t("Import exported CapCut package…"), str(exc))
        return
    destination = Path(parent) / f"{Path(source).name}_import_{uuid4().hex[:8]}"
    dialog = _ExportDialog({"package_source": source, "register_draft": register}, destination, gui)
    dialog.worker.start()
    dialog.exec()
    if dialog.worker.error:
        (getattr(gui, "log", None) or print)(f"[CapCut Import] Failed: {dialog.worker.error}")
        QMessageBox.warning(gui, t("Import exported CapCut package…"), dialog.worker.error)
    elif dialog.worker.result:
        (getattr(gui, "log", None) or print)(f"[CapCut Import] Created: {destination}")
        QMessageBox.information(gui, t("Import exported CapCut package…"),
            t("CapCut project imported to:\n{path}\n\nMedia paths have been updated. Open CapCut to check the new project.").format(path=destination)
            + ("\n\n" + t("Check blur and mask areas in CapCut. Effect intensity may differ, and native resources may need to be downloaded there.") if dialog.worker.result.get("native_blur_count") or dialog.worker.result.get("native_mask_count") else "")
            + ("\n\n" + dialog.worker.result["registration_warning"] if dialog.worker.result.get("registration_warning") else ""))
    dialog.deleteLater()
