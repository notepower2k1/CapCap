import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "app", ROOT / "ui"):
    sys.path.insert(0, str(path))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import preview_processor
import video_processor
from runtime_paths import subprocess_text_kwargs
from workflows.export_workflow import ExportWorkflow

FFMPEG = ROOT / "bin" / "ffmpeg" / "ffmpeg.exe"


class TestExportWorkflowProgress(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(self.work.cleanup)
        self.workflow = ExportWorkflow(self.work.name)
        self.workflow.engine_runtime = MagicMock()
        self.workflow.engine_runtime.get_video_dimensions.return_value = (1920, 1080)
        self.workflow._ensure_subtitle_ass = MagicMock(return_value="sample.ass")
        self.workflow._extract_overlay_layers = MagicMock(return_value=([], [], [], []))
        self.workflow._build_text_layer_images = MagicMock(return_value=[])
        self.workflow._export_subtitle_video = MagicMock(return_value=True)

    def run_export(self, mode, progress):
        def mux(*args, **kwargs):
            kwargs["on_progress"](50, "Mux halfway")
            kwargs["on_progress"](100, "Mux complete")

        self.workflow.engine_runtime.mux_audio_for_preview.side_effect = mux
        with redirect_stdout(io.StringIO()):
            self.workflow.run(video_path="source.mp4", audio_path="voice.wav", output_path="output.mp4",
                              mode=mode, output_fps="30", output_quality="720p", video_quality="low",
                              on_progress=lambda pct, msg: progress.append(pct))

    def test_both_reports_mux_progress_and_changes_fps_only_in_final_render(self):
        progress = []
        self.run_export("both", progress)
        kwargs = self.workflow.engine_runtime.mux_audio_for_preview.call_args.kwargs
        self.assertIsNone(kwargs.get("output_fps"))
        self.assertIsNone(kwargs.get("target_width"))
        self.assertTrue(any(20 < value < 50 for value in progress), progress)
        self.assertEqual(progress[-1], 100)
        final = self.workflow._export_subtitle_video.call_args.kwargs
        self.assertEqual(final["output_fps"], 30)
        self.assertEqual(final["target_width"], 1280)
        self.assertEqual(final["video_quality"], "low")

    def test_voice_reports_mux_progress_with_and_without_visual_layers(self):
        for has_visuals in (False, True):
            with self.subTest(has_visuals=has_visuals):
                self.workflow.engine_runtime.reset_mock()
                self.workflow._export_subtitle_video.reset_mock()
                self.workflow._extract_overlay_layers.return_value = ([{"x": 0.1}] if has_visuals else [], [], [], [])
                progress = []
                self.run_export("voice", progress)
                kwargs = self.workflow.engine_runtime.mux_audio_for_preview.call_args.kwargs
                self.assertEqual(kwargs["output_fps"], None if has_visuals else 30)
                self.assertEqual(kwargs["target_width"], None if has_visuals else 1280)
                self.assertTrue(any(25 < value < (50 if has_visuals else 95) for value in progress), progress)
                self.assertEqual(progress[-1], 100)
                if has_visuals:
                    self.assertEqual(self.workflow._export_subtitle_video.call_args.kwargs["output_fps"], 30)
                else:
                    self.workflow._export_subtitle_video.assert_not_called()


@unittest.skipUnless(FFMPEG.is_file(), "Bundled FFmpeg integration")
class TestNativeMuxProgress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        cls.folder = Path(cls.work.name).resolve()
        assert cls.folder.is_relative_to(ROOT / "temp")
        cls.video = cls.folder / "source.mp4"
        cls.audio = cls.folder / "voice.wav"
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
                        "-t", "4", "-map", "0:v", "-c:v", "libx264", "-preset", "ultrafast", str(cls.video),
                        "-t", "4", "-map", "1:a", "-c:a", "pcm_s16le", str(cls.audio)],
                       check=True, capture_output=True, timeout=15, **subprocess_text_kwargs())

    @classmethod
    def tearDownClass(cls):
        assert cls.folder.is_relative_to(ROOT / "temp")
        cls.work.cleanup()

    def assert_output(self, output, fps):
        probe = subprocess.run([str(FFMPEG.with_name("ffprobe.exe")), "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(output)],
                               check=True, capture_output=True, **subprocess_text_kwargs())
        self.assertEqual(Fraction(probe.stdout.strip()), fps)
        self.assertAlmostEqual(video_processor.get_video_duration(str(output)), 4.0, delta=0.15)

    def test_copy_mux_reports_live_progress_and_preserves_fps(self):
        events = []
        commands = []
        run = video_processor._run_ffmpeg_command_with_progress

        def paced_run(command, **kwargs):
            commands.append(command)
            cmd = list(command)
            index = cmd.index("-i")
            cmd[index:index] = ["-re"]
            return run(cmd, **kwargs)

        output = self.folder / "copied.mp4"
        with patch("video_processor._run_ffmpeg_command_with_progress", side_effect=paced_run):
            preview_processor.mux_audio_into_video_for_preview(
                str(self.video), str(self.audio), str(output),
                on_progress=lambda pct, msg: events.append((pct, msg)),
            )
        self.assertIn("copy", commands[0])
        self.assertGreaterEqual(len([pct for pct, msg in events if 0 < pct < 99]), 2, events)
        self.assertEqual(events[-1][0], 100)
        self.assert_output(output, 12)

    def test_failed_gpu_encode_retries_on_cpu_with_live_progress(self):
        events = []
        output = self.folder / "fallback.mp4"
        with patch("preview_processor._preferred_h264_encoder_args",
                   return_value=["-c:v", "h264_nvenc", "-preset", "invalid_preset"]), \
             redirect_stdout(io.StringIO()):
            preview_processor.mux_audio_into_video_for_preview(
                str(self.video), str(self.audio), str(output), output_fps=6, video_quality="very_high",
                on_progress=lambda pct, msg: events.append((pct, msg)),
            )
        self.assertTrue(any("retrying on CPU" in message for pct, message in events), events)
        self.assertTrue(any(pct < 100 and "Encoding video" in message for pct, message in events), events)
        self.assertEqual(events[-1][0], 100)
        self.assert_output(output, 6)

    def test_failed_cpu_encode_raises_error_without_reporting_complete(self):
        events = []
        with patch("preview_processor._preferred_h264_encoder_args", return_value=["-c:v", "libx264"]):
            with self.assertRaises(RuntimeError):
                preview_processor._run_ffmpeg_with_h264_fallback(
                    str(FFMPEG), [str(FFMPEG), "-this-option-does-not-exist"], str(self.folder / "failed.mp4"),
                    total_duration=4.0, on_progress=lambda pct, msg: events.append(pct),
                )
        self.assertNotIn(100, events)


class TestExportQualitySelection(unittest.TestCase):
    def test_dialog_keeps_two_profiles_and_migrates_removed_settings(self):
        from PySide6.QtWidgets import QApplication
        from ui.controllers.preview_controller import ExportSummaryDialog

        app = QApplication.instance() or QApplication([])
        for saved, expected in (("medium", "medium"), ("low", "low"), ("high", "medium"), ("very_high", "medium")):
            with self.subTest(saved=saved):
                settings = MagicMock()
                settings.value.return_value = saved
                with patch("ui.controllers.preview_controller.QSettings", return_value=settings):
                    dialog = ExportSummaryDialog(None, "Export test")
                    self.assertEqual([dialog.quality_combo.itemData(i) for i in range(dialog.quality_combo.count())],
                                     ["medium", "low"])
                    self.assertEqual(dialog.selected_quality, expected)
                    if saved in ("high", "very_high"):
                        settings.setValue.assert_called_once_with("export_video_quality", "medium")
                    dialog.quality_combo.setCurrentIndex(1)
                    dialog._on_start()
                    self.assertEqual(settings.setValue.call_args.args, ("export_video_quality", "low"))
                    dialog.deleteLater()
        app.processEvents()

    def test_legacy_profiles_use_balanced_encoding_on_cpu_and_gpu(self):
        for processor in (preview_processor, video_processor):
            for gpu in (False, True):
                with patch.object(processor, "_ffmpeg_supports_encoder", return_value=gpu):
                    balanced = processor._preferred_h264_encoder_args("unused.exe", video_quality="medium")
                    for legacy in ("high", "very_high", "veryhigh", "ultra", "max"):
                        with self.subTest(processor=processor.__name__, gpu=gpu, legacy=legacy):
                            self.assertEqual(processor._preferred_h264_encoder_args("unused.exe", video_quality=legacy), balanced)
                    low = processor._preferred_h264_encoder_args("unused.exe", video_quality="low")
                    self.assertNotEqual(low, balanced)
                    self.assertEqual(low[low.index("-preset") + 1], "p2" if gpu else "veryfast")
                    quality_flag = "-cq" if gpu else "-crf"
                    self.assertEqual(low[low.index(quality_flag) + 1], "28" if gpu else "26")


if __name__ == "__main__":
    unittest.main()
