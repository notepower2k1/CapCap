import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.workflows.export_workflow import ExportWorkflow
from ui.worker_adapters.preview_workers import PreviewMuxWorker, QuickPreviewWorker


class TestTimeWarpExportAndPreview(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="capcap_test_tw_")
        self.workspace_root = self.temp_dir

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("ui.worker_adapters.preview_workers.EngineRuntime")
    @patch("preview_processor.mux_audio_into_video_for_preview")
    @patch("preview_processor.apply_timewarp_to_video_clip")
    @patch("video_processor.get_video_duration", return_value=10.0)
    @patch("ui.worker_adapters.preview_workers.os.path.exists", return_value=True)
    def test_preview_mux_worker_warps_video_first_for_both_mode(
        self, mock_exists, mock_duration, mock_timewarp, mock_mux, mock_engine_cls
    ):
        mock_engine = MagicMock()
        mock_engine.embed_subtitles.return_value = True
        mock_engine_cls.return_value = mock_engine
        mock_mux.return_value = os.path.join(self.temp_dir, "preview_mux.mp4")

        dummy_video = os.path.join(self.temp_dir, "video.mp4")
        dummy_audio = os.path.join(self.temp_dir, "audio.wav")
        dummy_output = os.path.join(self.temp_dir, "output.mp4")
        dummy_srt = os.path.join(self.temp_dir, "subs.srt")
        for p in (dummy_video, dummy_audio, dummy_srt):
            with open(p, "w", encoding="utf-8") as f:
                f.write("test")

        warps = [{"id": "w1", "type": "slow", "time": 5.0, "speed": 0.5, "duration": 1.0}]
        worker = PreviewMuxWorker(
            video_path=dummy_video,
            audio_path=dummy_audio,
            output_path=dummy_output,
            mode="both",
            srt_path=dummy_srt,
            subtitle_style={"video_time_warps": warps},
            render_subtitles=True,
            temp_dir=self.temp_dir,
        )
        errors = []
        worker.finished.connect(lambda out, err: errors.append(err))
        worker.run()
        if errors and errors[0]:
            print(f"DEBUG WORKER ERROR: {errors[0]}")

        # 1. apply_timewarp_to_video_clip should be called on the raw video with include_audio=False
        mock_timewarp.assert_called_once()
        args, kwargs = mock_timewarp.call_args
        self.assertEqual(args[0], dummy_video)
        self.assertEqual(kwargs.get("include_audio"), False)

        # 2. mux_audio_into_video_for_preview should receive the warped video, not raw video
        mock_mux.assert_called_once()
        mux_args, _ = mock_mux.call_args
        self.assertTrue("preview_warped_" in mux_args[0])

        # 3. embed_subtitles should receive video_time_warps=None so it doesn't double-warp or atempo audio
        mock_engine.embed_subtitles.assert_called_once()
        _, embed_kwargs = mock_engine.embed_subtitles.call_args
        self.assertIsNone(embed_kwargs.get("video_time_warps"))

    @patch("app.workflows.export_workflow.os.path.exists", return_value=True)
    def test_export_workflow_warps_video_first_for_both_mode(self, mock_exists):
        workflow = ExportWorkflow(workspace_root=self.workspace_root)
        workflow.engine_runtime = MagicMock()
        workflow.engine_runtime.get_video_dimensions.return_value = (1920, 1080)
        workflow.engine_runtime.get_video_fps.return_value = 30.0
        workflow.engine_runtime.get_video_duration.return_value = 10.0
        workflow.engine_runtime.apply_timewarp_to_video_clip.return_value = True
        workflow.engine_runtime.mux_audio_for_preview.return_value = True
        workflow._export_subtitle_video = MagicMock(return_value=True)
        workflow._ensure_subtitle_ass = MagicMock(return_value=os.path.join(self.temp_dir, "subs.ass"))

        warps = [{"id": "w1", "type": "slow", "time": 5.0, "speed": 0.5, "duration": 1.0}]
        subtitle_style = {"video_time_warps": warps, "font_size": 20}

        dummy_video = os.path.join(self.temp_dir, "video.mp4")
        dummy_audio = os.path.join(self.temp_dir, "audio.wav")
        dummy_output = os.path.join(self.temp_dir, "output.mp4")
        dummy_srt = os.path.join(self.temp_dir, "subs.srt")

        workflow.run(
            video_path=dummy_video,
            audio_path=dummy_audio,
            output_path=dummy_output,
            mode="both",
            srt_path=dummy_srt,
            ass_path="",
            subtitle_style=subtitle_style,
            project_temp_dir=self.temp_dir,
        )

        # 1. apply_timewarp_to_video_clip must be called on raw video with include_audio=False
        workflow.engine_runtime.apply_timewarp_to_video_clip.assert_called_once()
        warp_args, warp_kwargs = workflow.engine_runtime.apply_timewarp_to_video_clip.call_args
        self.assertEqual(warp_args[0], dummy_video)
        self.assertEqual(warp_kwargs.get("include_audio"), False)

        # 2. mux_audio_for_preview should receive the warped video, not raw video
        workflow.engine_runtime.mux_audio_for_preview.assert_called_once()
        mux_args, _ = workflow.engine_runtime.mux_audio_for_preview.call_args
        self.assertTrue("final_warped_" in mux_args[0])

        # 3. _export_subtitle_video should be called with video_time_warps=None in subtitle_style
        workflow._export_subtitle_video.assert_called_once()
        _, sub_kwargs = workflow._export_subtitle_video.call_args
        passed_style = sub_kwargs.get("subtitle_style", {})
        self.assertIsNone(passed_style.get("video_time_warps"))

    @patch("ui.worker_adapters.preview_workers.EngineRuntime")
    @patch("preview_processor.mux_audio_into_video_clip_for_preview")
    @patch("preview_processor.apply_timewarp_to_video_clip")
    @patch("preview_processor.trim_video_clip")
    @patch("ui.worker_adapters.preview_workers.os.path.exists", return_value=True)
    def test_quick_preview_worker_warps_clip_first_for_both_mode(
        self, mock_exists, mock_trim, mock_timewarp, mock_mux, mock_engine_cls
    ):
        mock_engine = MagicMock()
        mock_engine.embed_ass_subtitles.return_value = True
        mock_engine_cls.return_value = mock_engine
        mock_mux.return_value = os.path.join(self.temp_dir, "quick_voice.mp4")

        dummy_video = os.path.join(self.temp_dir, "video.mp4")
        dummy_audio = os.path.join(self.temp_dir, "audio.wav")
        dummy_output = os.path.join(self.temp_dir, "output.mp4")
        dummy_ass = os.path.join(self.temp_dir, "subs.ass")
        for p in (dummy_video, dummy_audio, dummy_ass):
            with open(p, "w", encoding="utf-8") as f:
                f.write("test")

        warps = [{"id": "w1", "type": "slow", "time": 3.0, "media_start": 2.0, "media_end": 4.0, "speed": 0.5, "duration": 2.0}]
        worker = QuickPreviewWorker(
            video_path=dummy_video,
            output_path=dummy_output,
            mode="both",
            start_seconds=1.0,
            duration_seconds=6.0,
            ass_path=dummy_ass,
            audio_path=dummy_audio,
            subtitle_style={"video_time_warps": warps},
            temp_dir=self.temp_dir,
        )
        errors = []
        worker.finished.connect(lambda out, err: errors.append(err))
        worker.run()
        if errors and errors[0]:
            print(f"DEBUG QUICK WORKER ERROR: {errors[0]}")

        # 1. trim_video_clip must be called
        mock_trim.assert_called_once()

        # 2. apply_timewarp_to_video_clip must be called with include_audio=False
        mock_timewarp.assert_called_once()
        warp_args, warp_kwargs = mock_timewarp.call_args
        self.assertEqual(warp_kwargs.get("include_audio"), False)

        # 3. mux_audio_into_video_clip_for_preview must be called with warped clip and video_is_pretrimmed=True
        mock_mux.assert_called_once()
        mux_args, mux_kwargs = mock_mux.call_args
        self.assertTrue("preview_warped_" in mux_args[0])
        self.assertTrue(mux_kwargs.get("video_is_pretrimmed"))

        # 4. embed_ass_subtitles must receive video_time_warps=None
        mock_engine.embed_ass_subtitles.assert_called_once()
        _, embed_kwargs = mock_engine.embed_ass_subtitles.call_args
        self.assertIsNone(embed_kwargs.get("video_time_warps"))

    @patch("subtitle_builder.generate_srt")
    @patch("ui.main_window.srt_to_ass", return_value="/mock/live_preview.ass")
    @patch("os.path.exists", return_value=True)
    def test_write_live_preview_assets_maps_to_media_time(self, mock_exists, mock_srt_to_ass, mock_gen_srt):
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui.get_project_temp_dir.return_value = self.temp_dir
        gui.video_path_edit.text.return_value = os.path.join(self.temp_dir, "raw_video.mp4")
        gui.video_view = MagicMock()
        gui.video_view.video_source_width = 1920
        gui.video_view.video_source_height = 1080
        gui._subtitle_render_dimensions.return_value = (1920, 1080)
        gui.get_subtitle_export_style.return_value = {}
        gui._uses_exact_full_block_subtitle_background.return_value = False
        gui.live_preview_subtitle_path = ""
        gui.live_preview_ass_path = ""
        gui._live_preview_signature = None

        warps = [
            {"id": "w1", "type": "slow", "time": 5.4, "media_start": 3.4, "media_end": 5.4, "duration": 1.17, "speed": 0.631}
        ]
        gui.video_time_warps = warps

        segments = [
            {"start": 0.0, "end": 3.4, "text": "line 0"},
            {"start": 3.4, "end": 6.57, "text": "line 1"},
            {"start": 6.57, "end": 9.12, "text": "line 2"},
        ]

        # Call the unbound method with mock gui
        VideoTranslatorGUI._write_live_preview_assets(gui, segments)

        # Verify generate_srt received render_segments mapped to media time
        mock_gen_srt.assert_called_once()
        passed_args, _ = mock_gen_srt.call_args
        render_segments = passed_args[0]
        self.assertEqual(len(render_segments), 3)
        self.assertAlmostEqual(render_segments[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(render_segments[0]["end"], 3.4, places=2)
        self.assertAlmostEqual(render_segments[1]["start"], 3.4, places=2)
        self.assertAlmostEqual(render_segments[1]["end"], 5.4, places=2)
        # Crucial check: Segment 2 starts at media 5.4s (NOT delayed to timeline 6.57s)!
        self.assertAlmostEqual(render_segments[2]["start"], 5.4, places=2)
        self.assertAlmostEqual(render_segments[2]["end"], 7.95, places=2)

    @patch("video_processor.get_video_duration", return_value=15.5)
    def test_engine_runtime_and_export_workflow_get_video_duration(self, mock_dur):
        from services import EngineRuntime
        runtime = EngineRuntime()
        self.assertEqual(runtime.get_video_duration("dummy.mp4"), 15.5)

        workflow = ExportWorkflow(workspace_root=self.workspace_root)
        self.assertEqual(workflow._get_video_duration("dummy.mp4"), 15.5)


if __name__ == "__main__":
    unittest.main()
