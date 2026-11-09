import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "ui"), str(ROOT / "app"), str(ROOT)]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from video_filter_chain import build_video_filter_chain, build_video_color_chain, build_video_lut_chain
from runtime_paths import bin_path
from ui.utils.media_backend import _realtime_color_graph
from PySide6.QtWidgets import QApplication
from ui.main_window import VideoTranslatorGUI
from services.project_service import ProjectService


class VideoMirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app._capcap_test_mode = True

    def test_mirror_only_is_active_and_off_is_identity(self):
        self.assertEqual(build_video_filter_chain({"mirror_horizontal": True}), "hflip")
        self.assertEqual(build_video_color_chain({"mirror_horizontal": True}), "hflip")
        self.assertEqual(build_video_lut_chain({"mirror_horizontal": True}), "")
        for state in ({}, {"mirror_horizontal": False}, {"mirror_horizontal": "false"}):
            self.assertEqual(build_video_filter_chain(state), "")
            self.assertEqual(_realtime_color_graph(state), "")

    def test_mirror_precedes_color_and_overlay_input(self):
        state = {"mirror_horizontal": True, "final": {"brightness": 10}}
        self.assertTrue(build_video_color_chain(state).startswith("hflip,eq="))
        graph = _realtime_color_graph(state)
        self.assertTrue(graph.startswith("hflip[color_stage0]"))
        self.assertIn("[color_stage0]eq=", graph)
        self.assertTrue(graph.endswith("[color_main]"))

    def test_native_ffmpeg_preview_and_export_match_pixel_for_pixel(self):
        ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
        if not Path(ffmpeg).is_file():
            self.skipTest("FFmpeg unavailable")
        # Asymmetric 4x2 RGB frame: flip each row, without altering its pixels.
        row = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 200, 100, 20])
        original = row * 2
        expected = b"".join(row[i:i+3] for i in (9, 6, 3, 0)) * 2
        outputs = []
        for graph, mapped in ((build_video_color_chain({"mirror_horizontal": True}), False),
                              (_realtime_color_graph({"mirror_horizontal": True}), True)):
            command = [ffmpeg, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "4x2", "-i", "pipe:0"]
            command += ["-filter_complex", graph, "-map", "[color_main]"] if mapped else ["-vf", graph]
            command += ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
            outputs.append(subprocess.run(command, input=original, capture_output=True, check=True, timeout=20).stdout)
        self.assertEqual(outputs, [expected, expected])

    def test_inspector_toggle_reset_persistence_and_project_switch(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("ui.main_window.QTimer.singleShot"), \
                patch.object(VideoTranslatorGUI, "_preload_active_voice_if_needed"), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP disabled")):
            gui = VideoTranslatorGUI()
            try:
                video = Path(folder) / "source.mp4"
                video.touch()
                gui.project_service = ProjectService(folder)
                gui.current_project_state = gui.project_service.ensure_project(str(video))
                gui.schedule_live_video_filter_preview = MagicMock()
                gui._video_filter_inspector_available = lambda: False
                gui._show_video_inspector_for_track(SimpleNamespace())
                checkbox = gui.video_inspector_mirror_cb
                self.assertTrue(checkbox.isEnabled())
                checkbox.setChecked(True)
                self.assertTrue(gui.get_video_filter_state()["active"])
                self.assertTrue(gui.get_video_filter_state()["mirror_horizontal"])
                gui.schedule_live_video_filter_preview.assert_called()
                self.assertTrue(gui.current_project_state.settings["video_mirror_horizontal"])
                saved = gui.project_service.load_project(gui.project_service.project_file(gui.current_project_state.project_root))
                self.assertTrue(saved.settings["video_mirror_horizontal"])
                gui._restore_video_mirror_state(SimpleNamespace(settings={}))
                self.assertFalse(checkbox.isChecked())
                gui._restore_video_mirror_state(saved)
                self.assertTrue(checkbox.isChecked())
                gui.reset_video_filters()
                self.assertFalse(gui.get_video_filter_state()["mirror_horizontal"])
                self.assertFalse(checkbox.isChecked())
                self.assertFalse(gui.current_project_state.settings["video_mirror_horizontal"])
            finally:
                gui.deleteLater()


if __name__ == "__main__":
    unittest.main()
