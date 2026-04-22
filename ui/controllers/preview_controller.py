import os
import hashlib
import json
import shutil
import subprocess
import time

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QMessageBox

from runtime_paths import bin_path
from workers import ExactFramePreviewWorker, FinalExportWorker, PreviewMuxWorker, QuickPreviewWorker


class PreviewController:
    def __init__(self, gui):
        self.gui = gui

    @staticmethod
    def _format_duration_ms(duration_ms: int) -> str:
        total_seconds = max(0, int(round(float(duration_ms or 0) / 1000.0)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        value = float(max(0, int(num_bytes or 0)))
        units = ["B", "KB", "MB", "GB", "TB"]
        unit_idx = 0
        while value >= 1024.0 and unit_idx < len(units) - 1:
            value /= 1024.0
            unit_idx += 1
        return f"{value:.1f} {units[unit_idx]}"

    def _probe_source_fps(self, video_path: str) -> str:
        ffprobe_candidates = [
            bin_path("ffmpeg", "ffprobe.exe"),
            bin_path("ffprobe.exe"),
            shutil.which("ffprobe"),
            shutil.which("ffprobe.exe"),
        ]
        ffprobe_path = ""
        for candidate in ffprobe_candidates:
            if candidate and os.path.isfile(candidate):
                ffprobe_path = candidate
                break
        if not ffprobe_path:
            return "Unknown"
        try:
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                [
                    ffprobe_path,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=r_frame_rate",
                    "-of",
                    "default=nw=1:nk=1",
                    video_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                startupinfo=startupinfo,
                creationflags=creationflags,
                check=False,
            )
            raw = str(result.stdout or "").strip()
            if not raw:
                return "Unknown"
            if "/" in raw:
                num, den = raw.split("/", 1)
                fps = float(num) / max(1.0, float(den))
            else:
                fps = float(raw)
            return f"{fps:.2f}".rstrip("0").rstrip(".")
        except Exception:
            return "Unknown"

    def _resolve_export_resolution_label(self, video_path: str, output_quality: str) -> str:
        try:
            from video_processor import get_video_dimensions

            src_w, src_h = get_video_dimensions(video_path)
        except Exception:
            src_w, src_h = 0, 0

        if not src_w or not src_h:
            return "Unknown"

        target_w, target_h = self._resolve_output_canvas_dimensions(video_path)
        if target_w and target_h:
            return f"{target_w}x{target_h}"

        key = str(output_quality or "source").strip().lower()
        if key in ("", "source", "same", "original", "auto"):
            return f"{src_w}x{src_h} (Source)"

        portrait = src_h > src_w
        if key in ("720", "720p", "hd"):
            base_w, base_h = (720, 1280) if portrait else (1280, 720)
        elif key in ("1080", "1080p", "fullhd", "fhd", "full hd", "full"):
            base_w, base_h = (1080, 1920) if portrait else (1920, 1080)
        elif key in ("1440", "1440p", "2k", "qhd"):
            base_w, base_h = (1440, 2560) if portrait else (2560, 1440)
        elif key in ("2160", "2160p", "4k", "uhd"):
            base_w, base_h = (2160, 3840) if portrait else (3840, 2160)
        else:
            return f"{src_w}x{src_h}"

        if src_w <= base_w and src_h <= base_h:
            return f"{src_w}x{src_h} (Source)"
        return f"{base_w}x{base_h}"

    def _resolve_output_canvas_dimensions(self, video_path: str):
        try:
            from video_processor import get_video_dimensions
            src_w, src_h = get_video_dimensions(video_path)
        except Exception:
            return None, None
        if not src_w or not src_h:
            return None, None

        output_quality = self.gui.get_output_quality_key()
        output_ratio = self.gui.get_output_ratio_key()
        ratio_map = {
            "16:9": (16, 9),
            "9:16": (9, 16),
            "1:1": (1, 1),
            "4:3": (4, 3),
        }
        ratio = ratio_map.get(str(output_ratio or "source").strip().lower())
        quality_key = str(output_quality or "source").strip().lower()

        if quality_key in ("", "source", "same", "original", "auto"):
            if not ratio:
                return None, None
            src_ratio = src_w / src_h
            target_ratio = ratio[0] / ratio[1]
            if abs(src_ratio - target_ratio) < 0.001:
                return None, None
            fit_scale = min(src_w / ratio[0], src_h / ratio[1])
            return (
                max(2, int((ratio[0] * fit_scale) // 2 * 2)),
                max(2, int((ratio[1] * fit_scale) // 2 * 2)),
            )

        if quality_key in ("720", "720p", "hd"):
            short_edge = 720
        elif quality_key in ("1080", "1080p", "fullhd", "fhd", "full hd", "full"):
            short_edge = 1080
        elif quality_key in ("1440", "1440p", "2k", "qhd"):
            short_edge = 1440
        elif quality_key in ("2160", "2160p", "4k", "uhd"):
            short_edge = 2160
        else:
            return None, None

        if ratio:
            scale = short_edge / min(ratio)
            target_w = max(2, int((ratio[0] * scale) // 2 * 2))
            target_h = max(2, int((ratio[1] * scale) // 2 * 2))
            if src_w <= target_w and src_h <= target_h:
                fit_scale = min(src_w / ratio[0], src_h / ratio[1])
                target_w = max(2, int((ratio[0] * fit_scale) // 2 * 2))
                target_h = max(2, int((ratio[1] * fit_scale) // 2 * 2))
            return target_w, target_h

        portrait = src_h > src_w
        target_w = short_edge if portrait else int(round(short_edge * 16 / 9))
        target_h = int(round(short_edge * 16 / 9)) if portrait else short_edge
        if src_w <= target_w and src_h <= target_h:
            return None, None
        return target_w, target_h

    def _confirm_export_summary(self, *, video_path: str, output_path: str, mode: str, audio_path: str):
        output_quality = self.gui.get_output_quality_key()
        output_fps = self.gui.get_output_fps_key()
        source_fps = self._probe_source_fps(video_path)
        duration_ms = 0
        try:
            duration_ms = int(getattr(self.gui.media_player, "duration", lambda: 0)() or 0)
        except Exception:
            duration_ms = int(getattr(self.gui.timeline, "duration", 0) or 0)

        try:
            source_size = self._format_bytes(os.path.getsize(video_path))
        except Exception:
            source_size = "Unknown"

        mode_label = {
            "subtitle": "Subtitle only",
            "voice": "Voice only",
            "both": "Subtitle + voice",
        }.get(str(mode or "").strip().lower(), str(mode or "Unknown"))
        fps_label = f"{source_fps} FPS (Source)" if output_fps == "source" else f"{output_fps} FPS"
        canvas_label = self.gui.get_output_scale_mode_key().capitalize()
        focus_x, focus_y = self.gui.get_output_fill_focus()
        audio_label = "None"
        if mode in ("voice", "both"):
            audio_label = os.path.basename(audio_path) if audio_path else "Selected audio"

        summary_lines = [
            f"Name: {os.path.basename(output_path)}",
            f"Folder: {os.path.dirname(output_path)}",
            f"Mode: {mode_label}",
            f"Duration: {self._format_duration_ms(duration_ms)}",
            f"Resolution: {self._resolve_export_resolution_label(video_path, output_quality)}",
            f"FPS: {fps_label}",
            f"Canvas: {canvas_label}",
            f"Framing: {int(round(focus_x * 100))}% x / {int(round(focus_y * 100))}% y" if self.gui.get_output_scale_mode_key() == "fill" else "Framing: Center",
            f"Source Size: {source_size}",
            f"Audio: {audio_label}",
        ]

        box = QMessageBox(self.gui)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Export Summary")
        box.setText("Review export details before starting.")
        box.setInformativeText("\n".join(summary_lines))
        start_btn = box.addButton("Start Export", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is start_btn

    def _prepare_current_export_srt(self) -> str:
        segments = list(self.gui.get_active_segments() or [])
        if not segments:
            return str(self.gui.last_translated_srt_path or "").strip()

        out_path = str(self.gui.last_translated_srt_path or "").strip()
        if not out_path:
            video_path = self.gui.video_path_edit.text().strip()
            video_name = os.path.splitext(os.path.basename(video_path or "subtitle"))[0]
            out_dir = self.gui.srt_output_folder_edit.text().strip() or os.path.join(self.gui.workspace_root, "output")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{video_name}_vi.srt")

        from subtitle_builder import generate_srt

        generate_srt(segments, out_path)
        self.gui.last_translated_srt_path = out_path
        self.gui.processed_artifacts["srt_translated"] = out_path
        self.gui.persist_translation_project_data(self.gui.current_translated_segments, out_path)
        return out_path

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
            "output_quality": self.gui.get_output_quality_key(),
            "output_ratio": self.gui.get_output_ratio_key(),
            "output_scale_mode": self.gui.get_output_scale_mode_key(),
            "output_fill_focus": self.gui.get_output_fill_focus(),
            "output_fps": self.gui.get_output_fps_key(),
            "video_filter": self.gui.get_video_filter_state() if hasattr(self.gui, "get_video_filter_state") else {},
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

        if mode in ("subtitle", "both"):
            translated_srt_path = self._prepare_current_export_srt()
            # Force export to rebuild styled subtitles from the latest SRT instead of a stale ASS preview cache.
            translated_ass_path = ""

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

        default_dir = self.gui.final_output_folder_edit.text().strip() or os.path.join(self.gui.workspace_root, "output")
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

        if not self._confirm_export_summary(
            video_path=video_path,
            output_path=output_path,
            mode=mode,
            audio_path=chosen_audio,
        ):
            return

        self.gui.export_btn.setEnabled(False)
        self.gui.export_btn.setText("Exporting...")
        self.gui.progress_bar.setValue(96)
        self.gui.update_project_step("export", "running")
        self.gui._export_progress_messages = ["Preparing final export..."]
        self.gui.on_export_progress(5, "Preparing final export...")

        project_state_path = self.gui.project_service.project_file(self.gui.current_project_state.project_root) if self.gui.current_project_state else ""
        fill_focus_x, fill_focus_y = self.gui.get_output_fill_focus()
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
            output_fps=self.gui.get_output_fps_key(),
            output_ratio=self.gui.get_output_ratio_key(),
            output_scale_mode=self.gui.get_output_scale_mode_key(),
            output_fill_focus_x=fill_focus_x,
            output_fill_focus_y=fill_focus_y,
            video_filter_state=self.gui.get_video_filter_state() if hasattr(self.gui, "get_video_filter_state") else {},
            project_state_path=project_state_path,
            project_temp_dir=self.gui.get_project_temp_dir("export"),
        )
        self.gui.export_thread.progress.connect(self.gui.on_export_progress)
        self.gui.export_thread.finished.connect(self.gui.on_export_finished)
        self.gui.export_thread.start()

    def preview_five_seconds(self):
        if hasattr(self.gui, "ensure_media_backend_ready"):
            self.gui.ensure_media_backend_ready()
        video_path = self.gui.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self.gui, "Error", "Please choose a video first.")
            return

        mode = self.gui.get_output_mode_key()
        out_dir = self.gui.final_output_folder_edit.text().strip() or os.path.join(self.gui.workspace_root, "output")
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
        target_width, target_height = self._resolve_output_canvas_dimensions(video_path)
        fill_focus_x, fill_focus_y = self.gui.get_output_fill_focus()
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
            target_width=target_width,
            target_height=target_height,
            output_scale_mode=self.gui.get_output_scale_mode_key(),
            output_fill_focus_x=fill_focus_x,
            output_fill_focus_y=fill_focus_y,
            video_filter_state=self.gui.get_video_filter_state() if hasattr(self.gui, "get_video_filter_state") else {},
            temp_dir=self.gui.get_project_temp_dir("preview"),
        )
        self.gui.quick_preview_thread.finished.connect(self.gui.on_quick_preview_ready)
        self.gui.quick_preview_thread.start()

    def start_exact_frame_preview(self, show_dialog: bool = True):
        if hasattr(self.gui, "ensure_media_backend_ready"):
            self.gui.ensure_media_backend_ready()
        video_path = self.gui.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            if show_dialog:
                QMessageBox.warning(self.gui, "Error", "Please choose a video first.")
            return

        mode = self.gui.get_output_mode_key()
        preview_srt_path = ""
        preview_segments = []
        if mode in ("subtitle", "both"):
            preview_srt_path, preview_segments = self.build_full_active_subtitle_srt()
        has_active_video_filters = bool(hasattr(self.gui, "has_active_video_filters") and self.gui.has_active_video_filters())
        if mode in ("subtitle", "both") and not preview_srt_path and not has_active_video_filters:
            if show_dialog:
                QMessageBox.warning(self.gui, "Error", "No active subtitle track is available for frame preview.")
            return

        if self.gui._frame_preview_running:
            self.gui._pending_auto_frame_preview = True
            self.gui._show_dialog_on_frame_preview = self.gui._show_dialog_on_frame_preview or show_dialog
            return

        timestamp_seconds = max(0.0, self.gui.media_player.position() / 1000.0)
        out_dir = self.gui.get_project_temp_dir("preview")
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        self.gui.cleanup_file_if_exists(self.gui.last_exact_preview_frame_path)
        frame_output = os.path.join(out_dir, f"{video_name}_preview_frame_{int(time.time())}.png")

        self.gui._frame_preview_running = True
        self.gui._show_dialog_on_frame_preview = show_dialog
        self.gui.preview_frame_btn.setEnabled(False)
        self.gui.preview_frame_btn.setText("Rendering frame...")
        self.gui.progress_bar.setValue(90)
        self.gui.frame_preview_status_label.setText("Rendering exact frame preview...")
        # For live filter thumbnail preview, render the source frame directly and let the UI
        # provide the black background. This keeps the actual video content larger.
        use_output_canvas = bool(show_dialog)
        target_width, target_height = ((None, None) if not use_output_canvas else self._resolve_output_canvas_dimensions(video_path))
        fill_focus_x, fill_focus_y = self.gui.get_output_fill_focus()

        self.gui.frame_preview_thread = ExactFramePreviewWorker(
            video_path=video_path,
            output_path=frame_output,
            timestamp_seconds=timestamp_seconds,
            srt_path=preview_srt_path,
            subtitle_style=self.gui.get_subtitle_export_style(segments=preview_segments),
            target_width=target_width,
            target_height=target_height,
            output_scale_mode=self.gui.get_output_scale_mode_key(),
            output_fill_focus_x=fill_focus_x,
            output_fill_focus_y=fill_focus_y,
            video_filter_state=self.gui.get_video_filter_state() if hasattr(self.gui, "get_video_filter_state") else {},
        )
        self.gui.frame_preview_thread.finished.connect(self.gui.on_exact_frame_ready)
        self.gui.frame_preview_thread.start()

    def on_exact_frame_ready(self, output_path, error):
        self.gui._frame_preview_running = False
        self.gui.preview_frame_btn.setEnabled(True)
        self.gui.preview_frame_btn.setText("Open Large Frame Preview")
        self.gui.progress_bar.setValue(100)

        if error:
            self.gui.frame_preview_status_label.setText("Frame preview could not be rendered.")
            if self.gui._show_dialog_on_frame_preview:
                self.gui.show_error("Error", "Frame preview failed.", str(error))
            else:
                self.gui.log(f"[Frame Preview] skipped: {error}")
            self.gui._show_dialog_on_frame_preview = False
            return

        if output_path and os.path.exists(output_path):
            self.gui.last_exact_preview_frame_path = output_path
            self.gui.processed_artifacts["preview_frame"] = output_path
            self.gui.update_frame_preview_thumbnail(output_path)
            if bool(hasattr(self.gui, "has_active_video_filters") and self.gui.has_active_video_filters()) and hasattr(self.gui, "show_filter_thumbnail_preview"):
                self.gui.show_filter_thumbnail_preview(output_path)
            if self.gui._show_dialog_on_frame_preview:
                self.gui.show_frame_preview_dialog(output_path)

        rerun_pending = bool(getattr(self.gui, "_pending_auto_frame_preview", False))
        self.gui._pending_auto_frame_preview = False
        self.gui._show_dialog_on_frame_preview = False
        if rerun_pending:
            QTimer.singleShot(0, self.gui.trigger_auto_frame_preview)

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

        preview_srt_path = self.gui.get_project_temp_path("preview", "preview_subtitle_5s.srt", create_parent=True)
        self.gui.cleanup_file_if_exists(preview_srt_path)
        from subtitle_builder import generate_srt

        generate_srt(clipped, preview_srt_path)
        return preview_srt_path, clipped

    def build_full_active_subtitle_srt(self):
        segments = self.gui.get_active_segments()
        if not segments:
            return "", []
        preview_srt_path = self.gui.get_project_temp_path("preview", "preview_subtitle_full.srt", create_parent=True)
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
        if hasattr(self.gui, "ensure_media_backend_ready"):
            self.gui.ensure_media_backend_ready()
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
            self.gui._preview_video_has_burned_subtitles = self.gui.get_output_mode_key() in ("subtitle", "both")
            if getattr(self.gui, "_preview_video_has_burned_subtitles", False):
                self.gui.media_player.clear_subtitle()
            else:
                self.gui.sync_live_subtitle_preview()
            self.gui._refresh_preview_audio_controls()
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
        if hasattr(self.gui, "ensure_media_backend_ready"):
            self.gui.ensure_media_backend_ready()
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

        has_active_video_filters = bool(hasattr(self.gui, "has_active_video_filters") and self.gui.has_active_video_filters())
        self.gui._preview_video_has_burned_subtitles = bool(mode == "subtitle" and has_active_video_filters)

        # Subtitle-only preview can stay live when no canvas/filter processing is needed.
        if mode == "subtitle" and not has_active_video_filters:
            try:
                self.gui._preview_video_has_burned_subtitles = False
                if hasattr(self.gui.video_view, "set_preview_aspect_ratio"):
                    self.gui.video_view.set_preview_aspect_ratio(self.gui.get_output_ratio_key())
                if hasattr(self.gui.video_view, "set_preview_scale_mode"):
                    self.gui.video_view.set_preview_scale_mode(self.gui.get_output_scale_mode_key())
                self.gui.media_player.setSource(QUrl.fromLocalFile(video_path))
                self.gui.sync_live_subtitle_preview()
                self.gui._refresh_preview_audio_controls()
                self.gui.refresh_ui_state()
            except Exception:
                pass
            return
        ts = int(time.time())
        preview_out = self.gui.get_project_temp_path("preview", f"preview_vi_voice_{ts}.mp4", create_parent=True)
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
        self.gui._preview_video_has_burned_subtitles = bool(mode == "subtitle" and has_active_video_filters)
        if hasattr(self.gui, "preview_btn"):
            self.gui.preview_btn.setEnabled(False)
        self.gui.progress_bar.setValue(95)
        self.gui.refresh_ui_state()
        target_width, target_height = self._resolve_output_canvas_dimensions(video_path)
        fill_focus_x, fill_focus_y = self.gui.get_output_fill_focus()

        self.gui.preview_thread = PreviewMuxWorker(
            video_path,
            audio_path,
            preview_out,
            mode=mode,
            srt_path=preview_srt_path,
            subtitle_style=subtitle_style,
            render_subtitles=bool(mode == "subtitle" and has_active_video_filters),
            target_width=target_width,
            target_height=target_height,
            output_scale_mode=self.gui.get_output_scale_mode_key(),
            output_fill_focus_x=fill_focus_x,
            output_fill_focus_y=fill_focus_y,
            video_filter_state=self.gui.get_video_filter_state() if hasattr(self.gui, "get_video_filter_state") else {},
            temp_dir=self.gui.get_project_temp_dir("preview"),
        )
        self.gui.preview_thread.finished.connect(
            lambda preview_path, error: self.gui.on_preview_ready(preview_path, error, styled_signature)
        )
        self.gui.preview_thread.start()

    def on_preview_ready(self, preview_path, error, styled_signature=""):
        if hasattr(self.gui, "ensure_media_backend_ready"):
            self.gui.ensure_media_backend_ready()
        self.gui._styled_preview_running = False
        self.gui._suspend_live_subtitle_sync = False
        if hasattr(self.gui, "preview_btn"):
            self.gui.preview_btn.setEnabled(True)
        self.gui.progress_bar.setValue(100)
        self.gui.refresh_ui_state()

        if error:
            self.gui._video_filter_preview_dirty = bool(hasattr(self.gui, "has_active_video_filters") and self.gui.has_active_video_filters())
            self.gui._video_filter_apply_requested = False
            self.gui._play_video_filter_preview_when_ready = False
            self.gui._suspend_live_subtitle_sync = False
            self.gui.show_error("Error", "Preview failed.", str(error))
            self.gui._pipeline_fail("Preview failed.")
            self.gui.refresh_ui_state()
            return

        if preview_path and os.path.exists(preview_path):
            self.gui._video_filter_preview_dirty = False
            self.gui._video_filter_apply_requested = False
            if hasattr(self.gui, "hide_filter_thumbnail_preview"):
                self.gui.hide_filter_thumbnail_preview()
            self.gui.last_preview_video_path = preview_path
            self.gui.processed_artifacts["preview_video"] = preview_path
            self.gui.last_styled_preview_path = preview_path
            self.gui.last_styled_preview_signature = styled_signature
            self.gui.log(f"[Preview] ready={preview_path}")
            self.gui.refresh_video_dimensions(preview_path)
            self.gui.media_player.setSource(QUrl.fromLocalFile(preview_path))
            if getattr(self.gui, "_preview_video_has_burned_subtitles", False):
                self.gui.media_player.clear_subtitle()
            else:
                self.gui.sync_live_subtitle_preview()
            self.gui._refresh_preview_audio_controls()
            if getattr(self.gui, "_play_video_filter_preview_when_ready", False):
                self.gui._play_video_filter_preview_when_ready = False
                self.gui.media_player.play()
                self.gui.timeline.set_playing(True)

        if bool(getattr(self.gui, "_pipeline_active", False)) and str(getattr(self.gui, "_pipeline_step", "") or "").strip().lower() == "preview":
            self.gui.pipeline_controller.pipeline_advance("preview")
            self.gui.apply_segments_to_timeline()
            self.gui.refresh_ui_state()

        if getattr(self.gui, "_pending_video_filter_preview", False):
            QTimer.singleShot(0, self.gui.run_live_video_filter_preview)






