import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox
from worker_adapters import PrepareWorkflowWorker
from runtime_paths import subprocess_hidden_kwargs

try:
    from i18n import t
except ImportError:
    from ui.i18n import t

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
        self.local_worker_process = None
        self.worker_log_thread = None
        self.local_worker_api_url = ""
        self.local_worker_api_token = ""
        self.prepare_run_id = 0
        self.prepare_status_timer = None
        self.prepare_status_phase = ""
        self.active_processing_device = "cpu"

    def _app_root(self):
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _find_free_local_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_for_local_worker_server(self, api_url, timeout_s=60.0):
        deadline = time.monotonic() + timeout_s
        health_url = f"{api_url.rstrip('/')}/health"
        while time.monotonic() < deadline:
            process = self.local_worker_process
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"Local worker process exited early with code {process.returncode}.")
            try:
                with urllib.request.urlopen(health_url, timeout=1.0) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("Local worker process did not become ready.")

    def _kill_process_tree(self, process):
        if process is None:
            return
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    **subprocess_hidden_kwargs(),
                )
            else:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _stop_local_worker_server(self):
        process = self.local_worker_process
        self.local_worker_process = None
        self.local_worker_api_url = ""
        self.local_worker_api_token = ""
        log_thread = self.worker_log_thread
        self.worker_log_thread = None
        if process is not None:
            self._kill_process_tree(process)
            if log_thread is not None and log_thread.is_alive():
                try:
                    log_thread.join(timeout=2.0)
                except Exception:
                    pass
            try:
                if process.stdout and not process.stdout.closed:
                    process.stdout.close()
            except Exception:
                pass

    def _start_prepare_status_polling(self):
        self._stop_prepare_status_polling()
        self.prepare_status_phase = ""
        timer = QTimer(self.gui)
        timer.setInterval(400)
        timer.timeout.connect(self._poll_prepare_status)
        self.prepare_status_timer = timer
        timer.start()

    def _stop_prepare_status_polling(self):
        timer = self.prepare_status_timer
        self.prepare_status_timer = None
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass

    def _poll_prepare_status(self):
        api_url = self.local_worker_api_url
        token = self.local_worker_api_token
        if not api_url:
            return
        try:
            request = urllib.request.Request(f"{api_url.rstrip('/')}/v1/status")
            if token:
                request.add_header("X-CapCap-Token", token)
            with urllib.request.urlopen(request, timeout=0.35) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            phase = str(data.get("phase", "") or "").strip()
            message = str(data.get("message", "") or "").strip()
            progress = data.get("progress", None)
            detail = str(data.get("detail", "") or "").strip()
            if phase and phase != self.prepare_status_phase:
                self.prepare_status_phase = phase
                self._on_prepare_step_started(phase, message)
            if progress is not None or detail:
                self._on_prepare_step_progress(phase, progress, message, detail)
        except Exception:
            pass

    def _start_local_worker_server(self, processing_device: str = ""):
        self._stop_local_worker_server()
        app_root = self._app_root()
        server_script = os.path.join(app_root, "app", "remote_api_server.py")
        port = self._find_free_local_port()
        token = secrets.token_urlsafe(24)
        env = os.environ.copy()
        env["CAPCAP_RUNTIME_PROFILE"] = "local"
        env["CAPCAP_REMOTE_API_HOST"] = "127.0.0.1"
        env["CAPCAP_REMOTE_API_PORT"] = str(port)
        env["CAPCAP_REMOTE_API_TOKEN"] = token
        env["CAPCAP_REMOTE_PRELOAD_MODELS"] = "0"
        env["CAPCAP_RUN_REMOTE_API_SERVER"] = "1" if getattr(sys, "frozen", False) else "0"
        # The worker is a separate frozen Python process.  Force UTF-8 before
        # it starts so any third-party code that still relies on Python's
        # default text encoding cannot inherit a locale-specific ANSI codec.
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        pythonpath_entries = [app_root, os.path.join(app_root, "app")]
        current_pythonpath = env.get("PYTHONPATH", "")
        if current_pythonpath:
            pythonpath_entries.append(current_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
        env["CAPCAP_DEVICE"] = (
            "cuda" if str(processing_device or os.getenv("CAPCAP_DEVICE", "cpu")).strip().lower() == "cuda"
            else "cpu"
        )

        process_kwargs = subprocess_hidden_kwargs()
        if os.name == "nt":
            process_kwargs["creationflags"] = int(process_kwargs.get("creationflags", 0)) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )

        # A frozen GUI executable must be launched in an explicit worker mode.
        # Starting it with no arguments would open a second launcher window;
        # the worker entrypoint then runs the HTTP server without importing the
        # GUI or creating a QApplication.
        worker_command = ([sys.executable, "--worker-server"]
                          if getattr(sys, "frozen", False)
                          else [sys.executable, server_script])
        self.local_worker_process = subprocess.Popen(
            worker_command,
            cwd=app_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=False,
            **process_kwargs,
        )
        self.local_worker_api_url = f"http://127.0.0.1:{port}"
        self.local_worker_api_token = token
        self._start_worker_log_forwarding()
        try:
            self._wait_for_local_worker_server(self.local_worker_api_url)
        except Exception:
            self._stop_local_worker_server()
            raise

    def _start_worker_log_forwarding(self):
        process = self.local_worker_process
        if process is None or process.stdout is None:
            return

        def _forward_logs():
            try:
                for raw_line in iter(process.stdout.readline, b""):
                    if not raw_line:
                        break
                    text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not text:
                        continue
                    if any(h in text for h in ("/v1/status", "/health", "/v1/health")):
                        continue
                    if text.startswith("[Worker]"):
                        self.gui.log(text)
                    else:
                        self.gui.log(f"[Worker] {text}")
                    if any(tag in text for tag in (
                        "[ASR Progress]",
                        "[OCR Progress]",
                        "[SenseVoice Progress]",
                        "[CapCut STT Progress]",
                        "[Vocal Separation Progress]",
                    )):
                        import re
                        m = re.search(r'(\d+)%', text)
                        if m:
                            try:
                                parsed_pct = int(m.group(1))
                                step = "separation" if "[Vocal Separation" in text else "transcription"
                                QTimer.singleShot(0, lambda p=parsed_pct, t=text, s=step: self._on_prepare_step_progress(s, p, detail=t))
                            except Exception:
                                pass
            except Exception:
                pass
            finally:
                try:
                    if process.stdout and not process.stdout.closed:
                        process.stdout.close()
                except Exception:
                    pass

        t = threading.Thread(target=_forward_logs, daemon=True, name="WorkerLogForwarder")
        t.start()
        self.worker_log_thread = t

    def _mark_running_project_steps_stopped(self):
        state = getattr(self.gui, "current_project_state", None)
        steps = getattr(state, "steps", {}) or {}
        for step_name, status in list(steps.items()):
            if status != "running":
                continue
            try:
                self.gui.update_project_step(step_name, "failed")
            except Exception:
                try:
                    steps[step_name] = "failed"
                except Exception:
                    pass

    def _on_pipeline_stop(self):
        if self.progress_dialog:
            reply = QMessageBox.question(
                self.progress_dialog,
                t("Stop Pipeline?"),
                t("Stop the current pipeline run? The worker process for this run will be killed."),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                self.progress_dialog.cancel_stop_request()
                return

        # Stop any running voiceover worker thread
        if hasattr(self.gui, "voice_thread") and self.gui.voice_thread:
            thread = self.gui.voice_thread
            try:
                if hasattr(thread, "stop"):
                    thread.stop()
                if hasattr(thread, "finished"):
                    thread.finished.disconnect()
                if hasattr(thread, "progress"):
                    thread.progress.disconnect()
            except Exception:
                pass
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(300)
                    if thread.isRunning():
                        thread.terminate()
                        thread.wait(200)
            except Exception:
                pass
            self.gui.voice_thread = None
        try:
            from vieneu_tts import unload_vieneu_model
            unload_vieneu_model()
        except Exception:
            pass

        current_step = getattr(self.gui, "_pipeline_step", "prepare")
        self.gui._pipeline_active = False
        self.gui._pipeline_step = ""
        self.prepare_run_id += 1
        self._stop_prepare_status_polling()
        self._mark_running_project_steps_stopped()
        self._stop_local_worker_server()
        if self.progress_dialog:
            step_to_fail = "voiceover" if current_step == "voiceover" else ("preview" if current_step == "preview" else "ai_process")
            self.progress_dialog.fail_step(step_to_fail)
            self.progress_dialog.footer.setText(t("Pipeline stopped."))
            self.progress_dialog.footer.setStyleSheet("color: #FFB86B; font-weight: bold; font-size: 14px; margin-top: 15px;")
            self.progress_dialog.stop_btn.setEnabled(False)
            self.progress_dialog.stop_btn.setText(t("Stopped"))
        if hasattr(self.gui, "run_all_btn"):
            self.gui.run_all_btn.setEnabled(True)
            self.gui.run_all_btn.setText(t("Generate"))
        self.gui.progress_bar.setRange(0, 100)
        self.gui.progress_bar.setValue(0)
        self.gui.refresh_ui_state()
        self.gui.log("[Pipeline] Stop requested. Background worker processes killed.")

    
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
            model_name = getattr(self.gui, "get_whisper_model_name", lambda: "medium")()
            dlg = BackgroundableProgressDialog(f"{t('Downloading Whisper model:')} {model_name} ...", t("Hide"), 0, 0, self.gui)
            dlg.setWindowTitle(t("Downloading models"))
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
            try:
                self.progress_dialog.stop_requested.disconnect(self._on_pipeline_stop)
            except (RuntimeError, TypeError):
                pass
            self.progress_dialog.hide()
            self.progress_dialog.deleteLater()
            self.progress_dialog = None
        self.progress_dialog = PipelineProgressDialog(self.gui)
        self.progress_dialog.stop_requested.connect(self._on_pipeline_stop)
        if hasattr(self.gui, "_register_progress_dialog"):
            self.gui._register_progress_dialog(self.progress_dialog)
        from runtime_profile import is_remote_profile
        if is_remote_profile():
            # Backend runs separate + transcribe + translate in one batch.
            # Cleaner voice (separation) is handled inside the batch silently.
            self.progress_dialog.add_step("ai_process", "Subtitle Processing (AI)")
        else:
            self.progress_dialog.add_step("ai_process", "Subtitle Processing (AI)")
        self.progress_dialog.add_step("voiceover", "Synthesizing AI Voiceover")
        self.progress_dialog.add_step("preview", "Preparing Video Preview")
        self.progress_dialog.show()
        self.progress_dialog.raise_()
        self.progress_dialog.activateWindow()

    def run_all_pipeline(self, video_path=None, requires_separation=None, target_stage="full"):
        """Entry point for the full generation process."""
        if video_path is None:
            # Fallback to the UI field if not provided
            video_path = getattr(self.gui, "video_path_edit", None)
            if video_path:
                video_path = video_path.text().strip()
            else:
                video_path = getattr(self.gui, "last_video_path", "")

        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, t("Error"), t("Please select a video file first."))
            return

        # Determine if we need vocal separation based on UI settings
        if requires_separation is None:
            requires_separation = (self.gui.get_audio_handling_mode() == "clean")

        # Initialize state
        self.prepare_run_id += 1
        prepare_run_id = self.prepare_run_id
        self.gui._pipeline_active = True
        self.gui._pipeline_step = "prepare"
        self.target_stage = str(target_stage or "full").strip().lower()
        
        # UI Feedback
        if hasattr(self.gui, "run_all_btn"):
            self.gui.run_all_btn.setEnabled(False)
            self.gui.run_all_btn.setText(t("Processing..."))
            
        self._setup_progress_dialog(includes_separation=requires_separation)
        self.progress_dialog.start_step("ai_process")

        overlay = getattr(self.gui, "ocr_region_overlay", None)
        if overlay is not None:
            overlay.set_editable(False)
            overlay.hide()

        # Start the background worker
        self.gui.log(f"[Pipeline] Starting prepare workflow for: {video_path}")
        try:
            self.active_processing_device = (
                "cuda" if os.getenv("CAPCAP_DEVICE", "cpu").strip().lower() == "cuda" else "cpu"
            )
            self._start_local_worker_server(self.active_processing_device)
            self.gui.log(
                f"[Pipeline] Local worker process started at {self.local_worker_api_url} "
                f"(device={self.active_processing_device})"
            )
        except Exception as exc:
            self.pipeline_fail(f"Could not start local worker process: {exc}")
            return
        self._start_prepare_status_polling()
        transcription_engine = self.gui.get_transcription_engine()
        # Transcript-only is a true stop point: PrepareWorkflow still
        # extracts audio and transcribes, but does not call translation.
        skip_translation = self.gui.is_skip_translation() or self.target_stage == "transcript"
        output_mode = self.gui.get_output_mode_key()
        # Prefetch voice audio only when Full Pipeline will immediately
        # continue into TTS. A Run to Translate stop point must not spend
        # resources creating cache entries the user may never need.
        prefetch_tts = self.target_stage == "full" and output_mode in ("voice", "both")
        self.gui.prepare_workflow_thread = PrepareWorkflowWorker(
            self.gui.workspace_root,
            video_path,
            output_mode,
            self.gui.get_audio_handling_mode(),
            self.gui.get_source_language_code(),
            self.gui.get_target_language_code(),
            self.gui.is_ai_polish_enabled(),
            False,
            self.gui.get_ai_style_instruction(),
            self.gui.get_whisper_model_name(),
            transcription_engine=transcription_engine,
            speaker_diarization=self.gui.is_speaker_diarization_enabled(),
            speaker_diarization_num_speakers=self.gui.get_speaker_diarization_num_speakers(),
            skip_translation=skip_translation,
            prefetch_voice_name=self.gui.get_active_voice_name() if prefetch_tts else "",
            prefetch_voice_speed=self.gui._parse_voice_speed_value() if prefetch_tts else 1.0,
            remote_api_url=self.local_worker_api_url,
            remote_api_token=self.local_worker_api_token,
            force_remote_api=True,
        )
        
        # Connect signals
        self.gui.prepare_workflow_thread.step_started.connect(self._on_prepare_step_started)
        if hasattr(self.gui.prepare_workflow_thread, "progress"):
            self.gui.prepare_workflow_thread.progress.connect(
                lambda pct, msg: self._on_prepare_step_progress("transcription", pct, detail=msg)
            )
        self.gui.prepare_workflow_thread.finished.connect(
            lambda project_state_path, error, run_id=prepare_run_id: self.on_prepare_workflow_finished(
                project_state_path,
                error,
                run_id,
            )
        )
        self.gui.prepare_workflow_thread.start()

    def _on_prepare_step_started(self, step_id, message=""):
        # The Prepare workflow runs in a separate local process, so mirror
        # its active phase into the GUI's in-memory project state.  This lets
        # Stop mark the correct phase failed instead of leaving a stale
        # completed artifact to drive the sidebar badge.
        if step_id == "translation":
            try:
                self.gui.update_project_step("translate_raw", "running")
            except Exception:
                pass
        labels = {
            "prepare": "Preparing project",
            "extract_audio": "Extracting audio",
            "extraction": "Extracting audio",
            "separation": "Separating vocals",
            "diarization": "Detecting speakers",
            "transcription": "Transcribing audio",
            "translation": "Translating subtitles",
            "done": "Prepare complete",
            "error": "Prepare failed",
        }
        label = t(str(message or labels.get(str(step_id or ""), step_id or "Processing")).strip())
        if label:
            self.gui.log(f"[Pipeline] Phase: {label}")
            if self.progress_dialog:
                self.progress_dialog.footer.setText(t("Prepare: {label}", label=label))
                self.progress_dialog.footer.setStyleSheet("color: #9fb7d5; font-size: 13px; margin-top: 15px;")
        if step_id == "transcription":
            self._hide_whisper_download_dialog()

    def _on_prepare_step_progress(self, phase: str, progress, message: str = "", detail: str = ""):
        if not getattr(self.gui, "_pipeline_active", False):
            return
        pct = None
        if progress is not None:
            try:
                pct = max(0, min(100, int(progress)))
            except (ValueError, TypeError):
                pass

        disp_text = t(str(detail or message).strip())
        if not disp_text:
            disp_text = t("Transcribing audio ({percent}%)", percent=pct) if pct is not None else t("Processing...")

        # Update PipelineProgressDialog
        if self.progress_dialog:
            self.progress_dialog.footer.setText(t("Prepare: {label}", label=disp_text))
            self.progress_dialog.footer.setStyleSheet("color: #9fb7d5; font-size: 13px; margin-top: 15px;")
            if pct is not None and "ai_process" in self.progress_dialog.steps:
                self.progress_dialog.steps["ai_process"].status_label.setText(f"{pct}%")
                self.progress_dialog.steps["ai_process"].status_label.setStyleSheet("color: #00E5FF; font-weight: bold;")
                total_stages = max(1, len(self.progress_dialog.step_order))
                stage_slice = 100.0 / total_stages
                overall_val = int((pct / 100.0) * stage_slice)
                self.progress_dialog.overall_progress.setValue(min(int(stage_slice), max(0, overall_val)))

        # Update Main Window progress bar and status bar
        if pct is not None and hasattr(self.gui, "progress_bar"):
            scaled = 35 + int((pct / 100.0) * 30)
            self.gui.progress_bar.setValue(min(65, max(35, scaled)))
        if hasattr(self.gui, "status_bar") and disp_text:
            self.gui.status_bar.showMessage(disp_text, 2000)

    def on_prepare_workflow_finished(self, project_state_path, error, run_id=None):
        """Callback when the background PrepareWorkflow finishes completely."""
        self._hide_whisper_download_dialog()
        if run_id is not None and run_id != self.prepare_run_id:
            self.gui.log("[Pipeline] Ignoring stale prepare result from a stopped run.")
            return
        self._stop_prepare_status_polling()
        self._stop_local_worker_server()
        if not getattr(self.gui, "_pipeline_active", False):
            return

        if error or not project_state_path:
            self.pipeline_fail(f"Prepare workflow failed: {error}")
            if self.gui.get_transcription_engine() == "ocr":
                self.gui.toggle_ocr_overlay_visibility(False)
            self.gui.show_error(t("Prepare Failed"), t("Could not complete project preparation."), str(error))
            return

        if self.progress_dialog:
            self.progress_dialog.finish_step("ai_process")

        try:
            state = self.gui.project_service.load_project(project_state_path)
            self.gui.current_project_state = state
            self.gui.load_project_context(state)
            self.gui.refresh_ui_state()
            # The OCR crop is an editing aid. Once its transcript is ready,
            # return the preview to its normal unobstructed state. The crop
            # geometry remains available through the OCR button for later
            # adjustment, and this does not affect OCR Translator overlays.
            if self.gui.get_transcription_engine() == "ocr":
                self.gui.toggle_ocr_overlay_visibility(False)
                self.gui.log("[OCR Region] Hidden after OCR transcription completed.")
            self._notify_translation_fallback_if_used()
        except Exception as e:
            self.gui.log(f"[Pipeline] Error reloading state: {e}")

        mode = self.gui.get_output_mode_key()
        if self.target_stage in {"transcript", "translate"} or mode == "subtitle":
            self.pipeline_done()
            if self.progress_dialog:
                if self.target_stage == "transcript":
                    self.progress_dialog.skip_step("voiceover")
                elif self.target_stage == "translate":
                    self.progress_dialog.skip_step("voiceover")
                self.progress_dialog.set_completed()
            self.gui.log(f"[Pipeline] Reached requested stage: {self.target_stage}.")
        else:
            self.pipeline_advance("translation")

    def _notify_translation_fallback_if_used(self):
        """Show a UI notice when an AI translation request used Google fallback."""
        selected_provider = str(os.getenv("OPENAI_PROVIDER") or "google").strip().lower()
        if selected_provider == "google" or not self.gui.is_ai_polish_enabled():
            return
        models = list(getattr(self.gui, "current_translated_segment_models", []) or [])
        providers = {
            str(getattr(model, "metadata", {}).get("translation_provider", "") or "").strip().lower()
            for model in models
        }
        if "google-web" not in providers and "google" not in providers and "bing-web" not in providers and "bing" not in providers:
            return
        if "google-web" in providers or "google" in providers:
            self.gui._last_translation_provider = "google"
            notice = t("AI Provider is unavailable. Translation completed using Google Translate instead.")
        else:
            self.gui._last_translation_provider = "bing"
            notice = t("AI Provider is unavailable. Translation completed using Bing Translator instead.")
        signature = f"{getattr(self, 'prepare_run_id', 0)}:{selected_provider}"
        if getattr(self, "_fallback_notification_signature", "") == signature:
            return
        self._fallback_notification_signature = signature
        self.gui.log(f"[Translation] {notice}")
        QMessageBox.information(self.gui, t("Translation Fallback"), notice)

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
            if self.progress_dialog and self.progress_dialog.isVisible():
                self.progress_dialog.start_step("voiceover")
            self.gui.run_voiceover()
            
        elif completed_step == "voiceover":
            if getattr(self, "target_stage", "full") == "tts":
                self.pipeline_done()
                if self.progress_dialog:
                    self.progress_dialog.skip_step("preview")
                    self.progress_dialog.set_completed(t("✨ AI Voiceover complete! Audio track is ready."))
                    self.progress_dialog.raise_()
                    self.progress_dialog.activateWindow()
                QMessageBox.information(
                    self.gui,
                    t("Success"),
                    t("AI Voiceover generation finished successfully!\n\nThe new voice track is loaded and ready on the timeline."),
                )
                return
            self.gui._pipeline_step = "preview"
            if self.progress_dialog and self.progress_dialog.isVisible():
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
        self._stop_prepare_status_polling()
        self._stop_local_worker_server()
        
        if self.progress_dialog:
            current_step = getattr(self.gui, "_pipeline_step", "prepare")
            self.progress_dialog.fail_step(current_step)
            # Show the error reason in the footer
            self.progress_dialog.footer.setText(f"{t('FAILED')}: {t(reason)}")
            self.progress_dialog.footer.setStyleSheet("color: #FF4444; font-weight: bold;")

        # Restore UI
        overlay = getattr(self.gui, "ocr_region_overlay", None)
        if overlay is not None:
            overlay.hide()

        if hasattr(self.gui, "run_all_btn"):
            self.gui.run_all_btn.setEnabled(True)
            self.gui.run_all_btn.setText(t("Generate"))
        
        self.gui.progress_bar.setRange(0, 100)
        self.gui.progress_bar.setValue(0)
        self.gui.refresh_ui_state()

    def pipeline_done(self):
        """Marks the entire pipeline as successfully finished."""
        self.gui._pipeline_active = False
        self.gui._pipeline_step = ""
        self._stop_prepare_status_polling()
        self._stop_local_worker_server()
        
        if hasattr(self.gui, "run_all_btn"):
            self.gui.run_all_btn.setEnabled(True)
            self.gui.run_all_btn.setText(t("Generate"))
            
        self.gui.progress_bar.setRange(0, 100)
        self.gui.progress_bar.setValue(100)
        self.gui.refresh_ui_state()
