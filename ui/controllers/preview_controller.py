import os
import time

from PySide6.QtCore import QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QMessageBox

from workers import ExactFramePreviewWorker, FinalExportWorker, PreviewMuxWorker, QuickPreviewWorker


class PreviewController:
    def __init__(self, gui):
        self.gui = gui

    def export_final_video(self):
        video_path = self.gui.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, "Error", "Please choose a video first.")
            return

        mode = self.gui.get_output_mode_key()
        out_dir = os.path.join(os.getcwd(), "temp")
        os.makedirs(out_dir, exist_ok=True)

        video_name = os.path.splitext(os.path.basename(video_path))[0]
        translated_srt_path = self.gui.last_translated_srt_path
        chosen_audio = self.gui.resolve_selected_audio_path()

        if mode in ("subtitle", "both") and (not translated_srt_path or not os.path.exists(translated_srt_path)):
            QMessageBox.warning(self.gui, "Error", "Vietnamese subtitle file not found. Please run translation first.")
            return

        if mode in ("voice", "both") and (not chosen_audio or not os.path.exists(chosen_audio)):
            QMessageBox.warning(
                self.gui,
                "Error",
                "Selected audio source is not ready. Generate voice/mix first, or switch to 'Use existing mixed audio' and choose a valid file.",
            )
            return

        if mode == "subtitle":
            output_path = os.path.join(out_dir, f"{video_name}_sub_vi.mp4")
        elif mode == "voice":
            output_path = os.path.join(out_dir, f"{video_name}_voice_vi.mp4")
        else:
            output_path = os.path.join(out_dir, f"{video_name}_final_vi.mp4")

        self.gui.export_btn.setEnabled(False)
        self.gui.export_btn.setText("Exporting...")
        self.gui.progress_bar.setValue(96)
        self.gui.update_project_step("export", "running")

        project_state_path = self.gui.project_service.project_file(self.gui.current_project_state.project_root) if self.gui.current_project_state else ""
        self.gui.export_thread = FinalExportWorker(
            workspace_root=self.gui.workspace_root,
            video_path=video_path,
            output_path=output_path,
            mode=mode,
            srt_path=translated_srt_path,
            audio_path=chosen_audio,
            subtitle_style=self.gui.get_subtitle_export_style(),
            project_state_path=project_state_path,
        )
        self.gui.export_thread.finished.connect(self.gui.on_export_finished)
        self.gui.export_thread.start()

    def preview_five_seconds(self):
        video_path = self.gui.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, "Error", "Please choose a video first.")
            return

        mode = self.gui.get_output_mode_key()
        out_dir = self.gui.final_output_folder_edit.text().strip() or os.path.join(os.getcwd(), "output")
        os.makedirs(out_dir, exist_ok=True)

        translated_srt_path = self.gui.last_translated_srt_path
        chosen_audio = self.gui.resolve_selected_audio_path()

        if mode in ("subtitle", "both") and (not translated_srt_path or not os.path.exists(translated_srt_path)):
            QMessageBox.warning(self.gui, "Error", "Vietnamese subtitle file not found. Please run translation first.")
            return

        if mode in ("voice", "both") and (not chosen_audio or not os.path.exists(chosen_audio)):
            QMessageBox.warning(
                self.gui,
                "Error",
                "Selected audio source is not ready. Generate voice/mix first, or switch to 'Use existing mixed audio' and choose a valid file.",
            )
            return

        start_seconds = max(0.0, self.gui.media_player.position() / 1000.0)
        duration_seconds = 5.0
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        if self.gui.last_preview_video_path and self.gui.last_preview_video_path != self.gui.last_exact_preview_5s_path:
            self.gui.cleanup_file_if_exists(self.gui.last_preview_video_path)
            self.gui.processed_artifacts.pop("preview_video", None)
            self.gui.last_preview_video_path = ""
        self.gui.cleanup_file_if_exists(self.gui.last_exact_preview_5s_path)
        preview_output = os.path.join(out_dir, f"{video_name}_preview5s_{int(time.time())}.mp4")
        preview_srt_path = ""

        if mode in ("subtitle", "both"):
            preview_srt_path = self.build_subtitle_preview_srt(start_seconds, duration_seconds)
            if not preview_srt_path:
                QMessageBox.warning(self.gui, "Error", "Could not build the 5-second subtitle preview clip.")
                return

        self.gui.preview_5s_btn.setEnabled(False)
        self.gui.preview_5s_btn.setText("Rendering 5s...")
        self.gui.progress_bar.setValue(92)

        try:
            self.gui.media_player.stop()
            self.gui.media_player.setSource(QUrl())
        except Exception:
            pass

        self.gui.quick_preview_thread = QuickPreviewWorker(
            video_path=video_path,
            output_path=preview_output,
            mode=mode,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            srt_path=preview_srt_path,
            audio_path=chosen_audio,
            subtitle_style=self.gui.get_subtitle_export_style(),
        )
        self.gui.quick_preview_thread.finished.connect(self.gui.on_quick_preview_ready)
        self.gui.quick_preview_thread.start()

    def start_exact_frame_preview(self, show_dialog: bool = True):
        video_path = self.gui.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            if show_dialog:
                QMessageBox.warning(self.gui, "Error", "Please choose a video first.")
            return

        preview_srt_path = self.build_full_active_subtitle_srt()
        if not preview_srt_path:
            if show_dialog:
                QMessageBox.warning(self.gui, "Error", "No active subtitle track is available for frame preview.")
            return

        if self.gui._frame_preview_running:
            self.gui._pending_auto_frame_preview = True
            self.gui._show_dialog_on_frame_preview = self.gui._show_dialog_on_frame_preview or show_dialog
            return

        timestamp_seconds = max(0.0, self.gui.media_player.position() / 1000.0)
        out_dir = os.path.join(os.getcwd(), "temp")
        os.makedirs(out_dir, exist_ok=True)
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        self.gui.cleanup_file_if_exists(self.gui.last_exact_preview_frame_path)
        frame_output = os.path.join(out_dir, f"{video_name}_preview_frame_{int(time.time())}.png")

        self.gui._frame_preview_running = True
        self.gui._show_dialog_on_frame_preview = show_dialog
        self.gui.preview_frame_btn.setEnabled(False)
        self.gui.preview_frame_btn.setText("Rendering frame...")
        self.gui.progress_bar.setValue(90)
        self.gui.frame_preview_status_label.setText("Rendering exact frame preview...")

        self.gui.frame_preview_thread = ExactFramePreviewWorker(
            video_path=video_path,
            output_path=frame_output,
            timestamp_seconds=timestamp_seconds,
            srt_path=preview_srt_path,
            subtitle_style=self.gui.get_subtitle_export_style(),
        )
        self.gui.frame_preview_thread.finished.connect(self.gui.on_exact_frame_ready)
        self.gui.frame_preview_thread.start()

    def build_subtitle_preview_srt(self, start_seconds: float, duration_seconds: float):
        segments = self.gui.get_active_segments()
        if not segments:
            return ""

        clipped = []
        end_seconds = start_seconds + duration_seconds
        for seg in segments:
            seg_start = float(seg["start"])
            seg_end = float(seg["end"])
            if seg_end < start_seconds or seg_start > end_seconds:
                continue
            clipped.append(
                {
                    "start": max(0.0, seg_start - start_seconds),
                    "end": min(duration_seconds, seg_end - start_seconds),
                    "text": seg.get("text", ""),
                }
            )

        if not clipped:
            return ""

        preview_srt_path = os.path.join(os.getcwd(), "temp", "preview_subtitle_5s.srt")
        self.gui.cleanup_file_if_exists(preview_srt_path)
        from subtitle_builder import generate_srt

        generate_srt(clipped, preview_srt_path)
        return preview_srt_path

    def build_full_active_subtitle_srt(self):
        segments = self.gui.get_active_segments()
        if not segments:
            return ""
        preview_srt_path = os.path.join(os.getcwd(), "temp", "preview_subtitle_full.srt")
        self.gui.cleanup_file_if_exists(preview_srt_path)
        from subtitle_builder import generate_srt

        generate_srt(segments, preview_srt_path)
        return preview_srt_path

    def on_export_finished(self, output_path, error):
        self.gui.export_btn.setEnabled(True)
        self.gui.on_output_mode_changed(self.gui.output_mode_combo.currentText())
        self.gui.progress_bar.setValue(100)

        if error:
            self.gui.update_project_step("export", "failed")
            self.gui.show_error("Error", "Final export failed.", error)
            return

        if output_path and os.path.exists(output_path):
            self.gui.last_exported_video_path = output_path
            self.gui.processed_artifacts["final_video"] = output_path
            self.gui.update_project_artifact("final_video", output_path)
            self.gui.update_project_step("export", "done")
            self.gui.cleanup_temp_preview_files()
            self.gui.frame_preview_image_label.setText("No frame preview yet")
            self.gui.frame_preview_image_label.setPixmap(QPixmap())
            self.gui.frame_preview_status_label.setText("Exact frame preview updates here when available.")
            QMessageBox.information(self.gui, "Success", f"Final video exported successfully:\n\n{output_path}")

    def on_quick_preview_ready(self, output_path, error):
        self.gui.preview_5s_btn.setEnabled(True)
        self.gui.preview_5s_btn.setText("Open 5-Second Preview")
        self.gui.progress_bar.setValue(100)

        if error:
            self.gui.show_error("Error", "5-second preview failed.", error)
            return

        if output_path and os.path.exists(output_path):
            self.gui.last_exact_preview_5s_path = output_path
            self.gui.last_preview_video_path = output_path
            self.gui.processed_artifacts["preview_video_5s"] = output_path
            self.gui.refresh_video_dimensions(output_path)
            self.gui.media_player.setSource(QUrl.fromLocalFile(output_path))
            self.gui.media_player.setPosition(0)
            self.gui.play_btn.setText("Play")
            QMessageBox.information(
                self.gui,
                "Preview Ready",
                f"Generated a 5-second preview clip with the current export style:\n\n{output_path}",
            )

    def on_exact_frame_ready(self, output_path, error):
        self.gui._frame_preview_running = False
        self.gui.preview_frame_btn.setEnabled(True)
        self.gui.preview_frame_btn.setText("Open Large Frame Preview")
        self.gui.progress_bar.setValue(100)

        if error:
            self.gui.show_error("Error", "Exact frame preview failed.", error)
            self.gui.frame_preview_status_label.setText("Exact frame preview failed. You can try again.")
        elif output_path and os.path.exists(output_path):
            self.gui.last_exact_preview_frame_path = output_path
            self.gui.processed_artifacts["preview_frame"] = output_path
            self.gui.update_frame_preview_thumbnail(output_path)
            if self.gui._show_dialog_on_frame_preview:
                self.gui.show_frame_preview_dialog(output_path)

        self.gui._show_dialog_on_frame_preview = False
        if self.gui._pending_auto_frame_preview:
            self.gui._pending_auto_frame_preview = False
            if self.gui.auto_preview_frame_cb.isChecked():
                self.gui.schedule_auto_frame_preview()

    def preview_video_with_mixed_audio(self):
        video_path = self.gui.video_path_edit.text().strip()
        audio_path = self.gui.resolve_selected_audio_path()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, "Error", "Video file not found. Please select a video first.")
            return
        if not audio_path or not os.path.exists(audio_path):
            QMessageBox.warning(
                self.gui,
                "Error",
                "Selected audio source is not ready. Generate voice/mix first, or switch to 'Use existing mixed audio' and choose a valid file.",
            )
            return

        ts = int(time.time())
        if self.gui.last_preview_video_path and self.gui.last_preview_video_path != self.gui.last_exact_preview_5s_path:
            self.gui.cleanup_file_if_exists(self.gui.last_preview_video_path)
            self.gui.processed_artifacts.pop("preview_video", None)
            self.gui.last_preview_video_path = ""
        preview_out = os.path.join(os.getcwd(), "temp", f"preview_vi_voice_{ts}.mp4")

        try:
            self.gui.media_player.stop()
            self.gui.media_player.setSource(QUrl())
        except Exception:
            pass

        self.gui.log(f"[Preview] video={video_path}")
        self.gui.log(f"[Preview] audio={audio_path}")
        self.gui.log(f"[Preview] out={preview_out}")
        self.gui.preview_btn.setEnabled(False)
        self.gui.preview_btn.setText("Preparing preview...")
        self.gui.progress_bar.setValue(95)

        self.gui.preview_thread = PreviewMuxWorker(video_path, audio_path, preview_out)
        self.gui.preview_thread.finished.connect(self.gui.on_preview_ready)
        self.gui.preview_thread.start()

    def on_preview_ready(self, preview_path, error):
        self.gui.preview_btn.setEnabled(True)
        self.gui.preview_btn.setText("Open Video Preview With Selected Audio")
        self.gui.progress_bar.setValue(100)

        if error:
            self.gui.show_error("Error", "Preview failed.", str(error))
            self.gui._pipeline_fail("Preview failed.")
            return

        if preview_path and os.path.exists(preview_path):
            self.gui.last_preview_video_path = preview_path
            self.gui.processed_artifacts["preview_video"] = preview_path
            self.gui.log(f"[Preview] ready={preview_path}")
            self.gui.refresh_video_dimensions(preview_path)
            self.gui.media_player.setSource(QUrl.fromLocalFile(preview_path))
            self.gui.play_btn.setText("Play")
            QMessageBox.information(
                self.gui,
                "Preview Ready",
                "Loaded the preview video into the player.\nPress Play to review it, then click 'Export Final Video' when you are satisfied.",
            )
            self.gui._pipeline_done()
            self.gui.apply_segments_to_timeline()
            self.gui.refresh_ui_state()
