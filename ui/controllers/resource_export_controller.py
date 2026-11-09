"""Resource picker and background CapCap package export/import."""
from __future__ import annotations

import copy
import math
import traceback
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QDialog, QLabel, QTreeWidget, QTreeWidgetItem, QPushButton,
                               QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox, QProgressBar, QHeaderView)

from i18n import t
from app.services.resource_package import build_catalog, read_package, export_resources, import_resources, find_imported_package, require_package_video
from app.capcut.draft_export import ExportCancelled

_DIALOG_STYLE = """
    QDialog { background: #101826; color: #dbe5f3; }
    QLabel { color: #dbe5f3; }
    QTreeWidget { background: #121e30; alternate-background-color: #162338;
        color: #dbe5f3; border: 1px solid #2a3b52; border-radius: 8px; padding: 5px; }
    QTreeWidget::item { padding: 5px 2px; }
    QTreeWidget::item:selected { background: #274761; }
    QHeaderView::section { background: #1b2c43; color: #afc7df;
        border: none; padding: 8px; font-weight: 600; }
    QPushButton { background: #20334c; color: #e5edf7; border: 1px solid #36506d;
        border-radius: 7px; padding: 7px 15px; }
    QPushButton:hover { background: #2b4563; }
    QPushButton:disabled { color: #718399; background: #1a273a; }
    QPushButton#primaryActionBtn { background: #4ed0b3; color: #0b1620; border: none; font-weight: 600; }
    QPushButton#primaryActionBtn:hover { background: #66ddc2; }
    QPushButton#primaryActionBtn:disabled { background: #28443f; color: #819b96; }
    QProgressBar { border: 1px solid #36506d; border-radius: 5px; text-align: center; color: #dbe5f3; }
    QProgressBar::chunk { background: #4ed0b3; }
"""


def capture_resources(gui):
    """Read widgets only here; workers receive detached, sanitized data."""
    video = gui._resolve_preview_original_video_path() or gui.video_path_edit.text().strip()
    if not video or not Path(video).is_file():
        raise ValueError(t("Please choose a video first."))
    state = getattr(gui, "current_project_state", None)
    if state is not None:
        # Persist identity before exporting so even the original machine can
        # recognize this project after restarting or receiving a newer pack.
        gui.project_service.save_project(state)
    root = Path(getattr(state, "project_root", "") or Path(video).parent)
    def resolve(path):
        path = gui._normalize_local_file_path(str(path or ""))
        return str(Path(path).resolve() if Path(path).is_absolute() else (root/path).resolve()) if path else ""
    model = getattr(getattr(gui, "timeline", None), "_timeline", None)
    timeline = copy.deepcopy(model.to_dict()) if model else {}
    paths = copy.deepcopy(getattr(state, "artifacts", {}) or {})
    paths.update(getattr(gui, "processed_artifacts", {}) or {})
    fallbacks = {"voice_vi": "last_voice_vi_path", "mixed_vi": "last_mixed_vi_path", "music": "last_music_path",
                 "vocals": "last_vocals_path", "extracted_audio": "last_extracted_audio"}
    for role, attr in fallbacks.items():
        paths[role] = resolve(paths.get(role) or getattr(gui, attr, ""))
    if paths.get("final_video"):
        paths["final_video"] = resolve(paths["final_video"])
    original = copy.deepcopy(getattr(gui, "current_segments", []) or [])
    translated = copy.deepcopy(getattr(gui, "current_translated_segments", []) or [])
    for key, widget, data in (("source", "transcript_text", original), ("translated", "translated_text", translated)):
        if not data and hasattr(gui, widget):
            data.extend(gui.parse_srt_to_segments(getattr(gui, widget).toPlainText()))
    tts = {}
    for index, segment in enumerate(translated, 1):
        path = resolve(segment.get("_wav_path") or segment.get("voice_file") or segment.get("metadata", {}).get("_wav_path"))
        if path and Path(path).is_file():
            tts[index] = {"index": index, "path": path, "start": float(segment.get("start", 0)),
                          "end": float(segment.get("_audio_end", segment.get("end", 0))), "text": segment.get("text", "")}
    for track in timeline.get("tracks", []):
        for layer in track.get("layers", []):
            for key in ("source", "audio_path"):
                if layer.get(key):
                    layer[key] = resolve(layer[key])
            metadata = layer.get("metadata", {})
            segment = metadata.get("_seg_dict", {})
            for part in (metadata, segment):
                if part.get("_wav_path"):
                    part["_wav_path"] = resolve(part["_wav_path"])
            if layer.get("type") != "dub_subtitle":
                continue
            index = int(metadata.get("_seg_index", -1)) + 1
            if index <= 0:
                index = next((i for i,s in enumerate(translated, 1) if abs(float(s.get("start", 0))-float(layer.get("start", 0))) < .001), len(tts)+1)
            path = metadata.get("_wav_path") or segment.get("_wav_path")
            if path and Path(path).is_file():
                tts[index] = {"index": index, "path": path, "start": float(layer.get("start", 0)),
                              "end": float(metadata.get("_audio_end", layer.get("end", 0))), "text": layer.get("text", "")}
    settings = copy.deepcopy(getattr(state, "settings", {}) or {})
    settings.update(subtitle_style_controls=copy.deepcopy(gui._current_subtitle_style_controls_state()),
                    video_time_warps=copy.deepcopy(getattr(gui, "video_time_warps", []) or []),
                    video_mirror_horizontal=bool(getattr(gui, "_video_mirror_horizontal", False)),
                    blur_state={"enabled": bool(gui._blur_effect_enabled()), "regions": []},
                    package_output_controls={"ratio": gui.get_output_ratio_key(), "quality": gui.get_output_quality_key(),
                        "fps": gui.get_output_fps_key(), "scale_mode": gui.get_output_scale_mode_key(), "focus": list(gui.get_output_fill_focus())},
                    audio_handling_mode=gui.get_audio_handling_mode(),
                    preview_track_visibility={"TS1": bool(getattr(gui, "_subtitle_track_preview_visible", True)),
                        "T1 Text": bool(getattr(gui, "_text_track_preview_visible", True)),
                        "L1 Logo": bool(getattr(gui, "_logo_track_preview_visible", True)),
                        "M1": bool(getattr(gui, "_mask_track_preview_visible", True)), "B1": bool(gui._blur_effect_enabled())})
    return {"video_path": resolve(video), "project_uuid": getattr(state, "project_uuid", ""), "source_segments": original, "translated_segments": translated,
            "tts": list(tts.values()), "timeline": timeline, "files": paths, "settings": settings,
            "input_language": gui.get_source_language_code(), "target_language": gui.get_target_language_code(),
            "mode": gui.get_output_mode_key()}


