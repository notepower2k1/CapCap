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

        # Check light bg (original dark silhouette)
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
            any(r < 100 and g < 100 and b < 100 for r, g, b, a in dark_pixels),
            "Light background icon should remain dark silhouette",
        )

        # Check default auto-detection
        icon_auto = build_contrasting_window_icon(ico_path)
        self.assertGreaterEqual(len(icon_auto.availableSizes()), 4)

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


if __name__ == "__main__":
    unittest.main()
