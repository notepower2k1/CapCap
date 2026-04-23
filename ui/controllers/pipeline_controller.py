import os
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from workers import PrepareWorkflowWorker

# Robust import for the progress widget
try:
    from widgets.progress_dialog import BackgroundableProgressDialog, PipelineProgressDialog
except ImportError:
    # Fallback for different execution contexts
    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    from widgets.progress_dialog import BackgroundableProgressDialog, PipelineProgressDialog

class PipelineController:
    """
    Orchestrates the multi-stage video translation pipeline.
    Connects background workers to the UI and progress tracking widgets.
    """
    def __init__(self, gui):
        self.gui = gui
        self.progress_dialog = None
        self.whisper_download_dialog = None

    
    def _whisper_model_cached(self, model_name: str) -> bool:
        try:
            name = str(model_name or "").strip().lower()
            if not name:
                return True
            cache_root = os.path.join(self.gui.workspace_root, "models", "faster_whisper")
            if not os.path.isdir(cache_root):
                return False
            for entry in os.listdir(cache_root):
                low = entry.lower()
                if low.startswith("models--") and name in low:
                    return True
            return False
        except Exception:
            return True

    def _hide_whisper_download_dialog(self):
        try:
            if self.whisper_download_dialog is not None:
                self.whisper_download_dialog.hide()
                self.whisper_download_dialog.deleteLater()
                self.whisper_download_dialog = None
        except Exception:
            self.whisper_download_dialog = None

    def _show_whisper_download_dialog(self):
        try:
            if self.whisper_download_dialog is not None:
                return
            model_name = getattr(self.gui, "get_whisper_model_name", lambda: "base")()
            dlg = BackgroundableProgressDialog(f"Downloading Whisper model: {model_name} ...", "Hide", 0, 0, self.gui)
            dlg.setWindowTitle("Downloading models")
            dlg.setWindowModality(Qt.NonModal)
            dlg.setMinimumDuration(0)
            dlg.setAutoReset(False)
            dlg.setAutoClose(False)
            try:
                dlg.canceled.connect(dlg.hide)
            except Exception:
                pass
            self.whisper_download_dialog = dlg
            if hasattr(self.gui, "_register_progress_dialog"):
                self.gui._register_progress_dialog(dlg)
            dlg.show()
        except Exception:
            self.whisper_download_dialog = None
    def _setup_progress_dialog(self, includes_separation=True):
        """Creates and initializes the progress tracking dialog."""
        if self.progress_dialog:
            self.progress_dialog.close()
            
        self.progress_dialog = PipelineProgressDialog(self.gui)
        if hasattr(self.gui, "_register_progress_dialog"):
            self.gui._register_progress_dialog(self.progress_dialog)
        self.progress_dialog.add_step("prepare", "Preparing Project")
        self.progress_dialog.add_step("extraction", "Extracting Original Audio")
        if includes_separation:
            self.progress_dialog.add_step("separation", "Isolating Background Music")
        self.progress_dialog.add_step("transcription", "Transcribing Speech (AI)")
        self.progress_dialog.add_step("translation", "Translating & Polishing Contents")
        self.progress_dialog.add_step("voiceover", "Synthesizing AI Voiceover")
        self.progress_dialog.add_step("preview", "Preparing Video Preview")
        self.progress_dialog.show()

    def run_all_pipeline(self, video_path=None, requires_separation=None):
        """Entry point for the full generation process."""
        if video_path is None:
            # Fallback to the UI field if not provided
            video_path = getattr(self.gui, "video_path_edit", None)
            if video_path:
                video_path = video_path.text().strip()
            else:
                video_path = getattr(self.gui, "last_video_path", "")

        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, "Error", "Please select a video file first.")
            return

        # Determine if we need vocal separation based on UI settings
        if requires_separation is None:
            requires_separation = (self.gui.get_audio_handling_mode() == "clean")

        # Initialize state
        self.gui._pipeline_active = True
        self.gui._pipeline_step = "prepare"
        
        # UI Feedback
        if hasattr(self.gui, "run_all_btn"):
            self.gui.run_all_btn.setEnabled(False)
            self.gui.run_all_btn.setText("Processing...")
            
        self._setup_progress_dialog(includes_separation=requires_separation)
        self.progress_dialog.start_step("prepare")
        
        # Start the background worker
        self.gui.log(f"[Pipeline] Starting prepare workflow for: {video_path}")
        output_mode = self.gui.get_output_mode_key()
        optimize_subtitles = self.gui.is_ai_subtitle_optimization_enabled() and output_mode == "subtitle"
        self.gui.prepare_workflow_thread = PrepareWorkflowWorker(
            self.gui.workspace_root,
            video_path,
            output_mode,
            self.gui.get_audio_handling_mode(),
            self.gui.get_source_language_code(),
            self.gui.is_ai_polish_enabled(),
            optimize_subtitles,
            self.gui.get_ai_style_instruction(),
            self.gui.get_whisper_model_path(),
        )
        
        # Connect signals
        self.gui.prepare_workflow_thread.step_started.connect(self._on_prepare_step_started)
        self.gui.prepare_workflow_thread.finished.connect(self.on_prepare_workflow_finished)
        self.gui.prepare_workflow_thread.start()

    def _on_prepare_step_started(self, step_id):
        """Callback from PrepareWorkflowWorker when an internal stage begins."""
        if not self.progress_dialog:
            return

        order = ["prepare", "extraction", "separation", "transcription", "translation"]
        if step_id in order:
            idx = order.index(step_id)
            for i in range(idx):
                self.progress_dialog.finish_step(order[i])
            self.progress_dialog.start_step(step_id)
            self.gui._pipeline_step = step_id

            if step_id == "transcription":
                self._hide_whisper_download_dialog()
            elif step_id == "prepare":
                model_name = getattr(self.gui, "get_whisper_model_name", lambda: "base")()
                if not self._whisper_model_cached(model_name):
                    self._show_whisper_download_dialog()

    def on_prepare_workflow_finished(self, project_state_path, error):
        """Callback when the background PrepareWorkflow finishes completely."""
        self._hide_whisper_download_dialog()

        if error or not project_state_path:
            self.pipeline_fail(f"Prepare workflow failed: {error}")
            self.gui.show_error("Prepare Failed", "Could not complete project preparation.", str(error))
            return

        if self.progress_dialog:
            self.progress_dialog.finish_step("prepare")
            self.progress_dialog.finish_step("translation")

        try:
            state = self.gui.project_service.load_project(project_state_path)
            self.gui.current_project_state = state
            self.gui.load_project_context(state)
            self.gui.refresh_ui_state()
        except Exception as e:
            self.gui.log(f"[Pipeline] Error reloading state: {e}")

        mode = self.gui.get_output_mode_key()
        if mode == "subtitle":
            self.pipeline_done()
            if self.progress_dialog:
                self.progress_dialog.set_completed()
            self.gui.log("[Pipeline] Subtitles generated successfully.")
        else:
            self.pipeline_advance("translation")

    def pipeline_advance(self, completed_step: str):
        """Manages transitions between major pipeline segments."""
        if not self.gui._pipeline_active:
            return
            
        if self.progress_dialog:
            self.progress_dialog.finish_step(completed_step)

        mode = self.gui.get_output_mode_key()
        
        # State transitions
        if completed_step == "translation":
            if mode == "subtitle":
                self.pipeline_done()
                if self.progress_dialog:
                    self.progress_dialog.skip_step("voiceover")
                    self.progress_dialog.skip_step("preview")
                    self.progress_dialog.set_completed()
                return
            # Start voiceover
            self.gui._pipeline_step = "voiceover"
            if self.progress_dialog: self.progress_dialog.start_step("voiceover")
            self.gui.run_voiceover()
            
        elif completed_step == "voiceover":
            self.gui._pipeline_step = "preview"
            if self.progress_dialog:
                self.progress_dialog.start_step("preview")
            try:
                self.gui.log("[Pipeline] Voiceover complete. Preparing video preview.")
                self.gui.preview_video()
            except Exception as exc:
                self.pipeline_fail(f"Preview start failed: {exc}")
            
        elif completed_step == "preview":
            # Success!
            self.pipeline_done()
            if self.progress_dialog: 
                self.progress_dialog.set_completed()

    def pipeline_fail(self, reason: str):
        """Safely stops the pipeline and restores UI state on failure."""
        self.gui._pipeline_active = False
        
        if self.progress_dialog:
            current_step = getattr(self.gui, "_pipeline_step", "prepare")
            self.progress_dialog.fail_step(current_step)
            # Show the error reason in the footer
            self.progress_dialog.footer.setText(f"FAILED: {reason}")
            self.progress_dialog.footer.setStyleSheet("color: #FF4444; font-weight: bold;")

        # Restore UI
        if hasattr(self.gui, "run_all_btn"):
            self.gui.run_all_btn.setEnabled(True)
            self.gui.run_all_btn.setText("Generate Full Video")
        
        self.gui.progress_bar.setRange(0, 100)
        self.gui.progress_bar.setValue(0)
        self.gui.refresh_ui_state()

    def pipeline_done(self):
        """Marks the entire pipeline as successfully finished."""
        self.gui._pipeline_active = False
        self.gui._pipeline_step = ""
        
        if hasattr(self.gui, "run_all_btn"):
            self.gui.run_all_btn.setEnabled(True)
            self.gui.run_all_btn.setText("Generate Full Video")
            
        self.gui.progress_bar.setRange(0, 100)
        self.gui.progress_bar.setValue(100)
        self.gui.refresh_ui_state()
