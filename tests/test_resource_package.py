import copy
import hashlib
import json
import os
import threading
import time
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"ui"), str(ROOT/"app"), str(ROOT)]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services.resource_package import build_catalog, read_package, export_resources, import_resources, MANIFEST
from app.capcut.draft_export import ExportCancelled
from app.services.project_service import ProjectService
from app.services.gui_project_bridge import GUIProjectBridge
from app.services import resource_package as packages


class ResourcePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root/"original.mp4"
        self.voice = self.root/"voice.wav"
        self.logo = self.root/"logo.png"
        self.video.write_bytes(b"original media")
        self.voice.write_bytes(b"individual and full voice")
        self.logo.write_bytes(b"logo")
        self.snapshot = {
            "video_path": str(self.video), "input_language": "zh", "target_language": "ja", "mode": "both",
            "source_segments": [{"start": 0., "end": 1., "text": "Source", "api_key": "NEVER_EXPORT"}],
            "translated_segments": [{"start": 0., "end": 1., "text": "Translated", "speaker": "SPEAKER_00", "_wav_path": str(self.voice)}],
            "tts": [{"index": 1, "path": str(self.voice), "start": 0., "end": 1., "text": "Translated"}],
            "files": {"voice_vi": str(self.voice)},
            "settings": {"api_key": "NEVER_EXPORT", "provider": {"token": "NEVER_EXPORT"},
                         "video_time_warps": [{"type": "freeze", "time": .5, "duration": .2}],
                         "subtitle_style_controls": {"font": "Arial", "secret": "NEVER_EXPORT"}},
            "timeline": {"duration": 1.2, "composition": {"width": 1080, "height": 1920}, "tracks": [
                {"id": "v1", "name": "V1 Video", "type": "video", "layers": [{"id": "source", "type": "video", "source": str(self.video), "start": 0., "end": 1.2}]},
                {"id": "logo", "name": "L1 Logo", "type": "image", "metadata": {"_volume": 50, "api_key": "NEVER_EXPORT"},
                 "layers": [{"id": "image", "type": "image", "source": str(self.logo), "start": 0., "end": 1.2,
                             "metadata": {"api_key": "NEVER_EXPORT"}}]},
            ]}}
        self.items = build_catalog(self.snapshot)

    def export(self, selected=None):
        destination = self.root/("package_"+next(tempfile._get_candidate_names()))
        export_resources(self.snapshot, self.items, selected or [i["id"] for i in self.items], destination)
        return destination

    def test_complete_roundtrip_relinks_artifacts_layers_and_cue_files(self):
        before = copy.deepcopy(self.snapshot)
        package = self.export()
        for file in package.rglob("*.json"):
            text = file.read_text(encoding="utf-8")
            self.assertNotIn("NEVER_EXPORT", text)
            self.assertNotIn(str(self.root).replace("\\", "\\\\"), text)
        manifest = read_package(package)
        self.assertIn("Translated", (package/"audio/tts/index.csv").read_text(encoding="utf-8-sig"))
        self.assertEqual(len(manifest["items"]), len(self.items))
        workspace = self.root/"new_machine"
        result = import_resources(package, workspace)
        service = ProjectService(str(workspace))
        state = service.load_project(str(Path(result["path"])/"project.json"))
        self.assertEqual((state.input_language, state.target_language), ("zh", "ja"))
        self.assertEqual(state.project_id, service._build_project_id(result["video_path"]))
        self.assertTrue(all(Path(p).is_file() for p in state.artifacts.values()))
        self.assertEqual(Path(state.input_video).read_bytes(), self.video.read_bytes())
        timeline = json.loads(Path(state.artifacts["timeline"]).read_text())
        self.assertEqual(timeline["tracks"][0]["layers"][0]["source"], state.input_video)
        self.assertTrue(Path(timeline["tracks"][1]["layers"][0]["source"]).is_file())
        context = GUIProjectBridge(service).load_context(state)
        self.assertEqual(context["current_translated_segments"][0]["text"], "Translated")
        self.assertTrue(Path(context["current_translated_segments"][0]["_wav_path"]).is_file())
        self.assertEqual(context["current_translated_segments"][0]["speaker"], "SPEAKER_00")
        reopened = service.ensure_project(state.input_video, input_language="zh", target_language="ja")
        self.assertEqual(reopened.artifacts, state.artifacts)
        second = import_resources(package, workspace)
        self.assertEqual(second["path"], result["path"])
        self.assertTrue(second["already_imported"])
        self.assertEqual(len(list((workspace/"imported_packages").iterdir())), 1)
        self.assertEqual(before, self.snapshot)

    def test_partial_srt_import_with_packaged_video_never_overwrites_existing_project(self):
        package = self.export(["translated_srt", "source_video"])
        service = ProjectService(str(self.root/"workspace"))
        original = service.ensure_project(str(self.video))
        original_json = Path(service.project_file(original.project_root)).read_bytes()
        result = import_resources(package, self.root/"workspace")
        state = service.load_project(str(Path(result["path"])/"project.json"))
        context = GUIProjectBridge(service).load_context(state)
        self.assertEqual(context["current_translated_segments"][0]["text"], "Translated")
        self.assertFalse(context["current_segments"])
        self.assertNotEqual(state.input_video, str(self.video))
        self.assertEqual(Path(service.project_file(original.project_root)).read_bytes(), original_json)

    def test_missing_or_unselected_source_video_is_rejected_before_copy(self):
        package = self.export(["translated_srt"])
        with self.assertRaisesRegex(ValueError, "missing the source video"):
            import_resources(package, self.root/"workspace")
        self.assertFalse((self.root/"workspace/imported_packages").exists())
        complete = self.export()
        with self.assertRaisesRegex(ValueError, "must be selected"):
            import_resources(complete, self.root/"workspace", selected=["translated_srt"])
        manifest = read_package(complete)
        video = next(i for i in manifest["items"] if i["role"] == "source_video")
        (complete/video["relative_path"]).unlink()
        with self.assertRaisesRegex(ValueError, "missing the source video"):
            read_package(complete)

    def test_unselected_layer_media_are_removed_without_old_path_fallback(self):
        package = self.export(["timeline", "editor", "source_video"])
        result = import_resources(package, self.root/"workspace")
        state = ProjectService(str(self.root/"workspace")).load_project(str(Path(result["path"])/"project.json"))
        timeline = json.loads(Path(state.artifacts["timeline"]).read_text())
        self.assertFalse(timeline["tracks"][1]["layers"])
        self.assertTrue(result["warnings"])

    def test_timeline_only_keeps_subtitle_content_when_reopened(self):
        self.snapshot["timeline"]["tracks"].append({"name": "TS1", "type": "dub_subtitle", "layers": [
            {"id": "cue", "type": "dub_subtitle", "start": .2, "end": 1., "text": "Live timeline text"}]})
        self.items = build_catalog(self.snapshot)
        package = self.export(["timeline", "source_video"])
        result = import_resources(package, self.root/"workspace")
        service = ProjectService(str(self.root/"workspace"))
        state = service.load_project(str(Path(result["path"])/"project.json"))
        context = GUIProjectBridge(service).load_context(state)
        self.assertEqual(context["current_translated_segments"][0]["text"], "Live timeline text")

    def test_checksums_and_unsafe_paths_are_rejected_and_import_is_cleaned(self):
        package = self.export()
        manifest = read_package(package)
        item = manifest["items"][0]
        path = package/item["relative_path"]
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1])+original[1:])
        with self.assertRaisesRegex(ValueError, "checksum"):
            import_resources(package, self.root/"workspace")
        self.assertFalse(list((self.root/"workspace/imported_packages").iterdir()))
        path.write_bytes(original)
        manifest["items"][0]["relative_path"] = "../original.mp4"
        (package/MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_package(package)

    def test_cancel_export_and_import_remove_only_their_staging_directories(self):
        destination = self.root/"cancelled"
        with self.assertRaises(ExportCancelled):
            export_resources(self.snapshot, self.items, [i["id"] for i in self.items], destination, cancelled=lambda: True)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".capcap-resources-*")))
        package = self.export()
        with self.assertRaises(ExportCancelled):
            import_resources(package, self.root/"workspace", cancelled=lambda: True)
        self.assertFalse(list((self.root/"workspace/imported_packages").iterdir()))
        self.assertTrue(self.video.is_file())
        self.assertTrue(package.is_dir())

    def test_existing_destination_and_other_package_formats_are_rejected(self):
        package = self.export()
        with self.assertRaises(FileExistsError):
            export_resources(self.snapshot, self.items, ["source_srt"], package)
        manifest = read_package(package)
        manifest["format"] = "capcap-capcut-package"
        (package/MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_package(package)

    def test_package_id_survives_folder_rename_and_new_exports_get_new_ids(self):
        package = self.export()
        package_id = read_package(package)["package_id"]
        self.assertNotEqual(read_package(self.export())["package_id"], package_id)
        result = import_resources(package, self.root/"workspace")
        renamed = self.root/"renamed-package"
        package.rename(renamed)
        second = import_resources(renamed, self.root/"workspace")
        self.assertEqual(second["path"], result["path"])
        self.assertEqual(second["package_id"], package_id)

    def test_new_project_identity_is_saved_immediately_and_survives_reopen(self):
        service = ProjectService(str(self.root/"workspace"))
        state = service.ensure_project(str(self.video))
        raw = json.loads(Path(service.project_file(state.project_root)).read_text())
        self.assertEqual(raw["project_uuid"], state.project_uuid)
        self.assertTrue(state.project_uuid)
        self.assertEqual(service.ensure_project(str(self.video)).project_uuid, state.project_uuid)
        self.assertEqual(service.load_project(service.project_file(state.project_root)).project_uuid, state.project_uuid)

    def test_original_project_is_detected_even_for_a_new_package_id(self):
        workspace = self.root/"machine_a"
        service = ProjectService(str(workspace))
        original = service.ensure_project(str(self.video))
        before = Path(service.project_file(original.project_root)).read_bytes()
        self.snapshot["project_uuid"] = original.project_uuid
        self.items = build_catalog(self.snapshot)
        first, second = self.export(), self.export()
        self.assertNotEqual(read_package(first)["package_id"], read_package(second)["package_id"])
        for package in (first, second):
            result = import_resources(package, workspace)
            self.assertEqual(result["path"], original.project_root)
            self.assertEqual(result["duplicate_kind"], "project")
        self.assertFalse((workspace/"imported_packages").exists())
        self.assertEqual(Path(service.project_file(original.project_root)).read_bytes(), before)
        imported = import_resources(first, self.root/"machine_b")
        state = ProjectService(str(self.root/"machine_b")).load_project(str(Path(imported["path"])/"project.json"))
        self.assertEqual(state.project_uuid, original.project_uuid)
        self.assertEqual(import_resources(second, self.root/"machine_b")["path"], imported["path"])

    def test_old_project_identity_is_migrated_stably_without_renaming_folder(self):
        service = ProjectService(str(self.root/"workspace"))
        state = service.ensure_project(str(self.video))
        path = Path(service.project_file(state.project_root))
        raw = json.loads(path.read_text())
        raw.pop("project_uuid")
        path.write_text(json.dumps(raw), encoding="utf-8")
        first, second = service.load_project(str(path)), service.load_project(str(path))
        self.assertEqual(first.project_uuid, second.project_uuid)
        saved = service.ensure_project(str(self.video))
        self.assertEqual(saved.project_uuid, first.project_uuid)
        self.assertEqual(saved.project_id, state.project_id)
        self.assertEqual(saved.project_root, state.project_root)
        self.assertEqual(json.loads(path.read_text())["project_uuid"], first.project_uuid)

    def test_legacy_package_identity_and_missing_project_allow_reimport(self):
        package = self.export()
        manifest = read_package(package)
        manifest.pop("package_id")
        (package/MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        identity = read_package(package)["package_id"]
        self.assertTrue(identity.startswith("legacy-"))
        result = import_resources(package, self.root/"workspace")
        self.assertTrue(import_resources(package, self.root/"workspace")["already_imported"])
        (Path(result["path"])/"project.json").unlink()
        restored = import_resources(package, self.root/"workspace")
        self.assertNotEqual(result["path"], restored["path"])
        self.assertEqual(restored["package_id"], identity)

    def test_parallel_import_lock_prevents_a_second_import(self):
        package = self.export()
        with packages._import_lock(self.root/"workspace"):
            with self.assertRaisesRegex(ValueError, "Another CapCap package import"):
                import_resources(package, self.root/"workspace")

    def test_cancel_during_large_file_copy_removes_partial_media(self):
        self.video.write_bytes(b"x"*(9*1024*1024))
        self.items = build_catalog(self.snapshot)
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 3
        destination = self.root/"partial"
        with self.assertRaises(ExportCancelled):
            export_resources(self.snapshot, self.items, ["source_video"], destination, cancelled=cancelled)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".capcap-resources-*")))
        package = self.export(["source_video"])
        calls = 0
        with self.assertRaises(ExportCancelled):
            import_resources(package, self.root/"workspace", cancelled=cancelled)
        self.assertFalse(list((self.root/"workspace/imported_packages").iterdir()))
        self.assertEqual(self.video.stat().st_size, 9*1024*1024)

    def test_transient_windows_sharing_error_retries_commit(self):
        rename = Path.rename
        calls = []
        def locked_once(path, target):
            if path.name.startswith(".capcap-resources-"):
                calls.append(path)
                if len(calls) == 1:
                    error = OSError("temporarily locked")
                    error.winerror = 32
                    raise error
            return rename(path, target)
        with patch.object(Path, "rename", locked_once):
            package = self.export()
        self.assertEqual(len(calls), 2)
        self.assertTrue((package/MANIFEST).is_file())
        self.assertEqual(len(read_package(package)["items"]), len(self.items))

    def test_cleanup_failure_does_not_replace_original_error(self):
        error = OSError("locked WAV during cleanup")
        error.winerror = 32
        with patch.object(packages, "_copy", side_effect=ValueError("original copy failure")), \
                patch.object(packages, "_cleanup_owned", side_effect=error), \
                self.assertLogs(packages.__name__, level="WARNING"), \
                self.assertRaisesRegex(ValueError, "original copy failure") as caught:
            self.export(["source_video"])
        self.assertTrue(any("Temporary directory" in note for note in caught.exception.__notes__))

    def test_cancel_remains_responsive_during_windows_retry(self):
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ExportCancelled()
        error = OSError("sharing violation")
        error.winerror = 32
        with self.assertRaises(ExportCancelled):
            packages._retry_windows_io(lambda: (_ for _ in ()).throw(error), check)

    @unittest.skipUnless(os.name == "nt", "Windows file sharing semantics")
    def test_real_windows_file_lock_during_copy_and_cleanup(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        def hold(path):
            # READ sharing only: another reader is allowed, writes/deletes
            # are blocked just as with a scanner holding a fresh WAV.
            flags = 0x02000000 if path.is_dir() else 0x80
            handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, flags, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            def release():
                time.sleep(.15)
                kernel.CloseHandle(handle)
            thread = threading.Thread(target=release)
            thread.start()
            return thread
        directory = self.root/"owned-staging"
        directory.mkdir()
        target = directory/"0011.wav"
        target.write_bytes(b"previous content")
        thread = hold(target)
        try:
            digest = packages._copy(self.voice, target, lambda: None)
        finally:
            thread.join()
        self.assertEqual(digest, hashlib.sha256(self.voice.read_bytes()).hexdigest())
        self.assertEqual(target.read_bytes(), self.voice.read_bytes())
        thread = hold(target)
        try:
            packages._cleanup_owned(directory, self.root)
        finally:
            thread.join()
        self.assertFalse(directory.exists())
        directory.mkdir()
        thread = hold(directory)
        renamed = self.root/"completed-package"
        attempts = 0
        def commit():
            nonlocal attempts
            attempts += 1
            directory.rename(renamed)
        try:
            packages._retry_windows_io(commit)
        finally:
            thread.join()
        self.assertTrue(renamed.is_dir())
        self.assertGreater(attempts, 1)


class ResourcePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.app._capcap_test_mode = True

    def test_group_individual_all_selection_and_empty_disabled(self):
        from PySide6.QtCore import Qt
        from ui.controllers.resource_export_controller import ResourceSelectionDialog
        items = [{"id": str(n), "group": "Original transcript" if n<2 else "Media", "label": "Plain text (TXT)",
                  "relative_path": str(n), "size": 1024, "default_checked": n<2} for n in range(3)]
        dialog = ResourceSelectionDialog(items)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.selected_ids(), ["0", "1"])
        dialog.clear_all.click()
        self.assertFalse(dialog.selected_ids())
        self.assertFalse(dialog.submit.isEnabled())
        dialog.tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.assertEqual(dialog.selected_ids(), ["0", "1"])
        dialog.leaves["0"].setCheckState(0, Qt.Unchecked)
        self.assertEqual(dialog.tree.topLevelItem(0).checkState(0), Qt.PartiallyChecked)
        dialog.select_all.click()
        self.assertEqual(len(dialog.selected_ids()), 3)
        self.assertTrue(dialog.submit.isEnabled())

    def test_import_keeps_source_video_selected_and_export_shows_note(self):
        from ui.controllers.resource_export_controller import ResourceSelectionDialog
        from PySide6.QtCore import Qt
        items = [{"id": "video", "role": "source_video", "group": "Media", "label": "Source video (large file)",
                  "relative_path": "media/source.mp4", "size": 1024, "default_checked": False}]
        exported = ResourceSelectionDialog(items)
        imported = ResourceSelectionDialog(items, importing=True)
        self.addCleanup(exported.deleteLater)
        self.addCleanup(imported.deleteLater)
        self.assertFalse(exported.video_note.isHidden())
        exported.select_all.click()
        self.assertTrue(exported.video_note.isHidden())
        imported.clear_all.click()
        self.assertEqual(imported.selected_ids(), ["video"])
        self.assertFalse(imported.leaves["video"].flags() & Qt.ItemIsUserCheckable)

    def test_picker_reports_missing_video_without_asking_for_external_file(self):
        from PySide6.QtWidgets import QWidget
        from ui.controllers import resource_export_controller as controller
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = {"source_segments": [{"start": 0, "end": 1, "text": "source"}]}
            items = build_catalog(snapshot)
            package = root/"package"
            export_resources(snapshot, items, [i["id"] for i in items], package)
            parent = QWidget()
            self.addCleanup(parent.deleteLater)
            with patch.object(controller.QFileDialog, "getExistingDirectory", return_value=str(package)), \
                    patch.object(controller.QFileDialog, "getOpenFileName") as chooser, \
                    patch.object(controller, "_run") as worker, \
                    patch.object(controller.QMessageBox, "warning") as warning:
                self.assertIsNone(controller.choose_and_import_capcap_package(parent, str(root/"workspace")))
                chooser.assert_not_called()
                worker.assert_not_called()
                warning.assert_called_once()

    def test_duplicate_prompt_opens_existing_or_cancels_without_copying(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QWidget, QMessageBox
        from ui.controllers import resource_export_controller as controller
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root/"source.mp4"
            video.write_bytes(b"source")
            snapshot = {"video_path": str(video)}
            items = build_catalog(snapshot)
            package = root/"package"
            export_resources(snapshot, items, [i["id"] for i in items], package)
            existing = import_resources(package, root/"workspace")
            parent = QWidget()
            self.addCleanup(parent.deleteLater)
            for accept in (False, True):
                def click():
                    box = self.app.activeModalWidget()
                    role = QMessageBox.AcceptRole if accept else QMessageBox.RejectRole
                    next(b for b in box.buttons() if box.buttonRole(b) == role).click()
                with patch.object(controller.QFileDialog, "getExistingDirectory", return_value=str(package)), \
                        patch.object(controller, "ResourceSelectionDialog") as selector, \
                        patch.object(controller, "_run") as worker:
                    QTimer.singleShot(20, click)
                    result = controller.choose_and_import_capcap_package(parent, str(root/"workspace"))
                    selector.assert_not_called()
                    worker.assert_not_called()
                    if accept:
                        self.assertEqual(result["path"], existing["path"])
                        self.assertTrue(result["already_imported"])
                    else:
                        self.assertIsNone(result)

    def test_imported_output_controls_restore_without_firing_combo_callbacks(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from PySide6.QtWidgets import QComboBox
        from ui.controllers.resource_export_controller import restore_package_output_controls
        gui = SimpleNamespace(video_view=MagicMock())
        callbacks = []
        for name, keys in (("output_ratio_combo", ("source", "9:16")), ("output_quality_combo", ("source", "1080p")),
                           ("output_fps_combo", ("source", "30")), ("output_scale_mode_combo", ("fit", "fill"))):
            combo = QComboBox()
            for key in keys:
                combo.addItem(key, key)
            combo.currentIndexChanged.connect(lambda _: callbacks.append(True))
            setattr(gui, name, combo)
        gui.get_output_ratio_key = lambda: gui.output_ratio_combo.currentData()
        gui.get_output_scale_mode_key = lambda: gui.output_scale_mode_combo.currentData()
        state = SimpleNamespace(settings={"package_output_controls": {"ratio": "9:16", "quality": "1080p", "fps": "30", "scale_mode": "fill", "focus": [.8,.3]}})
        restore_package_output_controls(gui, state)
        self.assertEqual(gui.get_output_ratio_key(), "9:16")
        self.assertEqual(gui.get_output_scale_mode_key(), "fill")
        gui.video_view.set_preview_fill_focus.assert_called_once_with(.8,.3)
        self.assertFalse(callbacks)

    def test_background_export_and_import_dialogs(self):
        from PySide6.QtWidgets import QDialog
        from ui.controllers.resource_export_controller import _ResourceProgressDialog
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root/"source.mp4"
            video.write_bytes(b"test source")
            snapshot = {"video_path": str(video), "source_segments": [{"start": 0, "end": 1, "text": "source"}]}
            items = build_catalog(snapshot)
            destination = root/"package"
            dialog = _ResourceProgressDialog("export", dict(snapshot=snapshot, items=items, selected=[i["id"] for i in items], destination=str(destination)))
            dialog.worker.start()
            dialog.exec()
            self.assertFalse(dialog.worker.isRunning())
            self.assertFalse(dialog.worker.error, dialog.worker.error)
            dialog.deleteLater()
            imported = _ResourceProgressDialog("import", dict(source=str(destination), workspace=str(root/"workspace")))
            imported.worker.start()
            imported.exec()
            self.assertFalse(imported.worker.error, imported.worker.error)
            self.assertTrue(Path(imported.worker.result["video_path"]).is_file())
            imported.deleteLater()

    def test_import_controller_opens_new_project_with_its_language_pair(self):
        from PySide6.QtWidgets import QWidget, QComboBox, QDialog
        from ui.controllers import resource_export_controller as controller
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root/"source.mp4"
            video.write_bytes(b"test source")
            snapshot = {"video_path": str(video), "input_language": "zh", "target_language": "ja",
                        "source_segments": [{"start": 0, "end": 1, "text": "source"}]}
            items = build_catalog(snapshot)
            selected = [i["id"] for i in items]
            package = root/"package"
            export_resources(snapshot, items, selected, package)
            gui = QWidget()
            self.addCleanup(gui.deleteLater)
            gui.project_service = ProjectService(str(root/"workspace"))
            gui.log = lambda _: None
            gui.persist_current_timeline_project_data = lambda: None
            for name in ("lang_whisper_combo", "lang_target_combo"):
                combo = QComboBox(gui)
                for key in ("en", "vi", "zh", "ja"):
                    combo.addItem(key, key)
                setattr(gui, name, combo)
            opened = []
            def load(path):
                state = gui.project_service.ensure_project(path, input_language=gui.lang_whisper_combo.currentData(),
                                                          target_language=gui.lang_target_combo.currentData())
                opened.append(state)
            gui.load_video_project = load
            with patch.object(controller.QFileDialog, "getExistingDirectory", return_value=str(package)), \
                    patch.object(controller, "ResourceSelectionDialog") as selector, \
                    patch.object(controller.QMessageBox, "information"), \
                    patch.object(controller.QMessageBox, "warning") as warning:
                selector.return_value.exec.return_value = QDialog.Accepted
                selector.return_value.selected_ids.return_value = selected
                controller.import_capcap_package(gui)
                warning.assert_not_called()
            self.assertEqual(len(opened), 1)
            self.assertEqual((opened[0].input_language, opened[0].target_language), ("zh", "ja"))
            self.assertIn("transcript_segments", opened[0].artifacts)


class LauncherPackageImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.app._capcap_test_mode = True

    def test_launcher_imports_without_an_open_project_and_cancel_does_not_open(self):
        from ui.views.launcher import LauncherWindow
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            stack.enter_context(patch.object(LauncherWindow, "_detect_gpu_with_cuda", return_value=(False, "", False)))
            stack.enter_context(patch.object(LauncherWindow, "_load_recent"))
            stack.enter_context(patch.object(LauncherWindow, "_validate_resources_for_device"))
            stack.enter_context(patch("ui.views.launcher.QTimer.singleShot"))
            stack.enter_context(patch("ui.views.launcher.set_language"))
            stack.enter_context(patch("ui.views.launcher.workspace_root", return_value=folder))
            window = LauncherWindow()
            stack.callback(window.deleteLater)
            self.assertFalse(hasattr(window, "project_service"))
            self.assertEqual(len(window.import_project_menu.actions()), 2)
            accept = stack.enter_context(patch.object(window, "accept"))
            recent = stack.enter_context(patch.object(LauncherWindow, "add_recent"))
            importer = stack.enter_context(patch("controllers.resource_export_controller.choose_and_import_capcap_package", return_value=None))
            window.import_capcap_action.trigger()
            importer.assert_called_once_with(window, folder)
            accept.assert_not_called()
            recent.assert_not_called()
            importer.return_value = {"video_path": str(Path(folder)/"imported.mp4"), "warnings": []}
            window.import_capcap_action.trigger()
            self.assertEqual(window.selected_video, importer.return_value["video_path"])
            recent.assert_called_once_with(None, window.selected_video)
            accept.assert_called_once()
            with patch("controllers.capcut_export_controller.import_capcut_package") as capcut:
                window.import_capcut_action.trigger()
                capcut.assert_called_once_with(window)

    def test_shared_import_picker_needs_only_parent_and_workspace(self):
        from PySide6.QtWidgets import QWidget, QDialog
        from ui.controllers import resource_export_controller as controller
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root/"source.mp4"
            video.write_bytes(b"test source")
            snapshot = {"video_path": str(video)}
            items = build_catalog(snapshot)
            selected = [i["id"] for i in items]
            package = root/"package"
            export_resources(snapshot, items, selected, package)
            parent = QWidget()
            self.addCleanup(parent.deleteLater)
            with patch.object(controller.QFileDialog, "getExistingDirectory", return_value=str(package)), \
                    patch.object(controller, "ResourceSelectionDialog") as selector, \
                    patch.object(controller.QMessageBox, "warning") as warning:
                selector.return_value.exec.return_value = QDialog.Accepted
                selector.return_value.selected_ids.return_value = selected
                result = controller.choose_and_import_capcap_package(parent, str(root/"workspace"))
                warning.assert_not_called()
            self.assertTrue(Path(result["video_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
