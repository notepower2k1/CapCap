import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

app = QApplication.instance() or QApplication([])

from translation.prompt_loader import (
    prompt_directory,
    load_translation_presets,
    render_preset_prompt,
)
from services.resource_download_service import ResourceDownloadService
from runtime_paths import asset_path
from utils.display_utils import build_contrasting_window_icon


class TestRuntimeBugfixes(unittest.TestCase):
    def test_prompt_directory_and_presets(self):
        p_dir = prompt_directory()
        self.assertTrue(p_dir.is_dir(), f"prompt_directory {p_dir} must exist")
        presets = load_translation_presets()
        self.assertGreater(len(presets), 0, "Presets catalog must not be empty")

        rendered = render_preset_prompt("general_default", source_lang="zh", target_lang="vi")
        self.assertIn("vi", rendered)
        self.assertGreater(len(rendered), 100)

    def test_render_preset_prompt_fallback(self):
        # Even with an invalid preset ID, it should render without raising FileNotFoundError
        rendered = render_preset_prompt("non_existent_preset_xyz", source_lang="zh", target_lang="vi")
        self.assertIsInstance(rendered, str)
        self.assertGreater(len(rendered), 20)

    def test_vieneu_not_installed_by_default_in_fresh_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_frozen = getattr(sys, "frozen", False)
            orig_exe = getattr(sys, "executable", "")
            try:
                sys.frozen = True
                sys.executable = os.path.join(tmpdir, "CapCap.exe")

                # Fresh install only has README.txt
                v_dir = os.path.join(tmpdir, "models", "vieneu")
                os.makedirs(v_dir, exist_ok=True)
                with open(os.path.join(v_dir, "README.txt"), "w") as f:
                    f.write("readme")

                svc = ResourceDownloadService(tmpdir)
                self.assertFalse(
                    svc._is_vieneu_installed(),
                    "VieNeu should NOT be detected as installed when only README.txt is present",
                )
                self.assertFalse(
                    svc.is_resource_installed("voice:vieneu"),
                    "ResourceDownloadService.is_resource_installed('voice:vieneu') must return False",
                )

                # Now simulate downloaded model files
                tok_snap = os.path.join(
                    tmpdir,
                    "models",
                    "vieneu",
                    "hub",
                    "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX",
                    "snapshots",
                    "test_hash",
                )
                v3_snap = os.path.join(
                    tmpdir,
                    "models",
                    "vieneu",
                    "hub",
                    "models--pnnbao-ump--VieNeu-TTS-v3-Turbo",
                    "snapshots",
                    "test_hash",
                )
                os.makedirs(tok_snap, exist_ok=True)
                os.makedirs(v3_snap, exist_ok=True)
                with open(os.path.join(tok_snap, "model.onnx"), "w") as f:
                    f.write("dummy")
                with open(os.path.join(v3_snap, "model.onnx"), "w") as f:
                    f.write("dummy")

                self.assertTrue(
                    svc._is_vieneu_installed(),
                    "VieNeu should be detected as installed when valid model files exist in hub",
                )
            finally:
                sys.frozen = orig_frozen
                sys.executable = orig_exe

    def test_capcap_ico_multi_resolution(self):
        ico_path = asset_path("capcap.ico")
        self.assertTrue(os.path.exists(ico_path), "capcap.ico must exist in assets")

        # Check dark bg (white icon for dark title bar / taskbar)
        icon_dark_bg = build_contrasting_window_icon(ico_path, is_dark_bg=True)
        self.assertIsInstance(icon_dark_bg, QIcon)
        sizes = icon_dark_bg.availableSizes()
        self.assertGreaterEqual(len(sizes), 4, f"capcap.ico should have multiple sizes, got {sizes}")
        p32 = icon_dark_bg.pixmap(32, 32).toImage()
        white_pixels = [
            p32.pixelColor(x, y).getRgb()
            for y in range(32)
            for x in range(32)
            if p32.pixelColor(x, y).alpha() > 200
        ]
        self.assertTrue(len(white_pixels) > 0)
        self.assertTrue(
            all(r == 255 and g == 255 and b == 255 for r, g, b, a in white_pixels),
            "Dark background icon should be tinted white for high contrast",
        )

        # Check light bg (solid black silhouette for light/white headers)
        icon_light_bg = build_contrasting_window_icon(ico_path, is_dark_bg=False)
        p32_dark = icon_light_bg.pixmap(32, 32).toImage()
        dark_pixels = [
            p32_dark.pixelColor(x, y).getRgb()
            for y in range(32)
            for x in range(32)
            if p32_dark.pixelColor(x, y).alpha() > 200
        ]
        self.assertTrue(len(dark_pixels) > 0)
        self.assertTrue(
            all(r == 0 and g == 0 and b == 0 for r, g, b, a in dark_pixels),
            "Light background icon should be tinted solid black (#000000) for popup headers",
        )

        # Check default auto-detection
        icon_auto = build_contrasting_window_icon(ico_path)
        self.assertGreaterEqual(len(icon_auto.availableSizes()), 4)

    def test_dialog_icon_filter_and_light_icon(self):
        from PySide6.QtWidgets import QDialog
        from utils.display_utils import install_dialog_icon_filter
        ico_path = asset_path("capcap.ico")

        # Install filter
        filter_obj = install_dialog_icon_filter(app, ico_path)
        self.assertIsNotNone(filter_obj)

        # Dialog should automatically receive the black icon
        dlg = QDialog()
        dlg.show()
        dlg_pix = dlg.windowIcon().pixmap(32, 32).toImage()
        dark_pixels = [
            dlg_pix.pixelColor(x, y).getRgb()
            for y in range(32)
            for x in range(32)
            if dlg_pix.pixelColor(x, y).alpha() > 200
        ]
        self.assertTrue(len(dark_pixels) > 0)
        self.assertTrue(
            all(r == 0 and g == 0 and b == 0 for r, g, b, a in dark_pixels),
            "QDialog should automatically have solid black icon on show",
        )

    def test_sea_g2p_db_path_patch_and_resolution(self):
        from vieneu_tts import _patch_sea_g2p_db_path
        import sea_g2p.g2p

        # Verify patch is active
        res = _patch_sea_g2p_db_path()
        self.assertTrue(res)

        # Simulate broken __file__ inside a zip archive (like PyInstaller base_library.zip)
        orig_file = sea_g2p.g2p.__file__
        try:
            sea_g2p.g2p.__file__ = os.path.join(
                PROJECT_ROOT, "dist", "CapCap", "_internal", "base_library.zip", "sea_g2p", "g2p.py"
            )
            g2p = sea_g2p.g2p.G2P("vi")
            out = g2p.convert("xin chào")
            self.assertTrue(out, "G2P convert should return phonemes despite simulated zip path")
        finally:
            sea_g2p.g2p.__file__ = orig_file

    def test_engine_runtime_and_adapters_importable(self):
        from services.engine_runtime import EngineRuntime
        from importlib import import_module

        # Verify all adapters registered in EngineRuntime._ADAPTERS can be imported
        for key, (mod_name, cls_name) in EngineRuntime._ADAPTERS.items():
            mod = import_module(mod_name)
            cls = getattr(mod, cls_name)
            self.assertIsNotNone(cls, f"Adapter class {cls_name} in {mod_name} must exist")

        # Verify remote adapters can also be imported
        remote_adapters = [
            ("engines.remote_whisper_adapter", "RemoteWhisperAdapter"),
            ("engines.remote_translator_adapter", "RemoteTranslatorAdapter"),
            ("engines.remote_tts_adapter", "RemoteTTSAdapter"),
        ]
        for mod_name, cls_name in remote_adapters:
            mod = import_module(mod_name)
            cls = getattr(mod, cls_name)
            self.assertIsNotNone(cls, f"Remote adapter class {cls_name} in {mod_name} must exist")

        # Verify ocr_processor can be imported
        import ocr_processor
        self.assertTrue(hasattr(ocr_processor, "transcribe_video_ocr"))

    def test_ocr_overlay_suppressed_during_pipeline_and_progress_dialog(self):
        from PySide6.QtWidgets import QWidget
        from PySide6.QtCore import QEvent
        from ui.views.preview_panel import OcrRegionOverlay, OcrTranslatorOverlay
        from ui.widgets.progress_dialog import PipelineProgressDialog

        # Create a mock main window and target view
        main_win = QWidget()
        target_view = QWidget(main_win)
        target_view.resize(640, 480)
        main_win.video_view = target_view
        main_win._pipeline_active = False
        main_win._ocr_overlay_visible = True
        main_win._tracked_progress_dialogs = []

        ocr_overlay = OcrRegionOverlay()
        ocr_overlay.attach_to_view(target_view)
        main_win.ocr_region_overlay = ocr_overlay

        ocr_translator = OcrTranslatorOverlay()
        ocr_translator.attach_to_view(target_view)
        main_win.ocr_translator_overlay = ocr_translator

        # Initially, with pipeline inactive and no progress dialog, OCR is not suppressed
        self.assertFalse(ocr_overlay._is_suppressed())
        self.assertFalse(ocr_translator._is_suppressed())

        # Create PipelineProgressDialog
        progress_dlg = PipelineProgressDialog(main_win)
        main_win.pipeline_controller = type("MockPipelineController", (), {"progress_dialog": progress_dlg})()

        # Simulate showEvent on progress dialog
        progress_dlg._set_preview_overlays_suppressed(True)
        self.assertTrue(ocr_overlay._is_suppressed())
        self.assertTrue(ocr_translator._is_suppressed())
        self.assertFalse(ocr_overlay.isVisible())

        # Attempt to show or sync_to_view during suppression
        ocr_overlay.show()
        self.assertFalse(ocr_overlay.isVisible())
        ocr_overlay.sync_to_view()
        self.assertFalse(ocr_overlay.isVisible())

        # Simulate WindowActivate event on main window (e.g. tabbing in)
        activate_event = QEvent(QEvent.WindowActivate)
        ocr_overlay.eventFilter(main_win, activate_event)
        self.assertFalse(ocr_overlay.isVisible(), "OCR overlay must NOT show on WindowActivate while progress dialog is active")

        # Now simulate pipeline active flag
        progress_dlg._set_preview_overlays_suppressed(False)
        main_win._pipeline_active = True
        self.assertTrue(ocr_overlay._is_suppressed())
        ocr_overlay.sync_to_view()
        self.assertFalse(ocr_overlay.isVisible(), "OCR overlay must NOT show while pipeline is active")

        # When pipeline is done and progress dialog is gone, suppression is lifted
        main_win._pipeline_active = False
        main_win.pipeline_controller.progress_dialog = None
        self.assertFalse(ocr_overlay._is_suppressed())

    def test_windows_media_ocr_integration(self):
        from app.ocr_processor import get_windows_ocr_languages, WindowsMediaOcrEngine, WindowsMediaOcrResult
        import numpy as np
        import cv2

        # 1. get_windows_ocr_languages returns a list of installed language tags
        langs = get_windows_ocr_languages()
        self.assertIsInstance(langs, list)

        # 2. WindowsMediaOcrEngine with auto or first available language
        try:
            engine = WindowsMediaOcrEngine(lang="auto")
            self.assertIsNotNone(engine._target_lang)

            # Test with empty image
            empty_img = np.zeros((100, 100, 3), dtype=np.uint8)
            result = engine(empty_img)
            self.assertIsInstance(result, WindowsMediaOcrResult)
            self.assertEqual(result.txts, [])

            # Test with synthetic English text image if en-US or en is available
            has_en = any("en" in tag.lower() for tag in langs)
            if has_en:
                en_engine = WindowsMediaOcrEngine(lang="en")
                test_img = np.zeros((100, 400, 3), dtype=np.uint8)
                test_img.fill(255)
                cv2.putText(test_img, "HELLO OCR", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
                en_result = en_engine(test_img)
                self.assertIsInstance(en_result, WindowsMediaOcrResult)
                combined = " ".join(en_result.txts).upper()
                self.assertIn("HELLO", combined)

            # 3. Test that requesting an unsupported/uninstalled language raises RuntimeError
            with self.assertRaises(RuntimeError) as ctx:
                WindowsMediaOcrEngine(lang="non_existent_lang_12345")
            self.assertIn("Windows Media OCR", str(ctx.exception))
            self.assertIn("Windows Settings", str(ctx.exception))
        except RuntimeError as e:
            # If winocr or winrt is unavailable on the machine, verify error message
            self.assertIn("winocr", str(e).lower())

        # 4. Verify onnxruntime remains functional after winocr operations (no DLL conflict)
        import onnxruntime
        self.assertTrue(hasattr(onnxruntime, "InferenceSession"))

    def test_workspace_root_permissions_fallback(self):
        import tempfile
        from unittest.mock import patch
        from runtime_paths import _is_dir_writable, workspace_root

        # 1. Test _is_dir_writable
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(_is_dir_writable(td))
        self.assertFalse(_is_dir_writable(r"C:\Windows\System32\NonExistentDirectory_12345"))

        # 2. Test frozen mode with read-only exe dir (e.g. C:\Program Files\CapCap)
        with patch("sys.frozen", True, create=True), \
             patch("sys.executable", r"C:\Program Files\CapCap\CapCap.exe"), \
             patch("runtime_paths._is_dir_writable", return_value=False):
            ws = workspace_root()
            self.assertNotIn("Program Files", ws)
            self.assertTrue("CapCap" in ws)


    def test_export_runtime_logs(self):
        from unittest.mock import MagicMock, patch
        from main_window import VideoTranslatorGUI

        with tempfile.TemporaryDirectory() as td:
            target_file = os.path.join(td, "exported_logs.txt")
            mock_gui = MagicMock()
            mock_gui.workspace_root = td
            mock_gui._runtime_logs = ["[12:00:00] Sample log entry 1", "[12:00:01] Sample log entry 2"]
            mock_gui._flush_runtime_log_entries = MagicMock()
            mock_gui.log = MagicMock()

            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(target_file, "text")), \
                 patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
                VideoTranslatorGUI.export_runtime_logs(mock_gui)

                self.assertTrue(os.path.exists(target_file))
                with open(target_file, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("Sample log entry 1", content)
                self.assertIn("Sample log entry 2", content)
                mock_info.assert_called_once()

    def test_completed_translation_provider_label_fallback(self):
        from unittest.mock import MagicMock
        from main_window import VideoTranslatorGUI

        mock_gui = MagicMock()
        mock_gui._last_translation_provider = "google"
        mock_gui.current_translated_segment_models = []
        mock_gui.current_translated_segments = []

        label = VideoTranslatorGUI._completed_translation_provider_label(mock_gui)
        self.assertEqual(label, "Google Translate")

        mock_gui._last_translation_provider = "bing"
        label = VideoTranslatorGUI._completed_translation_provider_label(mock_gui)
        self.assertEqual(label, "Bing Translator")

    def test_hf_endpoint_configuration_and_mirror_fallback(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmpdir:
            # Case 1: Neither CAPCAP_HF_ENDPOINT nor HF_ENDPOINT set
            with patch.dict(os.environ, {}, clear=True):
                svc = ResourceDownloadService(tmpdir)
                self.assertNotIn("HF_ENDPOINT", os.environ)
                self.assertEqual(svc._hf_endpoint(), svc.HF_MIRROR_ENDPOINT)

            # Case 2: Only HF_ENDPOINT set
            with patch.dict(os.environ, {"HF_ENDPOINT": "https://custom-hf.com"}, clear=True):
                svc = ResourceDownloadService(tmpdir)
                self.assertEqual(svc._hf_endpoint(), "https://custom-hf.com")

            # Case 3: Only CAPCAP_HF_ENDPOINT set
            with patch.dict(os.environ, {"CAPCAP_HF_ENDPOINT": "https://mirror.capcap.com"}, clear=True):
                svc = ResourceDownloadService(tmpdir)
                self.assertEqual(os.environ.get("HF_ENDPOINT"), "https://mirror.capcap.com")
                self.assertEqual(svc._hf_endpoint(), "https://mirror.capcap.com")

    def test_whisper_turbo_download_unsets_mirror_and_falls_back(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ResourceDownloadService(tmpdir)

            called_snapshots = []
            seen_hf_endpoint = []

            def fake_snapshot_download(repo_id, **kwargs):
                called_snapshots.append(repo_id)
                seen_hf_endpoint.append(os.environ.get("HF_ENDPOINT"))
                return tmpdir

            with patch.dict(os.environ, {"HF_ENDPOINT": "https://hf-mirror.com"}):
                with patch("huggingface_hub.snapshot_download", side_effect=fake_snapshot_download), \
                     patch.object(svc, "is_resource_installed", return_value=True):
                    svc.download_resource("whisper:turbo")

                # Verify HF_ENDPOINT was unset during snapshot_download calls
                self.assertEqual(seen_hf_endpoint, [None])
                # Verify snapshot_download was called with mobiuslabsgmbh repo id
                self.assertEqual(
                    called_snapshots,
                    ["mobiuslabsgmbh/faster-whisper-large-v3-turbo"],
                )
                # Verify HF_ENDPOINT was restored afterwards
                self.assertEqual(os.environ.get("HF_ENDPOINT"), "https://hf-mirror.com")
                # Verify symlinks warning was suppressed
                self.assertEqual(os.environ.get("HF_HUB_DISABLE_SYMLINKS_WARNING"), "1")

            # Also test fallback to faster_whisper.download_model when snapshot_download fails
            called_fallback = []
            def fake_download_model(model_name, cache_dir=None):
                called_fallback.append(model_name)
                return cache_dir

            with patch.dict(os.environ, {"HF_ENDPOINT": "https://hf-mirror.com"}):
                with patch("huggingface_hub.snapshot_download", side_effect=RuntimeError("HF Hub unreachable")), \
                     patch("faster_whisper.download_model", side_effect=fake_download_model), \
                     patch.object(svc, "is_resource_installed", return_value=True):
                    svc.download_resource("whisper:turbo")

                self.assertEqual(called_fallback, ["turbo"])

    def test_loading_overlay_suppresses_logo_and_text_layers(self):
        from unittest.mock import MagicMock
        from main_window import VideoTranslatorGUI
        from widgets.mpv_video_view import _LogoRegionOverlayWindow

        # 1. Test _LogoRegionOverlayWindow.sync_to_view hiding when loading in progress
        mock_win = MagicMock()
        mock_win._project_loading_in_progress = True
        mock_target = MagicMock()
        mock_target.window.return_value = mock_win
        overlay = _LogoRegionOverlayWindow.__new__(_LogoRegionOverlayWindow)
        overlay._target_view = mock_target
        overlay.hide = MagicMock()
        overlay.sync_to_view()
        overlay.hide.assert_called_once()

        # 2. Test VideoTranslatorGUI.show_loading_overlay hides subtitle, logo, and text overlays
        mock_gui = MagicMock()
        mock_gui.video_view = MagicMock()
        mock_gui._loading_overlay = MagicMock()
        VideoTranslatorGUI.show_loading_overlay(mock_gui, video_path="test.mp4")
        self.assertTrue(mock_gui._project_loading_in_progress)
        mock_gui.video_view.subtitle_item.hide.assert_called_once()
        mock_gui.video_view.logo_overlay.hide.assert_called_once()
        mock_gui.video_view.text_overlay.hide.assert_called_once()

        # 3. Test _show_logo_overlay returns early when loading in progress
        mock_gui._project_loading_in_progress = True
        mock_gui.video_view.clear_logo = MagicMock()
        VideoTranslatorGUI._show_logo_overlay(mock_gui, track=MagicMock(), layer=MagicMock())
        # Should not touch video_view or layer since it returned early
        mock_gui.video_view.clear_logo.assert_not_called()

    def test_logo_overlay_cleared_on_project_reset_and_context_load(self):
        from unittest.mock import MagicMock
        from main_window import VideoTranslatorGUI
        from widgets.mpv_video_view import _LogoRegionOverlayWindow

        # 1. Test _LogoRegionOverlayWindow.clear_region
        overlay = _LogoRegionOverlayWindow.__new__(_LogoRegionOverlayWindow)
        mock_timer = MagicMock()
        mock_target = MagicMock()
        mock_main = MagicMock()
        overlay._sync_timer = mock_timer
        overlay._target_view = mock_target
        overlay._main_window = mock_main
        overlay._pixmap = MagicMock()
        overlay._logo_items = [{"id": "1"}]
        overlay._opacity = 0.5
        overlay._rotation = 45.0
        overlay._regions = [MagicMock()]
        overlay._drag_mode = "move"
        overlay._drag_index = 0
        overlay._active_index = 0
        overlay._suspended = True
        overlay.hide = MagicMock()
        overlay.update = MagicMock()

        overlay.clear_region()
        mock_timer.stop.assert_called_once()
        mock_target.removeEventFilter.assert_called_once_with(overlay)
        self.assertIsNone(overlay._pixmap)
        self.assertEqual(overlay._logo_items, [])
        self.assertEqual(overlay._opacity, 1.0)
        self.assertEqual(overlay._rotation, 0.0)
        self.assertEqual(overlay._regions, [])
        self.assertIsNone(overlay._target_view)

        # 2. Test VideoTranslatorGUI._reset_project_runtime_state clears logo & text
        mock_gui = MagicMock()
        mock_gui.video_view = MagicMock()
        mock_gui._logo_overlay_track = MagicMock()
        mock_gui._logo_overlay_layer = MagicMock()
        mock_gui._text_overlay_track = MagicMock()
        mock_gui._text_overlay_layer = MagicMock()
        mock_gui._clear_segment_editor_rows = MagicMock()
        mock_gui.sync_segment_editor_rows = MagicMock()
        mock_gui.update_progress_checklist = MagicMock()
        mock_gui.refresh_ui_state = MagicMock()

        VideoTranslatorGUI._reset_project_runtime_state(mock_gui)
        mock_gui.video_view.clear_logo.assert_called_once()
        mock_gui.video_view.clear_text.assert_called_once()
        self.assertIsNone(mock_gui._logo_overlay_track)
        self.assertIsNone(mock_gui._logo_overlay_layer)
        self.assertIsNone(mock_gui._text_overlay_track)
        self.assertIsNone(mock_gui._text_overlay_layer)

        # 3. Test load_project_context clears logo when no L1 Logo track exists
        mock_gui = MagicMock()
        mock_gui.timeline._timeline.tracks = []  # No logo track
        mock_gui.video_view = MagicMock()
        mock_gui._logo_overlay_track = "existing_track"
        mock_gui._logo_overlay_layer = "existing_layer"
        mock_state = MagicMock()
        mock_state.settings = {}
        mock_gui.project_bridge.load_context.return_value = {
            "artifacts": {},
            "last_original_srt_path": "",
            "last_translated_srt_path": "",
            "last_extracted_audio": "",
            "last_vocals_path": "",
            "last_music_path": "",
            "last_voice_vi_path": "",
            "last_mixed_vi_path": "",
            "current_segment_models": [],
            "current_translated_segment_models": [],
            "current_segments": [],
            "current_translated_segments": [],
        }

        # Call the actual method
        VideoTranslatorGUI.load_project_context(mock_gui, mock_state)

        # Verify clear_logo was called
        mock_gui.video_view.clear_logo.assert_called_once()
        self.assertIsNone(mock_gui._logo_overlay_track)
        self.assertIsNone(mock_gui._logo_overlay_layer)

    def test_whisper_is_resource_installed_requires_model_bin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ResourceDownloadService(tmpdir)
            svc._whisper_cache_root = lambda: tmpdir

            # 1. Directory with only config.json and incomplete files -> False
            model_hub_dir = Path(tmpdir) / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
            model_hub_dir.mkdir(parents=True, exist_ok=True)
            (model_hub_dir / "config.json").write_text("{}")
            blobs_dir = model_hub_dir / "blobs"
            blobs_dir.mkdir(parents=True, exist_ok=True)
            (blobs_dir / "model.bin.incomplete").write_bytes(b"")

            self.assertFalse(
                svc.is_resource_installed("whisper:turbo"),
                "Whisper Turbo should NOT be marked installed when model.bin is missing or incomplete",
            )

            # 2. Directory contains snapshots/<hash>/model.bin with size > 0 -> True
            snap_dir = model_hub_dir / "snapshots" / "test_snapshot_hash"
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_bin = snap_dir / "model.bin"
            snap_bin.write_bytes(b"dummy weights data")

            self.assertTrue(
                svc.is_resource_installed("whisper:turbo"),
                "Whisper Turbo should be marked installed when snapshots/<hash>/model.bin exists and has positive size",
            )

            # Verify no collision: Turbo installed must NOT make large-v3 or distil-large-v3 appear installed
            self.assertFalse(
                svc.is_resource_installed("whisper:large-v3"),
                "Whisper large-v3 must NOT be marked installed when only turbo is downloaded",
            )
            self.assertFalse(
                svc.is_resource_installed("whisper:distil-large-v3"),
                "Whisper distil-large-v3 must NOT be marked installed when only turbo is downloaded",
            )

    def test_resource_manager_button_states_installed_vs_missing(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QDialog
        from ui.views.resource_manager import open_resource_manager
        from services.resource_download_service import ResourceDownloadService

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ResourceDownloadService(tmpdir)
            mock_resources = [
                {
                    "id": "whisper:turbo",
                    "name": "Whisper Turbo",
                    "kind": "whisper",
                    "status": "missing",
                    "status_label": "Missing",
                    "auto_download_supported": True,
                    "target_dir": tmpdir,
                },
                {
                    "id": "voice:pack",
                    "name": "Piper Voice Pack",
                    "kind": "voice",
                    "status": "installed",
                    "status_label": "Ready",
                    "auto_download_supported": True,
                    "target_dir": tmpdir,
                },
            ]
            created_dialogs = []
            orig_init = QDialog.__init__
            def _fake_init(self, *args, **kwargs):
                orig_init(self, *args, **kwargs)
                created_dialogs.append(self)

            with patch.object(ResourceDownloadService, "list_resources", return_value=mock_resources), \
                 patch.object(QDialog, "exec", return_value=None), \
                 patch.object(QDialog, "__init__", _fake_init):
                open_resource_manager(tmpdir)

            self.assertTrue(len(created_dialogs) > 0)
            dlg = created_dialogs[-1]
            rows = getattr(dlg, "_resource_rows", {})
            self.assertIn("whisper:turbo", rows)
            self.assertIn("voice:pack", rows)

            # Missing resource button has primaryBtn styling
            missing_btn = rows["whisper:turbo"]["download_btn"]
            self.assertEqual(missing_btn.objectName(), "primaryBtn")
            self.assertEqual(missing_btn.text(), "Download Whisper")

            # Installed resource button has Re-download text and no primaryBtn
            installed_btn = rows["voice:pack"]["download_btn"]
            self.assertEqual(installed_btn.objectName(), "")
            self.assertEqual(installed_btn.text(), "Re-download")

    def test_resource_download_service_progress_reporting_formats(self):
        from unittest.mock import patch, MagicMock
        from services.resource_download_service import ResourceDownloadService

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ResourceDownloadService(tmpdir)
            progress_calls = []
            def progress_cb(pct, msg):
                progress_calls.append((pct, msg))

            # 1. Test _download_file progress format with MB and %
            fake_resp = MagicMock()
            fake_resp.headers = {"content-length": str(10 * 1024 * 1024)}
            fake_resp.iter_content.return_value = [b"x" * (1024 * 1024)] * 10
            fake_resp.__enter__.return_value = fake_resp
            fake_resp.__exit__.return_value = None

            dest_file = os.path.join(tmpdir, "test_file.bin")
            with patch("requests.get", return_value=fake_resp):
                svc._download_file("http://example.com/file.bin", dest_file, label="Testing File", progress_cb=progress_cb)

            self.assertTrue(any("Testing File" in msg and "MB" in msg for _, msg in progress_calls))
            first_download_call = [msg for _, msg in progress_calls if "Testing File 10%" in msg]
            self.assertTrue(len(first_download_call) > 0)
            self.assertIn("1.0/10.0 MB", first_download_call[0])

            # 2. Test _download_and_extract_zip progress format
            progress_calls.clear()
            dest_dir = os.path.join(tmpdir, "extracted_zip")
            with patch("requests.get", return_value=fake_resp), \
                 patch("zipfile.ZipFile"):
                svc._download_and_extract_zip("http://example.com/file.zip", dest_dir, progress_cb=progress_cb)

            self.assertTrue(any("Downloading... 10% (1.0/10.0 MB)" in msg for _, msg in progress_calls))
            self.assertTrue(any("Extracting files... (95%)" in msg for _, msg in progress_calls))

            # 3. Test _download_and_extract_tar progress format
            progress_calls.clear()
            dest_tar_dir = os.path.join(tmpdir, "extracted_tar")
            def fake_urlretrieve(url, path, reporthook=None):
                if reporthook:
                    reporthook(1, 1024 * 1024, 10 * 1024 * 1024)
            with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve), \
                 patch("tarfile.open"):
                svc._download_and_extract_tar("http://example.com/file.tar.bz2", dest_tar_dir, progress_cb=progress_cb)

            self.assertTrue(any("Downloading... 10% (1.0/10.0 MB)" in msg for _, msg in progress_calls))
            self.assertTrue(any("Extracting files... (95%)" in msg for _, msg in progress_calls))

    def test_i18n_resource_translations(self):
        from ui.i18n import _translate_text
        self.assertEqual(_translate_text("Re-download", "vi"), "Tải lại")
        self.assertEqual(_translate_text("Extracting files... (95%)", "vi"), "Đang giải nén tệp... (95%)")
        self.assertEqual(_translate_text("Extracting files...", "vi"), "Đang giải nén tệp...")

    def test_whisper_load_raises_file_not_found_when_weights_missing(self):
        from unittest.mock import patch
        import whisper_processor

        with patch.object(ResourceDownloadService, "is_resource_installed", return_value=False):
            with self.assertRaises(FileNotFoundError) as ctx:
                whisper_processor._load_whisper_model("turbo")
            self.assertIn("Resource Manager", str(ctx.exception))
            self.assertIn("turbo", str(ctx.exception))

    def test_whisper_cached_snapshot_requires_positive_model_bin_size(self):
        from unittest.mock import patch
        import whisper_processor

        with tempfile.TemporaryDirectory() as tmpdir:
            fw_dir = Path(tmpdir)
            snapshot_dir = fw_dir / "models--Systran--faster-whisper-turbo" / "snapshots" / "test_snapshot"
            snapshot_dir.mkdir(parents=True)
            empty_bin = snapshot_dir / "model.bin"
            empty_bin.touch()

            with patch("whisper_processor.models_path", return_value=str(fw_dir)):
                # Should be None when model.bin is 0 bytes
                self.assertIsNone(whisper_processor._cached_model_snapshot("turbo"))

                # Should return snapshot path when model.bin has positive size
                empty_bin.write_bytes(b"dummy model weights")
                self.assertEqual(whisper_processor._cached_model_snapshot("turbo"), str(snapshot_dir))

                self.assertIsNone(
                    whisper_processor._cached_model_snapshot("large-v3"),
                    "large-v3 snapshot must NOT resolve to turbo model directory",
                )

                # Also test the exact Hugging Face repo name that caused the collision:
                mobius_turbo = fw_dir / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "mobius_snap"
                mobius_turbo.mkdir(parents=True)
                (mobius_turbo / "model.bin").write_bytes(b"dummy mobius turbo weights")

                self.assertIsNone(
                    whisper_processor._cached_model_snapshot("large-v3"),
                    "large-v3 snapshot must NOT resolve to faster-whisper-large-v3-turbo directory",
                )


if __name__ == "__main__":
    unittest.main()