def restore_package_output_controls(gui, state):
    """Restore authored framing on every reopen of an imported package."""
    controls = state.settings.get("package_output_controls", {})
    if not isinstance(controls, dict) or not controls:
        return
    for field, name in (("ratio", "output_ratio_combo"), ("quality", "output_quality_combo"),
                        ("fps", "output_fps_combo"), ("scale_mode", "output_scale_mode_combo")):
        combo = getattr(gui, name, None)
        if combo is not None and field in controls:
            index = combo.findData(controls[field])
            if index >= 0:
                blocked = combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(blocked)
    view = getattr(gui, "video_view", None)
    if view is not None:
        view.set_preview_aspect_ratio(gui.get_output_ratio_key())
        view.set_preview_scale_mode(gui.get_output_scale_mode_key())
        try:
            x, y = (float(v) for v in controls.get("focus", (.5, .5)))
            if math.isfinite(x) and math.isfinite(y):
                view.set_preview_fill_focus(max(0., min(1., x)), max(0., min(1., y)))
        except (ValueError, TypeError):
            pass


def _size(value):
    return f"{value/1024/1024:.1f} MB" if value >= 1024*1024 else f"{value/1024:.1f} KB"


class ResourceSelectionDialog(QDialog):
    def __init__(self, items, parent=None, *, importing=False):
        super().__init__(parent)
        self.setStyleSheet(_DIALOG_STYLE)
        self.setWindowTitle(t("Import CapCap package…") if importing else t("Export Resources…"))
        self.resize(740, 560)
        self.setMinimumSize(580, 400)
        self.items = items
        self.importing = importing
        self.required_ids = {i["id"] for i in items if importing and i.get("role") == "source_video"}
        layout = QVBoxLayout(self)
        hint = QLabel(t("The source video is required. Select the resources to restore into a new project.") if importing else
                      t("Select files individually or by group. Include the source video and all editing data to move a complete project."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        tools = QHBoxLayout()
        self.select_all = QPushButton(t("Select all resources"))
        self.clear_all = QPushButton(t("Clear selection"))
        tools.addWidget(self.select_all)
        tools.addWidget(self.clear_all)
        tools.addStretch()
        layout.addLayout(tools)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([t("Resource"), t("Size")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.leaves, groups = {}, {}
        if not importing:
            for group in ("Original transcript", "Translated subtitles", "Individual TTS clips",
                          "Audio and rendered video", "Media", "Project editing data"):
                node = QTreeWidgetItem(self.tree, [t(group)])
                node.setFlags(node.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                groups[group] = node
        for item in items:
            group = item["group"]
            if group not in groups:
                node = QTreeWidgetItem(self.tree, [t(group)])
                node.setFlags(node.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                groups[group] = node
            leaf = QTreeWidgetItem(groups[group], [t(item["label"]), _size(item["size"])])
            leaf.setToolTip(0, item["relative_path"])
            leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
            leaf.setCheckState(0, Qt.Checked if importing or item.get("default_checked", True) else Qt.Unchecked)
            if item["id"] in self.required_ids:
                leaf.setFlags(leaf.flags() & ~Qt.ItemIsUserCheckable)
                leaf.setToolTip(0, t("Source video is required to import this package."))
            self.leaves[item["id"]] = leaf
        for group, node in groups.items():
            if not node.childCount():
                QTreeWidgetItem(node, [t("No resources available yet.")]).setFlags(Qt.NoItemFlags)
                node.setFlags(Qt.NoItemFlags)
            else:
                count = node.childCount()
                node.setText(0, f"{t(group)} ({count})")
                node.setText(1, _size(sum(i["size"] for i in items if i["group"] == group)))
            font = node.font(0)
            font.setBold(True)
            node.setFont(0, font)
        self.tree.expandAll()
        if "Individual TTS clips" in groups and groups["Individual TTS clips"].childCount() > 12:
            groups["Individual TTS clips"].setExpanded(False)
        layout.addWidget(self.tree, 1)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.video_note = QLabel(t("To import this package back into CapCap, you must select the source video when exporting."))
        self.video_note.setWordWrap(True)
        self.video_note.setStyleSheet("color: #f0c878;")
        layout.addWidget(self.video_note)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(t("Cancel"))
        self.submit = QPushButton(t("Import") if importing else t("Export"))
        self.submit.setObjectName("primaryActionBtn")
        self.submit.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(self.submit)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        self.submit.clicked.connect(self.accept)
        self.select_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        self.clear_all.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        self.tree.itemChanged.connect(self._update_summary)
        self._update_summary()

    def selected_ids(self):
        return [key for key,leaf in self.leaves.items() if leaf.checkState(0) == Qt.Checked]

    def _set_all(self, state):
        self.tree.blockSignals(True)
        for index in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(index).setCheckState(0, state)
        for key in self.required_ids:
            self.leaves[key].setCheckState(0, Qt.Checked)
        self.tree.blockSignals(False)
        self._update_summary()

    def _update_summary(self, *_):
        selected = set(self.selected_ids())
        size = sum(i["size"] for i in self.items if i["id"] in selected)
        self.summary.setText(t("Selected: {count} files · approximately {size}").format(count=len(selected), size=_size(size)))
        self.submit.setEnabled(bool(selected))
        self.video_note.setVisible(not self.importing and not any(i.get("role") == "source_video" and i["id"] in selected for i in self.items))


class _ResourceWorker(QThread):
    progress = Signal(int, int, str)
    def __init__(self, operation, arguments, parent=None):
        super().__init__(parent)
        self.operation, self.arguments = operation, arguments
        self.result, self.error, self.error_detail, self.was_cancelled = None, "", "", False

    def run(self):
        try:
            action = import_resources if self.operation == "import" else export_resources
            self.result = action(**self.arguments, progress=self.progress.emit, cancelled=self.isInterruptionRequested)
        except ExportCancelled:
            self.was_cancelled = True
        except Exception as exc:
            self.error = str(exc)
            self.error_detail = traceback.format_exc()


class _ResourceProgressDialog(QDialog):
    def __init__(self, operation, arguments, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_DIALOG_STYLE)
        self.setWindowTitle(t("Import CapCap package…") if operation == "import" else t("Export Resources…"))
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)
        self.label = QLabel(t("Preparing resources…"))
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.PlainText)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.cancel = QPushButton(t("Cancel"))
        for widget in (self.label, self.bar, self.cancel):
            layout.addWidget(widget)
        self.worker = _ResourceWorker(operation, arguments, self)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self.accept)
        self.cancel.clicked.connect(self.reject)

    def _progress(self, current, total, name):
        if self.worker.isInterruptionRequested():
            return
        self.label.setText(t(name) if name == "Finalizing package…" else t("Copying: {name}").format(name=t(name)))
        self.bar.setRange(0, total)
        self.bar.setValue(current)

    def reject(self):
        if self.worker.isRunning():
            self.worker.requestInterruption()
            self.cancel.setEnabled(False)
            self.label.setText(t("Cancelling resource transfer…"))
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


def _run(gui, operation, arguments):
    dialog = _ResourceProgressDialog(operation, arguments, gui)
    dialog.worker.start()
    dialog.exec()
    result, error = dialog.worker.result, dialog.worker.error
    if error:
        _log(gui, f"[Resources] {operation} failed: {dialog.worker.error_detail or error}")
        QMessageBox.warning(gui, t("Import CapCap package…") if operation == "import" else t("Export Resources…"), t(error))
    dialog.deleteLater()
    return result


def _log(parent, message):
    logger = getattr(parent, "log", None)
    (logger if callable(logger) else print)(message)


def _available(gui):
    active = bool(getattr(gui, "_pipeline_active", False))
    for name in ("extraction_thread", "vocal_thread", "voice_thread", "transcription_thread", "translation_thread",
                 "rewrite_translation_thread", "prepare_workflow_thread", "export_thread", "_alternate_range_transcription_worker"):
        worker = getattr(gui, name, None)
        try:
            active = active or bool(worker is not None and worker.isRunning())
        except RuntimeError:
            pass  # a retired Qt wrapper
    for worker in (getattr(gui, "_segment_preview_threads", {}) or {}).values():
        try:
            active = active or worker.isRunning()
        except RuntimeError:
            pass
    if active:
        QMessageBox.information(gui, t("Resources"), t("Please wait for the current process to finish."))
        return False
    return True


def export_other_resources(gui):
    if not _available(gui):
        return
    try:
        snapshot = capture_resources(gui)
        items = build_catalog(snapshot)
        selector = ResourceSelectionDialog(items, gui)
        if selector.exec() != QDialog.Accepted:
            return
        parent = QFileDialog.getExistingDirectory(gui, t("Choose resource export folder"))
        if not parent:
            return
        destination = Path(parent)/f"CapCap_resources_{Path(snapshot['video_path']).stem}_{uuid4().hex[:8]}"
        result = _run(gui, "export", dict(snapshot=snapshot, items=items, selected=selector.selected_ids(), destination=str(destination)))
        if result:
            gui.log(f"[Resources] Exported {result['files']} files: {result['path']}")
            QMessageBox.information(gui, t("Export Resources…"), t("Exported {count} files to:\n{path}\n\nUse the files directly or import this entire folder as a CapCap package.").format(count=result["files"], path=result["path"]))
    except Exception as exc:
        QMessageBox.warning(gui, t("Export Resources…"), t(str(exc)))


def choose_and_import_capcap_package(gui, workspace, before_import=None):
    """Shared by launcher and editor; no active video/project is required."""
    source = QFileDialog.getExistingDirectory(gui, t("Choose CapCap package folder"))
    if not source:
        return
    try:
        manifest = read_package(source)
        require_package_video(manifest)
        existing = find_imported_package(workspace, manifest["package_id"], manifest.get("project_uuid"))
        if existing:
            return _open_existing_package(gui, existing, before_import)
        selector = ResourceSelectionDialog(manifest["items"], gui, importing=True)
        if selector.exec() != QDialog.Accepted:
            return
        selected = selector.selected_ids()
        require_package_video(manifest, selected)
        if before_import is not None:
            before_import()
        result = _run(gui, "import", dict(source=source, workspace=workspace, selected=selected))
        return _open_existing_package(gui, result) if result and result.get("already_imported") else result
    except Exception as exc:
        QMessageBox.warning(gui, t("Import CapCap package…"), t(str(exc)))
    return None


def _open_existing_package(parent, result, before_open=None):
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    project_match = result.get("duplicate_kind") == "project"
    box.setWindowTitle(t("Project already exists") if project_match else t("Package already imported"))
    box.setText(t("This project already exists on this computer. Open the existing project? Its content will not be updated from this package.") if project_match else t("This package has already been imported on this computer. Open the existing project?"))
    open_button = box.addButton(t("Open existing project"), QMessageBox.AcceptRole)
    cancel_button = box.addButton(t("Cancel"), QMessageBox.RejectRole)
    box.setDefaultButton(open_button)
    box.setEscapeButton(cancel_button)
    box.exec()
    if box.clickedButton() != open_button:
        return None
    if before_open is not None:
        before_open()
    return result


def restore_imported_project_languages(gui, state):
    for name, value in (("lang_whisper_combo", state.input_language), ("lang_target_combo", state.target_language),
                        ("audio_handling_combo", state.settings.get("audio_handling_mode", "fast"))):
        combo = getattr(gui, name, None)
        if combo is not None:
            index = combo.findData(value)
            if index >= 0:
                was_blocked = combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(was_blocked)


def import_capcap_package(gui):
    if not _available(gui):
        return
    result = choose_and_import_capcap_package(gui, gui.project_service.workspace_root, gui.persist_current_timeline_project_data)
    try:
        if result:
            state = gui.project_service.load_project(str(Path(result["path"])/"project.json"))
            restore_imported_project_languages(gui, state)
            gui.load_video_project(result["video_path"])
            gui.log(f"[Resources] Imported project: {result['path']}")
            message = t("Opened the existing project.") if result.get("already_imported") else t("CapCap package imported as a new project. Your previous project is unchanged.")
            if result["warnings"]:
                message += "\n\n"+"\n".join(t(w) for w in result["warnings"])
            QMessageBox.information(gui, t("Import CapCap package…"), message)
    except Exception as exc:
        QMessageBox.warning(gui, t("Import CapCap package…"), t(str(exc)))
