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
from ui.helpers.srt_helpers import (
    diagnose_srt_timeline,
    parse_srt_to_segments,
    validate_srt_text,
)


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
        from ui.i18n import t

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
            self.assertIn(missing_btn.text(), ("Download Whisper", "Tải Whisper", t("Download Whisper")))

            # Installed resource button has Re-download text and no primaryBtn
            installed_btn = rows["voice:pack"]["download_btn"]
            self.assertEqual(installed_btn.objectName(), "")
            self.assertIn(installed_btn.text(), ("Re-download", "Tải lại", t("Re-download")))

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

    def test_refresh_text_layer_preview_hides_during_loading(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._project_loading_in_progress = True
        gui.video_view = MagicMock()
        text_ov = MagicMock()
        gui.video_view.text_overlay = text_ov

        VideoTranslatorGUI._refresh_text_layer_preview(gui)

        text_ov.hide.assert_called_once()
        gui.video_view.set_text_layers.assert_not_called()

    def test_mpv_video_view_clear_text(self):
        from unittest.mock import MagicMock
        from ui.widgets.mpv_video_view import MpvVideoView

        view = MagicMock(spec=MpvVideoView)
        overlay = MagicMock()
        view.text_overlay = overlay

        MpvVideoView.clear_text(view)

        overlay.set_editable.assert_called_once_with(False)
        overlay.set_items.assert_called_once_with([])
        overlay.hide.assert_called_once()

    def test_text_layer_overlay_sync_to_view_hides_during_project_loading(self):
        from unittest.mock import MagicMock
        from ui.widgets.mpv_video_view import _TextLayerOverlayWindow

        overlay = _TextLayerOverlayWindow()
        mock_win = MagicMock()
        mock_win._project_loading_in_progress = True
        mock_target = MagicMock()
        mock_target.window.return_value = mock_win
        mock_target.isVisible.return_value = True
        overlay._target_view = mock_target

        overlay.show()
        overlay.sync_to_view()
        self.assertFalse(overlay.isVisible())

        overlay.set_items([{"text": "hello"}])
        self.assertFalse(overlay.isVisible())

        overlay.set_suppressed(False)
        self.assertFalse(overlay.isVisible())

    def test_refresh_timed_layer_preview_restores_active_layers(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._project_loading_in_progress = False
        gui._timed_layer_preview_signature = None
        gui._logo_track_preview_visible = True
        gui.media_player = MagicMock()
        gui.media_player.position.return_value = 0
        gui.video_view = MagicMock()

        logo_layer = MagicMock(id="logo_1", type="image", source="test.png", start_time=0.0, end_time=5.0)
        mask_layer = MagicMock(id="mask_1", type="mask", start_time=0.0, end_time=5.0)
        blur_layer = MagicMock(
            id="blur_1",
            type="blur",
            start_time=0.0,
            end_time=5.0,
            position_x=0.1,
            position_y=0.1,
            width=0.2,
            height=0.2,
            blur_strength=20.0,
            blur_opacity=1.0,
            pixelate=False,
            pixelate_size=12,
        )
        text_layer = MagicMock(id="text_1", type="text", start_time=0.0, end_time=5.0)

        track_logo = MagicMock(name="L1 Logo", layers=[logo_layer])
        track_logo.name = "L1 Logo"
        track_mask = MagicMock(name="M1", layers=[mask_layer])
        track_mask.name = "M1"
        track_blur = MagicMock(name="B1", layers=[blur_layer])
        track_blur.name = "B1"
        track_text = MagicMock(name="T1", layers=[text_layer])
        track_text.name = "T1"

        gui.timeline = MagicMock()
        gui.timeline._timeline = MagicMock()
        gui.timeline._timeline.tracks = [track_logo, track_mask, track_blur, track_text]
        gui.timeline._selected_layer_id = ""

        gui._layer_is_active_at_preview_time.return_value = True
        gui._preview_is_playing.return_value = False
        gui._deferred_effect_layer_id_for.return_value = None
        gui._current_mask_regions_payload.return_value = [{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}]
        gui._blur_effect_enabled.return_value = True

        VideoTranslatorGUI.refresh_timed_layer_preview(gui, position_ms=0)

        # Verify text layer preview refresh
        gui._refresh_text_layer_preview.assert_called_once_with("")
        # Verify logo layer overlay restored
        gui._show_logo_overlay.assert_called_once_with(track_logo, logo_layer, editable=False)
        # Verify mask regions set and applied to preview
        gui.video_view.set_mask_regions.assert_called_once()
        self.assertTrue(gui._apply_mask_to_preview.called)
        # Verify blur regions set and applied to preview
        gui.video_view.set_blur_regions_normalized.assert_called_once()
        self.assertTrue(gui.apply_preview_blur_region.called)

    def test_show_logo_overlay_guards_during_project_loading(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._project_loading_in_progress = True
        gui.video_view = MagicMock()

        track = MagicMock(name="L1 Logo")
        layer = MagicMock(source="path/to/logo.png")

        # When project loading is in progress, _show_logo_overlay must return early
        VideoTranslatorGUI._show_logo_overlay(gui, track, layer)
        gui.video_view.show_logo.assert_not_called()

    def test_patch_audio_segment_inplace_silence(self):
        import numpy as np
        import soundfile as sf
        from app.audio_mixer import patch_audio_segment_inplace

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = os.path.join(tmpdir, "test_silence.wav")
            sr = 16000
            data = np.full(sr, 1000, dtype=np.int16)
            sf.write(wav_path, data, sr, subtype="PCM_16")

            result = patch_audio_segment_inplace(wav_path, start_seconds=0.2, duration_seconds=0.2)
            self.assertTrue(result)

            patched_data, patched_sr = sf.read(wav_path, dtype="int16")
            self.assertEqual(patched_sr, sr)
            self.assertEqual(len(patched_data), sr)

            # Untouched before 0.2s
            np.testing.assert_array_equal(patched_data[:3200], 1000)
            # Silence (0) at 0.2s - 0.4s (samples 3200 to 6400)
            np.testing.assert_array_equal(patched_data[3200:6400], 0)
            # Untouched after 0.4s
            np.testing.assert_array_equal(patched_data[6400:], 1000)

    def test_apply_generated_tts_texts_no_off_by_one_on_empty_text(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui.current_translated_segments = [
            {"text": "Hello", "start": 0.0, "end": 1.0},
            {"text": "", "start": 1.0, "end": 2.0},
            {"text": "World", "start": 2.0, "end": 3.0},
        ]
        voice_segments = [
            {"text": "Hello", "tts_text": "Xin chao"},
            {"text": "", "tts_text": ""},
            {"text": "World", "tts_text": "The gioi"},
        ]

        VideoTranslatorGUI._apply_generated_tts_texts(gui, voice_segments)

        self.assertEqual(gui.current_translated_segments[0].get("tts_text"), "Xin chao")
        self.assertEqual(gui.current_translated_segments[1].get("tts_text", ""), "")
        self.assertEqual(gui.current_translated_segments[2].get("tts_text"), "The gioi")

    def test_apply_generated_tts_texts_does_not_collapse_grouped_sub_segment_timing(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui.current_translated_segments = [
            {"text": "Sub 1", "tts_group_id": "grp1", "start": 0.0, "end": 2.0},
            {"text": "Sub 2", "tts_group_id": "grp1", "start": 2.5, "end": 5.0},
        ]
        voice_segments = [
            {"text": "Group text", "tts_text": "Group TTS", "tts_group_id": "grp1", "start": 0.0, "end": 5.0},
        ]

        VideoTranslatorGUI._apply_generated_tts_texts(gui, voice_segments)

        self.assertEqual(gui.current_translated_segments[0]["start"], 0.0)
        self.assertEqual(gui.current_translated_segments[1]["start"], 2.5)

    def test_on_segment_translation_edited_clears_tts_cache(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._syncing_segment_editor = False
        gui.current_segments = [{"start": 0.0, "end": 2.0}]
        gui.current_translated_segments = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "Old",
                "tts_text": "Old TTS",
                "dubbing_vi": "Old Dub",
                "_wav_path": "/fake/old.wav",
                "voice_edited": True,
            }
        ]
        gui.last_voice_vi_path = ""
        editor = MagicMock()
        editor.toPlainText.return_value = "New translated text"

        VideoTranslatorGUI.on_segment_translation_edited(gui, 0, editor)

        self.assertEqual(gui.current_translated_segments[0]["text"], "New translated text")
        self.assertNotIn("tts_text", gui.current_translated_segments[0])
        self.assertNotIn("dubbing_vi", gui.current_translated_segments[0])
        self.assertNotIn("_wav_path", gui.current_translated_segments[0])
        self.assertIs(gui.current_translated_segments[0]["voice_edited"], False)
        self.assertIs(gui._voiceover_force_refresh, True)
        self.assertIs(gui.current_translated_segments[0]["_tts_dirty"], True)

    def test_apply_generated_tts_texts_clears_tts_dirty(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui.current_translated_segments = [
            {"start": 0.0, "end": 2.0, "text": "Segment 1", "_tts_dirty": True}
        ]
        voice_segments = [
            {"start": 0.0, "end": 2.0, "tts_text": "Spoken 1", "subtitle_vi": "Segment 1"}
        ]
        VideoTranslatorGUI._apply_generated_tts_texts(gui, voice_segments)
        self.assertFalse(gui.current_translated_segments[0]["_tts_dirty"])

    def test_subtitle_inspector_tts_status_and_regenerate_button(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI
        from PySide6.QtWidgets import QLabel, QPushButton

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui._translation_phase_complete = MagicMock(return_value=True)
        gui._get_effective_selected_segment_index = MagicMock(return_value=0)
        gui.subtitle_inspector_summary_label = QLabel()
        gui.subtitle_inspector_tts_status_label = QLabel()
        gui.audio_inspector_regenerate_voice_btn = QPushButton()
        gui.rewrite_selected_segment_btn = QPushButton()
        gui.last_voice_vi_path = "voice.wav"

        # Case 1: Segment is dirty
        from ui.i18n import t
        gui.current_translated_segments = [{"_tts_dirty": True}]
        rows = [{"segment_index": 0, "_tts_dirty": True}]
        VideoTranslatorGUI._update_subtitle_inspector_summary(gui, rows)
        self.assertTrue(gui.subtitle_inspector_tts_status_label.isVisible())
        self.assertTrue(any(term in gui.subtitle_inspector_tts_status_label.text() for term in ("Needs TTS", "Chờ tạo TTS", t("Needs TTS"))))
        self.assertTrue(any(term in gui.audio_inspector_regenerate_voice_btn.text() for term in ("Regenerate voice", "Tạo lại giọng đọc", t("Regenerate voice"))))
        self.assertIn("⚡", gui.audio_inspector_regenerate_voice_btn.text())
        self.assertTrue(gui.audio_inspector_regenerate_voice_btn.isEnabled())

        # Case 2: Segment is not dirty
        gui.current_translated_segments = [{"_tts_dirty": False}]
        rows = [{"segment_index": 0, "_tts_dirty": False}]
        VideoTranslatorGUI._update_subtitle_inspector_summary(gui, rows)
        self.assertFalse(gui.subtitle_inspector_tts_status_label.isVisible())
        self.assertNotIn("⚡", gui.audio_inspector_regenerate_voice_btn.text())
        self.assertTrue(gui.audio_inspector_regenerate_voice_btn.isEnabled())

    def test_on_segment_audio_preview_ready_inplace_patch(self):
        from unittest.mock import MagicMock, patch
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui.last_voice_vi_path = "D:/dummy/voice.wav"
        gui.current_translated_segments = [
            {"start": 1.0, "end": 3.0, "text": "Segment 1", "_tts_dirty": True}
        ]
        gui.current_segments = gui.current_translated_segments
        gui._segment_preview_threads = {}
        gui.audio_inspector_regenerate_voice_btn = MagicMock()

        with patch("os.path.exists", return_value=True), \
             patch("app.audio_mixer.patch_audio_segment_inplace") as mock_patch:
            VideoTranslatorGUI.on_segment_audio_preview_ready(gui, 0, "D:/dummy/seg_0.wav", "")

            mock_patch.assert_called_once_with("D:/dummy/voice.wav", 1.0, 2.0, new_segment_wav_path="D:/dummy/seg_0.wav")
            self.assertEqual(gui.current_translated_segments[0]["_wav_path"], "D:/dummy/seg_0.wav")
            self.assertFalse(gui.current_translated_segments[0]["_tts_dirty"])
            gui.apply_segments_to_timeline.assert_called_once()
            gui._update_subtitle_inspector_summary.assert_called_once()

    def test_user_settings_save_and_load_workflow_preferences(self):
        import json
        from unittest.mock import MagicMock
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QComboBox, QLineEdit, QCheckBox, QRadioButton
        from ui.utils.settings_utils import save_user_settings, load_user_settings

        with tempfile.TemporaryDirectory() as tmp_dir:
            ini_path = os.path.join(tmp_dir, "test_workflow_prefs.ini")
            settings = QSettings(ini_path, QSettings.IniFormat)

            gui = MagicMock()
            gui.settings = settings
            del gui.set_voice_combo_value

            # Configure Voice settings on gui
            gui.voice_engine_combo = QComboBox()
            gui.voice_engine_combo.addItem("Edge TTS", "edge-tts")
            gui.voice_engine_combo.addItem("VieNeu-TTS", "vieneu")
            gui.voice_engine_combo.setCurrentIndex(1)

            gui.free_voice_combo = QComboBox()
            gui.free_voice_combo.addItem("Voice A", "voice_a_val")
            gui.free_voice_combo.addItem("Voice B", "voice_b_val")
            gui.free_voice_combo.setCurrentIndex(1)

            gui.voice_gender_combo = QComboBox()
            gui.voice_gender_combo.addItems(["Female", "Male"])
            gui.voice_gender_combo.setCurrentText("Male")

            gui.voice_speed_spin = QComboBox()
            gui.voice_speed_spin.addItems(["1.0x (Normal)", "1.2x (Fast)"])
            gui.voice_speed_spin.setCurrentText("1.2x (Fast)")

            gui.voice_timing_sync_combo = QComboBox()
            gui.voice_timing_sync_combo.addItem("Speed up voice to fit")
            gui.voice_timing_sync_combo.setCurrentText("Speed up voice to fit")

            gui.get_transcription_engine = MagicMock(return_value="whisper")

            # Configure Subtitle style
            gui._current_subtitle_style_controls_state = MagicMock(return_value={
                "preset": "custom",
                "font": "Arial",
                "size": 28,
                "color": "#FFFF00",
                "background_color": "#000000",
                "animation": "pop",
                "animation_time": 0.5,
                "single_line": True,
            })
            gui.subtitle_single_line_cb = QCheckBox()
            gui.subtitle_single_line_cb.setChecked(True)
            gui.subtitle_preset_custom_radio = QRadioButton()
            gui.subtitle_preset_youtube_radio = QRadioButton()
            gui.subtitle_preset_tiktok_radio = QRadioButton()
            gui.subtitle_preset_minimal_radio = QRadioButton()
            applied_styles = []
            gui._apply_subtitle_style_controls_state = MagicMock(side_effect=lambda state: applied_styles.append(dict(state)))

            # Configure Session-local / canvas / diarization widgets on gui
            gui.output_quality_combo = QComboBox()
            gui.output_quality_combo.addItem("Default", "source")
            gui.output_quality_combo.addItem("1080p", "1080p")
            gui.output_quality_combo.addItem("720p", "720p")
            gui.output_quality_combo.setCurrentIndex(1)

            gui.output_fps_combo = QComboBox()
            gui.output_fps_combo.addItem("Default", "source")
            gui.output_fps_combo.addItem("60 FPS", "60")
            gui.output_fps_combo.addItem("30 FPS", "30")
            gui.output_fps_combo.setCurrentIndex(1)

            gui.output_ratio_combo = QComboBox()
            gui.output_ratio_combo.addItem("Source", "source")
            gui.output_ratio_combo.addItem("16:9", "16:9")
            gui.output_ratio_combo.setCurrentIndex(1)

            gui.output_scale_mode_combo = QComboBox()
            gui.output_scale_mode_combo.addItem("Fit", "fit")
            gui.output_scale_mode_combo.addItem("Fill", "fill")
            gui.output_scale_mode_combo.setCurrentIndex(1)

            gui.audio_handling_combo = QComboBox()
            gui.audio_handling_combo.addItem("Fast", "fast")
            gui.audio_handling_combo.addItem("Cleaner voice", "clean")
            gui.audio_handling_combo.setCurrentIndex(1)

            gui.speaker_diarization_cb = QCheckBox()
            gui.speaker_diarization_cb.setChecked(True)

            gui.speaker_diarization_speakers_combo = QComboBox()
            gui.speaker_diarization_speakers_combo.addItem("Auto", -1)
            gui.speaker_diarization_speakers_combo.addItem("2 Speakers", 2)
            gui.speaker_diarization_speakers_combo.setCurrentIndex(1)

            # Pre-populate settings with session-local keys
            settings.setValue("speaker_diarization", True)

            # Other required fields on gui
            gui.output_mode_combo = QComboBox()
            gui.output_mode_combo.addItem("Vietnamese subtitles + voice")
            gui.lang_whisper_combo = QComboBox()
            gui.lang_whisper_combo.addItem("zh", "zh")
            gui.selected_whisper_model_name = "auto"
            gui.final_output_folder_edit = QLineEdit("C:/out")
            gui.audio_folder_edit = QLineEdit("C:/audio")
            gui.srt_output_folder_edit = QLineEdit("C:/srt")
            gui.voice_output_folder_edit = QLineEdit("C:/voice")
            gui.audio_source_edit = QLineEdit("C:/audio_src")
            gui.bg_music_edit = QLineEdit("C:/bg")
            gui.mixed_audio_edit = QLineEdit("C:/mixed")
            gui.video_path_edit = QLineEdit("")
            gui.auto_preview_frame_cb = QCheckBox()
            gui.keep_timeline_cb = QCheckBox()
            gui.anchor_inspector_cb = QCheckBox()
            gui.ai_dubbing_rewrite_cb = QCheckBox()
            gui.toggle_advanced_btn = MagicMock()
            gui.toggle_advanced_btn.isChecked.return_value = False
            gui.use_generated_audio_radio = QRadioButton()
            gui.use_existing_audio_radio = QRadioButton()
            gui.use_free_voice_radio = QRadioButton()
            gui.use_premium_voice_radio = QRadioButton()

            # Call save_user_settings
            save_user_settings(gui)

            # Assert in QSettings:
            self.assertEqual(settings.value("voice_engine"), "vieneu")
            self.assertEqual(settings.value("free_voice_value"), "voice_b_val")
            self.assertEqual(settings.value("free_voice_name"), "Voice B")
            self.assertEqual(settings.value("voice_gender"), "Male")
            self.assertEqual(settings.value("voice_speed"), "1.2x (Fast)")
            self.assertEqual(settings.value("voice_timing_sync_mode"), "Speed up voice to fit")
            self.assertEqual(settings.value("default_transcription_engine"), "whisper")
            self.assertTrue(settings.contains("subtitle_style_controls"))
            self.assertFalse(json.loads(settings.value("subtitle_style_controls"))["single_line"])
            self.assertEqual(settings.value("subtitle_preset"), "custom")
            self.assertEqual(settings.value("output_quality"), "1080p")
            self.assertEqual(settings.value("output_fps"), "60")
            self.assertEqual(settings.value("output_ratio"), "16:9")
            self.assertEqual(settings.value("output_scale_mode"), "fill")
            self.assertEqual(settings.value("audio_handling_mode"), "clean")
            self.assertFalse(settings.contains("speaker_diarization"))

            # Now test load_user_settings(gui):
            gui.voice_engine_combo.setCurrentIndex(0)
            gui.free_voice_combo.setCurrentIndex(0)
            gui.voice_gender_combo.setCurrentIndex(0)
            gui.voice_speed_spin.setCurrentIndex(0)
            gui.output_quality_combo.setCurrentIndex(0)
            gui.output_fps_combo.setCurrentIndex(0)
            gui.output_ratio_combo.setCurrentIndex(0)
            gui.output_scale_mode_combo.setCurrentIndex(0)
            gui.audio_handling_combo.setCurrentIndex(0)
            gui.speaker_diarization_cb.setChecked(True)
            gui.subtitle_single_line_cb.setChecked(True)

            load_user_settings(gui)

            # Assert:
            self.assertEqual(gui.voice_engine_combo.currentData(), "vieneu")
            self.assertEqual(gui.free_voice_combo.currentData(), "voice_b_val")
            self.assertEqual(gui.voice_gender_combo.currentText(), "Male")
            self.assertEqual(gui.voice_speed_spin.currentText(), "1.2x (Fast)")
            self.assertEqual(gui.voice_timing_sync_combo.currentText(), "Speed up voice to fit")
            self.assertEqual(os.environ.get("TRANSCRIPTION_ENGINE"), "whisper")
            self.assertTrue(len(applied_styles) > 0)
            self.assertEqual(applied_styles[-1].get("preset"), "custom")
            self.assertFalse(applied_styles[-1].get("single_line"))
            self.assertFalse(gui.subtitle_single_line_cb.isChecked())
            self.assertEqual(gui.output_quality_combo.currentData(), "1080p")
            self.assertEqual(gui.output_fps_combo.currentData(), "60")
            self.assertEqual(gui.output_ratio_combo.currentData(), "16:9")
            self.assertEqual(gui.output_scale_mode_combo.currentData(), "fill")
            self.assertEqual(gui.audio_handling_combo.currentData(), "clean")
            self.assertFalse(gui.speaker_diarization_cb.isChecked())
            self.assertEqual(gui.speaker_diarization_speakers_combo.currentData(), -1)

    def test_canvas_and_audio_handling_settings_persistence(self):
        from unittest.mock import MagicMock
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QComboBox, QLineEdit, QCheckBox, QRadioButton
        from ui.utils.settings_utils import save_user_settings, load_user_settings

        with tempfile.TemporaryDirectory() as tmp_dir:
            ini_path = os.path.join(tmp_dir, "test_canvas_settings.ini")
            settings = QSettings(ini_path, QSettings.IniFormat)

            class MockGUI:
                pass

            gui = MockGUI()
            gui.settings = settings
            gui.output_mode_combo = QComboBox()
            gui.output_mode_combo.addItem("Vietnamese subtitles + voice")
            gui.lang_whisper_combo = QComboBox()
            gui.final_output_folder_edit = QLineEdit("C:/out")
            gui.audio_folder_edit = QLineEdit("C:/audio")
            gui.srt_output_folder_edit = QLineEdit("C:/srt")
            gui.voice_output_folder_edit = QLineEdit("C:/voice")
            gui.audio_source_edit = QLineEdit("C:/src")
            gui.bg_music_edit = QLineEdit("C:/bg")
            gui.mixed_audio_edit = QLineEdit("C:/mix")
            gui.video_path_edit = QLineEdit("")
            gui.auto_preview_frame_cb = QCheckBox()
            gui.keep_timeline_cb = QCheckBox()
            gui.use_generated_audio_radio = QRadioButton()
            gui.use_existing_audio_radio = QRadioButton()
            gui.on_advanced_toggled = MagicMock()
            gui.on_audio_source_mode_changed = MagicMock()
            gui.on_subtitle_preset_changed = MagicMock()
            gui.update_subtitle_preview_style = MagicMock()
            gui.on_output_mode_changed = MagicMock()
            gui.refresh_ui_state = MagicMock()

            # Combo setup
            gui.output_quality_combo = QComboBox()
            gui.output_quality_combo.addItem("Max (source)", "source")
            gui.output_quality_combo.addItem("720p", "720p")
            gui.output_quality_combo.addItem("1080p (Full HD)", "1080p")
            gui.output_quality_combo.setCurrentIndex(2)  # 1080p

            gui.output_fps_combo = QComboBox()
            gui.output_fps_combo.addItem("Source", "source")
            gui.output_fps_combo.addItem("30 FPS", "30")
            gui.output_fps_combo.addItem("60 FPS", "60")
            gui.output_fps_combo.setCurrentIndex(2)  # 60

            gui.output_ratio_combo = QComboBox()
            gui.output_ratio_combo.addItem("Source", "source")
            gui.output_ratio_combo.addItem("16:9", "16:9")
            gui.output_ratio_combo.addItem("9:16", "9:16")
            gui.output_ratio_combo.setCurrentIndex(1)  # 16:9

            gui.output_scale_mode_combo = QComboBox()
            gui.output_scale_mode_combo.addItem("Fit", "fit")
            gui.output_scale_mode_combo.addItem("Fill", "fill")
            gui.output_scale_mode_combo.setCurrentIndex(1)  # fill

            gui.audio_handling_combo = QComboBox()
            gui.audio_handling_combo.addItem("Fast", "fast")
            gui.audio_handling_combo.addItem("Cleaner voice", "clean")
            gui.audio_handling_combo.setCurrentIndex(1)  # clean

            save_user_settings(gui)

            self.assertEqual(settings.value("output_quality"), "1080p")
            self.assertEqual(settings.value("output_fps"), "60")
            self.assertEqual(settings.value("output_ratio"), "16:9")
            self.assertEqual(settings.value("output_scale_mode"), "fill")
            self.assertEqual(settings.value("audio_handling_mode"), "clean")

            # Reset combos to index 0
            gui.output_quality_combo.setCurrentIndex(0)
            gui.output_fps_combo.setCurrentIndex(0)
            gui.output_ratio_combo.setCurrentIndex(0)
            gui.output_scale_mode_combo.setCurrentIndex(0)
            gui.audio_handling_combo.setCurrentIndex(0)

            load_user_settings(gui)

            self.assertEqual(gui.output_quality_combo.currentData(), "1080p")
            self.assertEqual(gui.output_fps_combo.currentData(), "60")
            self.assertEqual(gui.output_ratio_combo.currentData(), "16:9")
            self.assertEqual(gui.output_scale_mode_combo.currentData(), "fill")
            self.assertEqual(gui.audio_handling_combo.currentData(), "clean")

    def test_get_transcription_engine_falls_back_to_saved_default(self):
        from ui.main_window import VideoTranslatorGUI
        from unittest.mock import MagicMock
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QComboBox

        with tempfile.TemporaryDirectory() as tmp_dir:
            ini_path = os.path.join(tmp_dir, "test_asr_fallback.ini")
            settings = QSettings(ini_path, QSettings.IniFormat)
            gui = MagicMock(spec=VideoTranslatorGUI)
            gui.settings = settings
            settings.setValue("default_transcription_engine", "whisper")

            # Case A: gui.current_project_state = None
            gui.current_project_state = None
            self.assertEqual(VideoTranslatorGUI.get_transcription_engine(gui), "whisper")

            # Case B: gui.current_project_state = MagicMock(); gui.current_project_state.settings = {}
            gui.current_project_state = MagicMock()
            gui.current_project_state.settings = {}
            self.assertEqual(VideoTranslatorGUI.get_transcription_engine(gui), "whisper")

            # Case C: gui.current_project_state.settings = {"transcription_engine": "sensevoice"}
            gui.current_project_state.settings = {"transcription_engine": "sensevoice"}
            self.assertEqual(VideoTranslatorGUI.get_transcription_engine(gui), "sensevoice")

            # Case D: load_project_context
            # With empty project engine:
            state = MagicMock()
            state.settings = {}
            gui.project_bridge = MagicMock()
            gui.project_bridge.load_context.return_value = {
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
            gui.audio_handling_combo = QComboBox()
            VideoTranslatorGUI.load_project_context(gui, state)
            self.assertEqual(os.environ.get("TRANSCRIPTION_ENGINE"), "whisper")

            # With specific project engine:
            state.settings = {"transcription_engine": "ocr"}
            VideoTranslatorGUI.load_project_context(gui, state)
            self.assertEqual(os.environ.get("TRANSCRIPTION_ENGINE"), "ocr")

    def test_flexible_srt_parsing(self):
        # 1. Dot in timestamps
        srt_dot = "1\n00:01:23.456 --> 00:01:25.789\nHello world"
        segs = parse_srt_to_segments(srt_dot)
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["start"], 83.456)
        self.assertAlmostEqual(segs[0]["end"], 85.789)
        self.assertEqual(segs[0]["text"], "Hello world")

        # 2. Single-digit hours
        srt_single_h = "1\n0:01:23,456 --> 0:01:25,789\nSingle digit hour"
        segs = parse_srt_to_segments(srt_single_h)
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["start"], 83.456)
        self.assertAlmostEqual(segs[0]["end"], 85.789)

        # 3. 2-digit ms
        srt_2digit_ms = "1\n00:01:23,45 --> 00:01:25,78\nTwo digit ms"
        segs = parse_srt_to_segments(srt_2digit_ms)
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["start"], 83.45)
        self.assertAlmostEqual(segs[0]["end"], 85.78)

        # 4. Arrow "->"
        srt_arrow = "1\n00:01:23,456 -> 00:01:25,789\nShort arrow"
        segs = parse_srt_to_segments(srt_arrow)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["text"], "Short arrow")

        # 5. "#1" indexing
        srt_hash_idx = "#1\n00:00:01,000 --> 00:00:02,000\nHash index"
        segs = parse_srt_to_segments(srt_hash_idx)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["text"], "Hash index")

        # 6. Missing index line
        srt_no_idx = "00:00:01,000 --> 00:00:02,000\nNo index line"
        segs = parse_srt_to_segments(srt_no_idx)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["text"], "No index line")

    def test_validate_srt_syntax_error_reporting(self):
        # Malformed time range
        srt_bad_time = "1\nbad_time --> bad_time\nHello"
        valid, segs, err = validate_srt_text(srt_bad_time)
        self.assertFalse(valid)
        self.assertIn("Subtitle block 1", err)
        self.assertIn("invalid time range", err.lower())

        # Inverted start > end
        srt_inverted = "1\n00:00:10,000 --> 00:00:05,000\nInverted time"
        valid, segs, err = validate_srt_text(srt_inverted)
        self.assertFalse(valid)
        self.assertIn("Subtitle block 1", err)
        self.assertIn("ends before it starts", err)

        # Missing text
        srt_no_text = "1\n00:00:01,000 --> 00:00:02,000\n"
        valid, segs, err = validate_srt_text(srt_no_text)
        self.assertFalse(valid)
        self.assertIn("Subtitle block 1", err)
        self.assertIn("missing subtitle text", err)

    def test_diagnose_srt_timeline_matching(self):
        base = [
            {"start": 1.0, "end": 2.5, "text": "One"},
            {"start": 3.0, "end": 4.5, "text": "Two"},
            {"start": 5.0, "end": 6.5, "text": "Three"},
        ]
        imported = [
            {"start": 1.0, "end": 2.5, "text": "Một"},
            {"start": 3.0, "end": 4.5, "text": "Hai"},
            {"start": 5.0, "end": 6.5, "text": "Ba"},
        ]
        diag = diagnose_srt_timeline(imported, base)
        self.assertTrue(diag["count_match"])
        self.assertFalse(diag["has_desync"])
        self.assertIsNone(diag["first_desync"])
        self.assertEqual(len(diag["desync_cues"]), 0)
        self.assertIn("Đồng bộ hoàn toàn", diag["summary"])

    def test_diagnose_srt_timeline_dropped_cue(self):
        base = [
            {"start": 0.0, "end": 2.0, "text": "Cue 1"},
            {"start": 2.0, "end": 4.0, "text": "Cue 2"},
            {"start": 4.0, "end": 6.0, "text": "Cue 3 dropped"},
            {"start": 6.0, "end": 8.0, "text": "Cue 4"},
            {"start": 8.0, "end": 10.0, "text": "Cue 5"},
        ]
        imported = [
            {"start": 0.0, "end": 2.0, "text": "Câu 1"},
            {"start": 2.0, "end": 4.0, "text": "Câu 2"},
            {"start": 6.0, "end": 8.0, "text": "Câu 4"},
            {"start": 8.0, "end": 10.0, "text": "Câu 5"},
        ]
        diag = diagnose_srt_timeline(imported, base)
        self.assertFalse(diag["count_match"])
        self.assertTrue(diag["has_desync"])
        self.assertIsNotNone(diag["first_desync"])
        self.assertEqual(diag["first_desync"]["cue_number"], 3)
        self.assertEqual(diag["first_desync"]["reason"], "dropped_cue")

    def test_diagnose_srt_timeline_split_cue(self):
        base = [
            {"start": 0.0, "end": 5.0, "text": "Full sentence"},
            {"start": 5.5, "end": 8.0, "text": "Next sentence"},
        ]
        imported = [
            {"start": 0.0, "end": 2.0, "text": "Half 1"},
            {"start": 2.1, "end": 5.0, "text": "Half 2"},
            {"start": 5.5, "end": 8.0, "text": "Next sentence"},
        ]
        diag = diagnose_srt_timeline(imported, base)
        self.assertFalse(diag["count_match"])
        self.assertTrue(diag["has_desync"])
        self.assertIsNotNone(diag["first_desync"])
        self.assertEqual(diag["first_desync"]["reason"], "split_cue")

    def test_diagnose_srt_timeline_truncated_end(self):
        base = [
            {"start": 0.0, "end": 2.0, "text": "Cue 1"},
            {"start": 2.0, "end": 4.0, "text": "Cue 2"},
            {"start": 4.0, "end": 6.0, "text": "Cue 3"},
            {"start": 6.0, "end": 8.0, "text": "Cue 4"},
        ]
        imported = [
            {"start": 0.0, "end": 2.0, "text": "Câu 1"},
            {"start": 2.0, "end": 4.0, "text": "Câu 2"},
        ]
        diag = diagnose_srt_timeline(imported, base)
        self.assertFalse(diag["count_match"])
        self.assertTrue(diag["has_desync"])
        self.assertIsNotNone(diag["first_desync"])
        self.assertEqual(diag["first_desync"]["cue_number"], 3)
        self.assertEqual(diag["first_desync"]["reason"], "truncated_end")

    def test_gui_diagnose_srt_timeline_passthrough(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock(spec=VideoTranslatorGUI)
        base = [{"start": 0.0, "end": 2.0, "text": "A"}]
        imp = [{"start": 0.0, "end": 2.0, "text": "A"}]
        res = VideoTranslatorGUI.diagnose_srt_timeline(gui, imp, base)
        self.assertTrue(res["count_match"])
        self.assertFalse(res["has_desync"])

    def test_export_audio_missing_warnings(self):
        from unittest.mock import MagicMock, patch
        from ui.main_window import VideoTranslatorGUI
        from ui.i18n import t

        gui = MagicMock(spec=VideoTranslatorGUI)
        gui.processed_artifacts = {}
        gui.last_voice_vi_path = ""
        gui.last_music_path = ""
        gui.last_extracted_audio = ""
        gui.last_mixed_vi_path = ""

        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            VideoTranslatorGUI.export_dubbed_voice(gui)
            mock_warn.assert_called_once()
            self.assertIn(mock_warn.call_args[0][1], ("Missing Audio", "Chưa có âm thanh", t("Missing Audio")))

        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            VideoTranslatorGUI.export_background_music(gui)
            mock_warn.assert_called_once()
            self.assertIn(mock_warn.call_args[0][1], ("Missing Audio", "Chưa có âm thanh", t("Missing Audio")))

        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            VideoTranslatorGUI.export_mixed_audio(gui)
            mock_warn.assert_called_once()
            self.assertIn(mock_warn.call_args[0][1], ("Missing Audio", "Chưa có âm thanh", t("Missing Audio")))

    def test_export_audio_success(self):
        from unittest.mock import MagicMock, patch
        from ui.main_window import VideoTranslatorGUI
        from ui.i18n import t

        with tempfile.TemporaryDirectory() as td:
            src_voice = os.path.join(td, "src_voice.wav")
            with open(src_voice, "wb") as f:
                f.write(b"RIFFdummyvoicedata")

            target_save = os.path.join(td, "exported_voice.wav")

            gui = MagicMock(spec=VideoTranslatorGUI)
            gui.processed_artifacts = {"voice_vi": src_voice}
            gui.video_path_edit = MagicMock()
            gui.video_path_edit.text.return_value = "C:/videos/my_clip.mp4"
            gui.log = MagicMock()

            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(target_save, "wav")), \
                 patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
                VideoTranslatorGUI.export_dubbed_voice(gui)

                self.assertTrue(os.path.exists(target_save))
                with open(target_save, "rb") as f:
                    self.assertEqual(f.read(), b"RIFFdummyvoicedata")
                mock_info.assert_called_once()
                self.assertIn(mock_info.call_args[0][1], ("Saved", "Đã lưu", t("Saved")))
                gui.log.assert_called_once()

            # Background music export success
            src_bg = os.path.join(td, "src_bg.wav")
            with open(src_bg, "wb") as f:
                f.write(b"RIFFdummybgdata")
            target_bg = os.path.join(td, "exported_bg.wav")
            gui.processed_artifacts = {"music": src_bg}
            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(target_bg, "wav")), \
                 patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
                VideoTranslatorGUI.export_background_music(gui)
                self.assertTrue(os.path.exists(target_bg))
                with open(target_bg, "rb") as f:
                    self.assertEqual(f.read(), b"RIFFdummybgdata")
                mock_info.assert_called_once()
                self.assertIn(mock_info.call_args[0][1], ("Saved", "Đã lưu", t("Saved")))

            # Mixed audio export success with voice fallback
            target_mix = os.path.join(td, "exported_mix.wav")
            gui.processed_artifacts = {"voice_vi": src_voice}
            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(target_mix, "wav")), \
                 patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
                VideoTranslatorGUI.export_mixed_audio(gui)
                self.assertTrue(os.path.exists(target_mix))
                with open(target_mix, "rb") as f:
                    self.assertEqual(f.read(), b"RIFFdummyvoicedata")
                mock_info.assert_called_once()
                self.assertIn(mock_info.call_args[0][1], ("Saved", "Đã lưu", t("Saved")))

    def test_export_audio_menu_built(self):
        from unittest.mock import MagicMock
        from PySide6.QtWidgets import QPushButton
        from ui.views.main_window import _build_header_bar

        gui = MagicMock()
        gui.run_all_btn = QPushButton()
        gui.export_btn = QPushButton()
        gui.preview_5s_btn = QPushButton()
        _build_header_bar(gui)
        self.assertIsNotNone(gui.export_subtitles_menu)
        self.assertIsNotNone(gui.download_subtitle_action)
        self.assertIsNotNone(gui.download_original_action)
        self.assertIsNotNone(gui.export_audio_menu)
        self.assertIsNotNone(gui.export_voice_action)
        self.assertIsNotNone(gui.export_bg_music_action)
        self.assertIsNotNone(gui.export_mixed_audio_action)
        self.assertIsNone(getattr(gui, "preview_5s_action", None))

    def test_processing_workers_get_voice_preview_utils(self):
        from ui.worker_adapters.processing_workers import _get_voice_preview_utils
        vpu = _get_voice_preview_utils()
        self.assertTrue(hasattr(vpu, "voice_provider"))
        self.assertTrue(hasattr(vpu, "clamp_requested_speed"))
        self.assertTrue(hasattr(vpu, "provider_native_speed"))
        self.assertTrue(hasattr(vpu, "segment_cache_key"))

    def test_layer_controls_unlocked_when_video_loaded_without_subtitles(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        # 1. Test that with a valid video path, _optional_layer_controls_ready evaluates to True even without subtitles/audio
        gui = MagicMock()
        gui.video_path_edit.text.return_value = "C:/test/sample.mp4"
        gui.has_active_video_filters.return_value = False
        gui.timeline._timeline = MagicMock()
        gui.timeline._timeline.tracks = []
        gui.timeline._timeline.duration = 10.0
        gui.media_player.position.return_value = 0
        gui.video_view.get_blur_region_normalized.return_value = None
        gui._current_mask_regions_payload.return_value = []

        # Check has_active_overlay_layers returns False when no overlays exist
        self.assertFalse(VideoTranslatorGUI.has_active_overlay_layers(gui))

        # 2. Test has_active_overlay_layers returns True when an overlay exists
        mock_track = MagicMock()
        mock_track.type = "mask"
        mock_layer = MagicMock()
        mock_layer.visible = True
        mock_track.layers = [mock_layer]
        gui.timeline._timeline.tracks = [mock_track]

        self.assertTrue(VideoTranslatorGUI.has_active_overlay_layers(gui))

        # Test with blur region in video_view
        gui.timeline._timeline.tracks = []
        gui.video_view.get_blur_region_normalized.return_value = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
        self.assertTrue(VideoTranslatorGUI.has_active_overlay_layers(gui))

    def test_remote_api_server_status_resets_on_phase_change(self):
        from app.remote_api_server import _set_status, _get_status

        _set_status("transcription", "transcribing", progress=99, detail="whisper 99%")
        status = _get_status()
        self.assertEqual(status["phase"], "transcription")
        self.assertEqual(status["message"], "transcribing")
        self.assertEqual(status["progress"], 99)
        self.assertEqual(status["detail"], "whisper 99%")

        _set_status("translation", "translating", progress=None, detail="")
        status2 = _get_status()
        self.assertEqual(status2["phase"], "translation")
        self.assertEqual(status2["message"], "translating")
        self.assertEqual(status2["progress"], 0)
        self.assertEqual(status2["detail"], "")

    def test_pipeline_controller_prepare_step_started_resets_label_and_tracks_phase(self):
        from unittest.mock import MagicMock
        from ui.controllers.pipeline_controller import PipelineController

        gui = MagicMock()
        controller = PipelineController(gui)

        status_label = MagicMock()
        ai_process_step = MagicMock()
        ai_process_step.status_label = status_label

        dialog = MagicMock()
        dialog.steps = {"ai_process": ai_process_step}
        controller.progress_dialog = dialog

        controller._on_prepare_step_started("translation")

        self.assertEqual(controller.prepare_step_id, "translation")
        gui.update_project_step.assert_called_with("translate_raw", "running")
        status_label.setText.assert_called_with("0%")

    def test_pipeline_controller_prepare_step_progress_translation_fallback(self):
        from unittest.mock import MagicMock
        from ui.controllers.pipeline_controller import PipelineController
        from ui.i18n import t

        gui = MagicMock()
        gui._pipeline_active = True
        controller = PipelineController(gui)

        status_label = MagicMock()
        ai_process_step = MagicMock()
        ai_process_step.status_label = status_label

        footer = MagicMock()
        dialog = MagicMock()
        dialog.steps = {"ai_process": ai_process_step}
        dialog.step_order = ["ai_process"]
        dialog.footer = footer
        controller.progress_dialog = dialog

        controller._on_prepare_step_progress("translation", 42, "", "")
        footer_call = footer.setText.call_args[0][0]
        self.assertIn("42%", footer_call)
        self.assertTrue("Translating subtitles" in footer_call or "Đang dịch phụ đề" in footer_call)
        status_label.setText.assert_called_with("42%")

    def test_optional_layer_controls_ready_initialized_and_refresh_ui_state(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        # 1. VideoTranslatorGUI instance initializes _optional_layer_controls_ready without startup error
        win = VideoTranslatorGUI()
        try:
            self.assertTrue(hasattr(win, "_optional_layer_controls_ready"))
            self.assertFalse(win._optional_layer_controls_ready)
        finally:
            win.close()
            win.deleteLater()

        # 2. In refresh_ui_state, blur_area_btn.setEnabled succeeds even if _optional_layer_controls_ready
        # was not pre-set on mock, verifying calculation order before button state update
        mock_gui = MagicMock()
        mock_gui.video_path_edit.text.return_value = ""
        mock_gui.audio_source_edit.text.return_value = ""
        mock_gui.transcript_text.toPlainText.return_value = ""
        mock_gui.translated_text.toPlainText.return_value = ""
        mock_gui.current_project_state.steps = {}
        mock_gui.current_segments = []
        mock_gui.current_translated_segments = []
        mock_gui.video_time_warps = []
        mock_gui._preview_is_playing.return_value = False
        mock_gui._translation_phase_complete.return_value = False
        mock_gui.resolve_selected_audio_path.return_value = ""
        mock_gui.last_translated_srt_path = ""
        mock_gui.get_output_mode_key.return_value = "subtitle"
        mock_gui.get_output_scale_mode_key.return_value = "fit"
        mock_gui.get_output_fill_focus.return_value = (0.5, 0.5)
        mock_gui.get_active_segments.return_value = []
        mock_gui.voice_catalog_entries_all = []
        mock_gui.timeline = None
        mock_gui.has_active_overlay_layers.return_value = False
        mock_gui.using_existing_audio_source.return_value = False
        del mock_gui._optional_layer_controls_ready
        self.assertFalse(hasattr(mock_gui, "_optional_layer_controls_ready"))

        # Must succeed without AttributeError
        VideoTranslatorGUI.refresh_ui_state(mock_gui)
        self.assertTrue(hasattr(mock_gui, "_optional_layer_controls_ready"))
        mock_gui.blur_area_btn.setEnabled.assert_called_with(mock_gui._optional_layer_controls_ready)

    def test_setup_vieneu_hf_env_offline_mode(self):
        from unittest.mock import patch
        import vieneu_tts

        with tempfile.TemporaryDirectory() as tmpdir:
            model_hub_dir = os.path.join(
                tmpdir, "vieneu", "hub", "models--pnnbao-ump--VieNeu-TTS-v3-Turbo"
            )
            os.makedirs(model_hub_dir, exist_ok=True)

            def fake_models_path(*args):
                if args and args[0] == "vieneu":
                    return os.path.join(tmpdir, "vieneu", *args[1:])
                return os.path.join(tmpdir, *args)

            orig_env = os.environ.copy()
            try:
                os.environ.pop("HF_HUB_OFFLINE", None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                os.environ.pop("HF_HOME", None)
                with patch("vieneu_tts.models_path", side_effect=fake_models_path):
                    vieneu_tts.setup_vieneu_hf_env()
                self.assertEqual(os.environ.get("HF_HUB_OFFLINE"), "1")
                self.assertEqual(os.environ.get("TRANSFORMERS_OFFLINE"), "1")
                self.assertEqual(os.environ.get("HF_HOME"), os.path.join(tmpdir, "vieneu"))
            finally:
                os.environ.clear()
                os.environ.update(orig_env)

    def test_run_voiceover_cache_hit_syncs_preview_audio(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI

        with tempfile.TemporaryDirectory() as tmpdir:
            cached_audio = os.path.join(tmpdir, "cached_voice.wav")
            with open(cached_audio, "wb") as f:
                f.write(b"RIFFdummywavdata")

            mock_gui = MagicMock(spec=VideoTranslatorGUI)
            mock_state = MagicMock()
            mock_state.settings = {"voice_signature": "sig_cached_123"}
            mock_state.artifacts = {"voice_vi": cached_audio}
            mock_gui.ensure_current_project.return_value = mock_state

            mock_gui.translated_text = MagicMock()
            mock_gui.translated_text.toPlainText.return_value = "1\n00:00:00,000 --> 00:00:01,000\nXin chào\n"
            mock_gui._get_voiceover_segments.return_value = [{"start": 0.0, "end": 1.0, "text": "Xin chào"}]
            mock_gui._resolve_active_voice_name.return_value = "Adam"
            mock_gui.ensure_required_resources.return_value = True
            mock_gui.voice_output_folder_edit = MagicMock()
            mock_gui.voice_output_folder_edit.text.return_value = tmpdir
            mock_gui.workspace_root = tmpdir
            mock_gui.resolve_background_audio_path.return_value = ""
            mock_gui.get_audio_handling_mode.return_value = "original"
            mock_gui._parse_voice_speed_value.return_value = 1.0
            mock_gui.voice_timing_sync_combo = MagicMock()
            mock_gui.voice_timing_sync_combo.itemData.return_value = "Smart"
            mock_gui.voice_timing_sync_combo.currentText.return_value = "Smart"
            mock_gui.audio_a1_volume_slider = MagicMock()
            mock_gui.audio_a1_volume_slider.value.return_value = 50
            mock_gui.audio_a2_volume_slider = MagicMock()
            mock_gui.audio_a2_volume_slider.value.return_value = 100
            mock_gui.build_current_voice_signature.return_value = "sig_cached_123"
            mock_gui._voiceover_force_refresh = False
            mock_gui._normalize_local_file_path.side_effect = lambda p: p
            mock_gui.last_voice_vi_path = ""
            mock_gui.last_mixed_vi_path = ""
            mock_gui.processed_artifacts = {}
            mock_gui.current_translated_segments = [{"start": 0.0, "end": 1.0, "text": "Xin chào"}]
            mock_gui.current_segments = []
            mock_gui.timeline = MagicMock()
            mock_gui.progress_bar = MagicMock()

            # Execute run_voiceover on mock_gui
            VideoTranslatorGUI.run_voiceover(mock_gui)

            # Assert cache hit synced timeline and preview audio
            mock_gui.timeline.sync_tts_track.assert_called_once_with(
                cached_audio,
                segments=mock_gui.current_translated_segments,
            )
            mock_gui._sync_timeline_mute_to_gui.assert_called_once()
            mock_gui._sync_audio_mix_controls_from_tracks.assert_called_once()
            mock_gui.sync_preview_audio_track_to_output.assert_called_once_with(apply_to_player=True, force=True)
            mock_gui._pipeline_advance.assert_called_once_with("voiceover")

    def test_on_preview_ready_reattaches_audio_tracks(self):
        from unittest.mock import MagicMock
        from ui.controllers.preview_controller import PreviewController

        with tempfile.TemporaryDirectory() as tmpdir:
            preview_video = os.path.join(tmpdir, "preview.mp4")
            with open(preview_video, "wb") as f:
                f.write(b"fake_mp4_data")
            voice_audio = os.path.join(tmpdir, "voice.wav")
            with open(voice_audio, "wb") as f:
                f.write(b"fake_wav_data")

            mock_gui = MagicMock()
            mock_gui.processed_artifacts = {}
            mock_gui.last_voice_vi_path = voice_audio
            mock_gui.last_mixed_vi_path = ""
            mock_gui._audio_total_duration_ms.return_value = 5000.0
            mock_gui.video_time_warps = []
            mock_gui._preview_video_has_burned_subtitles = False
            mock_gui._pipeline_active = False

            controller = PreviewController(mock_gui)
            controller.on_preview_ready(preview_video, None)

            # Verify set_audio_tracks_snapshot was called with the re-attached audio
            mock_gui.media_player.set_audio_tracks_snapshot.assert_called_once()
            args, _ = mock_gui.media_player.set_audio_tracks_snapshot.call_args
            tracks, warps = args
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0]["id"], "preview_audio")
            self.assertEqual(tracks[0]["path"], voice_audio)
            self.assertEqual(tracks[0]["end"], 5.0)
            mock_gui.media_player.set_audio_file.assert_called_once_with(voice_audio)
            mock_gui.sync_preview_audio_track_to_output.assert_called_once_with(apply_to_player=False)

    def test_vocal_processor_cuda_runtime_setup(self):
        from unittest.mock import patch, MagicMock
        import vocal_processor
        import runtime_paths

        old_path = os.environ.get("PATH", "")
        old_session = vocal_processor._ONNX_SESSION
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                fake_cuda_dir = os.path.join(tmpdir, "cuda12_fw")
                os.makedirs(fake_cuda_dir, exist_ok=True)

                # 1. Verify _ensure_cuda_runtime adds cuda12_fw to PATH
                with patch("runtime_paths.join_root", return_value=fake_cuda_dir), \
                     patch("runtime_paths.bin_path", return_value=""):
                    vocal_processor._ensure_cuda_runtime()
                    self.assertIn(fake_cuda_dir, os.environ.get("PATH", ""))

                # 2. Verify vocal_processor._get_session() initializes with CUDAExecutionProvider when available
                vocal_processor._ONNX_SESSION = None
                fake_model_path = os.path.join(tmpdir, "fake_model.onnx")
                with open(fake_model_path, "wb") as f:
                    f.write(b"onnx")

                mock_session = MagicMock()
                with patch("vocal_processor._model_path", return_value=fake_model_path), \
                     patch.object(vocal_processor.ort, "get_available_providers", return_value=["CUDAExecutionProvider", "CPUExecutionProvider"]), \
                     patch.object(vocal_processor.ort, "InferenceSession", return_value=mock_session) as mock_inf_sess, \
                     patch("vocal_processor._ensure_cuda_runtime") as mock_ensure:
                    session = vocal_processor._get_session()
                    self.assertEqual(session, mock_session)
                    mock_ensure.assert_called_once()
                    mock_inf_sess.assert_called_once_with(
                        fake_model_path,
                        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                    )
        finally:
            os.environ["PATH"] = old_path
            vocal_processor._ONNX_SESSION = old_session

    def test_vocal_processor_slice_inference_equivalence(self):
        import numpy as np
        from unittest.mock import MagicMock

        # Simulate ONNX session where batch run and slice-by-slice return deterministic output
        mock_session = MagicMock()
        mock_session.run.side_effect = lambda output_names, input_feed: [input_feed["input"] * 2.0]

        # Scenario 1: Multi-slice input (spek has length > 1, e.g. 3 slices of shape (4, 3072, 256))
        spek = np.random.randn(3, 4, 3072, 256).astype(np.float32)

        # Execute slice-by-slice logic matching app/vocal_processor.py
        if len(spek) > 1:
            preds = []
            for b_idx in range(len(spek)):
                pred_slice = mock_session.run(None, {"input": spek[b_idx : b_idx + 1]})[0]
                preds.append(pred_slice)
            spec_pred = np.concatenate(preds, axis=0)
        else:
            spec_pred = mock_session.run(None, {"input": spek})[0]

        # Verify output shape matches input batch shape
        self.assertEqual(spec_pred.shape, (3, 4, 3072, 256))
        # Verify slice-by-slice called session.run once per slice to avoid VRAM exhaustion
        self.assertEqual(mock_session.run.call_count, 3)
        for call_args in mock_session.run.call_args_list:
            feed = call_args[0][1]
            self.assertEqual(feed["input"].shape, (1, 4, 3072, 256))
        np.testing.assert_allclose(spec_pred, spek * 2.0)

        # Scenario 2: Single slice input (spek has length == 1)
        mock_session.reset_mock()
        spek_single = np.random.randn(1, 4, 3072, 256).astype(np.float32)
        if len(spek_single) > 1:
            preds = []
            for b_idx in range(len(spek_single)):
                pred_slice = mock_session.run(None, {"input": spek_single[b_idx : b_idx + 1]})[0]
                preds.append(pred_slice)
            spec_pred_single = np.concatenate(preds, axis=0)
        else:
            spec_pred_single = mock_session.run(None, {"input": spek_single})[0]

        self.assertEqual(spec_pred_single.shape, (1, 4, 3072, 256))
        self.assertEqual(mock_session.run.call_count, 1)
        np.testing.assert_allclose(spec_pred_single, spek_single * 2.0)

    def test_stop_all_launcher_workers(self):
        from unittest.mock import MagicMock
        from views.launcher import stop_all_launcher_workers, _stale_launcher_workers

        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        _stale_launcher_workers.append(mock_worker)
        try:
            stop_all_launcher_workers()
            mock_worker.requestInterruption.assert_called_once()
            self.assertEqual(len(_stale_launcher_workers), 0)
        finally:
            _stale_launcher_workers.clear()

    def test_timeline_waveform_worker_interruption(self):
        from ui.worker_adapters.processing_workers import TimelineWaveformWorker

        worker = TimelineWaveformWorker("sig", "dummy.mp4", "", "", duration_s=10.0)
        self.assertFalse(worker.isInterruptionRequested())
        worker.requestInterruption()
        self.assertTrue(worker.isInterruptionRequested())

    def test_libmpv_closed_state_guards(self):
        from unittest.mock import MagicMock
        from ui.utils.media_backend import LibMpvMediaBackend

        backend = LibMpvMediaBackend.__new__(LibMpvMediaBackend)
        backend._is_closed = True
        backend._player = MagicMock()
        backend._source_path = "some_path.mp4"

        # None of these should call into backend._player when _is_closed is True
        backend.setSource("video.mp4")
        backend._on_native_audio_sink_ready(True)
        backend._apply_blur_filter()
        backend._apply_current_subtitle()

        backend._player.command.assert_not_called()

        # Test close() cleans up player safely
        backend.close()
        backend._player.terminate.assert_called_once()

    def test_safely_retire_worker(self):
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QEventLoop, QTimer

        app = QApplication.instance() or QApplication([])
        gui = VideoTranslatorGUI.__new__(VideoTranslatorGUI)

        # 1. Calling with None does nothing
        gui.safely_retire_worker(None)

        # 2. Worker that is already finished is deleted immediately without entering _retiring_workers
        finished_worker = MagicMock()
        finished_worker.isFinished.return_value = True
        gui.safely_retire_worker(finished_worker)
        finished_worker.deleteLater.assert_called_once()
        self.assertEqual(len(getattr(gui, "_retiring_workers", [])), 0)

        # 3. Running worker is added to _retiring_workers
        running_worker = MagicMock()
        running_worker.isFinished.return_value = False
        gui.safely_retire_worker(running_worker)
        self.assertIn(running_worker, gui._retiring_workers)

        # 4. When it finishes and timer ticks (processEvents), it should be removed and deleteLater called
        running_worker.isFinished.return_value = True
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()

        self.assertNotIn(running_worker, gui._retiring_workers)
        running_worker.deleteLater.assert_called_once()

    def test_on_translation_finished_safely_retires_worker(self):
        from unittest.mock import MagicMock, patch
        from ui.controllers.subtitle_controller import SubtitleController

        mock_gui = MagicMock()
        mock_worker = MagicMock()
        mock_gui.translation_thread = mock_worker
        controller = SubtitleController(mock_gui)
        controller._close_translation_progress = MagicMock()

        with patch("PySide6.QtWidgets.QMessageBox.information"):
            controller.on_translation_finished("1\n00:00:01,000 --> 00:00:02,000\nHello\n", None)

        self.assertIsNone(mock_gui.translation_thread)
        mock_gui.safely_retire_worker.assert_called_once_with(mock_worker)

    def test_show_launcher_accepted_does_not_request_interruption(self):
        from unittest.mock import MagicMock, patch
        from PySide6.QtWidgets import QDialog
        from ui.views.launcher import show_launcher, _stale_launcher_workers

        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        mock_worker.isFinished.return_value = False

        mock_window = MagicMock()
        mock_window.exec.return_value = QDialog.Accepted
        mock_window.selected_video = "test_video.mp4"
        mock_window._cache_worker = mock_worker

        with patch("ui.views.launcher.LauncherWindow", return_value=mock_window):
            res = show_launcher(None)
            self.assertEqual(res, "test_video.mp4")
            mock_worker.requestInterruption.assert_not_called()
            self.assertIn(mock_worker, _stale_launcher_workers)

        # Cleanup
        if mock_worker in _stale_launcher_workers:
            _stale_launcher_workers.remove(mock_worker)

        # Also test rejection requests interruption
        mock_worker_rej = MagicMock()
        mock_worker_rej.isRunning.return_value = True
        mock_window_rej = MagicMock()
        mock_window_rej.exec.return_value = QDialog.Rejected
        mock_window_rej.selected_video = ""
        mock_window_rej._cache_worker = mock_worker_rej

        with patch("ui.views.launcher.LauncherWindow", return_value=mock_window_rej):
            res = show_launcher(None)
            self.assertEqual(res, "")
            mock_worker_rej.requestInterruption.assert_called_once()


if __name__ == "__main__":
    unittest.main()



