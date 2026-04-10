import os
import hashlib
import json
import time

from PySide6.QtCore import QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QMessageBox

from workers import ExactFramePreviewWorker, FinalExportWorker, PreviewMuxWorker, QuickPreviewWorker


class PreviewController:
    def __init__(self, gui):
        self.gui = gui

    def _file_signature(self, path: str) -> dict:
        if not path or not os.path.exists(path):
            return {"path": "", "size": 0, "mtime_ns": 0}
        stat = os.stat(path)
        return {
            "path": os.path.abspath(path),
            "size": int(stat.st_size),
            "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
        }

    def _text_file_hash(self, path: str) -> str:
        if not path or not os.path.exists(path):
            return ""
        hasher = hashlib.sha1()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    def _build_styled_preview_signature(self, *, video_path: str, audio_path: str, mode: str, srt_path: str, subtitle_style: dict) -> str:
        payload = {
            "kind": "styled_preview_v2",
            "mode": mode,
            "video": self._file_signature(video_path),
            "audio": self._file_signature(audio_path),
            "subtitle_path": os.path.abspath(srt_path) if srt_path and os.path.exists(srt_path) else "",
            "subtitle_hash": self._text_file_hash(srt_path),
            "subtitle_style": subtitle_style or {},
        }
        return hashlib.sha1(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()

    def export_final_video(self):
        video_path = self.gui.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, "Error", "Please choose a video first.")
            return

        mode = self.gui.get_output_mode_key()
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        translated_srt_path = self.gui.last_translated_srt_path
        translated_ass_path = self.gui.live_preview_ass_path
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

        default_dir = self.gui.final_output_folder_edit.text().strip() or os.path.join(os.getcwd(), "output")
        os.makedirs(default_dir, exist_ok=True)
        if mode == "subtitle":
            suggested_name = f"{video_name}_sub_vi.mp4"
        elif mode == "voice":
            suggested_name = f"{video_name}_voice_vi.mp4"
        else:
            suggested_name = f"{video_name}_final_vi.mp4"

        default_path = os.path.join(default_dir, suggested_name)
        output_path, _ = QFileDialog.getSaveFileName(
            self.gui,
            "Export Final Video",
            default_path,
            "Video Files (*.mp4)",
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".mp4"):
            output_path += ".mp4"

        chosen_dir = os.path.dirname(output_path)
        if chosen_dir:
            self.gui.final_output_folder_edit.setText(chosen_dir)

        self.gui.export_btn.setEnabled(False)
        self.gui.export_btn.setText("Exporting...")
        self.gui.progress_bar.setValue(96)
        self.gui.update_project_step("export", "running")
        self.gui._export_progress_messages = ["Preparing final export..."]
        self.gui.on_export_progress(5, "Preparing final export...")

        project_state_path = self.gui.project_service.project_file(self.gui.current_project_state.project_root) if self.gui.current_project_state else ""
        self.gui.export_thread = FinalExportWorker(
            workspace_root=self.gui.workspace_root,
            video_path=video_path,
            output_path=output_path,
            mode=mode,
            srt_path=translated_srt_path,
            ass_path=translated_ass_path,
            audio_path=chosen_audio,
            subtitle_style=self.gui.get_subtitle_export_style(segments=self.gui.get_active_segments()),
            output_quality=self.gui.get_output_quality_key(),
            project_state_path=project_state_path,
        )
        self.gui.export_thread.progress.connect(self.gui.on_export_progress)
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
            try:
                # Release file handle so Windows can delete the previous preview clip.
                self.gui.media_player.stop()
                self.gui.media_player.setSource(QUrl())
            except Exception:
                pass
            self.gui.cleanup_file_if_exists(self.gui.last_preview_video_path)
            self.gui.processed_artifacts.pop("preview_video", None)
            self.gui.last_preview_video_path = ""
        self.gui.cleanup_file_if_exists(self.gui.last_exact_preview_5s_path)
        preview_output = os.path.join(out_dir, f"{video_name}_preview5s_{int(time.time())}.mp4")
        preview_srt_path = ""
        preview_segments = []

        if mode in ("subtitle", "both"):
            preview_srt_path, preview_segments = self.build_subtitle_preview_srt(start_seconds, duration_seconds)
            if not preview_srt_path:
                QMessageBox.warning(self.gui, "Error", "Could not build the 5-second subtitle preview clip.")
                return

        self.gui.preview_5s_btn.setEnabled(False)
        self.gui.preview_5s_btn.setText("Rendering 5s...")
        self.gui.progress_bar.setValue(92)

        try:
            self.gui.media_player.pause()
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
            subtitle_style=self.gui.get_subtitle_export_style(segments=preview_segments),
            render_subtitles=False,
        )
        self.gui.quick_preview_thread.finished.connect(self.gui.on_quick_preview_ready)
        self.gui.quick_preview_thread.start()

    def start_exact_frame_preview(self, show_dialog: bool = True):
        video_path = self.gui.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            if show_dialog:
                QMessageBox.warning(self.gui, "Error", "Please choose a video first.")
            return

        preview_srt_path, preview_segments = self.build_full_active_subtitle_srt()
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
            subtitle_style=self.gui.get_subtitle_export_style(segments=preview_segments),
        )
        self.gui.frame_preview_thread.finished.connect(self.gui.on_exact_frame_ready)
        self.gui.frame_preview_thread.start()

    def build_subtitle_preview_srt(self, start_seconds: float, duration_seconds: float):
        segments = self.gui.get_active_segments()
        if not segments:
            return "", []

        clipped = []
        end_seconds = start_seconds + duration_seconds
        for seg in segments:
            seg_start = float(seg["start"])
            seg_end = float(seg["end"])
            if seg_end < start_seconds or seg_start > end_seconds:
                continue
            clipped_words = []
            for word in seg.get("words", []) or []:
                try:
                    word_start = float(word.get("start", 0.0))
                    word_end = float(word.get("end", 0.0))
                except (TypeError, ValueError, AttributeError):
                    continue
                if word_end < start_seconds or word_start > end_seconds:
                    continue
                clipped_words.append(
                    {
                        "start": max(0.0, word_start - start_seconds),
                        "end": min(duration_seconds, word_end - start_seconds),
                        "text": str(word.get("text", "") or "").strip(),
                    }
                )
            clipped.append(
                {
                    "start": max(0.0, seg_start - start_seconds),
                    "end": min(duration_seconds, seg_end - start_seconds),
                    "text": seg.get("text", ""),
                    "words": clipped_words,
                    "manual_highlights": list(seg.get("manual_highlights", [])),
                }
            )

        if not clipped:
            return "", []

        preview_srt_path = os.path.normpath(os.path.join(os.getcwd(), "temp", "preview_subtitle_5s.srt"))
        self.gui.cleanup_file_if_exists(preview_srt_path)
        from subtitle_builder import generate_srt

        generate_srt(clipped, preview_srt_path)
        return preview_srt_path, clipped

    def build_full_active_subtitle_srt(self):
        segments = self.gui.get_active_segments()
        if not segments:
            return "", []
        preview_srt_path = os.path.normpath(os.path.join(os.getcwd(), "temp", "preview_subtitle_full.srt"))
        self.gui.cleanup_file_if_exists(preview_srt_path)
        from subtitle_builder import generate_srt

        generate_srt(segments, preview_srt_path)
        return preview_srt_path, segments

    def on_export_finished(self, output_path, error):
        self.gui._close_export_progress_dialog()
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
            self.gui.log(f"[Export] Final video exported successfully: {output_path}")
            self.gui.log("[Export] Kept current preview/subtitle state so you can continue editing after export.")

    def on_quick_preview_ready(self, output_path, error):
        self.gui._suspend_live_subtitle_sync = False
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
            self.gui.sync_live_subtitle_preview()
            self.gui.play_btn.setText("Play")
            # QMessageBox.information(
            #     self.gui,
            #     "Preview Ready",
            #     "Loaded the styled preview into the player.\nPress Play to review the karaoke render, then click 'Export Final Video' when you are satisfied.",
            # )
            self.gui._pipeline_done()
            self.gui.apply_segments_to_timeline()
            self.gui.refresh_ui_state()

    def preview_video(self):
        self._start_video_preview()

    def _start_video_preview(self):
        video_path = self.gui.video_path_edit.text().strip()
        audio_path = self.gui.resolve_selected_audio_path()
        mode = self.gui.get_output_mode_key()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, "Error", "Video file not found. Please select a video first.")
            return
        if mode in ("voice", "both") and (not audio_path or not os.path.exists(audio_path)):
            QMessageBox.warning(
                self.gui,
                "Error",
                "Selected audio source is not ready. Generate voice/mix first, or switch to 'Use existing mixed audio' and choose a valid file.",
            )
            return

        # Subtitle-only preview uses mpv's live subtitle overlay; avoid copying the full video into temp.
        if mode == "subtitle":
            try:
                self.gui.media_player.setSource(QUrl.fromLocalFile(video_path))
                self.gui.sync_live_subtitle_preview()
                self.gui.play_btn.setText("Play")
                self.gui.refresh_ui_state()
            except Exception:
                pass
            return
        ts = int(time.time())
        preview_out = os.path.normpath(os.path.join(os.getcwd(), "temp", f"preview_vi_voice_{ts}.mp4"))
        preview_srt_path = ""
        preview_segments = []
        subtitle_style = {}
        styled_signature = ""
        cached_preview = ""
        if mode in ("subtitle", "both"):
            preview_srt_path, preview_segments = self.build_full_active_subtitle_srt()
            if not preview_srt_path:
                QMessageBox.warning(self.gui, "Error", "No active subtitle track is available for video preview.")
                return
            subtitle_style = self.gui.get_subtitle_export_style(segments=preview_segments)
            styled_signature = self._build_styled_preview_signature(
                video_path=video_path,
                audio_path=audio_path,
                mode=mode,
                srt_path=preview_srt_path,
                subtitle_style=subtitle_style,
            )
            cached_preview = str(getattr(self.gui, "last_styled_preview_path", "") or "").strip()
            cached_signature = str(getattr(self.gui, "last_styled_preview_signature", "") or "").strip()
            if cached_preview and cached_signature == styled_signature and os.path.exists(cached_preview):
                self.gui.log(f"[Preview] styled cache hit: {cached_preview}")
                self.gui.on_preview_ready(cached_preview, "", styled_signature)
                return

        if self.gui.last_preview_video_path and self.gui.last_preview_video_path != self.gui.last_exact_preview_5s_path:
            if not (cached_preview and os.path.abspath(self.gui.last_preview_video_path) == os.path.abspath(cached_preview)):
                try:
                    # Release file handle so Windows can delete the previous preview clip.
                    self.gui.media_player.stop()
                    self.gui.media_player.setSource(QUrl())
                except Exception:
                    pass
                self.gui.cleanup_file_if_exists(self.gui.last_preview_video_path)
            self.gui.processed_artifacts.pop("preview_video", None)
            self.gui.last_preview_video_path = ""

        try:
            self.gui.media_player.pause()
        except Exception:
            pass

        self.gui.log(f"[Preview] video={video_path}")
        self.gui.log(f"[Preview] audio={audio_path or '<none>'}")
        self.gui.log(f"[Preview] out={preview_out}")
        self.gui.log(f"[Preview] app_mode={mode}")
        self.gui.log("[Preview] render_mode=styled")
        if styled_signature:
            self.gui.log(f"[Preview] styled cache miss: {styled_signature[:10]}")
        self.gui._styled_preview_running = True
        if hasattr(self.gui, "preview_btn"):
            self.gui.preview_btn.setEnabled(False)
            self.gui.preview_btn.setText("Preparing...")
        self.gui.progress_bar.setValue(95)
        self.gui.refresh_ui_state()

        self.gui.preview_thread = PreviewMuxWorker(
            video_path,
            audio_path,
            preview_out,
            mode=mode,
            srt_path=preview_srt_path,
            subtitle_style=subtitle_style,
            render_subtitles=False,
        )
        self.gui.preview_thread.finished.connect(
            lambda preview_path, error: self.gui.on_preview_ready(preview_path, error, styled_signature)
        )
        self.gui.preview_thread.start()

    def on_preview_ready(self, preview_path, error, styled_signature=""):
        self.gui._styled_preview_running = False
        self.gui._suspend_live_subtitle_sync = False
        if hasattr(self.gui, "preview_btn"):
            self.gui.preview_btn.setEnabled(True)
            self.gui.preview_btn.setText("Refresh Preview")
        self.gui.progress_bar.setValue(100)
        self.gui.refresh_ui_state()

        if error:
            self.gui._suspend_live_subtitle_sync = False
            self.gui.show_error("Error", "Preview failed.", str(error))
            self.gui._pipeline_fail("Preview failed.")
            return

        if preview_path and os.path.exists(preview_path):
            self.gui.last_preview_video_path = preview_path
            self.gui.processed_artifacts["preview_video"] = preview_path
            self.gui.last_styled_preview_path = preview_path
            self.gui.last_styled_preview_signature = styled_signature
            self.gui.log(f"[Preview] ready={preview_path}")
            self.gui.refresh_video_dimensions(preview_path)
            self.gui.media_player.setSource(QUrl.fromLocalFile(preview_path))
            self.gui.sync_live_subtitle_preview()
            self.gui.play_btn.setText("Play")
            # QMessageBox.information(
            #     self.gui,
            #     "Preview Ready",
            #     "Loaded the styled preview into the player.\nPress Play to review the karaoke render, then click 'Export Final Video' when you are satisfied.",
            # )
            self.gui.pipeline_controller.pipeline_advance("preview")
            self.gui.apply_segments_to_timeline()
            self.gui.refresh_ui_state()






