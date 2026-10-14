import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QTextEdit, QToolButton, QWidget
from services.gui_project_bridge import GUIProjectBridge
from services.project_service import ProjectService
from ui import i18n
from ui.controllers.pipeline_controller import PipelineController
from ui.helpers.srt_helpers import validate_srt_text
from ui.main_window import VideoTranslatorGUI
from ui.worker_adapters.processing_workers import PrepareWorkflowWorker
from workflows.prepare_workflow import PrepareWorkflow


class TestTranscriptImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(i18n, "_current_language", "en"))
        self.enterContext(patch.dict(os.environ, {"CAPCAP_RUNTIME_PROFILE": "local"}))
        self.video = Path(self.folder) / "video.mp4"
        self.video.touch()
        self.srt = Path(self.folder) / "original.srt"
        self.text = "1\n00:00:00,500 --> 00:00:01,800\n你好\n\n2\n00:00:02,250 --> 00:00:03,000\n再见\n"
        self.srt.write_text(self.text, encoding="utf-8-sig")
        gui = self.gui = QWidget()
        self.addCleanup(gui.deleteLater)
        gui.workspace_root = self.folder
        gui.video_path_edit = QLineEdit(str(self.video), gui)
        gui.srt_output_folder_edit = QLineEdit(self.folder, gui)
        gui.transcript_text = QTextEdit(gui)
        gui.translated_text = QTextEdit(gui)
        gui.current_segments = []
        gui.current_translated_segments = []
        gui.last_voice_vi_path = gui.last_mixed_vi_path = ""
        gui._pipeline_active = False
        gui.workflow_stage_labels = {}
        gui.workflow_stage_badges = {"transcript": QLabel(gui)}
        gui.run_all_btn = QToolButton(gui)
        gui.timeline = MagicMock()
        gui.project_service = ProjectService(self.folder)
        gui.project_bridge = GUIProjectBridge(gui.project_service)

        def ensure_project():
            gui.current_project_state = gui.project_service.ensure_project(str(self.video))
            return gui.current_project_state

        gui.ensure_current_project = ensure_project
        ensure_project()
        gui.get_output_mode_key = lambda: "subtitle"
        gui.get_transcription_engine = lambda: "ocr"
        gui._completed_transcript_engine_label = lambda: VideoTranslatorGUI._completed_transcript_engine_label(gui)
        gui._dict_segments_to_models = lambda segments, **kwargs: gui.project_bridge.dict_segments_to_models(segments, **kwargs)
        gui._sync_segment_models_from_current_segments = lambda: VideoTranslatorGUI._sync_segment_models_from_current_segments(gui)
        gui.persist_transcription_project_data = lambda segments, srt_path="": VideoTranslatorGUI.persist_transcription_project_data(gui, segments, srt_path)
        gui.update_workflow_stage_badges = lambda: VideoTranslatorGUI.update_workflow_stage_badges(gui)
        gui.refresh_ui_state = gui.update_workflow_stage_badges
        gui.import_original_srt = lambda: VideoTranslatorGUI.import_original_srt(gui)
        gui.validate_srt_text = validate_srt_text
        for name in ("run_pipeline_to_stage", "import_translated_srt", "run_all_pipeline", "skip_tts_stage",
                     "log", "show_error", "schedule_timeline_visual_refresh"):
            setattr(gui, name, MagicMock())
        self.enterContext(patch("ui.main_window.QFileDialog.getOpenFileName", return_value=(str(self.srt), "")))
        self.enterContext(patch("ui.main_window.QMessageBox.information"))
        self.enterContext(patch("ui.main_window.QMessageBox.critical"))

    def test_menu_import_loads_original_cues_and_enables_retranscription(self):
        VideoTranslatorGUI._update_generate_button_menu(self.gui, has_data=False)
        step_menu = self.gui.run_all_btn.menu().actions()[0].menu()
        transcript_menu = step_menu.actions()[0].menu()
        self.assertEqual(transcript_menu.title(), "Run to Transcript")
        self.assertEqual([action.text() for action in transcript_menu.actions()],
                         ["Auto Transcript", "Import Transcript File…"])
        self.gui._generate_transcript_action.trigger()
        self.gui.run_pipeline_to_stage.assert_called_once_with("transcript")
        self.gui._generate_import_original_srt_action.trigger()
        self.assertEqual(self.gui.current_segments[0]["start"], 0.5)
        self.assertEqual(self.gui.current_segments[1]["start"], 2.25)
        self.assertEqual(self.gui.current_segments[1]["text"], "再见")
        self.assertTrue(self.gui.current_project_state.settings["transcript_imported"])
        self.assertEqual(self.gui._generate_transcript_action.text(), "Re-transcript")
        self.assertTrue(self.gui._generate_transcript_action.isEnabled())
        self.assertTrue(self.gui._generate_translate_action.isEnabled())
        self.assertIn("Imported SRT", self.gui.workflow_stage_badges["transcript"].text())
        self.gui._pipeline_active = True
        self.gui.update_workflow_stage_badges()
        self.assertFalse(self.gui._generate_transcript_action.isEnabled())
        self.assertFalse(self.gui._generate_import_original_srt_action.isEnabled())

    def test_invalid_file_does_not_replace_existing_import(self):
        self.gui.import_original_srt()
        original = list(self.gui.current_segments)
        self.srt.write_text("This is not an SRT", encoding="utf-8")
        self.gui.import_original_srt()
        self.assertEqual(self.gui.current_segments, original)
        saved = self.gui.project_service.load_segment_artifact(self.gui.ensure_current_project(), "transcript_segments")
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[1].original_text, "再见")

    def test_full_pipeline_reuses_import_until_explicit_retranscript_succeeds(self):
        self.gui.import_original_srt()
        workflow = PrepareWorkflow(self.folder)
        with patch("workflows.prepare_workflow.ResourceDownloadService.validate_pipeline_runtime", return_value=[]), \
                patch("workflows.prepare_workflow.ResourceDownloadService.validate_ocr_runtime", return_value=[]), \
                patch("workflows.prepare_workflow.ResourceDownloadService.validate_sensevoice_runtime", return_value=[]), \
                patch("workflows.prepare_workflow.subprocess.run"), \
                patch.object(workflow.engine_runtime, "extract_audio", return_value=True), \
                patch.object(workflow.chunking_service, "probe_wav_duration", return_value=4.0), \
                patch.object(workflow.engine_runtime, "transcribe_audio") as whisper, \
                patch.object(workflow.engine_runtime, "transcribe_video_ocr") as ocr, \
                patch.object(workflow.engine_runtime, "transcribe_audio_sensevoice") as sensevoice, \
                patch.object(workflow.engine_runtime, "transcribe_audio_capcut") as capcut, \
                redirect_stdout(io.StringIO()):
            for engine in ("whisper", "ocr", "sensevoice", "capcut"):
                with self.subTest(engine=engine):
                    state = workflow.run(str(self.video), transcription_engine=engine, skip_translation=True)
                    saved = workflow.project_service.load_segment_artifact(state, "transcript_segments")
                    self.assertEqual([segment.original_text for segment in saved], ["你好", "再见"])
                    self.assertEqual([segment.start for segment in saved], [0.5, 2.25])
                    self.assertTrue(state.settings["transcript_imported"])
            for recognizer in (whisper, ocr, sensevoice, capcut):
                recognizer.assert_not_called()
            ocr.return_value = []
            with self.assertRaisesRegex(RuntimeError, "No readable text"):
                workflow.run(str(self.video), transcription_engine="ocr", skip_translation=True, force_transcription=True)
            state = self.gui.ensure_current_project()
            self.assertTrue(state.settings["transcript_imported"])
            self.assertEqual(len(workflow.project_service.load_segment_artifact(state, "transcript_segments")), 2)
            ocr.return_value = [{"start": 0, "end": 1, "text": "重新转录"}]
            state = workflow.run(str(self.video), transcription_engine="ocr", skip_translation=True, force_transcription=True)
            self.assertFalse(state.settings["transcript_imported"])
            self.assertEqual(workflow.project_service.load_segment_artifact(state, "transcript_segments")[0].original_text,
                             "重新转录")
            calls = ocr.call_count
            workflow.run(str(self.video), transcription_engine="ocr", skip_translation=True)
            self.assertEqual(ocr.call_count, calls)
            workflow.run(str(self.video), transcription_engine="ocr", skip_translation=True, force_transcription=True)
            self.assertEqual(ocr.call_count, calls + 1)
        self.assertEqual(self.srt.read_text(encoding="utf-8-sig"), self.text)

    def test_missing_import_does_not_silently_start_recognition(self):
        self.gui.import_original_srt()
        state = self.gui.ensure_current_project()
        state.set_artifact("transcript_segments", str(Path(self.folder) / "missing.json"))
        self.gui.project_service.save_project(state)
        workflow = PrepareWorkflow(self.folder)
        with self.assertRaisesRegex(RuntimeError, "Imported transcript is unavailable"):
            workflow.run(str(self.video), transcription_engine="ocr", skip_translation=True)

    def test_transcript_action_forwards_force_flag_to_worker_and_api(self):
        gui = MagicMock()
        gui.get_transcription_engine.return_value = "ocr"
        controller = PipelineController(gui)
        controller._ensure_local_worker_running = MagicMock()
        controller._start_prepare_status_polling = MagicMock()
        with patch("ui.controllers.pipeline_controller.PrepareWorkflowWorker") as worker:
            for stage in ("full", "transcript"):
                controller.target_stage = stage
                controller.start_prepare_workflow(str(self.video))
                self.assertEqual(worker.call_args.kwargs["force_transcription"], stage == "transcript")
        worker = PrepareWorkflowWorker(self.folder, str(self.video), "subtitle", "fast", "zh", "vi", False,
                                       False, "", "base", force_remote_api=True, force_transcription=True)
        with patch("remote_api.remote_api_post", return_value={"project_state_path": "project.json"}) as post:
            worker.run()
        self.assertTrue(post.call_args.args[1]["force_transcription"])

    def test_full_pipeline_skips_model_downloads_for_imported_transcript(self):
        self.gui.import_original_srt()
        self.gui.ensure_required_resources = MagicMock(return_value=True)
        self.gui.pipeline_controller = MagicMock()
        VideoTranslatorGUI.run_all_pipeline(self.gui)
        kwargs = self.gui.ensure_required_resources.call_args.kwargs
        self.assertFalse(kwargs["include_whisper"])
        self.assertFalse(kwargs["include_ocr"])
        self.gui.pipeline_controller.run_all_pipeline.assert_called_once_with(target_stage="full")


if __name__ == "__main__":
    unittest.main()
