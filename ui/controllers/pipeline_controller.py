import os

from PySide6.QtWidgets import QMessageBox

from workers import PrepareWorkflowWorker


class PipelineController:
    def __init__(self, gui):
        self.gui = gui

    def run_all_pipeline(self):
        video_path = self.gui.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, "Error", "Please select a video first.")
            return

        state = self.gui.ensure_current_project()
        if state:
            self.gui.load_project_context(state)

        mode = self.gui.get_output_mode_key()
        if mode in ("voice", "both") and self.gui.has_reusable_voice_inputs():
            self.gui._pipeline_active = True
            self.gui._pipeline_step = "voiceover"
            self.gui.run_all_btn.setEnabled(False)
            self.gui.run_all_btn.setText("Generating voice...")
            self.gui.progress_bar.setRange(0, 100)
            self.gui.log("[Voice] Reusing existing transcript, translation, and background assets...")
            self.gui.run_voiceover()
            return

        self.gui._pipeline_active = True
        self.gui._pipeline_step = "prepare"
        self.gui.run_all_btn.setEnabled(False)
        self.gui.run_all_btn.setText("Preparing...")
        self.gui.progress_bar.setRange(0, 0)
        self.gui.log("[Prepare] Running prepare workflow...")
        self.gui.prepare_workflow_thread = PrepareWorkflowWorker(
            self.gui.workspace_root,
            video_path,
            self.gui.get_output_mode_key(),
            self.gui.get_source_language_code(),
            self.gui.is_ai_polish_enabled(),
        )
        self.gui.prepare_workflow_thread.finished.connect(self.gui.on_prepare_workflow_finished)
        self.gui.prepare_workflow_thread.start()

    def on_prepare_workflow_finished(self, project_state_path, error):
        self.gui.progress_bar.setRange(0, 100)
        if error or not project_state_path:
            self.gui._pipeline_fail("Prepare workflow failed.")
            self.gui.show_error(
                "Prepare Failed",
                "Could not complete the automatic prepare workflow.",
                error or "Unknown workflow error.",
            )
            return

        try:
            state = self.gui.project_service.load_project(project_state_path)
            self.gui.current_project_state = state
            self.gui.load_project_context(state)
            self.gui.refresh_ui_state()
            self.gui.schedule_auto_frame_preview()
            self.gui.log(f"[Prepare] Project ready: {state.project_root}")
        except Exception as exc:
            self.gui._pipeline_fail("Could not reload project state.")
            self.gui.show_error("Prepare Failed", "Prepare workflow finished but project state could not be loaded.", str(exc))
            return

        mode = self.gui.get_output_mode_key()
        if mode == "subtitle":
            self.gui._pipeline_done()
            QMessageBox.information(
                self.gui,
                "Ready",
                "Vietnamese subtitles are ready.\n\nReview them if needed, then click 'Export Final Video'.",
            )
            return

        self.gui.run_all_btn.setEnabled(False)
        self.gui.run_all_btn.setText("Generating voice...")
        self.gui.run_voiceover()

    def pipeline_advance(self, completed_step: str):
        if not self.gui._pipeline_active:
            return

        mode = self.gui.get_output_mode_key()
        if completed_step == "extraction":
            if mode == "subtitle":
                self.gui.run_transcription()
                return
            self.gui.run_vocal_separation()
            return
        if completed_step == "separation":
            self.gui.run_transcription()
            return
        if completed_step == "transcription":
            self.gui.run_translation()
            return
        if completed_step == "translation":
            if mode == "subtitle":
                self.gui._pipeline_done()
                QMessageBox.information(
                    self.gui,
                    "Ready",
                    "Vietnamese subtitles are ready.\n\nReview them if needed, then click 'Export Final Video'.",
                )
                return
            self.gui.run_voiceover()
            return
        if completed_step == "voiceover":
            self.gui.preview_video_with_mixed_audio()

    def pipeline_fail(self, reason: str):
        if not self.gui._pipeline_active:
            return
        self.gui._pipeline_active = False
        self.gui._pipeline_step = ""
        self.gui.run_all_btn.setEnabled(True)
        self.gui.run_all_btn.setText("Generate")

    def pipeline_done(self):
        if not self.gui._pipeline_active:
            return
        self.gui._pipeline_active = False
        self.gui._pipeline_step = ""
        self.gui.run_all_btn.setEnabled(True)
        self.gui.run_all_btn.setText("Generate")
